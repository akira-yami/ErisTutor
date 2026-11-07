# -*- coding: utf-8 -*-
"""Boundary V12 cho coding/work runtime.

Work capability được phép chạy tool loop/checkpoint/permission của AgentHarness,
nhưng kết quả luôn quay lại RootAgent dưới dạng structured data. Nó tuyệt đối
không gọi UI, writer hay TTS.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping
import uuid


@dataclass(frozen=True, slots=True)
class WorkResult:
    status: str
    summary: str = ""
    artifact_refs: tuple[str, ...] = ()
    needs_permission: bool = False
    permission_request: Mapping[str, Any] | None = None
    metadata: Mapping[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "summary": self.summary,
            "artifact_refs": list(self.artifact_refs),
            "needs_permission": self.needs_permission,
            "permission_request": dict(self.permission_request or {}),
            "metadata": dict(self.metadata or {}),
        }


class WorkCapability:
    def __init__(self, harness: Any | None, *, logger: Any = None, host=None) -> None:
        self.harness = harness
        self.logger = logger
        self.host = host

    def snapshot(self) -> WorkResult:
        if self.harness is None:
            return WorkResult(status="unavailable")
        snapshot = getattr(self.harness, "snapshot", None)
        if not callable(snapshot):
            return WorkResult(status="ready")
        try:
            raw = snapshot()
            return WorkResult(status="ready", metadata=dict(raw) if isinstance(raw, Mapping) else {})
        except Exception as exc:  # noqa: BLE001
            self._log("debug", "[work-capability-v12] snapshot degraded: %s", exc)
            return WorkResult(status="degraded")

    def start_work(self, *, request: str, session_id: str = "", arguments: Mapping[str, Any] | None = None) -> WorkResult:
        """Bắt đầu AgentHarness task và chiếu output thành WorkResult data-only."""

        if self.harness is None or not callable(getattr(self.harness, "invoke", None)):
            return WorkResult(status="unavailable")
        args = dict(arguments or {})
        from runtime.learning_canvas.events import capture_canvas_scope
        scope = capture_canvas_scope(self.host) if self.host is not None else {}
        if scope:
            args["task_id"] = str(args.get("task_id") or "canvas-work-" + uuid.uuid4().hex)
            scopes = getattr(self.host, "_canvas_work_scopes", None)
            if scopes is None:
                scopes = self.host._canvas_work_scopes = {}
            scopes[args["task_id"]] = scope
            while len(scopes) > 256:
                scopes.pop(next(iter(scopes)))
        thread_id = str(args.get("thread_id") or f"conversation-work:{session_id or 'default'}")[:240]
        try:
            raw = self.harness.invoke(
                str(request or "")[:24000],
                thread_id=thread_id,
                task_id=str(args.get("task_id") or "")[:180],
                clarified_goal=str(args.get("clarified_goal") or "")[:24000],
                task_mode=str(args.get("task_mode") or "auto")[:32],
                autopilot=bool(args.get("autopilot", True)),
                source="conversation_root_v12",
                session_id=str(session_id or "")[:180],
                workspace_id=str(args.get("workspace_id") or "")[:180],
                workspace_path=str(args.get("workspace_path") or "")[:4000],
                workspace_name=str(args.get("workspace_name") or "")[:300],
                ide_context=args.get("ide_context") if isinstance(args.get("ide_context"), Mapping) else None,
                model_name=str(args.get("model_name") or "")[:180],
            )
            return self._from_harness_result(raw)
        except Exception as exc:  # noqa: BLE001
            self._log("debug", "[work-capability-v12] start degraded: %s", exc)
            return WorkResult(status="failed", summary=f"{type(exc).__name__}: {exc}"[:2000])

    def resume_work(self, *, task_id: str, response: Any = None, directive: str = "") -> WorkResult:
        if self.harness is None or not callable(getattr(self.harness, "resume_task", None)):
            return WorkResult(status="unavailable")
        try:
            raw = self.harness.resume_task(
                str(task_id or "")[:180],
                response,
                directive=str(directive or "")[:12000],
            )
            return self._from_harness_result(raw)
        except Exception as exc:  # noqa: BLE001
            self._log("debug", "[work-capability-v12] resume degraded: %s", exc)
            return WorkResult(status="failed", summary=f"{type(exc).__name__}: {exc}"[:2000])

    def invoke_for_root(self, *, arguments: Mapping[str, Any], event: Any, recent_history: Any) -> Mapping[str, Any]:
        del recent_history
        operation = str(arguments.get("operation") or "snapshot").strip().lower()
        if operation in {"start", "start_work", "execute"}:
            return self.start_work(
                request=str(arguments.get("request") or getattr(event, "text", "") or ""),
                session_id=str(getattr(event, "session_id", "") or ""),
                arguments=arguments,
            ).to_dict()
        if operation in {"resume", "resume_work"}:
            return self.resume_work(
                task_id=str(arguments.get("task_id") or ""),
                response=arguments.get("response"),
                directive=str(arguments.get("directive") or ""),
            ).to_dict()
        return self.snapshot().to_dict()

    @staticmethod
    def _from_harness_result(raw: Any) -> WorkResult:
        if not isinstance(raw, Mapping):
            return WorkResult(status="completed", summary=str(raw or "")[:16000])
        status = str(raw.get("status") or "completed")[:80]
        interrupt = raw.get("interrupt") if isinstance(raw.get("interrupt"), Mapping) else {}
        needs_permission = status == "waiting_permission" or str(interrupt.get("type") or "") == "permission.request"
        summary = str(
            raw.get("final")
            or raw.get("summary")
            or raw.get("last_summary")
            or raw.get("message")
            or ""
        )[:16000]
        refs = raw.get("artifact_refs") if isinstance(raw.get("artifact_refs"), (list, tuple)) else ()
        return WorkResult(
            status=status,
            summary=summary,
            artifact_refs=tuple(str(item)[:1000] for item in refs[:32]),
            needs_permission=needs_permission,
            permission_request=dict(interrupt) if interrupt else None,
            metadata={
                key: raw.get(key)
                for key in ("task_id", "thread_id", "workspace_id", "change_set_id")
                if raw.get(key) not in {None, ""}
            },
        )

    def _log(self, level: str, message: str, *args: Any) -> None:
        if self.logger is None:
            return
        try:
            getattr(self.logger, level)(message, *args)
        except Exception:
            pass


__all__ = ["WorkCapability", "WorkResult"]
