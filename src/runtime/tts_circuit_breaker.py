# -*- coding: utf-8 -*-
"""Circuit breaker riêng cho lane TTS.

Khóa breaker gồm provider/model/voice/locale. Một giọng Nhật lỗi không được kéo
sập giọng Việt, và một key mới không được dùng để đập tiếp vào model đang nóng.
"""

from __future__ import annotations

import os
import threading
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum


class BreakerState(str, Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


@dataclass(slots=True)
class _Entry:
    state: BreakerState = BreakerState.CLOSED
    failures: int = 0
    opened_at: float = 0.0
    successful_probes: int = 0
    probes_in_flight: int = 0
    last_failure_kind: str = ""
    active_probe_ids: set[str] = field(default_factory=set)


@dataclass(frozen=True, slots=True)
class BreakerDecision:
    allowed: bool
    state: BreakerState
    reason: str = ""
    probe_id: str = ""


def _record_metric(key: tuple[str, ...], *, state: BreakerState, event: str, kind: str = "") -> None:
    try:
        from runtime.hardening_metrics import record_tts_breaker
        # Key thực tế có 4 phần (Gemini) hoặc 5 phần (ElevenLabs account).
        # Metric chỉ lấy provider và locale, không bao giờ giải nén 4 cứng.
        provider, locale = str(key[0]), str(key[-1])
        record_tts_breaker(
            state=state.value, event=event, provider=str(provider), locale=str(locale), kind=str(kind or "")
        )
    except Exception:
        pass


class TtsCircuitBreaker:
    def __init__(self, *, failure_threshold: int = 3, cooldown_s: float = 45.0, successful_probes: int = 2, max_half_open_probes: int = 1) -> None:
        self.failure_threshold = max(1, int(failure_threshold))
        self.cooldown_s = max(1.0, float(cooldown_s))
        self.successful_probes = max(1, int(successful_probes))
        self.max_half_open_probes = max(1, int(max_half_open_probes))
        self._entries: dict[tuple[str, ...], _Entry] = {}
        self._lock = threading.RLock()

    def allow(self, key: tuple[str, ...], *, now: float | None = None) -> BreakerDecision:
        ts = time.monotonic() if now is None else float(now)
        with self._lock:
            entry = self._entries.setdefault(tuple(key), _Entry())
            if entry.state == BreakerState.OPEN:
                if ts - entry.opened_at < self.cooldown_s:
                    _record_metric(tuple(key), state=entry.state, event="blocked_cooldown", kind=entry.last_failure_kind)
                    return BreakerDecision(False, entry.state, "cooldown")
                entry.state = BreakerState.HALF_OPEN
                entry.probes_in_flight = 0
                entry.successful_probes = 0
                entry.active_probe_ids.clear()
                _record_metric(tuple(key), state=entry.state, event="cooldown_elapsed", kind=entry.last_failure_kind)
            if entry.state == BreakerState.HALF_OPEN:
                if entry.probes_in_flight >= self.max_half_open_probes:
                    _record_metric(tuple(key), state=entry.state, event="probe_blocked", kind=entry.last_failure_kind)
                    return BreakerDecision(False, entry.state, "probe_limit")
                entry.probes_in_flight += 1
                probe_id = uuid.uuid4().hex
                entry.active_probe_ids.add(probe_id)
                _record_metric(tuple(key), state=entry.state, event="probe_allowed", kind=entry.last_failure_kind)
                return BreakerDecision(True, entry.state, "probe", probe_id=probe_id)
            return BreakerDecision(True, entry.state, "closed")

    @staticmethod
    def _finish_probe(entry: _Entry, probe_id: str) -> bool:
        """Kết thúc đúng probe được cấp, không nhả nhầm request khác.

        Legacy callers không gửi ID vẫn được hỗ trợ, nhưng đường TTS mới luôn
        gửi probe_id để callback success/cancel đến trễ là no-op.
        """
        if probe_id:
            if probe_id not in entry.active_probe_ids:
                return False
            entry.active_probe_ids.remove(probe_id)
        elif entry.active_probe_ids:
            entry.active_probe_ids.pop()
        elif entry.probes_in_flight <= 0:
            return False
        entry.probes_in_flight = max(0, entry.probes_in_flight - 1)
        return True

    def record_success(self, key: tuple[str, ...], *, probe_id: str = "") -> None:
        with self._lock:
            entry = self._entries.setdefault(tuple(key), _Entry())
            if entry.state == BreakerState.HALF_OPEN:
                if not self._finish_probe(entry, probe_id):
                    return
                entry.successful_probes += 1
                if entry.successful_probes >= self.successful_probes:
                    self._entries[tuple(key)] = _Entry()
                    _record_metric(tuple(key), state=BreakerState.CLOSED, event="probe_recovered")
            elif not probe_id:
                # Không cho kết quả probe đến trễ đánh dấu OPEN là healthy.
                entry.failures = 0
                entry.last_failure_kind = ""

    def record_cancelled(self, key: tuple[str, ...], *, probe_id: str = "") -> None:
        """Huỷ thử nghiệm HALF_OPEN không chứng minh provider khoẻ hoặc hỏng.

        Nhả đúng một slot đã cấp, không reset failures/cooldown. Lặp lại sau
        record_success/record_failure là no-op; an toàn trong finally.
        """
        with self._lock:
            entry = self._entries.get(tuple(key))
            if entry is None or entry.state != BreakerState.HALF_OPEN:
                return
            if self._finish_probe(entry, probe_id):
                _record_metric(tuple(key), state=entry.state, event="probe_cancelled", kind=entry.last_failure_kind)

    def record_failure(self, key: tuple[str, ...], *, kind: str = "unknown", now: float | None = None, probe_id: str = "") -> None:
        ts = time.monotonic() if now is None else float(now)
        with self._lock:
            entry = self._entries.setdefault(tuple(key), _Entry())
            if entry.state == BreakerState.HALF_OPEN:
                if not self._finish_probe(entry, probe_id):
                    return
                entry.last_failure_kind = str(kind or "unknown")
                entry.active_probe_ids.clear()
                entry.probes_in_flight = 0
                entry.state = BreakerState.OPEN
                entry.opened_at = ts
                entry.failures = self.failure_threshold
                _record_metric(tuple(key), state=entry.state, event="probe_failed", kind=entry.last_failure_kind)
                return
            # Callback đến trễ sau khi probe đã thất bại/được hủy không được
            # cộng lại số lần lỗi hoặc reset breaker đã OPEN.
            if probe_id:
                return
            entry.last_failure_kind = str(kind or "unknown")
            entry.failures += 1
            if entry.failures >= self.failure_threshold:
                entry.state = BreakerState.OPEN
                entry.opened_at = ts
                _record_metric(tuple(key), state=entry.state, event="threshold_open", kind=entry.last_failure_kind)

    def state(self, key: tuple[str, ...]) -> BreakerState:
        with self._lock:
            return self._entries.get(tuple(key), _Entry()).state

    def reset(self) -> None:
        with self._lock:
            self._entries.clear()


def build_default_tts_breaker() -> TtsCircuitBreaker:
    return TtsCircuitBreaker(
        failure_threshold=int(os.environ.get("ERIS_TTS_BREAKER_FAILURES", "3") or 3),
        cooldown_s=float(os.environ.get("ERIS_TTS_BREAKER_COOLDOWN_S", "45") or 45),
        successful_probes=int(os.environ.get("ERIS_TTS_BREAKER_SUCCESS_PROBES", "2") or 2),
        max_half_open_probes=int(os.environ.get("ERIS_TTS_BREAKER_HALF_OPEN_PROBES", "1") or 1),
    )


GLOBAL_TTS_BREAKER = build_default_tts_breaker()


__all__ = ["BreakerDecision", "BreakerState", "GLOBAL_TTS_BREAKER", "TtsCircuitBreaker"]
