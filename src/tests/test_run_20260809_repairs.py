# -*- coding: utf-8 -*-
"""Regression tests cho các lỗi quan sát được ở run 2026-08-09.

Các test này cố ý bám vào invariant runtime, không bám câu văn cụ thể của model.
"""
from __future__ import annotations

from pathlib import Path


def test_missing_vision_client_salvages_real_ocr_even_in_no_fallback(monkeypatch):
    from cortex.vision_semantic_compressor import VisionCompressorConfig, VisionSemanticCompressor

    # Runtime settings hiện có thể bật no-fallback; monkeypatch trực tiếp policy
    # tại module để test không phụ thuộc file settings ngoài repo.
    monkeypatch.setattr("cortex.vision_semantic_compressor.no_fallback_mode_enabled", lambda: True)
    compressor = VisionSemanticCompressor(
        VisionCompressorConfig(enabled=True, model="remote_vision"),
        local_client=None,
        caption_client=None,
        structure_client=None,
    )
    note = compressor.compress(
        image_base64="ZmFrZQ==",
        ocr_text="Visual Studio Code\nERROR: test failure",
    )

    assert note.source == "ocr_only"
    assert any("ocr_text:" in item for item in note.evidence)
    assert note.important_text


def test_current_world_claim_requires_evidence_and_user_correction_is_repair():
    from runtime.grounding_surface_gate import (
        current_world_claim_without_evidence,
        is_grounding_correction,
    )

    previous = "Trời đang trong xanh, thỉnh thoảng nhìn ra ngoài cũng không tệ."
    assert current_world_claim_without_evidence(previous, ()) is True
    assert current_world_claim_without_evidence(previous, ("weather:verified",)) is False
    assert is_grounding_correction("Sắp tối đến nơi mà xanh cái gì má", previous) is True
    assert is_grounding_correction("Tôi chỉ không thích màu xanh", previous) is False


def test_autonomous_surface_rejects_environment_decoration_from_conversation_refs_only():
    from runtime.grounding_surface_gate import current_world_claim_without_evidence

    assert current_world_claim_without_evidence(
        "Trong căn phòng yên tĩnh thế này, tôi vẫn nghĩ về autonomy.",
        ("turn:test",),
    ) is True
    assert current_world_claim_without_evidence(
        "Chắc bạn đang mệt rồi.",
        ("turn:test",),
    ) is True
    assert current_world_claim_without_evidence(
        "Đã gần tối theo giờ của bạn rồi.",
        (),
    ) is False
    assert current_world_claim_without_evidence(
        "Chiều nay thật yên bình.",
        ("screen:current",),
    ) is True
    assert current_world_claim_without_evidence(
        "Tôi thấy cửa sổ Brave.",
        ("screen:current",),
    ) is False
    assert current_world_claim_without_evidence(
        "Trời đang mưa.",
        ("weather:verified",),
    ) is False


def test_autonomous_cadence_paraphrase_is_duplicate():
    from runtime.ai_orchestrated.surface_intent import _surface_text_is_duplicate

    first = "Trời đang trong xanh, thỉnh thoảng nhìn ra ngoài cũng không tệ."
    second = "Hôm nay yên tĩnh, thỉnh thoảng có những khoảnh khắc như thế này cũng không tệ nhỉ."
    assert _surface_text_is_duplicate(second, first) is True


def test_interactive_capability_planner_bypasses_when_subject_does_not_route_tool():
    from runtime.ai_orchestrated.executive_cortex import ExecutiveCortex

    calls = []

    def invoke(*_args, **_kwargs):
        calls.append(1)
        raise AssertionError("Capability planner không được gọi ở conversational turn")

    cortex = ExecutiveCortex(object(), object(), invoke_structured=invoke)
    directive = cortex.plan_interactive(
        "nhìn ra ngoài sao cơ",
        snapshot={
            "subject_decision": {
                "proposed_action": "speak",
                "arguments": {
                    "capability_route": False,
                    "semantic_target": "Giải thích câu vừa nói.",
                },
            }
        },
    )

    assert directive.status == "none"
    assert directive.reason == "subject_commit_requires_no_capability"
    assert calls == []


def test_vague_meaning_question_anchors_to_last_assistant_answer():
    from domain import Decision
    from parser_grounding.service import DecisionParser

    parser = DecisionParser(Decision(text="", animation="stand"))

    message = parser.build_chat_user_message(
        user_message="ý em là sao",
        last_chat_answer="Gắn nhãn không phải để hiểu, mà để tạm đặt xuống.",
    )

    assert "Câu trả lời trước của bạn" in message
    assert "Gắn nhãn không phải để hiểu" in message


def test_previous_public_turn_context_is_always_merged_for_chat_prompt():
    from runtime.ai_orchestrated.orchestrator import _merge_previous_public_context

    context = _merge_previous_public_context(
        "Current grounding: user_text",
        "previous_public_turn within reply window:\n- text=Gắn nhãn không phải để hiểu.",
    )

    assert "Current grounding: user_text" in context
    assert "previous_public_turn within reply window" in context
    assert "Gắn nhãn không phải để hiểu" in context


def test_public_addressing_keeps_assistant_self_pronoun_stable(tmp_path: Path):
    from runtime.addressing_policy import clean_public_addressing
    from runtime.user_addressing_state import UserAddressingStore, set_global_user_addressing_store

    store = UserAddressingStore(tmp_path / "addressing.json")
    set_global_user_addressing_store(store)
    try:
        assert clean_public_addressing("Em ở đây.") == "Tôi ở đây."
        assert clean_public_addressing("Đó là ý của em.") == "Đó là ý của tôi."
    finally:
        set_global_user_addressing_store(None)


def test_speech_act_rejects_natural_language_prose():
    from runtime.ai_orchestrated.semantic_contracts import TurnSemantics, speech_act_key

    prose = "Tôi đang cố gắng hiểu chiều sâu của sự khó chịu này"
    assert speech_act_key(prose) == "unspecified"
    assert TurnSemantics.from_mapping({"speech_act": prose}).speech_act == "unspecified"
    assert speech_act_key("independent_topic_shift") == "independent_topic_shift"
