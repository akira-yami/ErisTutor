from __future__ import annotations

"""Resolve HWND -> process metadata using minimum Windows privileges."""

import ctypes
import os
import threading
import time
from ctypes import wintypes
from dataclasses import dataclass


PROCESS_QUERY_LIMITED_INFORMATION = 0x1000


@dataclass(frozen=True, slots=True)
class ProcessIdentity:
    hwnd: int = 0
    pid: int = 0
    process_name: str = ""
    executable_path: str = ""
    window_class: str = ""
    title: str = ""


class ProcessResolver:
    def __init__(self, *, cache_seconds: float = 30.0) -> None:
        self.cache_seconds = max(1.0, float(cache_seconds))
        self._cache: dict[int, tuple[float, ProcessIdentity]] = {}
        self._lock = threading.RLock()

    def resolve(self, hwnd: int, *, title: str = "") -> ProcessIdentity:
        hwnd = int(hwnd or 0)
        if not hwnd:
            return ProcessIdentity(title=title)
        now = time.monotonic()
        with self._lock:
            cached = self._cache.get(hwnd)
            if cached and now - cached[0] <= self.cache_seconds:
                item = cached[1]
                if title and item.title != title:
                    item = ProcessIdentity(item.hwnd, item.pid, item.process_name, item.executable_path, item.window_class, title)
                return item
        item = self._resolve_uncached(hwnd, title=title)
        with self._lock:
            self._cache[hwnd] = (now, item)
        return item

    def _resolve_uncached(self, hwnd: int, *, title: str = "") -> ProcessIdentity:
        if os.name != "nt":
            return ProcessIdentity(hwnd=hwnd, title=title)
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
        user32.GetWindowThreadProcessId.restype = wintypes.DWORD
        user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
        user32.GetClassNameW.restype = ctypes.c_int
        kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.QueryFullProcessImageNameW.argtypes = [
            wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD),
        ]
        kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL
        pid = wintypes.DWORD(0)
        try:
            user32.GetWindowThreadProcessId(wintypes.HWND(hwnd), ctypes.byref(pid))
        except Exception:
            pid.value = 0

        window_class = ""
        try:
            buff = ctypes.create_unicode_buffer(256)
            if user32.GetClassNameW(wintypes.HWND(hwnd), buff, len(buff)):
                window_class = buff.value
        except Exception:
            pass

        path = ""
        process_name = ""
        handle = None
        if pid.value:
            try:
                handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid.value)
                if handle:
                    capacity = wintypes.DWORD(32768)
                    buff = ctypes.create_unicode_buffer(capacity.value)
                    if kernel32.QueryFullProcessImageNameW(handle, 0, buff, ctypes.byref(capacity)):
                        path = buff.value
                        process_name = os.path.basename(path)
            except Exception:
                pass
            finally:
                if handle:
                    try:
                        kernel32.CloseHandle(handle)
                    except Exception:
                        pass
        return ProcessIdentity(hwnd=hwnd, pid=int(pid.value), process_name=process_name, executable_path=path, window_class=window_class, title=title)

    def invalidate(self, hwnd: int | None = None) -> None:
        with self._lock:
            if hwnd is None:
                self._cache.clear()
            else:
                self._cache.pop(int(hwnd), None)


__all__ = ["ProcessIdentity", "ProcessResolver", "PROCESS_QUERY_LIMITED_INFORMATION"]
