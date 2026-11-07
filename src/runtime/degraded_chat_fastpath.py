# -*- coding: utf-8 -*-
"""Tiny offline/degraded replies for exact, semantically closed turns.

PDF annotation, pages 15-16 and 20
----------------------------------
Fast-path is an emergency escape hatch, not a Vietnamese-understanding engine.
This module therefore accepts only a deliberately small set of exact normalized
forms and is consulted only after the remote semantic authority has failed. It
must never intercept normal healthy chat or grow into another phrase taxonomy.
"""

from __future__ import annotations

import os
from typing import Any

from runtime.cognition.normalization import normalize_vi
from runtime.semantic_frame import validate_semantic_frame


_GREETING_FORMS = frozenset({
    "chao", "xin chao", "hello", "hi", "hey", "chao eris", "xin chao eris",
})
_ACK_FORMS = frozenset({
    "ok", "okay", "duoc", "duoc roi", "hieu roi", "cam on", "cam on em",
})
_SMALL_TALK_REQUEST_MARKERS = (
    "kiem chuyen",
    "noi chuyen gi do",
    "noi gi do",
    "ke gi do",
    "bat chuyen",
    "goi chuyen",
)
_SCREEN_CAPABILITY_MARKERS = (
    "xem duoc tui lam gi",
    "xem duoc toi lam gi",
    "thay duoc tui lam gi",
    "thay duoc toi lam gi",
    "biet tui lam gi",
    "biet toi lam gi",
)


def _paired_surface(language: str, variants: dict[str, str]) -> tuple[str, str, str]:
    """Tạo cặp display/speech tĩnh cho degraded path.

    ``language`` ở fast-path là ngôn ngữ UI, không phải quyền đổi voice của Eris.
    Vì voice contract production được pin sang tiếng Nhật, mọi câu fallback đã
    biết trước ở module này đều mang luôn một ``speech_text`` Nhật tương ứng.

    Làm vậy có hai lợi ích:
    1. degraded greeting không phải gọi thêm LLM chỉ để dịch một câu cố định;
    2. không còn tình trạng display tiếng Việt bị copy sang speech rồi gắn nhãn
       ``ja`` ở compatibility envelope.
    """

    display_language = language if language in {"vi", "en", "ja"} else "vi"
    display_text = variants.get(display_language, variants["vi"])
    # Mọi bảng variants trong file này bắt buộc có bản Nhật vì đó là transcript
    # TTS canonical cho Eris khi voice contract đang pin ja.
    speech_text = variants["ja"]
    return display_text, display_language, speech_text


def _enabled() -> bool:
    raw = str(os.getenv("ERIS_DEGRADED_CHAT_FASTPATH", "1") or "1").strip().lower()
    return raw not in {"0", "false", "off", "no"}


def build_degraded_reply(user_text: str, *, language_hint: str = "vi") -> dict[str, Any]:
    """Return a local reply for exact greeting/acknowledgement, otherwise ``{}``."""

    if not _enabled():
        return {}
    cue = normalize_vi(str(user_text or "")).strip(" ?.!,")
    language = str(language_hint or "vi").strip().lower()

    if cue in _GREETING_FORMS:
        text, display_language, speech_text = _paired_surface(language, {
            "en": "Hi. I got your message.",
            "ja": "こんにちは。メッセージは受け取ったよ。",
            "vi": "Chào. Tôi nhận được tin nhắn rồi.",
        })
        frame = {
            "turn_kind": "chat_response",
            "discourse_relation": "new_or_direct_turn",
            "user_act": "greeting",
            "target": "assistant",
            "literal_question": False,
            "ambiguity_owner": "none",
            "boundary_action": "none",
            "continuation_key": "none",
            "evidence_scope": "exact_local_syntax",
            "tool_intent": "none",
            "missing_evidence": [],
            "reply_obligation": {
                "must_answer": True,
                "must_not_broad_clarify": True,
                "must_not_call_tool": True,
            },
        }
    elif cue in _ACK_FORMS:
        text, display_language, speech_text = _paired_surface(language, {
            "en": "Got it.",
            "ja": "わかった。",
            "vi": "Ừ, tôi hiểu rồi.",
        })
        frame = {
            "turn_kind": "chat_response",
            "discourse_relation": "acknowledgement",
            "user_act": "acknowledgement",
            "target": "assistant",
            "literal_question": False,
            "ambiguity_owner": "none",
            "boundary_action": "none",
            "continuation_key": "recent_assistant_thread",
            "evidence_scope": "exact_local_syntax",
            "tool_intent": "none",
            "missing_evidence": [],
            "reply_obligation": {
                "must_answer": True,
                "must_not_broad_clarify": True,
                "must_not_call_tool": True,
            },
        }
    elif any(marker in cue for marker in _SMALL_TALK_REQUEST_MARKERS):
        text, display_language, speech_text = _paired_surface(language, {
            "en": "Then I'll start small: what's the tiny thing on your mind right now?",
            "ja": "じゃあ小さく始めよう。今いちばん気になってることは何？",
            "vi": "Vậy tôi khởi đầu nhẹ nha: trong đầu cậu lúc này đang vướng điều gì nhất?",
        })
        frame = {
            "turn_kind": "chat_response",
            "discourse_relation": "new_or_direct_turn",
            "user_act": "small_talk_request",
            "target": "assistant",
            "literal_question": False,
            "ambiguity_owner": "none",
            "boundary_action": "none",
            "continuation_key": "recent_assistant_thread",
            "evidence_scope": "user_text",
            "tool_intent": "none",
            "missing_evidence": [],
            "reply_obligation": {
                "must_answer": True,
                "must_not_broad_clarify": False,
                "must_not_call_tool": True,
            },
        }
    elif any(marker in cue for marker in _SCREEN_CAPABILITY_MARKERS):
        text, display_language, speech_text = _paired_surface(language, {
            "en": "I can use screen evidence when capture/OCR gives me something grounded. Right now I shouldn't pretend I saw details that did not resolve.",
            "ja": "画面は、キャプチャやOCRが根拠のある情報を出した時だけ扱える。今は見えたふりはしない。",
            "vi": "Có, nhưng chỉ khi capture/OCR cho ra bằng chứng đủ chắc. Lúc nãy tôi không nên giả vờ thấy chi tiết khi phần nhìn chưa resolve được.",
        })
        frame = {
            "turn_kind": "chat_response",
            "discourse_relation": "answer",
            "user_act": "capability_question",
            "target": "assistant",
            "literal_question": True,
            "ambiguity_owner": "none",
            "boundary_action": "none",
            "continuation_key": "screen_capability",
            "evidence_scope": "runtime_capability",
            "tool_intent": "none",
            "missing_evidence": ["grounded_screen_evidence"],
            "reply_obligation": {
                "must_answer": True,
                "must_not_broad_clarify": True,
                "must_not_call_tool": True,
            },
        }
    else:
        return {}

    validated = validate_semantic_frame(frame).frame
    assistant_act = {
        "kind": (
            "social"
            if cue in _GREETING_FORMS or any(marker in cue for marker in _SMALL_TALK_REQUEST_MARKERS)
            else ("answer" if any(marker in cue for marker in _SCREEN_CAPABILITY_MARKERS) else "acknowledgement")
        ),
        "proposition_mode": "none",
        "confidence": 1.0,
    }
    return {
        "semantic_frame": validated.to_dict(),
        "assistant_act": assistant_act,
        "text": text,
        # Display và speech là hai surface độc lập. Đừng xóa ba field này khi
        # refactor fallback: compatibility envelope cần chúng để biết câu Nhật
        # đã thật sự tồn tại, thay vì tự gắn nhãn ja lên display tiếng Việt.
        "display_language": display_language,
        "speech_text": speech_text,
        "speech_language": "ja",
        "should_speak": True,
        "performance_hint": "neutral",
        "degraded_fastpath": True,
    }


__all__ = ["build_degraded_reply"]
