"""Canvas protocol and assessment data; conversation work is queued for Root."""
from __future__ import annotations

from collections import OrderedDict
import copy
from contextlib import nullcontext
import json
import threading
import time
import uuid
from runtime.learning_core.records import digest

from .graph import GraphWorkspace
from .continuity import LearningContinuity
from .lesson import LearningLessonStore, OnDemandLessonWorker
from .prefetch import ProgressiveGraphPrefetch
from .focus import CanvasFocusStore
from .pack import load_pack, register_pack
from .snapshot import build_snapshot
from .exercises import CanvasExercises
from .narration import CanvasPlayback


class LearningCanvasService:
    def __init__(self, learning, *, store, pack=None, java_workspace=None, python_workspace=None):
        self.learning = learning
        self.pack = pack or load_pack()
        register_pack(learning, self.pack)
        self.focus = CanvasFocusStore(store, self.pack)
        # P3 là đồ thị khám phá độc lập; không gán mastery cho nút do người/AI thêm.
        self.graphs = GraphWorkspace(store, legacy_group_id=self.pack.group_id)
        self.continuity = LearningContinuity(store)
        self.lessons = LearningLessonStore(store)
        self.continuity.recover_interrupted_jobs()
        self._heartbeat_at = {}  # mono rate-limit; không tin số giây từ trình duyệt.
        # P3.7: không mở Gemini/lấy graph chi tiết khi panel chưa có session web.
        self._viewers = {}  # id browser-tab -> hạn dùng monotonic; tab đóng hoặc mất mạng tự hết hạn.
        self.prefetch = ProgressiveGraphPrefetch(self.graphs, on_change=self._prefetch_changed,
                                                on_result=self._prefetch_result, may_run=self.is_active)
        # P4: bài học dựng theo đúng nhánh đang xem; cache riêng, không cho Root nạp cả kho.
        self.lesson_worker = OnDemandLessonWorker(self.lessons, self.graphs,
                                                 may_run=self.is_active, on_change=self._lesson_changed)
        self._lock = threading.RLock()
        self._requests: OrderedDict[str, tuple[str, dict]] = OrderedDict()
        self.on_event = None
        self.on_state = None
        self.navigation_guard = None
        self._closed = False
        self.playback = CanvasPlayback(self.pack)
        self.exercises = CanvasExercises(learning, store, self.pack, workspace=java_workspace,
                                        python_workspace=python_workspace,
                                        on_completed=self._exercise_completed)

    def close(self):
        with self._lock:
            if self._closed:
                return
            self._closed = True
            self._viewers.clear()
        self.prefetch.close()
        self.lesson_worker.close()
        self.exercises.close()

    def is_active(self) -> bool:
        # Không kéo dài lease chỉ vì backend bận; mỗi tab nhìn thấy panel phải ping.
        now = time.monotonic()
        with self._lock:
            self._viewers = {key: due for key, due in self._viewers.items() if due > now}
            return not self._closed and bool(self._viewers)

    def _view_session(self, action: str, client_id: str) -> None:
        if not isinstance(client_id, str) or not 8 <= len(client_id) <= 100 or any(
                not (c.isascii() and (c.isalnum() or c in '-_:')) for c in client_id):
            raise ValueError("invalid_canvas_client_id")
        if action == "session_close":
            self._viewers.pop(client_id, None)
        else:
            self._viewers[client_id] = time.monotonic() + 40.0

    def _activate_selected_graph(self) -> None:
        graph = self.graphs.snapshot().get("graph")
        if graph and self.is_active():
            # Ưu tiên nội dung hiện tại trước việc chuẩn bị tầng ẩn.
            self.lesson_worker.request(graph["group_id"], graph["selected_node_id"])
            self._schedule_prefetch(graph["group_id"], graph["selected_node_id"])

    def brief(self) -> dict:
        return self.continuity.brief(max_subjects=4)

    def _lesson_changed(self):
        # Callback từ thread chỉ cập nhật panel hiện mở, không tự nói hay đổi focus.
        if self.is_active() and callable(self.on_state):
            self.on_state(self.state_packet(include_detail=False))

    def _prefetch_changed(self):
        # Worker chỉ phát trạng thái; Root vẫn giữ toàn bộ quyền hội thoại.
        if self.is_active() and callable(self.on_state):
            self.on_state(self.state_packet(include_detail=False))

    def _prefetch_result(self, result: dict):
        """Worker chỉ đưa sự kiện có dữ liệu vào queue Root; KHÔNG tự phát lời."""
        job = self.continuity.finish_job(result["job_id"], result["status"])
        if not job or result["status"] not in {"ready", "unavailable"} or self._closed:
            return
        if self.is_active() and callable(self.on_event):
            focus = self.focus.snapshot()
            self.on_event({"action": "graph_ready" if result["status"] == "ready" else "graph_failed",
                "node_id": focus["node_id"], "focus_revision": focus["focus_revision"],
                "graph_group_id": job["group_id"], "graph_node_id": job["node_id"],
                "graph_title": job["title"], "job_id": job["job_id"],
                "job_status": result["status"], "job_source": job["source"],
                "notice": "Nhánh ban đầu đã sẵn sàng trong Learning" if result["status"] == "ready"
                          else "Gemini hiện chưa tạo được nhánh; bản đồ vẫn được lưu"})

    def queue_graph_job(self, group_id: str, node_id: str, title: str, *, source="root") -> dict:
        """Tạo job trước khi giao Gemini để completion không vượt lên job queued."""
        job_id = "lg:" + uuid.uuid4().hex
        deferred = not self.is_active()
        self.continuity.queue_job(job_id, group_id, node_id, title=title,
                                  source=source, deferred=deferred)
        accepted = False if deferred else self.prefetch.request(group_id, node_id, job_id=job_id)
        if not deferred and not accepted:
            self.continuity.finish_job(job_id, "unchanged")
        return {"job_id": job_id, "status": "deferred" if deferred else "queued" if accepted else "unchanged",
                "group_id": group_id, "node_id": node_id}

    def record_graph_visit(self, group_id: str, node_id: str, *, action="selected", activate=True):
        info = self.graphs.inspect(group_id, node_id)
        self.continuity.visit(group_id, node_id, info["trail"][-1],
                              subject_title=info["topic"], action=action, activate=activate)

    def _schedule_prefetch(self, group_id: str, node_id: str):
        if group_id == self.pack.group_id or not node_id or not self.is_active():
            return
        job = self.continuity.start_deferred_job(group_id, node_id)
        accepted = self.prefetch.request(group_id, node_id,
                                         job_id=job["job_id"] if job else "")
        if job and not accepted:
            self.continuity.finish_job(job["job_id"], "unchanged")

    def _snapshot(self, *, detail_node_id="", include_activity=True):
        focus = self.focus.snapshot()
        result = build_snapshot(self.learning, self.pack, focus, detail_node_id=detail_node_id)
        result["playback_stream_id"] = self.playback.stream_id
        result["graph_workspace"] = self.graphs.snapshot()
        result["learning_continuity"] = self.continuity.snapshot(
            group_id=result["graph_workspace"]["selected_group_id"])
        # Trạng thái mastery của bài Java vẫn lấy từ LearningCore, không suy từ lượt xem.
        for node in result["nodes"]:
            if node.get("state") == "mastered":
                result["learning_continuity"].setdefault("verified_mastery", []).append(node["id"])
        visible = result["graph_workspace"].get("graph")
        if visible:
            group_id, node_id = visible["group_id"], visible["selected_node_id"]
            lesson = self.lessons.get(group_id, node_id)
            # Không bao giờ gửi các bài của những môn khác trong cùng một snapshot.
            result["dynamic_lesson"] = {"status": "ready", "content": lesson} if lesson else {
                "status": self.lesson_worker.state(group_id, node_id),
                "group_id": group_id, "node_id": node_id}
            result["graph_prefetch"] = {"status": self.prefetch.state(visible["group_id"],
                                                                         visible["selected_node_id"]),
                                        "node_id": visible["selected_node_id"]}
        playback = self.playback.snapshot()
        if playback:
            result["playback"] = playback
        late = self.focus.store.get_meta("learning_canvas_results_v1", {})
        for node in result["nodes"]:
            node["result_count"] = len(late.get(node["id"], []))
        if detail_node_id:
            result["late_results"] = [{k: v for k, v in row.items() if k != "data"} for row in late.get(detail_node_id, [])]
        activity = self.exercises.latest(detail_node_id or focus["node_id"]) if include_activity else None
        if activity:
            result["activity"] = activity
        return result

    def defer_result(self, kind, metadata, scope, *, event_id=""):
        node_id = scope.get("node_id")
        self.pack.node(node_id)
        with self._lock:
            late = self.focus.store.get_meta("learning_canvas_results_v1", {})
            key = event_id or digest([kind, metadata])
            rows = late.get(node_id, [])
            if any(row["id"] == key for row in rows):
                return
            data = metadata.get("research_capability", metadata.get("work_capability", metadata.get("canvas_capability", {})))
            rows.append({"id": key, "kind": kind, "summary": str(data.get("summary") or data.get("result") or "Đã có kết quả")[:2000],
                         "data": dict(data) if len(json.dumps(data, ensure_ascii=False).encode("utf-8")) <= 32000
                             else {"summary": str(data.get("summary", ""))[:12000], "data_truncated": True},
                         "focus_revision": scope.get("focus_revision"), "received_at": time.time()})
            late[node_id] = rows[-4:]
            self.focus.store.set_meta("learning_canvas_results_v1", late)
            if self.is_active() and callable(self.on_state):
                self.on_state(self.state_packet(include_detail=False))

    def _assessment_event(self, activity_id):
        payload = self.exercises.take_event(activity_id)
        if payload:
            focus = self.focus.snapshot()
            scope = {"node_id": payload["node_id"], "focus_revision": payload.get("focus_revision", focus["focus_revision"])}
            if focus["node_id"] != scope["node_id"] or focus["focus_revision"] != scope["focus_revision"]:
                self.defer_result("tutor_result", {"canvas_capability": payload}, scope, event_id=payload["event_id"])
            elif callable(self.on_event):
                self.on_event(payload)

    def _exercise_completed(self, activity):
        if self._closed:
            return
        activity = self.exercises.fallback(activity)
        if activity.get("status") == "completed":
            self.continuity.status(self.pack.group_id, activity["node_id"], "practiced",
                title=self.pack.node(activity["node_id"])["title"], subject_title=self.pack.path.title)
        self._assessment_event(activity["activity_id"])
        if self.is_active() and callable(self.on_state):
            packet = self.state_packet(include_detail=False)
            current = packet["learning_canvas"].get("activity", {})
            if current.get("activity_id") != activity["activity_id"]:
                packet["exercise_update"] = activity
            self.on_state(packet)

    def state_packet(self, *, include_detail=True) -> dict:
        """Server-originated state projection, applied by the UI without navigate."""
        with self._lock:
            focus = self.focus.snapshot()
            return {"type": "learning_canvas_state", "parse_mode": "learning_canvas_state",
                    "should_speak": False, "status": "ok",
                    "learning_canvas": self._snapshot(detail_node_id=focus["node_id"] if include_detail else "")}

    def handle(self, request: dict) -> dict:
        request_id = request.get("request_id", "")
        action = request.get("action", "open")
        envelope = {"type": "learning_canvas_ack", "parse_mode": "learning_canvas_ack",
                    "should_speak": False, "request_id": request_id if isinstance(request_id, str) else "",
                    "action": action if isinstance(action, str) else "", "status": "ok", "message": "Đã cập nhật."}
        with self._lock:
            try:
                if self._closed:
                    return {**envelope, "status": "error", "message": "canvas_closed"}
                if not isinstance(request_id, str) or not 1 <= len(request_id) <= 120:
                    raise ValueError("invalid_canvas_request_id")
                fingerprint = json.dumps(request, sort_keys=True, ensure_ascii=False, allow_nan=False)
                if len(fingerprint.encode("utf-8")) > 20000:
                    raise ValueError("canvas_request_too_large")
                if request_id in self._requests:
                    previous, response = self._requests[request_id]
                    if previous != fingerprint:
                        raise ValueError("canvas_request_id_reused")
                    return copy.deepcopy(response)
                # P3.7: session chỉ kích hoạt Gemini khi tab Learning đang hiển thị.
                # Không xem open không client_id (giao thức cũ/test) là phiên đang xem.
                client_id = request.get("client_id")
                if action in {"session_ping", "session_close"}:
                    self._view_session(action, client_id)
                    envelope["message"] = "Đã cập nhật phiên Learning."
                    return envelope
                if action == "open":
                    if "node_id" in request:
                        self.pack.node(request["node_id"])
                    if client_id is not None:
                        self._view_session(action, client_id)
                        self._activate_selected_graph()
                # P3: graph actions có revision riêng để không đụng focus/điểm học Java.
                graph_actions = {"graph_create", "graph_select", "graph_expand", "graph_delete", "delete_group", "mark_viewed", "study_heartbeat", "lesson_request", "lesson_narrate"}
                if action not in graph_actions and request.get("group_id", self.pack.group_id) != self.pack.group_id:
                    raise ValueError("unknown_canvas_group")
                prefetch_target = None
                if action == "graph_create":
                    title = request.get("title")
                    from .graph import _title
                    clean_title = _title(title)
                    existing = next((item for item in self.graphs.snapshot()["groups"]
                        if item["kind"] == "exploration" and item["title"].casefold() == clean_title.casefold()), None)
                    if existing:
                        self.graphs.select(existing["group_id"], base_revision=request.get("base_graph_revision"))
                        graph = self.graphs.snapshot()["graph"]
                        envelope["graph_update"] = {"group_id": graph["group_id"],
                            "node_id": graph["selected_node_id"], "existing": True}
                        self.record_graph_visit(graph["group_id"], graph["selected_node_id"], action="resumed")
                        prefetch_target = (graph["group_id"], graph["selected_node_id"])
                    else:
                        graph = self.graphs.create(clean_title,
                            base_revision=request.get("base_graph_revision"))
                        envelope["graph_update"] = {"group_id": graph["group_id"],
                            "node_id": graph["selected_node_id"], "existing": False}
                        self.record_graph_visit(graph["group_id"], graph["selected_node_id"], action="created")
                        envelope["learning_job"] = self.queue_graph_job(graph["group_id"], graph["selected_node_id"],
                                                                            graph["title"], source="user_ui")
                        prefetch_target = (graph["group_id"], graph["selected_node_id"])
                elif action == "graph_select":
                    self.graphs.select(request.get("group_id"), node_id=request.get("node_id", ""),
                        base_revision=request.get("base_graph_revision"))
                    viewed = self.graphs.snapshot().get("graph")
                    if viewed:
                        self.record_graph_visit(viewed["group_id"], viewed["selected_node_id"], action="selected")
                        prefetch_target = (viewed["group_id"], viewed["selected_node_id"])
                    else:
                        java = self.focus.snapshot()["node_id"]
                        self.continuity.visit(self.pack.group_id, java, self.pack.node(java)["title"],
                            subject_title=self.pack.path.title, action="resumed")
                elif action == "graph_expand":
                    children = self.graphs.expand(request.get("group_id"), request.get("parent_id"),
                        [{"title": request.get("title"), "summary": request.get("summary", "")}],
                        base_revision=request.get("base_graph_revision"))
                    envelope["graph_update"] = {"group_id": request.get("group_id"),
                        "node_id": children[0]["id"]}
                    self.record_graph_visit(request.get("group_id"), children[0]["id"], action="selected")
                    prefetch_target = (request.get("group_id"), children[0]["id"])
                elif action in {"graph_delete", "delete_group"}:
                    target_id = request.get("group_id", "")
                    target_title = request.get("title", "")
                    success = self.graphs.delete(group_id=target_id, title=target_title,
                        base_revision=request.get("base_graph_revision"))
                    envelope["graph_update"] = {"deleted": bool(success), "group_id": target_id, "title": target_title}
                    envelope["message"] = "Đã xoá môn học thành công." if success else "Không tìm thấy môn học để xoá."
                elif action == "lesson_request":
                    # Web chỉ xin bài của đúng nhánh đang được nhìn; không có endpoint
                    # để worker tạo hàng loạt bài học trong khi panel đóng.
                    current = self.graphs.snapshot()
                    group_id, node_id = request.get("group_id"), request.get("node_id")
                    if (not self.is_active() or not current.get("graph") or
                            current["selected_group_id"] != group_id or
                            current["graph"]["selected_node_id"] != node_id):
                        raise ValueError("canvas_focus_changed")
                    envelope["lesson_queued"] = self.lesson_worker.request(group_id, node_id, retry=True)
                elif action == "lesson_narrate":
                    current = self.graphs.snapshot()
                    group_id, node_id = request.get("group_id"), request.get("node_id")
                    if (not self.is_active() or not current.get("graph") or
                            current["selected_group_id"] != group_id or
                            current["graph"]["selected_node_id"] != node_id):
                        raise ValueError("canvas_focus_changed")
                    lesson = self.lessons.get(group_id, node_id)
                    if not lesson:
                        raise ValueError("lesson_not_ready")
                    from .lesson import build_lesson_narration_script
                    script = build_lesson_narration_script(lesson)
                    envelope["narration_script"] = script
                    if callable(self.on_event):
                        self.on_event({
                            "action": "lesson_narrate",
                            "group_id": group_id,
                            "node_id": node_id,
                            "title": lesson.get("title", ""),
                            "script": script,
                        })
                elif action == "mark_viewed":
                    group_id = request.get("group_id")
                    node_id = request.get("node_id")
                    visible = self.graphs.snapshot()
                    if visible["selected_group_id"] != group_id or not visible["graph"] or \
                            visible["graph"]["selected_node_id"] != node_id:
                        raise ValueError("canvas_focus_changed")
                    info = self.graphs.inspect(group_id, node_id)
                    self.continuity.status(group_id, node_id, "viewed", title=info["trail"][-1],
                                           subject_title=info["topic"])
                elif action == "study_heartbeat":
                    group_id, node_id = request.get("group_id"), request.get("node_id")
                    selected = self.graphs.snapshot()
                    if selected["selected_group_id"] != group_id:
                        raise ValueError("canvas_focus_changed")
                    if group_id == self.pack.group_id:
                        if self.focus.snapshot()["node_id"] != node_id:
                            raise ValueError("canvas_focus_changed")
                    elif not selected["graph"] or selected["graph"]["selected_node_id"] != node_id:
                        raise ValueError("canvas_focus_changed")
                    key = (group_id, node_id)
                    now = time.monotonic()
                    if now - self._heartbeat_at.get(key, 0) >= 25:
                        self._heartbeat_at[key] = now
                        self.continuity.add_study_time(group_id, node_id)
                elif action == "navigate":
                    with self.navigation_guard() if callable(self.navigation_guard) else nullcontext():
                        self.focus.navigate(request.get("node_id"), base_revision=request.get("base_revision"))
                    node_id = self.focus.snapshot()["node_id"]
                    self.continuity.visit(self.pack.group_id, node_id, self.pack.node(node_id)["title"],
                                          subject_title=self.pack.path.title, action="navigated")
                elif action == "exercise":
                    if request.get("node_id") != self.focus.snapshot()["node_id"]:
                        raise ValueError("canvas_focus_changed")
                    envelope["exercise_update"] = self.exercises.open(request.get("node_id"), request.get("exercise_id"), request_id)
                elif action in {"submit", "hint", "reveal", "exercise_status"}:
                    activity = self.exercises.handle(action, request)
                    envelope["exercise_update"] = activity
                    if action in {"submit", "exercise_status"} and activity["status"] == "completed":
                        self.continuity.status(self.pack.group_id, activity["node_id"], "practiced",
                            title=self.pack.node(activity["node_id"])["title"], subject_title=self.pack.path.title)
                        self._assessment_event(activity["activity_id"])
                elif action == "ack_eris_focus":
                    self.focus.acknowledge(base_revision=request.get("base_revision"), cause_id=request.get("cause_id"))
                elif action != "open":
                    raise ValueError("unknown_canvas_action")
                focus = self.focus.snapshot()
                detail_id = request.get("node_id", focus["node_id"]) if action == "open" else focus["node_id"]
                if not isinstance(detail_id, str):
                    raise ValueError("unknown_canvas_node")
                exercise_response = "exercise_update" in envelope
                envelope["learning_canvas"] = self._snapshot(detail_node_id="" if exercise_response else detail_id,
                                                             include_activity=not exercise_response)
                # The validated pack already bounds detail; this also guards future changes.
                if len(json.dumps(envelope, ensure_ascii=False).encode("utf-8")) > 60000:
                    raise ValueError("canvas_response_too_large")
                self._requests[request_id] = (fingerprint, copy.deepcopy(envelope))
                if prefetch_target is not None:
                    # Đáp ứng nhánh đang mở trước; prefetch tầng ẩn có ưu tiên thấp.
                    self.lesson_worker.request(*prefetch_target)
                    self._schedule_prefetch(*prefetch_target)
                while len(self._requests) > 128:
                    self._requests.popitem(last=False)
                if action in {"graph_create", "graph_expand", "graph_select"} and callable(self.on_event):
                    # Root có thể đề xuất bài giảng / mở rộng thêm, không có quyền tự chấm mastery.
                    graph_state = envelope["learning_canvas"]["graph_workspace"]
                    current = graph_state.get("graph") or {}
                    selected_node = next((n for n in current.get("nodes", [])
                                          if n["id"] == current.get("selected_node_id")), {})
                    self.on_event({"action": action, "node_id": focus["node_id"],
                        "focus_revision": focus["focus_revision"],
                        "graph_group_id": graph_state["selected_group_id"],
                        "graph_node_id": current.get("selected_node_id", ""),
                        "graph_title": current.get("title", ""),
                        "graph_node_title": selected_node.get("title", ""),
                        "graph_node_summary": selected_node.get("summary", "")})
                if action == "navigate" and callable(self.on_event):
                    self.on_event({"action": "navigate", "node_id": focus["node_id"],
                                   "focus_revision": focus["focus_revision"], "learning_canvas": envelope["learning_canvas"]})
            except (ValueError, TypeError) as exc:
                reason = str(exc) if isinstance(exc, ValueError) else "invalid_canvas_request"
                envelope.update(status="conflict" if reason in {"stale_canvas_revision", "stale_graph_revision"} else "error", message=reason)
                envelope["learning_canvas"] = self._snapshot(detail_node_id=self.focus.snapshot()["node_id"])
            return envelope
