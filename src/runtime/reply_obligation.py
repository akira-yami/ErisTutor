# -*- coding: utf-8 -*-
"""Public reply obligation enforcement for direct user turns.

A model may choose silence for an autonomous lane. It may not silently convert a
direct user message into an ambient meditation. This module is deliberately
small: semantic authority decides the obligation; runtime only enforces it.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping

from runtime.degraded_chat_fastpath import build_degraded_reply
from runtime.semantic_frame import SemanticFrame, validate_semantic_frame
from runtime.utterance_spec import degrade_surface_payload


class ReplyRequirement(str, Enum):
    REQUIRED = "required"
    OPTIONAL = "optional"
    FORBIDDEN = "forbidden"


@dataclass(frozen=True, slots=True)
class ReplyObligationResult:
    payload: dict[str, Any]
    requirement: ReplyRequirement
    repaired: bool = False
    reason: str = ""


def _observe_public_writer_fallback(kind: str) -> None:
    """Ghi fallback mà không để observability làm hỏng reply path."""

    try:
        from runtime.observability import GLOBAL_OBSERVABILITY
        GLOBAL_OBSERVABILITY.increment(
            "public_writer_fallback",
            tags={"kind": str(kind or "unknown")},
        )
    except Exception:
        pass


def requirement_from_frame(frame: SemanticFrame | Mapping[str, Any] | None, *, direct_user_turn: bool = True) -> ReplyRequirement:
    if isinstance(frame, SemanticFrame):
        typed = frame
    elif isinstance(frame, Mapping):
        typed = validate_semantic_frame(frame).frame
    else:
        typed = None

    boundary = str(getattr(typed, "boundary_action", "") or "").strip().lower() if typed else ""
    if boundary in {"silence", "do_not_reply", "forbidden"}:
        return ReplyRequirement.FORBIDDEN
    if typed is not None and bool(typed.reply_obligation.must_answer):
        return ReplyRequirement.REQUIRED
    return ReplyRequirement.REQUIRED if direct_user_turn else ReplyRequirement.OPTIONAL


def _minimal_surface(frame: SemanticFrame | None, *, language_hint: str) -> tuple[str, str, str]:
    """Return ``(display_text, speech_text_ja, assistant_act_kind)``.

    Đây là fallback cuối khi public writer không còn câu dùng được. Các câu ở
    đây đều là literal tĩnh nên ta có thể giữ sẵn bản Nhật tương ứng, thay vì
    copy display tiếng Việt sang speech rồi để tầng sau phải đoán lại ngôn ngữ.
    """

    language = str(language_hint or "vi").strip().lower()
    act = str(getattr(frame, "user_act", "") or "").strip().lower() if frame else ""
    if act == "greeting":
        display = {
            "en": "Hi. I got your message.",
            "ja": "こんにちは。メッセージは受け取ったよ。",
            "vi": "Chào. Tôi nhận được tin nhắn rồi.",
        }.get(language, "Chào. Tôi nhận được tin nhắn rồi.")
        return display, "こんにちは。メッセージは受け取ったよ。", "social"
    if act == "acknowledgement":
        display = {
            "en": "Got it.",
            "ja": "わかった。",
            "vi": "Ừ, tôi hiểu rồi.",
        }.get(language, "Ừ, tôi hiểu rồi.")
        return display, "わかった。", "acknowledgement"
    if act == "repair_request":
        display = {
            "en": "You're right, I failed to answer directly. Please send that exact line once more.",
            "ja": "その通り。直接答えられていなかった。同じ文をもう一度送って。",
            "vi": "Đúng, lượt trước tôi đã không trả lời trực tiếp. Bạn gửi lại đúng câu đó một lần nhé.",
        }.get(language, "Đúng, lượt trước tôi đã không trả lời trực tiếp. Bạn gửi lại đúng câu đó một lần nhé.")
        return display, "その通り。直接答えられていなかった。同じ文をもう一度送って。", "repair"
    # Chỉ dùng khi provider thật sự không trả được bất kỳ public text nào. Không
    # dùng văn mẫu “hụt nhịp/bám mạch” vì nó vừa vô nghĩa vừa che mất lỗi thật.
    display = {
        "en": "I received that message, but the reply generator returned no usable text.",
        "ja": "そのメッセージは受け取ったが、返答生成から使える文が返らなかった。",
        "vi": "Tôi đã nhận câu này, nhưng bộ sinh lời không trả về câu dùng được.",
    }.get(language, "Tôi đã nhận câu này, nhưng bộ sinh lời không trả về câu dùng được.")
    return display, "そのメッセージは受け取ったが、返答生成から使える文が返らなかった。", "repair"


def enforce_reply_obligation(
    payload: Mapping[str, Any] | None,
    *,
    user_text: str,
    frame: SemanticFrame | Mapping[str, Any] | None = None,
    language_hint: str = "vi",
    direct_user_turn: bool = True,
) -> ReplyObligationResult:
    data = dict(payload or {})
    typed = frame if isinstance(frame, SemanticFrame) else (
        validate_semantic_frame(frame).frame if isinstance(frame, Mapping) else None
    )
    requirement = requirement_from_frame(typed, direct_user_turn=direct_user_turn)

    if requirement is ReplyRequirement.FORBIDDEN:
        data = degrade_surface_payload(
            data,
            text="",
            language=language_hint,
            reason="reply_forbidden",
            should_speak=False,
        )
        return ReplyObligationResult(data, requirement, repaired=True, reason="reply_forbidden")

    text = str(data.get("text", "") or "").strip()
    should_speak = bool(data.get("should_speak", True))
    if requirement is not ReplyRequirement.REQUIRED or (text and should_speak):
        return ReplyObligationResult(data, requirement)

    if text and not should_speak:
        if typed is not None:
            data["semantic_frame"] = typed.to_dict()
            data["_typed_semantic_frame"] = typed
        data["should_speak"] = True
        data["reply_obligation_repair"] = True
        return ReplyObligationResult(data, requirement, repaired=True, reason="required_reply_should_speak")

    # Exact local forms are preferable because they preserve a correct typed
    # semantic frame and avoid inventing content during provider degradation.
    local = build_degraded_reply(user_text, language_hint=language_hint)
    if local:
        if typed is not None:
            local["semantic_frame"] = typed.to_dict()
            local["_typed_semantic_frame"] = typed
        local = degrade_surface_payload(
            local,
            text=str(local.get("text", "") or ""),
            language=language_hint,
            reason="exact_local_required_reply",
            performance_preset=str(local.get("performance_hint", "neutral") or "neutral"),
            should_speak=True,
            # ``build_degraded_reply`` đã commit cặp bilingual tĩnh. Nếu bỏ hai
            # override này, degrade_surface_payload legacy sẽ lại copy display
            # tiếng Việt sang speech và tái tạo đúng bug ta đang sửa.
            speech_text=str(local.get("speech_text", "") or ""),
            speech_language=str(local.get("speech_language", "ja") or "ja"),
        )
        _observe_public_writer_fallback("exact_local_required_reply")
        return ReplyObligationResult(local, requirement, repaired=True, reason="exact_local_required_reply")

    text, speech_text, act_kind = _minimal_surface(typed, language_hint=language_hint)
    if typed is not None:
        data["semantic_frame"] = typed.to_dict()
        data["_typed_semantic_frame"] = typed
    data.update({
        "assistant_act": {
            "kind": act_kind,
            "proposition_mode": "continue" if act_kind == "repair" else "none",
            "confidence": 1.0,
        },
        "reply_obligation_repair": True,
    })
    data = degrade_surface_payload(
        data,
        text=text,
        language=language_hint,
        reason="required_reply_repair",
        performance_preset="repair" if act_kind == "repair" else "neutral",
        should_speak=True,
        speech_text=speech_text,
        speech_language="ja",
    )
    _observe_public_writer_fallback("required_reply_repair")
    return ReplyObligationResult(data, requirement, repaired=True, reason="required_reply_repair")


__all__ = [
    "ReplyObligationResult",
    "ReplyRequirement",
    "enforce_reply_obligation",
    "requirement_from_frame",
]
