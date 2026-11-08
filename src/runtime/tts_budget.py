# -*- coding: utf-8 -*-
"""tts_budget.py - budget riêng cho TTS, tách khỏi token pressure của LLM.

Mỗi provider có đặc tính khác nhau. Gemini TTS thích transcript ngắn hơn, còn
ElevenLabs có thể giữ một câu hội thoại dài trong một request. Dùng chung một
hard-limit 260 ký tự từng khiến câu bình thường bị xé thành nhiều clip rồi tạo
khoảng chết giữa các segment.
"""

from __future__ import annotations

import os
from dataclasses import dataclass


def _env_int(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(str(os.environ.get(name, default) or default).strip())
    except (TypeError, ValueError):
        value = default
    return max(minimum, min(maximum, value))


def _normalize_provider(provider: str) -> str:
    raw = str(provider or "").strip().lower()
    if raw in {"eleven", "11labs", "eleven_labs"}:
        return "elevenlabs"
    if raw in {"edge", "edge-tts", "edgetts"}:
        return "edge-tts"
    if raw in {"google", "google_tts", "gemini_tts"}:
        return "gemini"
    return raw


@dataclass(frozen=True, slots=True)
class TtsRequestBudget:
    # Giữ field legacy để test/code cũ không vỡ. Đây là limit bảo thủ cho Gemini.
    max_transcript_chars: int = 260
    elevenlabs_max_transcript_chars: int = 900
    max_prompt_chars: int = 8000
    max_style_chars: int = 900
    max_concurrency: int = 3

    @classmethod
    def from_env(cls) -> "TtsRequestBudget":
        # ERIS_TTS_MAX_TRANSCRIPT_CHARS là override chung cũ. Nếu user set nó,
        # tôn trọng cho mọi provider; nếu không, ElevenLabs dùng trần riêng.
        common_raw = str(os.environ.get("ERIS_TTS_MAX_TRANSCRIPT_CHARS", "") or "").strip()
        common = None
        if common_raw:
            try:
                common = max(48, min(4000, int(common_raw)))
            except (TypeError, ValueError):
                common = None

        gemini_limit = common or _env_int("ERIS_TTS_GEMINI_MAX_TRANSCRIPT_CHARS", 260, 48, 1600)
        eleven_limit = common or _env_int("ERIS_TTS_ELEVENLABS_MAX_TRANSCRIPT_CHARS", 900, 120, 4000)
        return cls(
            max_transcript_chars=gemini_limit,
            elevenlabs_max_transcript_chars=eleven_limit,
            max_prompt_chars=_env_int("ERIS_TTS_MAX_PROMPT_CHARS", 8000, 1200, 24000),
            max_style_chars=_env_int("ERIS_TTS_MAX_STYLE_CHARS", 900, 120, 4000),
            max_concurrency=_env_int("ERIS_TTS_MAX_CONCURRENCY", 3, 1, 8),
        )

    def transcript_limit(self, provider: str = "") -> int:
        """Trả hard-limit theo provider đang chạy.

        ElevenLabs được giữ câu dài hơn để một utterance bình thường = một clip.
        Provider lạ dùng limit Gemini bảo thủ thay vì đoán mò rồi gửi request khổng lồ.
        """
        normalized = _normalize_provider(provider)
        if normalized in {"elevenlabs", "edge-tts"}:
            return max(120, int(self.elevenlabs_max_transcript_chars))
        return max(48, int(self.max_transcript_chars))

    def validate(
        self,
        *,
        transcript: str,
        prompt: str,
        style_text: str = "",
        provider: str = "",
    ) -> None:
        text = str(transcript or "").strip()
        full_prompt = str(prompt or "")
        style = str(style_text or "")
        transcript_limit = self.transcript_limit(provider)
        if len(text) > transcript_limit:
            raise RuntimeError(
                f"TTS transcript quá dài ({len(text)}>{transcript_limit}, provider={_normalize_provider(provider) or 'default'}); "
                "StageOutputPipeline phải chia segment trước khi synthesize"
            )
        if len(style) > self.max_style_chars:
            raise RuntimeError(
                f"TTS style notes quá dài ({len(style)}>{self.max_style_chars}); hãy rút gọn director notes"
            )
        if len(full_prompt) > self.max_prompt_chars:
            raise RuntimeError(
                f"TTS prompt vượt hard limit ({len(full_prompt)}>{self.max_prompt_chars})"
            )


GLOBAL_TTS_REQUEST_BUDGET = TtsRequestBudget.from_env()


__all__ = ["TtsRequestBudget", "GLOBAL_TTS_REQUEST_BUDGET"]
