# -*- coding: utf-8 -*-
"""Đồng hồ tương tác tách user, generation, text commit và audio playback.

Timer cũ đo từ lúc user gửi câu. Nếu LLM suy nghĩ 20 giây, Eris vừa nói xong đã
bị coi là "im quá 20 giây". Đồng hồ này lấy mốc hoạt động public cuối cùng và
biết khi nào generation/TTS còn bận, nên autonomous không chen vào giữa câu.
"""

from __future__ import annotations

import time
from dataclasses import dataclass


@dataclass(slots=True)
class InteractionClock:
    user_turn_at: float = 0.0
    generation_started_at: float = 0.0
    generation_finished_at: float = 0.0
    assistant_text_at: float = 0.0
    audio_pending_at: float = 0.0
    audio_started_at: float = 0.0
    audio_finished_at: float = 0.0
    interrupt_at: float = 0.0
    generation_active: bool = False
    audio_pending: bool = False
    audio_active: bool = False
    audio_floor_turn_id: str = ""

    def mark_user_turn(self, timestamp: float | None = None) -> None:
        self.user_turn_at = float(timestamp or time.time())

    def mark_generation_started(self, timestamp: float | None = None) -> None:
        self.generation_started_at = float(timestamp or time.time())
        self.generation_active = True

    def mark_generation_finished(self, timestamp: float | None = None) -> None:
        self.generation_finished_at = float(timestamp or time.time())
        self.generation_active = False

    def mark_assistant_text(self, timestamp: float | None = None) -> None:
        self.assistant_text_at = float(timestamp or time.time())
        self.generation_active = False

    def mark_audio_pending(self, timestamp: float | None = None, *, turn_id: str = "") -> None:
        self.audio_pending_at = float(timestamp or time.time())
        self.audio_pending = True
        normalized = str(turn_id or "").strip()
        if normalized:
            self.audio_floor_turn_id = normalized

    def mark_audio_started(self, timestamp: float | None = None, *, turn_id: str = "") -> None:
        self.audio_started_at = float(timestamp or time.time())
        self.audio_pending = False
        self.audio_active = True
        normalized = str(turn_id or "").strip()
        if normalized:
            self.audio_floor_turn_id = normalized

    def mark_audio_finished(self, timestamp: float | None = None, *, turn_id: str = "") -> None:
        normalized = str(turn_id or "").strip()
        if normalized and self.audio_floor_turn_id and normalized != self.audio_floor_turn_id:
            # Event trễ của turn cũ không được nhả floor mà turn mới đang giữ.
            return
        self.audio_finished_at = float(timestamp or time.time())
        self.audio_pending = False
        self.audio_active = False
        self.audio_floor_turn_id = ""

    def mark_interrupt(self, timestamp: float | None = None) -> None:
        self.interrupt_at = float(timestamp or time.time())
        self.audio_pending = False
        self.audio_active = False
        self.audio_floor_turn_id = ""

    @property
    def speech_floor_busy(self) -> bool:
        return bool(self.audio_pending or self.audio_active)

    @property
    def busy(self) -> bool:
        return bool(self.generation_active or self.speech_floor_busy)

    @property
    def last_public_activity_at(self) -> float:
        return max(
            self.user_turn_at,
            self.assistant_text_at,
            self.audio_pending_at,
            self.audio_started_at,
            self.audio_finished_at,
            self.interrupt_at,
        )

    def stale(self, window_seconds: float, *, now: float | None = None) -> bool:
        if self.busy:
            return False
        last = self.last_public_activity_at
        if last <= 0.0:
            return False
        return (float(now or time.time()) - last) >= max(0.0, float(window_seconds))


__all__ = ["InteractionClock"]
