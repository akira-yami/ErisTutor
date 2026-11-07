# -*- coding: utf-8 -*-
"""ConversationKernel V12 — lifecycle/concurrency owner, không phải conversation planner."""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from contextlib import contextmanager
import re
import threading
import time
import uuid
from typing import Any, Mapping, Sequence

from .agent import RootAgent
from .contracts import (
    ConversationAfter,
    ConversationEvent,
    ConversationSessionState,
    DeliveryReceipt,
    NextConversationMove,
    NoReply,
    RunPhase,
)
from .continuation import ContinuationLeaseScheduler
from .context import ConversationNoteTools
from .delivery import ConversationDelivery
from .receipts import DeliveryReceiptLedger
from .move_outcomes import MoveOutcomeLedger
from .session_store import ConversationSessionStore
from .writer import ConversationWriter, RenderedConversationMove
from .delivery import SurfaceEnvelope
from llm.provider_errors import ProviderUnavailable, provider_failure


@dataclass(frozen=True, slots=True)
class KernelTurn:
    session_id: str
    generation: int
    run_id: str
    move: NextConversationMove
    note: str
    continuation: bool = False
    # Metadata cơ học của continuation chain. Writer không nhìn thấy hai field
    # này; kernel chỉ truyền lại scheduler để chống self-wake vô hạn.
    continuation_chain_started_at: float = 0.0
    consecutive_self_wakes: int = 0
    # Cấp trước writer để receipt và TTS dùng một identity bất biến.
    delivery_id: str = ""
    focus_epoch: int = 0
    canvas_scope: Mapping[str, Any] = field(default_factory=dict)

    def writer_context(self) -> dict[str, Any]:
        return {
            "schema": "eris.conversation_writer_context.v12",
            "session_id": self.session_id,
            "generation": self.generation,
            "run_id": self.run_id,
            "move": self.move.to_dict(),
        }


@dataclass(frozen=True, slots=True)
class ConversationRunResult:
    event: ConversationEvent
    decision: NoReply | NextConversationMove | None
    turn: KernelTurn | None = None
    rendered: RenderedConversationMove | None = None
    delivery_id: str = ""
    status: str = "idle"
    # Chỉ chứa reason code đã lọc; không serialize prompt, response hoặc traceback.
    failure_reason: str = ""


def _safe_failure_reason(exc: Exception) -> str:
    """Bóc mã lỗi để nhìn thấy nguyên nhân mà không ghi dữ liệu người dùng.

    V0.3 trả mã lỗi từ các stage (generate/manifest/prepare/verify/receipt).
    Ngoại lệ provider không thuộc mã đã biết chỉ ghi tên exception class.
    """
    raw = str(exc).strip()
    if (len(raw) <= 480 and raw.startswith('v03_candidate_rejected:')
            and re.fullmatch(r'[a-zA-Z0-9_:;,=]+', raw)):
        return raw
    if (len(raw) <= 150 and re.fullmatch(r'(?:v03_|public_writer_|character_portrait_)[a-z0-9_]+', raw)):
        return raw
    return type(exc).__name__


def _record_run_failure(*, status: str, reason: str, run_id: str,
                        generation: int) -> None:
    """JSONL structured event phục vụ tra lỗi sau restart; không ghi lời chat."""
    try:
        from runtime.observability import GLOBAL_OBSERVABILITY
        GLOBAL_OBSERVABILITY.log(
            'conversation_run_failed',
            tags={'status': status, 'reason': reason[:120]},
            fields={'run_id': run_id, 'generation': generation,
                    'failure_reason': reason},
            status='degraded',
        )
    except Exception:
        pass


class ConversationKernel:
    def __init__(self, agent: RootAgent, *, store: ConversationSessionStore | None = None,
                 continuation: ContinuationLeaseScheduler | None = None,
                 receipts: DeliveryReceiptLedger | None = None,
                 outcomes: MoveOutcomeLedger | None = None,
                 writer: ConversationWriter | None = None,
                 delivery: ConversationDelivery | None = None,
                 logger: Any = None) -> None:
        self.agent = agent
        self.store = store or ConversationSessionStore(logger=logger)
        self.continuation = continuation or ContinuationLeaseScheduler(logger=logger)
        self.receipts = receipts or DeliveryReceiptLedger(logger=logger)
        self.outcomes = outcomes or MoveOutcomeLedger(logger=logger)
        self.writer = writer
        self.delivery = delivery
        self.logger = logger
        self._delivery_lock = threading.RLock()
        self._delivery_turns: dict[str, KernelTurn] = {}
        # Callback được orchestrator cung cấp, chỉ chạy SAU public delivery receipt.
        self.speculative_preparer: Any = None
        self._event_condition = threading.Condition(threading.RLock())
        self._event_results: dict[str, ConversationRunResult] = {}
        self._events_inflight: set[str] = set()
        self._recovered_outcome_sessions: set[str] = set()
        self._outcome_recovery_lock = threading.RLock()
        self._focus_lock = threading.RLock()
        self._focus_epochs: dict[str, int] = {}
        self._canvas_run_scopes: dict[str, dict] = {}
        self._canvas_lease_scopes: dict[str, dict] = {}
        self.on_settled: Any = None
        self.focus_scope_is_current: Any = None

    def _scope_current(self, run_id):
        scope = self._canvas_run_scopes.get(run_id, {})
        return not scope or not callable(self.focus_scope_is_current) or bool(self.focus_scope_is_current(scope))

    def bind_canvas_scope(self, session_id: str, scope: Mapping[str, Any], *, run_id="") -> None:
        with self._focus_lock:
            state = self.store.load(session_id)
            if state.active_run_id and (not run_id or state.active_run_id == run_id):
                self._canvas_run_scopes[state.active_run_id] = dict(scope)
                while len(self._canvas_run_scopes) > 512:
                    self._canvas_run_scopes.pop(next(iter(self._canvas_run_scopes)))

    def canvas_scope(self, session_id: str) -> dict:
        with self._focus_lock:
            return dict(self._canvas_run_scopes.get(self.store.load(session_id).active_run_id, {}))

    def note_focus_change(self, session_id: str) -> ConversationSessionState:
        """UI steering: keep committed delivery, invalidate uncommitted run only.

        Never change conversation generation, discard audio or create a user turn.
        The epoch also prevents an old delivery from leasing a continuation later.
        """
        with self._focus_lock:
            self._focus_epochs[session_id] = self._focus_epochs.get(session_id, 0) + 1
            self.continuation.cancel_session(session_id)
            self._canvas_lease_scopes.pop(session_id, None)
            return self.store.mutate(session_id, lambda prior, now: replace(
                prior, active_run_id="", active_move_id="", active_delivery_id="", phase=RunPhase.WAITING_USER,
            ) if prior.phase in {RunPhase.THINKING, RunPhase.WORKING, RunPhase.WRITING, RunPhase.CONTINUATION_PENDING}
            else prior)

    @contextmanager
    def focus_change_guard(self, session_id):
        # The UI cursor and delivery handoff have one linearization point.
        with self._focus_lock:
            yield
            self.note_focus_change(session_id)

    def handle_event(
        self,
        event: ConversationEvent,
        *,
        recent_history: Sequence[Mapping[str, Any]] = (),
        runtime_context: Mapping[str, Any] | None = None,
    ) -> ConversationRunResult:
        event_id = str(event.event_id or "")[:240]
        if not event_id:
            return self._handle_event_once(
                event,
                recent_history=recent_history,
                runtime_context=runtime_context,
            )
        with self._event_condition:
            while event_id in self._events_inflight:
                self._event_condition.wait()
            cached = self._event_results.get(event_id)
            if cached is not None:
                return cached
            self._events_inflight.add(event_id)
        try:
            result = self._handle_event_once(
                event,
                recent_history=recent_history,
                runtime_context=runtime_context,
            )
        except Exception:
            with self._event_condition:
                self._events_inflight.discard(event_id)
                self._event_condition.notify_all()
            raise
        with self._event_condition:
            if len(self._event_results) >= 512:
                self._event_results.pop(next(iter(self._event_results)))
            if result.status != "busy":
                self._event_results[event_id] = result
            self._events_inflight.discard(event_id)
            self._event_condition.notify_all()
        return result

    def _handle_event_once(
        self,
        event: ConversationEvent,
        *,
        recent_history: Sequence[Mapping[str, Any]] = (),
        runtime_context: Mapping[str, Any] | None = None,
        prepare_only: bool = False,
    ) -> ConversationRunResult:
        sid = str(event.session_id or "default")[:180]
        run_id = f"run:{uuid.uuid4().hex}"
        scope = (runtime_context or {}).get("__canvas_scope", {}) or (
            self._canvas_lease_scopes.get(sid, {}) if event.kind == "continuation_due" else {})
        if scope and callable(self.focus_scope_is_current) and not self.focus_scope_is_current(scope):
            return ConversationRunResult(event=event, decision=None, status="stale_focus")
        if event.kind == "user_turn":
            # Đổi generation TRƯỚC: callback TTS cũ tới đồng thời sẽ bị loại
            # ngay cả khi chưa kịp lấy delivery lock để discard.
            state = self.store.mutate(
                sid,
                lambda prior, now: replace(
                    prior,
                    generation=prior.generation + 1,
                    active_run_id=run_id,
                    active_move_id="",
                    active_delivery_id="",
                    phase=RunPhase.THINKING,
                ),
            )
            self.continuation.cancel_session(sid)
            self._discard_session_deliveries(sid)
        else:
            state = self.store.load(sid)
            if event.generation not in {0, state.generation}:
                return ConversationRunResult(event=event, decision=None, status="stale")
            # P1: các signal nền không có quyền chiếm active_run của một Root
            # đang suy nghĩ hay một câu đang phát. Chốt này phải ở Kernel, vì
            # pre-check ngoài orchestrator không atomic khi nhiều thread đua.
            busy_phases = {RunPhase.THINKING, RunPhase.WORKING, RunPhase.WRITING, RunPhase.DELIVERING}
            if state.phase in busy_phases:
                return ConversationRunResult(event=event, decision=None, status="busy")

            def claim_idle_run(prior: ConversationSessionState, now: float) -> ConversationSessionState:
                # Recheck bên trong store.mutate/transaction: không ghi đè run
                # của user hoặc một background event vừa claim trước mình.
                if prior.generation != state.generation or prior.phase in busy_phases:
                    return prior
                return replace(prior, active_run_id=run_id, active_move_id="",
                               active_delivery_id="", phase=RunPhase.THINKING)

            state = self.store.mutate(sid, claim_idle_run)
            if state.active_run_id != run_id:
                return ConversationRunResult(
                    event=event, decision=None,
                    status="stale" if event.generation not in {0, state.generation} else "busy",
                )

        # Hai background move liên tiếp cùng lỗi trong 90s => circuit breaker
        # cơ học, không chạy Root/Writer thêm theo timer. User turn vẫn tự do.
        if event.kind != 'user_turn' and self._background_failure_cooldown(sid):
            self._fail_current_run(sid, state.generation, run_id)
            return ConversationRunResult(event=event, decision=None, status='background_failure_cooldown')

        normalized_event = replace(event, session_id=sid, generation=state.generation)
        with self._focus_lock:
            focus_epoch = self._focus_epochs.get(sid, 0)
            if isinstance(scope, Mapping) and scope:
                self.bind_canvas_scope(sid, scope, run_id=run_id)
        agent_context = self._agent_runtime_context(sid, runtime_context)
        # Execution token được kernel cấp, không cho client/model tự chỉ định.
        agent_context["__conversation_run_id"] = run_id
        # Sau khi user chen vào, Root còn đang đợi DeepSeek/tool sẽ không được
        # tiếp tục làm hành động của run cũ. Callback này không vào model payload.
        def root_run_active() -> bool:
            # Một snapshot atomic/session read cho từng lần kiểm tra.
            current_run = self.store.load(sid)
            return (current_run.generation == state.generation and current_run.active_run_id == run_id
                    and self._scope_current(run_id))
        agent_context["__root_run_active"] = root_run_active
        # Pin registry vào normalized generation của kernel. Input trước kernel
        # có thể mang generation=0; receipt từ Root phải dùng generation THỰC.
        from .claim_evidence import EvidenceRegistry
        registry = agent_context.get("__evidence_registry")
        if isinstance(registry, EvidenceRegistry):
            if registry.event_id == normalized_event.event_id and registry.session_id == sid:
                agent_context["__evidence_registry"] = replace(
                    registry, generation=normalized_event.generation, run_id=run_id,
                )
            else:
                # Deferred continuation có event_id riêng. Tuyệt đối không đem
                # sources của wake signal khác sang chứng minh lời đang nói.
                agent_context["__evidence_registry"] = None
        try:
            decision = self.agent.decide(
                event=normalized_event,
                recent_history=recent_history,
                note=state.note,
                runtime_context=agent_context,
            )
        except ProviderUnavailable as exc:
            # Root không phát được quyết định => Kernel chỉ phát status kỹ thuật
            # cho user turn, không ngụy trang nó thành phát ngôn của Root.
            _record_run_failure(status="root_provider_failed", reason=exc.kind.value,
                                run_id=run_id, generation=state.generation)
            if normalized_event.kind == "user_turn":
                return self._deliver_runtime_failure(normalized_event, state.generation, run_id,
                                                     reason=f"root:{exc.kind.value}")
            self._fail_current_run(sid, state.generation, run_id)
            return ConversationRunResult(event=normalized_event, decision=None,
                                         status="root_provider_failed", failure_reason=exc.kind.value)
        current = self.store.load(sid)
        if current.generation != state.generation or current.active_run_id != run_id or not self._scope_current(run_id):
            self._release_updated_input(sid, state.generation, run_id)
            return ConversationRunResult(
                event=normalized_event,
                decision=decision,
                status="stale",
            )
        if isinstance(decision, NoReply):
            self.store.mutate(
                sid,
                lambda prior, now: replace(
                    prior,
                    active_run_id="",
                    active_move_id="",
                    active_delivery_id="",
                    phase=RunPhase.WAITING_USER,
                ) if prior.generation == state.generation and prior.active_run_id == run_id else prior,
            )
            return ConversationRunResult(
                event=normalized_event,
                decision=decision,
                status="no_reply",
            )

        collect_batch = agent_context.get("__root_input_batch")
        batch_view = agent_context.get("__root_input_batch_view", {})
        accepted_batch = batch_view.get("snapshot") if isinstance(batch_view, Mapping) else None
        if callable(collect_batch) and accepted_batch is not None:
            newest = collect_batch()
            if newest.revision != accepted_batch.revision:
                self._release_updated_input(sid, state.generation, run_id)
                return ConversationRunResult(event=normalized_event, decision=decision, status="input_updated")
            metadata = accepted_batch.to_dict()
            metadata.pop("canonical_text", None)
            normalized_event = replace(normalized_event, text=accepted_batch.canonical_text,
                                       metadata={**normalized_event.metadata, "user_input_batch": metadata})

        continuation_meta = (runtime_context or {}).get("continuation_lease", {})
        if not isinstance(continuation_meta, Mapping):
            continuation_meta = {}
        turn = KernelTurn(
            session_id=sid,
            generation=state.generation,
            run_id=run_id,
            move=decision,
            note=current.note,
            continuation=event.kind == "continuation_due",
            continuation_chain_started_at=float(continuation_meta.get("chain_started_at", 0.0) or 0.0),
            consecutive_self_wakes=max(0, int(continuation_meta.get("consecutive_self_wakes", 0) or 0)),
            delivery_id=f"delivery:{uuid.uuid4().hex}",
            focus_epoch=focus_epoch,
            canvas_scope=self.canvas_scope(sid),
        )
        self.store.mutate(
            sid,
            lambda prior, now: replace(
                prior,
                active_move_id=decision.move_id,
                phase=RunPhase.WRITING,
            ) if prior.generation == state.generation and prior.active_run_id == run_id else prior,
        )
        self._move_outcome(turn, 'preparing', event_id=normalized_event.event_id)
        if self.writer is None:
            self._move_outcome(turn, 'failed_before_delivery', reason='WRITER_UNAVAILABLE')
            self._fail_run(turn)
            return ConversationRunResult(
                event=normalized_event,
                decision=decision,
                turn=turn,
                status="writer_unavailable",
                failure_reason="WRITER_UNAVAILABLE",
            )
        try:
            rendered = self.writer.realize(
                turn=turn,
                event=normalized_event,
                recent_history=recent_history,
                runtime_context={**dict(runtime_context or {}),
                                 "__evidence_registry": agent_context.get("__evidence_registry"),
                                 "__character_portrait": agent_context.get("__character_portrait"),
                                 "__character_portrait_render": agent_context.get("__character_portrait_render")},
            )
        except Exception as exc:
            reason = _safe_failure_reason(exc)
            # Trước hotfix: log debug bị lọc khỏi eris_run; chỉ còn COMPOSING.
            # Warning này luôn hiện nhưng chỉ có mã lỗi, không có lời nói.
            self._log("warning",
                      "[conversation-v12] writer_failed event=%s run=%s generation=%s reason=%s",
                      normalized_event.event_id, run_id, turn.generation, reason)
            _record_run_failure(status='writer_failed', reason=reason,
                                run_id=run_id, generation=turn.generation)
            self._move_outcome(turn, 'failed_before_delivery', reason=reason)
            failure = provider_failure(exc, role="writer")
            if normalized_event.kind == "user_turn" and failure is not None:
                return self._deliver_runtime_failure(normalized_event, turn.generation, run_id,
                                                     reason=f"writer:{failure.kind.value}")
            self._fail_run(turn)
            return ConversationRunResult(
                event=normalized_event,
                decision=decision,
                turn=turn,
                status="writer_failed",
                failure_reason=reason,
            )

        current = self.store.load(sid)
        if current.generation != turn.generation or current.active_run_id != turn.run_id or not self._scope_current(turn.run_id):
            self._release_updated_input(sid, turn.generation, turn.run_id)
            self._move_outcome(turn, 'cancelled', reason='stale_after_prepare')
            return ConversationRunResult(
                event=normalized_event,
                decision=decision,
                turn=turn,
                rendered=rendered,
                status="stale",
            )
        if callable(collect_batch) and accepted_batch is not None:
            newest = collect_batch()
            if newest.revision != accepted_batch.revision:
                self._move_outcome(turn, 'cancelled', reason='INPUT_BATCH_UPDATED_BEFORE_DELIVERY')
                self._release_updated_input(sid, state.generation, run_id)
                return ConversationRunResult(event=normalized_event, decision=decision,
                                             turn=turn, rendered=rendered, status="input_updated")
        if prepare_only:
            # Chưa có public commit. Tháo claim tạm để event/user khác có thể
            # preempt; candidate chỉ được giữ nếu generation + run còn đúng.
            updated = self.store.mutate(
                sid, lambda prior, now: replace(
                    prior, active_run_id="", active_move_id="",
                    active_delivery_id="", phase=RunPhase.CONTINUATION_PENDING,
                ) if prior.generation == turn.generation and prior.active_run_id == turn.run_id
                and prior.phase == RunPhase.WRITING else prior,
            )
            if updated.generation != turn.generation or updated.phase != RunPhase.CONTINUATION_PENDING:
                self._move_outcome(turn, 'cancelled', reason='stale_speculative')
                return ConversationRunResult(event=normalized_event, decision=decision,
                                             turn=turn, status="stale")
            # Không ghi delivered: chưa ai nghe câu này.
            return ConversationRunResult(event=normalized_event, decision=decision,
                                         turn=turn, rendered=rendered, status="speculative_ready")

        delivery_id = turn.delivery_id
        if self.delivery is None or not self.mark_delivery_started(turn, delivery_id=delivery_id):
            self._move_outcome(turn, 'failed_before_delivery' if self.delivery is None else 'cancelled',
                               reason='DELIVERY_UNAVAILABLE' if self.delivery is None else 'PREEMPTED_BEFORE_DELIVERY')
            self._fail_run(turn)
            return ConversationRunResult(
                event=normalized_event,
                decision=decision,
                turn=turn,
                rendered=rendered,
                status="delivery_unavailable",
                failure_reason="DELIVERY_UNAVAILABLE_OR_PREEMPTED",
            )
        with self._delivery_lock:
            self._delivery_turns[delivery_id] = turn
        try:
            self.delivery.deliver(turn=turn, rendered=rendered, delivery_id=delivery_id)
        except Exception as exc:
            reason = _safe_failure_reason(exc)
            self._log("warning", "[conversation-v12] delivery_failed event=%s run=%s generation=%s reason=%s",
                      normalized_event.event_id, run_id, turn.generation, reason)
            _record_run_failure(status='delivery_failed', reason=reason,
                                run_id=run_id, generation=turn.generation)
            self.settle_delivery_id(
                session_id=turn.session_id,
                delivery_id=delivery_id,
                successful=False,
            )
            return ConversationRunResult(
                event=normalized_event,
                decision=decision,
                turn=turn,
                rendered=rendered,
                delivery_id=delivery_id,
                status="delivery_failed",
                failure_reason=reason,
            )
        return ConversationRunResult(
            event=normalized_event,
            decision=decision,
            turn=turn,
            rendered=rendered,
            delivery_id=delivery_id,
            status="delivering",
        )

    def _fail_current_run(self, sid: str, generation: int, run_id: str) -> None:
        """Không để lỗi provider ở background chiếm governor vô thời hạn."""
        state = self.store.load(sid)
        if state.generation == generation and state.active_run_id == run_id:
            self.store.mutate(sid, lambda prior, now: replace(
                prior, phase=RunPhase.FAILED, active_run_id="", active_move_id="", active_delivery_id=""))

    def _release_updated_input(self, sid: str, generation: int, run_id: str) -> None:
        self.store.mutate(sid, lambda prior, now: replace(
            prior, phase=RunPhase.CANCELLED, active_run_id="", active_move_id="", active_delivery_id="",
        ) if prior.generation == generation and prior.active_run_id == run_id else prior)

    def _deliver_runtime_failure(self, event: ConversationEvent, generation: int,
                                 run_id: str, *, reason: str) -> ConversationRunResult:
        """Thông báo offline không gọi Root/Writer/localizer, chỉ qua delivery chuẩn.

        Không dùng cho autonomous/continuation. Generation + run-id được kiểm
        tra cả trước chuẩn bị lẫn trước transport để không nói thay lượt cũ.
        """
        sid = event.session_id
        state = self.store.load(sid)
        if (event.kind != "user_turn" or state.generation != generation
                or state.active_run_id != run_id):
            return ConversationRunResult(event=event, decision=None, status="stale")
        move = NextConversationMove(
            intent="runtime_status_only", say="Provider tạm thời không trả lời",
            delivery="gentle", after=ConversationAfter.WAIT_USER,
        )
        turn = KernelTurn(session_id=sid, generation=generation, run_id=run_id,
                          move=move, note=state.note,
                          delivery_id=f"delivery:{uuid.uuid4().hex}", focus_epoch=self._focus_epochs.get(sid, 0))
        self.store.mutate(sid, lambda prior, now: replace(
            prior, active_move_id=move.move_id, phase=RunPhase.WRITING))
        rendered = RenderedConversationMove(
            surface=SurfaceEnvelope(
                display_text="Ư… tớ đang gặp trục trặc nên chưa trả lời được. Cậu thử lại sau một chút nhé.",
                speech_text="うぅ……今ちょっと調子が悪くて、返事ができないみたい。また少ししてから試してね。",
                language="vi", alignment_safe=False,
            ),
            metadata={"runtime_status_only": True, "event_kind": "user_turn",
                      "user_text": "", "provider_payload": {"display_language": "vi"}},
        )
        # Cố ý không chạy Writer: khi LLM chết đường này vẫn hoạt động.
        if self.delivery is None or not self.mark_delivery_started(turn, delivery_id=turn.delivery_id):
            self._fail_current_run(sid, generation, run_id)
            return ConversationRunResult(event=event, decision=None, turn=turn,
                                         status="runtime_status_unavailable", failure_reason=reason)
        with self._delivery_lock:
            self._delivery_turns[turn.delivery_id] = turn
        try:
            # Từ đây đi qua đúng transport/Stage/TTS hiện hành.
            self.delivery.deliver(turn=turn, rendered=rendered, delivery_id=turn.delivery_id)
        except Exception as exc:
            self.settle_delivery_id(session_id=sid, delivery_id=turn.delivery_id, successful=False)
            _record_run_failure(status="runtime_status_delivery_failed", reason=type(exc).__name__,
                                run_id=run_id, generation=generation)
            return ConversationRunResult(event=event, decision=None, turn=turn,
                                         status="runtime_status_delivery_failed", failure_reason=reason)
        return ConversationRunResult(event=event, decision=None, turn=turn, rendered=rendered,
                                     delivery_id=turn.delivery_id, status="runtime_status_delivering",
                                     failure_reason=reason)

    def prepare_continuation_candidate(
        self, *, lease: Any,
        recent_history: Sequence[Mapping[str, Any]] = (),
        runtime_context: Mapping[str, Any] | None = None,
    ) -> ConversationRunResult | None:
        """Speculatively run Root→Writer, never delivery. No extra speech owner.

        Preparation is attached to one lease/generation. If a user turn arrived,
        ordinary kernel preemption invalidates the run before it can be offered.
        """
        sid = str(lease.session_id or "default")[:180]
        current = self.continuation.peek(sid)
        if (current is None or current.lease_id != lease.lease_id
                or current.generation != lease.generation or current.status != "pending"):
            return None
        event = ConversationEvent.continuation_due(
            session_id=sid, generation=lease.generation, lease_id=lease.lease_id,
        )
        result = self._handle_event_once(
            event,
            recent_history=recent_history,
            runtime_context={**dict(runtime_context or {}), "continuation_lease": lease.to_dict()},
            prepare_only=True,
        )
        if result.status in {"speculative_ready", "no_reply"}:
            if not self.continuation.offer_speculative(lease, result):
                if result.turn is not None:
                    self._move_outcome(result.turn, 'cancelled', reason='stale_speculative_lease')
        return result

    def _deliver_prepared_continuation(
        self, candidate: ConversationRunResult, *, lease: Any,
    ) -> ConversationRunResult | None:
        """Claim a prepared move and deliver through the ORIGINAL delivery adapter."""
        turn = candidate.turn
        rendered = candidate.rendered
        if turn is None or rendered is None or turn.generation != lease.generation:
            return None
        sid = turn.session_id
        current = self.store.load(sid)
        if current.generation != turn.generation or current.phase not in {
            RunPhase.CONTINUATION_PENDING, RunPhase.WAITING_USER,
        }:
            return None
        updated = self.store.mutate(
            sid, lambda prior, now: replace(
                prior, active_run_id=turn.run_id, active_move_id=turn.move.move_id,
                phase=RunPhase.WRITING,
            ) if prior.generation == turn.generation and prior.phase in {
                RunPhase.CONTINUATION_PENDING, RunPhase.WAITING_USER,
            } else prior,
        )
        if updated.active_run_id != turn.run_id or updated.phase != RunPhase.WRITING:
            return None
        if self.delivery is None or not self.mark_delivery_started(turn, delivery_id=turn.delivery_id):
            self._fail_run(turn)
            return ConversationRunResult(event=candidate.event, decision=candidate.decision,
                                         turn=turn, rendered=rendered, status="delivery_unavailable")
        with self._delivery_lock:
            self._delivery_turns[turn.delivery_id] = turn
        try:
            self.delivery.deliver(turn=turn, rendered=rendered, delivery_id=turn.delivery_id)
        except Exception as exc:
            self.settle_delivery_id(session_id=sid, delivery_id=turn.delivery_id, successful=False)
            return ConversationRunResult(event=candidate.event, decision=candidate.decision,
                                         turn=turn, rendered=rendered, status="delivery_failed",
                                         failure_reason=_safe_failure_reason(exc))
        return ConversationRunResult(event=candidate.event, decision=candidate.decision,
                                     turn=turn, rendered=rendered,
                                     delivery_id=turn.delivery_id, status="delivering")

    def handle_due_continuation(
        self,
        *,
        session_id: str,
        recent_history: Sequence[Mapping[str, Any]] = (),
        runtime_context: Mapping[str, Any] | None = None,
        now: float | None = None,
    ) -> ConversationRunResult | None:
        sid = str(session_id or "default")[:180]
        state = self.store.load(sid)
        lease = self.continuation.claim_due(
            session_id=sid,
            generation=state.generation,
            now=now,
        )
        if lease is None:
            return None
        # Fast-path: consume candidate đã localize/verify từ Root/Writer.
        prepared = self.continuation.take_speculative(lease)
        if isinstance(prepared, ConversationRunResult):
            if prepared.status == "no_reply":
                self.continuation.consume(lease)
                return prepared
            delivered = self._deliver_prepared_continuation(prepared, lease=lease)
            if delivered is not None:
                self.continuation.consume(lease)
                return delivered
        event = ConversationEvent.continuation_due(
            session_id=sid,
            generation=lease.generation,
            lease_id=lease.lease_id,
        )
        context = {
            **dict(runtime_context or {}),
            "continuation_lease": lease.to_dict(),
        }
        try:
            result = self.handle_event(
                event,
                recent_history=recent_history,
                runtime_context=context,
            )
        except Exception:
            if self.store.load(sid).generation == lease.generation:
                self.continuation.release(lease)
            raise
        if result.status == "busy":
            # Chỉ busy mới nhả lại lease; lỗi writer là thất bại, không phải
            # tín hiệu timer tự viết lại. Không replay câu chưa nói.
            if self.store.load(sid).generation == lease.generation:
                self.continuation.release(lease)
            else:
                self.continuation.consume(lease)
        else:
            self.continuation.consume(lease)
        return result

    def settle_delivery_id(
        self,
        *,
        session_id: str = "",
        delivery_id: str,
        successful: bool,
        cancelled: bool = False,
    ) -> bool:
        did = str(delivery_id or "")[:180]
        with self._delivery_lock:
            turn = self._delivery_turns.get(did)
        if turn is None:
            return False
        sid = str(session_id or turn.session_id)[:180]
        if sid != turn.session_id:
            return False
        settled = self.settle_delivery(turn, delivery_id=did, successful=successful, cancelled=cancelled)
        if settled:
            with self._delivery_lock:
                self._delivery_turns.pop(did, None)
        return settled

    def _discard_session_deliveries(self, session_id: str) -> None:
        # Callback TTS cũ đến muộn phải bị loại, và receipt cho Root phải ghi
        # cancellation thay vì giữ vĩnh viễn trạng thái 'delivering'.
        self.outcomes.cancel_inflight(session_id, reason='preempted_by_user')
        with self._delivery_lock:
            stale = [
                delivery_id
                for delivery_id, turn in self._delivery_turns.items()
                if turn.session_id == session_id
            ]
            for delivery_id in stale:
                self._delivery_turns.pop(delivery_id, None)

    def _fail_run(self, turn: KernelTurn) -> None:
        state = self.store.load(turn.session_id)
        if state.generation != turn.generation or state.active_run_id != turn.run_id:
            return
        self.store.mutate(
            turn.session_id,
            lambda prior, now: replace(
                prior,
                active_delivery_id="",
                phase=RunPhase.FAILED,
            ),
        )

    def _log(self, level: str, message: str, *args: Any) -> None:
        method = getattr(self.logger, level, None) if self.logger is not None else None
        if callable(method):
            try:
                method(message, *args)
            except Exception:
                pass

    def begin_user_turn(self, *, session_id: str, user_text: str,
                        recent_history: Sequence[Mapping[str, Any]] = (),
                        runtime_context: Mapping[str, Any] | None = None) -> KernelTurn:
        sid = str(session_id or "default")[:180]
        run_id = f"run:{uuid.uuid4().hex}"
        state = self.store.mutate(
            sid,
            lambda prior, now: replace(
                prior,
                generation=prior.generation + 1,
                active_run_id=run_id,
                active_move_id="",
                active_delivery_id="",
                phase=RunPhase.THINKING,
            ),
        )
        self.continuation.cancel_session(sid)
        self._discard_session_deliveries(sid)
        event = ConversationEvent.user_turn(
            session_id=sid,
            text=str(user_text or ""),
            generation=state.generation,
        )
        decision = self.agent.decide(
            event=event,
            recent_history=recent_history,
            note=state.note,
            runtime_context=self._agent_runtime_context(sid, runtime_context),
        )
        if isinstance(decision, NoReply):
            self.store.mutate(sid, lambda prior, now: replace(prior, phase=RunPhase.WAITING_USER))
            raise RuntimeError("user_turn_no_reply")
        move = decision
        self.store.mutate(
            sid,
            lambda prior, now: replace(prior, active_move_id=move.move_id, phase=RunPhase.WRITING),
        )
        return KernelTurn(sid, state.generation, run_id, move, state.note, False,
                          focus_epoch=self._focus_epochs.get(sid, 0), canvas_scope=self.canvas_scope(sid))

    def begin_continuation(self, *, session_id: str,
                           recent_history: Sequence[Mapping[str, Any]] = (),
                           runtime_context: Mapping[str, Any] | None = None) -> KernelTurn | None:
        sid = str(session_id or "default")[:180]
        state = self.store.load(sid)
        lease = self.continuation.claim_due(session_id=sid, generation=state.generation)
        if lease is None:
            return None
        run_id = f"run:{uuid.uuid4().hex}"
        self.store.mutate(sid, lambda prior, now: replace(prior, active_run_id=run_id, phase=RunPhase.THINKING))
        try:
            event = ConversationEvent(
                event_id=f"continuation:{lease.lease_id}",
                session_id=sid,
                kind="continuation_due",
                generation=state.generation,
                metadata={"continuation_lease": lease.to_dict()},
            )
            decision = self.agent.decide(
                event=event,
                recent_history=recent_history,
                note=state.note,
                runtime_context=self._agent_runtime_context(
                    sid,
                    {**dict(runtime_context or {}), "continuation_lease": lease.to_dict()},
                ),
            )
            self.continuation.consume(lease)
            if isinstance(decision, NoReply):
                self.store.mutate(
                    sid,
                    lambda prior, now: replace(
                        prior,
                        active_run_id="",
                        active_move_id="",
                        phase=RunPhase.WAITING_USER,
                    ),
                )
                return None
            move = decision
            self.store.mutate(
                sid,
                lambda prior, now: replace(prior, active_move_id=move.move_id, phase=RunPhase.WRITING),
            )
            return KernelTurn(
                sid,
                state.generation,
                run_id,
                move,
                state.note,
                True,
                lease.chain_started_at,
                lease.consecutive_self_wakes,
                f"delivery:{uuid.uuid4().hex}",
                focus_epoch=self._focus_epochs.get(sid, 0),
                canvas_scope=self.canvas_scope(sid),
            )
        except Exception:
            self.continuation.release(lease)
            raise

    def _move_outcome(self, turn: KernelTurn, status: str, *, event_id: str = '',
                      delivery_id: str = '', reason: str = '') -> None:
        self.outcomes.update(
            session_id=turn.session_id, generation=turn.generation,
            run_id=turn.run_id, move_id=turn.move.move_id, status=status,
            delivery_id=delivery_id or turn.delivery_id, event_id=event_id,
            intent=turn.move.intent, say=turn.move.say, reason=reason,
        )

    def _background_failure_cooldown(self, session_id: str) -> bool:
        """Tối đa hai lần hỏng gần nhau từ timer, không tự chạy lại câu cũ.

        Đây là circuit breaker tài nguyên, không phải semantic scheduler.
        """
        rows = self.outcomes.list(session_id)
        if len(rows) < 2:
            return False
        last_two = rows[-2:]
        now = time.time()
        return bool(all(row.get('status') in {'failed_before_delivery', 'delivery_failed'}
                        and now - float(row.get('updated_at', 0)) < 90
                        for row in last_two))

    def _agent_runtime_context(
        self,
        session_id: str,
        runtime_context: Mapping[str, Any] | None,
    ) -> dict[str, Any]:
        """Gắn note tools cơ học cho RootAgent mà không đưa chúng xuống writer.

        Capability registry (nếu caller cung cấp) được giữ nguyên trong mapping
        nội bộ. ``RuntimeModelRootAgent`` tự chặn các key ``__*`` khỏi model
        payload; writer cũng chỉ nhận public runtime context gốc.
        """

        sid = str(session_id or "default")[:180]
        context = dict(runtime_context or {})
        with self._outcome_recovery_lock:
            if sid not in self._recovered_outcome_sessions:
                self._recovered_outcome_sessions.add(sid)
                # Không có public commit khi process crash ở giai đoạn TTS.
                # Chỉ chạy một lần khi session được kernel instance mới dùng.
                self.outcomes.recover_inflight_on_restart(sid)
        # Không đi qua model session transcript hay public history: dù Writer
        # chưa từng commit, Root vẫn được biết outcome trước đó.
        context['conversation_move_receipts'] = self.outcomes.feedback(sid)
        # Root biết tiến độ công việc thật ở mỗi lượt chat; không phải đợi worker
        # xong mới được phát lời. Không đưa raw log/tool input vào Writer/TTS.
        executor = getattr(getattr(self.writer, "host", None), "action_executor", None)
        work_lane = getattr(executor, "_parallel_work_service", None)
        if work_lane is not None:
            try:
                context["work_lane_progress"] = [
                    {"job_id": x["job_id"], "status": x["status"],
                     "summary": str(x.get("summary", ""))[:600],
                     "revision": x["revision"]}
                    for x in work_lane.recent(limit=3)
                ]
            except Exception:
                pass

        context["__conversation_note_tools"] = ConversationNoteTools(
            set_note=lambda text: self.store.set_note(sid, text),
            clear_note=lambda: self.store.clear_note(sid),
        )
        return context

    def set_note(self, session_id: str, note: str) -> None:
        self.store.set_note(session_id, note)

    def clear_note(self, session_id: str) -> None:
        self.store.clear_note(session_id)

    def mark_delivery_started(self, turn: KernelTurn, *, delivery_id: str) -> bool:
        did = str(delivery_id or '')[:180]
        with self._focus_lock:
            if turn.focus_epoch != self._focus_epochs.get(turn.session_id, 0) or not self._scope_current(turn.run_id):
                return False
            state = self.store.mutate(
                turn.session_id,
                lambda prior, now: replace(prior, active_delivery_id=did, phase=RunPhase.DELIVERING)
                if prior.generation == turn.generation and prior.active_run_id == turn.run_id
                and prior.active_move_id == turn.move.move_id and prior.phase == RunPhase.WRITING
                else prior,
            )
        if state.generation != turn.generation or state.active_run_id != turn.run_id \
                or state.active_delivery_id != did or state.phase != RunPhase.DELIVERING:
            return False
        # Handoff tới delivery không chứng minh TTS đã bắt đầu phát.
        # Chỉ audio_start thực tế mới chuyển thành trạng thái 'delivering'.
        self._move_outcome(turn, 'queued_for_audio', delivery_id=delivery_id)
        return True

    def mark_audio_started(self, *, delivery_id: str) -> bool:
        did = str(delivery_id or '')[:180]
        with self._delivery_lock:
            turn = self._delivery_turns.get(did)
        if turn is None:
            return False
        state = self.store.load(turn.session_id)
        if (state.generation != turn.generation or state.active_run_id != turn.run_id
                or state.active_delivery_id != did or state.phase != RunPhase.DELIVERING):
            return False
        self._move_outcome(turn, 'delivering', delivery_id=did)
        return True

    def settle_delivery(self, turn: KernelTurn, *, delivery_id: str,
                        successful: bool, cancelled: bool = False) -> bool:
        with self._focus_lock:
            settled = self._settle_delivery_locked(turn, delivery_id=delivery_id, successful=successful, cancelled=cancelled)
        if settled and callable(self.on_settled):
            self.on_settled(delivery_id, successful, cancelled)
        return settled

    def _settle_delivery_locked(self, turn: KernelTurn, *, delivery_id: str,
                        successful: bool, cancelled: bool = False) -> bool:
        state = self.store.load(turn.session_id)
        if state.generation != turn.generation:
            return False
        receipt = DeliveryReceipt(
            delivery_id=str(delivery_id or "")[:180], session_id=turn.session_id,
            generation=turn.generation, move_id=turn.move.move_id, successful=bool(successful),
        )
        if not self.receipts.commit_once(receipt):
            return False
        if not successful:
            self._move_outcome(turn, 'cancelled' if cancelled else 'delivery_failed',
                               delivery_id=delivery_id,
                               reason='tts_cancelled' if cancelled else 'delivery_terminal_failure')
            self.store.mutate(turn.session_id, lambda prior, now: replace(prior, phase=RunPhase.FAILED, active_delivery_id=""))
            return True
        self._move_outcome(turn, 'delivered', delivery_id=delivery_id)
        lease = self.continuation.schedule(
            session_id=turn.session_id,
            generation=turn.generation,
            move_id=turn.move.move_id,
            after=(turn.move.after if turn.focus_epoch == self._focus_epochs.get(turn.session_id, 0)
                   else ConversationAfter.WAIT_USER),
            parent_chain_started_at=turn.continuation_chain_started_at if turn.continuation else 0.0,
            parent_self_wakes=turn.consecutive_self_wakes if turn.continuation else 0,
        )
        phase = RunPhase.CONTINUATION_PENDING if lease is not None else RunPhase.WAITING_USER
        if lease is not None and turn.canvas_scope:
            self._canvas_lease_scopes[turn.session_id] = dict(turn.canvas_scope)
        else:
            self._canvas_lease_scopes.pop(turn.session_id, None)
        self.store.mutate(
            turn.session_id,
            lambda prior, now: replace(
                prior,
                phase=phase,
                active_delivery_id="",
                last_delivered_move_id=turn.move.move_id,
                last_delivery_finished_at=now,
            ),
        )
        if lease is not None and callable(self.speculative_preparer):
            try:
                # Callback chỉ dispatch background thread, không block TTS.
                self.speculative_preparer(lease)
            except Exception as exc:
                self._log("warning", "[conversation-v12] speculative_dispatch_failed reason=%s", type(exc).__name__)
        return True

    def preempt_for_user(self, session_id: str) -> ConversationSessionState:
        sid = str(session_id or "default")[:180]
        state = self.store.mutate(
            sid,
            lambda prior, now: replace(
                prior,
                generation=prior.generation + 1,
                active_run_id="",
                active_move_id="",
                active_delivery_id="",
                phase=RunPhase.CANCELLED,
            ),
        )
        # Callbacks cũ không thể thắng vì generation đã đổi.
        self.continuation.cancel_session(sid)
        self._discard_session_deliveries(sid)
        return state

    def snapshot(self, session_id: str) -> ConversationSessionState:
        return self.store.load(session_id)


__all__ = ["ConversationKernel", "ConversationRunResult", "KernelTurn"]
