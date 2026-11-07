from __future__ import annotations

from pathlib import Path

from runtime.conversation_continuity import ConversationContinuityAdapter
from runtime.discourse_state import (
    DiscourseActionKind,
    assistant_proposition_link,
)
from runtime.discourse_surface import DiscourseSurfaceRenderer
from runtime.semantic_frame import (
    AssistantAct,
    AssistantActKind,
    PropositionMode,
    validate_semantic_frame,
)
from runtime.session_turn_ledger import SessionTurnLedger


def _frame(
    *,
    kind: str,
    speaker: str,
    operation: str,
    turn_index: int = 0,
    confidence: float = 0.97,
    memory_query: dict | None = None,
):
    return validate_semantic_frame(
        {
            "user_act": "typed_test_act",
            "target": speaker,
            "reference": {
                "kind": kind,
                "speaker": speaker,
                "operation": operation,
                "turn_index": turn_index,
                "turn_offset": 0,
                "confidence": confidence,
            },
            "memory_query": memory_query or {
                "intent": "none",
                "subject": "",
                "predicate_hint": "",
                "object_hint": "",
                "temporal_scope": "current",
                "answer_mode": "exact",
                "confidence": 0.0,
            },
        }
    ).frame


def test_exact_recent_user_recall_depends_on_frame_not_user_phrase() -> None:
    ledger = SessionTurnLedger()
    target = ledger.record_user("câu cần được nhắc lại")
    current = ledger.record_user("một cách diễn đạt chưa từng xuất hiện trong source")

    resolution = ConversationContinuityAdapter(ledger).resolve(
        _frame(
            kind="recent_user_turn",
            speaker="user",
            operation="repeat",
            turn_index=target.index,
        ),
        current_user_turn_index=current.index,
    )
    surface = DiscourseSurfaceRenderer().render(resolution.action)

    assert resolution.action.kind is DiscourseActionKind.REPEAT_TURN
    assert surface.deterministic is True
    assert "câu cần được nhắc lại" in surface.text


def test_assistant_proposition_chain_keeps_original_root_without_surface_guessing() -> None:
    ledger = SessionTurnLedger()
    root = ledger.record_assistant(
        "Tôi thấy bạn thì dễ chịu hơn một chút.",
        meta={
            "assistant_act": {
                "kind": "assertion",
                "proposition_mode": "new",
                "confidence": 0.99,
            },
            "discourse": {
                "proposition_root_turn_index": 1,
                "active_proposition_root_turn_index": 1,
            },
        },
    )
    user = ledger.record_user("câu nối mạch bất kỳ")
    first = ConversationContinuityAdapter(ledger).resolve(
        _frame(
            kind="assistant_proposition",
            speaker="assistant",
            operation="confirm",
            turn_index=root.index,
        ),
        current_user_turn_index=user.index,
    )

    act = AssistantAct(
        kind=AssistantActKind.CONFIRMATION,
        proposition_mode=PropositionMode.CONTINUE,
        confidence=0.98,
    )
    next_index = 3
    proposition_root, active_root = assistant_proposition_link(
        assistant_act=act,
        assistant_turn_index=next_index,
        referenced_root_turn_index=first.proposition_root_turn.index,
        previous_active_root_turn_index=root.index,
    )
    assistant = ledger.record_assistant(
        "Tôi nói nghiêm túc.",
        meta={
            "assistant_act": act.to_dict(),
            "discourse": {
                "proposition_root_turn_index": proposition_root,
                "active_proposition_root_turn_index": active_root,
            },
        },
    )

    current = ledger.record_user("một câu khác cũng không cần nằm trong marker list")
    second = ConversationContinuityAdapter(ledger).resolve(
        _frame(
            kind="assistant_proposition",
            speaker="assistant",
            operation="clarify",
            turn_index=assistant.index,
        ),
        current_user_turn_index=current.index,
    )
    surface = DiscourseSurfaceRenderer().render(second.action)

    assert second.target_turn is not None
    assert second.target_turn.index == assistant.index
    assert second.proposition_root_turn is not None
    assert second.proposition_root_turn.index == root.index
    assert "Tôi thấy bạn" in surface.text
    assert "Tôi nói nghiêm túc" not in surface.text


def test_long_term_memory_requires_structured_query_not_raw_text() -> None:
    ledger = SessionTurnLedger()
    current = ledger.record_user("nội dung bề mặt không được dùng để chọn fact")
    adapter = ConversationContinuityAdapter(ledger)

    memory_resolution = adapter.resolve(
        _frame(
            kind="long_term_memory",
            speaker="none",
            operation="recall",
            memory_query={
                "intent": "recall_fact",
                "subject": "user",
                "predicate_hint": "user.name",
                "object_hint": "",
                "temporal_scope": "current",
                "answer_mode": "exact",
                "confidence": 0.96,
            },
        ),
        current_user_turn_index=current.index,
    )
    incomplete_resolution = adapter.resolve(
        _frame(
            kind="long_term_memory",
            speaker="none",
            operation="recall",
        ),
        current_user_turn_index=current.index,
    )

    assert memory_resolution.memory_recall_requested is True
    assert memory_resolution.memory_query.predicate_hint == "user.name"
    assert incomplete_resolution.memory_recall_requested is False


def test_discourse_prompt_maps_history_without_duplicating_raw_text() -> None:
    ledger = SessionTurnLedger()
    first = ledger.record_assistant(
        "Một phát biểu có nội dung.",
        meta={
            "assistant_act": {
                "kind": "assertion",
                "proposition_mode": "new",
                "confidence": 0.9,
            },
            "discourse": {
                "proposition_root_turn_index": 1,
                "active_proposition_root_turn_index": 1,
            },
        },
    )
    user = ledger.record_user("một lượt nối")
    ledger.record_assistant(
        "Một câu trả lời nối tiếp.",
        meta={
            "assistant_act": {
                "kind": "confirmation",
                "proposition_mode": "continue",
                "confidence": 0.9,
            },
            "discourse": {
                "reply_to_user_turn_index": user.index,
                "proposition_root_turn_index": first.index,
                "active_proposition_root_turn_index": first.index,
            },
        },
    )
    current = ledger.record_user("lượt hiện tại")

    context = ConversationContinuityAdapter(ledger).prompt_context(
        current_turn_index=current.index,
    )

    assert f'"active_assistant_proposition_turn_index":{first.index}' in context
    assert f'"current_user_turn_index":{current.index}' in context
    assert '"history_turn_map"' in context
    assert "Một câu trả lời nối tiếp" not in context


def test_semantic_reference_rejects_ambiguous_locator_and_speaker_conflict() -> None:
    result = validate_semantic_frame(
        {
            "reference": {
                "kind": "assistant_proposition",
                "speaker": "user",
                "operation": "clarify",
                "turn_index": "12",
                "turn_offset": 99,
                "confidence": "0.88",
            }
        }
    )

    assert result.frame.reference.to_dict() == {
        "kind": "none",
        "speaker": "none",
        "operation": "none",
        "turn_index": 0,
        "turn_offset": 0,
        "confidence": 0.0,
    }
    assert "reference.turn_offset_out_of_range:99" in result.violations
    assert "reference.locator_ambiguous:turn_index_and_turn_offset" in result.violations
    assert "reference.speaker_incompatible:assistant_proposition:user" in result.violations


def test_no_phrase_marker_classifier_remains_in_runtime_path() -> None:
    root = Path(__file__).resolve().parents[1]
    intent_source = (root / "core" / "conversation_intent.py").read_text(encoding="utf-8")
    continuity_source = (root / "runtime" / "conversation_continuity.py").read_text(encoding="utf-8")
    discourse_source = (root / "runtime" / "discourse_state.py").read_text(encoding="utf-8")
    orchestrator_source = (root / "runtime" / "ai_orchestrated" / "orchestrator.py").read_text(encoding="utf-8")

    assert "is_recent_user_turn_recall" not in intent_source
    assert "is_short_repair_request" not in continuity_source
    assert "_build_conversation_continuity_decision" not in orchestrator_source
    assert "_conversation_repair_fallback_text" not in orchestrator_source
    assert "from domain import Decision" not in discourse_source
    assert "_resolve_discourse_frame" in orchestrator_source


def test_settings_cooldown_still_uses_int64() -> None:
    body_source = Path(__file__).resolve().parents[2] / "body" / "UdpListener.SettingsPanel.cs"
    if not body_source.exists():
        body_source = Path(__file__).resolve().parents[1] / "integration" / "body_contracts" / "UdpListener.SettingsPanel.cs.fixture.txt"
    source = body_source.read_text(encoding="utf-8")

    assert "public long CooldownMs" in source
    assert 'ReadJsonInt64(pool, "global_cooldown_ms", 0L)' in source
    assert 'ReadJsonInt64(key, "cooldown_ms", 0L)' in source
