# -*- coding: utf-8 -*-
"""Task 1: không lấy câu nói, ID legacy hoặc fiction làm proof thế giới."""
from dataclasses import FrozenInstanceError
from types import SimpleNamespace

import pytest

from memory.memory_record import MemoryRecord
from runtime.conversation.claim_evidence import (
    build_evidence_registry, extend_evidence_registry, render_evidence_context,
)
from runtime.conversation.contracts import ConversationEvent
from runtime.conversation.context import RootCapabilityRegistry
from runtime.session_turn_ledger import SessionTurn


def _event():
    return ConversationEvent(event_id="evt-1", session_id="session-1", kind="user_turn",
                             text="Hôm qua tôi đi Paris", generation=4)


def _registry():
    turns = [SessionTurn(index=1, role="user", text="Trong truyện có rồng", timestamp=100,
                         meta={"scope": "fiction"}),
             SessionTurn(index=2, role="assistant", text="Tôi đã gửi file", timestamp=110)]
    record = MemoryRecord(id="legacy001", layer="semantic", text="User đã tới Paris",
                          metadata={"untrusted": {"nested": [1, 2]}})
    return build_evidence_registry(event=_event(), state=SimpleNamespace(version=12, last_applied_turn_seq=8),
                                   affect=None, report=None, ledger_turns=turns,
                                   memory_records=[record], sampled_at=123.0)


def test_registry_preserves_user_assistant_and_fiction_scope():
    registry = _registry()
    entries = {x.ref: x for x in registry.entries}
    assert entries["ledger:session-1:1"].scope == "fiction"
    assert entries["ledger:session-1:1"].subject == "user"
    assert entries["ledger:session-1:2"].source_kind == "assistant_utterance"
    assert entries["ledger:session-1:2"].scope == "utterance"
    assert entries["event:evt-1"].scope == "utterance"


def test_unresolved_legacy_ref_is_unknown():
    record = next(x for x in _registry().entries if x.ref == "memory:legacy001")
    assert record.status == "unknown"
    assert record.scope == "unresolved"
    assert "legacy provenance unresolved" in render_evidence_context(_registry())


def test_registry_and_nested_content_are_read_only():
    registry = _registry()
    with pytest.raises((AttributeError, FrozenInstanceError)):
        registry.entries = ()
    with pytest.raises(TypeError):
        registry.entries[0].content["revision"] = 100
    assert registry.fingerprint == _registry().fingerprint


def test_source_id_without_entailment_is_not_world_proof():
    # Có ref thực nhưng chỉ chứng minh Eris ĐÃ NÓI; không có action receipt.
    registry = _registry()
    assistant = next(x for x in registry.entries if x.source_kind == "assistant_utterance")
    assert assistant.content["text"] == "Tôi đã gửi file"
    assert assistant.scope == "utterance"
    assert not any(x.source_kind == "capability_result" for x in registry.entries)


def test_capability_error_is_not_action_success():
    registry = RootCapabilityRegistry({})
    event = _event()
    failed = registry.record_result(name="work", invocation_id="cap:one", event=event,
                                    run_id="run-1", result={"status": "failed", "error": "Timeout"})
    evidence = extend_evidence_registry(
        __import__('dataclasses').replace(_registry(), run_id="run-1"), [failed]
    )
    item = next(x for x in evidence.entries if x.ref == "capability:cap:one")
    assert item.status == "unknown"
    assert item.content["status"] == "failed"
    assert "capability_result" not in evidence.allowed_domains


def test_result_receipts_are_bound_to_event_run_generation():
    event = _event()
    store = RootCapabilityRegistry({})
    a = store.record_result(name="research", invocation_id="cap-a", event=event,
                            run_id="run-a", result={"status": "completed", "source_refs": ["web:1"]})
    store.record_result(name="research", invocation_id="cap-b", event=event,
                        run_id="run-b", result={"status": "completed"})
    pinned = __import__('dataclasses').replace(_registry(), run_id="run-a")
    registry = extend_evidence_registry(pinned, store.result_receipts_for("evt-1", run_id="run-a", generation=4))
    assert "capability:cap-a" in {e.ref for e in registry.entries}
    assert "capability:cap-b" not in {e.ref for e in registry.entries}
    assert store.result_receipts_for("evt-1", run_id="run-a", generation=5) == ()
    assert a.source_refs == ("web:1",)
    assert registry.fingerprint != pinned.fingerprint
    with pytest.raises(TypeError):
        a.result["status"] = "success"


def test_public_memory_text_and_records_share_one_selection():
    """Regression: recall() phải được gọi đúng một lần cho text VÀ sources."""
    from memory.memory_kernel import MemoryKernel
    from memory.memory_kernel_contracts import PublicMemoryContext
    from memory.memory_packet_builder import MemoryPacket
    record = MemoryRecord(id="r1", layer="semantic", text="Câu user nói", metadata={})
    packet = MemoryPacket(source_records=(record,))

    class Core:
        calls = 0
        def recall(self, query):
            assert query == "câu hỏi"
            self.calls += 1
            return packet
        def render_context_for_packet(self, selected, *, max_lines):
            assert selected is packet and max_lines == 64
            return "memory_context_from_same_packet"

    class LivingUser:
        calls = 0
        def compile_context(self, query, *, session_id, token_budget):
            assert query == "câu hỏi" and session_id == "ss" and token_budget == 6000
            self.calls += 1
            return SimpleNamespace(items=(), episodes=(), action_context=None,
                                   render_prompt_context=lambda: "")

    kernel = object.__new__(MemoryKernel)
    kernel.core = Core()
    kernel.living_user = LivingUser()
    result = kernel.build_public_context("câu hỏi", session_id="ss")
    assert isinstance(result, PublicMemoryContext)
    assert result.text == "memory_context_from_same_packet"
    assert result.records == (record,)
    assert result.degraded is False
    assert kernel.core.calls == kernel.living_user.calls == 1


def test_registry_reads_real_report_items_not_ids_alone():
    from runtime.source_grounding import GroundingEvidence
    report = SimpleNamespace(screen_evidence_present=True, evidence_items=[
        GroundingEvidence(evidence_id="vision:verified", source="current_screen",
                          role="primary", text="Cửa sổ editor hiện đang mở"),
        GroundingEvidence(evidence_id="memory:unresolved", source="memory",
                          role="supporting", text="Một câu ghi nhớ legacy"),
    ], evidence_ids=["phantom:no_evidence_item"])
    registry = build_evidence_registry(event=_event(), state=None, affect=None,
                                       report=report, ledger_turns=[], memory_records=[], sampled_at=4)
    items = {x.ref: x for x in registry.entries}
    assert items["vision:verified"].status == "supported"
    assert items["memory:unresolved"].status == "unknown"
    assert "phantom:no_evidence_item" not in items


def test_public_kernel_root_writer_receipt_same_generation():
    """Full public path (Root/Kernel/Writer), chỉ mock model/network; không mock owner."""
    from runtime.conversation import ConversationKernel
    from runtime.conversation.agent import RuntimeModelRootAgent
    from runtime.conversation.runtime_writer import RuntimeConversationWriter

    class Router:
        executive_client = object()
        steps = 0
        writer_kwargs = None
        def invoke_structured_worker(self, client, **kwargs):
            self.steps += 1
            if self.steps == 1:
                return {"kind": "use_capability", "capability": "work", "arguments": {}}
            return {"kind": "move", "intent": "báo việc", "say": "Báo trạng thái thật",
                    "delivery": "gọn", "after": "wait_user"}
        def generate_public_reply(self, **kwargs):
            self.writer_kwargs = kwargs
            return {"text": "Việc mới bắt đầu.", "speech_text": "Việc mới bắt đầu."}

    class Work:
        def invoke_for_root(self, **kwargs):
            return {"status": "started", "job_id": "work:123"}

    class Delivery:
        def deliver(self, **kwargs):
            return None

    router = Router()
    registry = RootCapabilityRegistry({"work": Work()})
    incoming = ConversationEvent(event_id="user:e2e", session_id="s", kind="user_turn",
                                 text="Làm giúp tôi", generation=0)
    sources = build_evidence_registry(event=incoming, state=None, affect=None,
                                      report=None, ledger_turns=(), memory_records=(), sampled_at=1.0)
    kernel = ConversationKernel(RuntimeModelRootAgent(router),
                                writer=RuntimeConversationWriter(SimpleNamespace(ai_model_router=router)),
                                delivery=Delivery())
    result = kernel.handle_event(incoming, runtime_context={
        "__root_capabilities": registry, "__evidence_registry": sources,
    })
    assert result.status == "delivering"
    assert result.event.generation == 1  # Kernel chuẩn hóa generation từ 0.
    evidence = result.rendered.metadata["evidence_registry"]
    assert evidence.run_id == result.turn.run_id
    assert evidence.generation == result.turn.generation
    capability = next(e for e in evidence.entries if e.source_kind == "capability_result")
    assert capability.status == "unknown" and capability.content["status"] == "started"
    assert "status=unknown" in router.writer_kwargs["grounding_context"]
    assert "job_id" not in router.writer_kwargs["grounding_context"]  # Không đẩy toàn bộ raw tool/result.
