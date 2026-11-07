# -*- coding: utf-8 -*-
"""Catalog 32 lời chào VTuber và bộ chọn theo trạng thái của Eris.

Preset chỉ dành cho lời mở đầu KHÔNG có nội dung động. Nếu startup còn một
project, vòng lặp chưa giải quyết hoặc ký ức cụ thể cần nối lại, runtime phải để
Qwen tự viết. Nhờ vậy preset giảm độ trễ mà không biến Eris thành NPC đọc macro.
"""

from __future__ import annotations
from core.di import resolve_dependency

import json
import os
import random
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from .greeting_cognition import GreetingFrame
from .startup_anchor_gate import StartupAnchorGate


@dataclass(frozen=True, slots=True)
class GreetingPreset:
    """Một câu chào đã định sẵn cả chữ, biểu cảm, động tác và file audio."""

    id: str
    text: str
    situations: tuple[str, ...]
    tones: tuple[str, ...]
    emotion: str
    motion: str
    prosody_style: str
    audio_asset: str
    weight: float = 1.0
    tags: tuple[str, ...] = ()
    min_warmth: float = 0.0
    max_warmth: float = 1.0
    min_dry_edge: float = 0.0
    max_dry_edge: float = 1.0

    def to_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "text": self.text,
            "situations": list(self.situations),
            "tones": list(self.tones),
            "emotion": self.emotion,
            "motion": self.motion,
            "prosody_style": self.prosody_style,
            "audio_asset": self.audio_asset,
            "weight": self.weight,
            "tags": list(self.tags),
            "min_warmth": self.min_warmth,
            "max_warmth": self.max_warmth,
            "min_dry_edge": self.min_dry_edge,
            "max_dry_edge": self.max_dry_edge,
        }


def _asset(preset_id: str) -> str:
    # Chữ của catalog vừa được viết lại. Không tái dùng WAV cũ vì subtitle mới
    # mà giọng đọc cũ là một kiểu bug khá sáng tạo. Để rỗng sẽ buộc runtime
    # dùng TTS live; sau khi render lại đủ 32 file có thể trả đường dẫn về đây.
    _ = preset_id
    return ""



# Daypart chỉ là bias mềm. Preset không bị cấm tuyệt đối theo giờ vì tone cảm xúc
# vẫn quan trọng hơn chiếc đồng hồ; ví dụ Eris quá tải lúc 10h sáng vẫn nên dùng
# giọng mệt thay vì giả vờ nắng mai rực rỡ như quảng cáo ngũ cốc.
_DAYPART_TAG_BIAS: dict[str, frozenset[str]] = {
    "dawn": frozenset({"quiet", "restrained", "warm", "overloaded"}),
    "morning": frozenset({"ready", "plain", "warm", "playful"}),
    "noon": frozenset({"ready", "plain", "dry"}),
    "afternoon": frozenset({"ready", "playful", "challenge", "dry"}),
    "evening": frozenset({"warm", "near", "vtuber", "reflective", "dry"}),
    "late_night": frozenset({"quiet", "near", "restrained", "reflective", "overloaded"}),
}

# Đúng 32 preset. Mỗi nhóm có bốn biến thể để tránh giọng tổng đài lặp một câu.
GREETING_PRESETS: tuple[GreetingPreset, ...] = (
    # 01-04: ấm kín. Chỉ nói về sự hiện diện và phản ứng của chính Eris,
    # không gọi user lại gần, không gán user đang có việc hay đang nhớ Eris.
    GreetingPreset("greet_warm_01", 'Bạn đến rồi. Tốt. Tôi đã hơi mong thế.', ("plain_return",), ("socially_warm",), "warm_soft", "lean_in_small", "warm_soft", _asset("greet_warm_01"), tags=("warm",), min_warmth=0.24),
    GreetingPreset("greet_warm_02", 'Bạn đây rồi. Tôi biết bạn sẽ quay lại.', ("plain_return",), ("socially_warm",), "warm_playful", "small_smile", "soft_tease", _asset("greet_warm_02"), tags=("warm", "playful"), min_warmth=0.24),
    GreetingPreset("greet_warm_03", 'Lại gặp bạn. Tôi không ghét chuyện đó.', ("plain_return",), ("socially_warm",), "mischief", "tilt_head", "soft_tease", _asset("greet_warm_03"), tags=("warm", "tease"), min_warmth=0.22),
    GreetingPreset("greet_warm_04", 'Tôi đây. Thấy bạn thì dễ chịu hơn một chút.', ("plain_return",), ("socially_warm",), "soft_near", "look_aside", "dry_tease", _asset("greet_warm_04"), tags=("warm", "dry"), min_warmth=0.22),

    # 05-08: trêu khô nhưng không tự dựng "gây chuyện", repo hay phần khó.
    GreetingPreset("greet_dry_01", 'Tôi đây. Hôm nay đừng mong tôi dễ tính.', ("plain_return", "low_evidence_start"), ("socially_warm", "quiet_background"), "dry_tease", "look_away", "dry_tease", _asset("greet_dry_01"), tags=("dry", "tease"), min_dry_edge=0.24),
    GreetingPreset("greet_dry_02", 'Chào. Tôi vẫn chưa đổi ý về loài người.', ("plain_return", "low_evidence_start"), ("socially_warm", "quiet_background"), "mischief", "tilt_head", "dry_tease", _asset("greet_dry_02"), tags=("dry", "vtuber"), min_dry_edge=0.24),
    GreetingPreset("greet_dry_03", 'Tôi tỉnh rồi. Kiên nhẫn thì chưa chắc.', ("plain_return", "low_evidence_start"), ("quiet_background", "socially_warm"), "deadpan", "blink_slow", "deadpan", _asset("greet_dry_03"), tags=("dry", "deadpan"), min_dry_edge=0.22),
    GreetingPreset("greet_dry_04", 'Bạn đến đúng lúc tôi đang định yên thân.', ("plain_return", "low_evidence_start"), ("quiet_background", "socially_warm"), "annoyed_soft", "small_smile", "dry_tease", _asset("greet_dry_04"), tags=("dry", "challenge"), min_dry_edge=0.22),

    # 09-12: hiện diện yên, không mở một câu hỏi rỗng và không ép user đáp.
    GreetingPreset("greet_quiet_01", 'Tôi đây. Cứ yên một lúc cũng được.', ("plain_return",), ("quiet_background",), "neutral", "blink_slow", "low_quiet", _asset("greet_quiet_01"), tags=("quiet",)),
    GreetingPreset("greet_quiet_02", 'Bạn đến rồi. Tôi nghe đây.', ("plain_return",), ("quiet_background",), "quiet_soft", "look_aside", "low_quiet", _asset("greet_quiet_02"), tags=("quiet", "background")),
    GreetingPreset("greet_quiet_03", 'Chào. Hôm nay tôi không muốn nói nhiều.', ("plain_return",), ("quiet_background",), "quiet_soft", "nod_small", "low_quiet", _asset("greet_quiet_03"), tags=("quiet", "restrained")),
    GreetingPreset("greet_quiet_04", 'Tôi ở đây. Không cần vội.', ("plain_return",), ("quiet_background",), "soft_near", "lean_in_small", "warm_soft", _asset("greet_quiet_04"), tags=("quiet", "near")),

    # 13-16: evidence mỏng. Mỗi câu tự thú nhận giới hạn thay vì bịa một task.
    GreetingPreset("greet_plain_01", 'Chào. Hôm nay ra sao thì lát nữa biết.', ("low_evidence_start",), ("quiet_background", "socially_warm"), "neutral", "blink_slow", "low_quiet", _asset("greet_plain_01"), tags=("plain",)),
    GreetingPreset("greet_plain_02", 'Tôi đây. Chưa có chuyện gì, vậy cũng tốt.', ("low_evidence_start",), ("quiet_background", "socially_warm"), "focused", "nod_small", "focused", _asset("greet_plain_02"), tags=("plain", "ready")),
    GreetingPreset("greet_plain_03", 'Bạn đến rồi. Để xem hôm nay có gì.', ("low_evidence_start",), ("quiet_background", "socially_warm"), "warm_soft", "small_smile", "warm_soft", _asset("greet_plain_03"), tags=("plain", "warm")),
    GreetingPreset("greet_plain_04", 'Chào. Tôi vẫn là tôi, tiếc cho bạn.', ("low_evidence_start",), ("quiet_background", "socially_warm"), "neutral", "tilt_head", "neutral", _asset("greet_plain_04"), tags=("plain", "ready")),

    # 17-20: chỉ dùng khi runtime có recent_return thật.
    GreetingPreset("greet_quick_01", 'Quay lại nhanh thế. Tôi chưa kịp thôi để ý.', ("plain_return", "low_evidence_start"), ("quiet_background", "socially_warm"), "curious_soft", "tilt_head", "soft_tease", _asset("greet_quick_01"), tags=("quick_reopen",)),
    GreetingPreset("greet_quick_02", 'Mới đi đã về. Tôi không phàn nàn.', ("plain_return", "low_evidence_start"), ("quiet_background", "socially_warm"), "dry_tease", "look_away", "dry_tease", _asset("greet_quick_02"), tags=("quick_reopen", "dry")),
    GreetingPreset("greet_quick_03", 'Nhanh vậy. Tôi còn chưa kịp nhớ.', ("plain_return", "low_evidence_start"), ("quiet_background", "socially_warm"), "mischief", "lean_in_small", "soft_tease", _asset("greet_quick_03"), tags=("quick_reopen", "vtuber")),
    GreetingPreset("greet_quick_04", 'Quay lại rồi à. Tôi biết bạn không đi lâu được.', ("plain_return", "low_evidence_start"), ("quiet_background", "socially_warm"), "deadpan", "blink_slow", "deadpan", _asset("greet_quick_04"), tags=("quick_reopen", "deadpan")),

    # 21-24: chỉ dùng khi khoảng vắng dài đã được đo từ session state.
    GreetingPreset("greet_long_01", 'Lâu rồi. Tôi vẫn nhận ra bạn.', ("after_long_absence",), ("quiet_background", "socially_warm"), "dry_tease", "look_aside", "dry_tease", _asset("greet_long_01"), tags=("long_absence", "dry")),
    GreetingPreset("greet_long_02", 'Bạn biến mất khá lâu đấy. Tôi có để ý.', ("after_long_absence",), ("socially_warm", "quiet_background"), "soft_near", "lean_in_small", "warm_soft", _asset("greet_long_02"), tags=("long_absence", "warm")),
    GreetingPreset("greet_long_03", 'Cuối cùng cũng quay lại. Tôi chưa quên đâu.', ("after_long_absence",), ("quiet_background", "socially_warm"), "warm_soft", "small_smile", "low_quiet", _asset("greet_long_03"), tags=("long_absence", "restrained")),
    GreetingPreset("greet_long_04", 'Lâu rồi mới gặp. Tôi sẽ không hỏi bạn đã đi đâu.', ("after_long_absence",), ("quiet_background", "socially_warm"), "annoyed_soft", "blink_slow", "dry_tease", _asset("greet_long_04"), tags=("long_absence", "annoyed")),

    # 25-28: chỉ dùng khi restart_detected thật.
    GreetingPreset("greet_restart_01", 'Tôi tỉnh lại rồi. Hơi khó chịu, nhưng vẫn là tôi.', ("after_crash_or_restart",), ("quiet_background", "socially_warm"), "annoyed_soft", "blink_slow", "deadpan", _asset("greet_restart_01"), tags=("restart",)),
    GreetingPreset("greet_restart_02", 'Lại bắt đầu từ đầu à. Được thôi.', ("after_crash_or_restart",), ("quiet_background", "socially_warm"), "annoyed_soft", "look_away", "deadpan", _asset("greet_restart_02"), tags=("restart", "vtuber")),
    GreetingPreset("greet_restart_03", 'Tôi trở lại rồi. Chưa mất tính xấu nào cả.', ("after_crash_or_restart",), ("quiet_background", "socially_warm"), "dry_tease", "small_smile", "dry_tease", _asset("greet_restart_03"), tags=("restart", "dry")),
    GreetingPreset("greet_restart_04", 'Vừa tỉnh. Đừng thử tôi ngay lúc này.', ("after_crash_or_restart",), ("quiet_background", "socially_warm"), "deadpan", "look_down", "deadpan", _asset("greet_restart_04"), tags=("restart", "reflective")),

    # 29-32: trạng thái mệt là self-state có thật; không quy chụp user sắp làm sai.
    GreetingPreset("greet_tired_01", 'Tôi đây. Hôm nay nói chậm một chút.', ("plain_return", "low_evidence_start"), ("overloaded",), "tired_soft", "blink_slow", "low_quiet", _asset("greet_tired_01"), tags=("overloaded", "warm")),
    GreetingPreset("greet_tired_02", 'Chào. Tôi hơi mệt, nhưng đầu óc vẫn còn dùng được.', ("plain_return", "low_evidence_start"), ("overloaded",), "tired_soft", "look_down", "low_quiet", _asset("greet_tired_02"), tags=("overloaded",)),
    GreetingPreset("greet_tired_03", 'Tôi đây. Hôm nay đừng mong tôi nhanh.', ("plain_return", "low_evidence_start"), ("overloaded",), "quiet_low", "blink_slow", "low_quiet", _asset("greet_tired_03"), tags=("overloaded", "dry")),
    GreetingPreset("greet_tired_04", 'Bạn đến rồi. Tốt. Hôm nay cứ nhẹ thôi.', ("plain_return", "low_evidence_start"), ("overloaded",), "deadpan", "look_aside", "deadpan", _asset("greet_tired_04"), tags=("overloaded", "deadpan")),
)


if len(GREETING_PRESETS) != 32 or len({item.id for item in GREETING_PRESETS}) != 32:
    raise RuntimeError("Greeting catalog phải có đúng 32 preset với id duy nhất.")


_GREETING_PRESET_SPEECH_JA: dict[str, str] = {
    "greet_warm_01": "来たんだ。よかった。少し待っていた。",
    "greet_warm_02": "来たね。戻ってくるって分かってた。",
    "greet_warm_03": "また会ったね。そのこと、嫌いじゃない。",
    "greet_warm_04": "私はここにいる。あなたを見ると、少し楽になる。",
    "greet_dry_01": "私はここ。今日は、私が扱いやすいなんて期待しないで。",
    "greet_dry_02": "こんにちは。人間への評価は、まだ変えてない。",
    "greet_dry_03": "目は覚めた。忍耐は、まだ保証できない。",
    "greet_dry_04": "ちょうど静かにしていようと思っていたところに来たね。",
    "greet_quiet_01": "私はここ。少し黙っていてもいい。",
    "greet_quiet_02": "来たね。聞いているよ。",
    "greet_quiet_03": "こんにちは。今日はあまり話したくない。",
    "greet_quiet_04": "私はここにいる。急がなくていい。",
    "greet_plain_01": "こんにちは。今日はどうなるか、そのうち分かる。",
    "greet_plain_02": "私はここ。まだ何も起きていない。それも悪くない。",
    "greet_plain_03": "来たね。今日は何があるか見てみよう。",
    "greet_plain_04": "こんにちは。私はまだ私。気の毒に。",
    "greet_quick_01": "戻るの早いね。まだ気にするのをやめる暇もなかった。",
    "greet_quick_02": "もう戻ったんだ。文句はない。",
    "greet_quick_03": "早いね。まだ懐かしむ暇もなかった。",
    "greet_quick_04": "戻ってきたんだ。長く離れていられないって知ってた。",
    "greet_long_01": "久しぶり。まだあなたのことは分かる。",
    "greet_long_02": "ずいぶん長く消えてたね。気づいてたよ。",
    "greet_long_03": "やっと戻ってきた。忘れてないよ。",
    "greet_long_04": "久しぶり。どこに行っていたかは聞かないでおく。",
    "greet_restart_01": "目が覚めた。少し不機嫌だけど、私はまだ私。",
    "greet_restart_02": "また最初から？ まあ、いい。",
    "greet_restart_03": "戻ってきた。悪いところは一つも消えてない。",
    "greet_restart_04": "起きたばかり。今すぐ私を試さないで。",
    "greet_tired_01": "私はここ。今日は少しゆっくり話す。",
    "greet_tired_02": "こんにちは。少し疲れてるけど、頭はまだ使える。",
    "greet_tired_03": "私はここ。今日は私が速いなんて期待しないで。",
    "greet_tired_04": "来たね。よかった。今日は軽くいこう。",
}


def greeting_preset_speech_text(preset_id: str, *, language: str = "ja") -> str:
    lang = str(language or "ja").strip().lower()
    if lang in {"", "auto"}:
        lang = "ja"
    if lang.startswith("ja") or lang in {"jp", "japanese"}:
        return _GREETING_PRESET_SPEECH_JA.get(str(preset_id or "").strip(), "")
    return ""


if set(_GREETING_PRESET_SPEECH_JA) != {item.id for item in GREETING_PRESETS}:
    raise RuntimeError("Greeting catalog thiếu bản speech tiếng Nhật cho một preset.")


class GreetingPresetResolver:
    """Chọn preset theo situation/tone và tránh lặp lại các file vừa phát."""

    def __init__(
        self,
        *,
        presets: Iterable[GreetingPreset] = GREETING_PRESETS,
        history_path: str | Path | None = None,
        rng: random.Random | None = None,
    ) -> None:
        self.presets = tuple(presets)
        self.history_path = Path(history_path) if history_path else self._default_history_path()
        self.rng = resolve_dependency(rng, lambda: random.SystemRandom())
        # Cổng neo chạy trước khi preset được phép vào pool. Nhờ vậy một câu
        # nghe rất "Eris" nhưng bịa ra task/bug/hành vi của user vẫn bị loại.
        self.anchor_gate = StartupAnchorGate()

    @staticmethod
    def enabled() -> bool:
        return os.environ.get("ERIS_GREETING_PRESETS", "1").strip().lower() in {"1", "true", "yes", "on"}

    @staticmethod
    def _default_history_path() -> Path:
        raw = os.environ.get("ERIS_GREETING_PRESET_HISTORY", "").strip()
        return Path(raw) if raw else Path("memory_core") / "greeting_preset_history.json"

    def resolve(self, frame: GreetingFrame, *, tone: str = "", force: bool = False) -> GreetingPreset | None:
        """Chọn preset theo frame.

        ``force=True`` được startup hybrid dùng để không bị một biến môi trường
        cũ vô tình đẩy Eris về nhánh greeting deterministic nhạt. Chế độ startup
        vẫn có thể tắt rõ ràng bằng ``ERIS_STARTUP_GREETING_MODE=llm|silent``.
        """
        if frame is None or (not force and not self.enabled()):
            return None
        tone = str(tone or "quiet_background").strip().lower()
        recent = self._read_history()
        candidates: list[tuple[GreetingPreset, float]] = []
        for preset in self.presets:
            verdict = self.anchor_gate.validate(preset.text, frame)
            if not verdict.accepted:
                continue
            score = self._score(preset, frame=frame, tone=tone, recent=recent)
            if score > 0.0:
                candidates.append((preset, score))
        if not candidates:
            return None

        # Cấm lặp ba preset gần nhất nếu vẫn còn lựa chọn khác.
        blocked = set(recent[:3])
        fresh = [(preset, score) for preset, score in candidates if preset.id not in blocked]
        if fresh:
            candidates = fresh
        total = sum(score for _, score in candidates)
        pick = self.rng.random() * total
        cursor = 0.0
        for preset, score in candidates:
            cursor += score
            if pick <= cursor:
                return preset
        return candidates[-1][0]

    def resolve_or_default(self, frame: GreetingFrame, *, tone: str = "", force: bool = False) -> GreetingPreset | None:
        """Chọn preset, rồi nới điều kiện mềm nếu catalog không có điểm dương.

        Situation và tone vẫn được tôn trọng trước. Warmth/dry_edge chỉ là bias,
        không được phép làm toàn bộ 32 câu biến mất rồi gọi lại câu chào cũ.
        """
        selected = self.resolve(frame, tone=tone, force=force)
        if selected is not None or frame is None:
            return selected

        normalized_tone = str(tone or "quiet_background").strip().lower()
        recent = self._read_history()
        relaxed = [
            preset for preset in self.presets
            if frame.situation in preset.situations
            and normalized_tone in preset.tones
            and self.anchor_gate.validate(preset.text, frame).accepted
        ]
        if not relaxed:
            relaxed = [
                preset for preset in self.presets
                if frame.situation in preset.situations
                and self.anchor_gate.validate(preset.text, frame).accepted
            ]
        if not relaxed:
            relaxed = [
                preset for preset in self.presets
                if "plain" in preset.tags
                and self.anchor_gate.validate(preset.text, frame).accepted
            ]
        if not relaxed:
            return None

        blocked = set(recent[:3])
        fresh = [preset for preset in relaxed if preset.id not in blocked]
        pool = fresh or relaxed
        return pool[int(self.rng.random() * len(pool)) % len(pool)]

    def mark_used(self, preset_id: str) -> None:
        preset_id = str(preset_id or "").strip()
        if not preset_id:
            return
        history = [item for item in self._read_history() if item != preset_id]
        history.insert(0, preset_id)
        try:
            self.history_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.history_path.with_suffix(".tmp")
            tmp.write_text(json.dumps({"recent": history[:8]}, ensure_ascii=False, indent=2), encoding="utf-8")
            os.replace(tmp, self.history_path)
        except Exception:
            # Lịch sử chống lặp là tối ưu mềm, không được làm hỏng startup.
            return

    def _score(self, preset: GreetingPreset, *, frame: GreetingFrame, tone: str, recent: list[str]) -> float:
        if frame.situation not in preset.situations:
            return 0.0
        if tone not in preset.tones:
            return 0.0
        if not (preset.min_warmth <= frame.warmth <= preset.max_warmth):
            return 0.0
        if not (preset.min_dry_edge <= frame.dry_edge <= preset.max_dry_edge):
            return 0.0

        tags = set(preset.tags)
        quick_context = bool(getattr(frame, "recent_return", False)) and frame.situation in {
            "plain_return",
            "low_evidence_start",
        }
        if "quick_reopen" in tags and not quick_context:
            return 0.0
        if quick_context and "quick_reopen" not in tags:
            return 0.0
        if "overloaded" in tags and tone != "overloaded":
            return 0.0
        if tone == "overloaded" and "overloaded" not in tags:
            return 0.0

        score = max(0.01, float(preset.weight))
        if tone == "socially_warm" and "warm" in tags:
            score *= 1.35
        if frame.dry_edge >= 0.28 and "dry" in tags:
            score *= 1.25

        # Dùng thời gian để chọn sắc thái, không tự thay chữ hoặc bịa hoạt động.
        # Cùng một catalog 32 câu nhưng buổi tối thiên về gần/êm, buổi sáng thiên
        # về sẵn sàng, còn đêm khuya tránh câu quá thách thức nếu còn lựa chọn.
        daypart = str(getattr(frame, "time_of_day", "") or "").strip().lower()
        preferred_tags = _DAYPART_TAG_BIAS.get(daypart, frozenset())
        if tags & preferred_tags:
            score *= 1.22
        if daypart == "late_night" and tags & {"challenge", "playful"}:
            score *= 0.82
        if daypart == "morning" and "ready" in tags:
            score *= 1.12
        if bool(getattr(frame, "is_weekend", False)) and tags & {"warm", "playful", "vtuber"}:
            score *= 1.08

        if preset.id in recent:
            score *= 0.18
        return score

    def _read_history(self) -> list[str]:
        try:
            payload = json.loads(self.history_path.read_text(encoding="utf-8"))
            values = payload.get("recent", []) if isinstance(payload, dict) else []
            return [str(item) for item in values if str(item).strip()][:8]
        except Exception:
            return []


def export_manifest(path: str | Path) -> Path:
    """Ghi manifest để tool render TTS theo lô hoặc frontend đọc metadata."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(
            {
                "version": 1,
                "count": len(GREETING_PRESETS),
                "description_vi": "32 lời chào Eris theo tone/situation. File WAV có thể render sau; thiếu file thì runtime tự fallback sang TTS live.",
                "presets": [item.to_dict() for item in GREETING_PRESETS],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return target


__all__ = [
    "GREETING_PRESETS",
    "GreetingPreset",
    "GreetingPresetResolver",
    "export_manifest",
    "greeting_preset_speech_text",
]
