# -*- coding: utf-8 -*-
"""Typed public-utterance contract shared by cognition and stage renderers.

The semantic authority decides meaning, assistant act and both language surfaces
once.  Stage/UI/TTS then render that immutable commit without asking another
model to translate or reinterpret delivery.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping


_LANGUAGE_ALIASES = {
    "": "",
    "auto": "",
    "vn": "vi",
    "vi-vn": "vi",
    "vietnamese": "vi",
    "jp": "ja",
    "ja-jp": "ja",
    "japanese": "ja",
    "en-us": "en",
    "english": "en",
}

_ALLOWED_PRIORITIES = {"background", "low", "normal", "high", "urgent"}


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _text(value: Any, limit: int = 8000) -> str:
    text = "".join(
        ch
        for ch in str(value or "")
        if not (0xD800 <= ord(ch) <= 0xDFFF or ord(ch) > 0xFFFF)
    )
    return text.strip()[: max(1, int(limit))]


def _first_text(*values: Any, limit: int = 8000) -> str:
    for value in values:
        if isinstance(value, Mapping) or isinstance(value, (list, tuple, set)):
            continue
        text = _text(value, limit)
        if text:
            return text
    return ""


def _language(value: Any, fallback: str) -> str:
    raw = _text(value, 24).lower()
    normalized = _LANGUAGE_ALIASES.get(raw, raw)
    if normalized in {"vi", "ja", "en"}:
        return normalized
    return fallback


def _clamp01(value: Any, default: float = 0.5) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = float(default)
    return max(0.0, min(1.0, number))


def _bool(value: Any, default: bool = True) -> bool:
    if isinstance(value, bool):
        return value
    raw = _text(value, 16).lower()
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    return bool(default)


@dataclass(frozen=True, slots=True)
class LocalizedSurface:
    language: str
    text: str

    def to_dict(self) -> dict[str, Any]:
        return {"language": self.language, "text": self.text}


@dataclass(frozen=True, slots=True)
class PerformanceSpec:
    preset: str = "neutral"
    intensity: float = 0.5
    nonverbal: str = "none"

    def to_dict(self) -> dict[str, Any]:
        return {
            "preset": self.preset,
            "intensity": self.intensity,
            "nonverbal": self.nonverbal,
        }


@dataclass(frozen=True, slots=True)
class DeliverySpec:
    priority: str = "normal"
    interruptible: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "priority": self.priority,
            "interruptible": self.interruptible,
        }


@dataclass(frozen=True, slots=True)
class UtteranceSpec:
    """Immutable renderer-facing commit produced by the semantic authority."""

    display: LocalizedSurface
    speech: LocalizedSurface
    performance: PerformanceSpec
    delivery: DeliverySpec
    turn_id: str = ""

    @classmethod
    def from_mapping(
        cls,
        value: Mapping[str, Any] | Any,
        *,
        fallback_text: str = "",
        default_display_language: str = "vi",
        default_speech_language: str = "ja",
        fallback_preset: str = "neutral",
        turn_id: str = "",
    ) -> "UtteranceSpec":
        root = _mapping(value)
        utterance_raw = root.get("utterance")
        nested = _mapping(utterance_raw)
        if nested:
            root = {**root, **nested}
        elif not isinstance(utterance_raw, Mapping) and _text(utterance_raw):
            fallback_text = _text(utterance_raw)

        display_value = root.get("display")
        speech_value = root.get("speech")
        display_raw = _mapping(display_value)
        speech_raw = _mapping(speech_value)
        performance_raw = _mapping(root.get("performance"))
        delivery_raw = _mapping(root.get("delivery"))

        legacy_display = _first_text(
            root.get("text"),
            root.get("display_text"),
            root.get("reply"),
            root.get("response"),
            root.get("message"),
            root.get("content"),
            fallback_text,
        )
        display_text = _first_text(
            display_raw.get("text") if display_raw else None,
            display_value if not display_raw else None,
            legacy_display,
        )
        # ``speech_text`` chỉ được xem là một speech surface đã commit khi
        # provider/caller thực sự gửi nó.  Trước đây ta fallback thẳng sang
        # ``display_text`` nhưng vẫn gắn ``default_speech_language='ja'``.
        # Hậu quả là một câu tiếng Việt có thể bị đóng dấu thành "Japanese đã
        # prelocalized" và tầng TTS không còn cơ hội dịch nó nữa.
        explicit_speech_text = _first_text(
            speech_raw.get("text") if speech_raw else None,
            speech_value if not speech_raw else None,
            root.get("speech_text"),
        )
        speech_text = explicit_speech_text or display_text
        speech_surface_is_explicit = bool(explicit_speech_text)

        # Trích xuất nếu model lọt câu tiếng Nhật dạng 「...」 vào display_text
        import re
        jp_match = re.search(r"「([\u3040-\u30ff\u3400-\u9fff\s\S]+?)」", display_text)
        if jp_match:
            extracted_jp = jp_match.group(1).strip()
            clean_display = re.sub(r"\s*「[\u3040-\u30ff\u3400-\u9fff\s\S]+?」\s*", "", display_text).strip()
            if clean_display:
                display_text = clean_display
            if (not speech_raw or not speech_raw.get("text")) and not root.get("speech_text"):
                speech_text = extracted_jp
                speech_surface_is_explicit = True


        display_language = _language(
            display_raw.get("language", root.get("display_language", "")),
            default_display_language,
        )

        # Nếu speech surface chỉ là fallback từ display thì ngôn ngữ trung thực
        # của chuỗi đó phải là display language.  Japanese là *đích cần dịch*,
        # không phải thuộc tính ta được phép bịa cho một transcript tiếng Việt.
        # Stage/TTS vẫn có thể pin voice contract sang ja và localize ở bước sau.
        speech_language = _language(
            speech_raw.get("language", root.get("speech_language", ""))
            if speech_surface_is_explicit
            else "",
            (default_speech_language or display_language)
            if speech_surface_is_explicit
            else display_language,
        )

        preset = _text(
            performance_raw.get(
                "preset",
                root.get("performance_hint", fallback_preset),
            ),
            60,
        ).lower() or fallback_preset
        nonverbal = _text(
            performance_raw.get("nonverbal", root.get("nonverbal_cue", "none")),
            60,
        ).lower() or "none"
        priority = _text(
            delivery_raw.get("priority", root.get("delivery_priority", "normal")),
            24,
        ).lower()
        if priority not in _ALLOWED_PRIORITIES:
            priority = "normal"

        return cls(
            display=LocalizedSurface(display_language, display_text),
            speech=LocalizedSurface(speech_language, speech_text),
            performance=PerformanceSpec(
                preset=preset,
                intensity=_clamp01(
                    performance_raw.get(
                        "intensity",
                        root.get("performance_intensity", 0.5),
                    )
                ),
                nonverbal=nonverbal,
            ),
            delivery=DeliverySpec(
                priority=priority,
                interruptible=_bool(
                    delivery_raw.get(
                        "interruptible",
                        root.get("interruptible", True),
                    ),
                    True,
                ),
            ),
            turn_id=_text(root.get("turn_id", turn_id), 120) or _text(turn_id, 120),
        )

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "display": self.display.to_dict(),
            "speech": self.speech.to_dict(),
            "performance": self.performance.to_dict(),
            "delivery": self.delivery.to_dict(),
        }
        if self.turn_id:
            payload["turn_id"] = self.turn_id
        return payload

    def to_flat_payload(self) -> dict[str, Any]:
        return {
            "text": self.display.text,
            "display_language": self.display.language,
            "speech_text": self.speech.text,
            "speech_language": self.speech.language,
            "performance_hint": self.performance.preset,
            "performance_intensity": self.performance.intensity,
            "nonverbal_cue": "" if self.performance.nonverbal == "none" else self.performance.nonverbal,
            "delivery_priority": self.delivery.priority,
            "interruptible": self.delivery.interruptible,
            "utterance_turn_id": self.turn_id,
            "utterance": self.to_dict(),
        }


def degrade_surface_payload(
    payload: Mapping[str, Any] | Any,
    *,
    text: str,
    language: str = "vi",
    reason: str,
    performance_preset: str | None = None,
    should_speak: bool = True,
    speech_text: str | None = None,
    speech_language: str | None = None,
) -> dict[str, Any]:
    """Rebind a post-commit rewrite without leaving contradictory surfaces.

    A deterministic safety/repair policy may occasionally replace public text
    after the semantic model has returned.  Mặc định policy không được tự dịch,
    nên display và speech cùng dùng replacement text/language.  Tuy nhiên một số
    fallback *đã có sẵn* cặp câu song ngữ được viết tĩnh trong source; caller có
    thể truyền ``speech_text``/``speech_language`` để giữ cặp đó.  Hàm này không
    sinh bản dịch mới, vì vậy semantic authority vẫn không bị nhân đôi.
    """

    data = dict(payload) if isinstance(payload, Mapping) else {}
    previous = UtteranceSpec.from_mapping(
        data,
        fallback_text=str(data.get("text", "") or ""),
        default_display_language=language or "vi",
        default_speech_language=language or "vi",
        fallback_preset=str(data.get("performance_hint", "neutral") or "neutral"),
    )
    resolved_language = _language(language, previous.display.language or "vi")
    resolved_text = _text(text)
    # ``None`` nghĩa là caller không có speech surface riêng -> dùng cùng display
    # như hành vi legacy. Chuỗi rỗng được giữ là rỗng nếu caller truyền rõ ràng.
    has_explicit_speech_override = speech_text is not None
    resolved_speech_text = _text(speech_text) if has_explicit_speech_override else resolved_text
    resolved_speech_language = _language(
        speech_language,
        previous.speech.language or resolved_language,
    ) if has_explicit_speech_override else resolved_language
    preset = _text(performance_preset, 60).lower() if performance_preset is not None else previous.performance.preset
    rebuilt = UtteranceSpec(
        display=LocalizedSurface(resolved_language, resolved_text),
        speech=LocalizedSurface(resolved_speech_language, resolved_speech_text),
        performance=PerformanceSpec(
            preset=preset or "neutral",
            intensity=previous.performance.intensity,
            nonverbal=previous.performance.nonverbal,
        ),
        delivery=previous.delivery,
        turn_id=previous.turn_id,
    )
    data.update(rebuilt.to_flat_payload())
    data["should_speak"] = bool(should_speak and resolved_text)
    data["utterance_degraded"] = True
    data["utterance_degraded_reason"] = _text(reason, 120) or "post_commit_rewrite"
    return data


__all__ = [
    "DeliverySpec",
    "LocalizedSurface",
    "PerformanceSpec",
    "UtteranceSpec",
    "degrade_surface_payload",
]
