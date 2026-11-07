# -*- coding: utf-8 -*-
"""Idle director có nhịp cho Eris Live2D.

Không dùng random mỗi frame. Một gesture được lên lịch, ease vào rồi ease ra;
giữa các gesture chỉ còn vi chuyển động rất nhỏ. Cách này tránh cả hai cực đoan:
đứng như ảnh thẻ và rung như model vừa uống ba lon nước tăng lực.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass

from .affect import AffectVector


@dataclass(frozen=True, slots=True)
class IdlePose:
    angle_x: float = 0.0
    angle_z: float = 0.0
    brow_left: float = 0.0
    brow_right: float = 0.0
    mouth_form: float = 0.0
    eye_open_left_scale: float = 1.0
    eye_open_right_scale: float = 1.0
    gaze_bias_x: float = 0.0
    gaze_bias_y: float = 0.0
    action: str = "settle"


_ZERO = IdlePose()


class IdleDirector:
    def __init__(self, *, seed: int | None = None) -> None:
        self._rng = random.Random(seed)
        self._next_at = 0.0
        self._started_at = 0.0
        self._ends_at = 0.0
        self._template = _ZERO
        self._last_action = ""
        self._private_mind_nudge = 0.0
        self._requested_motion = ""

    def notify_affect(
        self,
        *,
        now: float,
        source: str,
        thought_pressure: float,
        confidence: float,
    ) -> None:
        """Mời một gesture suy tư sớm hơn, không ép chạy ngay giữa lời nói."""

        source_key = str(source or "").lower()
        if "private_mind" in source_key or "inner" in source_key or "deepseek" in source_key:
            strength = max(0.0, min(1.0, float(thought_pressure)))
            certainty = max(0.0, min(1.0, float(confidence)))
            self._private_mind_nudge = max(self._private_mind_nudge, strength * (0.55 + certainty * 0.35))
            self._next_at = min(self._next_at or (now + 0.45), now + 0.45)

    def advance(
        self,
        *,
        now: float,
        active: bool,
        allow_gesture: bool,
        affect: AffectVector,
        thought_pressure: float,
        motion: str = "stand",
    ) -> IdlePose:
        if not active:
            self._started_at = 0.0
            self._ends_at = 0.0
            self._template = _ZERO
            self._requested_motion = ""
            self._next_at = max(self._next_at, now + 1.4)
            return _ZERO

        requested = str(motion or "stand").lower()
        if requested != self._requested_motion:
            self._requested_motion = requested
            if requested not in {"stand", "idle", "none", "settle"}:
                duration, template = self._template_for(requested)
                self._started_at = now
                self._ends_at = now + duration
                self._template = template
                self._next_at = self._ends_at + self._next_delay(affect=affect, thought_pressure=thought_pressure)
            else:
                self._ends_at = 0.0
                self._template = _ZERO

        if self._next_at <= 0.0:
            self._next_at = now + self._next_delay(affect=affect, thought_pressure=thought_pressure)

        if allow_gesture and now >= self._next_at and now >= self._ends_at:
            self._start_action(now=now, affect=affect, thought_pressure=thought_pressure)

        # Vi chuyển động nền, luôn rất nhỏ và không tác động mày/miệng.
        living_x = 0.42 * math.sin(now * 0.41) + 0.18 * math.sin(now * 0.17 + 0.8)
        living_z = 0.55 * math.sin(now * 0.29 + 1.2) + 0.22 * math.sin(now * 0.13)
        if requested in {"focus", "still"}:
            living_x *= 0.12
            living_z *= 0.12
        living_gaze_x = 0.008 * math.sin(now * 0.19 + 0.4)
        living_gaze_y = 0.006 * math.sin(now * 0.23 + 2.0)

        if self._ends_at <= self._started_at or not (self._started_at <= now <= self._ends_at):
            return IdlePose(
                angle_x=living_x,
                angle_z=living_z,
                gaze_bias_x=living_gaze_x,
                gaze_bias_y=living_gaze_y,
                action="settle",
            )

        progress = max(0.0, min(1.0, (now - self._started_at) / (self._ends_at - self._started_at)))
        # sin(pi*x)^1.35 tạo hold mềm ở giữa nhưng không có góc gãy khi vào/ra.
        envelope = math.sin(math.pi * progress) ** 1.35
        t = self._template
        return IdlePose(
            angle_x=living_x + t.angle_x * envelope,
            angle_z=living_z + t.angle_z * envelope,
            brow_left=t.brow_left * envelope,
            brow_right=t.brow_right * envelope,
            mouth_form=t.mouth_form * envelope,
            eye_open_left_scale=1.0 + (t.eye_open_left_scale - 1.0) * envelope,
            eye_open_right_scale=1.0 + (t.eye_open_right_scale - 1.0) * envelope,
            gaze_bias_x=living_gaze_x + t.gaze_bias_x * envelope,
            gaze_bias_y=living_gaze_y + t.gaze_bias_y * envelope,
            action=t.action,
        )

    def _next_delay(self, *, affect: AffectVector, thought_pressure: float) -> float:
        pressure = max(0.0, min(1.0, float(thought_pressure)))
        energy = max(0.0, min(1.0, affect.arousal * 0.55 + affect.curiosity * 0.35 + pressure * 0.35))
        low = 3.8 - energy * 1.0
        high = 8.6 - energy * 1.8
        return self._rng.uniform(max(2.7, low), max(low + 1.0, high))

    def _start_action(self, *, now: float, affect: AffectVector, thought_pressure: float) -> None:
        action = self._choose_action(affect=affect, thought_pressure=thought_pressure)
        self._last_action = action
        duration, template = self._template_for(action)
        self._started_at = now
        self._ends_at = now + duration
        self._template = template
        self._next_at = self._ends_at + self._next_delay(affect=affect, thought_pressure=thought_pressure)
        self._private_mind_nudge *= 0.28

    def _choose_action(self, *, affect: AffectVector, thought_pressure: float) -> str:
        pressure = max(float(thought_pressure), self._private_mind_nudge)
        candidates: list[tuple[str, float]] = [
            ("glance_left", 1.05),
            ("glance_right", 0.92),
            ("tilt_left", 0.74 + affect.curiosity * 0.55),
            ("tilt_right", 0.70 + affect.curiosity * 0.50),
            ("soften", 0.55 + affect.tenderness * 0.90 + max(0.0, affect.valence) * 0.45),
            ("ponder", 0.48 + affect.curiosity * 0.82 + pressure * 1.35),
            ("slow_blink", 0.38 + affect.fatigue * 0.95 + affect.boredom * 0.38),
        ]
        # Tránh lặp y hệt liên tiếp. Ngẫu nhiên có trí nhớ tối thiểu, thật xa xỉ.
        weighted = [(name, weight * (0.28 if name == self._last_action else 1.0)) for name, weight in candidates]
        total = sum(weight for _, weight in weighted)
        pick = self._rng.random() * total
        cursor = 0.0
        for name, weight in weighted:
            cursor += weight
            if pick <= cursor:
                return name
        return "glance_left"

    def _template_for(self, action: str) -> tuple[float, IdlePose]:
        label = action
        action = {
            "blink_slow": "slow_blink", "small_smile": "soften", "tilt_head": "tilt_right",
            "look_away": "glance_left", "look_aside": "glance_left", "read": "ponder",
            "thinking_small": "ponder", "ponder_left": "ponder", "ponder_right": "ponder_right",
            "shake_head_small": "shake_small", "look_down": "look_down_soft", "sad": "look_down_soft",
        }.get(action, action)
        # Keep the requested planner/asset label in the observable pose.
        if action != label:
            duration, pose = self._template_for(action)
            from dataclasses import replace
            return duration, replace(pose, action=label)
        if action in {"focus", "still"}:
            return 1.8, IdlePose(eye_open_left_scale=.97, eye_open_right_scale=.97, action=action)
        if action == "nod_small":
            return .9, IdlePose(angle_x=2.4, action=action)
        if action == "lean_back_small":
            return 1.25, IdlePose(angle_x=-2.8, angle_z=-.5, action=action)
        if action == "lean_in_small":
            return 1.5, IdlePose(angle_x=1.8, angle_z=.6, action=action)
        if action == "look_down_soft":
            return 1.6, IdlePose(angle_x=-2.2, gaze_bias_y=-.07, action=action)
        if action == "look_up_soft":
            return 1.6, IdlePose(angle_x=1.8, gaze_bias_y=.07, action=action)
        if action == "scan_short":
            return 1.7, IdlePose(angle_x=1.6, gaze_bias_x=.05, action=action)
        if action == "shake_small":
            return 1.2, IdlePose(angle_z=-2.4, action=action)
        if action == "ponder_right":
            return 2.65, IdlePose(angle_x=2.5, angle_z=-2.4, brow_right=.075, gaze_bias_x=.026, action=action)
        if action == "glance_left":
            return 1.7, IdlePose(angle_x=-2.2, angle_z=-.65, gaze_bias_x=-0.065, action=action)
        if action == "glance_right":
            return 1.55, IdlePose(angle_x=1.8, angle_z=.6, gaze_bias_x=0.058, action=action)
        if action == "tilt_left":
            return 2.15, IdlePose(angle_x=-.55, angle_z=-3.2, brow_left=0.025, brow_right=-0.012, action=action)
        if action == "tilt_right":
            return 2.15, IdlePose(angle_x=.4, angle_z=3.3, brow_left=0.036, brow_right=-0.016, action=action)
        if action == "soften":
            return 2.45, IdlePose(
                angle_z=1.0,
                mouth_form=0.045,
                eye_open_left_scale=0.955,
                eye_open_right_scale=0.965,
                action=action,
            )
        if action == "ponder":
            return 2.65, IdlePose(
                angle_x=-2.6,
                angle_z=2.8,
                brow_left=0.075,
                brow_right=-0.028,
                eye_open_left_scale=0.965,
                eye_open_right_scale=0.985,
                gaze_bias_x=-0.026,
                gaze_bias_y=0.050,
                action=action,
            )
        if action == "slow_blink":
            return 0.92, IdlePose(
                mouth_form=0.018,
                eye_open_left_scale=0.58,
                eye_open_right_scale=0.60,
                action=action,
            )
        return 1.0, _ZERO


__all__ = ["IdleDirector", "IdlePose"]
