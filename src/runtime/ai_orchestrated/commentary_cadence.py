# -*- coding: utf-8 -*-
"""Nhịp bình luận chủ động cho ERIS.

Module này không viết câu, không cấp quyền và không đoán user muốn nghe hay không.
Nó chỉ xuất telemetry về *nhịp tự biểu đạt của Eris* cho planner/observability.

Từ V66, cadence không còn đợi "user im đủ lâu". Expression phải có motive thật;
nhịp chỉ giúp Eris không tự làm loãng lời của mình.
"""
from __future__ import annotations

import os
import time
from dataclasses import asdict, dataclass
from typing import Any, Mapping


def _setting(name: str, default: Any) -> Any:
    try:
        from runtime.runtime_settings_store import runtime_setting
        return runtime_setting("commentary", name, default=default)
    except Exception:
        return os.getenv("ERIS_COMMENTARY_" + name.upper(), default)


def _as_bool(value: Any, default: bool = True) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on", "bật", "bat"}


def _as_float(value: Any, default: float) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    return result if result == result else default


@dataclass(frozen=True, slots=True)
class CommentaryExpectation:
    enabled: bool
    due: bool
    required: bool
    reason: str
    desired_interval_seconds: float
    maximum_quiet_seconds: float
    seconds_since_commentary: float
    seconds_since_user: float
    seconds_since_assistant: float
    anchor_kind: str
    engagement_id: str = ""
    engagement_kind: str = ""
    policy_action: str = "wait"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class CommentaryCadence:
    """Tạo expectation theo nhịp, không sở hữu nội dung semantic."""

    def __init__(self) -> None:
        self._started_at = time.time()

    def evaluate(
        self,
        *,
        now: float,
        last_commentary_at: float,
        session_started_at: float,
        seconds_since_user: float,
        seconds_since_assistant: float,
        discourse_open: bool,
        perception_delta: bool,
        activity_event: bool,
        has_public_seed: bool,
        telemetry: Mapping[str, Any] | None = None,
        engagement_decision: Any = None,
    ) -> CommentaryExpectation:
        enabled = _as_bool(_setting("enabled", True), True)
        desired = max(25.0, _as_float(_setting("desired_interval_seconds", 60.0), 60.0))
        maximum = max(desired + 20.0, _as_float(_setting("maximum_quiet_seconds", 120.0), 120.0))
        startup_delay = max(5.0, _as_float(_setting("startup_delay_seconds", 8.0), 8.0))
        baseline = float(last_commentary_at or session_started_at or self._started_at or now)
        since_commentary = max(0.0, now - baseline)
        telemetry = telemetry or {}
        interaction_busy = bool(telemetry.get("interaction_busy", False))

        decision_data: Mapping[str, Any]
        if isinstance(engagement_decision, Mapping):
            decision_data = engagement_decision
        elif callable(getattr(engagement_decision, "to_dict", None)):
            try:
                decision_data = engagement_decision.to_dict()
            except Exception:
                decision_data = {}
        else:
            decision_data = {}
        engagement_id = str(decision_data.get("engagement_id", "") or "").strip()
        engagement_kind = str(decision_data.get("engagement_kind", "") or "").strip()
        engagement_action = str(decision_data.get("action", "wait") or "wait").strip()
        engagement_due = bool(decision_data.get("due", False))
        engagement_required = bool(decision_data.get("required", False))
        engagement_reason = str(decision_data.get("reason", "") or "").strip()
        engagement_anchor = str(decision_data.get("anchor_kind", "") or "").strip()

        anchor_kind = "persona_presence"
        if engagement_id and engagement_anchor:
            anchor_kind = engagement_anchor
        elif discourse_open:
            anchor_kind = "conversation_continuation"
        elif activity_event:
            anchor_kind = "activity_event"
        elif perception_delta:
            anchor_kind = "screen_or_window_delta"
        elif has_public_seed:
            anchor_kind = "public_self_seed"
        elif str(telemetry.get("active_window", "") or telemetry.get("window_title", "") or "").strip():
            anchor_kind = "active_window"

        if not enabled:
            return CommentaryExpectation(False, False, False, "commentary_disabled", desired, maximum,
                                         since_commentary, seconds_since_user, seconds_since_assistant, anchor_kind)
        if interaction_busy:
            return CommentaryExpectation(True, False, False, "interaction_busy", desired, maximum,
                                         since_commentary, seconds_since_user, seconds_since_assistant, anchor_kind)
        if since_commentary < startup_delay:
            return CommentaryExpectation(True, False, False, "startup_grace", desired, maximum,
                                         since_commentary, seconds_since_user, seconds_since_assistant, anchor_kind)

        if engagement_id:
            # An active engagement owns its own rhythm. The generic companion
            # timer must not drag ERIS into unrelated ambient chatter while a
            # monitoring or event-driven contract explicitly says to wait.
            event_due = False
            due = engagement_due
            required = engagement_required
            reason = engagement_reason or ("engagement_due" if due else "engagement_not_due")
        else:
            event_due = bool((perception_delta or activity_event or discourse_open) and since_commentary >= min(25.0, desired))
            due = bool(event_due or since_commentary >= desired)
            required = bool(since_commentary >= maximum)
            reason = (
                "maximum_quiet_reached" if required
                else "meaningful_event_due" if event_due
                else "companion_cadence_due" if due
                else "cadence_not_due"
            )
        return CommentaryExpectation(
            True, due, required, reason, desired, maximum,
            since_commentary, seconds_since_user, seconds_since_assistant, anchor_kind,
            engagement_id, engagement_kind, engagement_action,
        )


__all__ = ["CommentaryCadence", "CommentaryExpectation"]
