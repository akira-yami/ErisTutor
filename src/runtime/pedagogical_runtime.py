"""Durable scheduling adapter for the pure pedagogical controller."""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, replace
import math
import threading
import time
from typing import Any, Callable, Iterator, Mapping

from runtime.ai_orchestrated.atomic_state import SQLiteAtomicStateStore, SQLiteStateTransaction
from runtime.pedagogical_contracts import TeachingDirective, TeachingEpisode, TeachingReceipt, normalize_episode
from runtime.runtime_settings_store import runtime_bool, runtime_float, runtime_int
from runtime.pedagogical_controller import (
    ControllerResult,
    EPISODE_SECONDS,
    PedagogicalLimits,
    expire_if_needed,
    note_audio_finished,
    note_public_commit,
    observe_user_turn,
    select_next_action,
)


PEDAGOGY_RUNTIME_SCHEMA = "eris.pedagogy.v1"
DWELL_SECONDS = 1.0
CLAIM_LEASE_SECONDS = 2.0
MAX_ID = 120
_OBSERVABILITY_REASONS = frozenset({
    "audio_finished",
    "awaiting_demonstration",
    "delivery_failed",
    "episode_expired",
    "grounding_rejected",
    "public_commit",
    "surface_failed",
    "tts_failed",
    "user_turn",
})


@dataclass(frozen=True, slots=True)
class PedagogicalRuntimeLimits:
    """Bounded dwell and controller budgets loaded from runtime settings."""

    minimum_dwell_seconds: float = 1.0
    maximum_dwell_seconds: float = 30.0
    max_autonomous_chunks: int = 2
    max_repair_attempts: int = 1
    max_consecutive_stalls: int = 2
    episode_expiry_seconds: float = 600.0

    def __post_init__(self) -> None:
        def bounded(value: Any, default: float, minimum: float, maximum: float) -> float:
            try:
                number = float(value)
            except (TypeError, ValueError, OverflowError):
                number = default
            return max(minimum, min(maximum, number))

        minimum = bounded(self.minimum_dwell_seconds, 1.0, 0.5, 60.0)
        maximum = bounded(self.maximum_dwell_seconds, 30.0, 1.0, 300.0)
        def bounded_int(value: Any, default: int, minimum: int, maximum: int) -> int:
            try:
                number = int(value)
            except (TypeError, ValueError, OverflowError):
                number = default
            return max(minimum, min(maximum, number))

        object.__setattr__(self, "minimum_dwell_seconds", minimum)
        object.__setattr__(self, "maximum_dwell_seconds", max(minimum, maximum))
        object.__setattr__(self, "max_autonomous_chunks", bounded_int(self.max_autonomous_chunks, 2, 0, 8))
        object.__setattr__(self, "max_repair_attempts", bounded_int(self.max_repair_attempts, 1, 0, 5))
        object.__setattr__(self, "max_consecutive_stalls", bounded_int(self.max_consecutive_stalls, 2, 1, 8))
        object.__setattr__(
            self, "episode_expiry_seconds",
            bounded(self.episode_expiry_seconds, 600.0, 60.0, 86400.0),
        )

    @classmethod
    def from_runtime_settings(cls) -> "PedagogicalRuntimeLimits":
        return cls(
            minimum_dwell_seconds=runtime_float("pedagogy", "minimum_dwell_seconds", default=1.0),
            maximum_dwell_seconds=runtime_float("pedagogy", "maximum_dwell_seconds", default=30.0),
            max_autonomous_chunks=runtime_int("pedagogy", "max_autonomous_chunks", default=2),
            max_repair_attempts=runtime_int("pedagogy", "max_repair_attempts", default=1),
            max_consecutive_stalls=runtime_int("pedagogy", "max_consecutive_stalls", default=2),
            episode_expiry_seconds=runtime_float("pedagogy", "episode_expiry_seconds", default=600.0),
        )

    @property
    def dwell_seconds(self) -> float:
        return max(self.minimum_dwell_seconds, min(self.maximum_dwell_seconds, DWELL_SECONDS))

    @property
    def controller_limits(self) -> PedagogicalLimits:
        return PedagogicalLimits(
            max_autonomous_chunks=self.max_autonomous_chunks,
            max_repair_attempts=self.max_repair_attempts,
            max_consecutive_stalls=self.max_consecutive_stalls,
            episode_expiry_seconds=self.episode_expiry_seconds,
        )


def _timestamp(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) and 0.0 <= number <= 10**12 else default


def _identifier(value: Any) -> str:
    return str(value or "").strip()[:MAX_ID]


def _counter(value: Any) -> int:
    try:
        return max(0, min(1_000_000_000, int(value)))
    except (TypeError, ValueError, OverflowError):
        return 0


def _boolean(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"true", "1", "yes"}
    if isinstance(value, (int, float)):
        return value == 1
    return False


def _empty_state() -> dict[str, Any]:
    return {
        "schema": PEDAGOGY_RUNTIME_SCHEMA,
        "version": 0,
        "episode": None,
        "dwell_anchor_at": 0.0,
        "dwell_due_at": 0.0,
        "claim_owner": "",
        "claim_until": 0.0,
        "claim_token": "",
        "pending_receipt": None,
        "committed_turn_id": "",
        "last_commit_autonomous": False,
        "cancel_reason": "",
    }


def _state_from_mapping(payload: Mapping[str, Any] | None) -> dict[str, Any]:
    data = payload if isinstance(payload, Mapping) else {}
    episode_data = data.get("episode")
    episode = normalize_episode(episode_data) if isinstance(episode_data, Mapping) else None
    receipt_data = data.get("pending_receipt")
    pending_receipt = None
    if isinstance(receipt_data, Mapping):
        try:
            candidate = TeachingReceipt(
                episode_id=receipt_data.get("episode_id", ""),
                plan_revision=receipt_data.get("plan_revision", 1),
                chunk_id=receipt_data.get("chunk_id", ""),
                turn_id=receipt_data.get("turn_id", ""),
                text_signature=receipt_data.get("text_signature", ""),
                successful=receipt_data.get("successful", True),
            )
            if candidate.episode_id and candidate.chunk_id and candidate.turn_id:
                pending_receipt = candidate.to_dict()
                pending_receipt["claim_token"] = _identifier(receipt_data.get("claim_token"))
        except (TypeError, ValueError):
            pending_receipt = None
    state = _empty_state()
    state.update(
        version=_counter(data.get("version", 0)),
        episode=episode.to_dict() if episode is not None else None,
        dwell_anchor_at=_timestamp(data.get("dwell_anchor_at")),
        dwell_due_at=_timestamp(data.get("dwell_due_at")),
        claim_owner=_identifier(data.get("claim_owner")),
        claim_until=_timestamp(data.get("claim_until")),
        claim_token=_identifier(data.get("claim_token")),
        pending_receipt=pending_receipt,
        committed_turn_id=_identifier(data.get("committed_turn_id")),
        last_commit_autonomous=_boolean(data.get("last_commit_autonomous", False)),
        cancel_reason=_identifier(data.get("cancel_reason")),
    )
    return state


class PedagogicalRuntime:
    """Owns SQLite persistence, dwell scheduling, and worker claims."""

    def __init__(
        self,
        store: SQLiteAtomicStateStore,
        *,
        state_key: str = "eris.pedagogy",
        clock: Callable[[], float] | None = None,
        timer_factory: Callable[..., Any] | None = None,
        wake_callback: Callable[[str], None] | None = None,
        limits: PedagogicalRuntimeLimits | None = None,
    ) -> None:
        if not isinstance(store, SQLiteAtomicStateStore):
            raise TypeError("store must be a SQLiteAtomicStateStore")
        self._store = store
        self._state_key = _identifier(state_key) or "eris.pedagogy"
        self._clock = clock or time.time
        self._timer_factory = timer_factory or threading.Timer
        self._wake_callback = wake_callback
        self._limits = limits or PedagogicalRuntimeLimits.from_runtime_settings()
        self._lock = threading.RLock()
        self._timer: Any | None = None
        self._atomic_tx: SQLiteStateTransaction | None = None
        record = self._store.ensure(self._state_key, _empty_state())
        self._state = _state_from_mapping(record.payload)
        with self._lock, self._transaction():
            self._settle_locked(self._now())
            self._arm_timer_locked()

    def start_episode(self, episode: TeachingEpisode) -> bool:
        if not isinstance(episode, TeachingEpisode) or not episode.episode_id or not episode.pending_chunk:
            return False
        current = self._now()
        with self._lock, self._transaction():
            self._cancel_timer_locked()
            default_expiry = episode.last_activity_at + EPISODE_SECONDS
            if abs(episode.expires_at - default_expiry) < 0.001:
                episode = replace(
                    episode,
                    expires_at=episode.last_activity_at + self._limits.episode_expiry_seconds,
                )
            self._state = _empty_state()
            self._state["episode"] = episode.to_dict()
            if episode.status == "dwelling":
                self._set_dwell_locked(current)
            self._stage_locked()
            self._arm_timer_locked()
        self._emit("episode_started", episode)
        return True

    def snapshot(self, now: float | None = None) -> dict[str, Any]:
        current = self._now(now)
        with self._lock, self._transaction():
            self._settle_locked(current)
            episode = self._episode_locked()
            claimed = bool(self._state["claim_owner"] and self._state["claim_until"] > current)
            due = bool(
                episode is not None
                and episode.status == "dwelling"
                and episode.pending_chunk is not None
                and not claimed
                and current >= self._state["dwell_due_at"]
            )
            return {
                "schema": PEDAGOGY_RUNTIME_SCHEMA,
                "version": self._state["version"],
                "active_episode": episode.to_dict() if episode is not None else None,
                "pending": bool(episode and episode.pending_chunk),
                "claimed": claimed,
                "delivery_pending": isinstance(self._state.get("pending_receipt"), Mapping),
                "due": due,
                "dwell_anchor_at": self._state["dwell_anchor_at"],
                "dwell_due_at": self._state["dwell_due_at"],
                "seconds_until_due": max(0.0, self._state["dwell_due_at"] - current)
                if episode is not None and episode.status == "dwelling" else 0.0,
            }

    def observe_user_turn(
        self,
        user_text: str,
        *,
        evidence: tuple[str, ...] = (),
        now: float | None = None,
    ) -> ControllerResult:
        current = self._now(now)
        with self._lock, self._transaction():
            self._settle_locked(current)
            episode = self._episode_locked()
            if episode is None:
                failed = TeachingEpisode("", "", "", (), 0.0, 0.0, status="failed")
                return ControllerResult(
                    failed,
                    select_next_action(failed, now=current, limits=self._limits.controller_limits),
                    (),
                )
            self._cancel_timer_locked()
            if episode.status == "dwelling":
                episode = replace(episode, status="observing")
            self._clear_dwell_locked(reason="user_turn")
            result = observe_user_turn(
                episode,
                user_text,
                evidence=tuple(evidence),
                now=current,
                limits=self._limits.controller_limits,
            )
            self._set_episode_locked(result.episode)
            self._stage_locked()
            self._arm_timer_locked()
            self._emit(
                "user_observed",
                result.episode,
                action=result.action.kind,
                reason=result.action.reason,
            )
            return result

    def claim_due(
        self,
        *,
        worker_id: str,
        now: float | None = None,
        lease_seconds: float = CLAIM_LEASE_SECONDS,
    ) -> TeachingDirective | None:
        current = self._now(now)
        owner = _identifier(worker_id) or "anonymous"
        try:
            lease = max(0.25, min(120.0, float(lease_seconds)))
        except (TypeError, ValueError, OverflowError):
            lease = CLAIM_LEASE_SECONDS
        with self._lock, self._transaction():
            self._settle_locked(current)
            episode = self._episode_locked()
            if (
                episode is None
                or episode.status != "dwelling"
                or episode.pending_chunk is None
                or current < self._state["dwell_due_at"]
                or self._state["claim_until"] > current
            ):
                self._arm_timer_locked()
                return None
            action = select_next_action(episode, now=current, limits=self._limits.controller_limits)
            directive = action.directive
            if directive is None:
                return None
            self._state["claim_owner"] = owner
            self._state["claim_until"] = current + lease
            self._state["claim_token"] = _identifier(
                f"{episode.episode_id}:{episode.plan_revision}:{episode.pending_chunk.chunk_id}:{owner}:{int(current * 1000)}"
            )
            directive = replace(directive, claim_token=self._state["claim_token"])
            self._stage_locked()
            self._arm_timer_locked()
            self._emit("directive_claimed", episode, action=action.kind, reason=action.reason)
            return directive

    def commit_receipt(self, receipt: TeachingReceipt, *, now: float | None = None, claim_token: str = "") -> bool:
        if not isinstance(receipt, TeachingReceipt) or not receipt.successful:
            return False
        current = self._now(now)
        ledger_key = "pedagogy:" + ":".join((receipt.episode_id, str(receipt.plan_revision), receipt.chunk_id))
        claim = self._store.claim_idempotency(
            ledger_key, owner="pedagogy_runtime", lease_seconds=CLAIM_LEASE_SECONDS, now=current,
        )
        if claim.state != "acquired":
            return False
        committed = False
        try:
            with self._lock, self._transaction():
                self._settle_locked(current)
                episode = self._episode_locked()
                if episode is None:
                    return False
                receipt_episode = episode
                if episode.status == "dwelling":
                    # A dwell lease proves this exact chunk was selected for a
                    # public attempt. Convert only this transient projection to
                    # the controller's receipt-valid state; the stored episode
                    # remains dwelling until a successful receipt consumes it.
                    if (not self._state["claim_owner"] or self._state["claim_until"] <= current
                            or _identifier(claim_token) != self._state["claim_token"]):
                        return False
                    receipt_episode = replace(episode, status="ready")
                autonomous_commit = episode.status == "dwelling"
                updated = note_public_commit(
                    receipt_episode,
                    receipt,
                    autonomous=autonomous_commit,
                )
                if updated == receipt_episode:
                    return False
                self._set_episode_locked(updated)
                self._clear_dwell_locked(reason="public_commit")
                self._state["committed_turn_id"] = _identifier(receipt.turn_id)
                self._state["last_commit_autonomous"] = autonomous_commit
                self._stage_locked()
                self._arm_timer_locked()
                committed = True
                self._emit("receipt_committed", updated, action="commit", reason="public_commit")
            return True
        finally:
            if committed:
                self._store.complete_idempotency(
                    ledger_key, owner="pedagogy_runtime", result=True, now=current,
                )
            else:
                self._store.abandon_idempotency(ledger_key, owner="pedagogy_runtime")

    def release_claim(
        self,
        reason: str,
        *,
        now: float | None = None,
        delay_seconds: float = 0.0,
        claim_token: str = "",
    ) -> bool:
        """Release a live autonomous lease without consuming its teaching chunk."""
        current = self._now(now)
        try:
            delay = max(0.0, min(300.0, float(delay_seconds)))
        except (TypeError, ValueError, OverflowError):
            delay = 0.0
        with self._lock, self._transaction():
            self._settle_locked(current)
            episode = self._episode_locked()
            if (
                episode is None
                or episode.status != "dwelling"
                or episode.pending_chunk is None
                or not self._state["claim_owner"]
                or _identifier(claim_token) != self._state["claim_token"]
            ):
                return False
            self._state["claim_owner"] = ""
            self._state["claim_until"] = 0.0
            self._state["claim_token"] = ""
            self._state["pending_receipt"] = None
            self._state["dwell_due_at"] = max(self._state["dwell_due_at"], current + delay)
            self._state["cancel_reason"] = _identifier(reason)
            self._stage_locked()
            self._arm_timer_locked()
            self._emit("claim_released", episode, action="release", reason=reason)
            return True

    def stage_receipt(self, receipt: TeachingReceipt, *, now: float | None = None, claim_token: str = "") -> bool:
        """Durably bind a public attempt to its live claim until TTS settles."""
        if not isinstance(receipt, TeachingReceipt) or not receipt.successful or not receipt.turn_id:
            return False
        current = self._now(now)
        with self._lock, self._transaction():
            self._settle_locked(current)
            episode = self._episode_locked()
            chunk = episode.pending_chunk if episode is not None else None
            if (
                episode is None
                or episode.status != "dwelling"
                or chunk is None
                or receipt.episode_id != episode.episode_id
                or receipt.plan_revision != episode.plan_revision
                or receipt.chunk_id != chunk.chunk_id
                or not self._state["claim_owner"]
                or self._state["claim_until"] <= current
                or _identifier(claim_token) != self._state["claim_token"]
            ):
                return False
            existing = self._state.get("pending_receipt")
            if isinstance(existing, Mapping):
                return {name: existing.get(name) for name in receipt.__dataclass_fields__} == receipt.to_dict()
            self._state["pending_receipt"] = receipt.to_dict()
            self._state["pending_receipt"]["claim_token"] = _identifier(claim_token)
            self._state["claim_until"] = max(self._state["claim_until"], current + 120.0)
            self._stage_locked()
            self._arm_timer_locked()
            self._emit("receipt_staged", episode, action="stage", reason="delivery_pending")
            return True

    def settle_delivery(
        self,
        *,
        turn_id: str,
        successful: bool,
        now: float | None = None,
    ) -> bool:
        """Settle one staged public attempt from the authoritative TTS terminal."""
        current = self._now(now)
        normalized_turn_id = _identifier(turn_id)
        receipt: TeachingReceipt | None = None
        with self._lock, self._transaction():
            self._settle_locked(current)
            episode = self._episode_locked()
            raw = self._state.get("pending_receipt")
            if not isinstance(raw, Mapping):
                return False
            staged_claim_token = _identifier(raw.get("claim_token"))
            try:
                receipt = TeachingReceipt(
                    episode_id=raw.get("episode_id", ""),
                    plan_revision=raw.get("plan_revision", 1),
                    chunk_id=raw.get("chunk_id", ""),
                    turn_id=raw.get("turn_id", ""),
                    text_signature=raw.get("text_signature", ""),
                    successful=raw.get("successful", True),
                )
            except (TypeError, ValueError):
                receipt = None
            if receipt is None or receipt.turn_id != normalized_turn_id:
                return False
            if not successful:
                self._state["pending_receipt"] = None
                self._state["claim_owner"] = ""
                self._state["claim_until"] = 0.0
                self._state["claim_token"] = ""
                self._state["dwell_due_at"] = max(self._state["dwell_due_at"], current)
                self._state["cancel_reason"] = "delivery_failed"
                self._stage_locked()
                self._arm_timer_locked()
                self._emit("delivery_failed", episode, action="release", reason="delivery_failed")
                return True

        if receipt is None or not self.commit_receipt(receipt, now=current, claim_token=staged_claim_token):
            return False
        self.notify_audio_finished(current, source_turn_id=receipt.turn_id)
        return True

    def notify_audio_finished(self, timestamp: float, *, source_turn_id: str = "") -> None:
        current = self._now(timestamp)
        source_id = _identifier(source_turn_id)
        with self._lock, self._transaction():
            self._settle_locked(current)
            episode = self._episode_locked()
            if episode is None or (source_id and source_id != self._state["committed_turn_id"]):
                return
            updated = note_audio_finished(
                episode,
                now=current,
                limits=self._limits.controller_limits,
            )
            if updated != episode:
                if (
                    episode.status == "speaking"
                    and updated.status == "waiting_user"
                    and episode.pending_chunk is not None
                    and not bool(self._state.get("last_commit_autonomous", False))
                ):
                    updated = replace(updated, status="dwelling")
                self._set_episode_locked(updated)
                if updated.status == "dwelling":
                    self._set_dwell_locked(current)
                    self._state["last_commit_autonomous"] = False
                else:
                    self._clear_dwell_locked(reason="audio_finished")
                self._stage_locked()
                self._arm_timer_locked()
                self._emit("audio_finished", updated, action="wait", reason="audio_finished")

    def cancel_dwell(self, reason: str, *, now: float | None = None) -> None:
        current = self._now(now)
        with self._lock, self._transaction():
            self._settle_locked(current)
            episode = self._episode_locked()
            self._cancel_timer_locked()
            updated = episode
            if episode is not None and episode.status == "dwelling":
                updated = replace(episode, status="waiting_user")
                self._set_episode_locked(updated)
            self._clear_dwell_locked(reason=reason)
            self._stage_locked()
            self._emit("dwell_cancelled", updated, action="cancel", reason=reason)

    def shutdown(self) -> None:
        with self._lock:
            self._cancel_timer_locked()

    @contextmanager
    def _transaction(self) -> Iterator[None]:
        with self._store.transaction(self._state_key, self._state) as tx:
            self._atomic_tx = tx
            self._state = _state_from_mapping(tx.payload)
            try:
                yield
            finally:
                self._atomic_tx = None

    def _now(self, value: float | None = None) -> float:
        return _timestamp(self._clock() if value is None else value)

    def _episode_locked(self) -> TeachingEpisode | None:
        payload = self._state.get("episode")
        return normalize_episode(payload) if isinstance(payload, Mapping) else None

    def _set_episode_locked(self, episode: TeachingEpisode) -> None:
        self._state["episode"] = episode.to_dict()

    def _set_dwell_locked(self, anchor: float) -> None:
        anchor = _timestamp(anchor)
        self._state["dwell_anchor_at"] = anchor
        self._state["dwell_due_at"] = anchor + self._limits.dwell_seconds
        self._state["claim_owner"] = ""
        self._state["claim_until"] = 0.0
        self._state["claim_token"] = ""
        self._state["cancel_reason"] = ""

    def _clear_dwell_locked(self, *, reason: str) -> None:
        self._state["dwell_anchor_at"] = 0.0
        self._state["dwell_due_at"] = 0.0
        self._state["claim_owner"] = ""
        self._state["claim_until"] = 0.0
        self._state["claim_token"] = ""
        self._state["pending_receipt"] = None
        self._state["cancel_reason"] = _identifier(reason)

    def _settle_locked(self, now: float) -> None:
        episode = self._episode_locked()
        changed = False
        if episode is not None:
            updated = expire_if_needed(episode, now=now, limits=self._limits.controller_limits)
            if updated != episode:
                self._set_episode_locked(updated)
                self._clear_dwell_locked(reason="episode_expired")
                changed = True
                self._emit("episode_expired", updated, action="expire", reason="episode_expired")
        if self._state["claim_until"] and self._state["claim_until"] <= now:
            self._state["claim_owner"] = ""
            self._state["claim_until"] = 0.0
            self._state["claim_token"] = ""
            self._state["pending_receipt"] = None
            changed = True
        if changed:
            self._stage_locked()

    def _stage_locked(self) -> None:
        self._state["schema"] = PEDAGOGY_RUNTIME_SCHEMA
        self._state["version"] = min(1_000_000_000, int(self._state["version"]) + 1)
        if self._atomic_tx is not None:
            self._atomic_tx.stage(self._state)

    def _arm_timer_locked(self) -> None:
        self._cancel_timer_locked()
        if self._wake_callback is None:
            return
        episode = self._episode_locked()
        if episode is None or episode.status != "dwelling" or episode.pending_chunk is None:
            return
        now = self._now()
        target = self._state["claim_until"] if self._state["claim_until"] > now else self._state["dwell_due_at"]
        if target <= 0.0:
            return

        def wake() -> None:
            try:
                self._wake_callback("pedagogical_dwell_due")
            except TypeError:
                self._wake_callback(reason="pedagogical_dwell_due")
            except Exception:
                pass

        try:
            timer = self._timer_factory(max(0.05, target - now), wake)
            timer.daemon = True
            timer.start()
            self._timer = timer
        except Exception:
            self._timer = None

    def _cancel_timer_locked(self) -> None:
        timer, self._timer = self._timer, None
        cancel = getattr(timer, "cancel", None)
        if callable(cancel):
            try:
                cancel()
            except Exception:
                pass

    def _emit(
        self,
        event: str,
        episode: TeachingEpisode | None = None,
        *,
        action: str = "",
        reason: str = "",
    ) -> None:
        """Best-effort observability projection with no public or user text."""
        try:
            current = episode or self._episode_locked()
            chunk = current.pending_chunk if current is not None else None
            status = current.status if current is not None else ""
            canonical_reason = _identifier(reason).lower()
            if canonical_reason not in _OBSERVABILITY_REASONS:
                canonical_reason = "delivery_failed" if event == "delivery_failed" else "external_cancel"
            fields = {
                "episode_id": _identifier(current.episode_id if current is not None else ""),
                "plan_revision": int(current.plan_revision) if current is not None else 0,
                "chunk_id": _identifier(chunk.chunk_id if chunk is not None else ""),
                "action": _identifier(action),
                "reason": canonical_reason,
                "status": _identifier(status),
            }
            from runtime.observability import GLOBAL_OBSERVABILITY

            tags = {"event": _identifier(event), "status": fields["status"]}
            GLOBAL_OBSERVABILITY.log(f"pedagogy.{event}", tags=tags, fields=fields, status="ok")
            GLOBAL_OBSERVABILITY.increment(
                "eris_pedagogy_lifecycle_events_total", tags=tags, fields=fields,
            )
            GLOBAL_OBSERVABILITY.gauge(
                "eris_pedagogy_pending",
                1.0 if current is not None and current.pending_chunk is not None else 0.0,
            )
        except Exception:
            pass


def _host_feature_enabled(host: Any) -> bool:
    explicit = getattr(host, "pedagogical_controller_enabled", None)
    if explicit is not None:
        return bool(explicit)
    settings = getattr(getattr(host, "runtime_policy_settings", None), "pedagogy", None)
    if settings is not None and hasattr(settings, "enabled"):
        return bool(settings.enabled)
    return runtime_bool("pedagogy", "enabled", default=False)


def ensure_pedagogical_runtime(host: Any) -> PedagogicalRuntime | None:
    """Attach one runtime to an enabled host without creating an extra store."""
    if host is None or not _host_feature_enabled(host):
        return None
    for name in ("ai_pedagogical_runtime", "pedagogical_runtime"):
        existing = getattr(host, name, None)
        if isinstance(existing, PedagogicalRuntime):
            return existing
    store = next(
        (
            getattr(host, name, None)
            for name in ("ai_social_atomic_store", "pedagogical_atomic_store", "atomic_store")
            if isinstance(getattr(host, name, None), SQLiteAtomicStateStore)
        ),
        None,
    )
    if store is None:
        return None
    runtime = PedagogicalRuntime(
        store,
        clock=getattr(host, "_runtime_clock", None) if callable(getattr(host, "_runtime_clock", None)) else None,
        wake_callback=getattr(host, "_wake_autonomous_loop", None),
    )
    try:
        setattr(host, "ai_pedagogical_runtime", runtime)
        setattr(host, "pedagogical_runtime", runtime)
    except Exception:
        pass
    return runtime


__all__ = [
    "PEDAGOGY_RUNTIME_SCHEMA",
    "PedagogicalRuntime",
    "PedagogicalRuntimeLimits",
    "ensure_pedagogical_runtime",
]
