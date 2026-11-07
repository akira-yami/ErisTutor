from __future__ import annotations

import logging
from types import SimpleNamespace

from runtime.chat_cycle import ChatCycleMixin
from runtime.conversation.agent import RuntimeModelRootAgent
from runtime.conversation.contracts import ConversationEvent
from runtime.conversation.delivery import SurfaceEnvelope
from runtime.conversation.kernel import ConversationKernel
from runtime.conversation.writer import RenderedConversationMove
from runtime.input_revision_buffer import InputRevisionBuffer
from runtime.session_turn_ledger import SessionTurnLedger


def test_repeated_messages_are_counted_and_preserved():
    buffer = InputRevisionBuffer(clock=lambda: 20)
    buffer.begin_turn("xin chào", timestamp=1)
    buffer.submit_revision("alo", timestamp=2, invalidate_active=False)
    buffer.submit_revision("alo", timestamp=3, invalidate_active=False)
    snapshot = buffer.snapshot(claim=True)
    assert snapshot.canonical_text == "xin chào\nalo\nalo"
    assert snapshot.to_dict()["message_count"] == 3
    assert snapshot.to_dict()["wait_seconds"] == 19
    assert snapshot.to_dict()["message_timestamps"] == [1, 2, 3]
    assert snapshot.stale_marked_revisions == ()
    assert not buffer.has_pending_revision()


def test_initial_burst_keeps_message_boundaries_instead_of_counting_lines():
    buffer = InputRevisionBuffer()
    snapshot = buffer.begin_turn("code\nline two\nalo", timestamp=1,
                                 entries=[(1, "code\nline two"), (2, "alo")])
    assert snapshot.to_dict()["message_count"] == 2
    assert snapshot.canonical_text == "code\nline two\nalo"


def _run_batched_turn(monkeypatch, *, during_writer=False, during_delivery=False):
    monkeypatch.setenv("ERIS_ROOT_SESSION_BOOTSTRAP", "0")
    requests = []
    delivered = []

    class Router:
        executive_client = object()
        def invoke_structured_worker(self, client, **kwargs):
            requests.append(kwargs["payload"])
            return {"kind": "move", "intent": "trả lời", "say": "Tôi nghe đây.",
                    "delivery": "tự nhiên", "after": "wait_user"}

    class Host(ChatCycleMixin):
        def __init__(self):
            self.logger = logging.getLogger("batch-test")
            self._chat_processing = False
            self.rapid_burst_window = 0
            self.input_revision_buffer = InputRevisionBuffer(debounce_seconds=0)
            self.pending = []
            self.conversation_id = "s"
            self.audio_revocations = []
            self.cycles = 0
            self.conversation_kernel = ConversationKernel(RuntimeModelRootAgent(Router()))
            self.conversation_kernel.writer = SimpleNamespace(realize=self.write)
            self.conversation_kernel.delivery = SimpleNamespace(deliver=self.deliver)
        def _supersede_tts_for_new_user_turn(self, reason):
            self.audio_revocations.append(reason)
        def deliver(self, **kwargs):
            delivered.append(kwargs)
            if during_delivery and len(delivered) == 1:
                self.pending = [(4, "vẫn chưa có chữ")]
        def _link_previous_public_turn_for_chat(self, _ts): return None
        def _previous_public_turn_context(self, _turn): return ""
        def _poll_ready_chat_events(self):
            pending, self.pending = self.pending, []
            return pending
        def write(self, **kwargs):
            if during_writer and len(requests) == 1:
                self.pending = [(4, "vẫn chưa có tiếng")]
            return RenderedConversationMove(surface=SurfaceEnvelope(display_text="Tôi nghe đây."))
        def _run_chat_cycle_inner(self, text, **kwargs):
            self.cycles += 1
            if self.cycles == 1:
                self.pending = [(2, "alo alo"), (3, "alo alo")]
            view = {}
            def collect(*, claim=False):
                snapshot = self._collect_user_input_batch(claim=claim)
                view["snapshot"] = snapshot
                return snapshot
            self.outcome = self.conversation_kernel.handle_event(
                ConversationEvent(event_id="e" + str(self.cycles), session_id="s", kind="user_turn", text=text),
                runtime_context={"__root_input_batch": collect, "__root_input_batch_view": view},
            )

    host = Host()
    host._run_chat_cycle("xin chào", user_timestamp=1)
    return host, requests, delivered


def test_queued_messages_go_into_one_root_request_before_writer(monkeypatch):
    host, requests, delivered = _run_batched_turn(monkeypatch)
    assert len(requests) == 1
    event = requests[0]["event"]
    assert event["text"] == "xin chào\nalo alo\nalo alo"
    assert event["metadata"]["user_input_batch"]["message_count"] == 3
    assert len(delivered) == 1
    assert host.outcome.event.text == event["text"]


def test_input_arriving_after_request_is_merged_before_any_audio_dispatch(monkeypatch):
    host, requests, delivered = _run_batched_turn(monkeypatch, during_writer=True)
    assert len(requests) == 2
    assert len(delivered) == 1  # first candidate never queues TTS then gets cancelled
    assert requests[-1]["event"]["text"] == "xin chào\nalo alo\nalo alo\nvẫn chưa có tiếng"
    assert requests[-1]["event"]["metadata"]["user_input_batch"]["message_count"] == 4
    assert host.outcome.status == "delivering"


def test_batch_ledger_retains_count_and_cannot_rewrite_a_delivered_exchange():
    ledger = SessionTurnLedger()
    turn = ledger.record_user("xin chào", timestamp=1)
    updated = ledger.update_user_batch(turn.index, "xin chào\nalo\nalo", metadata={"message_count": 3})
    assert updated.meta["user_input_batch"]["message_count"] == 3
    assert ledger.user_turns()[0].text == "xin chào alo alo"
    ledger.record_assistant("Chào.")
    assert ledger.update_user_batch(turn.index, "rewrite", metadata={}) is None


def test_input_after_tts_dispatch_retires_audio_before_root_replacement(monkeypatch):
    host, requests, delivered = _run_batched_turn(monkeypatch, during_delivery=True)
    assert len(requests) == 2
    assert len(delivered) == 2
    assert host.audio_revocations == ["user_chat_supersede"]
    assert requests[1]["event"]["metadata"]["user_input_batch"]["message_count"] == 4
    assert requests[1]["event"]["text"].endswith("vẫn chưa có chữ")


def test_real_orchestrator_extends_one_user_turn_and_uses_new_request_id(monkeypatch):
    import runtime.conversation.bootstrap as bootstrap
    import runtime.conversation.context_assembler as context
    import runtime.conversation.evidence_assembler as evidence
    import runtime.conversation.observation_bridge as observation
    from runtime.ai_orchestrated.orchestrator import AIChatOrchestrator
    monkeypatch.setenv("ERIS_ROOT_SESSION_BOOTSTRAP", "0")
    requests, delivered, writer_calls = [], [], []

    class Router:
        executive_client = object()
        def invoke_structured_worker(self, client, **kw):
            requests.append(kw["payload"])
            return {"kind": "move", "intent": "đáp lời", "say": "Tôi đây.",
                    "delivery": "tự nhiên", "after": "wait_user"}

    class Host(ChatCycleMixin):
        def __init__(self):
            self.logger = logging.getLogger("real-batch-test")
            self._chat_processing = False
            self.rapid_burst_window = 0
            self.input_revision_buffer = InputRevisionBuffer(debounce_seconds=0)
            self.pending = []
            self.chat_history = []
            self.ledger = SessionTurnLedger()
            self.kernel = ConversationKernel(RuntimeModelRootAgent(Router()))
            self.kernel.writer = SimpleNamespace(realize=self.write)
            self.kernel.delivery = SimpleNamespace(deliver=lambda **kw: delivered.append(kw))
        def _link_previous_public_turn_for_chat(self, ts): return None
        def _previous_public_turn_context(self, turn): return ""
        def _ensure_session_turn_ledger(self): return self.ledger
        def _append_chat_history(self, role, content): self.chat_history.append(dict(role=role, content=content))
        def _poll_ready_chat_events(self):
            events, self.pending = self.pending, []
            return events
        def _run_chat_cycle_inner(self, text, **kw):
            return AIChatOrchestrator(self)._run_v12(text, cycle_start=kw["cycle_start"])
        def write(self, **kw):
            writer_calls.append(1)
            if len(writer_calls) == 1:
                self.pending = [(4, "vẫn chưa có tiếng")]
            return RenderedConversationMove(surface=SurfaceEnvelope(display_text="Tôi đây."))
    host = Host()
    def assemble(**kw):
        if not requests:
            host.pending = [(2, "alo"), (3, "alo")]
        return {}
    monkeypatch.setattr(bootstrap, "ensure_conversation_kernel", lambda h: h.kernel)
    monkeypatch.setattr(context, "ensure_conversation_context_assembler", lambda h: SimpleNamespace(assemble=assemble))
    monkeypatch.setattr(evidence, "ensure_conversation_evidence_assembler", lambda h: SimpleNamespace(assemble_chat=lambda *a, **kw: {}))
    monkeypatch.setattr(observation, "ensure_conversation_observation_bridge", lambda h: SimpleNamespace(observe_user_turn=lambda *a, **kw: {}))
    host._run_chat_cycle("xin chào", user_timestamp=1)
    assert len(requests) == 2
    assert requests[0]["event"]["event_id"] != requests[1]["event"]["event_id"]
    assert requests[0]["event"]["metadata"]["user_input_batch"]["message_count"] == 3
    assert requests[1]["event"]["metadata"]["user_input_batch"]["message_count"] == 4
    assert len(delivered) == 1
    assert len(host.ledger.user_turns()) == 1
    assert len(host.chat_history) == 1
    assert host.chat_history[0]["content"] == "xin chào\nalo\nalo\nvẫn chưa có tiếng"
