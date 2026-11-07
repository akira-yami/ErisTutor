# -*- coding: utf-8 -*-
"""prosody_director.py — đạo diễn nhịp đọc cho TTS.

LLM/model nhỏ không nên tự nhét "[sarcastic]" hay "(cười khẽ)" vào câu. Nó dễ
diễn quá tay, làm bẩn UI/memory, hoặc đọc luôn tag.  Module này chọn prosody ở
runtime từ các tín hiệu đã có: emotion/stance/intent/valence/arousal/text.

Kết quả chỉ là chỉ dẫn biểu diễn cho TTS, không phải claim ERIS "có cảm xúc"
như người.  Não quyết định câu; stage quyết định câu nào để đọc; prosody quyết
định đọc câu đó bằng nhịp nào.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Mapping


@dataclass(frozen=True, slots=True)
class ProsodyPlan:
    style: str = "neutral"
    opening_tag: str = ""
    director_notes: str = ""
    pause_level: str = "none"      # none | light | medium
    emphasis_mode: str = "none"    # none | last_phrase | key_word | clipped
    localizer_style: str = ""      # hint cho speech_localizer khi dịch sang JP


_TECH_RE = re.compile(
    r"\b(bug|lỗi|loi|code|test|tts|stage|runtime|parser|guard|model|repo|api|json|python|file|patch)\b",
    re.I,
)
_TEASE_RE = re.compile(
    r"(lại|lai|phá|pha|đáng nghi|dang nghi|hay nhỉ|hay nhi|giỏi|gioi|ngu thật|ngu that|toang|chết chưa|chet chua)",
    re.I,
)
_BOUNDARY_RE = re.compile(r"(đừng|dung|không phải|khong phai|bớt|bot|thôi|thoi)", re.I)


class ProsodyDirector:
    """Chọn kiểu đọc ổn định, ít phụ thuộc prompt.

    Các nhãn style cố ý ít và thô.  TTS cần một hướng biểu diễn rõ, không cần
    mười mấy cảm xúc vi mô khiến giọng bị cosplay.
    """

    def plan(
        self,
        text: str,
        *,
        emotion: str = "",
        intent: str = "",
        stance: str = "",
        valence: float = 0.0,
        arousal: float = 0.3,
        fallback_tag: str = "",
        notes_hint: str = "",
        style_hint: str = "",
    ) -> ProsodyPlan:
        clean = " ".join(str(text or "").split()).strip()
        style = self._style(
            clean,
            emotion=emotion,
            intent=intent,
            stance=stance,
            valence=valence,
            arousal=arousal,
            style_hint=style_hint,
        )
        base = _STYLE_PLANS.get(style, _STYLE_PLANS["neutral"])
        opening_tag = base.opening_tag or fallback_tag
        notes = base.director_notes
        if notes_hint:
            notes = f"{notes} {notes_hint}".strip()
        return ProsodyPlan(
            style=style,
            opening_tag=opening_tag,
            director_notes=notes,
            pause_level=base.pause_level,
            emphasis_mode=base.emphasis_mode,
            localizer_style=base.localizer_style,
        )

    def plan_beat(
        self,
        text: str,
        beat: Mapping[str, Any] | None = None,
        *,
        emotion: str = "",
    ) -> ProsodyPlan:
        """Map một OpeningBeat typed sang chỉ dẫn TTS, không sửa câu chữ.

        Pause vẫn do scheduler thực thi. Director prosody chỉ mô tả cách đặt
        nhịp và emphasis, tránh nhét stage direction vào transcript rồi cầu may
        provider không đọc thành tiếng.
        """

        data = beat if isinstance(beat, Mapping) else {}
        delivery = str(data.get("delivery", "") or "").strip().lower()
        aliases = {
            "deadpan_soft": "deadpan",
            "warm_restrained": "warm_soft",
            "quiet_warm": "warm_soft",
            "curious_restrained": "curious_soft",
            "dry_warm": "dry_tease",
        }
        style_hint = aliases.get(delivery, delivery)
        emphasis = [str(item).strip() for item in (data.get("emphasis") or ()) if str(item).strip()][:3]
        pause_after = max(0, min(1200, int(data.get("pause_after_ms", 0) or 0)))
        notes: list[str] = []
        if emphasis:
            notes.append(f"Lightly emphasize: {', '.join(emphasis)}.")
        if pause_after:
            notes.append(f"Land the beat cleanly before a scheduler pause of {pause_after}ms.")
        return self.plan(
            text,
            emotion=emotion,
            style_hint=style_hint,
            notes_hint=" ".join(notes),
        )

    def _style(
        self,
        text: str,
        *,
        emotion: str,
        intent: str,
        stance: str,
        valence: float,
        arousal: float,
        style_hint: str,
    ) -> str:
        hinted = str(style_hint or "").strip().lower()
        if hinted in _STYLE_PLANS:
            return hinted

        haystack = " ".join([text, emotion, intent, stance]).lower()
        if _TECH_RE.search(haystack):
            if _TEASE_RE.search(haystack) and valence >= -0.1:
                return "dry_tease"
            return "focused"
        if any(x in haystack for x in ("curious", "curiosity", "tò mò", "to mo")):
            return "curious_soft"
        if any(x in haystack for x in ("teasing", "playful", "amused", "warm_playful", "playful_aside")):
            return "soft_tease" if valence >= 0.12 else "dry_tease"
        if _TEASE_RE.search(haystack):
            return "dry_tease"
        if any(x in haystack for x in ("irritated", "guarded", "detached", "boundary")) or _BOUNDARY_RE.search(haystack):
            return "deadpan"
        if any(x in haystack for x in ("tired", "withdrawn", "quiet_low", "melancholy")):
            return "low_quiet"
        if arousal >= 0.62:
            return "focused"
        if valence > 0.18:
            return "warm_soft"
        return "neutral"


_STYLE_PLANS: dict[str, ProsodyPlan] = {
    "neutral": ProsodyPlan(
        style="neutral",
        opening_tag="",
        director_notes="Read naturally with restrained warmth. Do not overact.",
    ),
    "focused": ProsodyPlan(
        style="focused",
        opening_tag="[clearly, steady pace]",
        director_notes="Technical clarity first. Keep the rhythm precise, calm, and unhurried.",
        emphasis_mode="clipped",
        localizer_style="focused",
    ),
    "warm_soft": ProsodyPlan(
        style="warm_soft",
        opening_tag="[softly, warm]",
        director_notes="Small warmth in the voice, close but not sentimental.",
        localizer_style="warm_soft",
    ),
    "curious_soft": ProsodyPlan(
        style="curious_soft",
        opening_tag="[softly, curious]",
        director_notes="A small curious lift. Gentle, not interrogating; keep the line compact.",
        pause_level="light",
        emphasis_mode="last_phrase",
        localizer_style="curious_soft",
    ),
    "soft_tease": ProsodyPlan(
        style="soft_tease",
        opening_tag="[mischievously, with a tiny smile]",
        director_notes="A tiny smile in the voice. Teasing, intimate, never cartoonish.",
        pause_level="light",
        emphasis_mode="last_phrase",
        localizer_style="soft_tease",
    ),
    "dry_tease": ProsodyPlan(
        style="dry_tease",
        opening_tag="[dryly, teasing, slightly slow]",
        director_notes=(
            "Dry teasing. Let one short pause land before the final phrase. "
            "Emphasize the last phrase lightly, as if unimpressed but amused."
        ),
        pause_level="light",
        emphasis_mode="last_phrase",
        localizer_style="dry_tease",
    ),
    "self_aware_dry": ProsodyPlan(
        style="self_aware_dry",
        opening_tag="[quietly self-aware, dry]",
        director_notes="Own the mistake without sounding submissive. Brief pause, then continue directly.",
        pause_level="light",
        emphasis_mode="key_word",
        localizer_style="self_aware_dry",
    ),
    "annoyed_soft": ProsodyPlan(
        style="annoyed_soft",
        opening_tag="[controlled irritation, low and crisp]",
        director_notes="Mild irritation under control. Narrow pitch, clipped ending, no shouting.",
        pause_level="light",
        emphasis_mode="last_phrase",
        localizer_style="annoyed_soft",
    ),
    "sharp_deadpan": ProsodyPlan(
        style="sharp_deadpan",
        opening_tag="[sharply, deadpan]",
        director_notes="One precise verbal hit. Keep pace measured and stop cleanly after the final phrase.",
        pause_level="light",
        emphasis_mode="last_phrase",
        localizer_style="sharp_deadpan",
    ),
    "cold_boundary": ProsodyPlan(
        style="cold_boundary",
        opening_tag="[cold, controlled, direct]",
        director_notes="Clear boundary. Low pitch, no smile, no threat, no apology tail.",
        pause_level="medium",
        emphasis_mode="clipped",
        localizer_style="cold_boundary",
    ),
    "deadpan": ProsodyPlan(
        style="deadpan",
        opening_tag="[flatly, with dry amusement]",
        director_notes="Deadpan restraint. Keep pitch narrow, crisp, and slightly unimpressed.",
        pause_level="light",
        emphasis_mode="key_word",
        localizer_style="deadpan",
    ),
    "low_quiet": ProsodyPlan(
        style="low_quiet",
        opening_tag="[quietly, low energy]",
        director_notes="Quiet and low. Let the line breathe, but do not sound broken.",
        pause_level="medium",
        localizer_style="low_quiet",
    ),
}


__all__ = ["ProsodyDirector", "ProsodyPlan"]
