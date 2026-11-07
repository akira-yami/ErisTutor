# -*- coding: utf-8 -*-
"""Post-turn observer. Không có pre-turn social gate.

Observer là tác vụ nền và mỗi role DeepSeek chỉ có một account/slot vật lý.
Vì vậy module này dùng mô hình actor đơn giản:

* tối đa một request observer đang chạy;
* nếu có nhiều lượt mới trong lúc request cũ chạy, chỉ giữ lượt mới nhất;
* request cũ có thể chưa dừng ngay ở socket, nhưng kết quả của nó không được commit;
* request mới nhất sẽ chạy ngay sau khi request đang giữ account kết thúc.

Thiết kế này tránh trường hợp observer cũ bị discard nhưng observer mới lại đập vào
role lock và chết với ``RoleWorkerBusyError``.
"""

from __future__ import annotations

import logging
import os
import threading
import time
import uuid
from collections import deque
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, MutableMapping, Sequence

from .contracts import CognitiveObservation, StateEvent


_LOG = logging.getLogger(__name__)


@dataclass(slots=True)
class ObservationTicket:
    ticket_id: str
    turn_id: str
    turn_seq: int
    user_text: str
    assistant_text: str
    event_kind: str
    submitted_at: float
    # ``future`` là promise của ticket, không phải Future trực tiếp của executor.
    # Nhờ vậy ticket pending có thể nằm trong actor mà chưa chiếm thread/role lock.
    future: Future[CognitiveObservation]
    user_meta: MutableMapping[str, Any] | None = None
    assistant_meta: MutableMapping[str, Any] | None = None
    absolute_expiry_at: float = 0.0
    cancel_scope: str = ""
    _runner: Callable[[], CognitiveObservation] | None = field(default=None, repr=False)
    _runner_future: Future[CognitiveObservation] | None = field(default=None, repr=False)
    _state: str = field(default="pending", repr=False)
    _started_at: float = field(default=0.0, repr=False)
    _inference_started_at: float = field(default=0.0, repr=False)
    _claimed: bool = False
    _timer: threading.Timer | None = field(default=None, repr=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def claim(self) -> bool:
        """Giành quyền hoàn tất/discard ticket đúng một lần."""

        with self._lock:
            if self._claimed:
                return False
            self._claimed = True
            return True

    @property
    def claimed(self) -> bool:
        with self._lock:
            return bool(self._claimed)


class PostTurnObserver:
    def __init__(self, router: Any, reducer: Any, *, logger: Any | None = None, workers: int = 2) -> None:
        self.router = router
        self.reducer = reducer
        self.logger = logger

        # Observer account chỉ cho một inference cùng lúc. Dùng một executor worker
        # để phản ánh đúng tài nguyên thật thay vì tạo nhiều Future cùng tranh lock.
        # Tham số ``workers`` vẫn được nhận để không phá API cấu hình cũ.
        _ = workers
        self.executor = ThreadPoolExecutor(
            max_workers=1,
            thread_name_prefix="eris-post-turn-observer",
        )
        self._tickets: dict[str, ObservationTicket] = {}
        self._pending: deque[ObservationTicket] = deque()
        self._inflight: ObservationTicket | None = None
        self._lock = threading.RLock()

        try:
            self.deadline_seconds = max(
                1.0,
                min(300.0, float(os.getenv("ERIS_POST_OBSERVER_DEADLINE_SEC", "120.0") or 120.0)),
            )
        except (TypeError, ValueError):
            self.deadline_seconds = 120.0
        try:
            self.idle_wait_seconds = max(
                0.0,
                min(30.0, float(os.getenv("ERIS_POST_OBSERVER_IDLE_WAIT_SEC", "4.0") or 4.0)),
            )
        except (TypeError, ValueError):
            self.idle_wait_seconds = 4.0
        try:
            self.inference_timeout_seconds = max(
                1.0,
                min(120.0, float(os.getenv("ERIS_POST_OBSERVER_HTTP_TIMEOUT_SEC", "90.0") or 90.0)),
            )
        except (TypeError, ValueError):
            self.inference_timeout_seconds = 90.0
        try:
            self.latest_wins = str(os.getenv("ERIS_POST_OBSERVER_LATEST_WINS", "1") or "1").strip().lower() not in {
                "0",
                "false",
                "off",
                "no",
            }
        except Exception:
            self.latest_wins = True

        # Chừa một khoảng nhỏ để broker/provider kịp trả lỗi có cấu trúc trước TTL.
        try:
            self.deadline_safety_seconds = max(
                0.05,
                min(2.0, float(os.getenv("ERIS_POST_OBSERVER_DEADLINE_SAFETY_SEC", "0.35") or 0.35)),
            )
        except (TypeError, ValueError):
            self.deadline_safety_seconds = 0.35

    def submit(
        self,
        *,
        turn_id: str,
        turn_seq: int,
        user_text: str,
        assistant_text: str,
        event_kind: str = "chat",
        recent_history: Sequence[Mapping[str, Any] | str] = (),
        mechanical_outcomes: Sequence[Mapping[str, Any]] = (),
        state_summary: Mapping[str, Any] | None = None,
        user_meta: MutableMapping[str, Any] | None = None,
        assistant_meta: MutableMapping[str, Any] | None = None,
    ) -> ObservationTicket:
        ticket_id = f"observe-{uuid.uuid4().hex[:12]}"
        submitted_at = time.time()
        cancel_scope = f"background:post_observer:{turn_id}"
        absolute_expiry_at = submitted_at + self.deadline_seconds

        # latest-wins: đánh dấu toàn bộ ticket cũ là superseded. Request đang chạy
        # có thể còn sống tới HTTP timeout, nhưng actor vẫn chỉ giữ đúng một pending.
        if self.latest_wins:
            self._discard_existing("superseded_by_latest")

        promise: Future[CognitiveObservation] = Future()
        ticket = ObservationTicket(
            ticket_id=ticket_id,
            turn_id=turn_id,
            turn_seq=turn_seq,
            user_text=user_text,
            assistant_text=assistant_text,
            event_kind=str(event_kind or "chat"),
            submitted_at=submitted_at,
            future=promise,
            user_meta=user_meta,
            assistant_meta=assistant_meta,
            absolute_expiry_at=absolute_expiry_at,
            cancel_scope=cancel_scope,
        )

        def observe_when_idle() -> CognitiveObservation:
            """Chờ idle rồi cấp ngân sách inference tính từ lúc chạy thật."""

            from runtime.inference_broker import (
                GLOBAL_INFERENCE_BROKER,
                InferenceRejected,
                inference_context,
            )

            now = time.time()
            remaining_total = ticket.absolute_expiry_at - now
            if remaining_total <= self.deadline_safety_seconds:
                raise InferenceRejected("post_observer_expired_before_start")

            # Thời gian chờ executor/actor không bị giả vờ là HTTP inference.
            # Tuy nhiên ticket vẫn có TTL tổng để một quan sát quá cũ không sống mãi.
            idle_budget = min(
                self.idle_wait_seconds,
                max(0.0, remaining_total - self.deadline_safety_seconds),
            )
            if not GLOBAL_INFERENCE_BROKER.wait_for_user_idle(idle_budget):
                raise InferenceRejected("post_observer_user_turn_deadline")

            inference_started_at = time.time()
            ticket._inference_started_at = inference_started_at
            remaining_total = ticket.absolute_expiry_at - inference_started_at
            effective_timeout = min(
                self.inference_timeout_seconds,
                max(0.0, remaining_total - self.deadline_safety_seconds),
            )
            if effective_timeout < 0.5:
                raise InferenceRejected("post_observer_expired_before_inference")

            with inference_context(
                "post_observer",
                optional=True,
                deadline_at=inference_started_at + effective_timeout,
                cancel_scope=cancel_scope,
                coalesce_key=f"post_observer:{turn_id}",
                idempotency_key=f"post_observer:{turn_id}",
                metadata={
                    "turn_seq": turn_seq,
                    "event_kind": event_kind,
                    "idle_wait_timeout_s": self.idle_wait_seconds,
                    "inference_timeout_s": effective_timeout,
                    "queue_wait_ms": max(0.0, (inference_started_at - submitted_at) * 1000.0),
                },
            ):
                return self.router.observe_completed_turn(
                    turn_id=turn_id,
                    turn_seq=turn_seq,
                    user_text=user_text,
                    assistant_text=assistant_text,
                    recent_history=recent_history,
                    mechanical_outcomes=mechanical_outcomes,
                    state_summary=state_summary,
                    event_kind=event_kind,
                    timeout_seconds=effective_timeout,
                )

        ticket._runner = observe_when_idle
        if ticket.user_meta is not None:
            ticket.user_meta["cognitive_observation_pending"] = True

        # Gắn timer trước khi submit executor để request cực nhanh không thể hoàn
        # tất rồi mới bị gắn một timer mồ côi sống thêm tới hết deadline.
        timer_delay = max(0.1, absolute_expiry_at - time.time())
        timer = threading.Timer(timer_delay, self._expire, args=(ticket,))
        timer.daemon = True
        ticket._timer = timer

        with self._lock:
            self._tickets[ticket_id] = ticket
            timer.start()
            if self._inflight is None:
                self._start_ticket_locked(ticket)
            else:
                # Khi latest_wins=False vẫn giữ FIFO, nhưng mặc định chỉ có một
                # pending mới nhất vì các ticket cũ đã được discard ở trên.
                self._pending.append(ticket)
                ticket._state = "pending"
        return ticket

    def _start_ticket_locked(self, ticket: ObservationTicket) -> None:
        """Bắt đầu ticket khi actor rảnh. Caller phải giữ ``self._lock``."""

        if ticket.claimed or ticket._runner is None:
            return
        if time.time() >= ticket.absolute_expiry_at:
            # Timer có thể chưa kịp chạy đúng millisecond. Không gửi request cũ.
            threading.Thread(
                target=self._discard_ticket,
                args=(ticket, "deadline_before_start"),
                daemon=True,
            ).start()
            return
        self._inflight = ticket
        ticket._state = "running"
        ticket._started_at = time.time()
        try:
            runner_future = self.executor.submit(ticket._runner)
        except Exception as exc:
            self._inflight = None
            self._finalize_failure(ticket, exc)
            self._start_next_locked()
            return
        ticket._runner_future = runner_future
        runner_future.add_done_callback(lambda done: self._runner_finished(ticket, done))

    def _start_next_locked(self) -> None:
        """Promote pending hợp lệ mới nhất/FIFO sau khi request trước kết thúc."""

        if self._inflight is not None:
            return
        while self._pending:
            candidate = self._pending.popleft()
            if candidate.claimed:
                continue
            if time.time() >= candidate.absolute_expiry_at:
                # Không chạy ticket hết hạn. Timer/nhánh discard sẽ hoàn tất metadata.
                self._discard_ticket(candidate, "deadline_before_start")
                continue
            self._start_ticket_locked(candidate)
            return

    def _runner_finished(
        self,
        ticket: ObservationTicket,
        runner_future: Future[CognitiveObservation],
    ) -> None:
        """Hoàn tất ticket rồi nhường actor cho pending mới nhất."""

        try:
            observation = runner_future.result()
        except Exception as exc:  # noqa: BLE001
            self._finalize_failure(ticket, exc)
        else:
            self._finalize_success(ticket, observation)
        finally:
            with self._lock:
                if self._inflight is ticket:
                    self._inflight = None
                self._start_next_locked()

    def _finalize_success(self, ticket: ObservationTicket, observation: CognitiveObservation) -> None:
        if not ticket.claim():
            # Ticket đã supersede/timeout; tuyệt đối không commit kết quả muộn.
            return
        self._detach_ticket(ticket)
        ticket._state = "completed"
        # Future chỉ được hoàn tất SAU khi event (nếu có) đã được gửi tới
        # reducer, để caller chờ future không đọc nhầm trạng thái giữa chừng.
        status = str(getattr(observation, "observation_status", "success") or "success")
        payload = observation.to_dict()
        if ticket.user_meta is not None:
            ticket.user_meta["cognitive_observation"] = payload
            ticket.user_meta["cognitive_observation_pending"] = False
            ticket.user_meta["cognitive_observation_status"] = status
        if ticket.assistant_meta is not None:
            ticket.assistant_meta["cognitive_observation_source"] = observation.source
        # unknown thật là kết quả model hợp lệ, nhưng không phải evidence để
        # state học thêm. Không tự tạo sự kiện neutral có thể xóa state trước.
        if status == "success":
            from memory.claim_provenance import classify_ingress
            event = StateEvent.from_observation(
            observation, base_version=self.reducer.snapshot().version,
        )
            # Không mượn origin=user của lời assistant hoặc bản tóm tắt observer.
            event.payload["claim_provenance"] = classify_ingress(
                "ai_post_turn_observer", turn_id=ticket.turn_id,
            )
            self.reducer.submit(event)
        try:
            from runtime.observability import GLOBAL_OBSERVABILITY

            GLOBAL_OBSERVABILITY.increment(
                "observer_completed_total", tags={"event_kind": ticket.event_kind, "status": status}
            )
            GLOBAL_OBSERVABILITY.observe(
                "observer_latency_ms",
                max(0.0, (time.time() - ticket.submitted_at) * 1000.0),
                tags={"event_kind": ticket.event_kind},
            )
        except Exception as exc:
            self._log("debug", "post-observer metric emission failed: %s", exc)
        if not ticket.future.done():
            ticket.future.set_result(observation)
        self._log(
            "info",
            "[post observer] turn=%s status=%s act=%s meaning=%s conf=%.2f repair=%s traits=%s frame=%s strategy=%s source=%s latency=%.0fms",
            observation.turn_seq,
            status,
            observation.user_act,
            observation.relationship_meaning,
            observation.confidence,
            observation.repair_needed,
            len(observation.trait_signals),
            observation.self_interpretation.frame,
            observation.consequence.strategy or "none",
            observation.source,
            observation.latency_ms,
        )

    def _finalize_failure(self, ticket: ObservationTicket, exc: Exception) -> None:
        if not ticket.claim():
            return
        self._detach_ticket(ticket)
        ticket._state = "failed"
        # Future chỉ báo lỗi sau khi metadata trạng thái đã được cập nhật.
        # Không đưa message của provider ra log: có thể chứa API key/prompt.
        from .model_router import ObserverInferenceError
        status = exc.status if isinstance(exc, ObserverInferenceError) else (
            "timeout" if "timeout" in type(exc).__name__.lower() else "provider_error"
        )
        if ticket.user_meta is not None:
            ticket.user_meta["cognitive_observation_pending"] = False
            ticket.user_meta["cognitive_observation_failed"] = True
            ticket.user_meta["cognitive_observation_status"] = status
        if ticket.assistant_meta is not None:
            ticket.assistant_meta["cognitive_observation_source"] = status
        try:
            from runtime.observability import GLOBAL_OBSERVABILITY
            GLOBAL_OBSERVABILITY.increment("observer_failure_total", tags={"event_kind": ticket.event_kind, "status": status})
        except Exception:
            pass
        if not ticket.future.done():
            ticket.future.set_exception(exc)
        self._log("warning", "[post observer] failed turn=%s status=%s error_type=%s", ticket.turn_seq, status, type(exc).__name__)

    def _detach_ticket(self, ticket: ObservationTicket) -> None:
        with self._lock:
            self._tickets.pop(ticket.ticket_id, None)
            try:
                self._pending.remove(ticket)
            except ValueError:
                pass
        if ticket._timer is not None:
            ticket._timer.cancel()

    def _expire(self, ticket: ObservationTicket) -> None:
        self._discard_ticket(ticket, "deadline")

    def discard_pending(self, reason: str = "discarded") -> None:
        """Discard cả pending và in-flight result khi user turn mới preempt nền."""

        self._discard_existing(str(reason or "discarded"))

    def _discard_existing(self, reason: str) -> None:
        with self._lock:
            tickets = tuple(self._tickets.values())
        for ticket in tickets:
            self._discard_ticket(ticket, reason)

    def _discard_ticket(self, ticket: ObservationTicket, reason: str) -> None:
        if not ticket.claim():
            return
        self._detach_ticket(ticket)
        ticket._state = "discarded"

        try:
            from runtime.inference_broker import GLOBAL_INFERENCE_BROKER

            GLOBAL_INFERENCE_BROKER.cancel_scope(ticket.cancel_scope)
        except Exception as exc:
            self._log("debug", "post-observer broker cancellation failed: %s", exc)

        # ``Future.cancel`` chỉ dừng được task chưa bắt đầu. Nếu HTTP đã chạy,
        # requests sẽ kết thúc theo timeout/socket close; kết quả về sau bị claim chặn.
        was_running = False
        cancelled_before_start = False
        runner_future = ticket._runner_future
        if runner_future is not None:
            try:
                was_running = runner_future.running()
                cancelled_before_start = bool(runner_future.cancel())
            except Exception as exc:
                self._log("debug", "post-observer runner cancellation failed: %s", exc)
        if not ticket.future.done():
            ticket.future.cancel()

        try:
            from runtime.observability import GLOBAL_OBSERVABILITY

            stage = "before_start" if cancelled_before_start and not was_running else "inflight_or_started"
            GLOBAL_OBSERVABILITY.increment(
                "observer_discard_total",
                tags={
                    "stage": stage,
                    "event_kind": ticket.event_kind,
                    "reason": str(reason or "discarded"),
                },
            )
        except Exception as exc:
            self._log("debug", "post-observer discard metric failed: %s", exc)

        if ticket.user_meta is not None:
            ticket.user_meta["cognitive_observation_pending"] = False
            if str(reason).startswith("deadline"):
                ticket.user_meta["cognitive_observation_timeout"] = True
            else:
                ticket.user_meta["cognitive_observation_superseded"] = True
        if ticket.assistant_meta is not None:
            ticket.assistant_meta["cognitive_observation_source"] = (
                "timeout_discarded" if str(reason).startswith("deadline") else "superseded_discarded"
            )

        if str(reason).startswith("deadline"):
            self._log(
                "warning",
                "[post observer] deadline turn=%s state=%s queue=%.0fms inference=%.0fms total<=%.1fs; result discarded",
                ticket.turn_seq,
                ticket._state,
                max(0.0, (ticket._started_at - ticket.submitted_at) * 1000.0) if ticket._started_at else 0.0,
                max(0.0, (time.time() - ticket._inference_started_at) * 1000.0)
                if ticket._inference_started_at
                else 0.0,
                self.deadline_seconds,
            )
        else:
            self._log(
                "debug",
                "[post observer] discarded turn=%s reason=%s state=%s",
                ticket.turn_seq,
                reason,
                ticket._state,
            )

        # Nếu ticket bị discard khi còn pending và actor đang rảnh, promote ngay.
        with self._lock:
            if self._inflight is None:
                self._start_next_locked()

    def shutdown(self) -> None:
        with self._lock:
            tickets = tuple(self._tickets.values())
        for ticket in tickets:
            self._discard_ticket(ticket, "shutdown")
        try:
            self.executor.shutdown(wait=False, cancel_futures=True)
        except Exception as exc:
            self._log("debug", "post-observer executor shutdown failed: %s", exc)

    def _log(self, level: str, message: str, *args: Any) -> None:
        if self.logger is None:
            return
        try:
            getattr(self.logger, level)(message, *args)
        except Exception as exc:
            _LOG.debug("post-observer logger failed: %s", exc, exc_info=exc)


__all__ = ["ObservationTicket", "PostTurnObserver"]
