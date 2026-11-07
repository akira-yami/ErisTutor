# -*- coding: utf-8 -*-
from __future__ import annotations

"""UI Automation client theo hướng event + cache, không poll cả desktop tree.

Snapshot đầu tiên của một HWND được cache. Sau đó WinEvent/UIA event chỉ đánh
dấu HWND/subtree bẩn; lần consumer cần dữ liệu tiếp theo mới refresh. Listener
UIA chạy trên COM MTA thread riêng theo khuyến nghị của Windows. Nếu package
``uiautomation`` không có API event tương ứng, client vẫn dùng WinEvent
invalidation + reconciliation TTL thay vì quay về scan mỗi perception tick.
"""

import ctypes
import atexit
import hashlib
import os
import queue
import threading
import time
from dataclasses import dataclass
from typing import Any

try:  # pragma: no cover - dependency Windows tùy chọn
    import uiautomation as auto  # type: ignore
except Exception:  # pragma: no cover
    auto = None  # type: ignore


@dataclass(frozen=True, slots=True)
class UIANodeSnapshot:
    runtime_id: str
    role: str
    name: str = ""
    value: str = ""
    bounds: tuple[int, int, int, int] | None = None
    is_password: bool = False
    has_focus: bool = False
    selected: bool = False
    enabled: bool = True
    automation_id: str = ""


class UIAEventClient:
    def __init__(
        self,
        *,
        max_nodes: int = 160,
        max_depth: int = 6,
        reconcile_seconds: float = 30.0,
    ) -> None:
        flag = os.getenv("ERIS_UIA_EVENTS_ENABLED", "1").strip().lower() not in {"0", "false", "off", "no"}
        self.enabled = flag and os.name == "nt" and auto is not None
        self.max_nodes = max(20, int(max_nodes))
        self.max_depth = max(1, int(max_depth))
        self.reconcile_seconds = max(5.0, float(reconcile_seconds))
        self._cache: dict[int, tuple[float, tuple[UIANodeSnapshot, ...]]] = {}
        self._dirty: set[int] = set()
        self._dirty_runtime_ids: dict[int, set[str]] = {}
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._commands: queue.Queue[tuple[str, int]] = queue.Queue()
        self._subscribed_hwnds: set[int] = set()
        self._handler_refs: list[Any] = []
        self._root_refs: dict[int, Any] = {}
        self._atexit_registered = False

    @property
    def alive(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def start(self) -> "UIAEventClient":
        with self._lock:
            if not self.enabled or self.alive:
                return self
            self._stop.clear()
            self._commands = queue.Queue()
            self._thread = threading.Thread(target=self._event_loop, name="eris-uia-mta", daemon=True)
            self._thread.start()
            if not self._atexit_registered:
                atexit.register(self.stop)
                self._atexit_registered = True
        return self

    def stop(self) -> None:
        with self._lock:
            self._stop.set()
            thread = self._thread
        if thread and thread is not threading.current_thread():
            thread.join(timeout=1.5)

    def invalidate(self, hwnd: int, *, runtime_id: str = "") -> None:
        """Đánh dấu dữ liệu bẩn; callback event không thực hiện cross-process scan."""
        hwnd = int(hwnd or 0)
        if not hwnd:
            return
        with self._lock:
            self._dirty.add(hwnd)
            if runtime_id:
                self._dirty_runtime_ids.setdefault(hwnd, set()).add(str(runtime_id))

    def cached(self, hwnd: int) -> tuple[UIANodeSnapshot, ...]:
        with self._lock:
            return self._cache.get(int(hwnd), (0.0, ()))[1]

    def subscribe_hwnd(self, hwnd: int) -> None:
        hwnd = int(hwnd or 0)
        if not self.enabled or not hwnd:
            return
        self.start()
        if hwnd not in self._subscribed_hwnds:
            self._commands.put(("subscribe", hwnd))

    def snapshot(self, hwnd: int, *, root: Any = None, force: bool = False) -> tuple[UIANodeSnapshot, ...]:
        hwnd = int(hwnd or 0)
        now = time.time()
        with self._lock:
            cached_at, cached_nodes = self._cache.get(hwnd, (0.0, ()))
            stale = bool(cached_at and now - cached_at >= self.reconcile_seconds)
            if not force and not stale and hwnd not in self._dirty and cached_nodes:
                return cached_nodes

        if root is None:
            if not self.enabled:
                return ()
            try:  # pragma: no cover - Windows E2E
                root = auto.ControlFromHandle(hwnd)
            except Exception:
                return ()
        self.subscribe_hwnd(hwnd)
        nodes = self._snapshot_root(root)
        with self._lock:
            self._cache[hwnd] = (now, nodes)
            self._dirty.discard(hwnd)
            self._dirty_runtime_ids.pop(hwnd, None)
        return nodes

    @staticmethod
    def _runtime_id(item: Any, role: str, name: str, automation_id: str) -> str:
        # Hash đủ để identity ổn định hơn bounding box nhưng không lưu raw text dài.
        raw = "|".join((automation_id, role, name[:120], str(getattr(item, "NativeWindowHandle", 0) or 0)))
        return hashlib.sha1(raw.encode("utf-8", "ignore")).hexdigest()[:18]

    def _snapshot_root(self, root: Any) -> tuple[UIANodeSnapshot, ...]:
        """Snapshot bounded subtree. Chỉ gọi khi cache miss/dirty/reconciliation."""
        nodes: list[UIANodeSnapshot] = []
        try:
            iterator = auto.WalkTree(root, maxDepth=self.max_depth) if auto is not None else ((root, 0),)
            for item, _depth in iterator:
                if len(nodes) >= self.max_nodes:
                    break
                try:
                    is_password = bool(getattr(item, "IsPassword", False))
                    role = str(getattr(item, "ControlTypeName", "") or getattr(item, "ControlType", "") or "control")
                    automation_id = str(getattr(item, "AutomationId", "") or "")
                    name = "" if is_password else str(getattr(item, "Name", "") or "").strip()
                    value = ""
                    if not is_password:
                        try:
                            pattern = item.GetValuePattern()
                            value = str(getattr(pattern, "Value", "") or "").strip()
                        except Exception:
                            pass
                    rect = getattr(item, "BoundingRectangle", None)
                    bounds = None
                    if rect is not None:
                        try:
                            left, top, right, bottom = int(rect.left), int(rect.top), int(rect.right), int(rect.bottom)
                            bounds = (left, top, max(1, right - left), max(1, bottom - top))
                        except Exception:
                            pass
                    nodes.append(UIANodeSnapshot(
                        runtime_id=self._runtime_id(item, role, name, automation_id),
                        role=role,
                        name=name,
                        value=value,
                        bounds=bounds,
                        is_password=is_password,
                        has_focus=bool(getattr(item, "HasKeyboardFocus", False)),
                        selected=bool(getattr(item, "IsSelected", False)),
                        enabled=bool(getattr(item, "IsEnabled", True)),
                        automation_id=automation_id,
                    ))
                except Exception:
                    continue
        except Exception:
            pass
        if not nodes:
            item = root
            try:
                is_password = bool(getattr(item, "IsPassword", False))
                role = str(getattr(item, "ControlTypeName", "") or getattr(item, "ControlType", "") or "control")
                automation_id = str(getattr(item, "AutomationId", "") or "")
                name = "" if is_password else str(getattr(item, "Name", "") or "").strip()
                nodes.append(UIANodeSnapshot(
                    runtime_id=self._runtime_id(item, role, name, automation_id),
                    role=role,
                    name=name,
                    is_password=is_password,
                    has_focus=bool(getattr(item, "HasKeyboardFocus", False)),
                    selected=bool(getattr(item, "IsSelected", False)),
                    enabled=bool(getattr(item, "IsEnabled", True)),
                    automation_id=automation_id,
                ))
            except Exception:
                return ()
        return tuple(nodes)

    @staticmethod
    def _hwnd_from_event_args(args: tuple[Any, ...]) -> int:
        """Cố lấy top-level HWND từ callback signature khác nhau của uiautomation."""
        for value in args:
            try:
                hwnd = int(getattr(value, "NativeWindowHandle", 0) or 0)
                if hwnd:
                    return hwnd
                top = value.GetTopLevelControl() if callable(getattr(value, "GetTopLevelControl", None)) else None
                hwnd = int(getattr(top, "NativeWindowHandle", 0) or 0)
                if hwnd:
                    return hwnd
            except Exception:
                continue
        return 0

    def _make_event_handler(self, fixed_hwnd: int = 0):
        def handler(*args: Any) -> None:
            hwnd = int(fixed_hwnd or self._hwnd_from_event_args(args))
            if hwnd:
                self.invalidate(hwnd)
        return handler

    def _try_subscribe_root(self, hwnd: int) -> None:  # pragma: no cover - Windows E2E
        if auto is None or hwnd in self._subscribed_hwnds:
            return
        try:
            root = auto.ControlFromHandle(hwnd)
        except Exception:
            return
        self._root_refs[hwnd] = root
        handler = self._make_event_handler(hwnd)
        registered = False

        # API package uiautomation thay đổi nhẹ giữa các bản. Ta feature-detect
        # thay vì hard fail cả sensor nếu một handler không tồn tại.
        try:
            add_structure = getattr(auto, "AddStructureChangedEventHandler", None)
            if callable(add_structure):
                scope = getattr(auto, "TreeScope_Subtree", None)
                if scope is None and hasattr(auto, "TreeScope"):
                    scope = getattr(auto.TreeScope, "Subtree", None)
                add_structure(root, scope, handler)
                self._handler_refs.append(("structure", root, handler))
                registered = True
        except Exception:
            pass

        try:
            add_prop = getattr(auto, "AddAutomationPropertyChangedEventHandler", None)
            if callable(add_prop):
                scope = getattr(auto, "TreeScope_Subtree", None)
                if scope is None and hasattr(auto, "TreeScope"):
                    scope = getattr(auto.TreeScope, "Subtree", None)
                props: list[int] = []
                prop_enum = getattr(auto, "PropertyId", None)
                for name in ("NameProperty", "ValueValueProperty", "SelectionItemIsSelectedProperty", "HasKeyboardFocusProperty"):
                    value = getattr(prop_enum, name, None) if prop_enum is not None else None
                    if value is not None:
                        props.append(int(value))
                if props:
                    add_prop(root, scope, handler, props)
                    self._handler_refs.append(("property", root, handler))
                    registered = True
        except Exception:
            pass

        # Dù package không expose handler phù hợp, đánh dấu subscribed để không
        # spam registration. WinEvent broker + reconcile TTL vẫn giữ tính đúng.
        self._subscribed_hwnds.add(hwnd)
        if registered:
            self._handler_refs.append(("root", root, handler))

    def _event_loop(self) -> None:  # pragma: no cover - Windows E2E
        ole32 = ctypes.windll.ole32
        COINIT_MULTITHREADED = 0x0
        try:
            ole32.CoInitializeEx(None, COINIT_MULTITHREADED)
        except Exception:
            pass

        focus_handler = self._make_event_handler(0)
        focus_registered = False
        try:
            add_focus = getattr(auto, "AddFocusChangedEventHandler", None) if auto is not None else None
            if callable(add_focus):
                add_focus(focus_handler)
                focus_registered = True
                self._handler_refs.append(("focus", None, focus_handler))
        except Exception:
            pass

        try:
            while not self._stop.is_set():
                try:
                    command, hwnd = self._commands.get(timeout=0.20)
                except queue.Empty:
                    continue
                if command == "stop":
                    break
                if command == "subscribe":
                    self._try_subscribe_root(hwnd)
        finally:
            # Remove handlers chỉ từ chính MTA thread đã đăng ký chúng.
            try:
                if focus_registered:
                    remove_focus = getattr(auto, "RemoveFocusChangedEventHandler", None)
                    if callable(remove_focus):
                        remove_focus(focus_handler)
            except Exception:
                pass
            for kind, root, handler in reversed(self._handler_refs):
                try:
                    if kind == "structure":
                        remove = getattr(auto, "RemoveStructureChangedEventHandler", None)
                        if callable(remove):
                            remove(root, handler)
                    elif kind == "property":
                        remove = getattr(auto, "RemoveAutomationPropertyChangedEventHandler", None)
                        if callable(remove):
                            remove(root, handler)
                except Exception:
                    pass
            self._handler_refs.clear()
            self._subscribed_hwnds.clear()
            self._root_refs.clear()
            try:
                ole32.CoUninitialize()
            except Exception:
                pass


__all__ = ["UIAEventClient", "UIANodeSnapshot"]
