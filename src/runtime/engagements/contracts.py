# -*- coding: utf-8 -*-
"""Typed contracts for long-running ERIS engagements.

An engagement is a durable interaction commitment, not merely a chat topic.
The kernel deliberately knows nothing about chess rules, compilers, workouts,
or any other domain humans may invent to occupy their finite lifespan.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Mapping

from .evidence import EvidenceBinding


JsonValue = Any


def _clean(value: Any, limit: int = 240) -> str:
    return " ".join(str(value or "").split()).strip()[:limit]


def _tuple_text(value: Any, *, limit: int = 12, item_limit: int = 160) -> tuple[str, ...]:
    return tuple(
        text for text in (_clean(item, item_limit) for item in tuple(value or ())) if text
    )[-limit:]


class LifecycleStatus(str, Enum):
    ACTIVE = "active"
    PAUSED = "paused"
    WAITING = "waiting"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class InactivityBehavior(str, Enum):
    KEEP = "keep"
    PAUSE = "pause"
    COMPLETE = "complete"


class FocusMode(str, Enum):
    FOREGROUND = "foreground"
    BACKGROUND = "background"
    SUSPENDED = "suspended"


class InitiativeMode(str, Enum):
    PROACTIVE = "proactive"
    EVENT_DRIVEN = "event_driven"
    GUIDED = "guided"
    MONITORING = "monitoring"
    AMBIENT = "ambient"
    SILENT_UNTIL_NEEDED = "silent_until_needed"


class InteractionAction(str, Enum):
    SPEAK = "speak"
    ASK = "ask"
    NOTIFY = "notify"
    ACT = "act"
    WAIT = "wait"


@dataclass(frozen=True, slots=True)
class InteractionContract:
    role: str = "collaborative_companion"
    initiative: InitiativeMode = InitiativeMode.EVENT_DRIVEN
    feedback_style: str = "direct_but_kind"
    allowed_actions: tuple[str, ...] = ()
    forbidden_actions: tuple[str, ...] = ()
    preferences: Mapping[str, JsonValue] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "role": self.role,
            "initiative": self.initiative.value,
            "feedback_style": self.feedback_style,
            "allowed_actions": list(self.allowed_actions),
            "forbidden_actions": list(self.forbidden_actions),
            "preferences": dict(self.preferences),
        }

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any] | None) -> "InteractionContract":
        data = data if isinstance(data, Mapping) else {}
        try:
            initiative = InitiativeMode(str(data.get("initiative", InitiativeMode.EVENT_DRIVEN.value)))
        except ValueError:
            initiative = InitiativeMode.EVENT_DRIVEN
        return cls(
            role=_clean(data.get("role"), 80) or "collaborative_companion",
            initiative=initiative,
            feedback_style=_clean(data.get("feedback_style"), 80) or "direct_but_kind",
            allowed_actions=_tuple_text(data.get("allowed_actions"), limit=24, item_limit=80),
            forbidden_actions=_tuple_text(data.get("forbidden_actions"), limit=24, item_limit=80),
            preferences=dict(data.get("preferences", {})) if isinstance(data.get("preferences"), Mapping) else {},
        )


@dataclass(frozen=True, slots=True)
class ContinuityPolicy:
    desired_interval_seconds: float = 75.0
    maximum_quiet_seconds: float = 150.0
    minimum_event_gap_seconds: float = 12.0
    inactivity_timeout_seconds: float = 3600.0
    survive_surface_change: bool = True
    auto_resume: bool = True
    resume_window_seconds: float = 86400.0
    reopen_completed: bool = False
    inactivity_behavior: InactivityBehavior = InactivityBehavior.PAUSE

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["inactivity_behavior"] = self.inactivity_behavior.value
        return data

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any] | None) -> "ContinuityPolicy":
        data = data if isinstance(data, Mapping) else {}

        def number(name: str, default: float, floor: float) -> float:
            try:
                value = float(data.get(name, default))
            except (TypeError, ValueError):
                value = default
            return max(floor, value)

        desired = number("desired_interval_seconds", 75.0, 15.0)
        maximum = max(desired + 10.0, number("maximum_quiet_seconds", 150.0, 30.0))
        try:
            inactivity_behavior = InactivityBehavior(
                str(data.get("inactivity_behavior", InactivityBehavior.PAUSE.value))
            )
        except ValueError:
            inactivity_behavior = InactivityBehavior.PAUSE
        return cls(
            desired_interval_seconds=desired,
            maximum_quiet_seconds=maximum,
            minimum_event_gap_seconds=number("minimum_event_gap_seconds", 12.0, 0.0),
            inactivity_timeout_seconds=number("inactivity_timeout_seconds", 3600.0, 120.0),
            survive_surface_change=bool(data.get("survive_surface_change", True)),
            auto_resume=bool(data.get("auto_resume", True)),
            resume_window_seconds=number("resume_window_seconds", 86400.0, 0.0),
            reopen_completed=bool(data.get("reopen_completed", False)),
            inactivity_behavior=inactivity_behavior,
        )


@dataclass(frozen=True, slots=True)
class LifecycleSignal:
    target: LifecycleStatus
    reason: str
    detail: str = ""
    confidence: float = 1.0


@dataclass(frozen=True, slots=True)
class EngagementEvent:
    engagement_id: str
    seq: int
    timestamp: float
    kind: str
    log_seq: int = 0
    source: str = "runtime"
    payload: Mapping[str, JsonValue] = field(default_factory=dict)
    evidence_refs: tuple[str, ...] = ()
    prev_hash: str = ""
    event_hash: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "engagement_id": self.engagement_id,
            "seq": self.seq,
            "log_seq": self.log_seq,
            "timestamp": self.timestamp,
            "kind": self.kind,
            "source": self.source,
            "payload": dict(self.payload),
            "evidence_refs": list(self.evidence_refs),
            "prev_hash": self.prev_hash,
            "event_hash": self.event_hash,
        }

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> "EngagementEvent":
        return cls(
            engagement_id=_clean(data.get("engagement_id"), 96),
            seq=max(0, int(data.get("seq", 0) or 0)),
            log_seq=max(0, int(data.get("log_seq", 0) or 0)),
            timestamp=max(0.0, float(data.get("timestamp", 0.0) or 0.0)),
            kind=_clean(data.get("kind"), 96) or "unknown",
            source=_clean(data.get("source"), 64) or "runtime",
            payload=dict(data.get("payload", {})) if isinstance(data.get("payload"), Mapping) else {},
            evidence_refs=_tuple_text(data.get("evidence_refs"), limit=16, item_limit=120),
            prev_hash=_clean(data.get("prev_hash"), 96),
            event_hash=_clean(data.get("event_hash"), 96),
        )


@dataclass(frozen=True, slots=True)
class EngagementEventBatch:
    """A bounded event-stream read with explicit retention-gap metadata."""

    consumer_id: str
    requested_cursor: int
    next_cursor: int
    earliest_available_cursor: int
    latest_cursor: int
    events: tuple[EngagementEvent, ...] = ()
    lost_event_count: int = 0
    leased_until: float = 0.0
    delivery_attempt: int = 0
    retry_after_seconds: float = 0.0
    dead_letter_id: str = ""
    blocked_reason: str = ""
    delivery_id: str = ""
    event_digest: str = ""

    @property
    def has_gap(self) -> bool:
        return self.lost_event_count > 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "consumer_id": self.consumer_id,
            "requested_cursor": self.requested_cursor,
            "next_cursor": self.next_cursor,
            "earliest_available_cursor": self.earliest_available_cursor,
            "latest_cursor": self.latest_cursor,
            "lost_event_count": self.lost_event_count,
            "has_gap": self.has_gap,
            "leased_until": self.leased_until,
            "delivery_attempt": self.delivery_attempt,
            "retry_after_seconds": self.retry_after_seconds,
            "dead_letter_id": self.dead_letter_id,
            "blocked_reason": self.blocked_reason,
            "delivery_id": self.delivery_id,
            "event_digest": self.event_digest,
            "events": [event.to_dict() for event in self.events],
        }


@dataclass(frozen=True, slots=True)
class EngagementConsumerState:
    consumer_id: str
    acknowledged_cursor: int = 0
    lease_start_cursor: int = 0
    lease_end_cursor: int = 0
    lease_expires_at: float = 0.0
    delivery_attempt: int = 0
    next_retry_at: float = 0.0
    last_success_at: float = 0.0
    last_failure_at: float = 0.0
    last_error: str = ""
    dead_letter_count: int = 0
    blocked_reason: str = ""
    blocked_at: float = 0.0

    @property
    def lease_active(self) -> bool:
        return self.lease_end_cursor > self.acknowledged_cursor and self.lease_expires_at > 0.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_mapping(cls, consumer_id: str, data: Mapping[str, Any] | None) -> "EngagementConsumerState":
        data = data if isinstance(data, Mapping) else {}
        def integer(name: str) -> int:
            try:
                return max(0, int(data.get(name, 0) or 0))
            except (TypeError, ValueError):
                return 0
        def number(name: str) -> float:
            try:
                return max(0.0, float(data.get(name, 0.0) or 0.0))
            except (TypeError, ValueError):
                return 0.0
        return cls(
            consumer_id=_clean(consumer_id, 96),
            acknowledged_cursor=integer("acknowledged_cursor"),
            lease_start_cursor=integer("lease_start_cursor"),
            lease_end_cursor=integer("lease_end_cursor"),
            lease_expires_at=number("lease_expires_at"),
            delivery_attempt=integer("delivery_attempt"),
            next_retry_at=number("next_retry_at"),
            last_success_at=number("last_success_at"),
            last_failure_at=number("last_failure_at"),
            last_error=_clean(data.get("last_error"), 240),
            dead_letter_count=integer("dead_letter_count"),
            blocked_reason=_clean(data.get("blocked_reason"), 240),
            blocked_at=number("blocked_at"),
        )


@dataclass(frozen=True, slots=True)
class EngagementDeliveryReceipt:
    """Durable proof that a specific leased batch was committed."""

    delivery_id: str
    consumer_id: str
    from_cursor: int
    to_cursor: int
    event_digest: str
    committed_at: float
    outcome: str = "processed"
    handler_token: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> "EngagementDeliveryReceipt":
        return cls(
            delivery_id=_clean(data.get("delivery_id"), 96),
            consumer_id=_clean(data.get("consumer_id"), 96),
            from_cursor=max(0, int(data.get("from_cursor", 0) or 0)),
            to_cursor=max(0, int(data.get("to_cursor", 0) or 0)),
            event_digest=_clean(data.get("event_digest"), 96),
            committed_at=max(0.0, float(data.get("committed_at", 0.0) or 0.0)),
            outcome=_clean(data.get("outcome"), 48) or "processed",
            handler_token=_clean(data.get("handler_token"), 160),
        )


@dataclass(frozen=True, slots=True)
class EngagementDeadLetter:
    letter_id: str
    consumer_id: str
    from_cursor: int
    to_cursor: int
    created_at: float
    attempts: int
    reason: str
    event_hashes: tuple[str, ...] = ()
    events: tuple[EngagementEvent, ...] = ()
    status: str = "open"
    replay_count: int = 0
    last_replayed_at: float = 0.0
    resolved_at: float = 0.0
    resolution: str = ""

    @property
    def open(self) -> bool:
        return self.status == "open"

    def to_dict(self) -> dict[str, Any]:
        return {
            "letter_id": self.letter_id,
            "consumer_id": self.consumer_id,
            "from_cursor": self.from_cursor,
            "to_cursor": self.to_cursor,
            "created_at": self.created_at,
            "attempts": self.attempts,
            "reason": self.reason,
            "event_hashes": list(self.event_hashes),
            "events": [event.to_dict() for event in self.events],
            "status": self.status,
            "replay_count": self.replay_count,
            "last_replayed_at": self.last_replayed_at,
            "resolved_at": self.resolved_at,
            "resolution": self.resolution,
        }

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> "EngagementDeadLetter":
        consumer_id = _clean(data.get("consumer_id"), 96)
        from_cursor = max(0, int(data.get("from_cursor", 0) or 0))
        to_cursor = max(0, int(data.get("to_cursor", 0) or 0))
        created_at = max(0.0, float(data.get("created_at", 0.0) or 0.0))
        letter_id = _clean(data.get("letter_id"), 96)
        if not letter_id:
            seed = f"{consumer_id}|{from_cursor}|{to_cursor}|{created_at:.6f}"
            import hashlib
            letter_id = "dlq_" + hashlib.sha1(seed.encode("utf-8", "ignore")).hexdigest()[:16]
        raw_events = data.get("events", ())
        events = tuple(
            EngagementEvent.from_mapping(item)
            for item in tuple(raw_events or ())
            if isinstance(item, Mapping)
        )
        status = _clean(data.get("status"), 24).lower() or "open"
        if status not in {"open", "resolved"}:
            status = "open"
        return cls(
            letter_id=letter_id,
            consumer_id=consumer_id,
            from_cursor=from_cursor,
            to_cursor=to_cursor,
            created_at=created_at,
            attempts=max(0, int(data.get("attempts", 0) or 0)),
            reason=_clean(data.get("reason"), 240),
            event_hashes=_tuple_text(data.get("event_hashes"), limit=64, item_limit=96),
            events=events,
            status=status,
            replay_count=max(0, int(data.get("replay_count", 0) or 0)),
            last_replayed_at=max(0.0, float(data.get("last_replayed_at", 0.0) or 0.0)),
            resolved_at=max(0.0, float(data.get("resolved_at", 0.0) or 0.0)),
            resolution=_clean(data.get("resolution"), 240),
        )


@dataclass(frozen=True, slots=True)
class EngagementReceiptAudit:
    valid: bool
    checked_receipts: int
    verifiable_receipts: int
    errors: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class EngagementStreamAudit:
    valid: bool
    checked_events: int
    earliest_cursor: int
    latest_cursor: int
    chain_anchor: str
    chain_head: str
    errors: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class EngagementReplayReport:
    engagement_id: str
    complete: bool
    replayed_events: int
    first_seq: int
    last_seq: int
    state_matches: bool
    reason: str = ""
    current_state: Mapping[str, JsonValue] = field(default_factory=dict)
    replayed_state: Mapping[str, JsonValue] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "engagement_id": self.engagement_id,
            "complete": self.complete,
            "replayed_events": self.replayed_events,
            "first_seq": self.first_seq,
            "last_seq": self.last_seq,
            "state_matches": self.state_matches,
            "reason": self.reason,
            "current_state": dict(self.current_state),
            "replayed_state": dict(self.replayed_state),
        }


@dataclass(frozen=True, slots=True)
class Engagement:
    engagement_id: str
    semantic_key: str
    kind: str
    objective: str
    adapter_id: str
    lifecycle: LifecycleStatus
    focus: FocusMode
    priority: int
    contract: InteractionContract
    continuity: ContinuityPolicy
    domain_state: Mapping[str, JsonValue]
    started_at: float
    updated_at: float
    last_progress_at: float
    last_user_at: float = 0.0
    last_assistant_at: float = 0.0
    event_seq: int = 0
    last_event_kind: str = ""
    evidence_binding: EvidenceBinding = field(default_factory=EvidenceBinding)
    source_surface_key: str = ""
    lifecycle_changed_at: float = 0.0
    last_event_log_seq: int = 0
    resume_focus: FocusMode = FocusMode.BACKGROUND

    @property
    def active(self) -> bool:
        return self.lifecycle in {LifecycleStatus.ACTIVE, LifecycleStatus.WAITING}

    @property
    def terminal(self) -> bool:
        return self.lifecycle in {LifecycleStatus.COMPLETED, LifecycleStatus.CANCELLED}

    def to_dict(self) -> dict[str, Any]:
        return {
            "engagement_id": self.engagement_id,
            "semantic_key": self.semantic_key,
            "kind": self.kind,
            "objective": self.objective,
            "adapter_id": self.adapter_id,
            "lifecycle": self.lifecycle.value,
            "focus": self.focus.value,
            "priority": self.priority,
            "contract": self.contract.to_dict(),
            "continuity": self.continuity.to_dict(),
            "domain_state": dict(self.domain_state),
            "started_at": self.started_at,
            "updated_at": self.updated_at,
            "last_progress_at": self.last_progress_at,
            "last_user_at": self.last_user_at,
            "last_assistant_at": self.last_assistant_at,
            "event_seq": self.event_seq,
            "last_event_kind": self.last_event_kind,
            "evidence_binding": self.evidence_binding.to_dict(),
            "source_surface_key": self.source_surface_key,
            "lifecycle_changed_at": self.lifecycle_changed_at,
            "last_event_log_seq": self.last_event_log_seq,
            "resume_focus": self.resume_focus.value,
        }

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> "Engagement":
        try:
            lifecycle = LifecycleStatus(str(data.get("lifecycle", LifecycleStatus.ACTIVE.value)))
        except ValueError:
            lifecycle = LifecycleStatus.ACTIVE
        try:
            focus = FocusMode(str(data.get("focus", FocusMode.BACKGROUND.value)))
        except ValueError:
            focus = FocusMode.BACKGROUND
        try:
            resume_focus = FocusMode(str(data.get("resume_focus", focus.value)))
        except ValueError:
            resume_focus = FocusMode.BACKGROUND
        if resume_focus == FocusMode.SUSPENDED:
            resume_focus = FocusMode.BACKGROUND
        updated_at = max(0.0, float(data.get("updated_at", 0.0) or 0.0))
        return cls(
            engagement_id=_clean(data.get("engagement_id"), 96),
            semantic_key=_clean(data.get("semantic_key"), 96),
            kind=_clean(data.get("kind"), 64) or "collaboration",
            objective=_clean(data.get("objective"), 360),
            adapter_id=_clean(data.get("adapter_id"), 80) or "generic",
            lifecycle=lifecycle,
            focus=focus,
            priority=max(0, min(100, int(data.get("priority", 50) or 50))),
            contract=InteractionContract.from_mapping(data.get("contract")),
            continuity=ContinuityPolicy.from_mapping(data.get("continuity")),
            domain_state=dict(data.get("domain_state", {})) if isinstance(data.get("domain_state"), Mapping) else {},
            started_at=max(0.0, float(data.get("started_at", 0.0) or 0.0)),
            updated_at=updated_at,
            last_progress_at=max(0.0, float(data.get("last_progress_at", 0.0) or 0.0)),
            last_user_at=max(0.0, float(data.get("last_user_at", 0.0) or 0.0)),
            last_assistant_at=max(0.0, float(data.get("last_assistant_at", 0.0) or 0.0)),
            event_seq=max(0, int(data.get("event_seq", 0) or 0)),
            last_event_kind=_clean(data.get("last_event_kind"), 96),
            evidence_binding=EvidenceBinding.from_mapping(
                data.get("evidence_binding")
                or ({"surface_key": data.get("source_surface_key", "")} if data.get("source_surface_key") else {})
            ),
            source_surface_key=_clean(data.get("source_surface_key"), 96),
            lifecycle_changed_at=max(0.0, float(data.get("lifecycle_changed_at", updated_at) or updated_at)),
            last_event_log_seq=max(0, int(data.get("last_event_log_seq", 0) or 0)),
            resume_focus=resume_focus,
        )


@dataclass(frozen=True, slots=True)
class EngagementProposal:
    semantic_key: str
    kind: str
    objective: str
    adapter_id: str
    confidence: float
    expected_duration: str
    contract: InteractionContract
    continuity: ContinuityPolicy
    priority: int = 60
    focus: FocusMode = FocusMode.FOREGROUND
    source: str = "user"
    evidence_binding: EvidenceBinding = field(default_factory=EvidenceBinding)


@dataclass(frozen=True, slots=True)
class PlannerProjection:
    engagement_id: str
    kind: str
    objective: str
    lifecycle: str
    focus: str
    priority: int
    role: str
    initiative: str
    feedback_style: str
    current_state_summary: str
    recent_progress: tuple[str, ...]
    next_expected_events: tuple[str, ...]
    interaction_constraints: tuple[str, ...]
    age_seconds: float
    seconds_since_progress: float
    # State typed đi xuyên planner -> blackboard -> Surface. Chuỗi summary chỉ
    # còn phục vụ log/UI cũ, không còn là nguồn để regex ngược ra nước cờ.
    current_state: Mapping[str, JsonValue] = field(default_factory=dict)
    evidence_binding: Mapping[str, JsonValue] = field(default_factory=dict)
    last_event_kind: str = ""
    lifecycle_changed_at: float = 0.0
    last_event_log_seq: int = 0
    resume_focus: FocusMode = FocusMode.BACKGROUND

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class InteractionSignal:
    engagement_id: str
    kind: str
    importance: float
    reason: str
    event_key: str = ""
    evidence_refs: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class InteractionDecision:
    action: InteractionAction = InteractionAction.WAIT
    engagement_id: str = ""
    engagement_kind: str = ""
    reason: str = "no_active_engagement"
    priority: int = 0
    due: bool = False
    required: bool = False
    anchor_kind: str = ""
    seconds_since_engagement_output: float = 0.0
    evidence_refs: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "action": self.action.value,
            "engagement_id": self.engagement_id,
            "engagement_kind": self.engagement_kind,
            "reason": self.reason,
            "priority": self.priority,
            "due": self.due,
            "required": self.required,
            "anchor_kind": self.anchor_kind,
            "seconds_since_engagement_output": round(self.seconds_since_engagement_output, 2),
            "evidence_refs": list(self.evidence_refs),
        }


__all__ = [
    "ContinuityPolicy",
    "Engagement",
    "EngagementEvent",
    "EngagementConsumerState",
    "EngagementDeadLetter",
    "EngagementEventBatch",
    "EngagementProposal",
    "EngagementReplayReport",
    "EngagementStreamAudit",
    "EvidenceBinding",
    "FocusMode",
    "InactivityBehavior",
    "InitiativeMode",
    "InteractionAction",
    "InteractionContract",
    "InteractionDecision",
    "InteractionSignal",
    "LifecycleSignal",
    "LifecycleStatus",
    "PlannerProjection",
]
