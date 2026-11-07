# -*- coding: utf-8 -*-
"""Cầu Win32 nhỏ giữ Live2D ngay dưới overlay Godot và đọc chuột desktop."""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class WindowGeometry:
    x: int
    y: int
    width: int
    height: int


class DesktopWindowBridge:
    def __init__(self, *, godot_pid: int | None = None) -> None:
        self.godot_pid = int(godot_pid or 0)
        self.is_windows = sys.platform == "win32"
        self._user32: Any | None = None
        self._wintypes: Any | None = None
        self._godot_hwnd = 0
        self._warned_missing_godot = False
        if self.is_windows:
            try:
                import ctypes
                from ctypes import wintypes

                self._ctypes = ctypes
                self._wintypes = wintypes
                self._user32 = ctypes.windll.user32
            except Exception:
                self.is_windows = False
                self._ctypes = None
        else:
            self._ctypes = None
            self._wintypes = None

    @classmethod
    def from_env(cls) -> "DesktopWindowBridge":
        try:
            pid = int(os.getenv("ERIS_GODOT_PID", "0") or "0")
        except ValueError:
            pid = 0
        return cls(godot_pid=pid)

    def cursor_screen(self) -> tuple[float, float] | None:
        if not self.is_windows or self._user32 is None or self._ctypes is None:
            return None

        if self._wintypes is None:
            return None
        point = self._wintypes.POINT()
        try:
            if self._user32.GetCursorPos(self._ctypes.byref(point)):
                return float(point.x), float(point.y)
        except Exception:
            return None
        return None

    @staticmethod
    def geometry(glfw: Any, window: Any) -> WindowGeometry:
        x, y = glfw.get_window_pos(window)
        width, height = glfw.get_window_size(window)
        return WindowGeometry(int(x), int(y), max(1, int(width)), max(1, int(height)))

    def replay_mouse_button_down(self, button: str) -> bool:
        """Replay only the down edge after a stale overlay hit on Windows.

        The renderer flips the native window to click-through immediately before
        this call. The user's real mouse-up is therefore delivered to the app
        underneath, preserving normal click/drag semantics instead of fabricating
        an entire click inside Eris.
        """
        if not self.is_windows or self._user32 is None:
            return False
        flags = {
            "left": 0x0002,
            "right": 0x0008,
            "middle": 0x0020,
        }
        flag = flags.get(str(button or "").strip().lower(), 0)
        if not flag:
            return False
        try:
            self._user32.mouse_event(flag, 0, 0, 0, 0)
            return True
        except Exception:
            return False

    def keep_live2d_below_godot(self, glfw: Any, window: Any) -> bool:
        """Đặt Live2D ngay dưới top-level window Godot trong z-order Windows."""
        if not self.is_windows or self._user32 is None or self._ctypes is None:
            return False
        if self.godot_pid <= 0:
            return False
        try:
            live_hwnd = int(glfw.get_win32_window(window) or 0)
        except Exception:
            return False
        if not live_hwnd:
            return False
        if not self._godot_hwnd or not self._user32.IsWindow(self._godot_hwnd):
            self._godot_hwnd = self._find_largest_window_for_pid(self.godot_pid, exclude=live_hwnd)
        if not self._godot_hwnd:
            if not self._warned_missing_godot:
                print(
                    "[Live2D][WARN] Không thấy top-level window của Godot. "
                    "Nếu chạy trong Editor, hãy tắt Embed Game on Play; cửa sổ ngoài "
                    "không thể nằm giữa model và bubble của viewport nhúng.",
                    flush=True,
                )
                self._warned_missing_godot = True
            return False

        # SWP_NOSIZE | SWP_NOMOVE | SWP_NOACTIVATE | SWP_NOSENDCHANGING
        flags = 0x0001 | 0x0002 | 0x0010 | 0x0400
        try:
            return bool(
                self._user32.SetWindowPos(
                    live_hwnd,
                    self._godot_hwnd,
                    0,
                    0,
                    0,
                    0,
                    flags,
                )
            )
        except Exception:
            return False

    def _find_largest_window_for_pid(self, pid: int, *, exclude: int = 0) -> int:
        if self._user32 is None or self._ctypes is None:
            return 0

        if self._wintypes is None:
            return 0
        candidates: list[tuple[int, int]] = []
        wintypes = self._wintypes
        enum_proc_type = self._ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

        @enum_proc_type
        def callback(hwnd: int, _lparam: int) -> bool:
            if int(hwnd) == int(exclude) or not self._user32.IsWindowVisible(hwnd):
                return True
            process_id = wintypes.DWORD()
            self._user32.GetWindowThreadProcessId(hwnd, self._ctypes.byref(process_id))
            if int(process_id.value) != int(pid):
                return True
            rect = wintypes.RECT()
            if not self._user32.GetWindowRect(hwnd, self._ctypes.byref(rect)):
                return True
            area = max(0, rect.right - rect.left) * max(0, rect.bottom - rect.top)
            if area > 0:
                candidates.append((area, int(hwnd)))
            return True

        try:
            self._user32.EnumWindows(callback, 0)
        except Exception:
            return 0
        return max(candidates, default=(0, 0))[1]
