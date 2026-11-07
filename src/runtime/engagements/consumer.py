# -*- coding: utf-8 -*-
"""Reliable event-consumer pump for the engagement stream."""
from __future__ import annotations
from core.di import resolve_dependency

import time
from dataclasses import asdict, dataclass
from typing import Any, Callable, Mapping

from runtime.policy_settings import EngagementSettings
from runtime.subsystem_health import FailureKind, SubsystemHealthRegistry

from .contracts import EngagementEventBatch
from .registry import EngagementRegistry


BatchHandler = Callable[[EngagementEventBatch], Any]


@dataclass(frozen=True, slots=True)
class ConsumerPollResult:
    consumer_id: str
    status: str
    event_count: int = 0
    cursor: int = 0
    delivery_attempt: int = 0
    retry_after_seconds: float = 0.0
    has_gap: bool = False
    dead_lettered: bool = False
    error: str = ""
    dead_letter_id: str = ""
    blocked_reason: str = ""
    delivery_id: str = ""
    receipt_committed: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class EngagementEventConsumer:
    """Process one leased batch per tick with durable delivery semantics."""

    def __init__(
        self,
        registry: EngagementRegistry,
        consumer_id: str,
        handler: BatchHandler,
        *,
        settings: EngagementSettings | None = None,
        health_registry: SubsystemHealthRegistry | None = None,
        clock=None,
        logger: Any | None = None,
    ) -> None:
        key = str(consumer_id or "").strip()[:96]
        if not key:
            raise ValueError("consumer_id is required")
        if not callable(handler):
            raise TypeError("handler must be callable")
        self.registry = registry
        self.consumer_id = key
        self.handler = handler
        self.settings = resolve_dependency(settings, lambda: EngagementSettings())
        self.health_registry = health_registry
        self._clock = clock or time.time
        self.logger = logger
        self.poll_count = 0
        self.success_count = 0
        self.failure_count = 0
        self.idle_count = 0
        self.last_poll_at = 0.0
        self.last_success_at = 0.0
        self.last_failure_at = 0.0
        self.last_error = ""

    @property
    def health_name(self) -> str:
        return f"engagement.consumer.{self.consumer_id}"

    def _invoke_handler(self, batch: EngagementEventBatch) -> tuple[Any, BaseException | None]:
        try:
            return self.handler(batch), None
        except Exception as exc:  # Handler is an external subsystem boundary.
            return None, exc

    @staticmethod
    def _handler_commit_metadata(result: Any) -> tuple[str, str]:
        if isinstance(result, Mapping):
            outcome = str(result.get("outcome", "processed") or "processed")[:48]
            token = str(result.get("idempotency_token", result.get("handler_token", "")) or "")[:160]
            return outcome, token
        if isinstance(result, str):
            return "processed", result[:160]
        return "processed", ""

    def poll_once(self, *, now: float | None = None) -> ConsumerPollResult:
        now = self._clock() if now is None else max(0.0, float(now))
        self.poll_count += 1
        self.last_poll_at = now
        batch = self.registry.lease_events(
            self.consumer_id,
            now=now,
            lease_seconds=self.settings.delivery_lease_seconds,
            limit=self.settings.event_consumer_batch_size,
        )
        if not batch.events:
            self.idle_count += 1
            status = (
                "blocked" if batch.blocked_reason
                else "backoff" if batch.retry_after_seconds > 0.0
                else "idle"
            )
            return ConsumerPollResult(
                consumer_id=self.consumer_id,
                status=status,
                cursor=batch.next_cursor,
                delivery_attempt=batch.delivery_attempt,
                retry_after_seconds=batch.retry_after_seconds,
                has_gap=batch.has_gap,
                blocked_reason=batch.blocked_reason,
                error=batch.blocked_reason,
                delivery_id=batch.delivery_id,
            )

        if batch.has_gap and self.settings.delivery_stop_on_gap:
            self.failure_count += 1
            self.last_failure_at = now
            self.last_error = (
                f"retention gap: lost {batch.lost_event_count} event(s) before cursor "
                f"{batch.earliest_available_cursor}"
            )
            self.registry.block_consumer(
                self.consumer_id, self.last_error, now=now
            )
            if self.health_registry is not None:
                self.health_registry.record_failure(
                    self.health_name,
                    self.last_error,
                    kind=FailureKind.INVARIANT_VIOLATION,
                    degraded=True,
                    reason="engagement consumer stopped on retention gap",
                    details={
                        "lost_event_count": batch.lost_event_count,
                        "requested_cursor": batch.requested_cursor,
                        "earliest_available_cursor": batch.earliest_available_cursor,
                    },
                )
            return ConsumerPollResult(
                consumer_id=self.consumer_id,
                status="gap",
                event_count=len(batch.events),
                cursor=batch.requested_cursor,
                delivery_attempt=batch.delivery_attempt,
                has_gap=True,
                error=self.last_error,
                blocked_reason=self.last_error,
                delivery_id=batch.delivery_id,
            )

        existing_receipt = self.registry.delivery_receipt(batch.delivery_id)
        if existing_receipt is not None:
            cursor = self.registry.acknowledge_events(
                self.consumer_id, existing_receipt.to_cursor, now=now
            )
            self.success_count += 1
            self.last_success_at = now
            self.last_error = ""
            return ConsumerPollResult(
                consumer_id=self.consumer_id, status="duplicate_suppressed",
                event_count=len(batch.events), cursor=cursor,
                delivery_attempt=batch.delivery_attempt, delivery_id=batch.delivery_id,
                receipt_committed=True,
            )

        before_dead_letters = len(self.registry.dead_letters(self.consumer_id, status="open"))
        handler_result, exc = self._invoke_handler(batch)
        if exc is not None:
            self.failure_count += 1
            self.last_failure_at = now
            self.last_error = f"{type(exc).__name__}: {exc}"[:240]
            if (
                self.settings.delivery_split_poison_batches
                and len(batch.events) > 1
                and batch.delivery_attempt >= self.settings.delivery_split_after_attempts
            ):
                midpoint = batch.events[max(0, len(batch.events) // 2 - 1)].log_seq
                state = self.registry.split_delivery_lease(
                    self.consumer_id, split_cursor=midpoint, now=now
                )
                if self.health_registry is not None:
                    self.health_registry.record_failure(
                        self.health_name, exc, kind=FailureKind.EXPECTED_DEGRADATION,
                        degraded=True, reason="engagement batch split to isolate poison event",
                        details={
                            "delivery_id": batch.delivery_id,
                            "original_events": len(batch.events),
                            "split_cursor": midpoint,
                        },
                    )
                return ConsumerPollResult(
                    consumer_id=self.consumer_id, status="split",
                    event_count=len(batch.events), cursor=state.acknowledged_cursor,
                    delivery_attempt=batch.delivery_attempt, error=self.last_error,
                    delivery_id=batch.delivery_id,
                )
            state = self.registry.fail_delivery(
                self.consumer_id,
                exc,
                now=now,
                max_attempts=self.settings.delivery_max_attempts,
                backoff_initial_seconds=self.settings.delivery_backoff_initial_seconds,
                backoff_max_seconds=self.settings.delivery_backoff_max_seconds,
                max_dead_letters=self.settings.max_dead_letters,
            )
            open_letters = self.registry.dead_letters(self.consumer_id, status="open")
            dead_lettered = len(open_letters) > before_dead_letters
            dead_letter_id = open_letters[-1].letter_id if dead_lettered else ""
            if self.health_registry is not None:
                self.health_registry.record_failure(
                    self.health_name,
                    exc,
                    kind=FailureKind.INVALID_EXTERNAL_DATA if dead_lettered else FailureKind.EXPECTED_DEGRADATION,
                    degraded=True,
                    reason="engagement event consumer failed",
                    details={
                        "attempt": batch.delivery_attempt,
                        "cursor": batch.next_cursor,
                        "dead_lettered": dead_lettered,
                    },
                )
            if self.logger is not None:
                try:
                    self.logger.warning(
                        "[engagement-consumer:%s] failed attempt=%s cursor=%s err=%s",
                        self.consumer_id,
                        batch.delivery_attempt,
                        batch.next_cursor,
                        exc,
                    )
                except (AttributeError, TypeError, ValueError):
                    pass
            return ConsumerPollResult(
                consumer_id=self.consumer_id,
                status="dead_lettered" if dead_lettered else "retry",
                event_count=len(batch.events),
                cursor=state.acknowledged_cursor,
                delivery_attempt=batch.delivery_attempt,
                retry_after_seconds=max(0.0, state.next_retry_at - now),
                has_gap=batch.has_gap,
                dead_lettered=dead_lettered,
                error=self.last_error,
                dead_letter_id=dead_letter_id,
                delivery_id=batch.delivery_id,
            )

        outcome, handler_token = self._handler_commit_metadata(handler_result)
        receipt = self.registry.commit_delivery(
            self.consumer_id, batch, now=now, outcome=outcome, handler_token=handler_token
        )
        cursor = receipt.to_cursor
        self.success_count += 1
        self.last_success_at = now
        self.last_error = ""
        if self.health_registry is not None:
            self.health_registry.record_success(
                self.health_name,
                details={
                    "events": len(batch.events), "cursor": cursor, "has_gap": batch.has_gap,
                    "delivery_id": batch.delivery_id, "outcome": receipt.outcome,
                },
            )
        return ConsumerPollResult(
            consumer_id=self.consumer_id,
            status="processed",
            event_count=len(batch.events),
            cursor=cursor,
            delivery_attempt=batch.delivery_attempt,
            has_gap=batch.has_gap,
            delivery_id=batch.delivery_id,
            receipt_committed=True,
        )

    def replay_dead_letter(
        self, letter_id: str, *, now: float | None = None
    ) -> ConsumerPollResult:
        """Replay one captured dead-letter batch without rewinding the live cursor."""

        now = self._clock() if now is None else max(0.0, float(now))
        letter = self.registry.dead_letter(letter_id)
        if letter is None:
            raise KeyError(str(letter_id or ""))
        if not letter.open:
            return ConsumerPollResult(
                consumer_id=self.consumer_id,
                status="already_resolved",
                cursor=letter.to_cursor,
                dead_letter_id=letter.letter_id,
            )
        batch = self.registry.dead_letter_batch(letter.letter_id)
        if not batch.events or batch.has_gap:
            error = "dead-letter payload is incomplete and cannot be replayed safely"
            if self.health_registry is not None:
                self.health_registry.record_failure(
                    self.health_name, error, kind=FailureKind.INVARIANT_VIOLATION,
                    degraded=True, reason="dead-letter replay unavailable",
                    details={"dead_letter_id": letter.letter_id},
                )
            return ConsumerPollResult(
                consumer_id=self.consumer_id, status="unrecoverable",
                cursor=letter.to_cursor, has_gap=batch.has_gap, error=error,
                dead_letter_id=letter.letter_id,
            )
        handler_result, exc = self._invoke_handler(batch)
        if exc is not None:
            self.failure_count += 1
            self.last_failure_at = now
            self.last_error = f"{type(exc).__name__}: {exc}"[:240]
            self.registry.mark_dead_letter_replayed(letter.letter_id, now=now)
            if self.health_registry is not None:
                self.health_registry.record_failure(
                    self.health_name, exc, kind=FailureKind.INVALID_EXTERNAL_DATA,
                    degraded=True, reason="dead-letter replay failed",
                    details={"dead_letter_id": letter.letter_id},
                )
            return ConsumerPollResult(
                consumer_id=self.consumer_id, status="replay_failed",
                event_count=len(batch.events), cursor=letter.to_cursor,
                delivery_attempt=letter.attempts, error=self.last_error,
                dead_letter_id=letter.letter_id,
                delivery_id=batch.delivery_id,
            )
        self.registry.mark_dead_letter_replayed(letter.letter_id, now=now)
        self.registry.resolve_dead_letter(
            letter.letter_id, resolution="operator_replay_succeeded", now=now
        )
        self.success_count += 1
        self.last_success_at = now
        self.last_error = ""
        if self.health_registry is not None:
            self.health_registry.record_success(
                self.health_name,
                details={
                    "dead_letter_id": letter.letter_id,
                    "replayed_events": len(batch.events),
                },
            )
        return ConsumerPollResult(
            consumer_id=self.consumer_id, status="replayed",
            event_count=len(batch.events), cursor=letter.to_cursor,
            delivery_attempt=letter.attempts, dead_letter_id=letter.letter_id,
            delivery_id=batch.delivery_id,
        )

    def snapshot(self) -> dict[str, Any]:
        state = self.registry.consumer_state(self.consumer_id)
        return {
            "schema_version": 2,
            "consumer_id": self.consumer_id,
            "poll_count": self.poll_count,
            "success_count": self.success_count,
            "failure_count": self.failure_count,
            "idle_count": self.idle_count,
            "last_poll_at": self.last_poll_at,
            "last_success_at": self.last_success_at,
            "last_failure_at": self.last_failure_at,
            "last_error": self.last_error,
            "delivery_state": state.to_dict(),
            "dead_letter_count": len(self.registry.dead_letters(self.consumer_id)),
            "open_dead_letter_count": len(
                self.registry.dead_letters(self.consumer_id, status="open")
            ),
            "delivery_receipt_count": len(
                self.registry.delivery_receipts(self.consumer_id)
            ),
        }


__all__ = ["ConsumerPollResult", "EngagementEventConsumer"]
