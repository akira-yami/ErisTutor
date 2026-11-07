# -*- coding: utf-8 -*-
"""P3 — Truy hồi đúng ngữ cảnh, mạch hội thoại và Root tự lựa chọn.

Các test này kiểm hợp đồng kỹ thuật (evidence/delivery/authority), không
thay thế hội thoại A/B với model thật để đánh giá EQ.
"""
from __future__ import annotations

from runtime.ai_orchestrated.contracts import RuntimeMindState
from runtime.ai_orchestrated.identity import IdentityProfile, StrategyRecord
from runtime.conversation.agent import RuntimeModelRootAgent, root_step_schema
from runtime.conversation.context import RootCapabilityRegistry
from runtime.conversation.social_situation import project_social_situation


def _state(*records):
    return RuntimeMindState(identity=IdentityProfile(strategies={
        "root_move": StrategyRecord(experiences=tuple(records)),
    }))


def _case(ref, session, situation, goal, reply, settled):
    return {
        "assistant_ref": "delivery:" + ref, "reaction_ref": "user:" + ref,
        "session_id": session, "settled_at": settled,
        "situation": situation, "goal": goal,
        "assistant_text": "Eris đã nói một câu.", "reaction_text": reply,
        "outcome": "rejected", "confidence": 0.85,
        "interpretation_source": "post_turn_observer",
    }


def _project(text="Kiro sửa một lỗi thành năm lỗi", **kw):
    options = dict(event_kind="user_turn", user_text=text, session_id="here",
                   recent_history=(), handoff={})
    options.update(kw)
    return project_social_situation(**options)


def test_p3_prefers_contextual_case_over_newer_unrelated_case():
    history = _state(
        _case("a", "here", "Kiro sửa lỗi liên tục", "kể chuyện Kiro", "Tôi đang kể Kiro mà", 50),
        _case("b", "here", "bài kiểm tra subnetting", "hướng dẫn mạng", "Giải giúp bài mạng", 100),
    )
    records = _project(state=history)["past_interactions"]
    assert records[0]["reaction_ref"] == "user:a"
    assert records[0]["match_basis"] == "shared_context_terms"
    assert "kiro" in records[0]["shared_terms"]
    assert len(records) == 1  # không nhét tình huống subnetting lạc đề


def test_p3_cross_session_requires_specific_shared_context_not_recent_only():
    history = _state(
        _case("old", "other", "Kiro sửa lỗi tạo lỗi mới", "nói về bug Kiro", "Kiro tạo bug nữa", 50),
        _case("new", "other", "đánh pokemon trong mưa", "pvp pokemon", "Pokemon thua", 100),
    )
    results = _project(state=history)["past_interactions"]
    assert len(results) == 1
    assert results[0]["reaction_ref"] == "user:old"
    assert results[0]["scope"] == "past_session"
    assert "session_id" not in results[0]
    assert "new" not in str(results)


def test_p3_only_unrelated_cross_session_is_not_injected():
    history = _state(_case("old", "other", "pokemon rain team", "pvp", "pokemon mất máu", 10))
    assert "past_interactions" not in _project(state=history)


def test_p3_history_retains_order_without_claiming_queued_audio_was_said():
    rows = (
        {"role": "user", "content": "Kiro code sai", "session_id": "here"},
        {"role": "assistant", "content": "Sao nó làm vậy?", "delivery_status": "delivered"},
        {"role": "user", "content": "Fix lỗi sinh thêm năm lỗi"},
        {"role": "assistant", "content": "Đưa log đi", "delivery_status": "queued_for_audio"},
        {"role": "assistant", "content": "Chưa rõ", "delivery_status": "delivery_failed"},
        {"role": "user", "content": "Lượt khác", "session_id": "other"},
        {"role": "user", "content": "Kiro sửa một lỗi thành năm lỗi"},
    )
    result = _project(recent_history=rows)
    turns = result["conversation_flow"]["recent_turns"]
    assert [turn["role"] for turn in turns] == ["user", "assistant", "user"]
    assert turns[1]["excerpt"] == "Sao nó làm vậy?"
    assert "Đưa log đi" not in str(turns)
    assert "Lượt khác" not in str(turns)
    assert "Kiro sửa một lỗi thành năm lỗi" not in str(turns)


def test_p3_user_feedback_is_evidence_not_autonomous_apology():
    result = _project(text="Sao bạn cứ hỏi log? Tôi đang kể mà", handoff={
        "last_user_text": "Sao bạn cứ hỏi log? Tôi đang kể mà",
        "repair_needed": True, "user_stance": "approach_feedback",
        "last_assistant_turn_ref": "assistant:1",
    })
    assert result["social_repair_evidence"]["previous_assistant_ref"] == "assistant:1"
    assert "not a requirement to apologize" in result["social_repair_evidence"]["interpretation"]
    assert not any(key in result for key in ("next_move", "reply_template", "emotion"))
    # Khi lỗi feedback đã thuộc lượt trước, không gán sang lượt hiện tại.
    fresh = _project(text="Giờ sửa code giúp tôi", handoff={
        "last_user_text": "Sao bạn cứ hỏi log? Tôi đang kể mà", "repair_needed": True})
    assert "social_repair_evidence" not in fresh


def test_p3_root_is_still_only_decider_and_schema_is_unchanged(monkeypatch):
    monkeypatch.setenv("ERIS_ROOT_INTERACTION_CHOICES", "0")
    prompt = RuntimeModelRootAgent._system_prompt(RootCapabilityRegistry({}))
    assert "ELEGANT SOCIAL INTELLIGENCE (P3)" in prompt
    assert "shared_context_terms" in prompt
    assert "chỉ một Root chọn move/no_reply" in prompt
    assert "interaction_choice" not in root_step_schema()["oneOf"][0]["properties"]
    assert len(root_step_schema()["oneOf"][0]["required"]) == 5


def test_p3_explicit_closure_prevents_unrelated_old_same_session_recall():
    history = _state(_case("old", "here", "chuyện ở phòng lab", "giải thích lỗi máy", "chuyện khác", 10))
    closed = _project(text="Thôi chuyện này, giờ chuyển sang việc khác", state=history, handoff={
        "last_user_text": "Thôi chuyện này, giờ chuyển sang việc khác",
        "topic_closed": True, "user_stance": "topic_closed",
    })
    assert closed["current_handoff"]["explicit_topic_closure"] is True
    assert "past_interactions" not in closed
