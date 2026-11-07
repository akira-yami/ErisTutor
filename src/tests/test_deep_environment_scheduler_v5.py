# -*- coding: utf-8 -*-
"""Regression tests cho ingress scheduler V5 của Deep Environment."""
from __future__ import annotations

import time

from runtime.ai_orchestrated.environment_event_bus import NormalizedEnvironmentEvent
from runtime.ai_orchestrated.environment_ingress_scheduler import (
    EnrichmentMode,
    EnvironmentIngressScheduler,
    EnvironmentPriority,
    scheduling_policy_for,
)


def _event(
    event_type: str,
    *,
    hwnd: int = 100,
    process: str = "Code.exe",
    source: str = "winevent",
    level: int = 1,
    semantic: bool = True,
    marker: int = 0,
) -> NormalizedEnvironmentEvent:
    return NormalizedEnvironmentEvent(
        timestamp=time.time(),
        source=source,
        event_type=event_type,
        process_name=process,
        pid=42,
        hwnd=hwnd,
        region_id=f"{process}:42:hwnd_{hwnd}:region",
        role="control",
        semantic=semantic,
        attention_level=level,
        metadata={"marker": marker},
    )


def _submit(scheduler: EnvironmentIngressScheduler, event: NormalizedEnvironmentEvent):
    now_ns = time.monotonic_ns()
    return scheduler.submit(event, ingress_monotonic_ns=now_ns, ingress_wall_time=time.time())


def test_scheduler_critical_vuot_qua_background_fifo() -> None:
    """Foreground đến sau vẫn phải được lấy trước structural churn đến trước."""
    scheduler = EnvironmentIngressScheduler(max_pending=64)
    _submit(scheduler, _event("structure_reordered", level=1))
    _submit(scheduler, _event("foreground_changed", level=1))

    first = scheduler.get(timeout=0.01)
    second = scheduler.get(timeout=0.01)
    assert first is not None and first.event.event_type == "foreground_changed"
    assert first.policy.priority == EnvironmentPriority.CRITICAL
    assert second is not None and second.event.event_type == "structure_reordered"


def test_scheduler_coalesce_storm_latest_wins() -> None:
    """100 reorder cùng HWND chỉ để lại state mới nhất trong pending queue."""
    scheduler = EnvironmentIngressScheduler(max_pending=64)
    for marker in range(100):
        result = _submit(scheduler, _event("structure_reordered", marker=marker))
        assert result.accepted

    assert scheduler.qsize == 1
    item = scheduler.get(timeout=0.01)
    assert item is not None
    assert item.event.metadata["marker"] == 99
    assert scheduler.stats().get("coalesced", 0) >= 99


def test_scheduler_backpressure_loai_background_truoc_critical() -> None:
    """Queue đầy không được hy sinh foreground để giữ background cũ."""
    scheduler = EnvironmentIngressScheduler(max_pending=64)
    for index in range(64):
        _submit(scheduler, _event("structure_reordered", hwnd=1000 + index, marker=index))

    result = _submit(scheduler, _event("foreground_changed", hwnd=9999))
    assert result.accepted is True
    assert result.evicted is True
    assert scheduler.qsize == 64
    first = scheduler.get(timeout=0.01)
    assert first is not None and first.event.event_type == "foreground_changed"


def test_priority_khong_dong_nghia_attention() -> None:
    """Event level 0 vẫn có thể CRITICAL nếu cần xử lý privacy ngay."""
    event = _event("password_control_focused", level=0, semantic=False, source="uia")
    policy = scheduling_policy_for(event)
    assert policy.priority == EnvironmentPriority.CRITICAL
    assert event.attention_level == 0


def test_chromium_structural_storm_khong_goi_generic_uia_enrichment() -> None:
    """Chromium raw show/hide/reorder đi qua adapter IA2 riêng, không enrich UIA kép."""
    event = _event(
        "object_shown",
        process="chrome.exe",
        source="winevent",
        level=1,
    )
    policy = scheduling_policy_for(event)
    assert policy.priority == EnvironmentPriority.BACKGROUND
    assert policy.enrichment == EnrichmentMode.NONE
    assert policy.chromium_refresh is False


def test_chromium_selection_la_critical_va_trigger_refresh() -> None:
    event = _event("selection_changed", process="msedge.exe", source="winevent", level=1)
    policy = scheduling_policy_for(event)
    assert policy.priority == EnvironmentPriority.CRITICAL
    assert policy.chromium_refresh is True
    assert policy.enrichment == EnrichmentMode.NONE


def test_chromium_poll_window_khong_discover_toan_bo_windows() -> None:
    """Hot path phải refresh đúng HWND, không EnumWindows mỗi native trigger."""
    from native.chromium_accessibility import ChromiumAccessibilityAdapter, ChromiumWindowSnapshot

    class Adapter(ChromiumAccessibilityAdapter):
        def __init__(self):
            # Không gọi dependency Windows trong unit test.
            self._last_selected = {}
            self._last_titles = {}
            self._last_provider = {}
            self._dirty_hwnds = set()
            self.snapshot_calls = []

        def discover_windows(self):  # pragma: no cover - phải không được gọi
            raise AssertionError("poll_window không được discover toàn desktop")

        def snapshot_window(self, hwnd, *, force=False):
            self.snapshot_calls.append((hwnd, force))
            return ChromiumWindowSnapshot(
                hwnd=int(hwnd),
                pid=7,
                process_name="chrome.exe",
                selected_tab="Docs",
                provider="ia2",
            )

    adapter = Adapter()
    events = adapter.poll_window(777, force=True, now=123.0)
    assert adapter.snapshot_calls == [(777, True)]
    assert events and events[0].event_type == "browser_tab_activated"
    assert events[0].timestamp == 123.0
