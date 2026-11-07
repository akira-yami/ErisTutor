# -*- coding: utf-8 -*-
"""Central admission control for every remote inference lane.

PDF annotation
--------------
This module implements the control-plane described in pages 6-11 of the
attached architecture report: one broker owns priority, bounded queues,
deadlines, coalescing, quota reservation and cancellation scopes.

The broker is deliberately transport-agnostic. Callers build an
:class:`InferenceRequest`, then execute the provider call through ``run``.
Requests that never reach ``admitted`` consume no remote quota.
"""

from __future__ import annotations

import asyncio
import atexit
import inspect
import math
import os
import re
import threading
import time
import uuid
from collections import defaultdict, deque
from contextlib import contextmanager
from email.utils import parsedate_to_datetime
from contextvars import ContextVar, Token
from dataclasses import asdict, dataclass, field, replace
from typing import Any, Callable, Iterator, Mapping

from runtime.pdf_rollout import broker_rollout_for_request
from runtime.broker_state_store import BrokerStateStore


@dataclass(frozen=True, slots=True)
class LanePolicy:
    priority: int
    optional: bool
    max_queued: int
    deadline_seconds: float
    max_concurrency: int


# PDF page 7: low number = high priority. Optional lanes are shed first.
LANE_POLICIES: dict[str, LanePolicy] = {
    "chat_main": LanePolicy(10, False, 8, 35.0, 2),
    "chat": LanePolicy(10, False, 8, 35.0, 2),
    "chat_tool_required": LanePolicy(20, False, 6, 25.0, 2),
    # Character HOTFIX 02: verifier nằm trong đường phát lời bắt buộc.
    # DS2API có thể sinh reasoning dài trước JSON cuối; deadline của broker
    # không được giết request còn làm việc. 0 = không áp deadline theo đồng hồ
    # (cơ chế cancel scope/preempt vẫn do broker quản lý). Có thể đặt giới hạn
    # bằng ERIS_BROKER_DEADLINE_SEC_PUBLIC_CLAIM_VERIFIER nếu cần vận hành.
    "public_claim_verifier": LanePolicy(20, False, 6, 0.0, 2),
    "directive": LanePolicy(20, False, 6, 20.0, 2),
    "tts_current_turn": LanePolicy(30, False, 3, 18.0, 1),
    "tts": LanePolicy(30, True, 3, 18.0, 1),
    "speech_localizer": LanePolicy(35, True, 2, 10.0, 1),
    "tts_localizer": LanePolicy(35, True, 2, 10.0, 1),
    "romaji_localizer": LanePolicy(35, True, 2, 10.0, 1),
    # Direct-chat slow Neuro runs in parallel after the deterministic fast commit.
    # It is lower priority than chat/TTS, cancellable by its own conversation scope,
    # and may never become authority over the already-served public turn.
    "neuro_continuation": LanePolicy(40, True, 2, 0.0, 1),
    # Tool/capability planning uses stable external capability identities, but the
    # planner itself is one-shot and may not own conversational session state.
    "capability_deliberation_required": LanePolicy(26, False, 4, 22.0, 1),
    "post_observer": LanePolicy(60, True, 2, 30.0, 1),
    # Living-user extraction has its own DeepSeek curator credential pool. It is
    # background with respect to the public writer, but it is not an expendable
    # optional request: the dedicated credential means it may run concurrently
    # without consuming the chat lane's concurrency/quota domain.
    "memory_curator": LanePolicy(65, False, 4, 12.0, 1),
    "memory_digest": LanePolicy(70, True, 2, 25.0, 1),
    "private_mind": LanePolicy(80, True, 1, 20.0, 1),
    "private_mind_optional": LanePolicy(80, True, 1, 20.0, 1),
    "work_background": LanePolicy(65, True, 2, 45.0, 1),
    "tutor_background": LanePolicy(85, True, 2, 20.0, 1),
    "micro_presence": LanePolicy(90, True, 1, 8.0, 1),
    # Rare conflict-resolution call from the neuro action gate. It is optional
    # like autonomous initiative, but deliberately has its own one-shot provider
    # lane so stale conversational state cannot become decision authority.
    "neuro_deliberation": LanePolicy(88, True, 1, 18.0, 1),
    "capability_deliberation": LanePolicy(89, True, 1, 18.0, 1),
    "autonomous": LanePolicy(90, True, 1, 18.0, 1),
    "vision_ambient": LanePolicy(95, True, 1, 18.0, 1),
    "vision_compressor": LanePolicy(95, True, 1, 18.0, 1),
    "transport": LanePolicy(50, False, 8, 30.0, 2),
}
_DEFAULT_POLICY = LanePolicy(50, False, 8, 30.0, 2)

# PDF annotation, pages 7-9 and 20: required chat lanes may shrink their
# prompt/output reservation before waiting for a full quota window. Optional
# lanes are shed instead. A transport must explicitly advertise that it can
# apply the smaller budget; otherwise the broker never lies about token use.
_REQUIRED_DEGRADE_LANES = frozenset({
    "chat_main", "chat", "chat_tool_required", "directive",
    "capability_deliberation_required", "transport",
})


@dataclass(slots=True)
class InferenceRequest:
    request_id: str
    lane: str
    provider: str
    project: str | None
    credential_pool: str | None
    model: str
    priority: int
    optional: bool
    deadline_at: float
    cancel_scope: str
    estimated_input_tokens: int
    reserved_output_tokens: int
    max_concurrency_cost: int = 1
    idempotency_key: str | None = None
    coalesce_key: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class AdmissionDecision:
    admitted: bool
    state: str
    reason: str
    queue_position: int | None
    reserved_input_tokens: int
    reserved_output_tokens: int
    effective_deadline_at: float


@dataclass(slots=True)
class _QueuedRequest:
    request: InferenceRequest
    sequence: int
    enqueued_at: float
    cancelled_reason: str = ""


@dataclass(slots=True)
class _Inflight:
    request: InferenceRequest
    started_at: float
    cancel_event: threading.Event = field(default_factory=threading.Event)
    provider_started: bool = False


class InferenceRejected(RuntimeError):
    """Raised when admission intentionally rejects a request before remote I/O."""

    def __init__(self, reason: str, request: InferenceRequest | None = None) -> None:
        super().__init__(f"inference_rejected:{reason}")
        self.reason = str(reason or "rejected")
        self.request = request


@dataclass(frozen=True, slots=True)
class CancellationToken:
    """Cooperative cancellation/deadline view passed down to provider adapters.

    PDF annotation, pages 2, 5, 7 and 10: cancelling a ticket must propagate
    into the work spawned by that ticket.  Python cannot always kill an HTTP
    request already inside a third-party SDK, but adapters can still cap their
    timeout, stop retry/fallback chains and discard a late result immediately.
    """

    event: threading.Event
    deadline_at: float

    @property
    def cancelled(self) -> bool:
        return self.event.is_set() or (
            math.isfinite(self.deadline_at) and time.time() >= self.deadline_at
        )

    def remaining_seconds(self, *, floor: float = 0.05) -> float | None:
        if not math.isfinite(self.deadline_at):
            return None
        return max(float(floor), self.deadline_at - time.time())

    def raise_if_cancelled(self, request: InferenceRequest | None = None) -> None:
        if self.event.is_set():
            raise InferenceRejected("cancelled", request)
        if math.isfinite(self.deadline_at) and time.time() >= self.deadline_at:
            raise InferenceRejected("deadline_exceeded", request)


@dataclass(frozen=True, slots=True)
class InferenceContext:
    lane: str = "transport"
    optional: bool | None = None
    priority: int | None = None
    deadline_at: float | None = None
    cancel_scope: str = ""
    coalesce_key: str = ""
    idempotency_key: str = ""
    project: str = ""
    credential_pool: str = ""
    metadata: Mapping[str, Any] = field(default_factory=dict)


_CONTEXT: ContextVar[InferenceContext] = ContextVar("eris_inference_context", default=InferenceContext())


def _env_lane_key(lane: str) -> str:
    return str(lane or "transport").strip().upper().replace("-", "_") or "TRANSPORT"


def _env_int(name: str, default: int, *, minimum: int) -> int:
    raw = str(os.getenv(name, "") or "").strip()
    if not raw:
        return default
    try:
        return max(minimum, int(raw))
    except ValueError:
        return default


def _env_float(name: str, default: float, *, minimum: float) -> float:
    raw = str(os.getenv(name, "") or "").strip()
    if not raw:
        return default
    try:
        return max(minimum, float(raw))
    except ValueError:
        return default


def lane_policy(lane: str) -> LanePolicy:
    """Return the effective per-lane SLO policy.

    PDF annotation, page 20: P2 calls for per-lane SLOs rather than one global
    timeout and queue size.  Static defaults remain the source-controlled
    contract, while explicit env overrides make canary tuning possible without
    teaching every caller about queue internals.
    """

    key = str(lane or "transport").strip().lower() or "transport"
    base = LANE_POLICIES.get(key, _DEFAULT_POLICY)
    env_key = _env_lane_key(key)
    return LanePolicy(
        priority=_env_int(f"ERIS_BROKER_PRIORITY_{env_key}", base.priority, minimum=0),
        optional=base.optional,
        max_queued=_env_int(f"ERIS_BROKER_MAX_QUEUED_{env_key}", base.max_queued, minimum=0),
        deadline_seconds=_env_float(
            f"ERIS_BROKER_DEADLINE_SEC_{env_key}", base.deadline_seconds, minimum=0.0
        ),
        max_concurrency=_env_int(
            f"ERIS_BROKER_LANE_CONCURRENCY_{env_key}", base.max_concurrency, minimum=1
        ),
    )


def current_inference_context() -> InferenceContext:
    return _CONTEXT.get()


@contextmanager
def inference_context(
    lane: str,
    *,
    optional: bool | None = None,
    priority: int | None = None,
    deadline_at: float | None = None,
    timeout_seconds: float | None = None,
    cancel_scope: str = "",
    coalesce_key: str = "",
    idempotency_key: str = "",
    project: str = "",
    credential_pool: str = "",
    metadata: Mapping[str, Any] | None = None,
) -> Iterator[InferenceContext]:
    """Attach lane semantics to all provider calls in the current execution context."""

    parent = current_inference_context()
    policy = lane_policy(lane)
    actual_deadline = deadline_at
    if actual_deadline is None and timeout_seconds is not None:
        requested_timeout = float(timeout_seconds)
        actual_deadline = (
            math.inf
            if requested_timeout <= 0.0
            else time.time() + max(0.05, requested_timeout)
        )
    if actual_deadline is None:
        actual_deadline = parent.deadline_at
    elif parent.deadline_at is not None and not (
        # HOTFIX 02: verifier public là một continuation bắt buộc của lượt
        # hiện tại, có thể đang chờ raw reasoning dài từ DS2API. Cho phép duy
        # nhất lane này xin deadline=inf. Quyền hủy vẫn kế thừa từ parent qua
        # cancel_scope, không cấp quyền cho post_observer/autonomous.
        str(lane).strip().lower() == 'public_claim_verifier'
        and actual_deadline == math.inf
    ):
        # V96: deadline con không được "reset đồng hồ" của user turn cha.
        # Trước đây fallback có thể xin thêm 90s rồi sống lâu hơn foreground,
        # tạo ra lượt chat treo hàng phút dù writer ban đầu chỉ xin vài chục giây.
        try:
            actual_deadline = min(float(actual_deadline), float(parent.deadline_at))
        except (TypeError, ValueError):
            actual_deadline = parent.deadline_at
    merged_metadata = dict(parent.metadata)
    merged_metadata.update(dict(metadata or {}))
    context = InferenceContext(
        lane=str(lane or parent.lane or "transport").strip().lower() or "transport",
        optional=policy.optional if optional is None else bool(optional),
        priority=policy.priority if priority is None else int(priority),
        deadline_at=actual_deadline,
        cancel_scope=str(cancel_scope or parent.cancel_scope or ""),
        coalesce_key=str(coalesce_key or parent.coalesce_key or ""),
        idempotency_key=str(idempotency_key or parent.idempotency_key or ""),
        project=str(project or parent.project or ""),
        credential_pool=str(credential_pool or parent.credential_pool or ""),
        metadata=merged_metadata,
    )
    token: Token[InferenceContext] = _CONTEXT.set(context)
    try:
        yield context
    finally:
        _CONTEXT.reset(token)


class InferenceBroker:
    """Single owner of remote admission, reservations and provider heat state."""

    def __init__(self) -> None:
        self.enabled = str(os.getenv("ERIS_INFERENCE_BROKER_ENABLED", "1") or "1").strip().lower() not in {
            "0", "false", "off", "no",
        }
        self._condition = threading.Condition(threading.RLock())
        self._queue: list[_QueuedRequest] = []
        self._inflight: dict[str, _Inflight] = {}
        # PDF annotation, pages 18-20: shadow/rollback calls are deliberately
        # outside quota enforcement, but still receive cancellation scopes and
        # remain visible in inspection telemetry.
        self._unenforced_inflight: dict[str, tuple[InferenceRequest, threading.Event, str]] = {}
        self._sequence = 0
        self._user_turn_scopes: set[str] = set()
        self._cancelled_scopes: dict[str, float] = {}
        self._request_events: dict[str, deque[tuple[float, str]]] = defaultdict(deque)
        self._token_events: dict[str, deque[tuple[float, str, int]]] = defaultdict(deque)
        self._input_token_events: dict[str, deque[tuple[float, str, int]]] = defaultdict(deque)
        self._output_token_events: dict[str, deque[tuple[float, str, int]]] = defaultdict(deque)
        # PDF annotation, pages 6, 8 and 19: organization/provider quota is the
        # outer safety boundary, while configured project budgets create an
        # internal buffer before the organization limit is touched.
        self._project_request_events: dict[tuple[str, str], deque[tuple[float, str]]] = defaultdict(deque)
        self._project_token_events: dict[tuple[str, str], deque[tuple[float, str, int]]] = defaultdict(deque)
        self._project_input_token_events: dict[tuple[str, str], deque[tuple[float, str, int]]] = defaultdict(deque)
        self._project_output_token_events: dict[tuple[str, str], deque[tuple[float, str, int]]] = defaultdict(deque)
        self._cooldown_until: dict[str, float] = defaultdict(float)
        self._dynamic_rpm: dict[str, int] = {}
        self._dynamic_tpm: dict[str, int] = {}
        self._dynamic_itpm: dict[str, int] = {}
        self._dynamic_otpm: dict[str, int] = {}
        # PDF annotation, pages 2 and 15-16: provider remaining/reset headers
        # are dynamic source-of-truth signals, not decorative log fields.
        self._header_remaining: dict[str, dict[str, int]] = defaultdict(dict)
        self._header_reset_at: dict[str, dict[str, float]] = defaultdict(dict)
        self._active_by_key: dict[tuple[str, str, str, str], int] = defaultdict(int)
        self._user_turn_remote_calls: dict[str, int] = defaultdict(int)
        self._user_turn_started_at: dict[str, float] = {}
        self._user_turn_category: dict[str, str] = {}
        # PDF annotation, page 6: ``idempotency_key`` is part of the proposed
        # request contract.  V6 keeps actual admission semantics instead of
        # leaving a distinguished-looking field to gather dust.
        self._idempotency_active: dict[str, str] = {}
        self._idempotency_tombstones: dict[str, float] = {}
        self._state_store = self._build_state_store()
        if self._state_store is not None:
            self._restore_durable_state()
            atexit.register(self.flush_durable_state)

    # ------------------------------------------------------------------
    # Durable control-plane state
    # ------------------------------------------------------------------
    @staticmethod
    def _build_state_store() -> BrokerStateStore | None:
        """Create an opt-in durable store without persisting user content.

        ``ERIS_BROKER_DURABLE_STATE=1`` enables the default path
        ``logs/inference_broker_state.json``.  ``ERIS_BROKER_STATE_PATH`` can
        point elsewhere and enables persistence implicitly.  Keeping this
        explicit avoids unit-test instances sharing quota ghosts from a prior
        run, a form of haunting that software engineers rarely budget for.
        """

        path = str(os.getenv("ERIS_BROKER_STATE_PATH", "") or "").strip()
        enabled = str(os.getenv("ERIS_BROKER_DURABLE_STATE", "0") or "0").strip().lower()
        if not path and enabled not in {"1", "true", "yes", "on"}:
            return None
        if not path:
            path = os.path.join("logs", "inference_broker_state.json")
        raw_interval = str(os.getenv("ERIS_BROKER_STATE_WRITE_INTERVAL_SEC", "0.5") or "0.5")
        try:
            interval = max(0.0, float(raw_interval))
        except ValueError:
            interval = 0.5
        return BrokerStateStore(path, min_write_interval_s=interval)

    def _restore_durable_state(self) -> None:
        if self._state_store is None:
            return
        snapshot = self._state_store.load()
        with self._condition:
            for provider, until in snapshot.cooldown_until.items():
                self._cooldown_until[provider] = max(self._cooldown_until[provider], until)
            for provider, limits in snapshot.dynamic_limits.items():
                if limits.get("rpm"):
                    self._dynamic_rpm[provider] = int(limits["rpm"])
                if limits.get("tpm"):
                    self._dynamic_tpm[provider] = int(limits["tpm"])
                if limits.get("itpm"):
                    self._dynamic_itpm[provider] = int(limits["itpm"])
                if limits.get("otpm"):
                    self._dynamic_otpm[provider] = int(limits["otpm"])
            for provider, values in snapshot.header_remaining.items():
                self._header_remaining[provider].update(values)
            for provider, values in snapshot.header_reset_at.items():
                self._header_reset_at[provider].update(values)
            self._idempotency_tombstones.update(snapshot.idempotency_tombstones)

    def _durable_payload_locked(self) -> dict[str, Any]:
        now = time.time()
        self._prune_idempotency_locked(now)
        providers = set(self._dynamic_rpm) | set(self._dynamic_tpm) | set(self._dynamic_itpm) | set(self._dynamic_otpm)
        limits: dict[str, dict[str, int]] = {}
        for provider in providers:
            values = {
                "rpm": int(self._dynamic_rpm.get(provider, 0)),
                "tpm": int(self._dynamic_tpm.get(provider, 0)),
                "itpm": int(self._dynamic_itpm.get(provider, 0)),
                "otpm": int(self._dynamic_otpm.get(provider, 0)),
            }
            clean = {kind: value for kind, value in values.items() if value > 0}
            if clean:
                limits[provider] = clean
        cooldown = {
            provider: float(until)
            for provider, until in self._cooldown_until.items()
            if float(until) > now
        }
        resets: dict[str, dict[str, float]] = {}
        remaining: dict[str, dict[str, int]] = {}
        for provider, values in self._header_reset_at.items():
            clean_resets = {kind: float(value) for kind, value in values.items() if float(value) > now}
            if clean_resets:
                resets[provider] = clean_resets
                clean_remaining = {
                    kind: int(value)
                    for kind, value in self._header_remaining[provider].items()
                    if kind in clean_resets and int(value) >= 0
                }
                if clean_remaining:
                    remaining[provider] = clean_remaining
        return {
            "cooldown_until": cooldown,
            "dynamic_limits": limits,
            "header_remaining": remaining,
            "header_reset_at": resets,
            "idempotency_tombstones": dict(self._idempotency_tombstones),
        }

    def _persist_durable_state_locked(self, *, force: bool = False) -> None:
        if self._state_store is None:
            return
        try:
            self._state_store.save(self._durable_payload_locked(), force=force)
        except OSError:
            self._metric("broker_state_persist_error_total", reason="io_error")

    def flush_durable_state(self) -> None:
        """Force the latest cooldown/header/idempotency checkpoint to disk."""

        if self._state_store is None:
            return
        with self._condition:
            self._persist_durable_state_locked(force=True)
        try:
            self._state_store.flush()
        except OSError:
            self._metric("broker_state_persist_error_total", reason="flush_error")

    # ------------------------------------------------------------------
    # Context / lifecycle
    # ------------------------------------------------------------------
    @contextmanager
    def user_turn(self, scope: str | None = None) -> Iterator[str]:
        actual = str(scope or f"user_turn:{uuid.uuid4().hex[:12]}")
        self.begin_user_turn(actual)
        token = _CONTEXT.set(
            InferenceContext(
                lane="chat_main",
                optional=False,
                priority=lane_policy("chat_main").priority,
                deadline_at=None,
                cancel_scope=actual,
                metadata={"user_turn": True},
            )
        )
        try:
            yield actual
        finally:
            _CONTEXT.reset(token)
            self.end_user_turn(actual)

    def begin_user_turn(self, scope: str) -> None:
        with self._condition:
            actual_scope = str(scope)
            self._user_turn_scopes.add(actual_scope)
            self._user_turn_remote_calls[actual_scope] = 0
            self._user_turn_started_at[actual_scope] = time.time()
            self._user_turn_category[actual_scope] = "unknown"
            # PDF page 7: a new chat turn preempts queued optional work.
            for queued in self._queue:
                if queued.request.optional and not queued.cancelled_reason:
                    queued.cancelled_reason = "preempted_by_user_turn"
            # PDF page 7: queued optional work has no remaining time value and
            # is dropped immediately. In-flight optional work is not cancelled
            # blindly across every provider. A required request preempts it only
            # when the same quota/concurrency domain would threaten chat's
            # deadline; unrelated providers may finish their already-reserved
            # work instead of suffering a ceremonial global panic.
            self._condition.notify_all()
        self._metric("broker_user_turn_begin", lane="chat_main")

    def set_user_turn_category(self, scope: str, category: str) -> None:
        actual = str(scope or "")
        if not actual:
            return
        normalized = str(category or "unknown").strip().lower() or "unknown"
        with self._condition:
            if actual in self._user_turn_category:
                self._user_turn_category[actual] = normalized[:48]

    def end_user_turn(self, scope: str) -> None:
        actual_scope = str(scope)
        with self._condition:
            self._user_turn_scopes.discard(actual_scope)
            remote_calls = int(self._user_turn_remote_calls.pop(actual_scope, 0))
            started_at = float(self._user_turn_started_at.pop(actual_scope, time.time()))
            category = self._user_turn_category.pop(actual_scope, "unknown")
            self._condition.notify_all()
        self._metric("broker_user_turn_end", lane="chat_main", reason=category)
        self._metric(
            "remote_calls_per_user_turn",
            lane="chat_main",
            reason=category,
            value=float(remote_calls),
        )
        self._metric(
            "user_turn_broker_scope_duration_ms",
            lane="chat_main",
            reason=category,
            value=max(0.0, (time.time() - started_at) * 1000.0),
        )

    def user_turn_active(self) -> bool:
        with self._condition:
            return bool(self._user_turn_scopes)

    def wait_for_user_idle(self, timeout: float) -> bool:
        deadline = time.time() + max(0.0, float(timeout))
        with self._condition:
            while self._user_turn_scopes:
                remaining = deadline - time.time()
                if remaining <= 0.0:
                    return False
                self._condition.wait(min(0.10, remaining))
            return True

    # ------------------------------------------------------------------
    # Request construction / admission
    # ------------------------------------------------------------------
    def request_from_context(
        self,
        *,
        provider: str,
        model: str,
        estimated_input_tokens: int,
        reserved_output_tokens: int,
        timeout_seconds: float | None = None,
        idempotency_key: str | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> InferenceRequest:
        context = current_inference_context()
        policy = lane_policy(context.lane)
        now = time.time()
        deadline = context.deadline_at
        if deadline is None:
            if timeout_seconds is not None:
                requested_timeout = float(timeout_seconds)
                deadline = (
                    math.inf
                    if requested_timeout <= 0.0
                    else now + max(0.05, requested_timeout)
                )
            elif float(policy.deadline_seconds) <= 0.0:
                deadline = math.inf
            else:
                deadline = now + max(0.05, float(policy.deadline_seconds))
        combined_metadata = dict(context.metadata)
        combined_metadata.update(dict(metadata or {}))
        return InferenceRequest(
            request_id=f"infer-{uuid.uuid4().hex[:16]}",
            lane=context.lane,
            provider=str(provider or "default").strip().lower() or "default",
            project=context.project or None,
            credential_pool=context.credential_pool or None,
            model=str(model or "unknown"),
            priority=int(context.priority if context.priority is not None else policy.priority),
            optional=bool(policy.optional if context.optional is None else context.optional),
            deadline_at=float(deadline),
            cancel_scope=context.cancel_scope or f"background:{context.lane}",
            estimated_input_tokens=max(0, int(estimated_input_tokens)),
            reserved_output_tokens=max(0, int(reserved_output_tokens)),
            idempotency_key=str(idempotency_key or context.idempotency_key or "") or None,
            coalesce_key=context.coalesce_key or None,
            metadata=combined_metadata,
        )

    def submit(self, request: InferenceRequest) -> AdmissionDecision:
        """Return a non-mutating admission preview for shadow/canary rollout.

        PDF annotation, pages 8 and 19: shadow mode must mirror the decision the
        enforcing broker *would* make, including provider heat, quota and
        concurrency.  Merely returning ``queued`` for every syntactically valid
        request produces decorative telemetry, which is a surprisingly popular
        substitute for evidence.
        """

        now = time.time()
        reason = self._immediate_rejection_reason(request, now)
        if reason:
            return AdmissionDecision(False, "rejected", reason, None, 0, 0, request.deadline_at)
        with self._condition:
            self._prune_locked(request.provider, now)
            duplicate_reason = self._idempotency_duplicate_reason_locked(request, now)
            if duplicate_reason:
                return AdmissionDecision(
                    False, "rejected", duplicate_reason, None, 0, 0, request.deadline_at
                )
            lane_depth = sum(
                1 for item in self._queue
                if item.request.lane == request.lane and not item.cancelled_reason
            )
            if lane_depth >= lane_policy(request.lane).max_queued:
                return AdmissionDecision(False, "rejected", "queue_full", None, 0, 0, request.deadline_at)
            if request.optional and self._user_turn_scopes:
                return AdmissionDecision(False, "rejected", "shed_optional_user_active", None, 0, 0, request.deadline_at)
            if request.optional and self._provider_pressure_locked(
                request.provider, now, request=request
            ) >= self._hot_threshold(request.lane):
                return AdmissionDecision(False, "rejected", "provider_hot_optional", None, 0, 0, request.deadline_at)
            preview_request = request
            quota_ok, _wait = self._quota_available_locked(preview_request, now)
            degrade_plan = None
            if not quota_ok and not request.optional:
                degrade_plan = self._required_degrade_plan_locked(request, now)
                if degrade_plan is not None:
                    preview_request = replace(
                        request,
                        estimated_input_tokens=degrade_plan[0],
                        reserved_output_tokens=degrade_plan[1],
                        metadata=dict(request.metadata),
                    )
                    quota_ok, _wait = self._quota_available_locked(preview_request, now)
            concurrency_ok = self._concurrency_available_locked(preview_request)
            if quota_ok and concurrency_ok:
                return AdmissionDecision(
                    True,
                    "admitted",
                    "preview_degraded" if degrade_plan is not None else "preview_admitted",
                    None,
                    preview_request.estimated_input_tokens,
                    preview_request.reserved_output_tokens,
                    request.deadline_at,
                )
            reason = "quota_unavailable" if not quota_ok else "concurrency_unavailable"
            if request.optional:
                return AdmissionDecision(False, "rejected", reason + "_optional", None, 0, 0, request.deadline_at)
            return AdmissionDecision(
                False, "queued", reason, lane_depth + 1, 0, 0, request.deadline_at
            )

    def run(self, request: InferenceRequest, call_fn: Callable[..., Any]) -> Any:
        if request.provider == "local":
            return call_fn()

        # PDF annotation, pages 18-20: progressive rollout is lane-specific.
        # Shadow mode calculates the real admission decision but does not block;
        # a kill switch drops optional work first and gives required chat an
        # explicit emergency bypass instead of silently taking the whole app down.
        rollout = broker_rollout_for_request(request)
        self._metric(
            "broker_rollout_decision_total",
            lane=request.lane,
            provider=request.provider,
            reason=rollout.effective_mode,
        )
        if not self.enabled:
            return self._run_unenforced(request, call_fn, mode="global_disabled")
        if rollout.disabled:
            if request.optional:
                self._record_rejection(request, "lane_kill_switch")
                raise InferenceRejected("lane_kill_switch", request)
            return self._run_unenforced(request, call_fn, mode="required_kill_bypass")
        if rollout.shadow:
            preview = self.submit(request)
            self._metric(
                "broker_shadow_decision_total",
                lane=request.lane,
                provider=request.provider,
                reason=f"{preview.state}:{preview.reason}",
            )
            return self._run_unenforced(request, call_fn, mode="shadow")

        inflight = self._admit_blocking(request)
        cancel_token = CancellationToken(inflight.cancel_event, request.deadline_at)
        started = time.perf_counter()
        try:
            if cancel_token.cancelled:
                self._refund_reservation(request.request_id)
                cancel_token.raise_if_cancelled(request)
            with self._condition:
                inflight.provider_started = True
                for scope in self._user_turn_scopes:
                    self._user_turn_remote_calls[scope] += 1
            result = self._invoke_provider_call(call_fn, cancel_token, request)
            # A provider may return after the user turn already pre-empted this
            # optional lane.  Do not let stale work surface or start another
            # fallback/retry merely because the socket eventually completed.
            cancel_token.raise_if_cancelled(request)
            usage = self._extract_usage(result)
            if usage is not None:
                self.settle_usage(
                    request.request_id,
                    actual_input_tokens=usage[0],
                    actual_output_tokens=usage[1],
                )
            self._record_completion(request, "completed", (time.perf_counter() - started) * 1000.0)
            return result
        except InferenceRejected:
            self._record_completion(request, "cancelled", (time.perf_counter() - started) * 1000.0)
            raise
        except Exception as exc:
            status = getattr(getattr(exc, "response", None), "status_code", None)
            if status == 429 or "429" in str(exc):
                retry_after = self._retry_after_from_exception(exc)
                self.record_rate_limit(request.provider, retry_after_s=retry_after)
            self._record_completion(request, "failed", (time.perf_counter() - started) * 1000.0)
            raise
        finally:
            self._release(request.request_id)

    def _run_unenforced(
        self,
        request: InferenceRequest,
        call_fn: Callable[..., Any],
        *,
        mode: str,
    ) -> Any:
        """Execute shadow/rollback traffic without quota reservation.

        This path is intentionally observable and cooperatively cancellable. It
        is not normal operation; it exists so rollout can be reversed without
        converting a broker defect into total chat unavailability.
        """

        event = threading.Event()
        token = CancellationToken(event, request.deadline_at)
        started = time.perf_counter()
        with self._condition:
            self._unenforced_inflight[request.request_id] = (request, event, mode)
            for scope in self._user_turn_scopes:
                self._user_turn_remote_calls[scope] += 1
        self._metric(
            "broker_unenforced_call_total",
            lane=request.lane,
            provider=request.provider,
            reason=mode,
        )
        try:
            token.raise_if_cancelled(request)
            result = self._invoke_provider_call(call_fn, token, request)
            token.raise_if_cancelled(request)
            usage = self._extract_usage(result)
            if usage is not None:
                self._metric(
                    "broker_unenforced_input_tokens",
                    lane=request.lane,
                    provider=request.provider,
                    reason=mode,
                    value=float(usage[0]),
                )
                self._metric(
                    "broker_unenforced_output_tokens",
                    lane=request.lane,
                    provider=request.provider,
                    reason=mode,
                    value=float(usage[1]),
                )
            self._record_completion(
                request, f"{mode}_completed", (time.perf_counter() - started) * 1000.0
            )
            return result
        except InferenceRejected:
            self._record_completion(
                request, f"{mode}_cancelled", (time.perf_counter() - started) * 1000.0
            )
            raise
        except Exception as exc:
            status = getattr(getattr(exc, "response", None), "status_code", None)
            if status == 429 or "429" in str(exc):
                self.record_rate_limit(
                    request.provider, retry_after_s=self._retry_after_from_exception(exc)
                )
            self._record_completion(
                request, f"{mode}_failed", (time.perf_counter() - started) * 1000.0
            )
            raise
        finally:
            with self._condition:
                self._unenforced_inflight.pop(request.request_id, None)
                self._condition.notify_all()

    async def run_async(self, request: InferenceRequest, call_fn: Callable[..., Any]) -> Any:
        """Async broker helper with the same admission contract as :meth:`run`.

        PDF annotation, pages 6-10: the proposed broker API explicitly permits
        sync or async execution. Admission waits happen in a worker thread so an
        asyncio event loop is not frozen by queue backpressure. Native coroutine
        adapters are cancelled at the deadline; sync adapters still receive the
        cooperative token and run outside the event loop.
        """

        if request.provider == "local":
            return await self._invoke_provider_call_async(
                call_fn, CancellationToken(threading.Event(), request.deadline_at), request
            )

        rollout = broker_rollout_for_request(request)
        self._metric(
            "broker_rollout_decision_total",
            lane=request.lane,
            provider=request.provider,
            reason=rollout.effective_mode,
        )
        if not self.enabled:
            return await self._run_unenforced_async(request, call_fn, mode="global_disabled")
        if rollout.disabled:
            if request.optional:
                self._record_rejection(request, "lane_kill_switch")
                raise InferenceRejected("lane_kill_switch", request)
            return await self._run_unenforced_async(
                request, call_fn, mode="required_kill_bypass"
            )
        if rollout.shadow:
            preview = self.submit(request)
            self._metric(
                "broker_shadow_decision_total",
                lane=request.lane,
                provider=request.provider,
                reason=f"{preview.state}:{preview.reason}",
            )
            return await self._run_unenforced_async(request, call_fn, mode="shadow")

        inflight = await asyncio.to_thread(self._admit_blocking, request)
        cancel_token = CancellationToken(inflight.cancel_event, request.deadline_at)
        started = time.perf_counter()
        try:
            if cancel_token.cancelled:
                self._refund_reservation(request.request_id)
                cancel_token.raise_if_cancelled(request)
            with self._condition:
                inflight.provider_started = True
                for scope in self._user_turn_scopes:
                    self._user_turn_remote_calls[scope] += 1
            result = await self._invoke_provider_call_async(call_fn, cancel_token, request)
            cancel_token.raise_if_cancelled(request)
            usage = self._extract_usage(result)
            if usage is not None:
                self.settle_usage(
                    request.request_id,
                    actual_input_tokens=usage[0],
                    actual_output_tokens=usage[1],
                )
            self._record_completion(
                request, "completed", (time.perf_counter() - started) * 1000.0
            )
            return result
        except InferenceRejected:
            self._record_completion(
                request, "cancelled", (time.perf_counter() - started) * 1000.0
            )
            raise
        except Exception as exc:
            status = getattr(getattr(exc, "response", None), "status_code", None)
            if status == 429 or "429" in str(exc):
                self.record_rate_limit(
                    request.provider, retry_after_s=self._retry_after_from_exception(exc)
                )
            self._record_completion(
                request, "failed", (time.perf_counter() - started) * 1000.0
            )
            raise
        finally:
            self._release(request.request_id)

    async def _run_unenforced_async(
        self,
        request: InferenceRequest,
        call_fn: Callable[..., Any],
        *,
        mode: str,
    ) -> Any:
        event = threading.Event()
        token = CancellationToken(event, request.deadline_at)
        started = time.perf_counter()
        with self._condition:
            self._unenforced_inflight[request.request_id] = (request, event, mode)
            for scope in self._user_turn_scopes:
                self._user_turn_remote_calls[scope] += 1
        self._metric(
            "broker_unenforced_call_total",
            lane=request.lane,
            provider=request.provider,
            reason=mode,
        )
        try:
            token.raise_if_cancelled(request)
            result = await self._invoke_provider_call_async(call_fn, token, request)
            token.raise_if_cancelled(request)
            usage = self._extract_usage(result)
            if usage is not None:
                self._metric(
                    "broker_unenforced_input_tokens", lane=request.lane,
                    provider=request.provider, reason=mode, value=float(usage[0])
                )
                self._metric(
                    "broker_unenforced_output_tokens", lane=request.lane,
                    provider=request.provider, reason=mode, value=float(usage[1])
                )
            self._record_completion(
                request, f"{mode}_completed", (time.perf_counter() - started) * 1000.0
            )
            return result
        except InferenceRejected:
            self._record_completion(
                request, f"{mode}_cancelled", (time.perf_counter() - started) * 1000.0
            )
            raise
        except Exception as exc:
            status = getattr(getattr(exc, "response", None), "status_code", None)
            if status == 429 or "429" in str(exc):
                self.record_rate_limit(
                    request.provider, retry_after_s=self._retry_after_from_exception(exc)
                )
            self._record_completion(
                request, f"{mode}_failed", (time.perf_counter() - started) * 1000.0
            )
            raise
        finally:
            with self._condition:
                self._unenforced_inflight.pop(request.request_id, None)
                self._condition.notify_all()

    @classmethod
    async def _invoke_provider_call_async(
        cls,
        call_fn: Callable[..., Any],
        cancel_token: CancellationToken,
        request: InferenceRequest | None = None,
    ) -> Any:
        kwargs = cls._provider_call_kwargs(call_fn, cancel_token, request)

        async def execute() -> Any:
            if inspect.iscoroutinefunction(call_fn):
                return await call_fn(**kwargs)
            value = await asyncio.to_thread(call_fn, **kwargs)
            if inspect.isawaitable(value):
                return await value
            return value

        task = asyncio.create_task(execute())
        try:
            timeout = cancel_token.remaining_seconds(floor=0.001)
            if timeout is None:
                return await task
            return await asyncio.wait_for(task, timeout=timeout)
        except asyncio.TimeoutError as exc:
            cancel_token.event.set()
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            raise InferenceRejected("deadline_exceeded") from exc
        except asyncio.CancelledError:
            cancel_token.event.set()
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            raise

    @classmethod
    def _provider_call_kwargs(
        cls,
        call_fn: Callable[..., Any],
        cancel_token: CancellationToken,
        request: InferenceRequest | None = None,
    ) -> dict[str, Any]:
        try:
            parameters = inspect.signature(call_fn).parameters
        except (TypeError, ValueError):
            parameters = {}
        kwargs: dict[str, Any] = {}
        if "cancel_token" in parameters:
            kwargs["cancel_token"] = cancel_token
        if "deadline_at" in parameters:
            kwargs["deadline_at"] = cancel_token.deadline_at
        if "timeout_seconds" in parameters:
            kwargs["timeout_seconds"] = cancel_token.remaining_seconds()
        # V6 budget contract: SDK adapters that can rewrite their own payload may
        # opt in without coupling the broker to a provider library. The name is
        # deliberately specific so an ordinary ``request`` argument is never
        # hijacked by accident.
        if request is not None and "inference_request" in parameters:
            kwargs["inference_request"] = request
        return kwargs

    @classmethod
    def _invoke_provider_call(
        cls,
        call_fn: Callable[..., Any],
        cancel_token: CancellationToken,
        request: InferenceRequest | None = None,
    ) -> Any:
        """Invoke an adapter while preserving compatibility with zero-arg callables."""
        return call_fn(**cls._provider_call_kwargs(call_fn, cancel_token, request))

    def _admit_blocking(self, request: InferenceRequest) -> _Inflight:
        now = time.time()
        reason = self._immediate_rejection_reason(request, now)
        if reason:
            self._record_rejection(request, reason)
            raise InferenceRejected(reason, request)

        with self._condition:
            duplicate_reason = self._idempotency_duplicate_reason_locked(request, now)
            if duplicate_reason:
                self._record_rejection(request, duplicate_reason)
                self._metric(
                    "broker_idempotency_duplicate_total",
                    lane=request.lane,
                    provider=request.provider,
                    reason=duplicate_reason,
                )
                raise InferenceRejected(duplicate_reason, request)
            if request.coalesce_key:
                for queued in self._queue:
                    if (
                        queued.request.coalesce_key == request.coalesce_key
                        and queued.request.lane == request.lane
                        and not queued.cancelled_reason
                    ):
                        queued.cancelled_reason = "coalesced_to_newer"
                        self._metric(
                            "broker_coalesced_total",
                            lane=request.lane,
                            provider=request.provider,
                            reason="newer_replaced_waiting",
                        )

            lane_depth = sum(
                1 for item in self._queue
                if item.request.lane == request.lane and not item.cancelled_reason
            )
            if lane_depth >= lane_policy(request.lane).max_queued:
                self._record_rejection(request, "queue_full")
                raise InferenceRejected("queue_full", request)

            self._register_idempotency_locked(request)
            self._sequence += 1
            queued = _QueuedRequest(request=request, sequence=self._sequence, enqueued_at=now)
            self._queue.append(queued)
            self._sort_queue_locked()
            self._record_queue_depth_locked(request.lane, request.provider)
            self._condition.notify_all()

            while True:
                now = time.time()
                self._prune_locked(request.provider, now)
                if queued.cancelled_reason:
                    self._reject_waiting_locked(queued, queued.cancelled_reason)
                if self._scope_cancelled_locked(request.cancel_scope):
                    self._reject_waiting_locked(queued, "cancel_scope")
                if math.isfinite(request.deadline_at) and request.deadline_at <= now + 0.025:
                    self._reject_waiting_locked(queued, "deadline_too_close")
                if request.optional and self._user_turn_scopes:
                    self._reject_waiting_locked(queued, "shed_optional_user_active")
                if request.optional:
                    pressure = self._provider_pressure_locked(
                        request.provider, now, request=request
                    )
                    if pressure >= self._hot_threshold(request.lane):
                        self._reject_waiting_locked(queued, "provider_hot_optional")

                # PDF annotation, pages 7-9: priority is enforced inside the
                # quota domain, not by one global head that blocks unrelated
                # providers.  A hot Gemini job must not stop a ready Groq chat
                # merely because it entered the Python list first.
                head = next(
                    (
                        item
                        for item in self._queue
                        if not item.cancelled_reason
                        and item.request.provider == request.provider
                    ),
                    None,
                )
                if head is queued:
                    quota_ok, wait_quota = self._quota_available_locked(request, now)
                    if not quota_ok and not request.optional:
                        degrade_plan = self._required_degrade_plan_locked(request, now)
                        if degrade_plan is not None:
                            self._apply_required_degrade_locked(request, degrade_plan)
                            quota_ok, wait_quota = self._quota_available_locked(request, now)
                    concurrency_ok = self._concurrency_available_locked(request)
                    if not request.optional and not (quota_ok and concurrency_ok):
                        self._preempt_optional_inflight_locked(
                            request,
                            quota_ok=quota_ok,
                            concurrency_ok=concurrency_ok,
                            quota_wait_s=wait_quota,
                            now=now,
                        )
                    if quota_ok and concurrency_ok:
                        if request.optional and self._user_turn_scopes:
                            self._metric(
                                "optional_background_admitted_during_user_turn_total",
                                lane=request.lane,
                                provider=request.provider,
                            )
                        self._remove_locked(request.request_id)
                        self._reserve_locked(request, now)
                        key = self._concurrency_key(request)
                        self._active_by_key[key] += max(1, request.max_concurrency_cost)
                        inflight = _Inflight(request=request, started_at=now)
                        self._inflight[request.request_id] = inflight
                        self._record_admitted(request, now - queued.enqueued_at)
                        self._condition.notify_all()
                        return inflight
                    wait_for = min(0.20, max(0.025, wait_quota))
                else:
                    wait_for = 0.10
                remaining = request.deadline_at - now if math.isfinite(request.deadline_at) else math.inf
                if math.isfinite(remaining) and remaining <= wait_for:
                    self._reject_waiting_locked(queued, "deadline_before_admission")
                self._condition.wait(wait_for if not math.isfinite(remaining) else min(wait_for, remaining))

    def _reject_waiting_locked(self, queued: _QueuedRequest, reason: str) -> None:
        """Release a queued idempotency lease before raising rejection."""

        request = queued.request
        self._remove_locked(request.request_id)
        self._release_idempotency_locked(request, provider_started=False)
        self._record_rejection(request, reason)
        raise InferenceRejected(reason, request)

    def _immediate_rejection_reason(self, request: InferenceRequest, now: float) -> str:
        if math.isfinite(request.deadline_at) and request.deadline_at <= now + 0.025:
            return "deadline_too_close"
        if not request.lane:
            return "invalid_lane"
        if request.optional and self.user_turn_active():
            return "shed_optional_user_active"
        return ""

    def _idempotency_ttl_seconds(self) -> float:
        raw = str(os.getenv("ERIS_BROKER_IDEMPOTENCY_TTL_SEC", "120") or "120")
        try:
            return max(1.0, min(86400.0, float(raw)))
        except ValueError:
            return 120.0

    @staticmethod
    def _idempotency_namespace(request: InferenceRequest) -> str:
        key = str(request.idempotency_key or "").strip()
        if not key:
            return ""
        return "|".join(
            (
                str(request.provider or "default").strip().lower(),
                str(request.project or "default").strip().lower(),
                str(request.lane or "transport").strip().lower(),
                key,
            )
        )

    def _prune_idempotency_locked(self, now: float) -> None:
        expired = [
            key for key, expires_at in self._idempotency_tombstones.items()
            if float(expires_at) <= now
        ]
        for key in expired:
            self._idempotency_tombstones.pop(key, None)

    def _idempotency_duplicate_reason_locked(
        self, request: InferenceRequest, now: float
    ) -> str:
        namespace = self._idempotency_namespace(request)
        if not namespace:
            return ""
        self._prune_idempotency_locked(now)
        active_request = self._idempotency_active.get(namespace)
        if active_request and active_request != request.request_id:
            return "duplicate_idempotency_active"
        if self._idempotency_tombstones.get(namespace, 0.0) > now:
            return "duplicate_idempotency_recent"
        return ""

    def _register_idempotency_locked(self, request: InferenceRequest) -> None:
        namespace = self._idempotency_namespace(request)
        if namespace:
            self._idempotency_active[namespace] = request.request_id

    def _release_idempotency_locked(
        self,
        request: InferenceRequest,
        *,
        provider_started: bool,
    ) -> None:
        namespace = self._idempotency_namespace(request)
        if not namespace:
            return
        if self._idempotency_active.get(namespace) == request.request_id:
            self._idempotency_active.pop(namespace, None)
        if provider_started:
            self._idempotency_tombstones[namespace] = (
                time.time() + self._idempotency_ttl_seconds()
            )
            self._persist_durable_state_locked()

    # ------------------------------------------------------------------
    # Quota / headers
    # ------------------------------------------------------------------
    def _rpm_limit(self, provider: str) -> int:
        key = provider.upper().replace("-", "_")
        raw = str(os.getenv(f"ERIS_BROKER_RPM_{key}", "") or "").strip()
        if raw:
            try:
                return max(1, int(raw))
            except ValueError:
                pass
        if provider in self._dynamic_rpm:
            return max(1, int(self._dynamic_rpm[provider]))
        return {"groq": 30, "gemini": 60, "openai": 60, "default": 60}.get(provider, 60)

    def _tpm_limit(self, provider: str) -> int:
        key = provider.upper().replace("-", "_")
        raw = str(os.getenv(f"ERIS_BROKER_TPM_{key}", "") or "").strip()
        if raw:
            try:
                return max(256, int(raw))
            except ValueError:
                pass
        if provider in self._dynamic_tpm:
            return max(256, int(self._dynamic_tpm[provider]))
        try:
            from llm.token_budget import budget_for_provider

            return max(256, int(budget_for_provider(provider)))
        except Exception:
            return 30_000

    def _itpm_limit(self, provider: str) -> int:
        key = provider.upper().replace("-", "_")
        raw = str(os.getenv(f"ERIS_BROKER_ITPM_{key}", "") or "").strip()
        if raw:
            try:
                return max(128, int(raw))
            except ValueError:
                pass
        if provider in self._dynamic_itpm:
            return max(128, int(self._dynamic_itpm[provider]))
        return self._tpm_limit(provider)

    def _otpm_limit(self, provider: str) -> int:
        key = provider.upper().replace("-", "_")
        raw = str(os.getenv(f"ERIS_BROKER_OTPM_{key}", "") or "").strip()
        if raw:
            try:
                return max(64, int(raw))
            except ValueError:
                pass
        if provider in self._dynamic_otpm:
            return max(64, int(self._dynamic_otpm[provider]))
        return self._tpm_limit(provider)

    @staticmethod
    def _project_env_fragment(value: str) -> str:
        cleaned = "".join(char if char.isalnum() else "_" for char in str(value or "").upper())
        return "_".join(part for part in cleaned.split("_") if part) or "DEFAULT"

    def _project_scope(self, request: InferenceRequest) -> tuple[str, str] | None:
        project = str(request.project or "").strip()
        return (request.provider, project) if project else None

    def _project_limit(self, request: InferenceRequest, kind: str) -> int | None:
        """Return an optional internal project budget.

        PDF annotation, page 19: project limits are deliberately lower than the
        organization/provider ceiling so ERIS sheds its own background work
        before the shared provider quota does it rudely with a 429.
        """

        project = str(request.project or "").strip()
        if not project:
            return None
        provider_key = self._project_env_fragment(request.provider)
        project_key = self._project_env_fragment(project)
        kind_key = str(kind or "").strip().upper()
        for name in (
            f"ERIS_BROKER_PROJECT_{kind_key}_{provider_key}_{project_key}",
            f"ERIS_BROKER_PROJECT_{kind_key}_{project_key}",
        ):
            raw = str(os.getenv(name, "") or "").strip()
            if not raw:
                continue
            try:
                minimum = 1 if kind_key == "RPM" else 1
                return max(minimum, int(raw))
            except ValueError:
                continue
        return None

    @staticmethod
    def _ratio(used: int | float, limit: int | float | None) -> float:
        if limit is None or float(limit) <= 0.0:
            return 0.0
        return max(0.0, float(used) / float(limit))

    def _provider_pressure_locked(
        self,
        provider: str,
        now: float,
        *,
        request: InferenceRequest | None = None,
    ) -> float:
        """Estimate provider heat before a hard limit is exhausted.

        The original pass only considered an explicit cooldown as "hot".
        Pages 6-9 describe proactive optional shedding, so pressure now also
        includes RPM/TPM/ITPM/OTPM utilization.  This protects chat before the
        provider has to teach us the lesson via rate limiting.
        """

        self._prune_locked(provider, now)
        ratios = [
            self._ratio(len(self._request_events[provider]), self._rpm_limit(provider)),
            self._ratio(sum(item[2] for item in self._token_events[provider]), self._tpm_limit(provider)),
            self._ratio(sum(item[2] for item in self._input_token_events[provider]), self._itpm_limit(provider)),
            self._ratio(sum(item[2] for item in self._output_token_events[provider]), self._otpm_limit(provider)),
        ]
        for kind, limit in (
            ("rpm", self._rpm_limit(provider)),
            ("tpm", self._tpm_limit(provider)),
            ("itpm", self._itpm_limit(provider)),
            ("otpm", self._otpm_limit(provider)),
        ):
            remaining = self._active_header_remaining_locked(provider, kind, now)
            if remaining is not None and limit > 0:
                ratios.append(max(0.0, min(1.0, 1.0 - (remaining / float(limit)))))
        if request is not None:
            scope = self._project_scope(request)
            if scope is not None:
                project_limits = {
                    "rpm": self._project_limit(request, "RPM"),
                    "tpm": self._project_limit(request, "TPM"),
                    "itpm": self._project_limit(request, "ITPM"),
                    "otpm": self._project_limit(request, "OTPM"),
                }
                ratios.extend(
                    [
                        self._ratio(len(self._project_request_events[scope]), project_limits["rpm"]),
                        self._ratio(
                            sum(item[2] for item in self._project_token_events[scope]),
                            project_limits["tpm"],
                        ),
                        self._ratio(
                            sum(item[2] for item in self._project_input_token_events[scope]),
                            project_limits["itpm"],
                        ),
                        self._ratio(
                            sum(item[2] for item in self._project_output_token_events[scope]),
                            project_limits["otpm"],
                        ),
                    ]
                )
        if self._cooldown_until[provider] > now:
            return 1.0
        return min(1.0, max(ratios or [0.0]))

    def _hot_threshold(self, lane: str = "") -> float:
        lane_key = _env_lane_key(lane or "transport")
        raw = str(
            os.getenv(
                f"ERIS_BROKER_HOT_UTILIZATION_{lane_key}",
                os.getenv("ERIS_BROKER_HOT_UTILIZATION", "0.82"),
            )
            or "0.82"
        ).strip()
        try:
            return max(0.25, min(0.99, float(raw)))
        except ValueError:
            return 0.82

    def optional_lane_advice(
        self,
        lane: str,
        *,
        provider: str = "default",
        project: str = "",
    ) -> dict[str, Any]:
        """Expose one pressure decision to event-driven background loops.

        The loop may use ``threshold_multiplier`` to demand a larger state delta
        and ``cooldown_multiplier`` to poll less aggressively.  Broker admission
        remains authoritative; this merely avoids manufacturing jobs that are
        almost certain to be shed.
        """

        policy = lane_policy(lane)
        probe = InferenceRequest(
            request_id="advice",
            lane=str(lane or "transport"),
            provider=str(provider or "default"),
            project=str(project or "") or None,
            credential_pool=None,
            model="advice",
            priority=policy.priority,
            optional=policy.optional,
            deadline_at=(
                math.inf
                if float(policy.deadline_seconds) <= 0.0
                else time.time() + policy.deadline_seconds
            ),
            cancel_scope="",
            estimated_input_tokens=0,
            reserved_output_tokens=0,
        )
        now = time.time()
        with self._condition:
            pressure = self._provider_pressure_locked(probe.provider, now, request=probe)
            active = bool(self._user_turn_scopes)
            hot = pressure >= self._hot_threshold(lane)
        allowed = not (policy.optional and (active or hot))
        return {
            "allowed": allowed,
            "reason": "user_turn_active" if active and policy.optional else "provider_hot" if hot and policy.optional else "allow",
            "pressure_score": round(pressure, 4),
            "hot": hot,
            "threshold_multiplier": 1.0 + (1.5 * pressure),
            "cooldown_multiplier": 1.0 + (3.0 * pressure),
        }

    @staticmethod
    def _window_wait(
        events: deque[tuple[Any, ...]],
        now: float,
        *,
        fallback: float = 0.20,
    ) -> float:
        if not events:
            return fallback
        return max(0.025, 60.0 - (now - float(events[0][0])))

    def _lane_allows_required_degrade(self, request: InferenceRequest) -> bool:
        lane = str(request.lane or "transport").strip().lower() or "transport"
        env_name = f"ERIS_BROKER_ALLOW_DEGRADE_{_env_lane_key(lane)}"
        raw = os.getenv(env_name)
        if raw is not None:
            return str(raw or "").strip().lower() not in {"0", "false", "no", "off"}
        return lane in _REQUIRED_DEGRADE_LANES

    def _remaining_token_capacity_locked(
        self, request: InferenceRequest, kind: str, now: float
    ) -> int:
        provider = request.provider
        if kind == "tpm":
            events = self._token_events[provider]
            limit = self._tpm_limit(provider)
        elif kind == "itpm":
            events = self._input_token_events[provider]
            limit = self._itpm_limit(provider)
        elif kind == "otpm":
            events = self._output_token_events[provider]
            limit = self._otpm_limit(provider)
        else:
            raise ValueError(f"unknown token quota kind: {kind}")
        capacity = max(0, int(limit) - sum(int(item[2]) for item in events))
        header_remaining = self._active_header_remaining_locked(provider, kind, now)
        if header_remaining is not None:
            capacity = min(capacity, max(0, int(header_remaining)))
        scope = self._project_scope(request)
        if scope is not None:
            project_kind = kind.upper()
            project_limit = self._project_limit(request, project_kind)
            if project_limit is not None:
                project_events = {
                    "tpm": self._project_token_events[scope],
                    "itpm": self._project_input_token_events[scope],
                    "otpm": self._project_output_token_events[scope],
                }[kind]
                project_used = sum(int(item[2]) for item in project_events)
                capacity = min(capacity, max(0, int(project_limit) - project_used))
        return capacity

    def _request_slot_available_locked(self, request: InferenceRequest, now: float) -> bool:
        provider = request.provider
        if self._cooldown_until[provider] > now:
            return False
        if len(self._request_events[provider]) >= self._rpm_limit(provider):
            return False
        header_remaining = self._active_header_remaining_locked(provider, "rpm", now)
        if header_remaining is not None and header_remaining < 1:
            return False
        scope = self._project_scope(request)
        if scope is not None:
            limit = self._project_limit(request, "RPM")
            if limit is not None and len(self._project_request_events[scope]) >= limit:
                return False
        return True

    def _required_degrade_plan_locked(
        self, request: InferenceRequest, now: float
    ) -> tuple[int, int, str] | None:
        """Return a truthful smaller reservation for a required request.

        PDF annotation, page 8: when quota is insufficient, required chat may
        shrink prompt/output before it queues. This only activates when the
        adapter advertised a payload rewrite contract in request metadata.
        Reservation numbers are therefore never reduced while the actual HTTP
        payload stays large, a tiny detail with surprisingly large consequences.
        """

        if request.optional or not self._lane_allows_required_degrade(request):
            return None
        if bool(request.metadata.get("broker_degraded")):
            return None
        supports_input = bool(request.metadata.get("input_degrade_supported", False))
        supports_output = bool(request.metadata.get("output_degrade_supported", False))
        if not supports_input and not supports_output:
            return None
        if not self._request_slot_available_locked(request, now):
            return None

        original_in = max(0, int(request.estimated_input_tokens))
        original_out = max(0, int(request.reserved_output_tokens))
        lane_key = _env_lane_key(request.lane)
        default_min_in = 512 if request.lane in {"chat_main", "chat", "chat_tool_required"} else 256
        default_min_out = 96 if request.lane in {"chat_main", "chat"} else 64
        min_in = min(
            original_in,
            _env_int(f"ERIS_BROKER_DEGRADE_MIN_INPUT_{lane_key}", default_min_in, minimum=0),
        )
        min_out = min(
            original_out,
            _env_int(f"ERIS_BROKER_DEGRADE_MIN_OUTPUT_{lane_key}", default_min_out, minimum=0),
        )
        if not supports_input:
            min_in = original_in
        if not supports_output:
            min_out = original_out

        tpm_capacity = self._remaining_token_capacity_locked(request, "tpm", now)
        in_capacity = self._remaining_token_capacity_locked(request, "itpm", now)
        out_capacity = self._remaining_token_capacity_locked(request, "otpm", now)
        if tpm_capacity < min_in + min_out or in_capacity < min_in or out_capacity < min_out:
            return None

        # Proportional first pass, then preserve the minimum response budget and
        # cut input history before shaving the final answer further.
        ratios = [1.0]
        if original_in > 0:
            ratios.append(in_capacity / float(original_in))
        if original_out > 0:
            ratios.append(out_capacity / float(original_out))
        if original_in + original_out > 0:
            ratios.append(tpm_capacity / float(original_in + original_out))
        scale = max(0.0, min(ratios))
        target_in = original_in if not supports_input else max(min_in, min(original_in, int(original_in * scale)))
        target_out = original_out if not supports_output else max(min_out, min(original_out, int(original_out * scale)))
        target_in = min(target_in, in_capacity)
        target_out = min(target_out, out_capacity)

        overflow = max(0, target_in + target_out - tpm_capacity)
        if overflow and supports_input:
            cut = min(overflow, max(0, target_in - min_in))
            target_in -= cut
            overflow -= cut
        if overflow and supports_output:
            cut = min(overflow, max(0, target_out - min_out))
            target_out -= cut
            overflow -= cut
        if overflow or target_in < min_in or target_out < min_out:
            return None
        if target_in >= original_in and target_out >= original_out:
            return None
        reason = "quota_adaptive_prompt_output"
        if target_in == original_in:
            reason = "quota_adaptive_output"
        elif target_out == original_out:
            reason = "quota_adaptive_prompt"
        return int(target_in), int(target_out), reason

    def _apply_required_degrade_locked(
        self, request: InferenceRequest, plan: tuple[int, int, str]
    ) -> None:
        target_in, target_out, reason = plan
        original_in = int(request.estimated_input_tokens)
        original_out = int(request.reserved_output_tokens)
        request.metadata.setdefault("broker_original_input_tokens", original_in)
        request.metadata.setdefault("broker_original_output_tokens", original_out)
        request.metadata["broker_degraded"] = True
        request.metadata["broker_degrade_reason"] = reason
        request.estimated_input_tokens = max(0, int(target_in))
        request.reserved_output_tokens = max(0, int(target_out))
        self._metric(
            "broker_required_degraded_total", lane=request.lane,
            provider=request.provider, reason=reason,
        )
        self._metric(
            "broker_degraded_input_tokens_saved", lane=request.lane,
            provider=request.provider, reason=reason,
            value=float(max(0, original_in - request.estimated_input_tokens)),
        )
        self._metric(
            "broker_degraded_output_tokens_saved", lane=request.lane,
            provider=request.provider, reason=reason,
            value=float(max(0, original_out - request.reserved_output_tokens)),
        )

    def _preempt_optional_inflight_locked(
        self,
        request: InferenceRequest,
        *,
        quota_ok: bool,
        concurrency_ok: bool,
        quota_wait_s: float,
        now: float,
    ) -> int:
        """Cooperatively cancel only optional work that threatens this request.

        PDF annotation, page 7: on-wire optional work may continue while it is
        within reservation and harmless to chat. It is preempted when it owns the
        same concurrency slot or a hot same-provider quota domain risks the
        required deadline.
        """

        if request.optional:
            return 0
        remaining = (
            max(0.0, request.deadline_at - now)
            if math.isfinite(request.deadline_at)
            else math.inf
        )
        pressure = self._provider_pressure_locked(request.provider, now, request=request)
        deadline_risk = remaining <= max(0.35, float(quota_wait_s) + 0.25)
        cancelled = 0
        required_key = self._concurrency_key(request)
        for inflight in self._inflight.values():
            other = inflight.request
            if not other.optional or inflight.cancel_event.is_set():
                continue
            same_provider = other.provider == request.provider
            same_project = (other.project or "default") == (request.project or "default")
            blocks_concurrency = (not concurrency_ok) and self._concurrency_key(other) == required_key
            threatens_quota = (
                not quota_ok
                and same_provider
                and same_project
                and (deadline_risk or pressure >= self._hot_threshold(request.lane))
            )
            if not (blocks_concurrency or threatens_quota):
                continue
            inflight.cancel_event.set()
            cancelled += 1
            self._metric(
                "broker_optional_inflight_preempted_total",
                lane=other.lane, provider=other.provider,
                reason="concurrency" if blocks_concurrency else "quota_deadline",
            )
        if cancelled:
            self._condition.notify_all()
        return cancelled

    def _quota_available_locked(self, request: InferenceRequest, now: float) -> tuple[bool, float]:
        provider = request.provider
        self._prune_locked(provider, now)
        cooldown_wait = max(0.0, self._cooldown_until[provider] - now)
        if cooldown_wait > 0.0:
            return False, cooldown_wait
        rpm_events = self._request_events[provider]
        token_events = self._token_events[provider]
        input_events = self._input_token_events[provider]
        output_events = self._output_token_events[provider]
        reserved_input = request.estimated_input_tokens
        reserved_output = request.reserved_output_tokens
        reserved = reserved_input + reserved_output
        checks: list[tuple[bool, deque[tuple[Any, ...]]]] = [
            (len(rpm_events) < self._rpm_limit(provider), rpm_events),
            (sum(item[2] for item in token_events) + reserved <= self._tpm_limit(provider), token_events),
            (sum(item[2] for item in input_events) + reserved_input <= self._itpm_limit(provider), input_events),
            (sum(item[2] for item in output_events) + reserved_output <= self._otpm_limit(provider), output_events),
        ]
        header_checks = (
            ("rpm", 1),
            ("tpm", reserved),
            ("itpm", reserved_input),
            ("otpm", reserved_output),
        )
        header_waits: list[float] = []
        for kind, addition in header_checks:
            remaining = self._active_header_remaining_locked(provider, kind, now)
            if remaining is None or addition <= remaining:
                continue
            reset_at = self._header_reset_at[provider].get(kind, now + 0.20)
            header_waits.append(max(0.025, reset_at - now))

        scope = self._project_scope(request)
        if scope is not None:
            project_checks = (
                ("RPM", self._project_request_events[scope], 1),
                ("TPM", self._project_token_events[scope], reserved),
                ("ITPM", self._project_input_token_events[scope], reserved_input),
                ("OTPM", self._project_output_token_events[scope], reserved_output),
            )
            for kind, events, addition in project_checks:
                limit = self._project_limit(request, kind)
                if limit is None:
                    continue
                used = len(events) if kind == "RPM" else sum(item[2] for item in events)
                checks.append((used + addition <= limit, events))

        if all(ok for ok, _events in checks) and not header_waits:
            return True, 0.0
        waits = [self._window_wait(events, now) for ok, events in checks if not ok]
        waits.extend(header_waits)
        return False, min(waits) if waits else 0.20

    def _reserve_locked(self, request: InferenceRequest, now: float) -> None:
        total = request.estimated_input_tokens + request.reserved_output_tokens
        for kind, amount in (
            ("rpm", 1),
            ("tpm", total),
            ("itpm", request.estimated_input_tokens),
            ("otpm", request.reserved_output_tokens),
        ):
            remaining = self._active_header_remaining_locked(request.provider, kind, now)
            if remaining is not None:
                self._header_remaining[request.provider][kind] = max(0, remaining - amount)
        self._request_events[request.provider].append((now, request.request_id))
        self._token_events[request.provider].append((now, request.request_id, total))
        self._input_token_events[request.provider].append(
            (now, request.request_id, request.estimated_input_tokens)
        )
        self._output_token_events[request.provider].append(
            (now, request.request_id, request.reserved_output_tokens)
        )
        scope = self._project_scope(request)
        if scope is not None:
            self._project_request_events[scope].append((now, request.request_id))
            self._project_token_events[scope].append((now, request.request_id, total))
            self._project_input_token_events[scope].append(
                (now, request.request_id, request.estimated_input_tokens)
            )
            self._project_output_token_events[scope].append(
                (now, request.request_id, request.reserved_output_tokens)
            )

    def settle_usage(
        self,
        request_id: str,
        *,
        actual_input_tokens: int,
        actual_output_tokens: int,
    ) -> None:
        """Replace a conservative reservation with provider-reported usage.

        PDF annotation, pages 8-9 and 15-16: unused reserve returns to the
        rolling bucket; usage above reserve becomes visible debt and tightens
        admission for following lanes.  RPM is intentionally not refunded
        because the provider request really happened.
        """

        actual_in = max(0, int(actual_input_tokens or 0))
        actual_out = max(0, int(actual_output_tokens or 0))
        with self._condition:
            matched_provider = ""
            for provider in tuple(self._token_events):
                if any(item[1] == request_id for item in self._token_events[provider]):
                    self._replace_token_event_locked(
                        self._input_token_events[provider], request_id, actual_in
                    )
                    self._replace_token_event_locked(
                        self._output_token_events[provider], request_id, actual_out
                    )
                    self._replace_token_event_locked(
                        self._token_events[provider], request_id, actual_in + actual_out
                    )
                    matched_provider = provider
                    break

            # Keep the internal project budget in the same settled state as the
            # organization bucket.  Otherwise a conservative reserve would stay
            # stuck for a minute and punish exactly the lane we just measured.
            for scope in tuple(self._project_token_events):
                if any(item[1] == request_id for item in self._project_token_events[scope]):
                    self._replace_token_event_locked(
                        self._project_input_token_events[scope], request_id, actual_in
                    )
                    self._replace_token_event_locked(
                        self._project_output_token_events[scope], request_id, actual_out
                    )
                    self._replace_token_event_locked(
                        self._project_token_events[scope], request_id, actual_in + actual_out
                    )
                    break

            if matched_provider:
                self._metric(
                    "broker_actual_input_tokens",
                    provider=matched_provider,
                    value=float(actual_in),
                )
                self._metric(
                    "broker_actual_output_tokens",
                    provider=matched_provider,
                    value=float(actual_out),
                )
            self._condition.notify_all()

    def record_response_headers(self, provider: str, headers: Mapping[str, Any] | None) -> None:
        """Use provider limit/remaining/reset headers as dynamic truth.

        PDF annotation, pages 2 and 15-16: limits alone are not enough. A
        provider may report a smaller live remainder and a precise reset window;
        admission must honor both before another optional lane creates a 429.
        """

        actual_provider = str(provider or "default").strip().lower() or "default"
        normalized = {str(key).lower(): str(value) for key, value in dict(headers or {}).items()}
        now = time.time()
        with self._condition:
            rpm = self._first_int(normalized, "x-ratelimit-limit-requests", "ratelimit-limit-requests")
            tpm = self._first_int(normalized, "x-ratelimit-limit-tokens", "ratelimit-limit-tokens")
            itpm = self._first_int(
                normalized, "x-ratelimit-limit-input-tokens",
                "x-ratelimit-limit-input-tokens-minute", "ratelimit-limit-input-tokens"
            )
            otpm = self._first_int(
                normalized, "x-ratelimit-limit-output-tokens",
                "x-ratelimit-limit-output-tokens-minute", "ratelimit-limit-output-tokens"
            )
            if rpm:
                self._dynamic_rpm[actual_provider] = rpm
            if tpm:
                self._dynamic_tpm[actual_provider] = tpm
            if itpm:
                self._dynamic_itpm[actual_provider] = itpm
            if otpm:
                self._dynamic_otpm[actual_provider] = otpm

            remaining_keys = {
                "rpm": ("x-ratelimit-remaining-requests", "ratelimit-remaining-requests"),
                "tpm": ("x-ratelimit-remaining-tokens", "ratelimit-remaining-tokens"),
                "itpm": ("x-ratelimit-remaining-input-tokens", "ratelimit-remaining-input-tokens"),
                "otpm": ("x-ratelimit-remaining-output-tokens", "ratelimit-remaining-output-tokens"),
            }
            reset_keys = {
                "rpm": ("x-ratelimit-reset-requests", "ratelimit-reset-requests"),
                "tpm": ("x-ratelimit-reset-tokens", "ratelimit-reset-tokens"),
                "itpm": ("x-ratelimit-reset-input-tokens", "ratelimit-reset-input-tokens"),
                "otpm": ("x-ratelimit-reset-output-tokens", "ratelimit-reset-output-tokens"),
            }
            for kind, keys in remaining_keys.items():
                remaining = self._first_nonnegative_int(normalized, *keys)
                if remaining is not None:
                    self._header_remaining[actual_provider][kind] = remaining
                reset_seconds = self._first_reset_seconds(normalized, now, *reset_keys[kind])
                if reset_seconds is not None:
                    self._header_reset_at[actual_provider][kind] = now + reset_seconds

            retry_after = self._first_reset_seconds(normalized, now, "retry-after")
            if retry_after and retry_after > 0:
                self._cooldown_until[actual_provider] = max(
                    self._cooldown_until[actual_provider], now + min(retry_after, 3600.0)
                )
            self._persist_durable_state_locked()
            self._condition.notify_all()

    def record_rate_limit(self, provider: str, *, retry_after_s: float = 20.0) -> None:
        with self._condition:
            wait = max(1.0, min(float(retry_after_s or 20.0), 3600.0))
            self._cooldown_until[str(provider)] = max(self._cooldown_until[str(provider)], time.time() + wait)
            self._persist_durable_state_locked()
            self._condition.notify_all()
        self._metric("broker_rate_limit", provider=provider, value=1.0)

    # ------------------------------------------------------------------
    # Cancellation / inspection
    # ------------------------------------------------------------------
    def cancel_scope(self, scope: str) -> int:
        actual = str(scope or "")
        if not actual:
            return 0
        cancelled = 0
        with self._condition:
            self._cancelled_scopes[actual] = time.time()
            for queued in self._queue:
                if queued.request.cancel_scope == actual and not queued.cancelled_reason:
                    queued.cancelled_reason = "cancel_scope"
                    cancelled += 1
            queued_cancelled = cancelled
            inflight_cancelled = 0
            for inflight in self._inflight.values():
                if inflight.request.cancel_scope == actual:
                    inflight.cancel_event.set()
                    inflight_cancelled += 1
                    cancelled += 1
            for request, event, _mode in self._unenforced_inflight.values():
                if request.cancel_scope == actual:
                    event.set()
                    inflight_cancelled += 1
                    cancelled += 1
            self._condition.notify_all()
        if queued_cancelled:
            self._metric(
                "broker_cancel_before_start_total", reason="scope", value=float(queued_cancelled)
            )
        if inflight_cancelled:
            self._metric(
                "broker_cancel_inflight_total", reason="scope", value=float(inflight_cancelled)
            )
        return cancelled

    def list_inflight(self) -> list[dict[str, Any]]:
        with self._condition:
            enforced = [
                {
                    **asdict(item.request),
                    "started_at": item.started_at,
                    "cancelled": item.cancel_event.is_set(),
                    "broker_mode": "enforce",
                }
                for item in self._inflight.values()
            ]
            unenforced = [
                {
                    **asdict(request),
                    "started_at": None,
                    "cancelled": event.is_set(),
                    "broker_mode": mode,
                }
                for request, event, mode in self._unenforced_inflight.values()
            ]
            return enforced + unenforced

    def list_queued(self) -> list[dict[str, Any]]:
        with self._condition:
            return [
                {**asdict(item.request), "sequence": item.sequence, "enqueued_at": item.enqueued_at, "cancelled_reason": item.cancelled_reason}
                for item in self._queue
            ]

    def snapshot(self, provider: str = "default", project: str | None = None) -> dict[str, Any]:
        now = time.time()
        with self._condition:
            self._prune_locked(provider, now)
            probe = InferenceRequest(
                request_id="snapshot",
                lane="transport",
                provider=provider,
                project=str(project or "") or None,
                credential_pool=None,
                model="snapshot",
                priority=lane_policy("transport").priority,
                optional=False,
                deadline_at=now + 1.0,
                cancel_scope="",
                estimated_input_tokens=0,
                reserved_output_tokens=0,
            )
            pressure = self._provider_pressure_locked(provider, now, request=probe)
            payload: dict[str, Any] = {
                "provider": provider,
                "project": str(project or ""),
                "rpm_used": len(self._request_events[provider]),
                "rpm_limit": self._rpm_limit(provider),
                "tpm_used": sum(item[2] for item in self._token_events[provider]),
                "tpm_limit": self._tpm_limit(provider),
                "itpm_used": sum(item[2] for item in self._input_token_events[provider]),
                "itpm_limit": self._itpm_limit(provider),
                "otpm_used": sum(item[2] for item in self._output_token_events[provider]),
                "otpm_limit": self._otpm_limit(provider),
                "pressure_score": round(pressure, 4),
                "hot": pressure >= self._hot_threshold("transport"),
                "cooldown_remaining_s": max(0.0, self._cooldown_until[provider] - now),
                "queued": len([item for item in self._queue if not item.cancelled_reason]),
                "inflight": len(self._inflight) + len(self._unenforced_inflight),
                "unenforced_inflight": len(self._unenforced_inflight),
                "user_turn_active": bool(self._user_turn_scopes),
                "idempotency_active": len(self._idempotency_active),
                "idempotency_recent": len(self._idempotency_tombstones),
                "durable_state_enabled": self._state_store is not None,
                "provider_header_remaining": {
                    kind: self._active_header_remaining_locked(provider, kind, now)
                    for kind in ("rpm", "tpm", "itpm", "otpm")
                },
                "provider_header_reset_remaining_s": {
                    kind: max(0.0, self._header_reset_at[provider].get(kind, 0.0) - now)
                    for kind in ("rpm", "tpm", "itpm", "otpm")
                },
            }
            scope = self._project_scope(probe)
            if scope is not None:
                payload["project_budget"] = {
                    "rpm_used": len(self._project_request_events[scope]),
                    "rpm_limit": self._project_limit(probe, "RPM"),
                    "tpm_used": sum(item[2] for item in self._project_token_events[scope]),
                    "tpm_limit": self._project_limit(probe, "TPM"),
                    "itpm_used": sum(item[2] for item in self._project_input_token_events[scope]),
                    "itpm_limit": self._project_limit(probe, "ITPM"),
                    "otpm_used": sum(item[2] for item in self._project_output_token_events[scope]),
                    "otpm_limit": self._project_limit(probe, "OTPM"),
                }
            return payload

    def reset_for_tests(self) -> None:
        with self._condition:
            self._queue.clear()
            self._inflight.clear()
            self._unenforced_inflight.clear()
            self._user_turn_scopes.clear()
            self._cancelled_scopes.clear()
            self._request_events.clear()
            self._token_events.clear()
            self._input_token_events.clear()
            self._output_token_events.clear()
            self._project_request_events.clear()
            self._project_token_events.clear()
            self._project_input_token_events.clear()
            self._project_output_token_events.clear()
            self._cooldown_until.clear()
            self._dynamic_rpm.clear()
            self._dynamic_tpm.clear()
            self._dynamic_itpm.clear()
            self._dynamic_otpm.clear()
            self._header_remaining.clear()
            self._header_reset_at.clear()
            self._active_by_key.clear()
            self._user_turn_remote_calls.clear()
            self._user_turn_started_at.clear()
            self._user_turn_category.clear()
            self._idempotency_active.clear()
            self._idempotency_tombstones.clear()
            if self._state_store is not None:
                self._state_store.clear()
            self._condition.notify_all()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _model_family(request: InferenceRequest) -> str:
        explicit = str(request.metadata.get("model_family", "") or "").strip().lower()
        if explicit:
            return explicit[:96]
        model = str(request.model or "unknown").strip().lower().split("/")[-1].split(":")[0]
        model = re.sub(r"[^a-z0-9._-]+", "-", model)
        family_patterns = (
            (r"^(qwen3(?:\.\d+)?)(?:[-_].*)?$", r"\1"),
            (r"^(llama[-_]?3(?:\.\d+)?)(?:[-_].*)?$", r"\1"),
            (r"^(gemini[-_]\d+(?:\.\d+)?)(?:[-_].*)?$", r"\1"),
            (r"^(gpt[-_]4o)(?:[-_].*)?$", r"\1"),
            (r"^(gpt[-_]5(?:\.\d+)?)(?:[-_].*)?$", r"\1"),
        )
        for pattern, replacement in family_patterns:
            if re.match(pattern, model):
                return re.sub(pattern, replacement, model).replace("_", "-")[:96]
        return model[:96] or "unknown"

    def _concurrency_key(self, request: InferenceRequest) -> tuple[str, str, str, str]:
        return (
            request.provider,
            request.project or "default",
            request.credential_pool or "default",
            self._model_family(request),
        )

    def _concurrency_available_locked(self, request: InferenceRequest) -> bool:
        key = self._concurrency_key(request)
        env = f"ERIS_BROKER_CONCURRENCY_{request.provider.upper().replace('-', '_')}"
        try:
            provider_limit = max(1, int(os.getenv(env, "0") or 0))
        except ValueError:
            provider_limit = 0
        limit = provider_limit or lane_policy(request.lane).max_concurrency
        return self._active_by_key[key] + max(1, request.max_concurrency_cost) <= limit

    def _refund_reservation(self, request_id: str) -> None:
        """Return quota reserved for work cancelled before provider I/O starts."""

        with self._condition:
            for provider in tuple(self._request_events):
                self._request_events[provider] = deque(
                    item for item in self._request_events[provider] if item[1] != request_id
                )
                self._token_events[provider] = deque(
                    item for item in self._token_events[provider] if item[1] != request_id
                )
                self._input_token_events[provider] = deque(
                    item for item in self._input_token_events[provider] if item[1] != request_id
                )
                self._output_token_events[provider] = deque(
                    item for item in self._output_token_events[provider] if item[1] != request_id
                )
            for scope in tuple(self._project_request_events):
                self._project_request_events[scope] = deque(
                    item for item in self._project_request_events[scope] if item[1] != request_id
                )
                self._project_token_events[scope] = deque(
                    item for item in self._project_token_events[scope] if item[1] != request_id
                )
                self._project_input_token_events[scope] = deque(
                    item for item in self._project_input_token_events[scope] if item[1] != request_id
                )
                self._project_output_token_events[scope] = deque(
                    item for item in self._project_output_token_events[scope] if item[1] != request_id
                )
            self._condition.notify_all()

    def _release(self, request_id: str) -> None:
        with self._condition:
            inflight = self._inflight.pop(request_id, None)
            if inflight is not None:
                key = self._concurrency_key(inflight.request)
                self._active_by_key[key] = max(
                    0, self._active_by_key[key] - max(1, inflight.request.max_concurrency_cost)
                )
                self._release_idempotency_locked(
                    inflight.request,
                    provider_started=bool(inflight.provider_started),
                )
            self._condition.notify_all()

    def _sort_queue_locked(self) -> None:
        self._queue.sort(key=lambda item: (item.request.priority, item.sequence))

    def _remove_locked(self, request_id: str) -> None:
        removed = next(
            (item.request for item in self._queue if item.request.request_id == request_id),
            None,
        )
        self._queue[:] = [item for item in self._queue if item.request.request_id != request_id]
        if removed is not None:
            self._record_queue_depth_locked(removed.lane, removed.provider)

    def _record_queue_depth_locked(self, lane: str, provider: str) -> None:
        depth = sum(
            1
            for item in self._queue
            if not item.cancelled_reason
            and item.request.lane == lane
            and item.request.provider == provider
        )
        self._gauge(
            "broker_queue_depth",
            float(depth),
            lane=lane,
            provider=provider,
        )

    def _scope_cancelled_locked(self, scope: str) -> bool:
        return bool(scope and scope in self._cancelled_scopes)

    def _prune_locked(self, provider: str, now: float) -> None:
        self._prune_idempotency_locked(now)
        threshold = now - 60.0
        request_events = self._request_events[provider]
        while request_events and request_events[0][0] < threshold:
            request_events.popleft()
        token_events = self._token_events[provider]
        while token_events and token_events[0][0] < threshold:
            token_events.popleft()
        input_events = self._input_token_events[provider]
        while input_events and input_events[0][0] < threshold:
            input_events.popleft()
        output_events = self._output_token_events[provider]
        while output_events and output_events[0][0] < threshold:
            output_events.popleft()
        for scope in tuple(self._project_request_events):
            if scope[0] != provider:
                continue
            for events in (
                self._project_request_events[scope],
                self._project_token_events[scope],
                self._project_input_token_events[scope],
                self._project_output_token_events[scope],
            ):
                while events and events[0][0] < threshold:
                    events.popleft()
        for kind, reset_at in tuple(self._header_reset_at[provider].items()):
            if reset_at > 0.0 and reset_at <= now:
                self._header_reset_at[provider].pop(kind, None)
                self._header_remaining[provider].pop(kind, None)
        stale_scopes = [scope for scope, ts in self._cancelled_scopes.items() if ts < now - 300.0]
        for scope in stale_scopes:
            self._cancelled_scopes.pop(scope, None)

    @staticmethod
    def _replace_token_event_locked(
        events: deque[tuple[float, str, int]],
        request_id: str,
        value: int,
    ) -> None:
        for index, item in enumerate(events):
            if item[1] == request_id:
                events[index] = (item[0], item[1], max(0, int(value)))
                return

    @classmethod
    def _extract_usage(cls, result: Any) -> tuple[int, int] | None:
        """Read OpenAI/Groq/Gemini usage from dicts, SDK objects or HTTP responses."""

        payload: Any = result
        if hasattr(result, "json") and callable(getattr(result, "json")):
            try:
                payload = result.json()
            except Exception:
                payload = result

        if isinstance(payload, Mapping):
            usage = payload.get("usage")
            if isinstance(usage, Mapping):
                prompt = cls._usage_int(
                    usage,
                    "prompt_tokens",
                    "input_tokens",
                    "promptTokenCount",
                )
                completion = cls._usage_int(
                    usage,
                    "completion_tokens",
                    "output_tokens",
                    "candidatesTokenCount",
                )
                if prompt is not None or completion is not None:
                    return max(0, prompt or 0), max(0, completion or 0)
            usage_meta = payload.get("usageMetadata") or payload.get("usage_metadata")
            if isinstance(usage_meta, Mapping):
                prompt = cls._usage_int(usage_meta, "promptTokenCount", "prompt_tokens", "input_tokens")
                completion = cls._usage_int(
                    usage_meta,
                    "candidatesTokenCount",
                    "completion_tokens",
                    "output_tokens",
                )
                if prompt is not None or completion is not None:
                    return max(0, prompt or 0), max(0, completion or 0)

        for attr in ("usage", "usage_metadata", "usageMetadata"):
            usage_obj = getattr(payload, attr, None)
            if usage_obj is None:
                continue
            prompt = cls._object_usage_int(
                usage_obj,
                "prompt_tokens",
                "input_tokens",
                "prompt_token_count",
                "promptTokenCount",
            )
            completion = cls._object_usage_int(
                usage_obj,
                "completion_tokens",
                "output_tokens",
                "candidates_token_count",
                "candidatesTokenCount",
            )
            if prompt is not None or completion is not None:
                return max(0, prompt or 0), max(0, completion or 0)
        return None

    @staticmethod
    def _usage_int(payload: Mapping[str, Any], *keys: str) -> int | None:
        for key in keys:
            if key not in payload:
                continue
            try:
                return int(payload[key])
            except (TypeError, ValueError):
                continue
        return None

    @staticmethod
    def _object_usage_int(payload: Any, *keys: str) -> int | None:
        for key in keys:
            if not hasattr(payload, key):
                continue
            try:
                return int(getattr(payload, key))
            except (TypeError, ValueError):
                continue
        return None

    def _active_header_remaining_locked(
        self, provider: str, kind: str, now: float
    ) -> int | None:
        reset_at = self._header_reset_at[provider].get(kind, 0.0)
        if reset_at and reset_at <= now:
            self._header_reset_at[provider].pop(kind, None)
            self._header_remaining[provider].pop(kind, None)
            return None
        return self._header_remaining[provider].get(kind)

    @staticmethod
    def _first_nonnegative_int(headers: Mapping[str, str], *keys: str) -> int | None:
        for key in keys:
            raw = headers.get(key, "")
            try:
                value = int(float(raw))
            except (TypeError, ValueError):
                continue
            if value >= 0:
                return value
        return None

    @classmethod
    def _first_reset_seconds(
        cls, headers: Mapping[str, str], now: float, *keys: str
    ) -> float | None:
        for key in keys:
            parsed = cls._parse_reset_seconds(headers.get(key, ""), now=now)
            if parsed is not None:
                return parsed
        return None

    @staticmethod
    def _parse_reset_seconds(raw: Any, *, now: float | None = None) -> float | None:
        text = str(raw or "").strip().lower()
        if not text:
            return None
        current = time.time() if now is None else float(now)
        # Groq/OpenAI commonly return values such as 1.5s, 2m59.2s or 120ms.
        pieces = re.findall(r"([0-9]+(?:\.[0-9]+)?)(ms|s|m|h|d)", text)
        if pieces:
            scale = {"ms": 0.001, "s": 1.0, "m": 60.0, "h": 3600.0, "d": 86400.0}
            return max(0.0, min(86400.0, sum(float(value) * scale[unit] for value, unit in pieces)))
        try:
            numeric = float(text)
            if numeric > current + 1.0:
                return max(0.0, min(86400.0, numeric - current))
            return max(0.0, min(86400.0, numeric))
        except ValueError:
            pass
        try:
            parsed = parsedate_to_datetime(str(raw))
            return max(0.0, min(86400.0, parsed.timestamp() - current))
        except (TypeError, ValueError, OverflowError):
            return None

    @staticmethod
    def _first_int(headers: Mapping[str, str], *keys: str) -> int | None:
        for key in keys:
            raw = headers.get(key, "")
            try:
                value = int(float(raw))
            except (TypeError, ValueError):
                continue
            if value > 0:
                return value
        return None

    @staticmethod
    def _first_float(headers: Mapping[str, str], *keys: str) -> float | None:
        for key in keys:
            raw = headers.get(key, "")
            try:
                return float(raw)
            except (TypeError, ValueError):
                continue
        return None

    @staticmethod
    def _retry_after_from_exception(exc: Exception) -> float:
        response = getattr(exc, "response", None)
        try:
            raw = str((getattr(response, "headers", {}) or {}).get("Retry-After", "") or "").strip()
            if raw:
                return max(1.0, float(raw))
        except (TypeError, ValueError):
            pass
        return 20.0

    @staticmethod
    def _raise_cancelled(request: InferenceRequest) -> None:
        raise InferenceRejected("cancelled_before_provider_call", request)

    def _record_admitted(self, request: InferenceRequest, queue_wait_s: float) -> None:
        self._metric("broker_admitted_total", lane=request.lane, provider=request.provider)
        self._metric("broker_queue_wait_ms", lane=request.lane, provider=request.provider, value=queue_wait_s * 1000.0)

    def _record_rejection(self, request: InferenceRequest, reason: str) -> None:
        self._metric("broker_rejected_total", lane=request.lane, provider=request.provider, reason=reason)

    def _record_completion(self, request: InferenceRequest, state: str, duration_ms: float) -> None:
        self._metric("broker_request_duration_ms", lane=request.lane, provider=request.provider, reason=state, value=duration_ms)

    @staticmethod
    def _gauge(
        name: str,
        value: float,
        *,
        lane: str = "",
        provider: str = "",
        reason: str = "",
    ) -> None:
        try:
            from runtime.observability import GLOBAL_OBSERVABILITY

            tags = {
                key: val
                for key, val in {
                    "lane": lane,
                    "provider": provider,
                    "reason": reason,
                }.items()
                if val
            }
            GLOBAL_OBSERVABILITY.gauge(name, float(value), tags=tags)
        except Exception:
            pass

    @staticmethod
    def _metric(name: str, *, lane: str = "", provider: str = "", reason: str = "", value: float = 1.0) -> None:
        try:
            from runtime.observability import GLOBAL_OBSERVABILITY

            tags = {key: val for key, val in {"lane": lane, "provider": provider, "reason": reason}.items() if val}
            if name.endswith("_ms"):
                GLOBAL_OBSERVABILITY.observe(name, value, tags=tags)
            else:
                GLOBAL_OBSERVABILITY.increment(name, value=value, tags=tags)
        except Exception:
            pass


GLOBAL_INFERENCE_BROKER = InferenceBroker()


__all__ = [
    "AdmissionDecision",
    "CancellationToken",
    "GLOBAL_INFERENCE_BROKER",
    "InferenceBroker",
    "InferenceContext",
    "InferenceRejected",
    "InferenceRequest",
    "LANE_POLICIES",
    "current_inference_context",
    "inference_context",
    "lane_policy",
    "broker_rollout_for_request",
]
