# -*- coding: utf-8 -*-
"""ReviewLock cho semantic winner của ERIS V74.

Lock này lưu semantic identity của winner sau bước merge. Review phía sau được
phép xác nhận hoặc hủy vì hard boundary/evidence invalidation, nhưng không được
tự ý đổi từ một intent đã chọn sang intent khác nếu không xuất hiện căn cứ ngữ
nghĩa mới hoặc điều kiện invalidation rõ ràng.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Any, Mapping

from cortex.subject.contracts import SubjectDeliberationFrame
from cortex.subject.neuro_control import SelectionResult


def _compact(value: Any, limit: int = 360) -> str:
    return " ".join(str(value or "").split()).strip()[: max(16, int(limit))]


@dataclass(frozen=True, slots=True)
class ReviewLockState:
    frame_id: str
    coalition_id: str
    action_kind: str
    speech_act: str
    semantic_target: str
    created_at: float
    invalidated: bool = False
    invalidation_reason: str = ""


class ReviewLock:
    def __init__(self, *, logger: Any | None = None, ttl_seconds: float = 120.0) -> None:
        self.logger = logger
        self.ttl_seconds = max(10.0, float(ttl_seconds))
        self._lock = threading.RLock()
        self._states: dict[str, ReviewLockState] = {}

    def lock(self, frame: SubjectDeliberationFrame, selection: SelectionResult, *, now: float | None = None) -> ReviewLockState:
        candidate = selection.winner(frame)
        args = dict(candidate.arguments or {})
        state = ReviewLockState(
            frame_id=frame.frame_id,
            coalition_id=candidate.coalition_id,
            action_kind=str(candidate.action_kind or "").strip().lower(),
            speech_act=_compact(args.get("speech_act", ""), 120).lower(),
            semantic_target=_compact(args.get("semantic_target", ""), 800),
            created_at=float(now or time.time()),
        )
        with self._lock:
            self._states[frame.frame_id] = state
            self._gc(now=state.created_at)
        return state

    def snapshot(self, frame_id: str, *, now: float | None = None) -> ReviewLockState | None:
        current = float(now or time.time())
        with self._lock:
            self._gc(now=current)
            return self._states.get(str(frame_id or ""))

    def invalidate(self, frame_id: str, reason: str, *, now: float | None = None) -> ReviewLockState | None:
        with self._lock:
            state = self._states.get(str(frame_id or ""))
            if state is None:
                return None
            updated = ReviewLockState(
                frame_id=state.frame_id,
                coalition_id=state.coalition_id,
                action_kind=state.action_kind,
                speech_act=state.speech_act,
                semantic_target=state.semantic_target,
                created_at=float(now or time.time()),
                invalidated=True,
                invalidation_reason=_compact(reason, 240),
            )
            self._states[state.frame_id] = updated
            return updated

    def allows(
        self,
        frame_id: str,
        candidate: Any,
        *,
        invalidation: bool = False,
        now: float | None = None,
    ) -> bool:
        """Cho biết candidate có giữ nguyên semantic winner hay không.

        ``now`` được nhận tường minh để deterministic test/replay không bị lệch
        vì đồng hồ tường. Runtime bình thường bỏ trống và dùng ``time.time()``.
        """
        state = self.snapshot(frame_id, now=now)
        if state is None or state.invalidated or invalidation:
            return True
        args = getattr(candidate, "arguments", {})
        args = dict(args) if isinstance(args, Mapping) else {}
        action = str(getattr(candidate, "action_kind", "") or "").strip().lower()
        speech_act = _compact(args.get("speech_act", ""), 120).lower()
        if action != state.action_kind:
            return False
        if state.speech_act and speech_act and speech_act != state.speech_act:
            return False
        return True

    def _gc(self, *, now: float) -> None:
        stale = [key for key, value in self._states.items() if now - value.created_at > self.ttl_seconds]
        for key in stale:
            self._states.pop(key, None)


__all__ = ["ReviewLockState", "ReviewLock"]
