# -*- coding: utf-8 -*-
"""Repair-state có TTL và số lượt hữu hạn.

State này không tự phát hiện lỗi. Nó chỉ giữ vòng đời của một repair đã được
TurnFrame/repair policy xác nhận, để rerun lỗi không biến thành vòng lặp vĩnh cửu.
"""

from __future__ import annotations

import time
from dataclasses import dataclass


def _ttl_enabled() -> bool:
    try:
        from runtime.rollout_control import feature_enabled
        return feature_enabled("repair_state_ttl")
    except Exception:
        return True


@dataclass(slots=True)
class RepairState:
    active: bool = False
    started_at: float = 0.0
    turns_left: int = 0
    fallback_used: bool = False
    target: str = ""
    exhausted: bool = False


class RepairStateController:
    def __init__(self, *, ttl_s: float = 60.0, max_public_turns: int = 2) -> None:
        self.ttl_s = max(1.0, float(ttl_s))
        self.max_public_turns = max(1, int(max_public_turns))
        self.state = RepairState()

    def begin(self, *, target: str = "", now: float | None = None, force: bool = False) -> RepairState:
        if self.state.exhausted and not force:
            return self.state
        self.state = RepairState(
            active=True,
            started_at=time.monotonic() if now is None else float(now),
            turns_left=self.max_public_turns,
            fallback_used=False,
            target=str(target or ""),
            exhausted=False,
        )
        self._metric("begin")
        return self.state

    def available(self, *, now: float | None = None) -> bool:
        if not self.state.active:
            return False
        if not _ttl_enabled():
            return True
        ts = time.monotonic() if now is None else float(now)
        if ts - self.state.started_at > self.ttl_s or self.state.turns_left <= 0:
            self.exhaust()
            return False
        return True

    def consume_public_turn(self, *, now: float | None = None) -> bool:
        if not self.available(now=now):
            return False
        if not _ttl_enabled():
            self._metric("legacy_public_turn")
            return True
        self.state.turns_left -= 1
        if self.state.turns_left <= 0:
            self.exhaust(event="public_turn_exhausted")
        else:
            self._metric("public_turn_consumed")
        return True

    def consume_fallback_once(self, *, now: float | None = None) -> bool:
        if not self.available(now=now) or self.state.fallback_used:
            self.exhaust()
            return False
        self.state.fallback_used = True
        self.state.turns_left -= 1
        self._metric("fallback_consumed")
        return True

    def exhaust(self, *, event: str = "exhaust") -> None:
        self.state.active = False
        self.state.turns_left = 0
        self.state.exhausted = True
        self._metric(event)

    def clear(self) -> None:
        """Topic đã đổi hoặc user đã chấp nhận; cho phép repair mới sau đó."""
        had_state = self.state.active or self.state.exhausted
        self.state = RepairState()
        if had_state:
            self._metric("clear")

    def _metric(self, event: str) -> None:
        try:
            from runtime.hardening_metrics import record_repair_state
            record_repair_state(
                active=self.state.active, exhausted=self.state.exhausted,
                turns_left=self.state.turns_left, event=event,
            )
        except Exception:
            pass


__all__ = ["RepairState", "RepairStateController"]
