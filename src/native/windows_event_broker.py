from __future__ import annotations

"""Low-overhead SetWinEventHook broker.

The callback emits only typed metadata. Accessibility content is fetched later
by the UIA/Chromium adapters after privacy policy has approved the surface.
"""

import ctypes
import atexit
import os
import threading
import time
from ctypes import wintypes
from typing import Callable

from runtime.ai_orchestrated.environment_event_bus import NormalizedEnvironmentEvent
from .process_resolver import ProcessResolver

EVENT_SYSTEM_FOREGROUND = 0x0003
EVENT_OBJECT_SHOW = 0x8002
EVENT_OBJECT_HIDE = 0x8003
EVENT_OBJECT_REORDER = 0x8004
EVENT_OBJECT_FOCUS = 0x8005
EVENT_OBJECT_SELECTION = 0x8006
EVENT_OBJECT_NAMECHANGE = 0x800C
EVENT_OBJECT_VALUECHANGE = 0x800E
WINEVENT_OUTOFCONTEXT = 0x0000
WINEVENT_SKIPOWNPROCESS = 0x0002
OBJID_WINDOW = 0x00000000

_EVENT_NAMES = {
    EVENT_SYSTEM_FOREGROUND: "foreground_changed",
    EVENT_OBJECT_SHOW: "object_shown",
    EVENT_OBJECT_HIDE: "object_hidden",
    EVENT_OBJECT_REORDER: "structure_reordered",
    EVENT_OBJECT_FOCUS: "focus_changed",
    EVENT_OBJECT_SELECTION: "selection_changed",
    EVENT_OBJECT_NAMECHANGE: "name_changed",
    EVENT_OBJECT_VALUECHANGE: "value_changed",
}
_TERMINAL_PROCESSES = {"windowsterminal.exe", "cmd.exe", "powershell.exe", "pwsh.exe"}
_MECHANICAL_RAW_KINDS = {"name_changed", "value_changed"}


class WindowsEventBroker:
    def __init__(self, callback: Callable[[NormalizedEnvironmentEvent], None], *, resolver: ProcessResolver | None = None, coalesce_ms: int = 120) -> None:
        self.callback = callback
        self.resolver = resolver or ProcessResolver()
        self.coalesce_ms = max(0, int(coalesce_ms))
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._hooks: list[int] = []
        self._callback_ref = None
        self._last: dict[tuple[int, int, int, int], float] = {}
        self._atexit_registered = False

    @property
    def available(self) -> bool:
        return os.name == "nt"

    @property
    def alive(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def start(self) -> "WindowsEventBroker":
        if not self.available or self.alive:
            return self
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="eris-native-winevent", daemon=True)
        self._thread.start()
        if not self._atexit_registered:
            atexit.register(self.stop)
            self._atexit_registered = True
        return self

    def stop(self) -> None:
        self._stop.set()
        if os.name == "nt":
            try:
                ctypes.windll.user32.PostThreadMessageW(getattr(self, "_thread_id", 0), 0x0012, 0, 0)
            except Exception:
                pass
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=1.5)

    def emit_raw(
        self,
        event: int,
        hwnd: int,
        object_id: int = 0,
        child_id: int = 0,
        *,
        timestamp: float | None = None,
        source_delay_ms: float | None = None,
    ) -> NormalizedEnvironmentEvent | None:
        """Injection seam cho test và WinEvent hook.

        ``source_delay_ms`` lấy từ ``dwmsEventTime`` của WinEvent callback. Khi có
        giá trị này ta lùi wall timestamp về thời điểm Windows phát event, nhờ đó
        metric source->ingress không còn đoán thời điểm source bằng lúc callback
        tình cờ được Python xử lý.
        """
        if timestamp is None:
            delay_ms = max(0.0, min(60_000.0, float(source_delay_ms or 0.0)))
            timestamp = time.time() - delay_ms / 1000.0
        else:
            timestamp = float(timestamp)
        if event not in _EVENT_NAMES or not hwnd:
            return None
        signature = (int(event), int(hwnd), int(object_id), int(child_id))
        last = self._last.get(signature, -1e18)
        now_ms = timestamp * 1000.0
        if self.coalesce_ms and now_ms - last < self.coalesce_ms:
            return None
        self._last[signature] = now_ms
        identity = self.resolver.resolve(hwnd)
        # WINEVENT_SKIPOWNPROCESS xử lý đường hook thật. Check PID thêm ở đây để
        # injection/reconciliation path cũng không tự quan sát overlay của Eris.
        if int(identity.pid or 0) == int(os.getpid()):
            return None
        raw_kind = _EVENT_NAMES[event]
        process_name = str(identity.process_name or "").lower()
        kind = raw_kind
        semantic = True
        level = 1
        importance = 0.18
        user_relevance = 0.55
        if raw_kind in _MECHANICAL_RAW_KINDS:
            semantic = False
            level, importance, user_relevance = 0, 0.02, 0.08
        if process_name == "taskmgr.exe" and raw_kind in {"name_changed", "value_changed", "object_shown", "object_hidden"}:
            kind = "counter_update"
            semantic = False
            level, importance, user_relevance = 0, 0.0, 0.06
        elif process_name in _TERMINAL_PROCESSES and raw_kind in {"name_changed", "value_changed", "object_shown", "object_hidden", "structure_reordered"}:
            kind = "scroll_churn"
            semantic = False
            level, importance, user_relevance = 0, 0.0, 0.08
        normalized = NormalizedEnvironmentEvent(
            timestamp=timestamp,
            source="winevent",
            event_type=kind,
            action=raw_kind,
            process_name=identity.process_name,
            pid=identity.pid,
            hwnd=int(hwnd),
            window_class=identity.window_class,
            region_id=f"{identity.process_name or 'process'}:{identity.pid}:hwnd_{int(hwnd)}:winevent:{object_id}:{child_id}",
            role="window" if object_id == OBJID_WINDOW else "accessible_object",
            semantic=semantic,
            source_confidence=0.98,
            novelty=0.55,
            importance=importance,
            user_relevance=user_relevance,
            confidence=0.96,
            attention_level=level,
            evidence_refs=(f"winevent:{event}:{hwnd}:{object_id}:{child_id}",),
            metadata={
                "event_code": int(event),
                "object_id": int(object_id),
                "child_id": int(child_id),
                "raw_event_type": raw_kind,
                "source_callback_delay_ms": max(0.0, float(source_delay_ms or 0.0)),
            },
        )
        try:
            self.callback(normalized)
        except Exception:
            return normalized
        return normalized

    def _run(self) -> None:  # pragma: no cover - Windows E2E path
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        kernel32.GetCurrentThreadId.restype = wintypes.DWORD
        user32.PostThreadMessageW.argtypes = [wintypes.DWORD, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
        user32.PostThreadMessageW.restype = wintypes.BOOL
        self._thread_id = int(kernel32.GetCurrentThreadId())
        WinEventProcType = ctypes.WINFUNCTYPE(
            None,
            wintypes.HANDLE,
            wintypes.DWORD,
            wintypes.HWND,
            wintypes.LONG,
            wintypes.LONG,
            wintypes.DWORD,
            wintypes.DWORD,
        )
        user32.SetWinEventHook.argtypes = [
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.HMODULE,
            WinEventProcType,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.DWORD,
        ]
        user32.SetWinEventHook.restype = wintypes.HANDLE
        user32.UnhookWinEvent.argtypes = [wintypes.HANDLE]
        user32.UnhookWinEvent.restype = wintypes.BOOL
        user32.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT]
        user32.GetMessageW.restype = wintypes.BOOL
        user32.TranslateMessage.argtypes = [ctypes.POINTER(wintypes.MSG)]
        user32.TranslateMessage.restype = wintypes.BOOL
        user32.DispatchMessageW.argtypes = [ctypes.POINTER(wintypes.MSG)]
        user32.DispatchMessageW.restype = wintypes.LPARAM

        kernel32.GetTickCount.restype = wintypes.DWORD

        def callback(_hook, event, hwnd, object_id, child_id, _thread, event_time):
            del _hook, _thread
            try:
                # WinEvent dwmsEventTime dùng tick-count 32-bit. Phép trừ modulo
                # giữ đúng cả khi counter wrap; giá trị phi lý >60s bị emit_raw clamp.
                source_delay_ms = 0.0
                if int(event_time or 0) > 0:
                    tick_now = int(kernel32.GetTickCount())
                    source_delay_ms = float((tick_now - int(event_time)) & 0xFFFFFFFF)
                self.emit_raw(
                    int(event),
                    int(hwnd or 0),
                    int(object_id),
                    int(child_id),
                    source_delay_ms=source_delay_ms,
                )
            except Exception:
                pass

        self._callback_ref = WinEventProcType(callback)
        flags = WINEVENT_OUTOFCONTEXT | WINEVENT_SKIPOWNPROCESS
        ranges = [
            (EVENT_SYSTEM_FOREGROUND, EVENT_SYSTEM_FOREGROUND),
            (EVENT_OBJECT_SHOW, EVENT_OBJECT_VALUECHANGE),
        ]
        for minimum, maximum in ranges:
            hook = user32.SetWinEventHook(minimum, maximum, 0, self._callback_ref, 0, 0, flags)
            if hook:
                self._hooks.append(int(hook))
        msg = wintypes.MSG()
        try:
            while not self._stop.is_set() and user32.GetMessageW(ctypes.byref(msg), 0, 0, 0) > 0:
                user32.TranslateMessage(ctypes.byref(msg))
                user32.DispatchMessageW(ctypes.byref(msg))
        finally:
            for hook in self._hooks:
                try:
                    user32.UnhookWinEvent(hook)
                except Exception:
                    pass
            self._hooks.clear()


__all__ = ["WindowsEventBroker"] + [name for name in globals() if name.startswith("EVENT_")]
