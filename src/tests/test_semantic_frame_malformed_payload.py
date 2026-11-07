from __future__ import annotations

from runtime.semantic_frame import validate_semantic_frame


def test_boolean_semantic_frame_is_repaired_instead_of_crashing() -> None:
    result = validate_semantic_frame(True)

    assert result.frame["version"] == "semantic_frame.v1"
    assert result.frame["reply_obligation"]["must_answer"] is True
    assert "semantic_frame_must_be_object:bool" in result.violations


def test_malformed_nested_semantic_frame_fields_are_safe() -> None:
    result = validate_semantic_frame(
        {
            "missing_evidence": True,
            "reply_obligation": False,
            "user_act": "relational_feedback",
            "target": "assistant",
        }
    )

    assert result.frame["missing_evidence"] == []
    assert result.frame["reply_obligation"] == {
        "must_answer": True,
        "must_not_broad_clarify": False,
        "must_not_call_tool": False,
    }
    assert "missing_evidence_must_be_array:bool" in result.violations
    assert "reply_obligation_must_be_object:bool" in result.violations


def test_model_router_preserves_text_when_semantic_frame_is_boolean() -> None:
    from runtime.ai_orchestrated.model_router import AIRuntimeModelRouter

    router = AIRuntimeModelRouter(llm_service=None, logger=None)
    payload = router._compat_public_envelope(
        {
            "text": "Ừ, vừa rồi tôi bám sai mạch.",
            "should_speak": True,
            "semantic_frame": True,
        }
    )

    assert payload["text"] == "Ừ, vừa rồi tôi bám sai mạch."
    assert payload["should_speak"] is True
    assert payload["semantic_frame"]["version"] == "semantic_frame.v1"
