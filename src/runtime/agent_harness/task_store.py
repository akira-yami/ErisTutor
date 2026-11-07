# -*- coding: utf-8 -*-
"""Durable task/event store for the Agent Console.

The store deliberately owns *task execution metadata*, not Eris' personality or
conversation memory.  LangGraph checkpoints remain the authority for the actual
agent state; this file is a small durable projection used by the UI and control
plane.
"""
from __future__ import annotations

import json
import os
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Iterable

from .coding_state import initial_coding_state, merge_coding_state, normalize_coding_state

_SCHEMA_VERSION = 5
_MAX_TASKS = 60
_MAX_EVENTS_PER_TASK = 360
_MAX_CONTROLS_PER_TASK = 80
_MAX_CHANGESETS_PER_TASK = 48


def default_agent_console_state_path() -> Path:
    explicit = str(os.getenv("ERIS_AGENT_CONSOLE_STATE_PATH", "") or "").strip()
    if explicit:
        return Path(explicit).expanduser().resolve()
    root = Path(str(os.getenv("ERIS_STATE_DIR", ".eris_state") or ".eris_state")).expanduser()
    return (root / "agent_console" / "tasks.json").resolve()


class AgentTaskStore:
    """Thread-safe JSON projection of agent tasks, events and control messages."""

    def __init__(self, path: str | os.PathLike[str] | None = None) -> None:
        self.path = Path(path).expanduser().resolve() if path else default_agent_console_state_path()
        self._lock = threading.RLock()
        self._data = self._load()

    def _empty(self) -> dict[str, Any]:
        return {"version": _SCHEMA_VERSION, "next_event_seq": 1, "tasks": []}

    def _load(self) -> dict[str, Any]:
        try:
            if not self.path.exists():
                return self._empty()
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict):
                return self._empty()
            tasks = payload.get("tasks")
            if not isinstance(tasks, list):
                tasks = []
            tasks = tasks[-_MAX_TASKS:]

            # MIGRATION V2: event seq là cursor tăng đơn điệu cho WS replay.
            # State V1 cũ không có seq nên ta gán tiếp từ seq lớn nhất đã thấy;
            # không rewrite file ngay lúc boot để tránh I/O không cần thiết.
            max_seq = 0
            missing: list[dict[str, Any]] = []
            for task in tasks:
                if not isinstance(task, dict):
                    continue
                for event in task.get("events", []) or []:
                    if not isinstance(event, dict):
                        continue
                    try:
                        seq = int(event.get("seq", 0) or 0)
                    except (TypeError, ValueError):
                        seq = 0
                    if seq > 0:
                        max_seq = max(max_seq, seq)
                    else:
                        missing.append(event)
            for event in sorted(missing, key=lambda item: (float(item.get("created_at", 0.0) or 0.0), str(item.get("event_id", "")))):
                max_seq += 1
                event["seq"] = max_seq

            try:
                declared_next = int(payload.get("next_event_seq", 0) or 0)
            except (TypeError, ValueError):
                declared_next = 0
            # MIGRATION V3: mọi task có semantic coding_state. Đây là telemetry
            # công khai của execution, không phải chain-of-thought của model.
            for task in tasks:
                if not isinstance(task, dict):
                    continue
                task["coding_state"] = normalize_coding_state(
                    task.get("coding_state"),
                    objective=str(task.get("clarified_goal", "") or task.get("goal", "") or ""),
                )
                raw_team = task.get("coding_team") if isinstance(task.get("coding_team"), dict) else {}
                metadata = task.get("metadata") if isinstance(task.get("metadata"), dict) else {}
                task["coding_team"] = {
                    "topology": str(raw_team.get("topology", metadata.get("team_topology", "")) or ""),
                    "roles": [str(x) for x in (raw_team.get("roles") or []) if str(x or "").strip()][:8],
                    "role": str(raw_team.get("role", "") or ""),
                    "review_round": int(raw_team.get("review_round", 0) or 0),
                    "artifacts": dict(raw_team.get("artifacts") or {}) if isinstance(raw_team.get("artifacts"), dict) else {},
                    "last_findings": [dict(x) for x in (raw_team.get("last_findings") or []) if isinstance(x, dict)][:20],
                }
                raw_sets = task.get("change_sets") if isinstance(task.get("change_sets"), list) else []
                task["change_sets"] = [dict(item) for item in raw_sets[-_MAX_CHANGESETS_PER_TASK:] if isinstance(item, dict)]
                self._repair_terminal_status_locked(task)

            return {
                "version": _SCHEMA_VERSION,
                "next_event_seq": max(max_seq + 1, declared_next, 1),
                "tasks": tasks,
            }
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            return self._empty()

    def _save_locked(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(json.dumps(self._data, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, self.path)

    @staticmethod
    def _now() -> float:
        return time.time()

    @staticmethod
    def _compact_text(value: Any, limit: int) -> str:
        text = str(value or "").strip()
        if len(text) <= limit:
            return text
        return text[: max(0, limit - 1)].rstrip() + "…"

    def _find_locked(self, task_id: str) -> dict[str, Any] | None:
        key = str(task_id or "").strip()
        for task in reversed(self._data["tasks"]):
            if str(task.get("task_id", "")) == key:
                return task
        return None

    def _repair_terminal_status_locked(self, task: dict[str, Any]) -> None:
        if str(task.get("status", "") or "").strip().lower() in {"completed", "failed", "stopped"}:
            return
        for event in reversed(task.get("events", []) or []):
            if not isinstance(event, dict):
                continue
            kind = str(event.get("type", "") or "").strip()
            if kind == "task_completed":
                task["status"] = "completed"
                task["finished_at"] = float(event.get("created_at", 0.0) or task.get("finished_at", 0.0) or 0.0)
                return
            if kind == "task_failed":
                task["status"] = "failed"
                task["finished_at"] = float(event.get("created_at", 0.0) or task.get("finished_at", 0.0) or 0.0)
                return
            if kind == "task_stopped":
                task["status"] = "stopped"
                task["finished_at"] = float(event.get("created_at", 0.0) or task.get("finished_at", 0.0) or 0.0)
                return

    def create_task(
        self,
        goal: str,
        *,
        task_id: str = "",
        executor: str = "gemini",
        model: str = "",
        thread_id: str = "",
        session_id: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            task_id = str(task_id or "").strip() or f"task_{uuid.uuid4().hex[:12]}"
            existing = self._find_locked(task_id)
            if existing is not None:
                return dict(existing)
            now = self._now()
            task = {
                "task_id": task_id,
                "thread_id": str(thread_id or task_id),
                "session_id": str(session_id or ""),
                "goal": self._compact_text(goal, 2000),
                "clarified_goal": "",
                "status": "queued",
                "executor": str(executor or "gemini"),
                "model": str(model or ""),
                "created_at": now,
                "updated_at": now,
                "started_at": 0.0,
                "finished_at": 0.0,
                "current_step": 0,
                "active_skills": [],
                "changed_files": [],
                "change_sets": [],
                "tests": {"passed": 0, "failed": 0, "summary": ""},
                # State semantic nhỏ để VS Code/Eris main hiểu executor đang làm gì
                # mà không phải parse stdout/tool JSON hay chạm chain-of-thought.
                "coding_state": initial_coding_state(goal),
                "coding_team": {
                    "topology": str((metadata or {}).get("team_topology", "") or ""),
                    "roles": [],
                    "role": "",
                    "review_round": 0,
                    "artifacts": {},
                    "last_findings": [],
                },
                "checkpoint_id": "",
                "checkpoint_count": 0,
                "last_event_seq": 0,
                "pending_interrupt": {},
                "events": [],
                "controls": [],
                "metadata": dict(metadata or {}),
            }
            self._data["tasks"].append(task)
            self._data["tasks"] = self._data["tasks"][-_MAX_TASKS:]
            self._save_locked()
            return dict(task)

    def update_task(self, task_id: str, **fields: Any) -> dict[str, Any] | None:
        allowed = {
            "goal", "clarified_goal", "status", "executor", "model", "started_at", "finished_at",
            "current_step", "active_skills", "changed_files", "change_sets", "tests", "coding_state", "coding_team", "checkpoint_id",
            "checkpoint_count", "metadata", "thread_id", "session_id", "pending_interrupt", "last_event_seq",
        }
        with self._lock:
            task = self._find_locked(task_id)
            if task is None:
                return None
            for key, value in fields.items():
                if key not in allowed:
                    continue
                if key in {"goal", "clarified_goal"}:
                    value = self._compact_text(value, 2000)
                elif key == "coding_state":
                    value = normalize_coding_state(
                        value, objective=str(task.get("clarified_goal", "") or task.get("goal", "") or "")
                    )
                elif key == "coding_team":
                    current = task.get("coding_team") if isinstance(task.get("coding_team"), dict) else {}
                    incoming = value if isinstance(value, dict) else {}
                    merged = dict(current)
                    merged.update(incoming)
                    value = merged
                task[key] = value
            task["updated_at"] = self._now()
            self._save_locked()
            return dict(task)

    def append_event(
        self,
        task_id: str,
        event_type: str,
        *,
        actor: str = "system",
        summary: str = "",
        detail: str = "",
        step: int | None = None,
        importance: float = 0.5,
        data: dict[str, Any] | None = None,
        event_id: str = "",
    ) -> dict[str, Any] | None:
        with self._lock:
            task = self._find_locked(task_id)
            if task is None:
                return None
            try:
                seq = int(self._data.get("next_event_seq", 1) or 1)
            except (TypeError, ValueError):
                seq = 1
            self._data["next_event_seq"] = seq + 1
            event = {
                "event_id": str(event_id or "").strip() or f"ae_{uuid.uuid4().hex[:14]}",
                "seq": seq,
                "type": str(event_type or "agent_status"),
                "actor": str(actor or "system"),
                "summary": self._compact_text(summary, 1600),
                "detail": self._compact_text(detail, 8000),
                "step": int(step if step is not None else task.get("current_step", 0) or 0),
                "importance": max(0.0, min(1.0, float(importance))),
                "created_at": self._now(),
                "data": dict(data or {}),
            }
            events = task.setdefault("events", [])
            events.append(event)
            task["events"] = events[-_MAX_EVENTS_PER_TASK:]
            task["updated_at"] = event["created_at"]
            task["last_event_seq"] = seq
            if step is not None:
                task["current_step"] = max(int(task.get("current_step", 0) or 0), int(step))
            self._project_event_locked(task, event)
            self._save_locked()
            return dict(event)

    def _project_event_locked(self, task: dict[str, Any], event: dict[str, Any]) -> None:
        kind = str(event.get("type", ""))
        data = event.get("data") if isinstance(event.get("data"), dict) else {}
        now = float(event.get("created_at", self._now()) or self._now())
        if kind == "task_started":
            task["status"] = "running"
            task["started_at"] = task.get("started_at") or now
            task["coding_state"] = merge_coding_state(
                task.get("coding_state"),
                {"phase": "starting", "waiting_for": "", "blocker": ""},
                objective=str(task.get("clarified_goal", "") or task.get("goal", "") or ""),
            )
        elif kind == "task_completed":
            task["status"] = "completed"
            task["finished_at"] = now
            task["coding_state"] = merge_coding_state(
                task.get("coding_state"),
                {"phase": "completed", "current_action": "", "waiting_for": "", "blocker": "", "next_action": ""},
                objective=str(task.get("clarified_goal", "") or task.get("goal", "") or ""),
            )
        elif kind == "task_failed":
            task["status"] = "failed"
            task["finished_at"] = now
            task["coding_state"] = merge_coding_state(
                task.get("coding_state"),
                {"phase": "failed", "current_action": "", "blocker": str(event.get("summary", "") or "Task failed")},
                objective=str(task.get("clarified_goal", "") or task.get("goal", "") or ""),
            )
        elif kind == "task_paused":
            task["status"] = "paused"
            task["coding_state"] = merge_coding_state(task.get("coding_state"), {"phase": "paused", "waiting_for": "resume"})
        elif kind == "task_resumed":
            task["status"] = "running"
            task["coding_state"] = merge_coding_state(task.get("coding_state"), {"phase": "resuming", "waiting_for": "", "blocker": ""})
        elif kind == "task_stopped":
            task["status"] = "stopped"
            task["finished_at"] = now
            task["coding_state"] = merge_coding_state(task.get("coding_state"), {"phase": "stopped", "current_action": "", "waiting_for": ""})
        elif kind == "coding_team_selected":
            team = dict(task.get("coding_team") or {})
            roles = [str(x) for x in (data.get("roles") or []) if str(x or "").strip()][:8]
            team.update({
                "topology": str(data.get("topology", team.get("topology", "")) or ""),
                "roles": roles,
                "role": roles[0] if roles else str(team.get("role", "") or ""),
            })
            task["coding_team"] = team
        elif kind == "team_handoff":
            team = dict(task.get("coding_team") or {})
            artifacts = dict(team.get("artifacts") or {}) if isinstance(team.get("artifacts"), dict) else {}
            source = str(data.get("from", "") or "").strip()
            artifact = str(data.get("artifact", "") or "").strip()
            if source and artifact:
                artifacts[source] = self._compact_text(artifact, 8000)
            team.update({
                "role": str(data.get("to", team.get("role", "")) or ""),
                "review_round": int(data.get("review_round", team.get("review_round", 0)) or 0),
                "artifacts": artifacts,
            })
            findings = data.get("findings") if isinstance(data.get("findings"), list) else []
            if findings:
                team["last_findings"] = [dict(x) if isinstance(x, dict) else {"message": str(x)} for x in findings[:20]]
            task["coding_team"] = team
        elif kind == "team_review_changes_requested":
            team = dict(task.get("coding_team") or {})
            findings = data.get("findings") if isinstance(data.get("findings"), list) else []
            team["last_findings"] = [dict(x) if isinstance(x, dict) else {"message": str(x)} for x in findings[:20]]
            team["review_round"] = int(data.get("review_round", team.get("review_round", 0)) or 0)
            task["coding_team"] = team
        elif kind == "permission_requested":
            task["status"] = "waiting_permission"
            task["pending_interrupt"] = dict(data or {})
            task["coding_state"] = merge_coding_state(task.get("coding_state"), {
                "phase": "waiting_permission", "waiting_for": "user_permission",
                "current_action": str(data.get("question", "") or event.get("summary", "") or ""),
            })
        elif kind in {"clarification_requested", "interrupt_requested"}:
            task["status"] = "waiting_user"
            task["pending_interrupt"] = dict(data or {})
            task["coding_state"] = merge_coding_state(task.get("coding_state"), {
                "phase": "waiting_user", "waiting_for": "user_choice",
                "current_action": str(data.get("question", "") or event.get("summary", "") or ""),
            })
        elif kind == "interrupt_resolved":
            task["status"] = "running"
            task["pending_interrupt"] = {}
            task["coding_state"] = merge_coding_state(task.get("coding_state"), {"phase": "resuming", "waiting_for": "", "current_action": ""})
        elif kind == "coding_state":
            task["coding_state"] = merge_coding_state(
                task.get("coding_state"), data,
                objective=str(task.get("clarified_goal", "") or task.get("goal", "") or ""),
            )
        elif kind in {"checkpoint_created", "checkpoint_restored"}:
            checkpoint_id = str(data.get("checkpoint_id", "") or "")
            if checkpoint_id:
                task["checkpoint_id"] = checkpoint_id
            if kind == "checkpoint_created":
                task["checkpoint_count"] = int(task.get("checkpoint_count", 0) or 0) + 1
        elif kind == "skill_activated":
            skill = str(data.get("skill", "") or "").strip()
            if skill:
                skills = list(task.get("active_skills") or [])
                if skill not in skills:
                    skills.append(skill)
                task["active_skills"] = skills[-24:]
        elif kind == "file_changed":
            path = str(data.get("path", "") or "").strip()
            if path:
                files = list(task.get("changed_files") or [])
                if path not in files:
                    files.append(path)
                task["changed_files"] = files[-120:]
                semantic_files = list((task.get("coding_state") or {}).get("files_modified") or [])
                if path not in semantic_files:
                    semantic_files.append(path)
                semantic_patch: dict[str, Any] = {
                    "current_file": path,
                    "files_modified": semantic_files[-80:],
                }
                # GraphBuilder thường đã phát coding_state=verifying trước event
                # artifact này. Chỉ suy ra phase khi event tới từ producer cũ.
                current_phase = str((task.get("coding_state") or {}).get("phase", "") or "")
                if current_phase in {"", "queued", "starting"}:
                    semantic_patch["phase"] = "modifying"
                task["coding_state"] = merge_coding_state(task.get("coding_state"), semantic_patch)
        elif kind == "changeset_created":
            change_set_id = str(data.get("change_set_id", data.get("operation_id", "")) or "").strip()
            if change_set_id:
                rows = [dict(item) for item in task.get("change_sets", []) if isinstance(item, dict)]
                current = next((item for item in rows if str(item.get("change_set_id", "")) == change_set_id), None)
                payload = {
                    "change_set_id": change_set_id,
                    "operation_id": str(data.get("operation_id", "") or change_set_id),
                    "checkpoint_id": str(data.get("checkpoint_id", "") or ""),
                    "files": [str(item) for item in (data.get("files") or []) if str(item or "").strip()][:120],
                    "status": str(data.get("status", "pending") or "pending"),
                    "created_at": now,
                    "updated_at": now,
                }
                if current is None:
                    rows.append(payload)
                else:
                    current.update(payload)
                task["change_sets"] = rows[-_MAX_CHANGESETS_PER_TASK:]
        elif kind in {"changeset_accepted", "changeset_reverted"}:
            change_set_id = str(data.get("change_set_id", data.get("operation_id", "")) or "").strip()
            rows = [dict(item) for item in task.get("change_sets", []) if isinstance(item, dict)]
            for item in rows:
                if str(item.get("change_set_id", "")) == change_set_id:
                    item["status"] = "accepted" if kind == "changeset_accepted" else "reverted"
                    item["updated_at"] = now
            task["change_sets"] = rows[-_MAX_CHANGESETS_PER_TASK:]
        elif kind == "test_result":
            passed = int(data.get("passed", 0) or 0)
            failed = int(data.get("failed", 0) or 0)
            summary = self._compact_text(data.get("summary", event.get("summary", "")), 500)
            task["tests"] = {"passed": passed, "failed": failed, "summary": summary}
            semantic_patch = {
                "test_state": {
                    "status": "failed" if failed else "passed",
                    "passed": passed,
                    "failed": failed,
                    "summary": summary,
                },
                "blocker": summary if failed else "",
            }
            current_phase = str((task.get("coding_state") or {}).get("phase", "") or "")
            if current_phase in {"", "queued", "starting", "testing"}:
                semantic_patch["phase"] = "blocked" if failed else "verifying"
            task["coding_state"] = merge_coding_state(task.get("coding_state"), semantic_patch)

    def execution_projection(self, task_id: str) -> dict[str, Any]:
        """Small rollbackable UI projection, separate from immutable event history."""
        with self._lock:
            task = self._find_locked(task_id)
            if task is None:
                return {}
            return {
                "current_step": int(task.get("current_step", 0) or 0),
                "active_skills": list(task.get("active_skills") or []),
                "changed_files": list(task.get("changed_files") or []),
                "change_sets": [dict(item) for item in task.get("change_sets", []) if isinstance(item, dict)],
                "tests": dict(task.get("tests") or {}),
                "coding_state": normalize_coding_state(
                    task.get("coding_state"), objective=str(task.get("clarified_goal", "") or task.get("goal", "") or "")
                ),
                "coding_team": dict(task.get("coding_team") or {}),
                "checkpoint_id": str(task.get("checkpoint_id", "") or ""),
                "checkpoint_count": int(task.get("checkpoint_count", 0) or 0),
            }

    def restore_execution_projection(
        self,
        task_id: str,
        projection: dict[str, Any],
        *,
        checkpoint_id: str = "",
    ) -> dict[str, Any] | None:
        """Restore task counters/state while preserving the audit/event timeline."""
        if not isinstance(projection, dict):
            return None
        with self._lock:
            task = self._find_locked(task_id)
            if task is None:
                return None
            task["current_step"] = max(0, int(projection.get("current_step", 0) or 0))
            task["active_skills"] = [str(x) for x in list(projection.get("active_skills") or [])][-24:]
            task["changed_files"] = [str(x) for x in list(projection.get("changed_files") or [])][-120:]
            task["change_sets"] = [
                dict(item) for item in list(projection.get("change_sets") or [])
                if isinstance(item, dict)
            ][-_MAX_CHANGESETS_PER_TASK:]
            tests = projection.get("tests") if isinstance(projection.get("tests"), dict) else {}
            task["tests"] = {
                "passed": int(tests.get("passed", 0) or 0),
                "failed": int(tests.get("failed", 0) or 0),
                "summary": self._compact_text(tests.get("summary", ""), 500),
            }
            task["coding_state"] = normalize_coding_state(
                projection.get("coding_state"), objective=str(task.get("clarified_goal", "") or task.get("goal", "") or "")
            )
            if isinstance(projection.get("coding_team"), dict):
                task["coding_team"] = dict(projection.get("coding_team") or {})
            task["checkpoint_id"] = str(checkpoint_id or projection.get("checkpoint_id", "") or "")
            # Count is historical, so never decrease it during rollback.
            task["checkpoint_count"] = max(
                int(task.get("checkpoint_count", 0) or 0),
                int(projection.get("checkpoint_count", 0) or 0),
            )
            task["status"] = "running"
            task["updated_at"] = self._now()
            self._save_locked()
            return dict(task)

    def enqueue_control(
        self,
        task_id: str,
        action: str,
        *,
        text: str = "",
        checkpoint_id: str = "",
        request_id: str = "",
        data: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        with self._lock:
            task = self._find_locked(task_id)
            if task is None:
                return None
            control = {
                "control_id": str(request_id or "").strip() or f"ctl_{uuid.uuid4().hex[:12]}",
                "action": str(action or "").strip().lower(),
                "text": self._compact_text(text, 4000),
                "checkpoint_id": str(checkpoint_id or "").strip(),
                "created_at": self._now(),
                "consumed": False,
                "data": dict(data or {}),
            }
            controls = task.setdefault("controls", [])
            controls.append(control)
            task["controls"] = controls[-_MAX_CONTROLS_PER_TASK:]
            task["updated_at"] = control["created_at"]
            self._save_locked()
            return dict(control)

    def consume_controls(self, task_id: str) -> list[dict[str, Any]]:
        with self._lock:
            task = self._find_locked(task_id)
            if task is None:
                return []
            pending = [dict(x) for x in task.get("controls", []) if isinstance(x, dict) and not x.get("consumed")]
            if not pending:
                return []
            pending_ids = {x.get("control_id") for x in pending}
            for item in task.get("controls", []):
                if isinstance(item, dict) and item.get("control_id") in pending_ids:
                    item["consumed"] = True
                    item["consumed_at"] = self._now()
            self._save_locked()
            return pending


    def latest_event_seq(self) -> int:
        """Cursor event lớn nhất đã persist, tiện cho reconnect/test client."""
        with self._lock:
            return max(0, int(self._data.get("next_event_seq", 1) or 1) - 1)

    def events_since(
        self,
        seq: int,
        *,
        task_id: str = "",
        limit: int = 500,
    ) -> list[dict[str, Any]]:
        """Trả event theo cursor tăng đơn điệu để WS reconnect chỉ replay delta.

        Đây là projection đọc thuần; lịch sử task vẫn là nguồn audit chính.
        """
        try:
            after = max(0, int(seq))
        except (TypeError, ValueError):
            after = 0
        cap = max(1, min(2000, int(limit or 500)))
        wanted_task = str(task_id or "").strip()
        with self._lock:
            rows: list[dict[str, Any]] = []
            for task in self._data.get("tasks", []):
                if not isinstance(task, dict):
                    continue
                current_task_id = str(task.get("task_id", "") or "")
                if wanted_task and current_task_id != wanted_task:
                    continue
                for event in task.get("events", []) or []:
                    if not isinstance(event, dict):
                        continue
                    try:
                        event_seq = int(event.get("seq", 0) or 0)
                    except (TypeError, ValueError):
                        event_seq = 0
                    if event_seq <= after:
                        continue
                    item = self._public_event(event)
                    item["task_id"] = current_task_id
                    item["thread_id"] = str(task.get("thread_id", "") or "")
                    item["session_id"] = str(task.get("session_id", "") or "")
                    rows.append(item)
            rows.sort(key=lambda item: int(item.get("seq", 0) or 0))
            return rows[:cap]

    def latest_task_for_thread(
        self,
        thread_id: str,
        *,
        session_id: str = "",
        statuses: Iterable[str] | None = None,
        event_limit: int = 0,
    ) -> dict[str, Any] | None:
        """Tìm task mới nhất của một chat thread để tránh chạy hai graph chồng nhau."""
        wanted_thread = str(thread_id or "").strip()
        wanted_session = str(session_id or "").strip()
        allowed = {str(item or "").strip().lower() for item in (statuses or []) if str(item or "").strip()}
        if not wanted_thread:
            return None
        with self._lock:
            for task in reversed(self._data.get("tasks", [])):
                if not isinstance(task, dict) or str(task.get("thread_id", "") or "") != wanted_thread:
                    continue
                if wanted_session and str(task.get("session_id", "") or "") not in {"", wanted_session}:
                    continue
                if allowed and str(task.get("status", "") or "").strip().lower() not in allowed:
                    continue
                return self._public_task(task, event_limit=event_limit)
        return None

    def tasks_for_thread(
        self,
        thread_id: str,
        *,
        session_id: str = "",
        limit: int = 24,
        event_limit: int = 160,
    ) -> list[dict[str, Any]]:
        """Lịch sử một chat thread theo thứ tự thời gian để Webview restore thật."""
        wanted_thread = str(thread_id or "").strip()
        wanted_session = str(session_id or "").strip()
        if not wanted_thread:
            return []
        cap = max(1, min(100, int(limit or 24)))
        with self._lock:
            rows: list[dict[str, Any]] = []
            for task in self._data.get("tasks", []):
                if not isinstance(task, dict) or str(task.get("thread_id", "") or "") != wanted_thread:
                    continue
                task_session = str(task.get("session_id", "") or "")
                if wanted_session and task_session not in {"", wanted_session}:
                    continue
                rows.append(self._public_task(task, event_limit=event_limit))
            return rows[-cap:]

    def list_tasks(
        self,
        *,
        session_id: str = "",
        limit: int = 24,
        event_limit: int = 0,
    ) -> list[dict[str, Any]]:
        """Danh sách task gọn cho IDE restore state sau reconnect/restart."""
        wanted_session = str(session_id or "").strip()
        cap = max(1, min(100, int(limit or 24)))
        with self._lock:
            rows: list[dict[str, Any]] = []
            for task in reversed(self._data.get("tasks", [])):
                if not isinstance(task, dict):
                    continue
                task_session = str(task.get("session_id", "") or "")
                if wanted_session and task_session not in {"", wanted_session}:
                    continue
                rows.append(self._public_task(task, event_limit=event_limit))
                if len(rows) >= cap:
                    break
            return rows

    def latest_pending_change_set(self, task_id: str) -> dict[str, Any] | None:
        """Lấy ChangeSet pending mới nhất để revert không đạp lên patch mới hơn."""
        with self._lock:
            task = self._find_locked(task_id)
            if task is None:
                return None
            for item in reversed(task.get("change_sets", []) or []):
                if isinstance(item, dict) and str(item.get("status", "pending") or "pending") == "pending":
                    return dict(item)
        return None

    def get_task(self, task_id: str, *, event_limit: int = 100) -> dict[str, Any] | None:
        with self._lock:
            task = self._find_locked(task_id)
            if task is None:
                return None
            return self._public_task(task, event_limit=event_limit)

    def snapshot(self, *, selected_task_id: str = "", task_limit: int = 18, event_limit: int = 120) -> dict[str, Any]:
        with self._lock:
            tasks = list(reversed(self._data.get("tasks", [])))[: max(1, task_limit)]
            if not selected_task_id and tasks:
                selected_task_id = str(tasks[0].get("task_id", ""))
            result = []
            for task in tasks:
                limit = event_limit if str(task.get("task_id", "")) == selected_task_id else 6
                result.append(self._public_task(task, event_limit=limit))
            return {
                "version": "agent_console.v1",
                "selected_task_id": selected_task_id,
                "tasks": result,
                "generated_at": self._now(),
            }

    @staticmethod
    def _public_task(task: dict[str, Any], *, event_limit: int) -> dict[str, Any]:
        public = {k: v for k, v in task.items() if k not in {"controls", "events"}}
        public["coding_state"] = normalize_coding_state(
            task.get("coding_state"), objective=str(task.get("clarified_goal", "") or task.get("goal", "") or "")
        )
        events = list(task.get("events") or [])
        selected = events[-max(0, event_limit):] if event_limit else []
        public["events"] = [AgentTaskStore._public_event(item) for item in selected if isinstance(item, dict)]
        public["pending_controls"] = sum(
            1 for item in task.get("controls", []) if isinstance(item, dict) and not item.get("consumed")
        )
        return public

    @staticmethod
    def _public_event(event: dict[str, Any]) -> dict[str, Any]:
        result = dict(event)
        detail = str(result.get("detail", "") or "")
        if len(detail) > 1600:
            result["detail"] = detail[:1599].rstrip() + "…"
        data = result.get("data")
        if isinstance(data, dict):
            kind = str(result.get("type", "") or "")

            # Các event này chính là contract của IDE, không phải raw debug data.
            # Generic truncation cũ từng biến diff/message thành {truncated:true}
            # sau reconnect, tức live thì đẹp còn mở lại chat thì mất ruột.
            if kind == "user_message":
                result["data"] = {
                    "content": AgentTaskStore._compact_text(data.get("content", result.get("summary", "")), 8000),
                    "source": AgentTaskStore._compact_text(data.get("source", ""), 80),
                }
                return result
            if kind == "task_completed":
                # Final answer là dữ liệu public cần replay lossless sau reconnect.
                # Chỉ detail/summary debug mới bị compact; nếu cắt final ở đây thì
                # live chat và history sẽ cho hai câu trả lời khác nhau.
                result["data"] = {
                    "final": str(data.get("final", result.get("detail", result.get("summary", ""))) or ""),
                }
                return result
            if kind == "file_changed":
                result["data"] = {
                    "path": AgentTaskStore._compact_text(data.get("path", ""), 700),
                    "tool": AgentTaskStore._compact_text(data.get("tool", ""), 120),
                    "operation_id": AgentTaskStore._compact_text(data.get("operation_id", ""), 160),
                    "diff": AgentTaskStore._compact_text(data.get("diff", ""), 16000),
                }
                return result
            if kind == "diff_observed":
                result["data"] = {
                    "chars": int(data.get("chars", 0) or 0),
                    "diff": AgentTaskStore._compact_text(data.get("diff", ""), 16000),
                }
                return result

            if kind in {"changeset_created", "changeset_accepted", "changeset_reverted"}:
                result["data"] = {
                    "change_set_id": AgentTaskStore._compact_text(data.get("change_set_id", data.get("operation_id", "")), 160),
                    "operation_id": AgentTaskStore._compact_text(data.get("operation_id", ""), 160),
                    "checkpoint_id": AgentTaskStore._compact_text(data.get("checkpoint_id", ""), 220),
                    "status": AgentTaskStore._compact_text(data.get("status", ""), 40),
                    "files": [AgentTaskStore._compact_text(item, 700) for item in (data.get("files") or [])[:120]],
                }
                return result

            if kind == "ide_request":
                result["data"] = {
                    "requestId": AgentTaskStore._compact_text(data.get("requestId", ""), 160),
                    "method": AgentTaskStore._compact_text(data.get("method", ""), 120),
                    "params": dict(data.get("params") or {}) if isinstance(data.get("params"), dict) else {},
                }
                return result

            # Choice cards are UI protocol, not debug payload. Preserve their
            # structure even when the original LLM text is verbose; clamp each
            # field instead of collapsing the whole object into {truncated:true}.
            if str(result.get("type", "") or "") in {"clarification_requested", "interrupt_requested", "permission_requested"}:
                raw_options = data.get("options") if isinstance(data.get("options"), list) else []
                options = []
                for index, item in enumerate(raw_options[:5]):
                    if not isinstance(item, dict):
                        continue
                    label = AgentTaskStore._compact_text(item.get("label", ""), 120)
                    if not label:
                        continue
                    options.append({
                        "id": AgentTaskStore._compact_text(item.get("id", "") or f"option_{index + 1}", 64),
                        "label": label,
                        "description": AgentTaskStore._compact_text(item.get("description", ""), 160),
                        "value": AgentTaskStore._compact_text(item.get("value", "") or label, 220),
                    })
                result["data"] = {
                    "question": AgentTaskStore._compact_text(data.get("question", result.get("summary", "")), 500),
                    "options": options,
                    "allow_other": bool(data.get("allowOther", data.get("allow_other", True))),
                    "reason": AgentTaskStore._compact_text(data.get("reason", ""), 80),
                    "toolCallId": AgentTaskStore._compact_text(data.get("toolCallId", data.get("tool_call_id", "")), 120),
                }
                return result

            try:
                encoded = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
            except (TypeError, ValueError):
                encoded = ""
            if len(encoded) > 3500:
                # Inspector cần metadata nhỏ (path/checkpoint/test), không cần raw tool output.
                result["data"] = {
                    "truncated": True,
                    "keys": [str(key)[:80] for key in list(data.keys())[:24]],
                }
        return result


__all__ = ["AgentTaskStore", "default_agent_console_state_path"]
