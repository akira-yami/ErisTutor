from __future__ import annotations

"""Đọc cửa sổ foreground trên Windows mà không kéo VLM vào cuộc."""

import ctypes
from ctypes import wintypes
import os

from .evidence_models import ActiveWindowInfo
from native.process_resolver import ProcessResolver


class ActiveWindowProvider:
    def __init__(self) -> None:
        self._resolver = ProcessResolver()

    def get(self) -> ActiveWindowInfo:
        if os.name != "nt":
            return ActiveWindowInfo(title="Desktop")
        try:
            user32 = ctypes.windll.user32
            hwnd = int(user32.GetForegroundWindow() or 0)
            if not hwnd:
                return ActiveWindowInfo(title="Desktop")
            length = int(user32.GetWindowTextLengthW(hwnd) or 0)
            buff = ctypes.create_unicode_buffer(max(1, length + 1))
            user32.GetWindowTextW(hwnd, buff, len(buff))

            rect = wintypes.RECT()
            bounds = None
            if user32.GetWindowRect(hwnd, ctypes.byref(rect)):
                width = max(1, int(rect.right - rect.left))
                height = max(1, int(rect.bottom - rect.top))
                bounds = (int(rect.left), int(rect.top), width, height)
            title = buff.value.strip() or "Desktop"
            identity = self._resolver.resolve(hwnd, title=title)
            return ActiveWindowInfo(
                title=title,
                hwnd=hwnd,
                bounds=bounds,
                process_name=identity.process_name,
                pid=identity.pid,
                window_class=identity.window_class,
                executable_path=identity.executable_path,
            )
        except Exception:
            return ActiveWindowInfo(title="Desktop")
