"""Windows input primitives scoped to a caller-supplied foreground window."""
from __future__ import annotations

import ctypes
from ctypes import wintypes
import os


class _MouseInput(ctypes.Structure):
    _fields_ = [
        ("dx", wintypes.LONG), ("dy", wintypes.LONG), ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t),
    ]


class _KeyboardInput(ctypes.Structure):
    _fields_ = [
        ("wVk", wintypes.WORD), ("wScan", wintypes.WORD), ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t),
    ]


class _InputData(ctypes.Union):
    _fields_ = [("mouse", _MouseInput), ("keyboard", _KeyboardInput)]


class _Input(ctypes.Structure):
    _fields_ = [("type", wintypes.DWORD), ("data", _InputData)]


_LEFT_DOWN = 0x0002
_LEFT_UP = 0x0004
_KEY_UP = 0x0002
_UNICODE = 0x0004
_ROOT_WINDOW = 2


def _user32():
    if os.name != "nt":
        raise OSError("Windows desktop input is unavailable")
    api = ctypes.WinDLL("user32", use_last_error=True)
    api.GetForegroundWindow.argtypes = []
    api.GetForegroundWindow.restype = wintypes.HWND
    api.GetAsyncKeyState.argtypes = [ctypes.c_int]
    api.GetAsyncKeyState.restype = ctypes.c_short
    api.WindowFromPoint.argtypes = [wintypes.POINT]
    api.WindowFromPoint.restype = wintypes.HWND
    api.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
    api.GetAncestor.restype = wintypes.HWND
    api.SetCursorPos.argtypes = [ctypes.c_int, ctypes.c_int]
    api.SetCursorPos.restype = wintypes.BOOL
    api.SendInput.argtypes = [wintypes.UINT, ctypes.POINTER(_Input), ctypes.c_int]
    api.SendInput.restype = wintypes.UINT
    return api


def _require_foreground(api, hwnd: int) -> None:
    if type(hwnd) is not int or hwnd <= 0:
        raise ValueError("invalid_window_handle")
    if api.GetForegroundWindow() != hwnd:
        raise PermissionError("foreground_window_changed")
    if any(api.GetAsyncKeyState(key) & 0x8000 for key in (0x01, 0x10, 0x11, 0x12, 0x5B, 0x5C)):
        raise PermissionError("input_modifier_held")


def _send(api, events: list[_Input]) -> None:
    batch = (_Input * len(events))(*events)
    sent = api.SendInput(len(events), batch, ctypes.sizeof(_Input))
    if sent != len(events):
        # Inputs are down/up pairs. If Windows stopped after a down, release it.
        if 0 < sent < len(events) and sent % 2:
            api.SendInput(1, ctypes.byref(events[sent]), ctypes.sizeof(_Input))
        raise OSError("desktop_input_incomplete")


def click_mouse(hwnd: int, x: int, y: int) -> None:
    """Send a left click only at a point belonging to the foreground window."""
    if any(type(value) is not int or not -(2**31) <= value < 2**31 for value in (x, y)):
        raise ValueError("invalid_screen_coordinates")
    api = _user32()
    _require_foreground(api, hwnd)
    target = api.WindowFromPoint(wintypes.POINT(x, y))
    if not target or api.GetAncestor(target, _ROOT_WINDOW) != hwnd:
        raise PermissionError("point_outside_foreground_window")
    if not api.SetCursorPos(x, y):
        raise OSError("cursor_move_failed")
    _require_foreground(api, hwnd)
    _send(api, [
        _Input(0, _InputData(mouse=_MouseInput(dwFlags=flag)))
        for flag in (_LEFT_DOWN, _LEFT_UP)
    ])


def type_text(hwnd: int, text: str) -> None:
    """Send literal Unicode text. Control characters and shortcuts are excluded."""
    if not isinstance(text, str) or not 1 <= len(text) <= 2000 or any(
        ord(char) < 32 or 127 <= ord(char) <= 159 for char in text
    ):
        raise ValueError("invalid_desktop_text")
    utf16 = text.encode("utf-16-le")
    events = [
        _Input(1, _InputData(keyboard=_KeyboardInput(
            wScan=int.from_bytes(utf16[index:index + 2], "little"), dwFlags=flag,
        )))
        for index in range(0, len(utf16), 2)
        for flag in (_UNICODE, _UNICODE | _KEY_UP)
    ]
    api = _user32()
    _require_foreground(api, hwnd)
    _send(api, events)
