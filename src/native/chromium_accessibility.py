# -*- coding: utf-8 -*-
from __future__ import annotations

"""Chromium accessibility adapter không cần extension.

Đường ưu tiên là IA2/MSAA native. UI Automation chỉ là fallback khi IA2 không
khả dụng. Adapter chỉ xuất semantic accessibility (tab, document, control),
không giả vờ đây là DOM/CDP và không đọc cookie/network/JavaScript state.
"""

import ctypes
import os
import time
from dataclasses import dataclass
from urllib.parse import urlparse
from typing import Any

from runtime.ai_orchestrated.environment_event_bus import NormalizedEnvironmentEvent
from .ia2_accessibility import IA2AccessibilityBackend, IA2NodeSnapshot
from .process_resolver import ProcessResolver
from .uia_event_client import UIAEventClient, UIANodeSnapshot


_CHROMIUM_PROCESSES = {"chrome.exe", "msedge.exe", "brave.exe", "chromium.exe"}


@dataclass(frozen=True, slots=True)
class ChromiumTab:
    title: str
    selected: bool = False
    enabled: bool = True
    bounds: tuple[int, int, int, int] | None = None


@dataclass(frozen=True, slots=True)
class ChromiumWindowSnapshot:
    hwnd: int
    pid: int
    process_name: str
    tabs: tuple[ChromiumTab, ...] = ()
    selected_tab: str = ""
    document_title: str = ""
    active_domain: str = ""
    provider: str = "unknown"


class ChromiumAccessibilityAdapter:
    def __init__(
        self,
        *,
        resolver: ProcessResolver | None = None,
        uia: UIAEventClient | None = None,
        ia2: IA2AccessibilityBackend | None = None,
    ) -> None:
        self.resolver = resolver or ProcessResolver()
        self.uia = uia or UIAEventClient(max_nodes=260, max_depth=8)
        self.ia2 = ia2 or IA2AccessibilityBackend(max_nodes=320, max_depth=9)
        self._last_selected: dict[int, str] = {}
        self._last_titles: dict[int, tuple[str, ...]] = {}
        self._last_provider: dict[int, str] = {}
        self._dirty_hwnds: set[int] = set()

    def start(self) -> "ChromiumAccessibilityAdapter":
        """Bật listener UIA nếu dependency hỗ trợ; IA2 vẫn là đường đọc ưu tiên."""
        try:
            self.uia.start()
        except Exception:
            pass
        return self

    def stop(self) -> None:
        try:
            self.uia.stop()
        except Exception:
            pass

    def notify_native_event(self, event: NormalizedEnvironmentEvent) -> None:
        """WinEvent/UIA event chỉ đánh dấu subtree bẩn, không scan cây ngay callback."""
        hwnd = int(event.hwnd or 0)
        if not hwnd:
            return
        if str(event.event_type or "") in {
            "foreground_changed", "focus_changed", "selection_changed", "name_changed",
            "value_changed", "structure_reordered", "object_shown", "object_hidden",
        }:
            self._dirty_hwnds.add(hwnd)
            self.uia.invalidate(hwnd)

    def discover_windows(self) -> tuple[int, ...]:
        if os.name != "nt":
            return ()
        found: list[int] = []
        try:  # pragma: no cover - Windows E2E
            user32 = ctypes.windll.user32
            PROC = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)

            def cb(hwnd, _):
                identity = self.resolver.resolve(int(hwnd))
                if identity.process_name.lower() in _CHROMIUM_PROCESSES and user32.IsWindowVisible(hwnd):
                    found.append(int(hwnd))
                return True

            user32.EnumWindows(PROC(cb), 0)
        except Exception:
            return ()
        return tuple(dict.fromkeys(found))

    @staticmethod
    def _role_text(node: Any) -> str:
        return str(getattr(node, "role", "") or "").strip().lower().replace("_", " ")

    @classmethod
    def _is_tab(cls, node: Any) -> bool:
        role = cls._role_text(node)
        return "tabitem" in role or "tab item" in role or role in {"tab", "page tab"} or role.endswith(" page tab")

    @classmethod
    def _is_document(cls, node: Any) -> bool:
        role = cls._role_text(node)
        return "document" in role or role in {"pane", "client"}

    @staticmethod
    def _safe_name(node: Any) -> str:
        # Chromium tab title/document title là semantic metadata. Password value
        # tuyệt đối không được đẩy vào adapter này.
        if bool(getattr(node, "is_password", False)):
            return ""
        return str(getattr(node, "name", "") or "").strip()

    @staticmethod
    def _domain_from_candidate(value: str) -> str:
        raw = str(value or "").strip()
        if not raw or " " in raw:
            return ""
        if "://" not in raw and "." in raw:
            raw = "https://" + raw
        try:
            host = (urlparse(raw).hostname or "").lower().strip(".")
            if host and "." in host:
                return host[:180]
        except Exception:
            pass
        return ""

    @classmethod
    def _looks_like_omnibox(cls, node: Any) -> bool:
        role = cls._role_text(node)
        name = str(getattr(node, "name", "") or "").lower()
        automation_id = str(getattr(node, "automation_id", "") or "").lower()
        return (
            any(token in role for token in ("edit", "editable", "text"))
            and (
                any(token in name for token in ("address", "search bar", "omnibox", "địa chỉ"))
                or any(token in automation_id for token in ("address", "omnibox"))
            )
        )

    def _from_nodes(
        self,
        hwnd: int,
        pid: int,
        process_name: str,
        nodes: tuple[Any, ...],
        *,
        provider: str,
    ) -> ChromiumWindowSnapshot:
        tabs: list[ChromiumTab] = []
        document_title = ""
        active_domain = ""
        for node in nodes:
            name = self._safe_name(node)
            if not active_domain and self._looks_like_omnibox(node) and not bool(getattr(node, "is_password", False)):
                # Chỉ giữ domain; query/path/raw omnibox value không đi vào event bus.
                active_domain = self._domain_from_candidate(str(getattr(node, "value", "") or ""))
            if self._is_tab(node) and name:
                tabs.append(ChromiumTab(
                    title=name,
                    selected=bool(getattr(node, "selected", False) or getattr(node, "has_focus", False)),
                    enabled=bool(getattr(node, "enabled", True)),
                    bounds=getattr(node, "bounds", None),
                ))
            elif not document_title and self._is_document(node) and name:
                document_title = name
        selected = next((tab.title for tab in tabs if tab.selected), "")
        return ChromiumWindowSnapshot(
            hwnd=int(hwnd),
            pid=int(pid),
            process_name=process_name,
            tabs=tuple(tabs),
            selected_tab=selected,
            document_title=document_title,
            active_domain=active_domain,
            provider=provider,
        )

    def snapshot_window(self, hwnd: int, *, force: bool = False) -> ChromiumWindowSnapshot | None:
        identity = self.resolver.resolve(hwnd)
        if identity.process_name.lower() not in _CHROMIUM_PROCESSES:
            return None

        # IA2-first đúng theo tài liệu. Chỉ khi provider/COM không khả dụng mới
        # rơi về UIA. IA2 tree ở đây vẫn là accessibility tree, không phải DOM.
        ia2_nodes: tuple[IA2NodeSnapshot, ...] = ()
        ia2_confirmed = False
        try:
            ia2_nodes, ia2_confirmed = self.ia2.snapshot(hwnd)
        except Exception:
            ia2_nodes, ia2_confirmed = (), False
        if ia2_nodes and ia2_confirmed:
            self._dirty_hwnds.discard(int(hwnd))
            return self._from_nodes(hwnd, identity.pid, identity.process_name, ia2_nodes, provider="ia2")

        uia_nodes: tuple[UIANodeSnapshot, ...] = self.uia.snapshot(
            hwnd,
            force=bool(force or int(hwnd) in self._dirty_hwnds),
        )
        self._dirty_hwnds.discard(int(hwnd))
        return self._from_nodes(hwnd, identity.pid, identity.process_name, uia_nodes, provider="uia")

    def diff_events(self, snapshot: ChromiumWindowSnapshot, *, now: float | None = None) -> tuple[NormalizedEnvironmentEvent, ...]:
        now = time.time() if now is None else float(now)
        events: list[NormalizedEnvironmentEvent] = []
        old_selected = self._last_selected.get(snapshot.hwnd, "")
        titles = tuple(tab.title for tab in snapshot.tabs)
        old_titles = self._last_titles.get(snapshot.hwnd, ())
        source = "chromium_ia2" if snapshot.provider == "ia2" else "chromium_uia"
        source_confidence = 0.96 if snapshot.provider == "ia2" else 0.91

        if snapshot.selected_tab and snapshot.selected_tab != old_selected:
            events.append(NormalizedEnvironmentEvent(
                timestamp=now,
                source=source,
                event_type="browser_tab_activated",
                action="selection_changed",
                process_name=snapshot.process_name,
                pid=snapshot.pid,
                hwnd=snapshot.hwnd,
                region_id=f"{snapshot.process_name}:{snapshot.pid}:hwnd_{snapshot.hwnd}:tab_strip",
                role="tab",
                title=snapshot.selected_tab,
                source_confidence=source_confidence,
                confidence=source_confidence,
                novelty=0.75,
                importance=0.28,
                user_relevance=0.72,
                attention_level=1,
                evidence_refs=(f"{source}:tab:selected:{snapshot.hwnd}",),
                metadata={"provider": snapshot.provider, "tab_count": len(titles), "active_domain": snapshot.active_domain},
            ))

        if old_titles and titles != old_titles:
            changed = next((title for title in titles if title not in old_titles), "")
            events.append(NormalizedEnvironmentEvent(
                timestamp=now,
                source=source,
                event_type="browser_tab_title_changed",
                action="name_changed",
                process_name=snapshot.process_name,
                pid=snapshot.pid,
                hwnd=snapshot.hwnd,
                region_id=f"{snapshot.process_name}:{snapshot.pid}:hwnd_{snapshot.hwnd}:tab_strip",
                role="tab",
                title=changed,
                source_confidence=max(0.86, source_confidence - 0.03),
                confidence=max(0.86, source_confidence - 0.03),
                novelty=0.55,
                importance=0.18,
                user_relevance=0.5,
                attention_level=1,
                evidence_refs=(f"{source}:tab:topology:{snapshot.hwnd}",),
                metadata={"tab_count": len(titles), "provider": snapshot.provider, "active_domain": snapshot.active_domain},
            ))

        previous_provider = self._last_provider.get(snapshot.hwnd, "")
        if previous_provider and previous_provider != snapshot.provider:
            events.append(NormalizedEnvironmentEvent(
                timestamp=now,
                source=source,
                event_type="browser_accessibility_provider_changed",
                action="provider_changed",
                process_name=snapshot.process_name,
                pid=snapshot.pid,
                hwnd=snapshot.hwnd,
                role="browser_window",
                source_confidence=0.98,
                confidence=0.98,
                novelty=0.2,
                importance=0.05,
                user_relevance=0.1,
                attention_level=0,
                metadata={"provider": snapshot.provider, "previous_provider": previous_provider},
            ))

        self._last_selected[snapshot.hwnd] = snapshot.selected_tab
        self._last_titles[snapshot.hwnd] = titles
        self._last_provider[snapshot.hwnd] = snapshot.provider
        return tuple(events)

    def poll_window(
        self,
        hwnd: int,
        *,
        force: bool = False,
        now: float | None = None,
    ) -> tuple[NormalizedEnvironmentEvent, ...]:
        """Refresh đúng Chromium HWND gây event.

        Đường event-triggered V5 tuyệt đối không ``EnumWindows -> snapshot mọi
        browser window`` cho mỗi selection/name change. Native WinEvent đã cho
        ta HWND, nên đọc đúng cửa sổ đó là đủ. ``poll_all`` chỉ còn là safety-net
        reconciliation định kỳ.
        """
        hwnd = int(hwnd or 0)
        if not hwnd:
            return ()
        snapshot = self.snapshot_window(hwnd, force=force)
        if snapshot is None:
            return ()
        return self.diff_events(snapshot, now=now)

    def poll_all(self, *, force: bool = False) -> tuple[NormalizedEnvironmentEvent, ...]:
        """Reconciliation toàn bộ Chromium windows, không dùng cho hot path."""
        events: list[NormalizedEnvironmentEvent] = []
        for hwnd in self.discover_windows():
            events.extend(self.poll_window(hwnd, force=force))
        return tuple(events)


__all__ = ["ChromiumAccessibilityAdapter", "ChromiumTab", "ChromiumWindowSnapshot"]
