# -*- coding: utf-8 -*-
"""Regression tests for the completed semantic-authority cutover."""

from __future__ import annotations

import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_legacy_semantic_modules_are_removed() -> None:
    removed = (
        "runtime/semantic_shadow.py",
        "runtime/social_act.py",
        "runtime/social_intent_head.py",
        "runtime/cognition/engine.py",
        "runtime/cognition/specialists/discourse.py",
        "runtime/cognition/specialists/semantic.py",
    )
    assert all(not (ROOT / relative).exists() for relative in removed)


def test_runtime_cognition_exports_only_typed_projection_helpers() -> None:
    import runtime.cognition as cognition

    assert set(cognition.__all__) == {
        "anchor_from_semantic_frame",
        "previous_assistant_from_history",
    }
    assert not hasattr(cognition, "PreResponseCognition")
    assert not hasattr(cognition, "CognitiveAcceptanceGate")


def test_semantic_authority_has_no_legacy_mode() -> None:
    from semantic_authority import SemanticAuthorityPolicy

    policy = SemanticAuthorityPolicy.from_runtime()
    assert policy.mode == "primary_model"
    assert policy.primary_model_first is True
    assert policy.allow_pre_llm_intercepts is False
    assert policy.enforce_response_contract is False


def test_typed_anchor_ignores_raw_text_and_uses_frame() -> None:
    from runtime.cognition.turn_anchor import anchor_from_semantic_frame

    frame = {
        "version": "semantic_frame.v1",
        "discourse_relation": "continuation_of_assistant_self_disclosure",
        "user_act": "relational_check_in",
        "target": "assistant",
    }
    assert anchor_from_semantic_frame(frame) == "continuation_bid"
    assert anchor_from_semantic_frame({}) == "unknown"


def test_deleted_modules_are_not_importable() -> None:
    assert importlib.util.find_spec("runtime.semantic_shadow") is None
    assert importlib.util.find_spec("runtime.social_act") is None
