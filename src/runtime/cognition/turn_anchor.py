# -*- coding: utf-8 -*-
"""Observer anchor derived only from ``semantic_frame.v1``.

PDF annotation, pages 12-13 and 20
----------------------------------
P2 cleanup removed the lexical discourse fallback.  Observer policy may project
an already validated frame into narrow operational hints, but it may not read
raw user text and invent a second meaning authority.
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence


_RELATION_HINTS = {
    "continue_assistant_move": "continuation_bid",
    "challenge_assistant_relevance": "challenge_relevance",
    "clarify_assistant_reference": "reference_clarification",
    "clarify_assistant_phrase": "reference_clarification",
    "clarify_assistant_statement": "reference_clarification",
    "challenge_assistant_assumption": "correction",
    # Phản bác một ấn tượng chủ quan không đồng nghĩa ERIS đã bị chứng minh sai.
    # Tách lane này khỏi correction để observer không tự động ghi repair/fault.
    "contest_assistant_impression": "stance_contest",
    "disagree_with_assistant_impression": "stance_contest",
    "style_feedback": "style_criticism",
    "close_topic": "close_topic",
    "relational_bid": "closeness_bid",
}

_FRAME_ACT_HINTS = {
    "style_criticism": "style_criticism",
    "style_request": "style_request",
    "correction_or_challenge": "correction",
    "disagreement": "stance_contest",
    "self_report_contrast": "stance_contest",
    "stance_contest": "stance_contest",
    "clarification_request": "reference_clarification",
    "continuation_bid": "continuation_bid",
    "relational_check_in": "closeness_bid",
    "closeness_bid": "closeness_bid",
    "request_reciprocity": "request_reciprocity",
    "play_bid": "play_bid",
    "action_request": "technical_request",
}


def anchor_from_semantic_frame(frame: Mapping[str, Any] | None) -> str:
    """Project typed frame fields into an observer hint without reclassification."""

    payload = dict(frame or {})
    if str(payload.get("version", "") or "") != "semantic_frame.v1":
        return "unknown"
    relation = str(payload.get("discourse_relation", "") or "").strip().lower()
    act = str(payload.get("user_act", "") or "").strip().lower()
    if relation in _RELATION_HINTS:
        return _RELATION_HINTS[relation]
    if relation.startswith("continuation_"):
        return "continuation_bid"
    if relation.startswith("clarify_"):
        return "reference_clarification"
    return _FRAME_ACT_HINTS.get(act, "unknown")


def previous_assistant_from_history(
    recent_history: Sequence[Mapping[str, Any] | str],
    *,
    current_assistant_text: str = "",
) -> str:
    current = " ".join(str(current_assistant_text or "").split()).strip()
    for item in reversed(tuple(recent_history or ())):
        if not isinstance(item, Mapping):
            continue
        if str(item.get("role", "") or "").strip().lower() != "assistant":
            continue
        text = " ".join(str(item.get("content", "") or "").split()).strip()
        if text and text != current:
            return text
    return ""


__all__ = ["anchor_from_semantic_frame", "previous_assistant_from_history"]
