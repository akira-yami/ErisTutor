# -*- coding: utf-8 -*-
"""stage_session_runtime.py - session envelope, ACK và replay cho sân khấu.

Phase 10 không tự retry UDP mù quáng. Nó gắn event_id/event_seq tăng dần, giữ
replay buffer ngắn và theo dõi ACK/health. Frontend cũ vẫn đọc ``seq`` như
segment index; frontend mới dùng ``event_seq`` để bỏ gói cũ hoặc xin replay.
"""

from __future__ import annotations

import threading
import time
import uuid
from collections import deque
from dataclasses import replace
from typing import Any, Mapping

from runtime.stage_protocol import StageProtocolEvent


class StageSessionRuntime:
    VERSION = "stage_session.v1"

    def __init__(self, *, max_replay: int = 160) -> None:
        self.session_id = f"stage_{uuid.uuid4().hex[:16]}"
        self._lock = threading.RLock()
        self._event_seq = 0
        self._last_acked = 0
        self._last_sent_at = 0.0
        self._last_ack_at = 0.0
        self._send_failures = 0
        self._avatar_ready = False
        self._replay: deque[dict[str, Any]] = deque(maxlen=max(20, int(max_replay)))

    def prepare(self, event: StageProtocolEvent) -> StageProtocolEvent:
        with self._lock:
            self._event_seq += 1
            event_seq = self._event_seq
        trace_id = str(getattr(event, "trace_id", "") or event.turn_id or f"trace_{event_seq}")
        return replace(
            event,
            event_id=str(getattr(event, "event_id", "") or f"evt_{uuid.uuid4().hex[:18]}"),
            session_id=str(getattr(event, "session_id", "") or self.session_id),
            trace_id=trace_id,
            event_seq=event_seq,
        )

    def record_sent(self, payload: Mapping[str, Any], *, success: bool) -> None:
        with self._lock:
            self._last_sent_at = time.time()
            self._replay.append(dict(payload))
            if success:
                self._send_failures = 0
            else:
                self._send_failures += 1

    def observe_inbound(self, payload: Mapping[str, Any]) -> None:
        event_type = str(payload.get("type", "") or "")
        data = payload.get("data") if isinstance(payload.get("data"), Mapping) else {}
        if event_type == "avatar_ready":
            with self._lock:
                self._avatar_ready = True
                self._last_ack_at = time.time()
        if event_type == "stage_ack":
            raw = payload.get("event_seq", data.get("event_seq", payload.get("seq", 0)))
            try:
                ack = int(raw or 0)
            except (TypeError, ValueError):
                ack = 0
            if ack > 0:
                with self._lock:
                    self._last_acked = max(self._last_acked, ack)
                    self._last_ack_at = time.time()

    def replay_after(self, event_seq: int) -> tuple[dict[str, Any], ...]:
        try:
            floor = int(event_seq)
        except (TypeError, ValueError):
            floor = 0
        with self._lock:
            return tuple(item for item in self._replay if int(item.get("event_seq", 0) or 0) > floor)

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            backlog = max(0, self._event_seq - self._last_acked)
            return {
                "version": self.VERSION,
                "session_id": self.session_id,
                "event_seq": self._event_seq,
                "last_acked_event_seq": self._last_acked,
                "ack_backlog": backlog,
                "avatar_ready": self._avatar_ready,
                "send_failures": self._send_failures,
                "replay_size": len(self._replay),
                "last_sent_at": self._last_sent_at,
                "last_ack_at": self._last_ack_at,
                "degraded": bool(self._send_failures >= 3 or backlog > 80),
            }


__all__ = ["StageSessionRuntime"]
