# -*- coding: utf-8 -*-
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

# Các hằng số và lớp dữ liệu liên quan đến action.
VALID_SYSTEM_ACTION_LIST = (
    "get_local_time",
    "get_now_playing",
    "focus_window",
    "open_url",
    "set_volume",
    "play_pause_media",
    "copy_text",
    "launch_app",
    "get_weather",
    # Năng lực nhận thức nội bộ read-only. Đây là action thật có evidence,
    # không phải prompt khoe rằng Eris "có thể tự kiểm tra".
    "review_discourse_claim",
    "inspect_unresolved_questions",
    "inspect_memory_candidates",
    "compare_recent_turns",
    "inspect_social_state",
    "detect_internal_contradictions",
    "summarize_open_loops",
    "inspect_capability_health",
    # Năng lực đọc môi trường read-only có evidence.
    "get_active_application",
    "read_visible_ui_text",
    "inspect_runtime_log",
    "read_current_error",
    "inspect_selected_file",
    "get_project_status",
    # Năng lực thao tác project autonomous có snapshot/evidence.
    "project_list_files",
    "project_read_file",
    "project_search_text",
    "project_find_symbol",
    "project_semantic_search",
    "project_git_log",
    "project_dependency_summary",
    "project_test_inventory",
    "project_git_diff",
    "project_apply_patch",
    "project_run_tests",
    "project_run_command",
    "project_rollback",
    "project_package_patch",
    "project_execute_task",
    "workspace_join",
    "workspace_switch",
    "workspace_list",
    "workspace_leave",
    "math_workspace",
    "python_workspace",
    "live_workspace_edit",
    "parallel_work",
)

VALID_SYSTEM_ACTIONS = set(VALID_SYSTEM_ACTION_LIST)


class ActionExecutionStatus(str, Enum):
    FAILED = "failed"
    DISPATCHED = "dispatched"
    OBSERVED = "observed"


_OBSERVED_ACTIONS = {
    "get_local_time", "get_now_playing", "get_weather",
    "review_discourse_claim", "inspect_unresolved_questions",
    "inspect_memory_candidates", "compare_recent_turns",
    "inspect_social_state", "detect_internal_contradictions",
    "summarize_open_loops", "inspect_capability_health",
    "get_active_application", "read_visible_ui_text",
    "inspect_runtime_log", "read_current_error",
    "inspect_selected_file", "get_project_status",
    "project_list_files", "project_read_file", "project_search_text",
    "project_find_symbol", "project_semantic_search", "project_git_log", "project_dependency_summary",
    "project_test_inventory", "project_git_diff", "project_apply_patch",
    "project_run_tests", "project_run_command", "project_rollback",
    "project_package_patch", "project_execute_task",
    "workspace_join", "workspace_switch", "workspace_list", "workspace_leave",
    "math_workspace", "python_workspace", "live_workspace_edit", "parallel_work",
}


@dataclass(slots=True)
class ActionExecutionResult:
    action_name: str
    action_input: str = ""
    success: bool = False
    message: str = ""
    data: dict[str, str] = field(default_factory=dict)
    status: ActionExecutionStatus | str = ""
    dispatched: bool = False
    observed: bool = False
    evidence: tuple[str, ...] = ()
    error_kind: str = ""
    observer_reason: str = ""
    publicized: bool = False

    def __post_init__(self) -> None:
        try:
            status = ActionExecutionStatus(self.status) if self.status else None
        except (ValueError, TypeError):
            status = None
        if status is None:
            if not self.success:
                status = ActionExecutionStatus.FAILED
            elif self.action_name in _OBSERVED_ACTIONS:
                status = ActionExecutionStatus.OBSERVED
            else:
                status = ActionExecutionStatus.DISPATCHED
        self.status = status
        self.dispatched = bool(self.dispatched or status in {ActionExecutionStatus.DISPATCHED, ActionExecutionStatus.OBSERVED})
        self.observed = bool(self.observed or status == ActionExecutionStatus.OBSERVED)
        if self.success and not self.evidence:
            if self.observed:
                self.evidence = (f"observer:{self.action_name}",)
            elif self.dispatched:
                self.evidence = (f"dispatch:{self.action_name}",)

    @property
    def confirmed(self) -> bool:
        return bool(self.success and self.observed)

    @property
    def public_mode(self) -> str:
        if not self.success:
            return "failed"
        return "observed_success" if self.observed else "request_sent"

    def to_public_meta(self) -> dict[str, Any]:
        return {
            "action_name": self.action_name,
            "action_input": self.action_input,
            "success": bool(self.success),
            "status": self.status.value if isinstance(self.status, ActionExecutionStatus) else str(self.status),
            "public_mode": self.public_mode,
            "dispatched": bool(self.dispatched),
            "observed": bool(self.observed),
            "evidence": list(self.evidence),
            "error_kind": self.error_kind,
            "observer_reason": self.observer_reason,
            "publicized": bool(self.publicized),
        }


__all__ = [
    "ActionExecutionResult", "ActionExecutionStatus",
    "VALID_SYSTEM_ACTION_LIST", "VALID_SYSTEM_ACTIONS",
]
