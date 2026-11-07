# -*- coding: utf-8 -*-
"""Public-turn continuity service extracted from RuntimeCoordinator.

The coordinator keeps compatibility wrappers, while this service owns ledger
recording, reply-window linking and a tiny *turn-collision* marker. The marker
never pauses cognition and never means that the user wants ERIS to be quiet.
"""
from __future__ import annotations
from core.di import resolve_dependency

import time
from types import SimpleNamespace
from typing import Any, Callable

from runtime.policy_settings import PublicTurnSettings
from runtime.public_turn_ledger import (
    PublicTurnLedger,
    infer_public_turn_channel,
    normalize_public_turn_channel,
)


class PublicTurnRuntime:
    def __init__(
        self,
        owner: Any,
        *,
        clock: Callable[[], float] | None = None,
        settings: PublicTurnSettings | None = None,
    ) -> None:
        self.owner = owner
        self._clock = clock or time.time
        self.settings = resolve_dependency(settings, lambda: PublicTurnSettings())

    def ensure_ledger(self) -> PublicTurnLedger:
        ledger = getattr(self.owner, "public_turn_ledger", None)
        if isinstance(ledger, PublicTurnLedger):
            return ledger
        ledger = PublicTurnLedger()
        self.owner.public_turn_ledger = ledger
        return ledger

    def infer_channel(self, decision: Any) -> str:
        if getattr(self.owner, "chat_mode", False) or getattr(self.owner, "_chat_processing", False):
            return "chat"
        return normalize_public_turn_channel(str(getattr(decision, "intent", "") or "autonomous"))

    def record(self, decision: Any, *, payload: dict | None = None, channel: str | None = None):
        payload = payload or {}
        text = str(payload.get("memory_text", payload.get("text", getattr(decision, "text", ""))) or "")
        should_speak = bool(payload.get("should_speak", getattr(decision, "should_speak", True)))
        if not should_speak or not text.strip():
            return None
        fallback = channel or self.infer_channel(decision)
        public_channel = infer_public_turn_channel(decision, fallback=fallback)
        stage_payload = payload.get("stage", {})
        turn_id = str(stage_payload.get("turn_id", "") or "") if isinstance(stage_payload, dict) else ""
        if not turn_id:
            turn_id = str(getattr(decision, "director_trace_id", "") or "")
        return self.ensure_ledger().append(
            channel=public_channel,
            text=text,
            parse_mode=str(payload.get("parse_mode", getattr(decision, "parse_mode", "")) or ""),
            visible_to_user=True,
            can_be_replied_to=True,
            turn_id=turn_id or None,
            timestamp=self._clock(),
        )

    def note_turn_collision(self, now: float | None = None, *, seconds: float | None = None) -> None:
        """Đánh dấu một cửa sổ rất ngắn để tránh hai public turn đè lên nhau.

        Đây là boundary cơ học của lượt nói, không phải social cooldown. Vì vậy
        method không đọc ``presence_maintenance_cooldown_sec`` hay
        ``reengagement_cooldown_sec`` và cũng không sở hữu quyền làm cognition
        ngủ. Self-turn vẫn được suy nghĩ; ExpressionCommitment chỉ DEFER output.
        """
        current_time = self._clock() if now is None else float(now)
        config = self.settings
        if seconds is None:
            requested = float(config.collision_grace_seconds)
        else:
            try:
                requested = float(seconds)
            except (TypeError, ValueError):
                requested = float(config.collision_grace_seconds)
        # Turn collision phải nhỏ và bounded. Một config 20-120s từ flow cũ
        # không được lén quay lại thành lệnh "đừng làm phiền user".
        window = max(0.0, min(5.0, requested))
        if window <= 0.0:
            return
        until = current_time + window
        self.owner.autonomous_turn_collision_until = max(
            float(getattr(self.owner, "autonomous_turn_collision_until", 0.0) or 0.0),
            until,
        )


    def link_previous_for_chat(self, user_timestamp: float):
        recent = self.ensure_ledger().latest_visible_before(user_timestamp)
        linked = (
            recent
            if recent is not None
            and user_timestamp - float(recent.timestamp) < self.settings.reply_window_seconds
            else None
        )
        context = getattr(self.owner, "reply_context", None)
        if context is None:
            context = SimpleNamespace(previous_public_turn=None)
            self.owner.reply_context = context
        context.previous_public_turn = linked
        return linked

    def previous_turn_context(self, public_turn: Any, *, now: float | None = None) -> str:
        if public_turn is None:
            return ""
        current_time = self._clock() if now is None else float(now)
        age = max(0.0, current_time - float(public_turn.timestamp))
        return (
            "previous_public_turn within reply window:\n"
            f"- turn_id={public_turn.turn_id}\n"
            f"- channel={public_turn.channel}\n"
            f"- age_seconds={age:.1f}\n"
            f"- parse_mode={public_turn.parse_mode}\n"
            f"- text={public_turn.text}\n"
            "Treat the current user message as a reply to that public turn when it reads like a reaction."
        )


__all__ = ["PublicTurnRuntime"]
