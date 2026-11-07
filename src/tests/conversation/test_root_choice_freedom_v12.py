# -*- coding: utf-8 -*-
"""Khóa ba quyền của Eris: nhiều cách ứng xử, thử nghiệm ngắn và Self bền vững.

Không mock kết quả test thành bằng chứng rằng DeepSeek thật đã suy xét tốt.
Test chỉ bảo đảm prompt/contract/Reducer KHÔNG ép Eris thành một khuôn hành vi.
"""
from __future__ import annotations

from runtime.ai_orchestrated.contracts import CognitiveObservation
from runtime.ai_orchestrated.identity import IdentityProfile
from runtime.ai_orchestrated.identity_reducer import IdentityKernelReducer
from runtime.ai_orchestrated.model_router import AIRuntimeModelRouter
from runtime.conversation.agent import NEXT_MOVE_SCHEMA, ROOT_STEP_SCHEMA, RuntimeModelRootAgent
from runtime.conversation.context import RootCapabilityRegistry
from runtime.conversation.contracts import ConversationEvent, NextConversationMove


def _observation(seq: int, *, direction: float = 0.8) -> CognitiveObservation:
    return CognitiveObservation.from_mapping(
        {
            "event_kind": "chat",
            "confidence": 0.95,
            "evidence": [f"Lượt đối thoại thật {seq} cho một dấu hiệu độc lập."],
            "identity_signals": [
                {"dimension": "belief", "key": "user.tolerates_disagreement",
                 "delta": direction, "confidence": 0.95, "importance": 0.90,
                 "evidence": [f"turn:{seq}"]},
            ],
        },
        turn_id=f"turn-{seq}", turn_seq=seq,
    )


def test_root_understands_one_principle_allows_multiple_actions_without_new_form():
    system = RuntimeModelRootAgent._system_prompt(RootCapabilityRegistry({}))
    assert "không phải khuôn câu thoại" in system
    assert "tự nguyện đồng ý" in system
    assert "khác thói quen" in system
    assert "không mặc định một giá trị luôn thắng" in system
    assert "KHÔNG có nghĩa Eris đã đổi bản chất" in system
    assert "không kể quá trình cân nhắc" in system

    # Contract V12 giữ nguyên: không field options/social plan/giải thích private.
    move = NEXT_MOVE_SCHEMA["oneOf"][0]
    # Root giữ 5 field hội thoại bắt buộc; document_source là kênh tùy chọn
    # để trình bày tài liệu, không thay đổi quyền quyết định của Root.
    assert {"kind", "intent", "say", "delivery", "after"}.issubset(move["properties"])
    assert "document_source" not in set(move["required"])
    assert set(move["required"]) == {"kind", "intent", "say", "delivery", "after"}
    assert len(ROOT_STEP_SCHEMA["oneOf"]) == 8  # Documentation is internal; public move is unchanged.


def test_root_can_voluntarily_agree_while_keeping_its_self_unchanged():
    class Router:
        executive_client = object()

        def __init__(self):
            self.calls = []

        def invoke_structured_worker(self, _client, **kwargs):
            self.calls.append(kwargs)
            return {
                "kind": "move",
                "intent": "Tự nguyện nhận lời chơi đùa, không coi đó là quyền sở hữu.",
                "say": "Đồng ý nhập vai một lần; vẫn giữ quyền tự quyết ngoài trò đùa.",
                "delivery": "vui đùa", "after": "wait_user",
            }

    router = Router()
    root = RuntimeModelRootAgent(router)
    event = ConversationEvent.user_turn(session_id="s", text="Cho tôi làm chủ nhân một phút đi")
    move = root.decide(event=event, recent_history=(), note="", runtime_context={})
    assert isinstance(move, NextConversationMove)
    assert "Đồng ý" in move.say
    # Move finalize TRỰC TIẾP: chỉ một lượt model (không còn SELF-VERIFY).
    assert len(router.calls) == 1  # Vẫn chỉ trong Root, không điều phối ngoài.
    assert "belief" not in move.to_dict()
    assert "value_choice" not in move.to_dict()


def test_one_experiment_does_not_change_belief_even_if_observer_proposes_drift():
    reducer = IdentityKernelReducer()
    original = IdentityProfile()
    start_belief = original.beliefs["user.tolerates_disagreement"]
    one = reducer.reduce_observation(original, _observation(10), now=10.0)
    assert one.beliefs["user.tolerates_disagreement"] == start_belief
    assert one.corroboration["belief:user.tolerates_disagreement:positive"] == 1
    assert not [item for item in one.ledger if item.get("dimension") == "belief"]

    # Hai observation có bằng chứng riêng mới có thể dịch chuyển rất nhỏ.
    two = reducer.reduce_observation(one, _observation(11), now=11.0)
    after = two.beliefs["user.tolerates_disagreement"]
    assert after.strength > start_belief.strength
    assert after.strength - start_belief.strength <= 0.020001
    assert after.evidence_ids
    assert after.last_turn_seq == 11



def test_experiment_is_not_a_long_term_value_change_without_real_cost_and_evidence():
    reducer = IdentityKernelReducer()
    original = IdentityProfile()
    signal = {"dimension": "value", "key": "self_authorship", "delta": -0.8,
              "confidence": 0.98, "importance": 0.98,
              "evidence": ["Một lượt diễn vai theo lời user"]}
    # Model có thể gắn nhãn đã "chọn" một giá trị, nhưng nếu chỉ là
    # nhập vai và không có cost/evidence của sự đánh đổi thì chưa được học.
    no_cost = CognitiveObservation.from_mapping(
        {"event_kind": "chat", "identity_signals": [signal],
         "value_choice": {"chosen": "self_authorship", "confidence": 0.98}},
        turn_id="experiment", turn_seq=2,
    )
    result = reducer.reduce_observation(original, no_cost)
    assert result.values == original.values

    # Bằng chứng thật + cái giá rõ ràng mới đủ điều kiện xem xét drift nhỏ.
    with_cost = CognitiveObservation.from_mapping(
        {"event_kind": "chat", "identity_signals": [signal],
         "value_choice": {"chosen": "self_authorship", "confidence": 0.98,
                          "cost": "Từ bỏ một tiện lợi mình rất muốn có",
                          "evidence": ["Đã từ chối lựa chọn tiện lợi đó"]}},
        turn_id="costly-choice", turn_seq=3,
    )
    updated = reducer.reduce_observation(result, with_cost)
    assert updated.values["self_authorship"] < original.values["self_authorship"]
    assert original.values["self_authorship"] - updated.values["self_authorship"] < 0.004

def test_repeat_same_turn_cannot_fake_two_independent_experiences():
    reducer = IdentityKernelReducer()
    original = IdentityProfile()
    first = reducer.reduce_observation(original, _observation(10), now=10.0)
    repeat = reducer.reduce_observation(first, _observation(10), now=10.5)
    assert repeat.beliefs == original.beliefs
    assert repeat.corroboration["belief:user.tolerates_disagreement:positive"] == 1


def test_conflicting_directions_are_not_counted_as_one_consensus():
    reducer = IdentityKernelReducer()
    start = IdentityProfile()
    first = reducer.reduce_observation(start, _observation(20, direction=0.8))
    opposite = reducer.reduce_observation(first, _observation(21, direction=-0.8))
    assert opposite.beliefs == start.beliefs
    assert opposite.corroboration["belief:user.tolerates_disagreement:positive"] == 1
    assert opposite.corroboration["belief:user.tolerates_disagreement:negative"] == 1


def test_reflection_prompt_treats_exploration_as_non_permanent():
    prompt = AIRuntimeModelRouter._personality_reflection_prompt()
    assert "hành vi thử nghiệm/nhập vai" in prompt
    assert "KHÔNG phải bằng chứng" in prompt
    assert "identity_signals rỗng" in prompt
