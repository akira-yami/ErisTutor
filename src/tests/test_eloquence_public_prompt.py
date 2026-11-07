# -*- coding: utf-8 -*-
"""Gói eloquence — kiểm prompt public writer giữ đủ mảnh bắt buộc và đã bỏ
các chuỗi giọng-kỹ-thuật cũ. Không khẳng định chất lượng văn (user duyệt gu),
chỉ bảo đảm prompt vẫn là str hợp lệ với hằng/field bắt buộc và language-lock.
"""
from __future__ import annotations

from runtime.ai_orchestrated.model_router import AIRuntimeModelRouter
from runtime.eris_voice_contract import CHAT_VOICE_CORE_V39
from runtime.social_stance_policy import STANCE_PUBLIC_RULES_VI


def test_eloquence_prompt_keeps_required_pieces() -> None:
    p = AIRuntimeModelRouter._public_reply_prompt("vi", "ja")

    # Vào vai: writer là chính ERIS nói với một người thật.
    assert "ERIS" in p
    assert "người thật" in p
    assert "giọng nói trực tiếp của ERIS" in p

    # Hằng V39 và stance nhúng nguyên văn.
    assert CHAT_VOICE_CORE_V39 in p
    assert STANCE_PUBLIC_RULES_VI[:30] in p

    # Hai bề mặt + khoá ngôn ngữ theo display/speech language.
    assert "tiếng Việt" in p
    assert "tiếng Nhật" in p
    assert "text" in p
    assert "speech_text" in p
    assert "should_speak" in p

    # Lằn ranh trung thực giữ đầy đủ ý.
    assert "grounding" in p
    assert "khả năng" in p  # capability honesty
    assert "receipt" in p
    assert "EVIDENCE_ENRICHMENT_V73" in p

    assert "Return JSON only." in p


def test_eloquence_prompt_language_lock_mentions_both_languages() -> None:
    p = AIRuntimeModelRouter._public_reply_prompt("vi", "ja")
    # Khoá ngôn ngữ hiển thị: text giữ đúng display language, không trộn cả câu.
    assert "Giữ nguyên text trong tiếng Việt" in p


def test_eloquence_prompt_dropped_old_technical_strings() -> None:
    p = AIRuntimeModelRouter._public_reply_prompt("vi", "ja")
    # Các chuỗi cố ý bỏ: sanitizer/tầng dưới đã lo hoặc viết lại giọng người.
    assert "HARD DISPLAY LANGUAGE LOCK" not in p
    assert "không reasoning" not in p
    assert "stage direction" not in p
    assert "public writer cho một lượt chat" not in p
    assert "one concise" not in p.lower()
    # Regression gu INFJ (không được tái xuất chuỗi preset cũ).
    assert "ấm kín" not in p
