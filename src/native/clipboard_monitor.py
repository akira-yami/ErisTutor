from __future__ import annotations

"""Metadata-first clipboard change monitor.

The core event intentionally carries formats and a coarse size bucket only. It
never reads or persists clipboard plaintext by default.
"""

import ctypes
import atexit
import os
import threading
import time
from ctypes import wintypes
from typing import Callable

from runtime.ai_orchestrated.environment_event_bus import NormalizedEnvironmentEvent

WM_CLIPBOARDUPDATE = 0x031D
WM_DESTROY = 0x0002


class ClipboardMonitor:
    def __init__(self, callback: Callable[[NormalizedEnvironmentEvent], None]) -> None:
        self.callback = callback
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._atexit_registered = False

    @property
    def available(self) -> bool:
        return os.name == "nt"

    def start(self) -> "ClipboardMonitor":
        if not self.available or (self._thread and self._thread.is_alive()):
            return self
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="eris-clipboard-monitor", daemon=True)
        self._thread.start()
        if not self._atexit_registered:
            atexit.register(self.stop)
            self._atexit_registered = True
        return self

    def stop(self) -> None:
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=1.0)

    def emit_metadata(self, *, formats: tuple[int, ...] = (), size_bucket: str = "unknown", now: float | None = None) -> NormalizedEnvironmentEvent:
        event = NormalizedEnvironmentEvent(
            timestamp=time.time() if now is None else float(now), source="clipboard_listener", event_type="clipboard_changed",
            action="clipboard_update", semantic=True, source_confidence=0.99, confidence=0.99,
            novelty=0.45, importance=0.12, user_relevance=0.38, attention_level=1, privacy_scope="METADATA",
            metadata={"formats": tuple(int(x) for x in formats[:16]), "size_bucket": size_bucket},
            evidence_refs=("clipboard:wm_clipboardupdate",),
        )
        self.callback(event)
        return event

    def _run(self) -> None:  # pragma: no cover - Windows E2E
        # Message-only window is enough to receive WM_CLIPBOARDUPDATE.
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        WNDPROC = ctypes.WINFUNCTYPE(wintypes.LPARAM, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)

        class WNDCLASS(ctypes.Structure):
            _fields_ = [("style", wintypes.UINT), ("lpfnWndProc", WNDPROC), ("cbClsExtra", ctypes.c_int), ("cbWndExtra", ctypes.c_int),
                        ("hInstance", wintypes.HINSTANCE), ("hIcon", wintypes.HICON), ("hCursor", wintypes.HANDLE), ("hbrBackground", wintypes.HBRUSH),
                        ("lpszMenuName", wintypes.LPCWSTR), ("lpszClassName", wintypes.LPCWSTR)]

        kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
        kernel32.GetModuleHandleW.restype = wintypes.HMODULE
        user32.RegisterClassW.argtypes = [ctypes.POINTER(WNDCLASS)]
        user32.RegisterClassW.restype = wintypes.ATOM
        user32.UnregisterClassW.argtypes = [wintypes.LPCWSTR, wintypes.HINSTANCE]
        user32.UnregisterClassW.restype = wintypes.BOOL
        user32.CreateWindowExW.argtypes = [
            wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
            ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
            wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, ctypes.c_void_p,
        ]
        user32.CreateWindowExW.restype = wintypes.HWND
        user32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
        user32.DefWindowProcW.restype = wintypes.LPARAM
        user32.AddClipboardFormatListener.argtypes = [wintypes.HWND]
        user32.AddClipboardFormatListener.restype = wintypes.BOOL
        user32.RemoveClipboardFormatListener.argtypes = [wintypes.HWND]
        user32.RemoveClipboardFormatListener.restype = wintypes.BOOL
        user32.DestroyWindow.argtypes = [wintypes.HWND]
        user32.DestroyWindow.restype = wintypes.BOOL
        user32.PeekMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT, wintypes.UINT]
        user32.PeekMessageW.restype = wintypes.BOOL
        user32.TranslateMessage.argtypes = [ctypes.POINTER(wintypes.MSG)]
        user32.TranslateMessage.restype = wintypes.BOOL
        user32.DispatchMessageW.argtypes = [ctypes.POINTER(wintypes.MSG)]
        user32.DispatchMessageW.restype = wintypes.LPARAM
        user32.PostQuitMessage.argtypes = [ctypes.c_int]
        user32.EnumClipboardFormats.argtypes = [wintypes.UINT]
        user32.EnumClipboardFormats.restype = wintypes.UINT
        user32.OpenClipboard.argtypes = [wintypes.HWND]
        user32.OpenClipboard.restype = wintypes.BOOL
        user32.CloseClipboard.argtypes = []
        user32.CloseClipboard.restype = wintypes.BOOL

        def proc(hwnd, msg, wparam, lparam):
            if msg == WM_CLIPBOARDUPDATE:
                formats: list[int] = []
                if user32.OpenClipboard(hwnd):
                    try:
                        fmt = 0
                        while len(formats) < 16:
                            fmt = int(user32.EnumClipboardFormats(fmt) or 0)
                            if not fmt:
                                break
                            formats.append(fmt)
                    finally:
                        user32.CloseClipboard()
                try:
                    self.emit_metadata(formats=tuple(formats))
                except Exception:
                    pass
                return 0
            if msg == WM_DESTROY:
                user32.PostQuitMessage(0)
                return 0
            return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

        proc_ref = WNDPROC(proc)
        class_name = f"ErisClipboardMetadataWindow-{id(self):x}"
        wc = WNDCLASS()
        wc.lpfnWndProc = proc_ref
        wc.hInstance = kernel32.GetModuleHandleW(None)
        wc.lpszClassName = class_name
        atom = user32.RegisterClassW(ctypes.byref(wc))
        if not atom:
            return
        HWND_MESSAGE = -3
        hwnd = None
        registered = False
        try:
            hwnd = user32.CreateWindowExW(0, class_name, class_name, 0, 0, 0, 0, 0, HWND_MESSAGE, 0, wc.hInstance, 0)
            if not hwnd or not user32.AddClipboardFormatListener(hwnd):
                return
            registered = True
            msg = wintypes.MSG()
            while not self._stop.is_set():
                while user32.PeekMessageW(ctypes.byref(msg), hwnd, 0, 0, 1):
                    user32.TranslateMessage(ctypes.byref(msg))
                    user32.DispatchMessageW(ctypes.byref(msg))
                self._stop.wait(0.08)
        finally:
            if registered:
                user32.RemoveClipboardFormatListener(hwnd)
            if hwnd:
                user32.DestroyWindow(hwnd)
            user32.UnregisterClassW(class_name, wc.hInstance)


__all__ = ["ClipboardMonitor", "WM_CLIPBOARDUPDATE"]
