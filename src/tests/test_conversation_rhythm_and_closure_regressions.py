from __future__ import annotations

from types import SimpleNamespace

from runtime.conversation_handoff import ConversationHandoffRuntime
from runtime.expression_consummation import ExpressionConsummationRuntime
from runtime.expression_urge_runtime import ExpressionUrgeRuntime
from runtime.mental_material_runtime import MentalMaterialRuntime
from runtime.subject_initiative_runtime import _candidate_from_intention
from runtime.ai_orchestrated.random_chat_seed import RandomChatSeed


def test_explicit_topic_closure_is_observed_without_assigning_next_speaker():
    rt = ConversationHandoffRuntime()

    state = rt.observe_user_turn(
        "coi như vụ hả này đi vào dĩ vãng nha. lần sau tôi sẽ nói tử tế hơn mà",
        turn_ref="session_turn:17",
        now=100.0,
    )
    assert state.topic_closed is True
    assert state.user_stance == "topic_closed"

    # Assistant commit chỉ cập nhật provenance. Handoff không quyết ai giữ floor.
    state = rt.observe_assistant_commit(
        "Ừ, vậy nhé. Tôi không giữ làm gì đâu.",
        speech_act="answer",
        now=101.0,
    )
    assert state.topic_closed is True
    assert "next_move_owner" not in state.to_dict()

    # User turn mới trở thành observation mới và tự thay boundary cũ.
    state = rt.observe_user_turn(
        "à mà tôi hỏi cái khác nhé",
        turn_ref="session_turn:18",
        now=110.0,
    )
    assert state.topic_closed is False


def test_topic_closure_marks_previous_public_expression_satiated():
    rt = ExpressionConsummationRuntime(SimpleNamespace(logger=None))
    rt.note_expression(
        content="Một tiếng hả ngắn giống như một khoảng trống.",
        public_text="Một tiếng hả ngắn giống như một khoảng trống.",
        now=100.0,
    )

    outcome = rt.observe_user_turn(
        "coi như vụ hả này đi vào dĩ vãng nha",
        now=101.0,
    )
    assert outcome["outcome"] == "topic_satiated"


def test_expression_boundary_keeps_private_concerns_from_rebuilding_public_urge():
    host = SimpleNamespace()
    rt = ExpressionUrgeRuntime(host)
    rt._ingest_content(
        input_key="concern:concern.hah:1:0",
        content="nghĩ thêm về tiếng hả",
        motive="revisit",
        source="active_concern",
        evidence_refs=(),
        now=100.0,
        base_strength=0.9,
        freshness=0.9,
        concern_id="concern.hah",
    )
    assert rt.wake_pressure(now=100.1) > 0

    rt.mark_conversation_boundary(now=101.0, reason="user_topic_closure", hold_seconds=180.0)
    assert rt.wake_pressure(now=101.1) == 0

    rt.observe(
        (),
        active_concerns=(
            {
                "concern_id": "concern.hah.2",
                "subject": "lại nghĩ thêm về tiếng hả nhưng chỉ nên ở private cognition",
                "state": "simmering",
                "novelty": 0.9,
                "speech_pressure": 1.0,
                "activation": 1.0,
                "metadata": {"event_count": 2},
            },
        ),
        now=102.0,
    )
    assert rt.wake_pressure(now=102.1) == 0


def test_mental_material_from_old_private_concern_is_not_public_after_topic_boundary():
    host = SimpleNamespace()
    rt = MentalMaterialRuntime(host)
    seed = RandomChatSeed.from_material_mapping(
        {
            "decision": "seed",
            "seed_id": "material.hah.old",
            "material_kind": "opinion",
            "premise": "tiếp tục nghĩ về tiếng hả theo một góc riêng đủ cụ thể để material tồn tại",
            "topic_tags": ["hah", "closure"],
            "epistemic_mode": "opinion",
            "confidence": 0.9,
        },
        causal_motive={
            "cause_id": "concern.hah",
            "cause_kind": "active_concern",
            "motive_kind": "continue_concern",
            "strength": 0.9,
            "source_refs": ["concern.hah"],
            "legacy_motive_kind": "want_to_revisit",
        },
        requested_mode="eris_led",
    )
    assert rt.ingest_seed(seed, now=100.0) is not None
    rt.mark_conversation_boundary(now=101.0, reason="user_topic_closure", hold_seconds=180.0)
    assert rt.wake_pressure(now=102.0) == 0
    assert rt.project(now=102.0) == ((), ())


def test_understand_user_can_become_public_candidate_after_topic_closure():
    intention = {
        "intention_id": "intent.understand.1",
        "motive_kind": "understand_user",
        "semantic_goal": "tìm hiểu thêm về user",
        "pressure": 0.65,
        "commitment_strength": 0.60,
        "confidence": 0.75,
        "source": "subject_core",
    }
    candidate = _candidate_from_intention(
        intention,
        revision=1,
        now=100.0,
        runtime_context={
            "conversation_topic_closed": True,
            "user_model_uncertainty": 0.8,
        },
    )
    assert candidate is not None
    assert candidate.candidate.speech_act == "user_probe"
    assert candidate.candidate.seed_id == "intent.understand.1"
    assert "bridge" in candidate.candidate.topic.lower() or "chuyển" in candidate.candidate.topic.lower()


def test_understand_user_stays_private_when_no_reason_to_probe():
    intention = {
        "intention_id": "intent.understand.2",
        "motive_kind": "understand_user",
        "semantic_goal": "tìm hiểu thêm về user",
        "pressure": 0.30,
        "commitment_strength": 0.30,
        "confidence": 0.55,
        "source": "subject_core",
    }
    candidate = _candidate_from_intention(
        intention,
        revision=1,
        now=100.0,
        runtime_context={
            "conversation_topic_closed": False,
            "user_model_uncertainty": 0.2,
        },
    )
    assert candidate is None
