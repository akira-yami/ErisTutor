# -*- coding: utf-8 -*-
"""Cầu cảm xúc liên tục cho Live2D.

Runtime ERIS đã có ``PersistentInternalState`` với valence/arousal/curiosity...
Module này chỉ chiếu snapshot đó thành một pose mặt nhỏ, không đọc raw chain of
thought và không tự suy diễn từ văn bản nhật ký. Nhờ vậy nội tâm có dư âm trên
model mà mỗi câu nghĩ thầm không biến thành một màn kịch kabuki.
"""

from __future__ import annotations

from dataclasses import dataclass, fields
from typing import Any, Mapping


def _clamp(value: Any, minimum: float = 0.0, maximum: float = 1.0) -> float:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        numeric = minimum
    if numeric != numeric:
        numeric = minimum
    return max(minimum, min(maximum, numeric))


def _signed(value: Any) -> float:
    return _clamp(value, -1.0, 1.0)


@dataclass(frozen=True, slots=True)
class AffectPose:
    mouth_corner_l: float = 0.0
    mouth_corner_r: float = 0.0
    lip_press: float = 0.0
    tongue: float = 0.0
    mouth_form: float = 0.0
    brow_left: float = 0.0
    brow_right: float = 0.0
    angle_x: float = 0.0
    angle_z: float = 0.0
    eye_open_left: float = 1.0
    eye_open_right: float = 1.0
    gaze_bias_x: float = 0.0
    gaze_bias_y: float = 0.0
    cheek: float = 0.0


@dataclass(frozen=True, slots=True)
class AffectVector:
    """Vector affect công khai cho renderer, không chứa nội dung suy nghĩ."""

    valence: float = 0.0
    arousal: float = 0.18
    curiosity: float = 0.30
    boredom: float = 0.06
    social_energy: float = 0.32
    attachment: float = 0.26
    irritation: float = 0.08
    protectiveness: float = 0.14
    confidence: float = 0.44
    fatigue: float = 0.12
    tenderness: float = 0.16
    cognitive_tension: float = 0.06

    @classmethod
    def from_mapping(
        cls,
        value: Mapping[str, Any] | None,
        *,
        fallback: "AffectVector | None" = None,
    ) -> "AffectVector":
        base = fallback or cls()
        source = value if isinstance(value, Mapping) else {}
        kwargs: dict[str, float] = {}
        for descriptor in fields(cls):
            name = descriptor.name
            raw = source.get(name, getattr(base, name))
            kwargs[name] = _signed(raw) if name == "valence" else _clamp(raw)
        return cls(**kwargs)

    def lerp(self, other: "AffectVector", weight: float) -> "AffectVector":
        ratio = _clamp(weight)
        return self.__class__(
            **{
                descriptor.name: getattr(self, descriptor.name)
                + (getattr(other, descriptor.name) - getattr(self, descriptor.name)) * ratio
                for descriptor in fields(self)
            }
        )

    def to_dict(self) -> dict[str, float]:
        return {
            descriptor.name: round(float(getattr(self, descriptor.name)), 4)
            for descriptor in fields(self)
        }

    def visual_pose(self, *, thought_pressure: float = 0.0) -> AffectPose:
        """Map state liên tục sang biểu cảm nền có biên cố ý nhỏ.

        Pose này dành cho idle và làm lớp nền dưới emotion của lời thoại. Các hệ
        số không cố tái tạo tâm lý học; chúng chỉ giữ quan hệ trực quan ổn định:
        valence ấm khóe miệng, irritation siết mày, fatigue hạ mí, curiosity tạo
        bất đối xứng rất nhẹ.
        """

        positive = max(0.0, self.valence)
        negative = max(0.0, -self.valence)
        pressure = _clamp(thought_pressure)
        warmth = _clamp(
            self.tenderness * 0.46
            + self.attachment * 0.22
            + positive * 0.32
        )
        tension = _clamp(
            max(
                self.cognitive_tension,
                self.irritation * 0.72,
                pressure * 0.68,
            )
        )

        mouth_form = (
            positive * 0.20
            + warmth * 0.10
            - negative * 0.22
            - self.irritation * 0.27
            - self.fatigue * 0.045
        )

        brow_base = (
            self.arousal * 0.055
            + self.curiosity * 0.045
            - self.irritation * 0.20
            - negative * 0.11
            - tension * 0.055
        )
        brow_asymmetry = self.curiosity * (0.055 + self.confidence * 0.055)
        brow_asymmetry += pressure * 0.035

        angle_x = (
            -self.fatigue * 0.95
            - self.boredom * 0.70
            - tension * 0.70
            + positive * 0.28
        )
        angle_z = (
            self.curiosity * 1.15
            + warmth * 0.55
            - self.irritation * 0.68
            - negative * 0.32
            + pressure * 0.55
        )

        eye_base = (
            1.0
            - self.fatigue * 0.13
            - self.boredom * 0.075
            - self.irritation * 0.075
            - tension * 0.045
            + self.arousal * 0.025
        )
        eye_asymmetry = min(0.025, self.curiosity * 0.018 + pressure * 0.012)

        corner = mouth_form * 0.65
        asymmetry = self.curiosity * self.confidence * 0.035
        subtle_cheek = _clamp((warmth - 0.28) * 0.38, 0.0, 0.22)
        return AffectPose(
            mouth_corner_l=_clamp(corner + asymmetry, -0.25, 0.25),
            mouth_corner_r=_clamp(corner - asymmetry, -0.25, 0.25),
            lip_press=_clamp(tension * 0.22 + self.irritation * 0.12, 0.0, 0.34),
            mouth_form=_clamp(mouth_form, -0.34, 0.30),
            brow_left=_clamp(brow_base + brow_asymmetry, -0.34, 0.34),
            brow_right=_clamp(brow_base - brow_asymmetry * 0.48, -0.34, 0.34),
            angle_x=_clamp(angle_x, -2.6, 1.1),
            angle_z=_clamp(angle_z, -2.2, 3.0),
            eye_open_left=_clamp(eye_base - eye_asymmetry, 0.76, 1.0),
            eye_open_right=_clamp(eye_base + eye_asymmetry * 0.45, 0.76, 1.0),
            gaze_bias_y=_clamp(self.curiosity * 0.018 - self.fatigue * 0.020, -0.035, 0.035),
            cheek=subtle_cheek,
        )

    def dominant_label(self) -> str:
        """Nhãn debug/tiêu đề cửa sổ, không dùng để quyết định cognition."""

        if self.irritation >= 0.48 and self.irritation >= self.tenderness:
            return "annoyed_soft"
        if self.fatigue >= 0.62 and self.valence <= 0.12:
            return "quiet_low"
        if self.valence <= -0.34:
            return "distant" if self.boredom >= 0.34 else "quiet_low"
        if self.curiosity >= 0.62 and self.curiosity >= self.tenderness:
            return "curious"
        if self.tenderness >= 0.48 or (self.valence >= 0.28 and self.attachment >= 0.34):
            return "warm_soft"
        if self.cognitive_tension >= 0.48:
            return "focused"
        return "neutral"


NEUTRAL_AFFECT = AffectVector()


__all__ = ["AffectPose", "AffectVector", "NEUTRAL_AFFECT"]
