# -*- coding: utf-8 -*-
"""Thread-safe event-sourced registry for ERIS engagements."""
from __future__ import annotations

import hashlib
import json
import threading
import time
from dataclasses import replace
from typing import Any, Callable, Iterable, Mapping, Sequence

from runtime.subsystem_health import FailureKind, SubsystemHealthRegistry

from .adapters import AdapterRegistry
from .contracts import (
    Engagement,
    EngagementConsumerState,
    EngagementDeadLetter,
    EngagementDeliveryReceipt,
    EngagementEvent,
    EngagementEventBatch,
    EngagementProposal,
    EngagementReplayReport,
    EngagementReceiptAudit,
    EngagementStreamAudit,
    FocusMode,
    InactivityBehavior,
    LifecycleSignal,
    LifecycleStatus,
)
from .evidence import EvidenceBinding


PersistFn = Callable[[Mapping[str, Any]], None]


def _new_id(proposal: EngagementProposal, now: float) -> str:
    raw = f"{proposal.semantic_key}|{now:.6f}"
    digest = hashlib.sha1(raw.encode("utf-8", "ignore")).hexdigest()[:12]
    return f"eng_{digest}"


def _event_kind_for(lifecycle: LifecycleStatus) -> str:
    return {
        LifecycleStatus.ACTIVE: "engagement.resumed",
        LifecycleStatus.PAUSED: "engagement.paused",
        LifecycleStatus.WAITING: "engagement.waiting",
        LifecycleStatus.COMPLETED: "engagement.completed",
        LifecycleStatus.CANCELLED: "engagement.cancelled",
    }[lifecycle]


def _canonical_event_payload(
    *,
    engagement_id: str,
    seq: int,
    log_seq: int,
    timestamp: float,
    kind: str,
    source: str,
    payload: Mapping[str, Any],
    evidence_refs: Sequence[str],
    prev_hash: str,
) -> bytes:
    data = {
        "engagement_id": engagement_id,
        "seq": int(seq),
        "log_seq": int(log_seq),
        "timestamp": round(float(timestamp), 6),
        "kind": kind,
        "source": source,
        "payload": dict(payload),
        "evidence_refs": list(evidence_refs),
        "prev_hash": prev_hash,
    }
    return json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")


def _event_hash(event: EngagementEvent, *, prev_hash: str | None = None) -> str:
    previous = event.prev_hash if prev_hash is None else str(prev_hash or "")
    return hashlib.sha256(
        _canonical_event_payload(
            engagement_id=event.engagement_id,
            seq=event.seq,
            log_seq=event.log_seq,
            timestamp=event.timestamp,
            kind=event.kind,
            source=event.source,
            payload=event.payload,
            evidence_refs=event.evidence_refs,
            prev_hash=previous,
        )
    ).hexdigest()


def snapshot_digest(payload: Mapping[str, Any]) -> str:
    """Return the canonical digest used by signed engagement snapshots."""
    return _snapshot_digest(payload)


def validate_snapshot_payload(payload: Mapping[str, Any]) -> tuple[bool, str]:
    """Validate a registry snapshot without constructing a registry instance.

    Legacy snapshots before schema 6 are accepted as unsigned input so the
    normal registry migration path can sign them on the next persistence.
    """
    if not isinstance(payload, Mapping):
        return False, "snapshot_not_mapping"
    try:
        schema_version = max(0, int(payload.get("schema_version", 0) or 0))
    except (TypeError, ValueError):
        return False, "snapshot_schema_invalid"
    if schema_version < 6:
        return True, "legacy_unsigned"
    expected = str(payload.get("snapshot_digest", "") or "")
    if not expected:
        return False, "snapshot_digest_missing"
    if expected != _snapshot_digest(payload):
        return False, "snapshot_digest_mismatch"
    return True, "valid"


def _snapshot_digest(payload: Mapping[str, Any]) -> str:
    canonical = dict(payload)
    canonical.pop("snapshot_digest", None)
    encoded = json.dumps(
        canonical, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _dead_letter_id(
    consumer_id: str, from_cursor: int, to_cursor: int, created_at: float, event_hashes: Sequence[str]
) -> str:
    seed = "|".join((
        consumer_id, str(from_cursor), str(to_cursor), f"{created_at:.6f}", *tuple(event_hashes)
    ))
    return "dlq_" + hashlib.sha1(seed.encode("utf-8", "ignore")).hexdigest()[:16]


def _batch_identity(
    consumer_id: str, requested_cursor: int, next_cursor: int, events: Sequence[EngagementEvent]
) -> tuple[str, str]:
    event_seed = "|".join(
        event.event_hash or f"{event.log_seq}:{event.engagement_id}:{event.kind}"
        for event in events
    )
    digest = hashlib.sha256(event_seed.encode("utf-8", "ignore")).hexdigest()
    delivery_seed = f"{consumer_id}|{int(requested_cursor)}|{int(next_cursor)}|{digest}"
    delivery_id = "del_" + hashlib.sha1(
        delivery_seed.encode("utf-8", "ignore")
    ).hexdigest()[:20]
    return delivery_id, digest


class EngagementRegistry:
    def __init__(
        self,
        adapters: AdapterRegistry,
        *,
        initial_state: Mapping[str, Any] | None = None,
        persist: PersistFn | None = None,
        max_events: int = 160,
        consumer_lag_warning_events: int = 128,
        open_dead_letter_warning_count: int = 1,
        max_delivery_receipts: int = 512,
        clock: Callable[[], float] | None = None,
        health_registry: SubsystemHealthRegistry | None = None,
    ) -> None:
        self.adapters = adapters
        self._persist = persist
        self._clock = clock or time.time
        self._health = health_registry
        self._max_events = max(24, int(max_events))
        self._consumer_lag_warning_events = max(1, int(consumer_lag_warning_events))
        self._open_dead_letter_warning_count = max(1, int(open_dead_letter_warning_count))
        self._max_delivery_receipts = max(32, int(max_delivery_receipts))
        self._lock = threading.RLock()
        self._engagements: dict[str, Engagement] = {}
        self._events: list[EngagementEvent] = []
        self._revision = 0
        self._event_log_seq = 0
        self._event_chain_anchor = ""
        self._event_chain_head = ""
        self._consumers: dict[str, EngagementConsumerState] = {}
        self._dead_letters: list[EngagementDeadLetter] = []
        self._delivery_receipts: dict[str, EngagementDeliveryReceipt] = {}
        self._load_issues: list[str] = []
        self._snapshot_integrity_valid = True
        self._snapshot_integrity_error = ""
        self._load(initial_state or {})

    @property
    def revision(self) -> int:
        with self._lock:
            return self._revision

    @property
    def event_cursor(self) -> int:
        with self._lock:
            return self._event_log_seq

    @property
    def earliest_event_cursor(self) -> int:
        with self._lock:
            return self._events[0].log_seq if self._events else self._event_log_seq + 1

    def consumer_cursor(self, consumer_id: str) -> int:
        key = str(consumer_id or "").strip()[:96]
        with self._lock:
            state = self._consumers.get(key)
            return int(state.acknowledged_cursor) if state is not None else 0

    def consumer_state(self, consumer_id: str) -> EngagementConsumerState:
        key = str(consumer_id or "").strip()[:96]
        if not key:
            raise ValueError("consumer_id is required")
        with self._lock:
            return self._consumers.get(key, EngagementConsumerState(consumer_id=key))

    def delivery_receipt(self, delivery_id: str) -> EngagementDeliveryReceipt | None:
        key = str(delivery_id or "").strip()[:96]
        with self._lock:
            return self._delivery_receipts.get(key)

    def delivery_receipts(self, consumer_id: str = "") -> tuple[EngagementDeliveryReceipt, ...]:
        key = str(consumer_id or "").strip()[:96]
        with self._lock:
            items = tuple(self._delivery_receipts.values())
            if key:
                items = tuple(item for item in items if item.consumer_id == key)
            return tuple(sorted(items, key=lambda item: (item.committed_at, item.delivery_id)))

    def dead_letters(
        self, consumer_id: str = "", *, status: str = ""
    ) -> tuple[EngagementDeadLetter, ...]:
        key = str(consumer_id or "").strip()[:96]
        normalized_status = str(status or "").strip().lower()
        with self._lock:
            items = self._dead_letters
            if key:
                items = [item for item in items if item.consumer_id == key]
            if normalized_status:
                items = [item for item in items if item.status == normalized_status]
            return tuple(items)

    def dead_letter(self, letter_id: str) -> EngagementDeadLetter | None:
        key = str(letter_id or "").strip()[:96]
        with self._lock:
            return next((item for item in self._dead_letters if item.letter_id == key), None)

    def dead_letter_batch(self, letter_id: str) -> EngagementEventBatch:
        key = str(letter_id or "").strip()[:96]
        if not key:
            raise ValueError("letter_id is required")
        with self._lock:
            letter = next((item for item in self._dead_letters if item.letter_id == key), None)
            if letter is None:
                raise KeyError(key)
            events = letter.events
            if not events:
                retained = tuple(
                    event for event in self._events
                    if letter.from_cursor < event.log_seq <= letter.to_cursor
                )
                if letter.event_hashes:
                    allowed = set(letter.event_hashes)
                    retained = tuple(event for event in retained if event.event_hash in allowed)
                events = retained
            expected = len(letter.event_hashes)
            lost = max(0, expected - len(events)) if expected else 0
            delivery_id, event_digest = _batch_identity(
                letter.consumer_id, letter.from_cursor, letter.to_cursor, events
            )
            return EngagementEventBatch(
                consumer_id=letter.consumer_id,
                requested_cursor=letter.from_cursor,
                next_cursor=letter.to_cursor,
                earliest_available_cursor=(events[0].log_seq if events else self.earliest_event_cursor),
                latest_cursor=self._event_log_seq,
                events=events,
                lost_event_count=lost,
                delivery_attempt=letter.attempts,
                dead_letter_id=letter.letter_id,
                delivery_id=delivery_id,
                event_digest=event_digest,
            )

    def mark_dead_letter_replayed(
        self, letter_id: str, *, now: float | None = None
    ) -> EngagementDeadLetter:
        key = str(letter_id or "").strip()[:96]
        now = self._clock() if now is None else float(now)
        with self._lock:
            for index, item in enumerate(self._dead_letters):
                if item.letter_id != key:
                    continue
                updated = replace(
                    item, replay_count=item.replay_count + 1, last_replayed_at=now
                )
                self._dead_letters[index] = updated
                self._touch_locked()
                return updated
        raise KeyError(key)

    def resolve_dead_letter(
        self, letter_id: str, *, resolution: str = "replayed", now: float | None = None
    ) -> EngagementDeadLetter:
        key = str(letter_id or "").strip()[:96]
        now = self._clock() if now is None else float(now)
        detail = str(resolution or "resolved").strip()[:240] or "resolved"
        with self._lock:
            for index, item in enumerate(self._dead_letters):
                if item.letter_id != key:
                    continue
                updated = replace(
                    item, status="resolved", resolved_at=now, resolution=detail
                )
                self._dead_letters[index] = updated
                self._touch_locked()
                return updated
        raise KeyError(key)

    def reopen_dead_letter(
        self, letter_id: str, *, now: float | None = None
    ) -> EngagementDeadLetter:
        key = str(letter_id or "").strip()[:96]
        now = self._clock() if now is None else float(now)
        with self._lock:
            for index, item in enumerate(self._dead_letters):
                if item.letter_id != key:
                    continue
                updated = replace(
                    item, status="open", resolved_at=0.0, resolution=""
                )
                self._dead_letters[index] = updated
                self._touch_locked()
                return updated
        raise KeyError(key)

    def create(self, proposal: EngagementProposal, *, now: float | None = None) -> Engagement:
        now = self._clock() if now is None else float(now)
        with self._lock:
            existing = self._resumable_for_proposal_locked(proposal, now=now)
            if existing is not None:
                if proposal.focus == FocusMode.FOREGROUND:
                    self._demote_foreground_locked(except_id=existing.engagement_id)
                updated = replace(
                    existing,
                    objective=proposal.objective or existing.objective,
                    adapter_id=proposal.adapter_id,
                    contract=proposal.contract,
                    continuity=proposal.continuity,
                    focus=proposal.focus,
                    priority=proposal.priority,
                    updated_at=now,
                    evidence_binding=existing.evidence_binding.merged(proposal.evidence_binding),
                    source_surface_key=(proposal.evidence_binding.surface_key or existing.source_surface_key),
                )
                self._engagements[existing.engagement_id] = updated
                kind = "engagement.refreshed" if existing.active else "engagement.resumed"
                return self._append_locked(
                    updated,
                    kind,
                    now,
                    proposal.source,
                    {"detail": proposal.objective, "previous_lifecycle": existing.lifecycle.value},
                )

            if proposal.focus == FocusMode.FOREGROUND:
                self._demote_foreground_locked()
            adapter = self.adapters.get(proposal.adapter_id)
            engagement = Engagement(
                engagement_id=_new_id(proposal, now),
                semantic_key=proposal.semantic_key,
                kind=proposal.kind,
                objective=proposal.objective,
                adapter_id=proposal.adapter_id,
                lifecycle=LifecycleStatus.ACTIVE,
                focus=proposal.focus,
                priority=proposal.priority,
                contract=proposal.contract,
                continuity=proposal.continuity,
                domain_state=adapter.initial_state(proposal.objective, metadata={"source": proposal.source}),
                started_at=now,
                updated_at=now,
                last_progress_at=now,
                evidence_binding=proposal.evidence_binding,
                source_surface_key=proposal.evidence_binding.surface_key,
                lifecycle_changed_at=now,
                resume_focus=proposal.focus,
            )
            self._engagements[engagement.engagement_id] = engagement
            return self._append_locked(
                engagement,
                "engagement.started",
                now,
                proposal.source,
                {"detail": proposal.objective},
            )

    def append_event(
        self,
        engagement_id: str,
        kind: str,
        *,
        now: float | None = None,
        source: str = "runtime",
        payload: Mapping[str, Any] | None = None,
        evidence_refs: Sequence[str] = (),
    ) -> Engagement | None:
        now = self._clock() if now is None else float(now)
        with self._lock:
            engagement = self._engagements.get(str(engagement_id or ""))
            if engagement is None:
                return None
            return self._append_locked(
                engagement,
                kind,
                now,
                source,
                payload or {},
                evidence_refs=evidence_refs,
            )

    def bind(
        self,
        engagement_id: str,
        binding: EvidenceBinding | Mapping[str, Any],
        *,
        now: float | None = None,
        source: str = "runtime",
    ) -> Engagement | None:
        """Merge stable evidence identifiers into an engagement via the event log."""
        now = self._clock() if now is None else float(now)
        incoming = EvidenceBinding.from_mapping(binding)
        if not incoming.meaningful:
            return self.get(engagement_id)
        with self._lock:
            engagement = self._engagements.get(str(engagement_id or ""))
            if engagement is None:
                return None
            merged = engagement.evidence_binding.merged(incoming)
            if merged == engagement.evidence_binding:
                return engagement
            return self._append_locked(
                engagement,
                "engagement.binding_updated",
                now,
                source,
                {"binding": merged.to_dict(), "detail": "evidence binding updated"},
            )

    def transition(
        self,
        engagement_id: str,
        lifecycle: LifecycleStatus,
        *,
        now: float | None = None,
        detail: str = "",
        source: str = "user",
        reason: str = "explicit_control",
    ) -> Engagement | None:
        now = self._clock() if now is None else float(now)
        with self._lock:
            current = self._engagements.get(str(engagement_id or ""))
            if current is None:
                return None
            if current.lifecycle == lifecycle:
                return current
            focus = current.focus
            resume_focus = current.resume_focus
            if lifecycle == LifecycleStatus.ACTIVE:
                focus = current.resume_focus if current.resume_focus != FocusMode.SUSPENDED else FocusMode.BACKGROUND
                if focus == FocusMode.FOREGROUND:
                    self._demote_foreground_locked(except_id=current.engagement_id)
            elif lifecycle in {LifecycleStatus.PAUSED, LifecycleStatus.COMPLETED, LifecycleStatus.CANCELLED}:
                if current.focus != FocusMode.SUSPENDED:
                    resume_focus = current.focus
                focus = FocusMode.SUSPENDED
            updated = replace(current, focus=focus, resume_focus=resume_focus, updated_at=now)
            self._engagements[current.engagement_id] = updated
            return self._append_locked(
                updated,
                _event_kind_for(lifecycle),
                now,
                source,
                {"detail": detail, "reason": reason, "previous_lifecycle": current.lifecycle.value},
            )

    def set_focus(self, engagement_id: str, focus: FocusMode, *, now: float | None = None) -> Engagement | None:
        now = self._clock() if now is None else float(now)
        with self._lock:
            current = self._engagements.get(str(engagement_id or ""))
            if current is None:
                return None
            if current.active and focus == FocusMode.SUSPENDED:
                return current
            if not current.active and focus != FocusMode.SUSPENDED:
                return current
            if focus == FocusMode.FOREGROUND:
                self._demote_foreground_locked(except_id=current.engagement_id)
            updated = replace(current, focus=focus, updated_at=now)
            self._engagements[current.engagement_id] = updated
            self._touch_locked()
            return updated

    def foreground(self) -> Engagement | None:
        with self._lock:
            candidates = [
                item for item in self._engagements.values()
                if item.active and item.focus == FocusMode.FOREGROUND
            ]
            if not candidates:
                return None
            return max(candidates, key=lambda item: (item.priority, item.updated_at))

    def active(self) -> tuple[Engagement, ...]:
        with self._lock:
            items = [item for item in self._engagements.values() if item.active]
            return tuple(sorted(
                items,
                key=lambda item: (item.focus != FocusMode.FOREGROUND, -item.priority, -item.updated_at),
            ))

    def all(self) -> tuple[Engagement, ...]:
        with self._lock:
            return tuple(sorted(self._engagements.values(), key=lambda item: (-item.updated_at, item.engagement_id)))

    def latest(
        self,
        *,
        kind: str = "",
        lifecycles: Iterable[LifecycleStatus] | None = None,
    ) -> Engagement | None:
        allowed = set(lifecycles) if lifecycles is not None else None
        normalized_kind = str(kind or "").strip()
        with self._lock:
            candidates = [
                item for item in self._engagements.values()
                if (not normalized_kind or item.kind == normalized_kind)
                and (allowed is None or item.lifecycle in allowed)
            ]
            return max(candidates, key=lambda item: (item.updated_at, item.priority), default=None)

    def get(self, engagement_id: str) -> Engagement | None:
        with self._lock:
            return self._engagements.get(str(engagement_id or ""))

    def pending_terminal(self) -> tuple[Engagement, ...]:
        """Terminal engagements whose final lifecycle event has not been spoken yet."""
        with self._lock:
            pending: list[Engagement] = []
            for engagement in self._engagements.values():
                if not engagement.terminal:
                    continue
                events = [event for event in self._events if event.engagement_id == engagement.engagement_id]
                terminal_seq = max(
                    (event.seq for event in events if event.kind in {"engagement.completed", "engagement.cancelled"}),
                    default=0,
                )
                acknowledged_seq = max(
                    (event.seq for event in events if event.kind == "assistant.spoke"),
                    default=0,
                )
                if terminal_seq > acknowledged_seq:
                    pending.append(engagement)
            return tuple(sorted(pending, key=lambda item: (-item.priority, -item.updated_at)))

    def events_since(self, cursor: int = 0) -> tuple[EngagementEvent, ...]:
        """Return events after a global log cursor.

        Before schema v3 this method compared per-engagement sequence numbers,
        which was incorrect as soon as two engagements existed. The cursor is
        now monotonic across the whole registry.
        """
        with self._lock:
            return tuple(event for event in self._events if event.log_seq > int(cursor or 0))

    def read_events(
        self,
        consumer_id: str = "",
        *,
        cursor: int | None = None,
        limit: int = 128,
    ) -> EngagementEventBatch:
        """Read a bounded event batch and disclose retention gaps explicitly.

        This compatibility read never creates a delivery lease. Named consumers
        default to their persisted acknowledged cursor and callers still advance
        checkpoints explicitly with :meth:`acknowledge_events`.
        """
        key = str(consumer_id or "").strip()[:96]
        with self._lock:
            state = self._consumers.get(key) if key else None
            requested = (
                int(cursor or 0)
                if cursor is not None
                else int(state.acknowledged_cursor if state is not None else 0)
            )
            return self._read_batch_locked(key, requested=requested, limit=limit)

    def block_consumer(
        self, consumer_id: str, reason: str, *, now: float | None = None
    ) -> EngagementConsumerState:
        key = str(consumer_id or "").strip()[:96]
        if not key:
            raise ValueError("consumer_id is required")
        now = self._clock() if now is None else float(now)
        detail = str(reason or "consumer blocked").strip()[:240]
        with self._lock:
            state = self._consumers.get(key, EngagementConsumerState(consumer_id=key))
            updated = replace(
                state, lease_start_cursor=0, lease_end_cursor=0, lease_expires_at=0.0,
                next_retry_at=0.0, blocked_reason=detail, blocked_at=now,
                last_failure_at=now, last_error=detail,
            )
            self._consumers[key] = updated
            self._touch_locked()
            return updated

    def lease_events(
        self,
        consumer_id: str,
        *,
        now: float | None = None,
        lease_seconds: float = 30.0,
        limit: int = 128,
    ) -> EngagementEventBatch:
        """Lease an event batch for at-least-once processing.

        Repeated reads during an active lease return the same cursor range. A
        failed delivery remains unacknowledged and becomes available after its
        retry backoff. This prevents two worker ticks from racing ahead merely
        because both felt optimistic at the same time.
        """
        key = str(consumer_id or "").strip()[:96]
        if not key:
            raise ValueError("consumer_id is required")
        now = self._clock() if now is None else float(now)
        duration = max(1.0, min(3600.0, float(lease_seconds)))
        with self._lock:
            state = self._consumers.get(key, EngagementConsumerState(consumer_id=key))
            if state.blocked_reason:
                batch = self._read_batch_locked(
                    key, requested=state.acknowledged_cursor, limit=0,
                )
                return replace(batch, blocked_reason=state.blocked_reason)
            if state.next_retry_at > now:
                batch = self._read_batch_locked(
                    key, requested=state.acknowledged_cursor, limit=0,
                )
                return replace(
                    batch,
                    delivery_attempt=state.delivery_attempt,
                    retry_after_seconds=max(0.0, state.next_retry_at - now),
                )

            pending_lease = state.lease_end_cursor > state.acknowledged_cursor
            if pending_lease:
                events = tuple(
                    event for event in self._events
                    if state.lease_start_cursor < event.log_seq <= state.lease_end_cursor
                )
                earliest = self._events[0].log_seq if self._events else self._event_log_seq + 1
                lost = max(0, (earliest - 1) - state.lease_start_cursor)
                lease_is_live = state.lease_expires_at > now
                attempt = max(1, state.delivery_attempt) if lease_is_live else max(1, state.delivery_attempt + 1)
                expires = state.lease_expires_at if lease_is_live else now + duration
                if not lease_is_live:
                    state = replace(
                        state, lease_expires_at=expires, delivery_attempt=attempt,
                        next_retry_at=0.0,
                    )
                    self._consumers[key] = state
                    self._touch_locked()
                delivery_id, event_digest = _batch_identity(
                    key, state.lease_start_cursor, state.lease_end_cursor, events
                )
                return EngagementEventBatch(
                    consumer_id=key,
                    requested_cursor=state.lease_start_cursor,
                    next_cursor=state.lease_end_cursor,
                    earliest_available_cursor=earliest,
                    latest_cursor=self._event_log_seq,
                    events=events,
                    lost_event_count=lost,
                    leased_until=expires,
                    delivery_attempt=attempt,
                    delivery_id=delivery_id,
                    event_digest=event_digest,
                )

            batch = self._read_batch_locked(
                key, requested=state.acknowledged_cursor, limit=limit,
            )
            if not batch.events:
                if state.lease_end_cursor or state.lease_expires_at or state.delivery_attempt:
                    self._consumers[key] = replace(
                        state,
                        lease_start_cursor=0,
                        lease_end_cursor=0,
                        lease_expires_at=0.0,
                        delivery_attempt=0,
                        next_retry_at=0.0,
                    )
                    self._touch_locked()
                return batch
            attempt = max(1, state.delivery_attempt + 1)
            leased = replace(
                state,
                lease_start_cursor=batch.requested_cursor,
                lease_end_cursor=batch.next_cursor,
                lease_expires_at=now + duration,
                delivery_attempt=attempt,
                next_retry_at=0.0,
            )
            self._consumers[key] = leased
            self._touch_locked()
            return replace(
                batch,
                leased_until=leased.lease_expires_at,
                delivery_attempt=attempt,
            )

    def commit_delivery(
        self,
        consumer_id: str,
        batch: EngagementEventBatch,
        *,
        now: float | None = None,
        outcome: str = "processed",
        handler_token: str = "",
    ) -> EngagementDeliveryReceipt:
        """Atomically persist a delivery receipt and advance its cursor."""

        key = str(consumer_id or "").strip()[:96]
        if not key or key != batch.consumer_id:
            raise ValueError("consumer_id does not match leased batch")
        now = self._clock() if now is None else float(now)
        with self._lock:
            existing = self._delivery_receipts.get(batch.delivery_id)
            if existing is not None:
                return existing
            state = self._consumers.get(key, EngagementConsumerState(consumer_id=key))
            if batch.next_cursor < state.acknowledged_cursor:
                raise ValueError("delivery cursor is behind acknowledged cursor")
            if state.lease_end_cursor and batch.next_cursor != state.lease_end_cursor:
                raise ValueError("delivery does not match active lease")
            receipt = EngagementDeliveryReceipt(
                delivery_id=batch.delivery_id,
                consumer_id=key,
                from_cursor=batch.requested_cursor,
                to_cursor=batch.next_cursor,
                event_digest=batch.event_digest,
                committed_at=now,
                outcome=str(outcome or "processed").strip()[:48] or "processed",
                handler_token=str(handler_token or "").strip()[:160],
            )
            self._delivery_receipts[receipt.delivery_id] = receipt
            if len(self._delivery_receipts) > self._max_delivery_receipts:
                ordered = sorted(
                    self._delivery_receipts.values(),
                    key=lambda item: (item.committed_at, item.delivery_id),
                )[-self._max_delivery_receipts:]
                self._delivery_receipts = {item.delivery_id: item for item in ordered}
            acknowledged = max(state.acknowledged_cursor, min(batch.next_cursor, self._event_log_seq))
            self._consumers[key] = replace(
                state, acknowledged_cursor=acknowledged, lease_start_cursor=0,
                lease_end_cursor=0, lease_expires_at=0.0, delivery_attempt=0,
                next_retry_at=0.0, last_success_at=now, last_error="",
                blocked_reason="", blocked_at=0.0,
            )
            self._touch_locked()
            return receipt

    def split_delivery_lease(
        self, consumer_id: str, *, split_cursor: int, now: float | None = None
    ) -> EngagementConsumerState:
        """Narrow a failed multi-event lease so poison events are isolated."""

        key = str(consumer_id or "").strip()[:96]
        if not key:
            raise ValueError("consumer_id is required")
        now = self._clock() if now is None else float(now)
        with self._lock:
            state = self._consumers.get(key, EngagementConsumerState(consumer_id=key))
            split = int(split_cursor)
            if not (state.lease_start_cursor < split < state.lease_end_cursor):
                raise ValueError("split cursor must be inside the active lease")
            updated = replace(
                state, lease_end_cursor=split, lease_expires_at=0.0,
                delivery_attempt=0, next_retry_at=now,
            )
            self._consumers[key] = updated
            self._touch_locked()
            return updated

    def acknowledge_events(self, consumer_id: str, cursor: int, *, now: float | None = None) -> int:
        """Persist a monotonic checkpoint and clear a completed lease."""
        key = str(consumer_id or "").strip()[:96]
        if not key:
            raise ValueError("consumer_id is required")
        now = self._clock() if now is None else float(now)
        with self._lock:
            state = self._consumers.get(key, EngagementConsumerState(consumer_id=key))
            acknowledged = max(
                state.acknowledged_cursor,
                min(max(0, int(cursor or 0)), self._event_log_seq),
            )
            changed = acknowledged != state.acknowledged_cursor or state.lease_end_cursor > 0
            self._consumers[key] = replace(
                state,
                acknowledged_cursor=acknowledged,
                lease_start_cursor=0,
                lease_end_cursor=0,
                lease_expires_at=0.0,
                delivery_attempt=0,
                next_retry_at=0.0,
                last_success_at=now,
                last_error="",
                blocked_reason="",
                blocked_at=0.0,
            )
            if changed:
                self._touch_locked()
            return acknowledged

    def fail_delivery(
        self,
        consumer_id: str,
        error: BaseException | str,
        *,
        now: float | None = None,
        max_attempts: int = 5,
        backoff_initial_seconds: float = 2.0,
        backoff_max_seconds: float = 60.0,
        max_dead_letters: int = 64,
    ) -> EngagementConsumerState:
        """Record a failed leased delivery, retrying or dead-lettering it.

        Once the attempt budget is exhausted, the leased cursor range is moved
        to a bounded dead-letter ledger and the consumer advances. The failure
        is therefore visible and recoverable by operators without blocking every
        newer event behind one poisonous payload forever.
        """
        key = str(consumer_id or "").strip()[:96]
        if not key:
            raise ValueError("consumer_id is required")
        now = self._clock() if now is None else float(now)
        maximum = max(1, int(max_attempts))
        initial = max(0.0, float(backoff_initial_seconds))
        cap = max(initial, float(backoff_max_seconds))
        with self._lock:
            state = self._consumers.get(key, EngagementConsumerState(consumer_id=key))
            reason = str(error or "delivery failed").strip()[:240]
            attempt = max(1, state.delivery_attempt)
            if state.lease_end_cursor <= state.acknowledged_cursor:
                updated = replace(
                    state,
                    last_failure_at=now,
                    last_error=reason,
                    next_retry_at=now + min(cap, initial),
                    delivery_attempt=attempt,
                )
                self._consumers[key] = updated
                self._touch_locked()
                return updated

            if attempt >= maximum:
                hashes = tuple(
                    event.event_hash for event in self._events
                    if state.lease_start_cursor < event.log_seq <= state.lease_end_cursor
                    and event.event_hash
                )
                captured_events = tuple(
                    event for event in self._events
                    if state.lease_start_cursor < event.log_seq <= state.lease_end_cursor
                )
                self._dead_letters.append(EngagementDeadLetter(
                    letter_id=_dead_letter_id(
                        key, state.lease_start_cursor, state.lease_end_cursor, now, hashes
                    ),
                    consumer_id=key,
                    from_cursor=state.lease_start_cursor,
                    to_cursor=state.lease_end_cursor,
                    created_at=now,
                    attempts=attempt,
                    reason=reason,
                    event_hashes=hashes,
                    events=captured_events,
                ))
                keep = max(1, int(max_dead_letters))
                self._dead_letters = self._dead_letters[-keep:]
                updated = replace(
                    state,
                    acknowledged_cursor=max(state.acknowledged_cursor, state.lease_end_cursor),
                    lease_start_cursor=0,
                    lease_end_cursor=0,
                    lease_expires_at=0.0,
                    delivery_attempt=0,
                    next_retry_at=0.0,
                    last_failure_at=now,
                    last_error=reason,
                    dead_letter_count=state.dead_letter_count + 1,
                )
            else:
                delay = min(cap, initial * (2 ** max(0, attempt - 1)))
                updated = replace(
                    state,
                    lease_expires_at=0.0,
                    next_retry_at=now + delay,
                    last_failure_at=now,
                    last_error=reason,
                )
            self._consumers[key] = updated
            self._touch_locked()
            return updated

    def reset_consumer(self, consumer_id: str, *, cursor: int | None = None) -> EngagementConsumerState:
        key = str(consumer_id or "").strip()[:96]
        if not key:
            raise ValueError("consumer_id is required")
        with self._lock:
            current = self._consumers.get(key, EngagementConsumerState(consumer_id=key))
            acknowledged = current.acknowledged_cursor if cursor is None else max(0, min(int(cursor), self._event_log_seq))
            updated = EngagementConsumerState(consumer_id=key, acknowledged_cursor=acknowledged)
            self._consumers[key] = updated
            self._touch_locked()
            return updated

    def reset_consumer_to_earliest(self, consumer_id: str) -> EngagementConsumerState:
        """Move a gapped consumer to the first retained cursor without skipping retained events."""

        with self._lock:
            cursor = max(0, self.earliest_event_cursor - 1)
        return self.reset_consumer(consumer_id, cursor=cursor)

    def _read_batch_locked(self, consumer_id: str, *, requested: int, limit: int) -> EngagementEventBatch:
        requested = max(0, int(requested or 0))
        latest = self._event_log_seq
        earliest = self._events[0].log_seq if self._events else latest + 1
        lost = max(0, (earliest - 1) - requested)
        effective = max(requested, earliest - 1)
        bounded = max(0, min(1024, int(limit or 0)))
        events = tuple(event for event in self._events if event.log_seq > effective)[:bounded]
        next_cursor = events[-1].log_seq if events else effective
        delivery_id, event_digest = _batch_identity(
            consumer_id, requested, next_cursor, events
        )
        return EngagementEventBatch(
            consumer_id=consumer_id,
            requested_cursor=requested,
            next_cursor=next_cursor,
            earliest_available_cursor=earliest,
            latest_cursor=latest,
            events=events,
            lost_event_count=lost,
            delivery_id=delivery_id,
            event_digest=event_digest,
        )

    def events_for(self, engagement_id: str, *, after_seq: int = 0) -> tuple[EngagementEvent, ...]:
        with self._lock:
            return tuple(
                event for event in self._events
                if event.engagement_id == str(engagement_id or "") and event.seq > int(after_seq or 0)
            )

    def reconcile_inactivity(self, *, now: float | None = None) -> tuple[Engagement, ...]:
        now = self._clock() if now is None else float(now)
        transitioned: list[Engagement] = []
        with self._lock:
            candidates = tuple(self._engagements.values())
            for engagement in candidates:
                if not engagement.active:
                    continue
                behavior = engagement.continuity.inactivity_behavior
                if behavior == InactivityBehavior.KEEP:
                    continue
                baseline = max(
                    engagement.last_progress_at,
                    engagement.last_user_at,
                    engagement.last_assistant_at,
                    engagement.lifecycle_changed_at,
                    engagement.updated_at,
                )
                if now - baseline < engagement.continuity.inactivity_timeout_seconds:
                    continue
                target = (
                    LifecycleStatus.COMPLETED
                    if behavior == InactivityBehavior.COMPLETE
                    else LifecycleStatus.PAUSED
                )
                updated = self.transition(
                    engagement.engagement_id,
                    target,
                    now=now,
                    detail="inactivity timeout",
                    source="lifecycle_controller",
                    reason="inactivity_timeout",
                )
                if updated is not None:
                    transitioned.append(updated)
        return tuple(transitioned)

    def compact(
        self,
        *,
        now: float | None = None,
        terminal_retention_seconds: float = 604800.0,
        max_terminal_engagements: int = 64,
    ) -> tuple[str, ...]:
        """Prune acknowledged terminal engagements without dropping live work."""
        now = self._clock() if now is None else float(now)
        retention = max(0.0, float(terminal_retention_seconds))
        terminal_limit = max(0, int(max_terminal_engagements))
        with self._lock:
            pending = {item.engagement_id for item in self.pending_terminal()}
            terminal = sorted(
                (
                    item for item in self._engagements.values()
                    if item.terminal and item.engagement_id not in pending
                ),
                key=lambda item: item.updated_at,
                reverse=True,
            )
            remove: set[str] = set()
            for index, engagement in enumerate(terminal):
                age = now - max(engagement.lifecycle_changed_at, engagement.updated_at)
                expired = retention == 0.0 or age >= retention
                over_limit = index >= terminal_limit
                if expired or over_limit:
                    remove.add(engagement.engagement_id)
            if not remove:
                return ()
            for engagement_id in remove:
                self._engagements.pop(engagement_id, None)
            # The event stream is an append-only integrity chain. Retention trims
            # only its oldest prefix in _record_event_locked; deleting arbitrary
            # middle events here would make every later hash unverifiable.
            self._touch_locked()
            return tuple(sorted(remove))

    def replay_engagement(self, engagement_id: str) -> EngagementReplayReport:
        """Replay retained events and compare the adapter projection to state.

        A replay is complete only when the retained stream begins at event one
        and reaches the engagement's current event sequence. Long-running tasks
        may have an intentionally truncated prefix; those are reported rather
        than reconstructed from wishful thinking.
        """
        key = str(engagement_id or "").strip()
        with self._lock:
            engagement = self._engagements.get(key)
            if engagement is None:
                return EngagementReplayReport(
                    engagement_id=key, complete=False, replayed_events=0,
                    first_seq=0, last_seq=0, state_matches=False, reason="engagement_not_found",
                )
            events = sorted(
                (event for event in self._events if event.engagement_id == key),
                key=lambda event: event.seq,
            )
            first_seq = events[0].seq if events else 0
            last_seq = events[-1].seq if events else 0
            complete = bool(
                events
                and first_seq == 1
                and events[0].kind == "engagement.started"
                and last_seq == engagement.event_seq
            )
            if not complete:
                reason = "retained_prefix_incomplete" if events else "no_retained_events"
                return EngagementReplayReport(
                    engagement_id=key, complete=False, replayed_events=0,
                    first_seq=first_seq, last_seq=last_seq, state_matches=False,
                    reason=reason, current_state=dict(engagement.domain_state),
                )
            adapter = self.adapters.get(engagement.adapter_id)
            state: Mapping[str, Any] = adapter.initial_state(
                engagement.objective, metadata={"source": events[0].source},
            )
            for event in events:
                state = adapter.reduce(state, event)
            replayed = dict(state)
            current = dict(engagement.domain_state)
            return EngagementReplayReport(
                engagement_id=key, complete=True, replayed_events=len(events),
                first_seq=first_seq, last_seq=last_seq,
                state_matches=replayed == current,
                reason="" if replayed == current else "projection_mismatch",
                current_state=current, replayed_state=replayed,
            )

    def repair_engagement_projection(self, engagement_id: str) -> EngagementReplayReport:
        """Replace a corrupt adapter projection only when replay is complete."""
        with self._lock:
            report = self.replay_engagement(engagement_id)
            if not report.complete or report.state_matches:
                return report
            engagement = self._engagements.get(report.engagement_id)
            if engagement is None:
                return report
            self._engagements[report.engagement_id] = replace(
                engagement, domain_state=dict(report.replayed_state),
            )
            if self._health is not None:
                self._health.record_success(
                    "engagement.projection_replay",
                    details={"engagement_id": report.engagement_id, "events": report.replayed_events},
                )
            self._touch_locked()
            return self.replay_engagement(report.engagement_id)

    def audit_projections(self) -> tuple[EngagementReplayReport, ...]:
        with self._lock:
            return tuple(self.replay_engagement(item.engagement_id) for item in self._engagements.values())

    def accept_snapshot_integrity(self, *, reason: str = "operator_verified") -> dict[str, Any]:
        """Explicitly trust the currently loaded/repaired state and clear load-integrity debt."""

        with self._lock:
            self._snapshot_integrity_valid = True
            self._snapshot_integrity_error = ""
            if self._health is not None:
                self._health.record_success(
                    "engagement.snapshot_integrity",
                    details={"reason": str(reason or "operator_verified")[:120]},
                )
            self._touch_locked()
            return self.snapshot_integrity()

    def snapshot_integrity(self) -> dict[str, Any]:
        with self._lock:
            return {
                "valid": self._snapshot_integrity_valid,
                "error": self._snapshot_integrity_error,
                "schema_version": 7,
            }

    def audit_delivery_receipts(self) -> EngagementReceiptAudit:
        """Verify committed receipts against consumer cursors and retained payloads."""

        with self._lock:
            errors: list[str] = []
            verifiable = 0
            for receipt in self._delivery_receipts.values():
                state = self._consumers.get(receipt.consumer_id)
                if state is None or state.acknowledged_cursor < receipt.to_cursor:
                    errors.append(f"receipt_cursor_uncommitted:{receipt.delivery_id}")
                retained = tuple(
                    event for event in self._events
                    if receipt.from_cursor < event.log_seq <= receipt.to_cursor
                )
                expected_count = max(0, receipt.to_cursor - receipt.from_cursor)
                if len(retained) == expected_count:
                    verifiable += 1
                    _, digest = _batch_identity(
                        receipt.consumer_id, receipt.from_cursor, receipt.to_cursor, retained
                    )
                    if digest != receipt.event_digest:
                        errors.append(f"receipt_digest_mismatch:{receipt.delivery_id}")
            return EngagementReceiptAudit(
                valid=not errors, checked_receipts=len(self._delivery_receipts),
                verifiable_receipts=verifiable, errors=tuple(dict.fromkeys(errors)),
            )

    def repair_receipt_cursors(self, *, now: float | None = None) -> tuple[str, ...]:
        """Advance only cursors proven committed by durable delivery receipts."""

        now = self._clock() if now is None else float(now)
        repaired: list[str] = []
        with self._lock:
            by_consumer: dict[str, int] = {}
            for receipt in self._delivery_receipts.values():
                by_consumer[receipt.consumer_id] = max(
                    by_consumer.get(receipt.consumer_id, 0), receipt.to_cursor
                )
            for consumer_id, cursor in by_consumer.items():
                state = self._consumers.get(
                    consumer_id, EngagementConsumerState(consumer_id=consumer_id)
                )
                if state.acknowledged_cursor >= cursor:
                    continue
                self._consumers[consumer_id] = replace(
                    state, acknowledged_cursor=min(cursor, self._event_log_seq),
                    lease_start_cursor=0, lease_end_cursor=0, lease_expires_at=0.0,
                    delivery_attempt=0, next_retry_at=0.0, last_success_at=now,
                    last_error="", blocked_reason="", blocked_at=0.0,
                )
                repaired.append(consumer_id)
            if repaired:
                self._touch_locked()
        return tuple(sorted(repaired))

    def audit_event_stream(self) -> EngagementStreamAudit:
        """Verify the retained hash chain, cursor order and chain metadata."""
        with self._lock:
            errors: list[str] = list(self._load_issues)
            expected_prev = self._event_chain_anchor
            previous_cursor = 0
            for index, event in enumerate(self._events):
                if previous_cursor and event.log_seq != previous_cursor + 1:
                    errors.append(f"event_log_gap:{previous_cursor}->{event.log_seq}")
                if event.log_seq <= previous_cursor:
                    errors.append(f"event_log_not_monotonic:{event.log_seq}")
                if event.prev_hash != expected_prev:
                    errors.append(f"event_prev_hash_mismatch:{event.log_seq}")
                expected_hash = _event_hash(event, prev_hash=expected_prev)
                if event.event_hash != expected_hash:
                    errors.append(f"event_hash_mismatch:{event.log_seq}")
                expected_prev = event.event_hash
                previous_cursor = event.log_seq
            expected_head = self._events[-1].event_hash if self._events else self._event_chain_anchor
            if self._event_chain_head != expected_head:
                errors.append("event_chain_head_mismatch")
            return EngagementStreamAudit(
                valid=not errors,
                checked_events=len(self._events),
                earliest_cursor=self._events[0].log_seq if self._events else self._event_log_seq + 1,
                latest_cursor=self._event_log_seq,
                chain_anchor=self._event_chain_anchor,
                chain_head=self._event_chain_head,
                errors=tuple(dict.fromkeys(errors)),
            )

    def rechain_event_stream(self, *, reason: str = "operator_repair") -> EngagementStreamAudit:
        """Explicitly rebuild integrity metadata without changing event content."""
        with self._lock:
            previous = self._event_chain_anchor
            repaired: list[EngagementEvent] = []
            for event in self._events:
                normalized = replace(event, prev_hash=previous, event_hash="")
                normalized = replace(normalized, event_hash=_event_hash(normalized, prev_hash=previous))
                repaired.append(normalized)
                previous = normalized.event_hash
            self._events = repaired
            self._event_chain_head = previous
            self._load_issues = []
            if self._health is not None:
                self._health.record_success(
                    "engagement.event_stream",
                    details={"repair_reason": str(reason or "operator_repair")[:120], "events": len(repaired)},
                )
            self._touch_locked()
            return self.audit_event_stream()

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            payload = {
                "schema_version": 7,
                "revision": self._revision,
                "event_log_seq": self._event_log_seq,
                "event_chain_anchor": self._event_chain_anchor,
                "event_chain_head": self._event_chain_head,
                "consumer_cursors": {
                    key: state.acknowledged_cursor for key, state in self._consumers.items()
                },
                "consumers": {
                    key: state.to_dict() for key, state in self._consumers.items()
                },
                "dead_letters": [item.to_dict() for item in self._dead_letters],
                "delivery_receipts": [item.to_dict() for item in self.delivery_receipts()],
                "engagements": [item.to_dict() for item in self._engagements.values()],
                "events": [event.to_dict() for event in self._events[-self._max_events:]],
            }
            payload["snapshot_digest"] = _snapshot_digest(payload)
            return payload

    def validate_invariants(self) -> tuple[str, ...]:
        """Return structural violations without mutating state."""
        with self._lock:
            errors: list[str] = []
            active_foreground = [
                item.engagement_id for item in self._engagements.values()
                if item.active and item.focus == FocusMode.FOREGROUND
            ]
            if len(active_foreground) > 1:
                errors.append("multiple_active_foreground:" + ",".join(sorted(active_foreground)))
            for item in self._engagements.values():
                if item.active and item.focus == FocusMode.SUSPENDED:
                    errors.append(f"active_suspended:{item.engagement_id}")
                if not item.active and item.focus != FocusMode.SUSPENDED:
                    errors.append(f"inactive_visible:{item.engagement_id}")
                if item.last_event_log_seq > self._event_log_seq:
                    errors.append(f"engagement_cursor_ahead:{item.engagement_id}")
            log_sequences = [event.log_seq for event in self._events]
            if log_sequences != sorted(log_sequences) or len(log_sequences) != len(set(log_sequences)):
                errors.append("event_log_not_strictly_monotonic")
            per_engagement: dict[str, int] = {}
            for event in self._events:
                previous = per_engagement.get(event.engagement_id, 0)
                if event.seq <= previous:
                    errors.append(f"event_seq_not_monotonic:{event.engagement_id}")
                    break
                per_engagement[event.engagement_id] = event.seq
            for receipt in self._delivery_receipts.values():
                if receipt.from_cursor > receipt.to_cursor:
                    errors.append(f"delivery_receipt_reversed:{receipt.delivery_id}")
                if receipt.to_cursor > self._event_log_seq:
                    errors.append(f"delivery_receipt_ahead:{receipt.delivery_id}")
            for consumer_id, state in self._consumers.items():
                cursor = state.acknowledged_cursor
                if cursor < 0 or cursor > self._event_log_seq:
                    errors.append(f"invalid_consumer_cursor:{consumer_id}")
                if state.lease_end_cursor > self._event_log_seq:
                    errors.append(f"consumer_lease_ahead:{consumer_id}")
                if state.lease_start_cursor > state.lease_end_cursor:
                    errors.append(f"consumer_lease_reversed:{consumer_id}")
            errors.extend(self.audit_event_stream().errors)
            errors.extend(self.audit_delivery_receipts().errors)
            for report in self.audit_projections():
                if report.complete and not report.state_matches:
                    errors.append(f"projection_replay_mismatch:{report.engagement_id}")
            return tuple(dict.fromkeys(errors))

    def _delivery_pressure_locked(self) -> dict[str, Any]:
        lag = {
            key: max(0, self._event_log_seq - state.acknowledged_cursor)
            for key, state in self._consumers.items()
        }
        lagging = sorted(
            key for key, value in lag.items() if value >= self._consumer_lag_warning_events
        )
        blocked = sorted(
            key for key, state in self._consumers.items() if state.blocked_reason
        )
        open_letters = sum(1 for item in self._dead_letters if item.open)
        earliest = self._events[0].log_seq if self._events else self._event_log_seq + 1
        at_risk = sorted(
            key for key, state in self._consumers.items()
            if state.acknowledged_cursor < earliest - 1
        )
        reasons: list[str] = []
        if lagging:
            reasons.append("consumer_lag")
        if at_risk:
            reasons.append("consumer_retention_gap")
        if blocked:
            reasons.append("consumer_blocked")
        if open_letters >= self._open_dead_letter_warning_count:
            reasons.append("open_dead_letters")
        return {
            "degraded": bool(reasons),
            "reasons": reasons,
            "max_lag": max(lag.values(), default=0),
            "lag_warning_events": self._consumer_lag_warning_events,
            "lagging_consumers": lagging,
            "at_risk_consumers": at_risk,
            "blocked_consumers": blocked,
            "open_dead_letters": open_letters,
            "open_dead_letter_warning_count": self._open_dead_letter_warning_count,
        }

    def diagnostics(self, *, now: float | None = None) -> dict[str, Any]:
        now = self._clock() if now is None else float(now)
        with self._lock:
            counts = {status.value: 0 for status in LifecycleStatus}
            focus_counts = {focus.value: 0 for focus in FocusMode}
            stale: list[dict[str, Any]] = []
            for engagement in self._engagements.values():
                counts[engagement.lifecycle.value] += 1
                focus_counts[engagement.focus.value] += 1
                if engagement.active:
                    idle = max(0.0, now - max(engagement.last_progress_at, engagement.updated_at))
                    if idle >= engagement.continuity.inactivity_timeout_seconds:
                        stale.append({
                            "engagement_id": engagement.engagement_id,
                            "kind": engagement.kind,
                            "idle_seconds": round(idle, 2),
                        })
            consumers = {key: state.to_dict() for key, state in sorted(self._consumers.items())}
            audit = self.audit_event_stream()
            projection_reports = self.audit_projections()
            complete_replays = [item for item in projection_reports if item.complete]
            projection_mismatches = [item.engagement_id for item in complete_replays if not item.state_matches]
            return {
                "schema_version": 2,
                "revision": self._revision,
                "event_cursor": self._event_log_seq,
                "earliest_event_cursor": self._events[0].log_seq if self._events else self._event_log_seq + 1,
                "retained_events": len(self._events),
                "event_integrity": audit.to_dict(),
                "delivery_receipt_integrity": self.audit_delivery_receipts().to_dict(),
                "snapshot_integrity": self.snapshot_integrity(),
                "projection_replay": {
                    "complete_count": len(complete_replays),
                    "incomplete_count": len(projection_reports) - len(complete_replays),
                    "mismatches": projection_mismatches,
                },
                "consumer_cursors": {key: state.acknowledged_cursor for key, state in self._consumers.items()},
                "consumers": consumers,
                "consumer_lag": {
                    key: max(0, self._event_log_seq - state.acknowledged_cursor)
                    for key, state in sorted(self._consumers.items())
                },
                "delivery_pressure": self._delivery_pressure_locked(),
                "active_delivery_leases": sum(
                    1 for state in self._consumers.values()
                    if state.lease_end_cursor > state.acknowledged_cursor and state.lease_expires_at > now
                ),
                "delivery_receipt_count": len(self._delivery_receipts),
                "recent_delivery_receipts": [item.to_dict() for item in self.delivery_receipts()[-8:]],
                "dead_letter_count": len(self._dead_letters),
                "open_dead_letter_count": sum(1 for item in self._dead_letters if item.open),
                "resolved_dead_letter_count": sum(1 for item in self._dead_letters if not item.open),
                "dead_letters": [item.to_dict() for item in self._dead_letters[-8:]],
                "engagement_count": len(self._engagements),
                "pending_terminal_count": len(self.pending_terminal()),
                "invariant_violations": list(self.validate_invariants()),
                "lifecycle_counts": counts,
                "focus_counts": focus_counts,
                "stale_active": stale,
            }

    def _append_locked(
        self,
        engagement: Engagement,
        kind: str,
        now: float,
        source: str,
        payload: Mapping[str, Any],
        *,
        evidence_refs: Sequence[str] = (),
    ) -> Engagement:
        updated, event = self._record_event_locked(
            engagement,
            kind,
            now,
            source,
            payload,
            evidence_refs=evidence_refs,
        )
        if not str(kind or "").startswith("engagement."):
            adapter = self.adapters.get(updated.adapter_id)
            signal = adapter.lifecycle_signal(updated, event, updated.domain_state)
            if isinstance(signal, LifecycleSignal) and signal.target != updated.lifecycle:
                focus = updated.focus
                resume_focus = updated.resume_focus
                if signal.target == LifecycleStatus.ACTIVE:
                    focus = updated.resume_focus if updated.resume_focus != FocusMode.SUSPENDED else FocusMode.BACKGROUND
                    if focus == FocusMode.FOREGROUND:
                        self._demote_foreground_locked(except_id=updated.engagement_id)
                elif signal.target in {
                    LifecycleStatus.PAUSED,
                    LifecycleStatus.COMPLETED,
                    LifecycleStatus.CANCELLED,
                }:
                    if updated.focus != FocusMode.SUSPENDED:
                        resume_focus = updated.focus
                    focus = FocusMode.SUSPENDED
                updated = replace(updated, focus=focus, resume_focus=resume_focus)
                self._engagements[updated.engagement_id] = updated
                updated, _ = self._record_event_locked(
                    updated,
                    _event_kind_for(signal.target),
                    now,
                    "lifecycle_controller",
                    {
                        "detail": signal.detail or signal.reason,
                        "reason": signal.reason,
                        "trigger_event": event.kind,
                        "trigger_log_seq": event.log_seq,
                        "confidence": max(0.0, min(1.0, float(signal.confidence))),
                    },
                )
        self._touch_locked()
        return updated

    def _record_event_locked(
        self,
        engagement: Engagement,
        kind: str,
        now: float,
        source: str,
        payload: Mapping[str, Any],
        *,
        evidence_refs: Sequence[str] = (),
    ) -> tuple[Engagement, EngagementEvent]:
        seq = engagement.event_seq + 1
        self._event_log_seq += 1
        previous_hash = self._event_chain_head or self._event_chain_anchor
        event = EngagementEvent(
            engagement_id=engagement.engagement_id,
            seq=seq,
            log_seq=self._event_log_seq,
            timestamp=now,
            kind=str(kind or "unknown"),
            source=str(source or "runtime"),
            payload=dict(payload or {}),
            evidence_refs=tuple(str(item) for item in evidence_refs if str(item).strip())[:16],
            prev_hash=previous_hash,
        )
        event = replace(event, event_hash=_event_hash(event, prev_hash=previous_hash))
        adapter = self.adapters.get(engagement.adapter_id)
        state = adapter.reduce(engagement.domain_state, event)
        progress_event = kind not in {
            "assistant.spoke",
            "engagement.waiting",
            "engagement.binding_updated",
            "engagement.refreshed",
        }
        binding_payload = payload.get("binding") if isinstance(payload, Mapping) else None
        effective_binding = (
            engagement.evidence_binding.merged(binding_payload)
            if binding_payload else engagement.evidence_binding
        )
        lifecycle = engagement.lifecycle
        focus = engagement.focus
        resume_focus = engagement.resume_focus
        lifecycle_changed_at = engagement.lifecycle_changed_at
        if kind == "engagement.completed":
            lifecycle = LifecycleStatus.COMPLETED
            if focus != FocusMode.SUSPENDED:
                resume_focus = focus
            focus = FocusMode.SUSPENDED
        elif kind == "engagement.cancelled":
            lifecycle = LifecycleStatus.CANCELLED
            if focus != FocusMode.SUSPENDED:
                resume_focus = focus
            focus = FocusMode.SUSPENDED
        elif kind == "engagement.paused":
            lifecycle = LifecycleStatus.PAUSED
            if focus != FocusMode.SUSPENDED:
                resume_focus = focus
            focus = FocusMode.SUSPENDED
        elif kind in {"engagement.resumed", "engagement.started"}:
            lifecycle = LifecycleStatus.ACTIVE
            if kind == "engagement.resumed" and focus == FocusMode.SUSPENDED:
                focus = resume_focus if resume_focus != FocusMode.SUSPENDED else FocusMode.BACKGROUND
        elif kind == "engagement.waiting":
            lifecycle = LifecycleStatus.WAITING
        if lifecycle != engagement.lifecycle:
            lifecycle_changed_at = now
        updated = replace(
            engagement,
            lifecycle=lifecycle,
            focus=focus,
            domain_state=dict(state),
            updated_at=now,
            last_progress_at=now if progress_event else engagement.last_progress_at,
            last_user_at=now if kind == "user.message_received" else engagement.last_user_at,
            last_assistant_at=now if kind == "assistant.spoke" else engagement.last_assistant_at,
            event_seq=seq,
            last_event_kind=kind,
            evidence_binding=effective_binding,
            source_surface_key=str(
                effective_binding.surface_key
                or payload.get("surface_key", engagement.source_surface_key)
                or engagement.source_surface_key
            )[:96],
            lifecycle_changed_at=lifecycle_changed_at,
            last_event_log_seq=event.log_seq,
            resume_focus=resume_focus,
        )
        self._engagements[updated.engagement_id] = updated
        self._events.append(event)
        self._event_chain_head = event.event_hash
        if len(self._events) > self._max_events:
            removed = self._events[:-self._max_events]
            self._events = self._events[-self._max_events:]
            if removed:
                self._event_chain_anchor = removed[-1].event_hash
        return updated, event

    def _resumable_for_proposal_locked(
        self,
        proposal: EngagementProposal,
        *,
        now: float,
    ) -> Engagement | None:
        candidates = [
            item for item in self._engagements.values()
            if item.semantic_key == proposal.semantic_key
        ]
        for existing in sorted(candidates, key=lambda item: item.updated_at, reverse=True):
            if existing.lifecycle == LifecycleStatus.CANCELLED:
                continue
            if existing.active:
                return existing
            if existing.lifecycle == LifecycleStatus.PAUSED and proposal.continuity.auto_resume:
                return existing
            if (
                existing.lifecycle == LifecycleStatus.COMPLETED
                and proposal.continuity.reopen_completed
                and now - existing.updated_at <= proposal.continuity.resume_window_seconds
            ):
                return existing
        return None

    def _demote_foreground_locked(self, except_id: str = "") -> None:
        for engagement_id, item in tuple(self._engagements.items()):
            if engagement_id == except_id or not item.active or item.focus != FocusMode.FOREGROUND:
                continue
            self._engagements[engagement_id] = replace(item, focus=FocusMode.BACKGROUND)

    def _touch_locked(self) -> None:
        self._revision += 1
        if self._persist is not None:
            try:
                self._persist(self.snapshot())
            except Exception as exc:  # Persistence callback is a process boundary.
                if self._health is not None:
                    self._health.record_failure(
                        "engagement.persistence",
                        exc,
                        kind=FailureKind.PERSISTENCE_FAILURE,
                        degraded=True,
                        reason="engagement snapshot could not be persisted",
                    )
            else:
                if self._health is not None:
                    self._health.record_success(
                        "engagement.persistence",
                        details={"revision": self._revision, "event_cursor": self._event_log_seq},
                    )

    def _load(self, payload: Mapping[str, Any]) -> None:
        try:
            self._revision = max(0, int(payload.get("revision", 0) or 0))
        except (TypeError, ValueError):
            self._revision = 0
        try:
            self._event_log_seq = max(0, int(payload.get("event_log_seq", 0) or 0))
        except (TypeError, ValueError):
            self._event_log_seq = 0
        for item in tuple(payload.get("engagements", ()) or ()):
            if not isinstance(item, Mapping):
                continue
            engagement = Engagement.from_mapping(item)
            if engagement.engagement_id:
                self._engagements[engagement.engagement_id] = engagement
        # Normalize legacy/corrupt focus combinations before exposing state.
        foreground = sorted(
            (
                item for item in self._engagements.values()
                if item.active and item.focus == FocusMode.FOREGROUND
            ),
            key=lambda item: (item.priority, item.updated_at),
            reverse=True,
        )
        foreground_winner = foreground[0].engagement_id if foreground else ""
        for engagement_id, engagement in tuple(self._engagements.items()):
            focus = engagement.focus
            if engagement.active:
                if focus == FocusMode.SUSPENDED:
                    focus = FocusMode.BACKGROUND
                elif focus == FocusMode.FOREGROUND and engagement_id != foreground_winner:
                    focus = FocusMode.BACKGROUND
            elif focus != FocusMode.SUSPENDED:
                focus = FocusMode.SUSPENDED
            if focus != engagement.focus:
                self._engagements[engagement_id] = replace(engagement, focus=focus)

        try:
            schema_version = max(0, int(payload.get("schema_version", 0) or 0))
        except (TypeError, ValueError):
            schema_version = 0
        if schema_version >= 6:
            expected_digest = str(payload.get("snapshot_digest", "") or "")
            if not expected_digest:
                self._snapshot_integrity_valid = False
                self._snapshot_integrity_error = "snapshot_digest_missing"
            elif expected_digest != _snapshot_digest(payload):
                self._snapshot_integrity_valid = False
                self._snapshot_integrity_error = "snapshot_digest_mismatch"
        self._event_chain_anchor = str(payload.get("event_chain_anchor", "") or "")[:96]
        self._event_chain_head = str(payload.get("event_chain_head", "") or "")[:96]
        migrated_events: list[EngagementEvent] = []
        migration_cursor = 0
        for item in tuple(payload.get("events", ()) or ()):
            if not isinstance(item, Mapping):
                continue
            try:
                event = EngagementEvent.from_mapping(item)
            except (TypeError, ValueError):
                self._load_issues.append("invalid_event_payload")
                continue
            if not event.engagement_id:
                continue
            migration_cursor += 1
            if event.log_seq <= 0:
                event = replace(event, log_seq=migration_cursor)
            migrated_events.append(event)
            self._event_log_seq = max(self._event_log_seq, event.log_seq)
        self._events = sorted(migrated_events, key=lambda event: event.log_seq)[-self._max_events:]

        legacy_chain = schema_version < 5 or any(
            not event.event_hash for event in self._events
        )
        if legacy_chain:
            previous = self._event_chain_anchor
            normalized: list[EngagementEvent] = []
            for event in self._events:
                event = replace(event, prev_hash=previous, event_hash="")
                event = replace(event, event_hash=_event_hash(event, prev_hash=previous))
                normalized.append(event)
                previous = event.event_hash
            self._events = normalized
            self._event_chain_head = previous
        elif self._events and not self._event_chain_head:
            self._event_chain_head = self._events[-1].event_hash
        if len(self._events) > self._max_events:
            removed = self._events[:-self._max_events]
            self._events = self._events[-self._max_events:]
            if removed:
                self._event_chain_anchor = removed[-1].event_hash

        raw_states = payload.get("consumers", {})
        if isinstance(raw_states, Mapping):
            for key, value in raw_states.items():
                consumer_id = str(key or "").strip()[:96]
                if not consumer_id:
                    continue
                state = EngagementConsumerState.from_mapping(consumer_id, value if isinstance(value, Mapping) else {})
                state = replace(
                    state,
                    acknowledged_cursor=min(state.acknowledged_cursor, self._event_log_seq),
                    lease_start_cursor=min(state.lease_start_cursor, self._event_log_seq),
                    lease_end_cursor=min(state.lease_end_cursor, self._event_log_seq),
                )
                self._consumers[consumer_id] = state
        raw_consumers = payload.get("consumer_cursors", {})
        if isinstance(raw_consumers, Mapping):
            for key, value in raw_consumers.items():
                consumer_id = str(key or "").strip()[:96]
                if not consumer_id or consumer_id in self._consumers:
                    continue
                try:
                    cursor = int(value or 0)
                except (TypeError, ValueError):
                    continue
                self._consumers[consumer_id] = EngagementConsumerState(
                    consumer_id=consumer_id,
                    acknowledged_cursor=max(0, min(cursor, self._event_log_seq)),
                )
        for item in tuple(payload.get("delivery_receipts", ()) or ()):
            if not isinstance(item, Mapping):
                continue
            try:
                receipt = EngagementDeliveryReceipt.from_mapping(item)
            except (TypeError, ValueError):
                self._load_issues.append("invalid_delivery_receipt")
                continue
            if receipt.delivery_id and receipt.consumer_id:
                self._delivery_receipts[receipt.delivery_id] = receipt
        if len(self._delivery_receipts) > self._max_delivery_receipts:
            ordered = sorted(
                self._delivery_receipts.values(),
                key=lambda item: (item.committed_at, item.delivery_id),
            )[-self._max_delivery_receipts:]
            self._delivery_receipts = {item.delivery_id: item for item in ordered}
        for item in tuple(payload.get("dead_letters", ()) or ()):
            if not isinstance(item, Mapping):
                continue
            try:
                letter = EngagementDeadLetter.from_mapping(item)
            except (TypeError, ValueError):
                self._load_issues.append("invalid_dead_letter_payload")
                continue
            if letter.consumer_id:
                self._dead_letters.append(letter)
        self._dead_letters = self._dead_letters[-256:]
        for engagement_id, engagement in tuple(self._engagements.items()):
            last_log_seq = max(
                (event.log_seq for event in self._events if event.engagement_id == engagement_id),
                default=engagement.last_event_log_seq,
            )
            if last_log_seq != engagement.last_event_log_seq:
                self._engagements[engagement_id] = replace(
                    engagement,
                    last_event_log_seq=last_log_seq,
                )


__all__ = ["EngagementRegistry"]
