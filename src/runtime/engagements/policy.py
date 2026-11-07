# -*- coding: utf-8 -*-
"""Interaction policy and attention arbitration for engagements."""
from __future__ import annotations

from typing import Any, Mapping, Sequence

from .contracts import (
    Engagement,
    FocusMode,
    InitiativeMode,
    InteractionAction,
    InteractionDecision,
    InteractionSignal,
    LifecycleStatus,
)


class EngagementInteractionPolicy:
    def __init__(self, *, signal_importance_threshold: float = 0.45) -> None:
        self.signal_importance_threshold = max(0.0, min(1.0, float(signal_importance_threshold)))

    def evaluate(
        self,
        engagement: Engagement,
        signals: Sequence[InteractionSignal],
        *,
        now: float,
        seconds_since_user: float,
        seconds_since_assistant: float,
        telemetry: Mapping[str, Any] | None = None,
    ) -> InteractionDecision:
        telemetry = telemetry if isinstance(telemetry, Mapping) else {}
        significant = max(signals, key=lambda item: item.importance, default=None)
        policy = engagement.continuity
        baseline = engagement.last_assistant_at or engagement.started_at
        since_output = max(0.0, now - baseline)
        # V66: cadence của engagement dựa vào nhịp output của Eris, không đòi
        # user phải im đủ N giây mới được chủ động.
        cadence_open = True
        event_due = bool(
            significant is not None
            and significant.importance >= self.signal_importance_threshold
            and since_output >= policy.minimum_event_gap_seconds
            and cadence_open
        )

        # Terminal state still deserves one final delivery. Excluding completed
        # monitors from arbitration made successful deployments disappear at the
        # exact moment the user cared about them. A classic automation flourish.
        if engagement.lifecycle in {LifecycleStatus.COMPLETED, LifecycleStatus.CANCELLED}:
            if event_due:
                action = (
                    InteractionAction.NOTIFY
                    if engagement.contract.initiative in {
                        InitiativeMode.MONITORING,
                        InitiativeMode.SILENT_UNTIL_NEEDED,
                    }
                    else InteractionAction.SPEAK
                )
                return InteractionDecision(
                    action=action,
                    engagement_id=engagement.engagement_id,
                    engagement_kind=engagement.kind,
                    reason=f"engagement_event:{significant.reason}" if significant else "engagement_terminal",
                    priority=engagement.priority + 30,
                    due=True,
                    required=True,
                    anchor_kind=f"engagement:{engagement.kind}",
                    seconds_since_engagement_output=since_output,
                    evidence_refs=significant.evidence_refs if significant is not None else (),
                )
            return InteractionDecision(
                engagement_id=engagement.engagement_id,
                engagement_kind=engagement.kind,
                reason="terminal_event_not_due",
                priority=engagement.priority,
                anchor_kind=f"engagement:{engagement.kind}",
                seconds_since_engagement_output=since_output,
            )

        if not engagement.active or engagement.focus == FocusMode.SUSPENDED:
            return InteractionDecision(reason="engagement_inactive")
        if bool(telemetry.get("interaction_busy", False)):
            return InteractionDecision(
                engagement_id=engagement.engagement_id,
                engagement_kind=engagement.kind,
                reason="interaction_busy",
                priority=engagement.priority,
                anchor_kind=f"engagement:{engagement.kind}",
                seconds_since_engagement_output=since_output,
            )

        initiative = engagement.contract.initiative
        cadence_enabled = initiative in {
            InitiativeMode.PROACTIVE,
            InitiativeMode.GUIDED,
            InitiativeMode.AMBIENT,
        }
        # Adapter có thể xin một nhịp tương tác ngắn hơn trong một pha cụ thể,
        # ví dụ task cờ đang chờ bàn. Khóa này là generic để policy không cần
        # biết domain nào đang chạy, một sự kiềm chế hiếm hoi nhưng đáng quý.
        try:
            state_cadence_seconds = max(
                0.0,
                float(engagement.domain_state.get("interaction_cadence_seconds", 0.0) or 0.0),
            )
        except (TypeError, ValueError):
            state_cadence_seconds = 0.0
        state_cadence_due = bool(
            cadence_enabled
            and state_cadence_seconds > 0.0
            and not bool(engagement.domain_state.get("event_driven_only", False))
            and cadence_open
            and since_output >= state_cadence_seconds
        )
        event_driven_only = bool(engagement.domain_state.get("event_driven_only", False))
        generic_cadence_enabled = cadence_enabled and not event_driven_only
        cadence_due = bool(generic_cadence_enabled and cadence_open and since_output >= policy.desired_interval_seconds)
        maximum_due = bool(generic_cadence_enabled and cadence_open and since_output >= policy.maximum_quiet_seconds)

        if initiative in {InitiativeMode.MONITORING, InitiativeMode.SILENT_UNTIL_NEEDED} and not event_due:
            return InteractionDecision(
                engagement_id=engagement.engagement_id,
                engagement_kind=engagement.kind,
                reason="waiting_for_meaningful_event",
                priority=engagement.priority,
                anchor_kind=f"engagement:{engagement.kind}",
                seconds_since_engagement_output=since_output,
            )
        if event_due or state_cadence_due or cadence_due or maximum_due:
            preferred_action = str(
                engagement.domain_state.get("preferred_interaction_action", "") or ""
            ).strip().lower()
            if initiative == InitiativeMode.MONITORING:
                action = InteractionAction.NOTIFY
            elif (
                preferred_action == InteractionAction.ASK.value
                and state_cadence_due
                and not cadence_due
                and not maximum_due
            ):
                action = InteractionAction.ASK
            elif initiative == InitiativeMode.GUIDED and engagement.lifecycle == LifecycleStatus.WAITING:
                action = InteractionAction.ASK
            else:
                action = InteractionAction.SPEAK
            reason = (
                f"engagement_event:{significant.reason}" if event_due and significant is not None
                else "engagement_maximum_quiet" if maximum_due
                else "engagement_state_cadence" if state_cadence_due
                else "engagement_cadence_due"
            )
            return InteractionDecision(
                action=action,
                engagement_id=engagement.engagement_id,
                engagement_kind=engagement.kind,
                reason=reason,
                priority=engagement.priority + (20 if event_due else 10 if maximum_due else 0),
                due=True,
                required=maximum_due,
                anchor_kind=f"engagement:{engagement.kind}",
                seconds_since_engagement_output=since_output,
                evidence_refs=significant.evidence_refs if significant is not None else (),
            )
        return InteractionDecision(
            engagement_id=engagement.engagement_id,
            engagement_kind=engagement.kind,
            reason="engagement_not_due",
            priority=engagement.priority,
            anchor_kind=f"engagement:{engagement.kind}",
            seconds_since_engagement_output=since_output,
        )


class AttentionManager:
    """Choose one engagement voice candidate. Notifications are not a choir."""

    def arbitrate(self, decisions: Sequence[InteractionDecision]) -> InteractionDecision:
        candidates = [decision for decision in decisions if decision.due]
        if not candidates:
            informative = [decision for decision in decisions if decision.engagement_id]
            return max(informative, key=lambda item: item.priority, default=InteractionDecision())
        return max(
            candidates,
            key=lambda item: (
                int(item.required),
                item.priority,
                int(item.action in {InteractionAction.NOTIFY, InteractionAction.ACT}),
            ),
        )


__all__ = ["AttentionManager", "EngagementInteractionPolicy"]
