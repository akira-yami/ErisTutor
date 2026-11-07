# -*- coding: utf-8 -*-
"""Regression cho hai lỗi integration làm V44 ngã ở runtime thật."""
from __future__ import annotations

import json

from runtime.json_boundary import public_payload, to_json_compatible
from runtime.semantic_frame import AssistantAct, SemanticFrame
from runtime.session_turn_ledger import SessionTurnLedger


def test_session_turn_ledger_can_merge_typed_discourse_meta() -> None:
    """Full source phải mang đúng API mà orchestrator V44 gọi."""

    ledger = SessionTurnLedger()
    turn = ledger.record_user("thật hem")
    updated = ledger.merge_turn_meta(
        turn.index,
        {"semantic_frame": SemanticFrame().to_dict()},
    )

    assert updated is not None
    assert updated.meta["semantic_frame"]["version"] == "semantic_frame.v1"
    assert ledger.get_turn(turn.index) == updated


def test_public_payload_strips_private_typed_objects_and_is_json_safe() -> None:
    frame = SemanticFrame(user_act="confirm")
    assistant_act = AssistantAct()
    payload = {
        "text": "Có.",
        "semantic_frame": frame,
        "assistant_act": assistant_act,
        "_typed_semantic_frame": frame,
        "_typed_assistant_act": assistant_act,
    }

    public = public_payload(payload)
    encoded = json.dumps(public, ensure_ascii=False)

    assert '"text": "Có."' in encoded
    assert public["semantic_frame"]["user_act"] == "confirm"
    assert "_typed_semantic_frame" not in public
    assert "_typed_assistant_act" not in public


def test_json_boundary_handles_nested_domain_objects() -> None:
    converted = to_json_compatible(
        {"items": [SemanticFrame(), AssistantAct()]},
        drop_private_typed=True,
    )
    json.dumps(converted, ensure_ascii=False)
    assert converted["items"][0]["version"] == "semantic_frame.v1"
