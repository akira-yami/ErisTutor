# -*- coding: utf-8 -*-
"""Một quyết định chen ngang cho cả Chat, ASR và VoiceManager.

Không coi mọi câu ngắn là backchannel: chỉ những tiếng phụ họa *được biết rõ*
được giữ lại, để không nuốt câu hỏi/correction ngắn của người dùng.

Nâng cấp Incremental Dialogue (Text-first):
Bổ sung tham số ngữ cảnh câu nói trước của Eris (``previous_assistant_text``,
``last_assistant_act``) thông qua ``InteractionDynamicsController``. Nếu Eris vừa
hỏi câu xác nhận ("đúng không?", "giữ cấu trúc cũ nhé?"), từ "ừ" của user được
xem là câu trả lời thực sự (YIELD_TO_USER / STEERING) chứ không phải backchannel.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from .contracts import InterruptKind
from runtime.interaction_dynamics import assistant_opened_response_slot

_BACKCHANNELS = frozenset({
    "ừ", "uh", "uhm", "ừm", "à", "ờ", "ô", "ồ", "đúng", "đúng rồi",
    "ok", "oke", "mhm", "hm", "hmm", "được", "rồi", "yeah", "yep",
})
_STOP = re.compile(r"^(?:dừng|stop|khoan|đợi|thôi|im|đừng nói|ngưng)\b", re.I)
# Chỉ bỏ dấu câu ở rìa; không biến câu như 'ô, thế à?' thành phụ họa.
_EDGE_PUNCT = " \t\r\n.!…。、！？"


@dataclass(frozen=True, slots=True)
class InterruptDecision:
    kind: InterruptKind
    cancel_generation: bool
    cancel_audio: bool
    preserve_continuation: bool


def classify_interrupt(
    text: str,
    *,
    final: bool = True,
    previous_assistant_text: str = "",
    last_assistant_act: str = "",
    speech_busy: bool = True,
) -> InterruptDecision:
    """Phân loại hành vi chen ngang có xét ngữ cảnh câu nói trước của Eris."""
    normalized = unicodedata.normalize("NFC", str(text or "")).casefold()
    normalized = " ".join(normalized.strip().split())
    if normalized.strip(_EDGE_PUNCT) in _BACKCHANNELS:
        # Nếu Eris vừa đặt câu hỏi / mở slot trả lời, "ừ" là câu trả lời thực sự (YIELD_TO_USER),
        # không được nuốt thành backchannel.
        if assistant_opened_response_slot(
            previous_assistant_text,
            last_assistant_act=last_assistant_act,
        ):
            return InterruptDecision(InterruptKind.STEERING, True, True, False)
        return InterruptDecision(InterruptKind.BACKCHANNEL, False, False, True)
    if _STOP.search(normalized):
        # Nếu Eris đang im lặng (speech_busy=False) và câu dài dạng kể chuyện
        # ("khoan hôm qua còn chuyện này"), không coi "khoan" là lệnh STOP cứng.
        if not speech_busy and len(normalized.split()) > 4:
            return InterruptDecision(InterruptKind.STEERING, False, False, False)
        return InterruptDecision(InterruptKind.STOP, True, True, False)
    if normalized and (final or len(normalized) >= 4):
        return InterruptDecision(InterruptKind.STEERING, True, True, False)
    return InterruptDecision(InterruptKind.UNKNOWN, False, False, True)


__all__ = ["InterruptDecision", "classify_interrupt"]
