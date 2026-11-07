# -*- coding: utf-8 -*-
"""ActionCapability V12.

Adapter data-only giữa RootAgent và ``action_executor`` hiện hữu. Capability này
không viết câu public và không quyết định action nào nên chạy; RootAgent phải
chọn capability/action trước, còn executor vẫn sở hữu validation/permission/risk.
"""
from __future__ import annotations

from typing import Any, Mapping


class ActionCapability:
    def __init__(self, executor: Any | None, *, logger: Any = None) -> None:
        self.executor = executor
        self.logger = logger

    def invoke_for_root(self, *, arguments: Mapping[str, Any], event: Any, recent_history: Any) -> Mapping[str, Any]:
        del event, recent_history
        if self.executor is None:
            return {"status": "unavailable", "reason": "action_executor_missing"}
        action_id = str(
            arguments.get("action_id")
            or arguments.get("capability_id")
            or arguments.get("action")
            or ""
        ).strip()
        if not action_id:
            return {
                "status": "invalid",
                "reason": "action_id_required",
                "hint": "Pass action_id/capability_id plus arguments. Do not claim execution before a successful result.",
            }
        if action_id in {"search_web", "project_execute_task"}:
            return {"status": "retired", "reason": f"retired_action:{action_id}"}
        typed = arguments.get("arguments")
        typed_args = dict(typed) if isinstance(typed, Mapping) else {
            str(k): v for k, v in arguments.items()
            if k not in {"action_id", "capability_id", "action", "action_input"}
        }
        try:
            execute_typed = getattr(self.executor, "execute_capability", None)
            if callable(execute_typed):
                result = execute_typed(action_id, typed_args)
            else:
                execute = getattr(self.executor, "execute", None)
                if not callable(execute):
                    return {"status": "unavailable", "reason": "action_executor_has_no_execute"}
                result = execute(action_id, str(arguments.get("action_input") or ""))
            status = getattr(result, "status", "")
            status_value = getattr(status, "value", status)
            public_meta = getattr(result, "to_public_meta", None)
            meta = public_meta() if callable(public_meta) else {}
            return {
                "status": str(status_value or ("ok" if bool(getattr(result, "success", False)) else "failed")),
                "action_id": action_id,
                "success": bool(getattr(result, "success", False)),
                "message": str(getattr(result, "message", "") or "")[:12000],
                "data": dict(getattr(result, "data", {}) or {}),
                "evidence": list(getattr(result, "evidence", ()) or ())[:64],
                "error_kind": str(getattr(result, "error_kind", "") or ""),
                "observer_reason": str(getattr(result, "observer_reason", "") or ""),
                "public_meta": dict(meta or {}) if isinstance(meta, Mapping) else {},
            }
        except Exception as exc:  # noqa: BLE001
            self._log("debug", "[action-capability-v12] failed action=%s: %s", action_id, exc)
            return {"status": "failed", "action_id": action_id, "error": type(exc).__name__, "message": str(exc)[:2400]}

    def _log(self, level: str, message: str, *args: Any) -> None:
        method = getattr(self.logger, level, None) if self.logger is not None else None
        if callable(method):
            try:
                method(message, *args)
            except Exception:
                pass


__all__ = ["ActionCapability"]
