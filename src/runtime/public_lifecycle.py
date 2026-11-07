# -*- coding: utf-8 -*-
"""Observable lifecycle for one public turn.

This exposes coarse runtime state only. It never exposes chain-of-thought. The
avatar and UI can therefore remain visibly alive while the semantic authority
is composing and ElevenLabs is buffering.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class PublicLifecycleSnapshot:
    turn_id: str = ""
    channel: str = ""
    state: str = "IDLE"


_TERMINAL_STATES = {"DEGRADED", "INTERRUPTED", "DONE"}
_ALLOWED_TRANSITIONS = {
    "IDLE": set(),
    # V62: một public turn có thể chủ động chọn im ngay trong giai đoạn compose
    # (dedupe/grounding/policy). DONE là kết thúc hợp lệ, không phải subsystem lỗi.
    "COMPOSING": {"CONTENT_COMMITTED", "DEGRADED", "INTERRUPTED", "DONE"},
    "CONTENT_COMMITTED": {"AUDIO_BUFFERING", "SPEAKING", "DEGRADED", "INTERRUPTED", "DONE"},
    "AUDIO_BUFFERING": {"SPEAKING", "DEGRADED", "INTERRUPTED", "DONE"},
    "SPEAKING": {"DEGRADED", "INTERRUPTED", "DONE"},
    "DEGRADED": set(),
    "INTERRUPTED": set(),
    "DONE": set(),
}


class PublicLifecycleController:
    def __init__(self, owner: Any) -> None:
        self.owner = owner
        self._lock = threading.RLock()
        self._snapshot = PublicLifecycleSnapshot()

    def snapshot(self) -> PublicLifecycleSnapshot:
        with self._lock:
            return self._snapshot

    def _emit(self, event_type: str, *, reason: str, turn_id: str, channel: str, state: str) -> None:
        emit = getattr(self.owner, "_emit_stage_protocol_state", None)
        if callable(emit):
            emit(
                event_type,
                reason=reason,
                data={
                    "turn_id": turn_id,
                    "channel": channel,
                    "state": state,
                    "label": {
                        "RECEIVED": "Đã nhận yêu cầu…",
                        "COMPOSING": "Đang suy nghĩ…",
                        "CONTENT_COMMITTED": "Đang chuẩn bị trả lời…",
                        "AUDIO_BUFFERING": "Đang chuẩn bị giọng…",
                    }.get(state, ""),
                },
            )

    def begin(self, *, turn_id: str, channel: str, reason: str = "public_composing", received: bool = False) -> str:
        turn_id = str(turn_id or "").strip()
        channel = str(channel or "unknown").strip().lower() or "unknown"
        with self._lock:
            previous = self._snapshot
            if previous.turn_id and previous.turn_id != turn_id and previous.state not in {"DONE", "DEGRADED", "INTERRUPTED"}:
                self._emit("interrupted", reason="superseded", turn_id=previous.turn_id, channel=previous.channel, state="INTERRUPTED")
            self._snapshot = PublicLifecycleSnapshot(turn_id, channel, "COMPOSING")
        if received:
            self._emit("received", reason="input_received", turn_id=turn_id, channel=channel, state="RECEIVED")
        self._emit("thinking_started", reason=reason, turn_id=turn_id, channel=channel, state="COMPOSING")
        return turn_id

    def transition(self, state: str, *, turn_id: str = "", reason: str = "") -> bool:
        state = str(state or "").strip().upper()
        event_type = {
            "CONTENT_COMMITTED": "content_committed",
            "AUDIO_BUFFERING": "audio_buffering",
            "SPEAKING": "speaking",
            "DEGRADED": "degraded",
            "INTERRUPTED": "interrupted",
            "DONE": "done",
        }.get(state)
        if not event_type:
            return False
        with self._lock:
            current = self._snapshot
            expected = str(turn_id or current.turn_id).strip()
            if not current.turn_id or current.state in _TERMINAL_STATES:
                return False
            if expected and current.turn_id != expected:
                return False
            if state == current.state:
                return True
            if state not in _ALLOWED_TRANSITIONS.get(current.state, set()):
                return False
            resolved_turn = current.turn_id
            channel = current.channel or "unknown"
            self._snapshot = PublicLifecycleSnapshot(resolved_turn, channel, state)
        if state in {"SPEAKING", "DEGRADED", "INTERRUPTED", "DONE"}:
            self._emit("thinking_finished", reason=reason or state.lower(), turn_id=resolved_turn, channel=channel, state=state)
        self._emit(event_type, reason=reason or state.lower(), turn_id=resolved_turn, channel=channel, state=state)
        return True

    def interrupt(self, *, reason: str = "user_interrupt") -> bool:
        with self._lock:
            current = self._snapshot
            if not current.turn_id or current.state in _TERMINAL_STATES:
                return False
        return self.transition("INTERRUPTED", turn_id=current.turn_id, reason=reason)


def lifecycle_for(owner: Any) -> PublicLifecycleController:
    controller = getattr(owner, "public_lifecycle", None)
    if isinstance(controller, PublicLifecycleController) and controller.owner is owner:
        return controller
    controller = PublicLifecycleController(owner)
    owner.public_lifecycle = controller
    return controller


__all__ = ["PublicLifecycleController", "PublicLifecycleSnapshot", "lifecycle_for"]
