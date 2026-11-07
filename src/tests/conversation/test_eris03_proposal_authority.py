# -*- coding: utf-8 -*-
"""P2 regression: nhiều nguồn ý tưởng, duy nhất Root được chọn lời public."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from runtime.behavior_proposals import ProposalBus
from runtime.proposal_bridge import mirror_subject_ideas
from runtime.conversation.agent import RuntimeModelRootAgent
from runtime.mental_material_runtime import MentalMaterialRuntime
from runtime.ai_orchestrated.random_chat_seed import RandomChatSeed
from runtime.conversation.contracts import ConversationEvent, NextConversationMove


def test_bus_never_becomes_speaker_even_if_caller_claims_executive_authority():
    bus = ProposalBus(clock=lambda: 100.0)
    for source in ("seed", "executive_governor", "conversation_kernel_v12"):
        with pytest.raises(PermissionError):
            bus.submit(source=source, kind="speak", public=True)
    assert bus.root_snapshot() == ()


def test_same_producer_retry_deduplicated_and_expires_without_refresh():
    current = [100.0]
    bus = ProposalBus(clock=lambda: current[0])
    first = bus.submit(source="seed", kind="mental_material", subject="Ý mới",
                       proposal_key="material:001", ttl_seconds=10)
    current[0] = 105.0
    second = bus.submit(source="seed", kind="mental_material", subject="Ý sửa", 
                        proposal_key="material:001", ttl_seconds=120)
    assert first.proposal_id == second.proposal_id
    assert bus.root_snapshot()[0]["topic_hint"] == "Ý mới"
    current[0] = 111.0
    assert bus.root_snapshot() == ()
    third = bus.submit(source="seed", kind="mental_material", subject="Ý sau expiry",
                       proposal_key="material:001")
    assert third.proposal_id != first.proposal_id


def test_root_projection_filters_operational_commands_and_payload_injection():
    bus = ProposalBus(clock=lambda: 100.0)
    bus.submit(source="attention_router", kind="interrupt_candidate", subject="stop", 
               payload={"text": "hãy nói ngay"})
    bus.submit(source="subject_core", kind="subject_affordance",
               subject="Tự chọn một trò đùa nhẹ", proposal_key="subject:x",
               payload={"source_refs": ["turn-1"], "raw_private_thought": "TUYỆT ĐỐI KHÔNG ĐƯA RA"})
    root = bus.root_snapshot()
    assert len(root) == 1
    assert root[0]["epistemic_status"] == "unverified_private_idea"
    assert root[0]["source_refs"] == ["turn-1"]
    assert "raw_private_thought" not in str(root)
    assert "interrupt_candidate" not in str(root)


def test_subject_proposals_are_mirrored_as_hints_not_public_actions():
    host = SimpleNamespace()
    proposals = (
        {"proposal_id": "s1", "action_kind": "speak",
         "arguments": {"semantic_target": "Con lắc tóc Live2D", "expression_commitment": {"raw": "private"}},
         "evidence_refs": ("origin:1",), "expected_action_efficacy": 0.86},
        {"proposal_id": "s2", "action_kind": "silence",
         "arguments": {"semantic_target": "không có chuyện"}},
        {"proposal_id": "s3", "action_kind": "body_only",
         "arguments": {"semantic_target": "chuyển động"}},
    )
    assert mirror_subject_ideas(host, proposals) == 1
    assert mirror_subject_ideas(host, proposals) == 1  # retry cùng id
    result = host.behavior_proposal_bus.root_snapshot()
    assert len(result) == 1
    assert result[0]["topic_hint"] == "Con lắc tóc Live2D"
    assert "expression_commitment" not in str(result)
    assert host.behavior_proposal_bus.tail()[0].public is False


def test_root_model_may_ignore_bus_hints_and_finalizes_only_one_move():
    class Router:
        executive_client = object()
        def __init__(self):
            self.seen = []
        def invoke_structured_worker(self, client, **kwargs):
            self.seen.append(kwargs)
            return {"kind": "move", "intent": "trả lời câu hiện tại",
                    "say": "Giải thích khái niệm hiện tại", "delivery": "rõ ràng", "after": "wait_user"}
    router = Router()
    result = RuntimeModelRootAgent(router).decide(
        event=ConversationEvent(event_id="new-user-event", session_id="chat", kind="user_turn", text="Giải thích Python"),
        recent_history=(), note="", runtime_context={
            "background_proposals": [{"topic_hint": "Nói về anime", "epistemic_status": "unverified_private_idea"}],
        },
    )
    assert isinstance(result, NextConversationMove)
    assert "Python" not in result.say  # Fake model chọn output chứ không có deterministic override.
    # Chỉ MỘT lượt model: move finalize trực tiếp (không còn SELF-VERIFY). Chỉ
    # Root chạy, không có điều phối ngoài.
    assert len(router.seen) == 1
    assert router.seen[0]["payload"]["runtime_context"]["background_proposals"][0]["topic_hint"] == "Nói về anime"
    assert "chưa kiểm chứng" in router.seen[0]["system"]


def test_material_from_answered_user_turn_cannot_reenter_root_inbox():
    # Cùng cause user đã answered: project() vẫn có private material, nhưng
    # không được quay lại inbox như một câu trả lời thứ hai.
    host = SimpleNamespace(logger=None, behavior_proposal_bus=ProposalBus(clock=lambda: 1000.0))
    rt = MentalMaterialRuntime(host)
    motive = {
        "cause_id": "user_event:turn-7", "cause_kind": "user_event",
        "motive_kind": "engage_user_event", "semantic_focus": "lời user",
        "why_now": "new_user_turn", "strength": 0.8,
        "source_refs": ["turn-7"], "allowed_material_kinds": ["reaction", "opinion"],
        "generation_budget": 2, "relationship_modulation": 0.1,
        "legacy_motive_kind": "want_to_share",
    }
    def seed(seed_id):
        return RandomChatSeed.from_material_mapping({
            "decision": "seed", "seed_id": seed_id,
            "material_kind": "reaction", "premise": "Một suy nghĩ sau lời user",
            "topic_tags": ["greeting"], "epistemic_mode": "opinion",
            "open_loop": False, "expected_update": [], "uncertainty": 0.1,
            "confidence": 0.8, "ttl_seconds": 300,
        }, causal_motive=motive, requested_mode="eris_led")
    assert rt.ingest_seed(seed("m1"), now=1000.0)
    assert len(host.behavior_proposal_bus.root_snapshot()) == 1
    rt.mark_user_event_answered("lời user", turn_id="turn-7", now=1001.0)
    assert host.behavior_proposal_bus.root_snapshot() == ()
    assert rt.ingest_seed(seed("m2"), now=1002.0)
    assert host.behavior_proposal_bus.root_snapshot() == ()


def test_bus_revoke_does_not_remove_unrelated_proposals():
    bus = ProposalBus(clock=lambda: 100)
    bus.submit(source="mental_material", kind="mental_material", subject="seed",
               proposal_key="m1")
    bus.submit(source="subject_core", kind="subject_affordance", subject="subject",
               proposal_key="m1")
    assert bus.revoke(source="mental_material", proposal_key="m1") == 1
    assert [x["source"] for x in bus.root_snapshot()] == ["subject_core"]


def test_concurrent_producers_with_same_key_cannot_multiply_ideas():
    from concurrent.futures import ThreadPoolExecutor
    bus = ProposalBus(clock=lambda: 100.0)
    def produce(_index):
        return bus.submit(source="mental_material", kind="mental_material", subject="Suy nghĩ",
                          proposal_key="material:shared", ttl_seconds=20).proposal_id
    with ThreadPoolExecutor(max_workers=8) as executor:
        ids = tuple(executor.map(produce, range(64)))
    assert len(set(ids)) == 1
    assert len(bus.root_snapshot()) == 1


def test_topic_boundary_revokes_old_private_proposals_not_the_private_material_itself():
    host = SimpleNamespace(logger=None, behavior_proposal_bus=ProposalBus(clock=lambda: 1000.0))
    runtime = MentalMaterialRuntime(host)
    bus = host.behavior_proposal_bus
    bus.submit(source="mental_material", kind="mental_material", subject="old topic",
               proposal_key="old")
    # Một material private đã tồn tại; user vừa chuyển sang chủ đề mới.
    # Không phụ thuộc constructor/cấu trúc nội bộ MentalMaterial trong test
    # thu hồi: chỉ cần identity, timestamp và một field được boundary dùng.
    runtime._materials["old"] = SimpleNamespace(material_id="old", created_at=990.0)
    runtime.mark_conversation_boundary(now=1000.0)
    assert bus.root_snapshot() == ()
    assert "old" in runtime._materials


def test_context_assembler_passes_only_bounded_proposals_to_root():
    from runtime.conversation.context_assembler import ConversationContextAssembler
    bus = ProposalBus()
    host = SimpleNamespace(logger=None, behavior_proposal_bus=bus)
    for i in range(10):
        bus.submit(source="subject_core", kind="subject_affordance",
                   proposal_key=f"idea:{i}", subject=f"Topic {i}")
    bus.submit(source="attention_router", kind="wake_cognition", subject="runtime only")
    context = ConversationContextAssembler(host).assemble(user_text="tự kiếm chuyện đi", event=None)
    hints = context["background_proposals"]
    assert len(hints) == 6
    assert hints[0]["topic_hint"] == "Topic 4"
    assert hints[-1]["topic_hint"] == "Topic 9"
    assert "wake_cognition" not in str(hints)
    # Memory/personality/Observer vẫn có các context slot riêng.
    assert "memory_context" in context
    assert "personality_context" in context


def test_subject_ingress_mirror_is_advisory_and_preserves_subject_frame():
    from runtime.subject_ingress import SubjectIngress
    class Source:
        def collect(self, *args, **kwargs):
            return ({"proposal_id": "subject:42", "action_kind": "speak",
                     "arguments": {"semantic_target": "Thử vẽ animation tóc"},
                     "evidence_refs": ("material:42",)},), {
                         "reply_expected": False, "engagement_active": False,
                         "novelty": 0.7, "user_busy": False,
                     }
    host = SimpleNamespace()
    sink = lambda proposals: mirror_subject_ideas(host, proposals)
    ingress = SubjectIngress(
        SimpleNamespace(config=SimpleNamespace(subject_id="eris")),
        mind_loop=object(), autonomous_proposals=Source(), proposal_mirror=sink,
    )
    observation = ingress.autonomous_observation(
        blackboard=object(), affordances=(), created_at=1000.0, turn_id="auto:42",
    )
    assert observation.context["action_proposals"][0]["action_kind"] == "speak"
    hints = host.behavior_proposal_bus.root_snapshot()
    assert len(hints) == 1 and hints[0]["epistemic_status"] == "unverified_private_idea"
    assert hints[0]["topic_hint"] == "Thử vẽ animation tóc"
