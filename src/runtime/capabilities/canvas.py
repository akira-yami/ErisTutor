"""Root's native Canvas tool. P3 cho phép đề xuất nhánh, không tự gán điểm học."""
from __future__ import annotations

import uuid


class CanvasCapability:
    def __init__(self, host):
        self.host = host

    def invoke_for_root(self, *, arguments, event, recent_history=()):
        canvas = getattr(self.host, "learning_canvas_service", None)
        if canvas is None:
            return {"status": "unavailable", "reason": "canvas_not_ready"}
        operation = arguments.get("operation", "snapshot")
        try:
            with canvas._lock:
                if operation == "learning_brief":
                    return {"status": "completed", "learning_brief": canvas.brief()}
                if operation == "library":
                    graph_workspace = canvas.graphs.snapshot()
                    return {"status": "completed", "groups": [
                        {"group_id": canvas.pack.group_id, "title": canvas.pack.path.title,
                         "nodes": list(canvas.pack.path.kp_ids)}]
                        + graph_workspace["groups"][1:], "graph_revision": graph_workspace["revision"],
                        "learning_brief": canvas.brief()}
                graph_update = None
                learning_job = None
                if operation == "learning_history":
                    return {"status": "completed", "learning_continuity": canvas.continuity.snapshot(),
                            "graph_workspace": canvas.graphs.snapshot()}
                if operation in {"graph_create", "graph_expand", "graph_select", "graph_prefetch", "graph_resume"}:
                    current = canvas.graphs.snapshot()
                    base = arguments.get("base_graph_revision", current["revision"])
                    if operation == "graph_create":
                        requested = arguments.get("title")
                        # Tránh tạo lại môn đã có: hỏi/tự phát hiện lại thì tiếp tục bản đồ cũ.
                        existing = next((g for g in current["groups"] if isinstance(requested, str)
                            and g["kind"] == "exploration" and g["title"].strip().casefold() == requested.strip().casefold()), None)
                        if existing:
                            graph_id = existing["group_id"]
                            snapshot = canvas.graphs._load()
                            saved = next(g for g in snapshot["groups"] if g["group_id"] == graph_id)
                            graph_update = {"group_id": graph_id, "node_id": saved["selected_node_id"],
                                            "existing": True}
                        else:
                            graph = canvas.graphs.create(requested,
                                base_revision=base, origin="eris_proposed", follow=False)
                            graph_update = {"group_id": graph["group_id"], "node_id": graph["selected_node_id"],
                                            "existing": False}
                            canvas.record_graph_visit(graph["group_id"], graph["selected_node_id"],
                                                      action="created", activate=False)
                            learning_job = canvas.queue_graph_job(graph["group_id"],
                                graph["selected_node_id"], graph["title"], source="root")
                    elif operation == "graph_expand":
                        additions = canvas.graphs.expand(arguments.get("group_id"), arguments.get("parent_id"),
                            arguments.get("nodes"), base_revision=base, origin="eris_proposed", follow=False)
                        graph_update = {"nodes": additions, "group_id": arguments.get("group_id")}
                    elif operation == "graph_prefetch":
                        # Root chỉ yêu cầu Gemini chuẩn bị; không thay focus/chấm bài.
                        group_id, node_id = arguments.get("group_id"), arguments.get("node_id")
                        canvas.graphs.inspect(group_id, node_id)
                        job = canvas.queue_graph_job(group_id, node_id,
                            canvas.graphs.inspect(group_id, node_id)["trail"][-1], source="root")
                        learning_job = job
                        graph_update = {"prefetch_queued": job["status"] == "queued",
                                        "group_id": group_id, "node_id": node_id}
                    elif operation in {"graph_select", "graph_resume"}:
                        # Tiếp tục đúng nút cũ; chỉ chuyển khi Root đã được user yêu cầu.
                        group_id = arguments.get("group_id")
                        target = arguments.get("node_id", "") if operation == "graph_select" else ""
                        canvas.graphs.select(group_id, base_revision=base, node_id=target)
                        graph = canvas.graphs.snapshot().get("graph")
                        if graph:
                            canvas.record_graph_visit(group_id, graph["selected_node_id"], action="resumed")
                            canvas._schedule_prefetch(group_id, graph["selected_node_id"])
                        else:
                            node = canvas.focus.snapshot()["node_id"]
                            canvas.continuity.visit(canvas.pack.group_id, node, canvas.pack.node(node)["title"],
                                subject_title=canvas.pack.path.title, action="resumed")
                elif operation == "open_group":
                    if arguments.get("group_id") != canvas.pack.group_id:
                        raise ValueError("unknown_canvas_group")
                elif operation in {"focus", "accept_focus"}:
                    base = arguments.get("base_revision", canvas.focus.snapshot()["revision"])
                    if operation == "accept_focus":
                        if getattr(event, "kind", "") != "user_turn" or not getattr(event, "text", "").strip():
                            raise ValueError("canvas_accept_requires_user_turn")
                        canvas.focus.accept_suggestion(arguments.get("node_id"), base_revision=base)
                    else:
                        canvas.focus.eris_focus(arguments.get("node_id"), base_revision=base,
                                               cause_id=f"root:{uuid.uuid4().hex}")
                elif operation not in {"snapshot", "snapshot_full"}:
                    raise ValueError("unknown_canvas_operation")
                # Panel đóng: trả receipt ngắn cho Root, không serialize full graph,
                # không gửi state/auto-open web. Panel mở: giữ protocol/UI cũ.
                active = canvas.is_active()
                packet = canvas.state_packet() if active or operation == "snapshot_full" else None
                if active and operation in {"open_group", "focus", "accept_focus", "graph_create", "graph_expand", "graph_select", "graph_resume", "graph_prefetch"}:
                    packet["open_panel"] = False
                    accepted = self.host._send_runtime_json(packet, channel="learning_canvas")
                    if accepted is False:
                        return {"status": "failed", "reason": "canvas_transport_failed"}
                kernel = getattr(self.host, "conversation_kernel", None)
                bind = getattr(kernel, "bind_canvas_scope", None)
                focus = canvas.focus.snapshot()
                if callable(bind):
                    bind(event.session_id, {"node_id": focus["node_id"], "focus_revision": focus["focus_revision"]})
                applied = not focus["pinned"] and focus["node_id"] == arguments.get("node_id")
                return {"status": "completed", "operation": operation,
                        **({"learning_canvas": packet["learning_canvas"]} if packet else
                           {"learning_brief": canvas.brief(), "panel_active": False}),
                        **({"applied": applied, "reason": "applied" if applied else "user_pinned"}
                           if operation in {"focus", "accept_focus"} else {}),
                        **({"graph_update": graph_update} if graph_update is not None else {}),
                        **({"learning_job": learning_job} if learning_job is not None else {}),
                        "receipt": "state_sent" if operation != "snapshot" else "snapshot_read",
                        "caution": "Đồ thị khám phá chưa phải bằng chứng mastery; Root sở hữu quyết định nói."}
        except (ValueError, TypeError) as exc:
            return {"status": "failed", "reason": str(exc)}


def canvas_capability_for_host(host):
    capability = getattr(host, "canvas_capability", None)
    if not isinstance(capability, CanvasCapability):
        capability = host.canvas_capability = CanvasCapability(host)
    return capability
