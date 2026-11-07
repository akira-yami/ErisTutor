# -*- coding: utf-8 -*-
"""voice_director.py — đạo diễn một câu TTS liên tục, có diễn xuất nhẹ.

Voice Director là side-channel nhỏ dành riêng cho cách phát giọng. Nó không sửa
public text, không chia câu và không quyết định bật/tắt giọng. Một transcript đi
vào luôn được synth thành một clip duy nhất; STYLE chỉ chọn sắc thái ấm, nhấn
nhẹ, tốc độ, năng lượng, pitch và tối đa một tiếng đệm/nonverbal cue ngắn.

Cue chỉ tồn tại ở lớp TTS, không chui vào display text hay memory. Nếu STYLE chậm
hoặc trả JSON sai, ProsodyDirector hiện có vẫn phát nguyên câu. Đạo diễn giọng là
lớp làm đẹp có thể bỏ qua, không phải cửa thu phí trước loa.
"""

from __future__ import annotations

import json
import logging
import os
import re
from typing import Any, Mapping

logger = logging.getLogger("VoiceDirector")

_ALLOWED_STYLES = {
    "neutral",
    "focused",
    "warm_soft",
    "curious_soft",
    "soft_tease",
    "dry_tease",
    "self_aware_dry",
    "annoyed_soft",
    "sharp_deadpan",
    "cold_boundary",
    "deadpan",
    "low_quiet",
}

_ALLOWED_PITCH = {"slightly_low", "neutral", "slightly_high"}
_ALLOWED_VOCALIZATIONS = {
    "none",
    "soft_hum",
    "gentle_ack",
    "small_laugh",
    "soft_sigh",
    "curious_hm",
    "soft_surprise",
}
_ALLOWED_VOCALIZATION_POSITIONS = {"prefix", "suffix"}

# Text fallback cho provider không có audio tags. ElevenLabs dùng audio tags cho
# laugh/sigh ngay trong cùng request; các filler còn lại được render trực tiếp
# vào transcript TTS nhưng không xuất hiện trên UI hay memory.
_VOCALIZATION_TEXT: dict[str, dict[str, str]] = {
    "vi": {
        "none": "",
        "soft_hum": "Ừm…",
        "gentle_ack": "Ừ.",
        "small_laugh": "Ha…",
        "soft_sigh": "Ha…",
        "curious_hm": "Hửm?",
        "soft_surprise": "Ồ.",
    },
    "ja": {
        "none": "",
        "soft_hum": "ん…",
        "gentle_ack": "うん。",
        "small_laugh": "ふふ。",
        "soft_sigh": "はぁ…",
        "curious_hm": "ん？",
        "soft_surprise": "へえ。",
    },
    "en": {
        "none": "",
        "soft_hum": "Mm…",
        "gentle_ack": "Mm-hm.",
        "small_laugh": "Heh…",
        "soft_sigh": "Haa…",
        "curious_hm": "Hm?",
        "soft_surprise": "Oh.",
    },
}


def voice_director_enabled() -> bool:
    """Runtime flag; bật mặc định nhưng env override vẫn thắng."""
    env_value = os.environ.get("ERIS_VOICE_DIRECTOR_ENABLED")
    if env_value is not None and str(env_value).strip():
        raw = str(env_value).strip().lower()
        return raw not in {"0", "false", "off", "no", "disabled"}
    try:
        from runtime.runtime_settings_store import runtime_bool

        return runtime_bool("tts", "voice_director_enabled", default=True)
    except Exception:
        return True


def voice_director_timeout_seconds() -> float:
    """Timeout ngắn cho lane STYLE phụ trợ.

    Mặc định 3 giây và chặn tối đa 6 giây. Nếu STYLE không kịp, phát nguyên câu
    với prosody deterministic thay vì bắt người dùng ngắm avatar suy tư.
    """
    raw = str(os.environ.get("ERIS_VOICE_DIRECTOR_TIMEOUT_S", "3") or "3").strip()
    try:
        return max(0.8, min(6.0, float(raw)))
    except ValueError:
        return 3.0


def vocalization_text(kind: object, language_hint: object = "vi") -> str:
    """Trả filler ngắn theo ngôn ngữ cho provider không hỗ trợ audio tag."""
    cue = str(kind or "none").strip().lower()
    lang = str(language_hint or "vi").strip().lower()
    if lang.startswith("ja"):
        lang = "ja"
    elif lang.startswith("en"):
        lang = "en"
    else:
        lang = "vi"
    return _VOCALIZATION_TEXT.get(lang, _VOCALIZATION_TEXT["vi"]).get(cue, "")


def _extract_content(payload: Any) -> str:
    if isinstance(payload, Mapping):
        choices = payload.get("choices")
        if isinstance(choices, list) and choices:
            first = choices[0] if isinstance(choices[0], Mapping) else {}
            message = first.get("message") if isinstance(first, Mapping) else {}
            if isinstance(message, Mapping):
                return str(message.get("content", "") or "")
    choices = getattr(payload, "choices", None) or []
    if choices:
        message = getattr(choices[0], "message", None)
        return str(getattr(message, "content", "") or "")
    return str(getattr(payload, "text", "") or "")


def _first_json_object(text: str) -> dict[str, Any]:
    clean = str(text or "").strip()
    clean = re.sub(r"^```(?:json|jsonl)?\s*", "", clean, flags=re.I)
    clean = re.sub(r"\s*```$", "", clean)
    try:
        value = json.loads(clean)
        return dict(value) if isinstance(value, Mapping) else {}
    except json.JSONDecodeError:
        start = clean.find("{")
        if start < 0:
            return {}
        try:
            value, _ = json.JSONDecoder().raw_decode(clean[start:])
            return dict(value) if isinstance(value, Mapping) else {}
        except json.JSONDecodeError:
            return {}


def _text_fingerprint(value: object) -> str:
    return re.sub(r"\s+", "", str(value or "")).strip()


def _clamp(value: object, low: float, high: float, default: float) -> float:
    try:
        return max(low, min(high, float(value)))
    except (TypeError, ValueError):
        return default


def _clean_phrase_list(value: object, *, text: str) -> list[str]:
    if not isinstance(value, (list, tuple)):
        return []
    result: list[str] = []
    for item in value:
        phrase = " ".join(str(item or "").split()).strip()
        if phrase and phrase in text and phrase not in result:
            result.append(phrase)
        if len(result) >= 2:
            break
    return result


def validate_voice_plan(raw: object, *, original_text: str, fallback_style: str) -> dict[str, Any]:
    """Chuẩn hóa Voice Director v3 thành một delivery duy nhất.

    Hỗ trợ đọc đúng một ``segments[0]`` từ client cũ để nâng cấp êm. Kế hoạch có
    nhiều segment bị loại bỏ hoàn toàn; fallback sẽ đọc nguyên câu. V3 cho phép
    đúng một vocalization ngắn, nhưng không cho model tự viết chuỗi âm thanh.
    """
    if not isinstance(raw, Mapping):
        return {}

    delivery_raw = raw.get("delivery")
    if not isinstance(delivery_raw, Mapping):
        legacy_segments = raw.get("segments")
        if not isinstance(legacy_segments, list) or len(legacy_segments) != 1:
            return {}
        delivery_raw = legacy_segments[0]
    if not isinstance(delivery_raw, Mapping):
        return {}

    returned_text = str(delivery_raw.get("text", original_text) or "").strip()
    if returned_text and _text_fingerprint(returned_text) != _text_fingerprint(original_text):
        return {}

    style = str(
        delivery_raw.get("style", raw.get("global_style", fallback_style)) or fallback_style
    ).strip().lower()
    if style not in _ALLOWED_STYLES:
        style = fallback_style if fallback_style in _ALLOWED_STYLES else "warm_soft"

    pitch = str(delivery_raw.get("pitch", "neutral") or "neutral").strip().lower()
    if pitch not in _ALLOWED_PITCH:
        pitch = "neutral"

    vocalization = str(delivery_raw.get("vocalization", "none") or "none").strip().lower()
    if vocalization not in _ALLOWED_VOCALIZATIONS:
        vocalization = "none"
    cue_position = str(delivery_raw.get("vocalization_position", "") or "").strip().lower()
    if cue_position not in _ALLOWED_VOCALIZATION_POSITIONS:
        cue_position = "suffix" if vocalization == "small_laugh" else "prefix"
    if vocalization == "none":
        cue_position = "prefix"

    delivery = {
        "style": style,
        "emphasis": _clean_phrase_list(delivery_raw.get("emphasis", ()), text=original_text),
        # Biên hẹp để đạo diễn chỉ tinh chỉnh, không bẻ giọng thành nhân vật khác.
        "speed": round(_clamp(delivery_raw.get("speed", 1.0), 0.94, 1.06, 1.0), 3),
        "energy": round(_clamp(delivery_raw.get("energy", 0.42), 0.26, 0.62, 0.42), 3),
        "warmth": round(_clamp(delivery_raw.get("warmth", 0.62), 0.35, 0.85, 0.62), 3),
        "pitch": pitch,
        # Timeline vẫn đóng đinh: có cue nhưng không có segment/pause riêng.
        "pause_after_ms": 0,
        "vocalization": vocalization,
        "vocalization_position": cue_position,
    }

    return {
        "version": 3,
        "source": "deepseek_style_warmth_director_v3",
        "global_style": style,
        "delivery": delivery,
        "reason": str(raw.get("reason", "") or "")[:160],
    }


class DeepSeekVoiceDirector:
    """Gọi STYLE worker để làm ấm cách đọc của transcript đã chốt."""

    def __init__(self, llm_service: Any) -> None:
        self.llm_service = llm_service

    @property
    def client(self) -> Any:
        return getattr(self.llm_service, "style_client", None) if self.llm_service is not None else None

    def plan(
        self,
        stage_frame: Any,
        *,
        payload: Mapping[str, Any] | None = None,
        channel: str = "",
        user_text: str = "",
    ) -> dict[str, Any]:
        if not voice_director_enabled() or self.client is None:
            return {}
        text = str(getattr(stage_frame, "tts_text", "") or "").strip()
        if not text:
            return {}

        fallback_style = str(getattr(stage_frame, "prosody_style", "neutral") or "neutral").strip().lower()
        emotion = str(getattr(stage_frame, "emotion", "neutral") or "neutral")
        payload = payload or {}
        emotion_debug = payload.get("emotion_debug") if isinstance(payload, Mapping) else {}
        summary = emotion_debug.get("summary") if isinstance(emotion_debug, Mapping) else {}
        valence = summary.get("valence", 0.0) if isinstance(summary, Mapping) else 0.0
        arousal = summary.get("arousal", 0.3) if isinstance(summary, Mapping) else 0.3
        stance = summary.get("stance", "") if isinstance(summary, Mapping) else ""

        system_prompt = (
            "You are the ERIS Warmth Director for one continuous TTS take. "
            "Never rewrite, split, shorten, expand, translate, punctuate, or otherwise edit the transcript. "
            "Never create segments, pauses, SSML, raw sound-effect text, or ElevenLabs tags. "
            "Never decide whether the line should be spoken. Your job is to make the existing line warmer, smoother, "
            "more natural, and emotionally attentive while keeping ERIS restrained, clear, private, subtly feminine, "
            "and never bubbly, childish, clingy, theatrical, breathy, or whispery. Technical content stays crisp. "
            "Firm boundaries stay firm. Humor stays dry but not cold.\n\n"
            "You may choose at most one short optional vocalization from this exact enum: "
            "none, soft_hum, gentle_ack, small_laugh, soft_sigh, curious_hm, soft_surprise. "
            "Use none most of the time. Use soft_hum for thoughtful hesitation, gentle_ack for quiet agreement, "
            "small_laugh only for genuinely playful or dry-humorous lines, soft_sigh only for relief or mild exasperation, "
            "curious_hm for a real question, and soft_surprise for mild pleasant surprise. Never use a cue for technical "
            "instructions, safety/medical/grief/distress, apologies, firm boundaries, or when it could sound mocking. "
            "Choose vocalization_position prefix or suffix; small_laugh usually fits suffix, the others usually prefix.\n\n"
            "Return exactly one JSON object with keys reason and delivery. delivery has only: style, emphasis, speed, "
            "energy, warmth, pitch, vocalization, vocalization_position. emphasis contains at most two exact phrases "
            "copied from the transcript. Use conservative values: speed 0.94-1.06, energy 0.26-0.62, warmth 0.35-0.85. "
            "Allowed styles: neutral, focused, warm_soft, curious_soft, soft_tease, dry_tease, self_aware_dry, "
            "annoyed_soft, sharp_deadpan, cold_boundary, deadpan, low_quiet. Allowed pitch: slightly_low, neutral, "
            "slightly_high. One delivery only. No segments key. JSON only."
        )
        user_prompt = (
            f"Channel: {str(channel or 'unknown')}\n"
            f"Emotion: {emotion}\n"
            f"Fallback prosody: {fallback_style}\n"
            f"Stance: {stance}\n"
            f"Valence: {valence}\n"
            f"Arousal: {arousal}\n"
            f"Previous user message: {str(user_text or '')[:700]}\n\n"
            "Immutable TTS transcript, rendered as one uninterrupted clip:\n"
            f"{text}\n\n"
            "Choose a subtle warmer delivery and, only when it truly helps, one short vocalization. JSON only."
        )

        try:
            response = self.client.create_chat_completion(
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=0.22,
                frequency_penalty=0.0,
                presence_penalty=0.0,
                max_tokens=360,
                response_format={"type": "json_object"},
                grammar=None,
                timeout=voice_director_timeout_seconds(),
            )
            parsed = _first_json_object(_extract_content(response))
            plan = validate_voice_plan(parsed, original_text=text, fallback_style=fallback_style)
            if not plan:
                logger.warning("[voice-director] invalid single-take plan; fallback prosody style=%s", fallback_style)
                return {}
            delivery = plan.get("delivery", {})
            logger.info(
                "[voice-director] single_take=1 style=%s warmth=%.2f cue=%s@%s timeout=%.1fs",
                delivery.get("style", fallback_style),
                float(delivery.get("warmth", 0.62) or 0.62),
                delivery.get("vocalization", "none"),
                delivery.get("vocalization_position", "prefix"),
                voice_director_timeout_seconds(),
            )
            return plan
        except Exception as exc:
            logger.warning("[voice-director] STYLE failed; fallback prosody: %s", exc)
            return {}


__all__ = [
    "DeepSeekVoiceDirector",
    "validate_voice_plan",
    "vocalization_text",
    "voice_director_enabled",
    "voice_director_timeout_seconds",
]
