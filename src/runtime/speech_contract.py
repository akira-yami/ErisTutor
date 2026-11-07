# -*- coding: utf-8 -*-
"""Speech contract được chốt một lần cho mỗi turn.

Provider fallback được phép đổi nhà cung cấp hoặc quality tier. Nó không được tự
đổi ngôn ngữ và danh tính giọng giữa chừng. Contract này đi từ StageFrame tới
TTS request để giữ nguyên hai thứ đó.
"""

from __future__ import annotations

import os
import re
from dataclasses import asdict, dataclass
from typing import Any, Mapping


_JAPANESE_RE = re.compile(r"[\u3040-\u30ff\u3400-\u9fff]")
_LOCALES = {"vi": "vi-VN", "ja": "ja-JP", "en": "en-US"}


def configured_speech_language() -> str:
    """Live voice selection; independent from the public/chat language."""
    from runtime.runtime_settings_store import runtime_text

    raw = runtime_text("tts", "language", default="ja").strip().lower()
    return normalize_language(raw) if raw not in {"", "auto"} else "ja"


def normalize_language(value: str, *, text: str = "") -> str:
    raw = str(value or "").strip().lower().replace("_", "-")
    if raw in {"ja", "jp", "japanese"} or raw.startswith("ja-"):
        return "ja"
    if raw in {"vi", "vn", "vietnamese", "tiếng việt", "tieng viet"} or raw.startswith("vi-"):
        return "vi"
    if raw in {"en", "english"} or raw.startswith("en-"):
        return "en"
    if _JAPANESE_RE.search(str(text or "")):
        return "ja"
    # Eris là hệ Việt; auto không được biến thành tung đồng xu giữa các provider.
    return "vi"


@dataclass(frozen=True, slots=True)
class SpeechContract:
    language: str = "vi"
    locale: str = "vi-VN"
    voice_family: str = "eris_primary"
    provider_tier: str = "primary"
    can_translate: bool = False

    def to_payload(self) -> dict[str, Any]:
        return asdict(self)


class SpeechContractResolver:
    def __init__(self, *, default_language: str = "ja") -> None:
        self.default_language = default_language or "vi"

    def resolve(self, payload: Mapping[str, Any] | None, *, text: str = "") -> SpeechContract:
        data = dict(payload or {})
        supplied = data.get("speech_contract") if isinstance(data.get("speech_contract"), Mapping) else {}
        supplied = dict(supplied or {})
        try:
            from runtime.rollout_control import feature_enabled
            language_pin_enabled = feature_enabled("tts_language_pin")
        except Exception:
            language_pin_enabled = str(os.environ.get("ERIS_TTS_LANGUAGE_PIN", "1") or "1").strip().lower() not in {
                "0", "false", "off", "no",
            }
        if not language_pin_enabled:
            # Rollback an toàn: bỏ override contract của turn và suy từ transcript,
            # nhưng vẫn không cho fallback nhảy sang ngôn ngữ khác.
            language = normalize_language("", text=text)
            locale = _LOCALES.get(language, language)
            voice_family = str(os.environ.get("ERIS_TTS_VOICE_FAMILY") or os.environ.get("ERIS_TTS_VOICE") or "eris_primary").strip()
            return SpeechContract(language, locale, voice_family, "safe_legacy", False)
        language = normalize_language(
            str(
                supplied.get("language")
                or data.get("tts_language")
                or data.get("language_hint")
                or self.default_language
            ),
            text=text,
        )
        default_language = normalize_language(self.default_language, text=text)
        if default_language == "ja" and language != "ja":
            language = "ja"
        locale = str(supplied.get("locale") or _LOCALES.get(language, language)).strip()
        voice_family = str(
            supplied.get("voice_family")
            or data.get("voice_family")
            or os.environ.get("ERIS_TTS_VOICE_FAMILY")
            or os.environ.get("ERIS_TTS_VOICE")
            or "eris_primary"
        ).strip()
        provider_tier = str(supplied.get("provider_tier") or "primary").strip() or "primary"
        if "can_translate" in supplied:
            can_translate = bool(supplied.get("can_translate"))
        elif "can_translate" in data:
            can_translate = bool(data.get("can_translate"))
        else:
            can_translate = language == "ja"
        return SpeechContract(language, locale, voice_family, provider_tier, can_translate)


__all__ = ["SpeechContract", "SpeechContractResolver", "normalize_language", "configured_speech_language"]
