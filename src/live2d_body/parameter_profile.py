# -*- coding: utf-8 -*-
"""Audited INFJ native parameters: head axes +/-30, mouth opening 0..1."""

from __future__ import annotations

from dataclasses import dataclass


def _clamp(value: float, minimum: float, maximum: float) -> float:
    return max(float(minimum), min(float(maximum), float(value)))


@dataclass(frozen=True, slots=True)
class InfjParameterProfile:
    """Miền giá trị thực tế của model, mô tả theo phía người đang nhìn model."""

    # Người dùng nhìn: âm = đầu sang trái, dương = đầu sang phải.
    angle_x_left: float = -30.0
    angle_x_center: float = 0.0
    angle_x_right: float = 30.0
    angle_z_min: float = -30.0
    angle_z_max: float = 30.0

    # Mắt mở/đóng theo chuẩn Cubism.
    eye_closed: float = 0.0
    eye_open: float = 1.0

    # Mỗi mắt có trục X riêng: -1 vào phía sống mũi, +1 ra phía thái dương.
    eye_toward_nose: float = -1.0
    eye_outward: float = 1.0
    eye_down: float = -1.0
    eye_up: float = 1.0

    # Lông mày và mood miệng.
    brow_low: float = -1.0
    brow_neutral: float = 0.0
    brow_raised: float = 1.0
    mouth_bad: float = -1.0
    mouth_neutral: float = 0.0
    mouth_happy: float = 1.0

    # Native mouth: 0 closed, .5 half open, 1 fully open.
    mouth_closed: float = 0.0
    mouth_half: float = 0.5
    mouth_open: float = 1.0

    def clamp_angle_x(self, value: float) -> float:
        return _clamp(value, self.angle_x_left, self.angle_x_right)

    def clamp_angle_z(self, value: float) -> float:
        return _clamp(value, self.angle_z_min, self.angle_z_max)

    def clamp_eye_open(self, value: float) -> float:
        return _clamp(value, self.eye_closed, self.eye_open)

    def clamp_eye_axis(self, value: float) -> float:
        return _clamp(value, -1.0, 1.0)

    def clamp_brow(self, value: float) -> float:
        return _clamp(value, self.brow_low, self.brow_raised)

    def clamp_mouth_form(self, value: float) -> float:
        return _clamp(value, self.mouth_bad, self.mouth_happy)

    def clamp_mouth_open(self, value: float) -> float:
        return _clamp(value, self.mouth_closed, self.mouth_open)

    def mouth_open_from_ratio(self, ratio: float) -> float:
        """Map normalized opening into the native 0..1 mouth domain."""
        normalized = _clamp(ratio, 0.0, 1.0)
        return self.mouth_closed + (self.mouth_open - self.mouth_closed) * normalized

    def mouth_open_from_speech_ratio(self, ratio: float) -> float:
        """Open continuously from silence to the rig's .84 wide vowel pose."""
        voiced = _clamp((ratio - 0.025) / (0.86 - 0.025), 0.0, 1.0)
        return self.mouth_open_from_ratio(0.84 * voiced ** 0.77)

    def tongue_from_jaw(self, opening: float) -> float:
        """V13.2 interior tongue keyforms follow the final native jaw opening."""
        progress = _clamp((opening - .11) / (.70 - .11), 0.0, 1.0)
        return progress * progress * (3.0 - 2.0 * progress)

    def compose_speaking_mouth(
        self,
        *,
        mood_form: float,
        speech_ratio: float,
    ) -> tuple[float, float]:
        """Ghép mood miệng và độ há thành một pose hai trục.

        ``ParamMouthForm`` luôn giữ nghĩa mood ``-1..1``. Envelope voice chỉ
        điều khiển ``ParamMouthOpenY``; mood vui nới độ há rất nhẹ, mood xấu
        siết rất nhẹ để hai trục chuyển động cùng nhau mà không làm sai nhịp
        audio. Biên 8% là cố ý nhỏ vì phần còn lại đã nằm trong keyform của rig.
        """

        form = self.clamp_mouth_form(mood_form)
        ratio = _clamp(speech_ratio, 0.0, 1.0)
        coupled_ratio = _clamp(ratio * (1.0 + form * 0.08), 0.0, 1.0)
        return form, self.mouth_open_from_speech_ratio(coupled_ratio)

    def sanitize(self, parameter_id: str, value: float) -> float:
        """Clamp theo semantics trước khi giao cho Cubism Core."""
        if parameter_id == "ParamAngleX":
            return self.clamp_angle_x(value)
        if parameter_id == "ParamAngleZ":
            return self.clamp_angle_z(value)
        if parameter_id in {"ParamEyeLOpen", "ParamEyeROpen"}:
            return self.clamp_eye_open(value)
        if parameter_id in {"eyeballxl", "eyeballxr", "eyebally", "eyeballyr", "ParamEyeBallX", "ParamEyeBallY"}:
            return self.clamp_eye_axis(value)
        if parameter_id in {"ParamBrowLAngle", "ParamBrowRAngle"}:
            return self.clamp_brow(value)
        if parameter_id in {"ParamErisMouthCornerL", "ParamErisMouthCornerR"}:
            return _clamp(value, -1.0, 1.0)
        if parameter_id in {"ParamErisLipPress", "ParamErisTongue", "ParamCheek", "ParamBreath", "ParamEyeHighlight"}:
            return _clamp(value, 0.0, 1.0)
        if parameter_id == "ParamMouthForm":
            return self.clamp_mouth_form(value)
        if parameter_id == "ParamMouthOpenY":
            return self.clamp_mouth_open(value)
        return float(value)


INFJ_PARAMETER_PROFILE = InfjParameterProfile()
