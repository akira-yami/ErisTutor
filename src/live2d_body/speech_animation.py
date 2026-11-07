# -*- coding: utf-8 -*-
"""Nhịp nói và diễn xuất bề mặt cho Live2D Eris.

Trong playback, độ mở và dáng miệng bám các đường tín hiệu từ WAV đang phát.
Thiếu envelope hoặc gặp khoảng im lặng thì miệng khép. Text chỉ cấp context
diễn xuất.

Cùng context sân khấu (prosody, speech act, action, motion) còn được chiếu sang
đầu, mí, mày và hướng nhìn. Biên cố ý nhỏ; emotion/affect vẫn là lớp chính.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Iterable, Mapping


def _clamp(value: float, minimum: float, maximum: float) -> float:
    return max(float(minimum), min(float(maximum), float(value)))


def _smoothstep(value: float) -> float:
    x = _clamp(value, 0.0, 1.0)
    return x * x * (3.0 - 2.0 * x)


def _pulse(now: float, start: float, duration: float, *, power: float = 1.35) -> float:
    if duration <= 0.0 or now < start or now > start + duration:
        return 0.0
    progress = (now - start) / duration
    return math.sin(math.pi * progress) ** max(0.2, float(power))


def _unique_strings(values: Iterable[Any]) -> tuple[str, ...]:
    result: list[str] = []
    seen: set[str] = set()
    for raw in values:
        value = str(raw or "").strip().lower()
        if not value or value in seen:
            continue
        seen.add(value)
        result.append(value)
    return tuple(result)


def _read_audio_curve(raw: Any, maximum: float) -> tuple[float, ...]:
    if not isinstance(raw, (list, tuple)):
        return ()
    values = []
    for item in raw[:2400]:
        try:
            value = float(item)
        except (TypeError, ValueError, OverflowError):
            value = 0.0
        # Keep each sample's time slot even if a sender gave us a bad value.
        values.append(_clamp(value, 0.0, maximum) if math.isfinite(value) else 0.0)
    return tuple(values)


@dataclass(frozen=True, slots=True)
class SpeechContext:
    text: str = ""
    prosody_style: str = "neutral"
    speech_act: str = ""
    actions: tuple[str, ...] = ()
    motion: str = ""
    delivery: Mapping[str, Any] | None = None
    audio_envelope: tuple[float, ...] = ()
    audio_frame_ms: int = 0
    audio_duration_ms: int = 0
    audio_mouth_shape: tuple[float, ...] = ()

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> "SpeechContext":
        data = payload.get("data") if isinstance(payload.get("data"), Mapping) else {}
        assert isinstance(data, Mapping)
        raw_delivery = data.get("delivery")
        delivery = dict(raw_delivery) if isinstance(raw_delivery, Mapping) else {}
        performance_act = delivery.get("performance_act") if isinstance(delivery, Mapping) else None
        performance_kind = (
            str(performance_act.get("kind", "") or "").strip().lower()
            if isinstance(performance_act, Mapping)
            else ""
        )
        actions = list(data.get("actions") or ()) if isinstance(data.get("actions"), (list, tuple)) else []
        if performance_kind:
            actions.append(performance_kind)
        envelope = _read_audio_curve(data.get("audio_envelope"), .90)
        shape = _read_audio_curve(data.get("audio_mouth_shape"), 1.0)
        try:
            audio_frame_ms = max(0, int(data.get("audio_frame_ms", 0) or 0))
        except (TypeError, ValueError, OverflowError):
            audio_frame_ms = 0
        try:
            audio_duration_ms = max(0, int(data.get("audio_duration_ms", 0) or 0))
        except (TypeError, ValueError, OverflowError):
            audio_duration_ms = 0
        return cls(
            text=str(payload.get("tts_text", payload.get("display_text", "")) or ""),
            prosody_style=str(data.get("prosody_style", payload.get("prosody_style", "neutral")) or "neutral").strip().lower(),
            speech_act=str(data.get("speech_act", payload.get("speech_act", "")) or "").strip().lower(),
            actions=_unique_strings(actions),
            motion=str(payload.get("motion", "") or "").strip().lower(),
            delivery=delivery,
            audio_envelope=envelope,
            audio_frame_ms=audio_frame_ms,
            audio_duration_ms=audio_duration_ms,
            audio_mouth_shape=shape if len(shape) == len(envelope) else (),
        )

    def merged(self, other: "SpeechContext") -> "SpeechContext":
        """Ưu tiên field có dữ liệu ở context mới, giữ cache cho event audio mỏng."""

        return SpeechContext(
            text=other.text or self.text,
            prosody_style=(
                other.prosody_style
                if other.prosody_style and other.prosody_style != "neutral"
                else self.prosody_style or "neutral"
            ),
            speech_act=other.speech_act or self.speech_act,
            actions=_unique_strings((*self.actions, *other.actions)),
            motion=other.motion or self.motion,
            delivery=other.delivery or self.delivery,
            # Surface cues can be reused across events, playback PCM cannot.
            audio_envelope=other.audio_envelope,
            audio_frame_ms=other.audio_frame_ms,
            audio_duration_ms=other.audio_duration_ms,
            audio_mouth_shape=other.audio_mouth_shape,
        )


@dataclass(frozen=True, slots=True)
class SpeechPose:
    mouth_open_ratio: float = 0.0
    mouth_shape: float = 0.0
    mouth_form_scale: float = 1.0
    mouth_form_offset: float = 0.0
    brow_left: float = 0.0
    brow_right: float = 0.0
    angle_x: float = 0.0
    angle_z: float = 0.0
    eye_open_left_scale: float = 1.0
    eye_open_right_scale: float = 1.0
    gaze_bias_x: float = 0.0
    gaze_bias_y: float = 0.0
    cue: str = "silent"


class SpeechAnimator:
    """Sinh mouth pose và vi diễn xuất theo context của đoạn đang phát."""

    def __init__(self) -> None:
        self._active = False
        self._started_at = 0.0
        self._context = SpeechContext()
        self._phase = 0.0
        self._audio_envelope: tuple[float, ...] = ()
        self._audio_frame_ms = 0
        self._audio_duration_ms = 0

    @property
    def active(self) -> bool:
        return self._active

    @property
    def context(self) -> SpeechContext:
        return self._context

    def start(self, *, now: float, context: SpeechContext, audio_playback: bool = False) -> None:
        self._active = True
        self._started_at = float(now)
        self._context = context
        raw_envelope = tuple(context.audio_envelope or ())
        self._audio_frame_ms = max(0, int(context.audio_frame_ms or 0))
        self._audio_duration_ms = max(0, int(context.audio_duration_ms or 0))
        # RMS từ vài backend có biên rất thấp dù audio nghe rõ. Chuẩn hóa chỉ khi
        # peak yếu, giữ nguyên số 0 để khoảng im vẫn khép miệng thật và không biến
        # Eris thành cá cảnh đang thở theo nhiễu nền.
        peak = max(raw_envelope, default=0.0)
        # Silence and sparse speech are real audio data, not a reason to invent
        # text-driven movement. A short envelope closes naturally at its end.
        if not raw_envelope or self._audio_frame_ms <= 0:
            self._audio_envelope = ()
        elif 0.0 < peak < 0.55:
            gain = min(8.0, 0.72 / peak)
            self._audio_envelope = tuple(
                0.0 if value <= 0.003 else _clamp(value * gain, 0.0, 0.90)
                for value in raw_envelope
            )
        else:
            self._audio_envelope = raw_envelope
        # Phase ổn định theo text, tránh mọi câu cùng gật ở đúng một nhịp.
        checksum = sum((index + 1) * ord(char) for index, char in enumerate(context.text[:160]))
        self._phase = (checksum % 997) / 997.0 * math.tau

    def stop(self) -> None:
        self._active = False

    def sample(self, *, now: float) -> SpeechPose:
        if not self._active:
            return SpeechPose()

        elapsed = max(0.0, float(now) - self._started_at)
        mouth_ratio = self._sample_audio_curve(self._audio_envelope, elapsed)
        shape_curve = self._context.audio_mouth_shape
        mouth_shape = (
            self._sample_audio_curve(shape_curve, elapsed)
            if mouth_ratio > 0.025 and len(shape_curve) == len(self._audio_envelope)
            else 0.0
        )
        context = self._context
        tags = set(context.actions)
        if context.motion:
            tags.add(context.motion)
        style = context.prosody_style or "neutral"
        text = context.text.rstrip()
        is_question = (
            "?" in text[-4:]
            or context.speech_act in {"question", "ask", "clarify", "curious"}
            or "curious_hm" in tags
        )
        is_exclamation = "!" in text[-4:]

        # Chuyển động nền khi nói phải nhìn thấy được trên model 520x900. Bản cũ
        # chỉ dao động 0.1 độ, về toán học là có tồn tại nhưng về thị giác gần như
        # một lời đồn. Vẫn giữ biên nhỏ để không biến hội thoại thành lắc đầu liên tục.
        entry = _smoothstep(elapsed / 0.22)
        living = math.sin(elapsed * 1.55 + self._phase)
        phrase_beat = math.sin(elapsed * 3.15 + self._phase * 0.37) * mouth_ratio
        angle_x = (living * 4.2 + phrase_beat * 1.8) * entry
        angle_z = (
            math.sin(elapsed * 1.13 + self._phase * 0.63) * 3.0
            + math.sin(elapsed * 2.35 + self._phase) * mouth_ratio * 1.0
        ) * entry
        # Năng lượng âm tiết làm mày/đầu nhúc nhích rất nhẹ; không thay emotion,
        # chỉ giúp khuôn mặt không rơi về hai trạng thái thở và chớp mắt.
        syllable_energy = _smoothstep(mouth_ratio)
        brow_left = syllable_energy * 0.025
        brow_right = syllable_energy * 0.018
        mouth_form_offset = 0.0
        eye_left = 1.0
        eye_right = 1.0
        gaze_x = 0.0
        gaze_y = 0.0
        cue_names: list[str] = []

        if style in {"warm_soft", "soft_tease", "restrained_warmth"}:
            mouth_form_offset += 0.035 if style == "warm_soft" else 0.055
            eye_left *= 0.975
            eye_right *= 0.982
            cue_names.append("warm")
        elif style in {"dry_tease", "self_aware_dry", "deadpan", "sharp_deadpan"}:
            brow_left += 0.080 if style == "dry_tease" else 0.025
            brow_right -= 0.030
            eye_left *= 0.94
            eye_right *= 0.965
            gaze_x -= 0.018
            mouth_form_offset += 0.018 if style == "dry_tease" else -0.015
            cue_names.append("dry")
        elif style in {"annoyed_soft", "cold_boundary", "cold_precision"}:
            brow_left -= 0.075
            brow_right -= 0.070
            eye_left *= 0.91
            eye_right *= 0.92
            mouth_form_offset -= 0.055
            angle_x -= 0.18
            cue_names.append("firm")
        elif style in {"low_quiet", "gentle_low"}:
            brow_left -= 0.025
            brow_right -= 0.020
            eye_left *= 0.92
            eye_right *= 0.93
            gaze_y -= 0.018
            angle_x -= 0.16
            cue_names.append("quiet")
        elif style == "curious_soft":
            brow_left += 0.090
            brow_right += 0.025
            eye_left *= 0.99
            angle_z += 0.24
            gaze_y += 0.016
            cue_names.append("curious")
        elif style in {"focused", "low_direct"}:
            brow_left -= 0.035
            brow_right -= 0.035
            eye_left *= 0.965
            eye_right *= 0.965
            cue_names.append("focused")
        elif style == "surprised_soft":
            brow_left += .12
            brow_right += .10
            eye_left *= 1.03
            eye_right *= 1.03
            angle_x -= .8
            cue_names.append("surprised")

        if is_question:
            brow_left += 0.105
            brow_right += 0.030
            angle_z += 0.32
            gaze_y += 0.022
            cue_names.append("question")
        elif is_exclamation:
            brow_left += 0.055
            brow_right += 0.050
            eye_left *= 1.02
            eye_right *= 1.02
            cue_names.append("emphasis")

        if tags & {"small_smile", "laugh_soft", "audible_laugh_soft", "playful_laugh"}:
            mouth_form_offset += 0.075
            eye_left *= 0.955
            eye_right *= 0.970
            cue_names.append("smile")
        if tags & {"look_away", "look_aside", "scoff_soft", "requested_tease"}:
            gaze_x -= 0.085
            angle_x -= 0.38
            brow_left += 0.035
            cue_names.append("look_away")
        if tags & {"look_down", "sigh", "sigh_soft", "soft_sigh"}:
            gaze_y -= 0.075
            angle_x -= 0.48
            eye_left *= 0.91
            eye_right *= 0.92
            mouth_form_offset -= 0.025
            cue_names.append("look_down")
        if tags & {"tilt_head", "curious_hm"}:
            angle_z += 0.58
            brow_left += 0.075
            brow_right -= 0.018
            gaze_y += 0.020
            cue_names.append("tilt")
        if tags & {"lean_in_small", "soft_near"}:
            angle_x += 0.34
            eye_left *= 0.975
            eye_right *= 0.980
            cue_names.append("near")
        if "lean_back_small" in tags:
            angle_x -= 1.4
            cue_names.append("back")
        if tags & {"focus", "still"}:
            angle_x *= .15
            angle_z *= .15
            eye_left *= .97
            eye_right *= .97
            cue_names.append("steady")
        if tags & {"squint_soft", "scoff_soft"}:
            eye_left *= 0.88
            eye_right *= 0.90
            cue_names.append("squint")

        # Action một lần ở đầu câu, không giữ mắt đóng suốt cả đoạn. Con người đã
        # phát minh ra wink, không phải chế độ tiết kiệm điện cho một bên mắt.
        if "wink" in tags:
            wink = _pulse(elapsed, 0.22, 0.42, power=1.0)
            eye_left *= 1.0 - wink * 0.88
            brow_left += wink * 0.12
            cue_names.append("wink")
        if tags & {"blink_slow", "soft_breath"}:
            blink = _pulse(elapsed, 0.18, 0.62, power=0.72)
            eye_left *= 1.0 - blink * 0.56
            eye_right *= 1.0 - blink * 0.54
            cue_names.append("slow_blink")
        if "nod_small" in tags:
            nod = _pulse(elapsed, 0.12, 0.72, power=1.0)
            angle_x += math.sin(elapsed * math.pi / 0.36) * 2.2 * nod
            cue_names.append("nod")
        if "shake_head_small" in tags:
            shake = _pulse(elapsed, 0.12, 0.86, power=0.9)
            angle_z += math.sin(elapsed * math.pi / 0.19) * 2.4 * shake
            cue_names.append("shake")

        return SpeechPose(
            mouth_open_ratio=mouth_ratio,
            mouth_shape=mouth_shape,
            mouth_form_scale=1.0,
            mouth_form_offset=_clamp(mouth_form_offset, -0.16, 0.16),
            brow_left=_clamp(brow_left, -0.20, 0.28),
            brow_right=_clamp(brow_right, -0.20, 0.24),
            angle_x=_clamp(angle_x, -6.0, 6.0),
            angle_z=_clamp(angle_z, -6.0, 6.0),
            eye_open_left_scale=_clamp(eye_left, 0.08, 1.04),
            eye_open_right_scale=_clamp(eye_right, 0.08, 1.04),
            gaze_bias_x=_clamp(gaze_x, -0.13, 0.13),
            gaze_bias_y=_clamp(gaze_y, -0.11, 0.11),
            cue="+".join(cue_names) or "speech",
        )

    def _sample_audio_curve(self, values: tuple[float, ...], elapsed: float) -> float:
        if not values or self._audio_frame_ms <= 0:
            return 0.0
        if self._audio_duration_ms > 0 and elapsed >= self._audio_duration_ms / 1000.0:
            return 0.0
        position = elapsed * 1000.0 / self._audio_frame_ms
        index = int(position)
        if index >= len(values):
            return 0.0
        current = values[index]
        following = values[index + 1] if index + 1 < len(values) else current
        value = _clamp(current + (following - current) * (position - index), 0.0, 1.0)
        return 0.0 if value < 1.0e-5 else value


__all__ = ["SpeechAnimator", "SpeechContext", "SpeechPose"]
