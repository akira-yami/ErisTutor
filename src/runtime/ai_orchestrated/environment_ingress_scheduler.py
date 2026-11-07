# -*- coding: utf-8 -*-
"""Bộ lập lịch ingress cho Deep Environment.

Module này giải quyết một vấn đề rất cụ thể của hệ thống event-driven trên desktop:
Windows/Chromium có thể phát hàng trăm event cấu trúc trong vài chục mili giây,
trong khi một event foreground, focus hoặc command_failed phải được xử lý ngay.

Thiết kế cố tình tách hai khái niệm:

* ``priority``: event cần được *xử lý nhanh đến đâu*;
* ``attention_level``: event có đáng *đánh thức cognition* hay không.

Ví dụ password focus có priority CRITICAL vì privacy phải phản ứng ngay, nhưng
attention vẫn có thể là 0. Nhờ vậy scheduler không vô tình biến "ưu tiên xử lý"
thành "ưu tiên nói".

Scheduler dùng một priority heap kèm coalescing index. Event background kiểu
``structure_reordered`` được latest-wins theo HWND/region; event quan trọng giữ FIFO
ổn định trong cùng priority. Khi backlog đầy, background bị loại trước critical.
Không có sleep đặc biệt cho Chrome và không rải ``if event_type`` khắp worker.
"""
from __future__ import annotations

import heapq
import threading
import time
from collections import Counter
from dataclasses import dataclass, replace
from enum import Enum, IntEnum
from typing import Any, Mapping

from .environment_event_bus import NormalizedEnvironmentEvent


_CHROMIUM_PROCESSES = {"chrome.exe", "msedge.exe", "brave.exe", "chromium.exe"}
_NATIVE_SOURCES = {"winevent", "foreground_reconcile"}


class EnvironmentPriority(IntEnum):
    """Độ khẩn của scheduler. Số nhỏ hơn được lấy ra trước."""

    CRITICAL = 0
    NORMAL = 10
    BACKGROUND = 20


class CoalescePolicy(str, Enum):
    """Cách gộp event đang chờ trong queue."""

    NONE = "none"
    LATEST_PER_WINDOW = "latest_per_window"
    LATEST_PER_REGION = "latest_per_region"
    LATEST_PER_OBJECT = "latest_per_object"


class EnrichmentMode(str, Enum):
    """Mức enrichment cho raw native event.

    NONE
        Structured metadata hiện tại đã đủ; không gọi UIA.
    MINIMAL
        Cần semantic control/focus tối thiểu. Worker có thể gọi UIA một lần.
    SEMANTIC
        Event có khả năng tạo semantic fact đáng giữ, ví dụ dialog mới.
    """

    NONE = "none"
    MINIMAL = "minimal"
    SEMANTIC = "semantic"


@dataclass(frozen=True, slots=True)
class EventSchedulingPolicy:
    """Policy declarative gắn với một event trước khi vào semantic worker."""

    priority: EnvironmentPriority
    coalesce: CoalescePolicy
    max_queue_age_ms: int
    enrichment: EnrichmentMode = EnrichmentMode.NONE
    chromium_refresh: bool = False


# Bảng policy tập trung. Worker không cần hiểu từng event type.
# Những event không có trong bảng sẽ đi qua policy mặc định dựa trên attention.
_POLICY_BY_EVENT_TYPE: dict[str, EventSchedulingPolicy] = {
    # Critical path: cập nhật UserSituation nhanh, nhưng attention vẫn do event quyết định.
    "foreground_changed": EventSchedulingPolicy(EnvironmentPriority.CRITICAL, CoalescePolicy.LATEST_PER_WINDOW, 1000, EnrichmentMode.MINIMAL, True),
    "focus_changed": EventSchedulingPolicy(EnvironmentPriority.CRITICAL, CoalescePolicy.LATEST_PER_OBJECT, 800, EnrichmentMode.MINIMAL, True),
    "selection_changed": EventSchedulingPolicy(EnvironmentPriority.CRITICAL, CoalescePolicy.LATEST_PER_OBJECT, 800, EnrichmentMode.MINIMAL, True),
    "browser_tab_activated": EventSchedulingPolicy(EnvironmentPriority.CRITICAL, CoalescePolicy.LATEST_PER_WINDOW, 1000),
    "command_failed": EventSchedulingPolicy(EnvironmentPriority.CRITICAL, CoalescePolicy.NONE, 2000),
    "command_completed": EventSchedulingPolicy(EnvironmentPriority.CRITICAL, CoalescePolicy.NONE, 2000),
    "task_completed": EventSchedulingPolicy(EnvironmentPriority.CRITICAL, CoalescePolicy.NONE, 2000),
    "blocking_dialog": EventSchedulingPolicy(EnvironmentPriority.CRITICAL, CoalescePolicy.LATEST_PER_WINDOW, 1000),
    "dialog_candidate": EventSchedulingPolicy(EnvironmentPriority.CRITICAL, CoalescePolicy.LATEST_PER_WINDOW, 1000),
    "password_control_focused": EventSchedulingPolicy(EnvironmentPriority.CRITICAL, CoalescePolicy.LATEST_PER_OBJECT, 500),

    # Event có giá trị timeline/task nhưng không cần chen trước foreground/error.
    "command_started": EventSchedulingPolicy(EnvironmentPriority.NORMAL, CoalescePolicy.NONE, 800),
    "file_saved": EventSchedulingPolicy(EnvironmentPriority.NORMAL, CoalescePolicy.LATEST_PER_REGION, 800),
    "source_modified": EventSchedulingPolicy(EnvironmentPriority.NORMAL, CoalescePolicy.LATEST_PER_REGION, 800),
    "browser_tab_title_changed": EventSchedulingPolicy(EnvironmentPriority.NORMAL, CoalescePolicy.LATEST_PER_WINDOW, 500),
    "clipboard_changed": EventSchedulingPolicy(EnvironmentPriority.NORMAL, CoalescePolicy.LATEST_PER_WINDOW, 500),
    "media_changed": EventSchedulingPolicy(EnvironmentPriority.NORMAL, CoalescePolicy.LATEST_PER_WINDOW, 800),
    "editor_focused": EventSchedulingPolicy(EnvironmentPriority.NORMAL, CoalescePolicy.LATEST_PER_OBJECT, 500),
    "terminal_focused": EventSchedulingPolicy(EnvironmentPriority.NORMAL, CoalescePolicy.LATEST_PER_OBJECT, 500),
    "ui_tab_activated": EventSchedulingPolicy(EnvironmentPriority.NORMAL, CoalescePolicy.LATEST_PER_OBJECT, 500),

    # Raw WinEvent có thể là dialog trên app thường, nên object_shown được giữ NORMAL.
    "object_shown": EventSchedulingPolicy(EnvironmentPriority.NORMAL, CoalescePolicy.LATEST_PER_WINDOW, 350, EnrichmentMode.SEMANTIC),
    "name_changed": EventSchedulingPolicy(EnvironmentPriority.BACKGROUND, CoalescePolicy.LATEST_PER_WINDOW, 180, EnrichmentMode.NONE, True),

    # Structural/mechanical churn. Latest-wins và stale rất nhanh.
    "object_hidden": EventSchedulingPolicy(EnvironmentPriority.BACKGROUND, CoalescePolicy.LATEST_PER_WINDOW, 140),
    "structure_reordered": EventSchedulingPolicy(EnvironmentPriority.BACKGROUND, CoalescePolicy.LATEST_PER_WINDOW, 140),
    "value_changed": EventSchedulingPolicy(EnvironmentPriority.BACKGROUND, CoalescePolicy.LATEST_PER_OBJECT, 140),
    "ui_control_changed": EventSchedulingPolicy(EnvironmentPriority.BACKGROUND, CoalescePolicy.LATEST_PER_OBJECT, 180),
    "control_name_changed": EventSchedulingPolicy(EnvironmentPriority.BACKGROUND, CoalescePolicy.LATEST_PER_OBJECT, 180),
    "control_value_changed": EventSchedulingPolicy(EnvironmentPriority.BACKGROUND, CoalescePolicy.LATEST_PER_OBJECT, 180),
    "browser_accessibility_provider_changed": EventSchedulingPolicy(EnvironmentPriority.BACKGROUND, CoalescePolicy.LATEST_PER_WINDOW, 600),
    "counter_update": EventSchedulingPolicy(EnvironmentPriority.BACKGROUND, CoalescePolicy.LATEST_PER_WINDOW, 100),
    "clock_tick": EventSchedulingPolicy(EnvironmentPriority.BACKGROUND, CoalescePolicy.LATEST_PER_WINDOW, 100),
    "scroll_churn": EventSchedulingPolicy(EnvironmentPriority.BACKGROUND, CoalescePolicy.LATEST_PER_WINDOW, 100),
    "progress_update": EventSchedulingPolicy(EnvironmentPriority.BACKGROUND, CoalescePolicy.LATEST_PER_WINDOW, 120),
    "mechanical_value_update": EventSchedulingPolicy(EnvironmentPriority.BACKGROUND, CoalescePolicy.LATEST_PER_WINDOW, 100),
    "ocr_jitter": EventSchedulingPolicy(EnvironmentPriority.BACKGROUND, CoalescePolicy.LATEST_PER_REGION, 100),
}


def scheduling_policy_for(event: NormalizedEnvironmentEvent) -> EventSchedulingPolicy:
    """Trả policy scheduling duy nhất cho event.

    Chromium raw events có override nhỏ nhưng tập trung ở đây: generic UIA enrichment
    bị tắt vì ChromiumAccessibilityAdapter là semantic provider chính. Chỉ các trigger
    chọn lọc mới yêu cầu refresh IA2/UIA, tránh scan accessibility tree cho mọi
    show/hide/reorder storm.
    """

    event_type = str(event.event_type or "unknown")
    base = _POLICY_BY_EVENT_TYPE.get(event_type)
    if base is None:
        if int(event.attention_level or 0) >= 2:
            base = EventSchedulingPolicy(EnvironmentPriority.CRITICAL, CoalescePolicy.NONE, 500)
        elif int(event.attention_level or 0) <= 0 or not bool(event.semantic):
            base = EventSchedulingPolicy(EnvironmentPriority.BACKGROUND, CoalescePolicy.LATEST_PER_OBJECT, 160)
        else:
            base = EventSchedulingPolicy(EnvironmentPriority.NORMAL, CoalescePolicy.LATEST_PER_OBJECT, 600)

    process_name = str(event.process_name or "").strip().lower()
    if process_name in _CHROMIUM_PROCESSES and event.source in _NATIVE_SOURCES:
        # Chromium semantics đi qua IA2-first/UIA fallback riêng; gọi generic UIA
        # thêm lần nữa chỉ tạo cross-process round-trip trùng lặp.
        refresh = event_type in {"foreground_changed", "focus_changed", "selection_changed", "name_changed"}
        if event_type in {"object_shown", "object_hidden", "structure_reordered", "value_changed"}:
            return EventSchedulingPolicy(
                EnvironmentPriority.BACKGROUND,
                CoalescePolicy.LATEST_PER_WINDOW,
                120,
                EnrichmentMode.NONE,
                False,
            )
        return replace(base, enrichment=EnrichmentMode.NONE, chromium_refresh=refresh)

    return base


def _coalesce_key(event: NormalizedEnvironmentEvent, policy: EventSchedulingPolicy) -> str:
    """Sinh identity ổn định cho latest-wins mà không phụ thuộc bounding-box."""

    if policy.coalesce == CoalescePolicy.NONE:
        return ""

    prefix = f"{event.process_name.lower()}|{int(event.pid or 0)}|{int(event.hwnd or 0)}|{event.event_type}"
    if policy.coalesce == CoalescePolicy.LATEST_PER_WINDOW:
        return prefix
    if policy.coalesce == CoalescePolicy.LATEST_PER_REGION:
        region = str(event.region_id or "window")
        return f"{prefix}|{region}"

    metadata: Mapping[str, Any] = event.metadata or {}
    object_identity = (
        str(metadata.get("runtime_id", "") or "").strip()
        or str(metadata.get("automation_id", "") or "").strip()
        or str(event.region_id or "").strip()
        or str(event.role or "object").strip()
    )
    return f"{prefix}|{object_identity}"


@dataclass(frozen=True, slots=True)
class ScheduledEnvironmentEvent:
    """Envelope runtime-only của event đang chờ semantic worker."""

    event: NormalizedEnvironmentEvent
    ingress_monotonic_ns: int
    ingress_wall_time: float
    enqueue_monotonic_ns: int
    policy: EventSchedulingPolicy
    sequence: int
    coalesce_key: str = ""


@dataclass(frozen=True, slots=True)
class SchedulerSubmitResult:
    accepted: bool
    coalesced: bool = False
    evicted: bool = False
    dropped: bool = False
    reason: str = ""
    queue_depth: int = 0
    item: ScheduledEnvironmentEvent | None = None


class EnvironmentIngressScheduler:
    """Priority scheduler bounded, có coalescing index và backpressure.

    Heap chỉ giữ ``(priority, sequence, token)``. Payload thật nằm trong ``_pending``.
    Khi latest-wins thay thế event cũ, heap entry cũ trở thành tombstone và được bỏ
    qua lúc pop. Heap được compact định kỳ để storm dài không làm tombstone phình vô hạn.
    """

    def __init__(self, *, max_pending: int = 1024) -> None:
        self.max_pending = max(64, int(max_pending))
        self._condition = threading.Condition(threading.RLock())
        self._heap: list[tuple[int, int, int]] = []
        self._pending: dict[int, ScheduledEnvironmentEvent] = {}
        self._coalesce_index: dict[str, int] = {}
        self._sequence = 0
        self._token = 0
        self._closed = False
        self._stats: Counter[str] = Counter()

    def close(self) -> None:
        with self._condition:
            self._closed = True
            self._condition.notify_all()

    @property
    def qsize(self) -> int:
        with self._condition:
            return len(self._pending)

    def stats(self) -> dict[str, int]:
        with self._condition:
            return dict(self._stats)

    def submit(
        self,
        event: NormalizedEnvironmentEvent,
        *,
        ingress_monotonic_ns: int,
        ingress_wall_time: float,
    ) -> SchedulerSubmitResult:
        policy = scheduling_policy_for(event)
        enqueue_ns = time.monotonic_ns()
        key = _coalesce_key(event, policy)

        with self._condition:
            if self._closed:
                self._stats["dropped_closed"] += 1
                return SchedulerSubmitResult(False, dropped=True, reason="closed", queue_depth=len(self._pending))

            coalesced = False
            old_token = self._coalesce_index.get(key) if key else None
            if old_token is not None and old_token in self._pending:
                self._remove_token_locked(old_token)
                self._stats["coalesced"] += 1
                coalesced = True

            evicted = False
            if len(self._pending) >= self.max_pending:
                evicted = self._evict_for_locked(policy.priority)
                if not evicted:
                    self._stats["dropped_backpressure"] += 1
                    return SchedulerSubmitResult(
                        False,
                        coalesced=coalesced,
                        dropped=True,
                        reason="backpressure",
                        queue_depth=len(self._pending),
                    )

            self._sequence += 1
            self._token += 1
            item = ScheduledEnvironmentEvent(
                event=event,
                ingress_monotonic_ns=int(ingress_monotonic_ns),
                ingress_wall_time=float(ingress_wall_time),
                enqueue_monotonic_ns=enqueue_ns,
                policy=policy,
                sequence=self._sequence,
                coalesce_key=key,
            )
            token = self._token
            self._pending[token] = item
            if key:
                self._coalesce_index[key] = token
            heapq.heappush(self._heap, (int(policy.priority), item.sequence, token))
            self._stats["accepted"] += 1
            if evicted:
                self._stats["evicted_for_priority"] += 1
            self._maybe_compact_heap_locked()
            self._condition.notify()
            return SchedulerSubmitResult(
                True,
                coalesced=coalesced,
                evicted=evicted,
                queue_depth=len(self._pending),
                item=item,
            )

    def get(self, *, timeout: float | None = None, max_priority: EnvironmentPriority | None = None) -> ScheduledEnvironmentEvent | None:
        """Lấy event ưu tiên cao nhất; tự bỏ event đã stale trước khi trả về."""

        deadline = None if timeout is None else time.monotonic() + max(0.0, float(timeout))
        with self._condition:
            while True:
                item = self._pop_ready_locked(max_priority=max_priority)
                if item is not None:
                    return item
                if self._closed:
                    return None
                if deadline is not None:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        return None
                    self._condition.wait(timeout=remaining)
                else:
                    self._condition.wait()

    def get_nowait(self, *, max_priority: EnvironmentPriority | None = None) -> ScheduledEnvironmentEvent | None:
        with self._condition:
            return self._pop_ready_locked(max_priority=max_priority)

    def _pop_ready_locked(self, *, max_priority: EnvironmentPriority | None) -> ScheduledEnvironmentEvent | None:
        now_ns = time.monotonic_ns()
        while self._heap:
            priority_value, _sequence, token = self._heap[0]
            if max_priority is not None and priority_value > int(max_priority):
                return None
            heapq.heappop(self._heap)
            item = self._pending.pop(token, None)
            if item is None:
                continue
            if item.coalesce_key and self._coalesce_index.get(item.coalesce_key) == token:
                self._coalesce_index.pop(item.coalesce_key, None)

            age_ms = max(0.0, (now_ns - item.enqueue_monotonic_ns) / 1_000_000.0)
            if item.policy.max_queue_age_ms > 0 and age_ms > float(item.policy.max_queue_age_ms):
                self._stats["dropped_stale"] += 1
                continue

            self._stats["dequeued"] += 1
            return item
        return None

    def _remove_token_locked(self, token: int) -> ScheduledEnvironmentEvent | None:
        item = self._pending.pop(token, None)
        if item is not None and item.coalesce_key and self._coalesce_index.get(item.coalesce_key) == token:
            self._coalesce_index.pop(item.coalesce_key, None)
        return item

    def _evict_for_locked(self, incoming_priority: EnvironmentPriority) -> bool:
        """Loại event tệ nhất/già nhất nếu incoming đáng được giữ hơn hoặc bằng."""

        if not self._pending:
            return True
        token, candidate = max(
            self._pending.items(),
            key=lambda pair: (int(pair[1].policy.priority), -int(pair[1].sequence)),
        )
        # incoming số nhỏ hơn = ưu tiên cao hơn. Nếu incoming còn tệ hơn candidate,
        # giữ backlog hiện tại và drop incoming.
        if int(incoming_priority) > int(candidate.policy.priority):
            return False
        self._remove_token_locked(token)
        return True

    def _maybe_compact_heap_locked(self) -> None:
        # Coalescing latest-wins tạo tombstone trong heap. Compact khi số tombstone
        # lớn hơn đáng kể số event thật để storm dài không tăng RAM vô hạn.
        if len(self._heap) <= max(256, len(self._pending) * 3 + 64):
            return
        active_tokens = set(self._pending)
        self._heap = [entry for entry in self._heap if entry[2] in active_tokens]
        heapq.heapify(self._heap)
        self._stats["heap_compactions"] += 1


__all__ = [
    "CoalescePolicy",
    "EnrichmentMode",
    "EnvironmentIngressScheduler",
    "EnvironmentPriority",
    "EventSchedulingPolicy",
    "ScheduledEnvironmentEvent",
    "SchedulerSubmitResult",
    "scheduling_policy_for",
]
