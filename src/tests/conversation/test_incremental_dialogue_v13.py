# -*- coding: utf-8 -*-
"""Bộ kiểm thử cho nâng cấp Incremental Dialogue (Text-first) trên Eris.

Bao gồm đủ 4 tình huống chồng lấn theo yêu cầu:
1. User nhắn thêm khi Root đang nghĩ:
   ``InputRevisionBuffer`` đánh dấu stale cho run đang chạy, gom câu mới qua
   cửa sổ debounce ngắn, và restart Root đúng 1 lần duy nhất.
2. User cắt ngang khi Eris đang trả lời:
   Tạo ``SuspendedMove`` lưu vào ``OpenLoopStore``, truyền phần chưa nói dưới
   dạng hint vào lượt Root kế tiếp để quyết định ``RESUME`` hay ``RETIRE``.
3. User nhắn từ ngắn ("ừ", "khoan"):
   ``InteractionDynamicsController`` xét theo ngữ cảnh trước đó ("ừ" sau câu hỏi
   của Eris là ``YIELD_TO_USER``, "ừ" khi Eris đang giảng bài là ``KEEP_SPEAKING``,
   "khoan" khi đang nói là ``HARD_STOP`` nhưng khi kể chuyện mở đầu là ``YIELD_TO_USER``).
4. Chờ Root suy nghĩ gây trễ nhịp:
   ``MicroActRouter`` phát trực tiếp các act không mang nội dung mới
   (``RECEIPT``, ``PROMPT_CONTINUE``, ``STOP_ACK``) và ghi log bằng ``InteractionActReceipt``.
"""
from __future__ import annotations

from types import SimpleNamespace

from runtime.chat_cycle import ChatCycleMixin
from runtime.conversation.continuation import ContinuationLeaseScheduler
from runtime.conversation.contracts import ConversationAfter, NextConversationMove
from runtime.conversation.delivery import SurfaceEnvelope
from runtime.conversation.interrupts import InterruptKind, classify_interrupt
from runtime.conversation.kernel import KernelTurn, ConversationRunResult
from runtime.conversation.writer import RenderedConversationMove
from runtime.incoming_dispatcher import IncomingMessageDispatcher
from runtime.input_revision_buffer import InputRevisionBuffer
from runtime.interaction_dynamics import (
    DynamicsAction,
    InteractionDynamicsController,
    MicroActKind,
    MicroActRouter,
)
from runtime.interrupt_ledger import InterruptEvent, InterruptLedger
from runtime.open_loop_store import OpenLoopDecision, OpenLoopStore


class _DummyLogger:
    def info(self, *_args, **_kwargs):
        pass

    def warning(self, *_args, **_kwargs):
        pass

    def error(self, *_args, **_kwargs):
        pass

    def debug(self, *_args, **_kwargs):
        pass


def test_1_input_revision_buffer_marks_stale_and_restarts_root_once():
    """Tình huống 1: User nhắn thêm khi Root đang nghĩ -> mark stale, gom debounce, restart đúng 1 lần."""
    stale_notifications: list[tuple[int, int]] = []
    buffer = InputRevisionBuffer(debounce_seconds=0.05)

    snap1 = buffer.begin_turn("Mình muốn dùng sqlite", timestamp=10.0, session_id="s1")
    assert snap1.revision == 1
    assert snap1.canonical_text == "Mình muốn dùng sqlite"
    assert not buffer.is_revision_stale(1)

    # User nhắn thêm 2 câu liên tiếp khi Root đang nghĩ ở revision 1
    buffer.submit_revision(
        "à không, đổi sang postgres đi",
        timestamp=10.02,
        on_mark_stale=lambda old_rev, new_rev: stale_notifications.append((old_rev, new_rev)),
    )
    buffer.submit_revision(
        "vì hệ thống có nhiều process chạy song song",
        timestamp=10.04,
        on_mark_stale=lambda old_rev, new_rev: stale_notifications.append((old_rev, new_rev)),
    )

    assert buffer.is_revision_stale(1) is True
    assert buffer.has_pending_revision() is True
    assert stale_notifications[0] == (1, 2)

    # Gom qua cửa sổ debounce ngắn và claim đúng 1 lần cho lần restart Root kế tiếp
    coalesced = buffer.coalesce_and_claim_latest(debounce_seconds=0.01)
    assert coalesced is not None
    assert coalesced.revision == 3
    assert "Mình muốn dùng sqlite" in coalesced.canonical_text
    assert "à không, đổi sang postgres đi" in coalesced.canonical_text
    assert "vì hệ thống có nhiều process chạy song song" in coalesced.canonical_text
    assert buffer.has_pending_revision() is False


def test_1b_chat_cycle_restarts_root_exactly_once_when_user_adds_messages_while_thinking():
    """Kiểm tra tích hợp trên ChatCycleMixin: Root chỉ chạy 1 lần khởi đầu + 1 lần restart cho chuỗi tin nhắn chồng lấn."""

    class _ThinkingHost(ChatCycleMixin):
        def __init__(self) -> None:
            self.logger = _DummyLogger()
            self.chat_mode = True
            self._chat_processing = False
            self.ai_runtime_enabled = True
            self.rapid_burst_window = 0.0
            self.input_revision_buffer = InputRevisionBuffer(debounce_seconds=0.01)
            self.root_runs: list[str] = []
            self.preempted_count = 0
            self.conversation_kernel = SimpleNamespace(
                store=SimpleNamespace(load=lambda _sid: SimpleNamespace(active_delivery_id="")),
                preempt_for_user=lambda _sid: setattr(self, "preempted_count", self.preempted_count + 1)
            )
            self.ai_chat_orchestrator = SimpleNamespace(run=self._fake_orchestrator_run)

        def _link_previous_public_turn_for_chat(self, _ts):
            return None

        def _previous_public_turn_context(self, _turn):
            return ""

        def _fake_orchestrator_run(self, user_text: str, **_kwargs) -> None:
            self.root_runs.append(user_text)
            if len(self.root_runs) == 1:
                # Giả lập trong lúc Root đang suy nghĩ câu đầu tiên, user nhắn thêm 2 câu nữa
                self._run_chat_cycle("à không, postgres nhé", user_timestamp=101.0)
                self._run_chat_cycle("tại có nhiều worker", user_timestamp=101.01)

    host = _ThinkingHost()
    host._run_chat_cycle("Mình tính dùng sqlite", user_timestamp=100.0)

    # Root chạy lần 1 cho câu đầu, sau đó nhận thấy có revision mới thì gom cả 2 câu bổ sung và restart đúng 1 lần (tổng cộng 2 lần gọi orchestrator, không phải 3).
    assert len(host.root_runs) == 2
    assert host.root_runs[0] == "Mình tính dùng sqlite"
    assert "à không, postgres nhé" in host.root_runs[1]
    assert "tại có nhiều worker" in host.root_runs[1]
    assert host.preempted_count == 0  # no delivery yet; collect one batch instead


def test_2_open_loop_store_captures_suspended_move_and_decides_resume_or_retire():
    """Tình huống 2: User cắt ngang khi Eris đang trả lời -> tạo SuspendedMove vào OpenLoopStore -> RESUME hoặc RETIRE."""
    store = OpenLoopStore(default_ttl_seconds=120.0)
    event = InterruptEvent(
        interrupted_turn_id="turn:10",
        interrupted_seq=1,
        spoken_text_so_far="Có ba nguyên nhân chính. Một là memory cache",
        pending_text="hai là Redis giữ state sai, ba là scheduler timeout.",
        user_text="Khoan, cache đó là memory cache hay disk?",
        timestamp=50.0,
    )

    suspended = store.capture_from_interrupt(
        event,
        session_id="s_interrupt",
        source_move_id="move:10",
        source_generation=2,
        intent="Giải thích 3 nguyên nhân gây lỗi hệ thống",
    )
    assert suspended is not None
    assert suspended.status == "suspended"
    assert suspended.recommended_action == OpenLoopDecision.RESUME.value
    assert "hai là Redis giữ state sai" in suspended.pending_text_hint

    # Lượt kế tiếp: user hỏi câu phụ (aside) -> hint vẫn được truyền cho Root với khuyến nghị RESUME
    hint_text, payloads = store.prepare_hints_for_root(
        "s_interrupt",
        latest_user_text="Khoan, cache đó nằm ở process nào?",
        now=52.0,
    )
    assert "OPEN_LOOP_HINT" in hint_text
    assert "recommended_decision=RESUME" in hint_text
    assert len(payloads) == 1

    # Nhưng nếu user đính chính bác bỏ hướng Redis hoặc đổi chủ đề -> chuyển sang RETIRE ngay
    hint_after_retire, payloads_retire = store.prepare_hints_for_root(
        "s_interrupt",
        latest_user_text="Thôi bỏ đoạn đó, sang chuyện khác đi",
        now=55.0,
    )
    assert hint_after_retire == ""
    assert payloads_retire[0]["status"] == "retired"
    assert payloads_retire[0]["recommended_action"] == OpenLoopDecision.RETIRE.value
    assert store.active_loops("s_interrupt", now=56.0) == ()


def test_2b_continuation_scheduler_preserves_speculative_candidate_in_open_loop_store():
    """Khi continuation.py hủy phiên do user cắt ngang, ý định cũ được lưu vào OpenLoopStore thay vì mất trắng."""
    open_store = OpenLoopStore()
    scheduler = ContinuationLeaseScheduler(open_loop_store=open_store)
    lease = scheduler.schedule(
        session_id="s_cont",
        generation=1,
        move_id="move:1",
        after=ConversationAfter.MAY_CONTINUE,
        now=10.0,
    )
    assert lease is not None
    move = NextConversationMove(
        intent="Phân tích tiếp nguyên nhân thứ hai về Redis",
        say="Giải thích thêm về cơ chế lock của Redis",
    )
    turn = KernelTurn(session_id="s_cont", generation=1, run_id="run:1", move=move, note="")
    rendered = RenderedConversationMove(
        surface=SurfaceEnvelope(display_text="Nguyên nhân thứ hai nằm ở cơ chế lock của Redis."),
        metadata={},
    )
    candidate = ConversationRunResult(
        event=None,  # type: ignore[arg-type]
        decision=move,
        turn=turn,
        rendered=rendered,
        status="speculative_ready",
    )
    assert scheduler.offer_speculative(lease, candidate) is True

    # User cắt ngang làm cancel_session
    scheduler.cancel_session("s_cont")
    loops = open_store.active_loops("s_cont")
    assert len(loops) == 1
    assert "Phân tích tiếp nguyên nhân thứ hai" in loops[0].intent
    assert "Nguyên nhân thứ hai nằm ở cơ chế lock của Redis" in loops[0].pending_text_hint


def test_3_interaction_dynamics_controller_context_sensitive_short_words():
    """Tình huống 3: Phân loại từ ngắn ('ừ', 'khoan') theo ngữ cảnh câu nói trước đó của Eris."""
    controller = InteractionDynamicsController()

    # Case A: Eris đang giảng bài -> User nhắn "ừ" => KEEP_SPEAKING
    dec_lecture = controller.evaluate_incoming(
        "ừ",
        speech_busy=True,
        spoken_text_so_far="Nguyên nhân thứ hai là do cache race condition ở tầng worker...",
    )
    assert dec_lecture.action == DynamicsAction.KEEP_SPEAKING
    assert dec_lecture.cancel_audio is False
    assert dec_lecture.create_user_turn is False

    # Case B: Eris vừa hỏi câu xác nhận -> User nhắn "ừ" => YIELD_TO_USER (tạo user turn trả lời)
    dec_question = controller.evaluate_incoming(
        "ừ",
        speech_busy=True,
        spoken_text_so_far="Tôi nghĩ phương án A ổn hơn, vậy giữ cấu trúc cũ nhé?",
    )
    assert dec_question.action == DynamicsAction.YIELD_TO_USER
    assert dec_question.cancel_audio is True
    assert dec_question.create_user_turn is True

    # Case C: Eris đang giải thích -> User nhắn "khoan" => HARD_STOP
    dec_stop = controller.evaluate_incoming(
        "khoan",
        speech_busy=True,
        spoken_text_so_far="Tiếp theo ta sẽ cấu hình lại toàn bộ...",
    )
    assert dec_stop.action == DynamicsAction.HARD_STOP
    assert dec_stop.cancel_audio is True

    # Case D: Eris đang im lặng, user kể chuyện mở đầu bằng "khoan hôm qua còn chuyện này" => YIELD_TO_USER
    dec_story = controller.evaluate_incoming(
        "khoan hôm qua còn chuyện này mình quên chưa kể cho cậu",
        speech_busy=False,
        thinking_busy=False,
    )
    assert dec_story.action == DynamicsAction.YIELD_TO_USER
    assert dec_story.cancel_audio is False
    assert dec_story.create_user_turn is True

    # Kiểm tra classify_interrupt tương thích ngữ cảnh
    assert classify_interrupt(
        "ừ",
        previous_assistant_text="Vậy giữ Redis nhé?",
    ).kind == InterruptKind.STEERING
    assert classify_interrupt(
        "ừ",
        previous_assistant_text="Thứ hai là cache race...",
    ).kind == InterruptKind.BACKCHANNEL


def test_4_micro_act_router_emits_fast_act_and_logs_interaction_act_receipt():
    """Tình huống 4: MicroActRouter phát trực tiếp RECEIPT / PROMPT_CONTINUE và ghi log bằng InteractionActReceipt."""
    controller = InteractionDynamicsController()
    router = MicroActRouter(cooldown_seconds=1.0)
    emitted_surfaces: list[tuple[str, str]] = []

    # User gửi một câu kể chuyện còn bỏ lửng ("Hôm qua mình mới gặp chuyện này...")
    decision = controller.evaluate_incoming(
        "Hôm qua mình mới gặp chuyện này...",
        speech_busy=False,
        thinking_busy=False,
    )
    assert decision.micro_act == MicroActKind.PROMPT_CONTINUE

    receipt = router.select_and_emit(
        decision,
        user_text="Hôm qua mình mới gặp chuyện này...",
        input_revision=5,
        eris_currently_speaking=False,
        emit_fn=lambda surface, kind: emitted_surfaces.append((surface, kind.value)) or True,
        now=100.0,
    )
    assert receipt is not None
    assert receipt.act == MicroActKind.PROMPT_CONTINUE
    assert receipt.surface == "Rồi sao?"
    assert receipt.delivered is True
    assert receipt.related_input_revision == 5
    assert receipt.floor_intent == "YIELD"
    assert emitted_surfaces == [("Rồi sao?", "PROMPT_CONTINUE")]

    # Kiểm tra không phát đè khi Eris đang nói (KEEP_SPEAKING)
    keep_decision = controller.evaluate_incoming(
        "ừm",
        speech_busy=True,
        spoken_text_so_far="Đoạn code này chạy trên main thread.",
    )
    assert router.select_and_emit(
        keep_decision,
        user_text="ừm",
        eris_currently_speaking=True,
        now=105.0,
    ) is None

    # Kiểm tra tích hợp qua IncomingMessageDispatcher
    class _DispatcherHost(ChatCycleMixin):
        def __init__(self) -> None:
            self.logger = _DummyLogger()
            self.chat_mode = False
            self.interaction_clock = SimpleNamespace(speech_floor_busy=True)
            self.interrupt_ledger = InterruptLedger()
            self.last_text = "Vậy giữ cấu trúc cũ nhé?"
            self.chats: list[str] = []
            self.reasons: list[str] = []

        def _supersede_tts_for_new_user_turn(self, reason: str) -> bool:
            self.reasons.append(reason)
            return True

        def _capture_speech_interrupt_for_user(self, text: str, *, timestamp: float):
            return None

        def _run_chat_cycle(self, text: str, *, user_timestamp: float) -> None:
            self.chats.append(text)

    host = _DispatcherHost()
    # Vì last_text của Eris là câu hỏi "Vậy giữ cấu trúc cũ nhé?", câu "ừ" của user phải tạo user turn!
    IncomingMessageDispatcher._handle_chat(SimpleNamespace(owner=host, clock=lambda: 200.0), {"message": "ừ", "timestamp": 200.0})
    assert host.chats == ["ừ"]
    assert host.reasons == ["user_chat_supersede"]
