# -*- coding: utf-8 -*-
"""V96: health gate dùng chung cho các chuỗi provider từ xa.

Mục tiêu không phải thay thế circuit breaker riêng của từng endpoint. Lớp này
trả lời câu hỏi ở cấp runtime: "chuỗi provider mà Eris cần có đang dùng được
không?". Khi toàn bộ chain vừa thất bại, background phải dừng đập mạng và
foreground chỉ được phép mở đúng một probe sau cooldown.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, replace
from enum import Enum
from typing import Callable, Iterable


class ProviderHealthState(str, Enum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    OFFLINE = "offline"
    PROBING = "probing"


class ProviderUnavailableFast(RuntimeError):
    """Fail-fast có chủ đích khi provider đang trong cooldown/offline."""


@dataclass(frozen=True, slots=True)
class ProviderHealthSnapshot:
    key: str
    state: ProviderHealthState = ProviderHealthState.HEALTHY
    consecutive_failures: int = 0
    total_failures: int = 0
    last_success_at: float = 0.0
    last_failure_at: float = 0.0
    next_probe_at: float = 0.0
    last_error: str = ""
    revision: int = 0

    @property
    def cooldown_remaining_s(self) -> float:
        return max(0.0, self.next_probe_at - time.time())


@dataclass(frozen=True, slots=True)
class ProviderPermit:
    allowed: bool
    probe: bool = False
    reason: str = "allow"
    state: ProviderHealthState = ProviderHealthState.HEALTHY


_FOREGROUND_PROBE_LANES = frozenset({
    "chat_main",
    "chat",
    "chat_tool_required",
    "directive",
    "capability_deliberation_required",
    "transport",
})


class ProviderHealthRegistry:
    def __init__(
        self,
        *,
        clock: Callable[[], float] | None = None,
        backoff_steps: Iterable[float] = (5.0, 15.0, 30.0, 60.0, 120.0),
        failure_threshold: int = 2,
    ) -> None:
        self._clock = clock or time.time
        steps = tuple(max(0.25, float(value)) for value in backoff_steps)
        self._backoff_steps = steps or (5.0,)
        self._failure_threshold = max(1, int(failure_threshold))
        self._lock = threading.RLock()
        self._items: dict[str, ProviderHealthSnapshot] = {}

    @staticmethod
    def _key(value: str) -> str:
        return str(value or "remote_llm").strip().lower() or "remote_llm"

    def snapshot(self, key: str) -> ProviderHealthSnapshot:
        normalized = self._key(key)
        with self._lock:
            return self._items.get(normalized) or ProviderHealthSnapshot(key=normalized)

    def acquire(self, key: str, *, lane: str) -> ProviderPermit:
        """Xin phép gọi provider mà không tạo thêm network I/O.

        OFFLINE chỉ mở đúng một half-open probe và chỉ cho lane foreground.
        Background không có quyền tự "thử xem provider sống lại chưa" vì nhiều
        worker cùng làm vậy sẽ biến cooldown thành một cơn retry storm.
        """
        normalized = self._key(key)
        lane_key = str(lane or "transport").strip().lower() or "transport"
        now = float(self._clock())
        with self._lock:
            item = self._items.get(normalized) or ProviderHealthSnapshot(key=normalized)
            if item.state in {ProviderHealthState.HEALTHY, ProviderHealthState.DEGRADED}:
                return ProviderPermit(True, False, "allow", item.state)
            if item.state == ProviderHealthState.PROBING:
                return ProviderPermit(False, False, "probe_inflight", item.state)
            if now < item.next_probe_at:
                return ProviderPermit(False, False, "cooldown", item.state)
            if lane_key not in _FOREGROUND_PROBE_LANES:
                return ProviderPermit(False, False, "foreground_probe_only", item.state)
            probing = replace(
                item,
                state=ProviderHealthState.PROBING,
                revision=item.revision + 1,
            )
            self._items[normalized] = probing
            return ProviderPermit(True, True, "half_open_probe", probing.state)

    def record_success(self, key: str) -> ProviderHealthSnapshot:
        normalized = self._key(key)
        now = float(self._clock())
        with self._lock:
            old = self._items.get(normalized) or ProviderHealthSnapshot(key=normalized)
            item = replace(
                old,
                state=ProviderHealthState.HEALTHY,
                consecutive_failures=0,
                last_success_at=now,
                next_probe_at=0.0,
                last_error="",
                revision=old.revision + 1,
            )
            self._items[normalized] = item
            return item

    def record_degraded_success(self, key: str, *, reason: str = "fallback_used") -> ProviderHealthSnapshot:
        """Chain vẫn phục vụ được nhưng primary vừa hỏng; cho phép request tiếp."""
        normalized = self._key(key)
        now = float(self._clock())
        with self._lock:
            old = self._items.get(normalized) or ProviderHealthSnapshot(key=normalized)
            item = replace(
                old,
                state=ProviderHealthState.DEGRADED,
                consecutive_failures=0,
                last_success_at=now,
                next_probe_at=0.0,
                last_error=str(reason or "fallback_used")[:240],
                revision=old.revision + 1,
            )
            self._items[normalized] = item
            return item

    def record_failure(
        self,
        key: str,
        error: BaseException | str,
        *,
        hard: bool = False,
    ) -> ProviderHealthSnapshot:
        normalized = self._key(key)
        now = float(self._clock())
        with self._lock:
            old = self._items.get(normalized) or ProviderHealthSnapshot(key=normalized)
            failures = old.consecutive_failures + 1
            total = old.total_failures + 1
            go_offline = bool(hard or old.state == ProviderHealthState.PROBING or failures >= self._failure_threshold)
            if go_offline:
                step_index = min(max(0, failures - 1), len(self._backoff_steps) - 1)
                next_probe = now + self._backoff_steps[step_index]
                state = ProviderHealthState.OFFLINE
            else:
                next_probe = 0.0
                state = ProviderHealthState.DEGRADED
            item = replace(
                old,
                state=state,
                consecutive_failures=failures,
                total_failures=total,
                last_failure_at=now,
                next_probe_at=next_probe,
                last_error=" ".join(str(error or "provider_failure").split())[:320],
                revision=old.revision + 1,
            )
            self._items[normalized] = item
            return item


GLOBAL_REMOTE_LLM_HEALTH = ProviderHealthRegistry()


__all__ = [
    "ProviderHealthState",
    "ProviderUnavailableFast",
    "ProviderHealthSnapshot",
    "ProviderPermit",
    "ProviderHealthRegistry",
    "GLOBAL_REMOTE_LLM_HEALTH",
]
