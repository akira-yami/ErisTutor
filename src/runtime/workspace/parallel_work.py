# -*- coding: utf-8 -*-
"""Luồng công việc dài của Eris, độc lập với đường thoại và TTS.

Root/Identity duy nhất được giao hoặc dừng công việc. Worker chỉ thực hiện
nhiệm vụ đã giao; mỗi lượt suy luận trả MỘT thao tác hoặc một kết quả, không
được tự tạo lượt nói công khai. Tiến độ được lưu SQLite và có thể truy vấn.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
import json
import os
from pathlib import Path
import sqlite3
import threading
import time
import uuid
from typing import Any, Callable, Mapping

from runtime.agent_harness.work_skill_context import WorkSkillContext

# LLM nằm trong worker riêng; chat_main không đợi model hoặc tool của job.
_WORK_STEP_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["kind", "text", "action_id", "arguments"],
    "properties": {
        "kind": {"type": "string", "enum": ["progress", "tool", "run_tool", "say", "show", "edit", "complete", "wait_user"]},
        "text": {"type": "string"},
        "action_id": {"type": "string"},
        "arguments": {"type": "object", "additionalProperties": True},
    },
}
# Tool được phép chạy nền, dựa trên cơ chế sandbox/capability hiện hữu.
_WORK_TOOLS = frozenset({"math_workspace", "python_workspace", "project_read_file", "project_search_text", "project_run_tests"})
_SKILL_ACTIONS = frozenset({"search_capabilities", "activate_skill"})
_TERMINAL = frozenset({"completed", "cancelled", "failed", "needs_review"})


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)


@dataclass
class WorkJob:
    job_id: str
    task: str
    status: str = "queued"
    revision: int = 0
    summary: str = ""
    last_evidence: str = ""
    cancel_requested: bool = False

    def snapshot(self) -> dict[str, Any]:
        return {"job_id": self.job_id, "task": self.task, "status": self.status,
                "revision": self.revision, "summary": self.summary,
                "last_evidence": self.last_evidence}


class ParallelWorkService:
    """Một work-lane có bộ nhớ, không phải một coordinator nói chuyện thứ hai."""

    def __init__(self, db_path: str | Path, *, planner: Callable[[dict], Mapping[str, Any]] | None,
                 tool: Callable[[str, Mapping[str, Any]], Any] | None = None,
                 publish: Callable[[dict], Any] | None = None,
                 max_steps: int = 12, max_workers: int = 2,
                 powers_root: str | Path | None = None,
                 should_continue: Callable[[str], bool] | None = None):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.planner = planner
        self.tool = tool
        self.publish = publish or (lambda _: None)
        self.max_steps = max(1, min(30, int(max_steps)))
        self._lock = threading.RLock()
        self._pool = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="ERIS-WorkLane")
        self._running: dict[str, WorkJob] = {}
        self._skill_context = None
        self._should_continue = should_continue or (lambda _job_id: True)
        with self._connect() as con:
            con.executescript("""
                CREATE TABLE IF NOT EXISTS jobs (
                    job_id TEXT PRIMARY KEY, task TEXT NOT NULL, status TEXT NOT NULL,
                    revision INTEGER NOT NULL, summary TEXT NOT NULL,
                    evidence TEXT NOT NULL, updated_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS events (
                    job_id TEXT NOT NULL, revision INTEGER NOT NULL,
                    kind TEXT NOT NULL, detail TEXT NOT NULL, at REAL NOT NULL,
                    PRIMARY KEY(job_id, revision)
                );
                CREATE TABLE IF NOT EXISTS work_documents (
                    job_id TEXT PRIMARY KEY, revision INTEGER NOT NULL,
                    blocks TEXT NOT NULL, updated_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS work_document_events (
                    job_id TEXT NOT NULL, revision INTEGER NOT NULL,
                    operation TEXT NOT NULL, block_id TEXT NOT NULL,
                    detail TEXT NOT NULL, at REAL NOT NULL,
                    PRIMARY KEY(job_id, revision)
                );
            """)
            # Không tự chạy lại side effect sau crash: chưa biết tool cũ đã thực thi chưa.
            con.execute("UPDATE jobs SET status='needs_review' WHERE status IN ('queued','running')")
        self._skill_context = WorkSkillContext(self.db_path, powers_root=powers_root)

    def _connect(self):
        con = sqlite3.connect(str(self.db_path), timeout=8)
        con.execute("PRAGMA busy_timeout=8000")
        return con

    def _store(self, job: WorkJob, kind: str, detail: str = "") -> None:
        with self._lock, self._connect() as con:
            job.revision += 1
            con.execute("INSERT INTO jobs(job_id,task,status,revision,summary,evidence,updated_at) "
                        "VALUES(?,?,?,?,?,?,?) ON CONFLICT(job_id) DO UPDATE SET "
                        "status=excluded.status,revision=excluded.revision,summary=excluded.summary,"
                        "evidence=excluded.evidence,updated_at=excluded.updated_at",
                        (job.job_id, job.task, job.status, job.revision, job.summary,
                         job.last_evidence, time.time()))
            con.execute("INSERT INTO events VALUES(?,?,?,?,?)",
                        (job.job_id, job.revision, kind, detail[:5000], time.time()))

    def _publish(self, job: WorkJob, kind: str, text: str,
                 *, artifacts: list[dict] | None = None, document_revision: int = 0) -> None:
        # Gói gửi UI không cấp quyền phát ngôn; lỗi UDP không làm hỏng công việc.
        evt = {"job_id": job.job_id, "revision": job.revision, "status": job.status,
               "kind": kind, "text": text[:3500]}
        if artifacts is None:
            doc = self.document(job.job_id)
            if doc and doc["blocks"]:
                artifacts = self._artifacts(doc["blocks"])
                document_revision = doc["revision"]
        if artifacts is not None:
            evt["artifacts"] = artifacts
            evt["document_revision"] = document_revision
        try:
            self.publish(evt)
        except Exception:
            pass

    def start(self, task: str) -> dict:
        task = str(task or "").strip()
        if not task or len(task) > 6000:
            raise ValueError("Nhiệm vụ rỗng hoặc quá dài")
        job = WorkJob(job_id="work-" + uuid.uuid4().hex, task=task)
        self._store(job, "created", "Root giao nhiệm vụ")
        with self._lock:
            self._running[job.job_id] = job
        self._pool.submit(self._run, job)
        return job.snapshot()

    def snapshot(self, job_id: str) -> dict | None:
        with self._lock, self._connect() as con:
            row = con.execute("SELECT job_id,task,status,revision,summary,evidence "
                              "FROM jobs WHERE job_id=?", (str(job_id),)).fetchone()
        return dict(zip(("job_id", "task", "status", "revision", "summary", "last_evidence"), row)) if row else None

    def recent(self, limit: int = 4) -> list[dict]:
        with self._lock, self._connect() as con:
            rows = con.execute("SELECT job_id,task,status,revision,summary,evidence FROM jobs "
                               "ORDER BY updated_at DESC LIMIT ?", (max(1, min(10, limit)),)).fetchall()
        return [dict(zip(("job_id", "task", "status", "revision", "summary", "last_evidence"), row)) for row in rows]

    def events(self, job_id: str, limit: int = 15) -> list[dict]:
        with self._lock, self._connect() as con:
            rows = con.execute("SELECT revision,kind,detail FROM events WHERE job_id=? "
                               "ORDER BY revision DESC LIMIT ?", (job_id, min(max(limit, 1), 30))).fetchall()
        return [{"revision": v, "kind": k, "detail": d} for v, k, d in reversed(rows)]

    def document(self, job_id: str) -> dict | None:
        """Truy vấn tài liệu bền vững của Work Session mà không chờ LLM.

        Giữ nguyên block_id để Root/Writer có thể sửa theo revision.
        """
        if self.snapshot(job_id) is None:
            return None
        with self._lock, self._connect() as con:
            row = con.execute("SELECT revision, blocks FROM work_documents WHERE job_id=?", (job_id,)).fetchone()
        return {"job_id": job_id, "revision": int(row[0]) if row else 0,
                "blocks": json.loads(row[1]) if row else []}

    @staticmethod
    def _artifacts(blocks: list[dict]) -> list[dict]:
        """Projection cho renderer Godot hiện hữu, không qua Bubble/TTS."""
        result: list[dict] = []
        for block in blocks[:28]:
            item = {k: v for k, v in block.items() if k != "block_id"}
            if item.get("type") == "paragraph":
                item["type"] = "text"
            elif item.get("type") == "heading":
                item["type"] = "section"
            elif item.get("type") == "list":
                item = {"type": "text", "text": "\n".join("• " + str(x) for x in item.get("items", [])[:40])}
            elif item.get("type") == "quote":
                item = {"type": "text", "text": "“" + str(item.get("text", "")) + "”"}
            if item.get("type") in {"text", "section", "formula", "code", "table", "image"}:
                result.append(item)
        return result

    def edit_document(self, job_id: str, *, operation: str,
                      block_id: str = "", block: Mapping[str, Any] | None = None,
                      base_revision: int | None = None) -> dict:
        """Một điểm commit duy nhất cho các block thuộc Work Session.

        Chỉ sửa trong SQLite transaction; CAS ngăn Writer và Worker ghi đè.
        Không bao giờ tạo lời thoại hoặc gọi model từ đường này.
        """
        from runtime.workspace.live_document import _bounded_block
        import re
        if operation not in {"insert_block", "replace_block", "remove_block"}:
            raise ValueError("Thao tác tài liệu không hợp lệ")
        if base_revision is None or isinstance(base_revision, bool) or not isinstance(base_revision, int):
            raise ValueError("Thiếu base_revision")
        if block_id and not re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", block_id):
            raise ValueError("block_id không hợp lệ")
        normalized = _bounded_block(block) if operation != "remove_block" else None
        with self._lock, self._connect() as con:
            if self._running.get(job_id) is not None and self._running[job_id].cancel_requested:
                return {"error": "job_cancelling"}
            row = con.execute("SELECT status,revision FROM jobs WHERE job_id=?", (job_id,)).fetchone()
            if not row:
                return {"error": "job_not_found"}
            if row[0] in {"cancelled", "failed", "needs_review"}:
                return {"error": "job_not_editable"}
            record = con.execute("SELECT revision,blocks FROM work_documents WHERE job_id=?", (job_id,)).fetchone()
            revision = int(record[0]) if record else 0
            blocks = json.loads(record[1]) if record else []
            if base_revision != revision:
                return {"error": "revision_conflict", "revision": revision}
            ids = [b["block_id"] for b in blocks]
            if operation == "insert_block":
                if len(blocks) >= 40:
                    return {"error": "document_full"}
                block_id = block_id or f"block_{revision+1}"
                if block_id in ids:
                    return {"error": "duplicate_block"}
                blocks.append({"block_id": block_id, **normalized})
            else:
                if block_id not in ids:
                    return {"error": "block_not_found"}
                index = ids.index(block_id)
                if operation == "replace_block":
                    blocks[index] = {"block_id": block_id, **normalized}
                else:
                    blocks.pop(index)
            revision += 1
            con.execute("INSERT INTO work_documents(job_id,revision,blocks,updated_at) VALUES(?,?,?,?) "
                        "ON CONFLICT(job_id) DO UPDATE SET revision=excluded.revision, "
                        "blocks=excluded.blocks, updated_at=excluded.updated_at",
                        (job_id, revision, _json(blocks), time.time()))
            con.execute("INSERT INTO work_document_events VALUES(?,?,?,?,?,?)",
                        (job_id, revision, operation, block_id,
                         _json({"block": normalized})[:4000], time.time()))
        # Trả snapshot đã commit; caller phát bản tài liệu đầy đủ có version.
        return {"job_id": job_id, "revision": revision, "blocks": blocks,
                "operation": operation, "block_id": block_id}

    def _apply_work_edit(self, job: WorkJob, kind: str, args: Mapping[str, Any],
                         narrative: str) -> None:
        operation = str(args.get("operation", "") or ("insert_block" if kind == "show" else "replace_block"))
        if kind == "show" and operation != "insert_block":
            raise ValueError("show chỉ được insert_block")
        if kind == "edit" and operation not in {"replace_block", "remove_block"}:
            raise ValueError("edit chỉ được replace/remove")
        if "base_revision" not in args:
            job.last_evidence = _json({"error": "missing_base_revision"})
            self._store(job, "workspace_conflict", job.last_evidence)
            self._publish(job, "workspace_conflict", "Thiếu revision tài liệu; cần đọc lại.")
            return
        expected = args["base_revision"]
        result = self.edit_document(job.job_id, operation=operation,
                                    block_id=str(args.get("block_id", "") or ""),
                                    block=args.get("block"), base_revision=expected)
        if "error" in result:
            # Tránh job kẹt vô hạn khi Writer vừa sửa tài liệu.
            job.last_evidence = _json(result)
            self._store(job, "workspace_conflict", job.last_evidence)
            self._publish(job, "workspace_conflict", str(result["error"]))
            return
        job.summary = narrative or "Đã cập nhật bảng làm việc."
        self._store(job, "workspace_edit", _json({"operation": operation,
                    "block_id": result["block_id"], "document_revision": result["revision"]}))
        self._publish(job, "workspace", job.summary,
                      artifacts=self._artifacts(result["blocks"]),
                      document_revision=result["revision"])

    def execute(self, command: Mapping[str, Any]) -> dict:
        """Contract điều khiển chung cho cả API cũ và Unified Agent Sessions."""
        op = str(command.get("operation", "") or "")
        job_id = str(command.get("job_id", "") or "")
        if op == "start":
            return self.start(str(command.get("task", "")))
        if op == "recent":
            return {"jobs": self.recent()}
        if op == "status":
            state = self.snapshot(job_id)
            if not state:
                return {"error": "job_not_found"}
            state["events"] = self.events(job_id, 6)
            state["document_revision"] = (self.document(job_id) or {}).get("revision", 0)
            return state
        if op == "document":
            return self.document(job_id) or {"error": "job_not_found"}
        if op == "edit_document":
            return self.edit_document(job_id,
                                      operation=str(command.get("document_operation", "")),
                                      block_id=str(command.get("block_id", "")),
                                      block=command.get("block"),
                                      base_revision=command.get("base_revision"))
        if op == "resume":
            return self.resume(job_id, str(command.get("answer", "")))
        if op == "cancel":
            return self.cancel(job_id)
        return {"error": "unsupported_operation"}

    def cancel(self, job_id: str) -> dict:
        with self._lock:
            job = self._running.get(job_id)
            if job is None:
                snap = self.snapshot(job_id)
                return snap or {"error": "job_not_found"}
            job.cancel_requested = True
            # Không giả là tiến trình tool đã dừng khi nó đang I/O.
            self._store(job, "cancel_requested", "Chờ điểm ngắt hợp tác")
            return job.snapshot()

    def resume(self, job_id: str, answer: str) -> dict:
        """Root quyết định tiếp tục sau khi có câu trả lời user thật.

        Job chưa rõ side effect lúc crash không được resume mù; cần xem lại.
        """
        answer = str(answer or "").strip()
        if not answer or len(answer) > 4000:
            raise ValueError("Thiếu câu trả lời để tiếp tục công việc")
        with self._lock:
            snap = self.snapshot(job_id)
            if not snap or snap["status"] != "waiting_user" or job_id in self._running:
                return {"error": "job_not_waiting_user"}
            job = WorkJob(job_id=snap["job_id"], task=snap["task"],
                          status="queued", revision=snap["revision"],
                          summary=snap["summary"], last_evidence=snap["last_evidence"])
            self._store(job, "user_answer", answer)
            self._running[job.job_id] = job
            self._pool.submit(self._run, job)
            return job.snapshot()

    def _run(self, job: WorkJob) -> None:
        try:
            if self.planner is None:
                job.status = "needs_review"
                job.summary = "Chưa nối model giải quyết công việc."
                self._store(job, "planner_unavailable")
                return
            job.status = "running"
            self._store(job, "started")
            for _ in range(self.max_steps):
                if job.cancel_requested or not self._should_continue(job.job_id):
                    job.status = "cancelled"
                    self._store(job, "goal_or_user_preempted")
                    return
                events = self.events(job.job_id, 10)
                context = {"task": job.task, "job_id": job.job_id,
                           "recent_events": events,
                           "allowed_tools": sorted(_WORK_TOOLS | _SKILL_ACTIONS),
                           "document": self.document(job.job_id)}
                context.update(self._skill_context.context(
                    job.job_id, allowed_tools=_WORK_TOOLS,
                    events=self.events(job.job_id, 30), evidence=job.last_evidence))
                step = self.planner(context)
                if job.cancel_requested or not self._should_continue(job.job_id):
                    job.status = "cancelled"
                    self._store(job, "late_model_result_discarded")
                    return
                if not isinstance(step, Mapping):
                    raise ValueError("Model không trả work step hợp lệ")
                kind = str(step.get("kind", ""))
                narrative = str(step.get("text", "") or "").strip()[:3500]
                if kind == "progress":
                    if not narrative:
                        raise ValueError("Tiến độ không có nội dung")
                    job.summary = narrative
                    self._store(job, "progress", narrative)
                    self._publish(job, "progress", narrative)
                elif kind == "say":
                    # Work chỉ ĐỀ XUẤT câu nói cho Root, không có SpeechAuthority.
                    if not narrative:
                        raise ValueError("Thiếu lời thoại đề xuất")
                    job.summary = narrative
                    self._store(job, "speech_suggestion", narrative)
                    self._publish(job, "speech_suggestion", narrative)
                elif kind in {"show", "edit"}:
                    args = step.get("arguments", {})
                    if not isinstance(args, Mapping):
                        raise ValueError("arguments không hợp lệ")
                    self._apply_work_edit(job, kind, args, narrative)
                elif kind in {"tool", "run_tool"}:
                    action = str(step.get("action_id", ""))
                    args = step.get("arguments", {})
                    if action in _SKILL_ACTIONS:
                        if not isinstance(args, Mapping):
                            raise ValueError("Đối số Skill không hợp lệ")
                        if action == "search_capabilities":
                            outcome = self._skill_context.search(str(args.get("query", "")))
                        else:
                            outcome = self._skill_context.activate(job.job_id, str(args.get("skill_name", "")))
                        job.last_evidence = _json(outcome)[:5000]
                        self._store(job, "skill_result", job.last_evidence)
                        self._publish(job, "skill_result", narrative or "Đã xử lý yêu cầu Skill.")
                        continue
                    if action not in _WORK_TOOLS or not isinstance(args, Mapping) or self.tool is None:
                        raise ValueError("Work tool chưa được cấp quyền hoặc chưa nối")
                    # Ghi intent TRƯỚC side effect. Crash giữa chừng không replay tự động.
                    self._store(job, "tool_dispatched", _json({"action": action, "args": args})[:5000])
                    if job.cancel_requested or not self._should_continue(job.job_id):
                        job.status = "cancelled"
                        self._store(job, "cancelled")
                        return
                    result = self.tool(action, args)
                    if job.cancel_requested or not self._should_continue(job.job_id):
                        job.status = "cancelled"
                        # Tool đã có thể phát sinh tác dụng phụ; chỉ bỏ kết quả đến muộn,
                        # không tự tuyên bố tool đã rollback hoặc side effect chưa xảy ra.
                        self._store(job, "late_tool_result_discarded")
                        return
                    success = bool(getattr(result, "success", False))
                    evidence = list(getattr(result, "evidence", ()) or ())
                    message = str(getattr(result, "message", "") or "")
                    data = getattr(result, "data", {}) or {}
                    detail = _json({"action": action, "ok": success, "message": message,
                                    "evidence": evidence, "data": data})[:5000]
                    job.last_evidence = detail
                    job.summary = narrative or ("Đã nhận kết quả công cụ." if success else "Công cụ báo lỗi.")
                    self._store(job, "tool_result", detail)
                    self._publish(job, "tool_result", job.summary)
                elif kind in {"complete", "wait_user"}:
                    # waiting_user phải nhả quyền sở hữu *cùng lúc* status đổi;
                    # nếu không resume nhìn thấy waiting_user mà _running còn job cũ.
                    with self._lock:
                        job.status = "completed" if kind == "complete" else "waiting_user"
                        job.summary = narrative
                        self._store(job, kind, narrative)
                        if kind == "wait_user" and self._running.get(job.job_id) is job:
                            self._running.pop(job.job_id, None)
                    self._publish(job, kind, narrative)
                    return
                else:
                    raise ValueError("Loại bước công việc không hợp lệ")
            job.status = "needs_review"
            job.summary = "Đã tới giới hạn bước; cần Eris quyết định tiếp."
            self._store(job, "step_limit")
        except Exception as exc:
            job.status = "failed"
            job.summary = "Work Lane thất bại: " + type(exc).__name__
            self._store(job, "error", type(exc).__name__)
        finally:
            self._publish(job, "state", job.summary)
            with self._lock:
                # Lượt cũ tuyệt đối không được dọn nhầm lượt resume vừa khởi động.
                if self._running.get(job.job_id) is job:
                    self._running.pop(job.job_id, None)


def plan_with_router(router: Any, context: Mapping[str, Any]) -> Mapping[str, Any]:
    """Gọi broker/model đã có, không sinh model mới hay đưa work vào chat_main."""
    call = getattr(router, "invoke_structured_worker", None)
    client = (getattr(router, "deep_client", None) or getattr(router, "surface_client", None))
    if not callable(call) or client is None:
        raise RuntimeError("work_model_unavailable")
    response = call(
        client,
        system=("Bạn là nhánh THỰC HIỆN CÔNG VIỆC được Eris Root giao, không phải chủ thể nói chuyện. "
                "Mỗi bước trả JSON kind=progress/run_tool/show/edit/say/complete/wait_user, "
                "text, action_id, arguments. kind=show dùng arguments={operation:insert_block,block_id,block:{type,latex/text/...},base_revision}; "
                "kind=edit dùng replace_block/remove_block với base_revision lấy từ document. "
                "kind=say chỉ là ĐỀ XUẤT cho Root, không tự phát TTS. "
                "Hãy chia công việc thành nhiều bước, gọi tool thật khi có ích, dựa trên EVIDENCE "
                "đã trả về để quyết định bước tiếp. KHÔNG tự nhận tool đã chạy khi chưa có receipt. "
                "Không tạo lời Bubble, không tự tạo mục tiêu ngoài task. "
                "Nếu chưa có căn cứ hoàn tất, dùng wait_user hoặc progress; không bịa kết quả. "
                "Có thể run_tool search_capabilities(query) rồi activate_skill(skill_name) để đọc SKILL.md, "
                "nhưng Skill không mở thêm quyền thực thi. Dùng active_skills và evidence_digest "
                "để giữ ngữ cảnh công việc dài. Mỗi bước ngắn, tool chỉ trong allowed_tools."),
        payload=dict(context), schema=_WORK_STEP_SCHEMA,
        name="eris_parallel_work_step", max_tokens=1000,
        inference_tier="subconscious_4b", priority=65, timeout_seconds=45.0,
        cognitive_lane="work_background", temperature=0.2,
    )
    if not isinstance(response, Mapping):
        raise ValueError("work_model_invalid_response")
    return response
