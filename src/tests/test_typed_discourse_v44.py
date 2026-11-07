from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from runtime.ai_orchestrated.model_router import (
    AIRuntimeModelRouter,
    ASSISTANT_ACT_SCHEMA,
    PUBLIC_REPLY_SCHEMA,
    SEMANTIC_FRAME_SCHEMA,
)
from runtime.discourse_state import (
    DiscourseActionKind,
    DiscourseStateBuilder,
    assistant_proposition_link,
)
from runtime.discourse_surface import DiscourseSurfaceRenderer
from runtime.memory_recall_adapter import MemoryRecallAdapter
from runtime.semantic_frame import (
    AssistantAct,
    AssistantActKind,
    MemoryAnswerMode,
    MemoryQueryFrame,
    MemoryQueryIntent,
    PropositionMode,
    ReferenceKind,
    ReferenceOperation,
    ReferenceSpeaker,
    SemanticFrame,
    validate_assistant_act,
    validate_semantic_frame,
)
from runtime.session_turn_ledger import SessionTurnLedger


def test_semantic_frame_is_typed_and_still_mapping_compatible() -> None:
    result = validate_semantic_frame(
        {
            "reference": {
                "kind": "recent_user_turn",
                "speaker": "assistant",  # cố ý sai để compatibility matrix sửa
                "operation": "confirm",  # operation này không hợp kind
                "turn_index": 4,
                "turn_offset": 2,
                "confidence": 0.91,
            }
        }
    )

    assert isinstance(result.frame, SemanticFrame)
    assert result.frame.reference.kind is ReferenceKind.NONE
    assert result.frame.reference.speaker is ReferenceSpeaker.NONE
    assert result.frame.reference.operation is ReferenceOperation.NONE
    assert result.frame.reference.turn_offset == 0
    assert dict(result.frame)["reference"]["turn_index"] == 0
    assert "reference.speaker_incompatible:recent_user_turn:assistant" in result.violations
    assert "reference.operation_incompatible:recent_user_turn:confirm" in result.violations


def test_reference_continue_alias_keeps_recent_assistant_context() -> None:
    result = validate_semantic_frame(
        {
            "reference": {
                "kind": "continue",
                "speaker": "assistant",
                "operation": "recall",
                "turn_index": 0,
                "turn_offset": 1,
                "confidence": 0.7,
            }
        }
    )

    assert result.frame.reference.kind is ReferenceKind.RECENT_ASSISTANT_TURN
    assert result.frame.reference.speaker is ReferenceSpeaker.ASSISTANT
    assert result.frame.reply_obligation.must_answer is True
    assert result.frame.reply_obligation.must_not_broad_clarify is True
    assert "reference.kind_alias:continue:recent_assistant_turn" in result.violations


def test_boolean_strings_are_not_treated_as_true() -> None:
    result = validate_semantic_frame(
        {
            "literal_question": "false",
            "reply_obligation": {
                "must_answer": "false",
                "must_not_broad_clarify": "true",
                "must_not_call_tool": 0,
            },
        }
    )

    assert result.frame.literal_question is False
    assert result.frame.reply_obligation.must_answer is True  # default an toàn
    assert result.frame.reply_obligation.must_not_broad_clarify is False
    assert result.frame.reply_obligation.must_not_call_tool is False
    assert "literal_question_must_be_boolean:str" in result.violations
    assert "reply_obligation.must_answer_must_be_boolean:str" in result.violations


def test_assistant_act_contract_prevents_acknowledgement_from_becoming_proposition() -> None:
    ack = validate_assistant_act(
        {
            "kind": "acknowledgement",
            "proposition_mode": "new",
            "confidence": 0.9,
        }
    )
    assert ack.act.kind is AssistantActKind.ACKNOWLEDGEMENT
    assert ack.act.proposition_mode is PropositionMode.NONE

    proposition_root, active_root = assistant_proposition_link(
        assistant_act=ack.act,
        assistant_turn_index=9,
        referenced_root_turn_index=0,
        previous_active_root_turn_index=3,
    )
    assert proposition_root == 0
    assert active_root == 3


def test_assistant_act_contract_normalizes_observed_compound_web_labels() -> None:
    invitation = validate_assistant_act(
        {
            "kind": "accept_invitation_with_condition",
            "proposition_mode": "new",
        }
    )
    follow_up = validate_assistant_act(
        {
            "kind": "acknowledge_and_question",
            "proposition_mode": "new",
        }
    )

    assert invitation.act.kind is AssistantActKind.ANSWER
    assert invitation.act.proposition_mode is PropositionMode.NEW
    assert follow_up.act.kind is AssistantActKind.QUESTION
    assert follow_up.act.proposition_mode is PropositionMode.NEW

    short_accept = validate_assistant_act(
        {"kind": "accept", "proposition_mode": "new"}
    )
    assert short_accept.act.kind is AssistantActKind.ANSWER


def test_semantic_frame_unwraps_v1_provider_wrapper() -> None:
    result = validate_semantic_frame(
        {
            "v1": {
                "user_act": "invitation",
                "target": "assistant",
                "reply_obligation": {"must_answer": True},
            }
        }
    )

    assert result.frame.user_act == "invitation"
    assert result.frame.target == "assistant"
    assert "semantic_frame.wrapper_alias:v1" in result.violations


def test_confirmation_continues_existing_proposition_root() -> None:
    act = AssistantAct(
        kind=AssistantActKind.CONFIRMATION,
        proposition_mode=PropositionMode.CONTINUE,
        confidence=0.99,
    )
    root, active = assistant_proposition_link(
        assistant_act=act,
        assistant_turn_index=7,
        referenced_root_turn_index=2,
        previous_active_root_turn_index=1,
    )
    assert root == 2
    assert active == 2


def test_open_question_candidate_excludes_already_answered_user_question() -> None:
    ledger = SessionTurnLedger()
    question = ledger.record_user("Một câu hỏi đã được trả lời?")
    ledger.record_assistant(
        "Đây là câu trả lời.",
        meta={
            "assistant_act": {
                "kind": "answer",
                "proposition_mode": "new",
                "confidence": 0.9,
            },
            "discourse": {
                "reply_to_user_turn_index": question.index,
                "proposition_root_turn_index": 2,
                "active_proposition_root_turn_index": 2,
            },
        },
    )
    current = ledger.record_user("Lượt hiện tại")
    state = DiscourseStateBuilder(ledger).build(current_turn_index=current.index)
    assert state["candidate_references"]["open_question_turn_index"] == 0


def test_discourse_renderer_is_separate_and_exact_repeat_beats_model_wording() -> None:
    ledger = SessionTurnLedger()
    target = ledger.record_user("nguyên văn cần giữ")
    current = ledger.record_user("câu hiện tại")
    frame = validate_semantic_frame(
        {
            "reference": {
                "kind": "recent_user_turn",
                "speaker": "user",
                "operation": "repeat",
                "turn_index": target.index,
                "turn_offset": 0,
                "confidence": 0.95,
            }
        }
    ).frame
    from runtime.discourse_state import DiscourseResolver

    resolution = DiscourseResolver(ledger).resolve(
        frame,
        current_user_turn_index=current.index,
    )
    surface = DiscourseSurfaceRenderer().render(
        resolution.action,
        model_text="Model đã tự diễn lại sai.",
    )
    assert resolution.action.kind is DiscourseActionKind.REPEAT_TURN
    assert surface.deterministic is True
    assert "nguyên văn cần giữ" in surface.text
    assert "Model" not in surface.text


def test_typed_memory_query_builds_ir_without_compiling_raw_text() -> None:
    query = MemoryQueryFrame(
        intent=MemoryQueryIntent.RECALL_FACT,
        subject="user",
        predicate_hint="user.name",
        temporal_scope="current",
        answer_mode=MemoryAnswerMode.EXACT,
        confidence=0.96,
    )
    ir = MemoryRecallAdapter._ir_from_typed_query(
        query,
        raw_text="câu bề mặt cố ý không chứa chữ tên",
    )

    assert ir is not None
    assert ir.target.fact_key == "user.name"
    assert ir.intercept_permission is True
    assert ir.raw_text == "câu bề mặt cố ý không chứa chữ tên"
    assert ir.evidence == ("semantic_frame.memory_query",)


def test_public_schema_requires_memory_query_and_assistant_act() -> None:
    assert "memory_query" in SEMANTIC_FRAME_SCHEMA["required"]
    assert PUBLIC_REPLY_SCHEMA["properties"]["assistant_act"] is ASSISTANT_ACT_SCHEMA
    assert "assistant_act" in PUBLIC_REPLY_SCHEMA["required"]


def test_model_router_returns_typed_private_contracts_for_runtime() -> None:
    router = AIRuntimeModelRouter(llm_service=None, logger=None)
    payload = router._compat_public_envelope(
        {
            "semantic_frame": {
                "reference": {
                    "kind": "long_term_memory",
                    "speaker": "none",
                    "operation": "recall",
                    "turn_index": 0,
                    "turn_offset": 0,
                    "confidence": 0.95,
                },
                "memory_query": {
                    "intent": "recall_fact",
                    "subject": "user",
                    "predicate_hint": "user.name",
                    "object_hint": "",
                    "temporal_scope": "current",
                    "answer_mode": "exact",
                    "confidence": 0.95,
                },
            },
            "assistant_act": {
                "kind": "answer",
                "proposition_mode": "new",
                "confidence": 0.9,
            },
            "text": "Tên bạn là Khang.",
            "should_speak": True,
        }
    )

    assert isinstance(payload["_typed_semantic_frame"], SemanticFrame)
    assert isinstance(payload["_typed_assistant_act"], AssistantAct)
    assert payload["semantic_frame"]["memory_query"]["predicate_hint"] == "user.name"


def test_discourse_state_does_not_duplicate_chat_text() -> None:
    ledger = SessionTurnLedger()
    ledger.record_user("chuỗi bí mật chỉ được có trong chat history")
    current = ledger.record_user("current")
    context = DiscourseStateBuilder(ledger).prompt_context(current_turn_index=current.index)
    assert "chuỗi bí mật" not in context
    assert "history_turn_map" in context


def test_discourse_module_has_no_decision_or_surface_dependency() -> None:
    root = Path(__file__).resolve().parents[1]
    source = (root / "runtime" / "discourse_state.py").read_text(encoding="utf-8")
    assert "from domain import Decision" not in source
    assert "Bạn vừa nói" not in source
    assert "Tôi vừa nói" not in source
