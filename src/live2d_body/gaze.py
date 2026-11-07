# -*- coding: utf-8 -*-
"""Eye tracking mượt cho model có parameter mắt tách trái/phải.

Hai trục X của mắt INFJ không phải một trục màn hình chung. Với từng mắt,
``-1`` là nhìn vào sống mũi và ``+1`` là nhìn ra ngoài. Vì vậy cùng một hướng
nhìn phải được biến đổi thành hai giá trị trái dấu, đồng thời mắt nhìn vào trong
được giới hạn nhẹ hơn để tránh cảm giác lé khi con trỏ ở xa.
"""

from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class GazeSample:
    left_x: float
    right_x: float
    left_y: float
    right_y: float
    head_x_degrees: float
    left_eye_open_scale: float = 1.0
    right_eye_open_scale: float = 1.0


def _soft_axis(value: float, *, dead_zone: float = 0.075) -> float:
    """Chuẩn hóa một trục về -1..1 với dead-zone và soft saturation."""
    value = max(-3.0, min(3.0, float(value)))
    magnitude = abs(value)
    if magnitude <= dead_zone:
        return 0.0
    normalized = (magnitude - dead_zone) / max(1e-6, 1.0 - dead_zone)
    # Tanh giữ chuyển động có lực ở giữa nhưng không đóng đinh con ngươi ở mép.
    return math.copysign(math.tanh(normalized * 1.18), value)


class GazeController:
    def __init__(
        self,
        *,
        strength_x: float = 0.30,
        strength_y: float = 0.18,
        right_x_sign: float = -1.0,
        inner_eye_ratio: float = 0.78,
        head_follow_degrees: float = 2.4,
        head_follow_left_degrees: float | None = None,
        head_follow_right_degrees: float | None = None,
        response_x: float = 7.5,
        response_y: float = 6.5,
    ) -> None:
        self.strength_x = max(0.0, min(0.65, float(strength_x)))
        self.strength_y = max(0.0, min(0.65, float(strength_y)))
        self.right_x_sign = -1.0 if float(right_x_sign) < 0.0 else 1.0
        self.inner_eye_ratio = max(0.55, min(1.0, float(inner_eye_ratio)))

        legacy = max(0.0, min(8.0, float(head_follow_degrees)))
        self.head_follow_left_degrees = max(
            0.0,
            min(12.0, float(head_follow_left_degrees if head_follow_left_degrees is not None else legacy * 2.0)),
        )
        self.head_follow_right_degrees = max(
            0.0,
            min(12.0, float(head_follow_right_degrees if head_follow_right_degrees is not None else legacy)),
        )
        self.response_x = max(1.0, min(30.0, float(response_x)))
        self.response_y = max(1.0, min(30.0, float(response_y)))
        self._x = 0.0
        self._y = 0.0

    def update(
        self,
        *,
        cursor_screen: tuple[float, float] | None,
        face_screen: tuple[float, float],
        span_x: float,
        span_y: float,
        dt: float,
    ) -> GazeSample:
        if cursor_screen is None:
            target_x = 0.0
            target_y = 0.0
        else:
            target_x = _soft_axis((cursor_screen[0] - face_screen[0]) / max(1.0, span_x))
            # EyeBallY dương là nhìn lên.
            target_y = _soft_axis((face_screen[1] - cursor_screen[1]) / max(1.0, span_y))

        dt = max(0.0, min(0.1, float(dt)))
        blend_x = 1.0 - math.exp(-self.response_x * dt) if dt > 0.0 else 1.0
        blend_y = 1.0 - math.exp(-self.response_y * dt) if dt > 0.0 else 1.0
        self._x += (target_x - self._x) * blend_x
        self._y += (target_y - self._y) * blend_y

        outer_strength = self.strength_x
        inner_strength = self.strength_x * self.inner_eye_ratio
        if self._x >= 0.0:
            # Nhìn sang phải của người dùng: mắt L ra ngoài, mắt R vào mũi.
            left_x = self._x * outer_strength
            right_x = self._x * inner_strength * self.right_x_sign
        else:
            # Nhìn sang trái của người dùng: mắt L vào mũi, mắt R ra ngoài.
            left_x = self._x * inner_strength
            right_x = self._x * outer_strength * self.right_x_sign

        vertical = self._y * self.strength_y
        if self._x < 0.0:
            head_x = self._x * self.head_follow_left_degrees
        else:
            head_x = self._x * self.head_follow_right_degrees

        # Liếc xa và nhìn xuống làm mí khép rất nhẹ. Renderer nhân hệ số này
        # với giá trị auto-blink hiện tại, nên chớp mắt vẫn đóng được hoàn toàn.
        horizontal_squint = 0.07 * abs(self._x)
        downward_squint = 0.10 * max(0.0, -self._y)
        eye_open_scale = max(0.78, 1.0 - horizontal_squint - downward_squint)

        return GazeSample(
            left_x=left_x,
            right_x=right_x,
            left_y=vertical,
            right_y=vertical,
            head_x_degrees=head_x,
            left_eye_open_scale=eye_open_scale,
            right_eye_open_scale=eye_open_scale,
        )
