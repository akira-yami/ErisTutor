# -*- coding: utf-8 -*-
"""P0 hồi quy từ log ngày 13/10/2026; tất cả đều chạy offline.

Kiểm định: Seed contract, interrupt classifier, public history khác terminal
TTS, receipt đến sớm và receipt stale không làm sai câu mới.
"""
from __future__ import annotations

from types import SimpleNamespace

from runtime.ai_orchestrated.social_candidate import SocialSpeechCandidate
from runtime.chat_cycle import ChatCycleMixin
from runtime.conversation.speech_outcome import (
    note_public_delivery_outcome, register_public_surface,
)
from runtime.incoming_dispatcher import IncomingMessageDispatcher
from runtime.prompt_context_builder import PromptContextBuilderMixin
from runtime.session_turn_ledger import SessionTurnLedger


class _Logger:
    def info(self, *_args, **_kwargs):
        pass

    def debug(self, *_args, **_kwargs):
        pass


class _InboundHost(ChatCycleMixin):
    def __init__(self) -> None:
        self.logger = _Logger()
        self.chat_mode = False
        self.ai_discovery_runtime = None
        self.interaction_clock = SimpleNamespace(speech_floor_busy=True)
        self.reasons = []
        self.cancelled_generations = []
        self.chats = []
        self.captured = []
        self.llm_service = SimpleNamespace(
            cancel_pending_requests=lambda **kwargs: self.cancelled_generations.append(kwargs["reason"])
        )

    def _supersede_tts_for_new_user_turn(self, reason):
        self.reasons.append(reason)
        return True

    def _capture_speech_interrupt_for_user(self, text, *, timestamp):
        self.captured.append(text)

    def _run_chat_cycle(self, text, *, user_timestamp):
        self.chats.append(text)


def test_backchannel_preserves_audio_and_does_not_cancel_root_generation():
    host = _InboundHost()
    host._enter_chat_cycle()  # UDP chat_start trước khi có transcript.
    IncomingMessageDispatcher._handle_chat(SimpleNamespace(owner=host, clock=lambda: 1.0), {"message": "ừ"})
    assert host.reasons == []
    assert host.cancelled_generations == []
    assert host.chats == []
    assert host.captured == []
    assert host.chat_mode is False  # Không giữ chat_mode sau backchannel.


def test_steering_user_turn_preempts_audio_once_and_runs_chat():
    host = _InboundHost()
    host._enter_chat_cycle()
    IncomingMessageDispatcher._handle_chat(SimpleNamespace(owner=host, clock=lambda: 1.0), {"message": "dừng lại đi"})
    assert host.reasons == ["user_chat_supersede"]
    assert host.cancelled_generations == ["enter_chat_mode"]
    assert host.chats == ["dừng lại đi"]


def test_chat_without_chat_start_still_acquires_turn_and_preempts():
    host = _InboundHost()
    IncomingMessageDispatcher._handle_chat(SimpleNamespace(owner=host, clock=lambda: 1.0), {"message": "đổi đề tài"})
    assert host.chat_mode is True
    assert host.reasons == ["user_chat_supersede"]
    assert host.chats == ["đổi đề tài"]


def test_seed_contract_roundtrip_keeps_epistemic_origin_without_public_rights():
    candidate = SocialSpeechCandidate(
        decision="candidate", topic="Một ý mới", seed_event_kind="curiosity_question",
        seed_epistemic_mode="hunch", seed_open_loop=True,
    )
    restored = SocialSpeechCandidate.from_mapping(candidate.to_dict())
    assert restored.seed_event_kind == "curiosity_question"
    assert restored.seed_epistemic_mode == "hunch"
    assert restored.seed_open_loop is True
    assert restored.available is True  # Đủ đề xuất; không được tự gửi TTS.


class _LedgerHost:
    def __init__(self):
        self.chat_history = []
        self.ledger = SessionTurnLedger()
        self.journal = []

    def _ensure_session_turn_ledger(self):
        return self.ledger


def _record_pending(host, did, text):
    host.chat_history.append({
        "role": "assistant", "content": text,
        "delivery_id": did, "delivery_status": "pending",
    })
    host.ledger.record_assistant(text, meta={"delivery_id": did, "delivery_status": "pending"})


def test_cancelled_public_surface_is_not_a_delivered_memory():
    host = _LedgerHost()
    _record_pending(host, "delivery:1", "Câu dài chưa nói xong")
    register_public_surface(host, delivery_id="delivery:1", on_delivered=lambda: host.journal.append("done"))
    assert note_public_delivery_outcome(host, delivery_id="delivery:1", status="cancelled") is True
    assert note_public_delivery_outcome(host, delivery_id="delivery:1", status="delivered") is False
    assert host.chat_history[0]["delivery_status"] == "cancelled"
    assert host.ledger.assistant_turns()[0].meta["delivery_status"] == "cancelled"
    assert host.journal == []


def test_no_tts_receipt_before_history_works_and_commits_once():
    host = _LedgerHost()
    assert note_public_delivery_outcome(host, delivery_id="delivery:early", status="delivered")
    _record_pending(host, "delivery:early", "Có.")
    register_public_surface(host, delivery_id="delivery:early", on_delivered=lambda: host.journal.append("Có."))
    assert host.chat_history[0]["delivery_status"] == "delivered"
    assert host.ledger.assistant_turns()[0].meta["delivery_status"] == "delivered"
    assert host.journal == ["Có."]
    assert not note_public_delivery_outcome(host, delivery_id="delivery:early", status="delivered")
    assert host.journal == ["Có."]


def test_stale_terminal_receipt_only_changes_its_own_delivery():
    host = _LedgerHost()
    _record_pending(host, "delivery:old", "Lời cũ")
    _record_pending(host, "delivery:new", "Lời mới")
    register_public_surface(host, delivery_id="delivery:old")
    register_public_surface(host, delivery_id="delivery:new")
    assert note_public_delivery_outcome(host, delivery_id="delivery:old", status="cancelled")
    assert [x["delivery_status"] for x in host.chat_history] == ["cancelled", "pending"]
    assert [x.meta["delivery_status"] for x in host.ledger.assistant_turns()] == ["cancelled", "pending"]
    assert note_public_delivery_outcome(host, delivery_id="delivery:new", status="delivered")
    assert [x["delivery_status"] for x in host.chat_history] == ["cancelled", "delivered"]


def test_prompt_history_labels_cancelled_audio_and_never_journals_unfinished():
    """Chữ vẫn trên panel, nhưng LLM thấy nhãn bị ngắt và memory không học nhầm."""
    class _Journal:
        def __init__(self):
            self.events = []

        def record_conversation_turn(self, role, text, **_kwargs):
            self.events.append((role, text))

    class _PromptHost(PromptContextBuilderMixin):
        def __init__(self):
            self.chat_history = []
            self.ledger = SessionTurnLedger()
            self.memory_kernel = SimpleNamespace(living_user=_Journal())

        def _ensure_session_turn_ledger(self):
            return self.ledger

    host = _PromptHost()
    host._append_chat_history("assistant", "Lời chưa xong", pending_delivery_id="delivery:1")
    host.ledger.record_assistant("Lời chưa xong", meta={"delivery_id": "delivery:1"})
    assert host.memory_kernel.living_user.events == []
    assert "TTS chưa xác nhận" in host._chat_history_for_llm()[0]["content"]
    register_public_surface(
        host, delivery_id="delivery:1",
        on_delivered=lambda: host._journal_conversation_turn("assistant", "Lời chưa xong"),
    )
    assert note_public_delivery_outcome(host, delivery_id="delivery:1", status="cancelled")
    assert "âm thanh bị ngắt" in host._chat_history_for_llm()[0]["content"]
    assert host.memory_kernel.living_user.events == []
    assert host.chat_history[0]["content"] == "Lời chưa xong"  # Vẫn thấy text thực.


def test_audio_done_commits_episodic_memory_exactly_once():
    class _Journal:
        def __init__(self):
            self.events = []

        def record_conversation_turn(self, role, text, **_kwargs):
            self.events.append((role, text))

    class _PromptHost(PromptContextBuilderMixin):
        def __init__(self):
            self.chat_history = []
            self.ledger = SessionTurnLedger()
            self.memory_kernel = SimpleNamespace(living_user=_Journal())

        def _ensure_session_turn_ledger(self):
            return self.ledger

    host = _PromptHost()
    host._append_chat_history("assistant", "Đã phát hết", pending_delivery_id="delivery:done")
    host.ledger.record_assistant("Đã phát hết", meta={"delivery_id": "delivery:done"})
    register_public_surface(
        host, delivery_id="delivery:done",
        on_delivered=lambda: host._journal_conversation_turn("assistant", "Đã phát hết"),
    )
    assert not host.memory_kernel.living_user.events
    assert note_public_delivery_outcome(host, delivery_id="delivery:done", status="delivered")
    assert not note_public_delivery_outcome(host, delivery_id="delivery:done", status="delivered")
    assert host.memory_kernel.living_user.events == [("assistant", "Đã phát hết")]
    assert host._chat_history_for_llm()[0]["content"] == "Đã phát hết"
