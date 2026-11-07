# -*- coding: utf-8 -*-
"""stage_continuation.py — runtime sở hữu khoảng lặng có chủ đích giữa các speech beat.

Không dùng ``time.sleep`` mù: mọi khoảng chờ đi qua ``Event.wait`` nên user
interrupt/reset có thể đánh thức và hủy ngay. Pause ngắn vẫn thuộc prosody/TTS;
pause dài trở thành trạng thái sân khấu thật với lifecycle event riêng.
"""
from __future__ import annotations

import random
import threading
from dataclasses import dataclass
from typing import Any, Callable


@dataclass(frozen=True, slots=True)
class ContinuationWait:
    kind: str
    delay_ms: int
    resume_policy: str = "timer"
    cancel_on_user_input: bool = True
    motion: str = "thinking_small"
    reason: str = ""


class StageContinuationRuntime:
    """Quản lý generation/cancel token cho nhịp nói nhiều đoạn."""

    def __init__(self, *, short_pause_max_ms: int = 350, max_wait_ms: int = 2000) -> None:
        self.short_pause_max_ms = max(100, min(500, int(short_pause_max_ms)))
        self.max_wait_ms = max(self.short_pause_max_ms, min(2000, int(max_wait_ms)))
        self._lock = threading.RLock()
        self._generation = 0
        self._cancel_event = threading.Event()
        self._state = "idle"
        self._active: ContinuationWait | None = None

    @property
    def state(self) -> str:
        with self._lock:
            return self._state

    def begin_generation(self) -> tuple[int, threading.Event]:
        with self._lock:
            self._cancel_event.set()
            self._generation += 1
            self._cancel_event = threading.Event()
            self._state = "ready"
            self._active = None
            return self._generation, self._cancel_event

    def cancel(self, reason: str = "cancelled") -> bool:
        with self._lock:
            was_active = self._state not in {"idle", "cancelled", "completed"}
            self._state = "cancelled"
            self._active = ContinuationWait("cancelled", 0, reason=reason)
            self._cancel_event.set()
            return was_active

    def complete(self, generation: int) -> None:
        with self._lock:
            if generation == self._generation and not self._cancel_event.is_set():
                self._state = "completed"
                self._active = None

    def wait_after_frame(
        self,
        frame: Any,
        *,
        generation: int,
        cancel_event: threading.Event,
        emit: Callable[[str, str], None] | None = None,
    ) -> bool:
        wait = self.wait_contract(frame)
        if wait.delay_ms <= 0:
            return True
        if generation != self._generation or cancel_event.is_set():
            return False

        # Pause ngắn vẫn được thi hành ở manager để playback không dính sát nhau,
        # nhưng không đổi avatar sang trạng thái "đang nghĩ".
        if wait.kind == "breath_pause":
            return not cancel_event.wait(wait.delay_ms / 1000.0)

        with self._lock:
            if generation != self._generation or cancel_event.is_set():
                return False
            self._state = "thinking"
            self._active = wait
        if emit is not None:
            emit("thinking_started", wait.reason or f"pause:{wait.delay_ms}ms")

        cancelled = cancel_event.wait(wait.delay_ms / 1000.0)
        with self._lock:
            if generation == self._generation and not cancelled:
                self._state = "ready"
                self._active = None
        if emit is not None:
            emit("continuation_cancelled" if cancelled else "thinking_finished", "cancelled" if cancelled else "timer_elapsed")
        return not cancelled

    def wait_contract(self, frame: Any) -> ContinuationWait:
        delivery = getattr(frame, "delivery", {})
        beat = delivery.get("active_beat") if isinstance(delivery, dict) else {}
        beat = beat if isinstance(beat, dict) else {}
        try:
            pause_ms = max(0, int(beat.get("pause_after_ms", 0) or 0))
        except (TypeError, ValueError):
            pause_ms = 0
        pause_ms = min(self.max_wait_ms, pause_ms)
        if pause_ms <= 0:
            return ContinuationWait("none", 0)

        resume_policy = str(beat.get("resume_policy", "timer") or "timer")
        motion = str(beat.get("thinking_motion", "thinking_small") or "thinking_small")
        if pause_ms <= self.short_pause_max_ms:
            return ContinuationWait("breath_pause", pause_ms, resume_policy=resume_policy, motion=motion)

        # Biên độ nhỏ tránh mọi take có nhịp giống hệt metronome, nhưng vẫn hữu hạn
        # và deterministic nếu beat đặt pause_jitter_ms=0.
        try:
            jitter = max(0, min(300, int(beat.get("pause_jitter_ms", 80) or 0)))
        except (TypeError, ValueError):
            jitter = 80
        delay_ms = max(self.short_pause_max_ms + 1, min(self.max_wait_ms, pause_ms + random.randint(-jitter, jitter)))
        return ContinuationWait(
            "thinking_pause",
            delay_ms,
            resume_policy=resume_policy,
            cancel_on_user_input=bool(beat.get("cancel_on_user_input", True)),
            motion=motion,
            reason=str(beat.get("pause_reason", "deliberate_thought") or "deliberate_thought"),
        )


__all__ = ["ContinuationWait", "StageContinuationRuntime"]
