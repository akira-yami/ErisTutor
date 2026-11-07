# -*- coding: utf-8 -*-
from __future__ import annotations

"""Filesystem monitor cho workspace đã được người dùng cho phép.

Windows path dùng ``ReadDirectoryChangesW`` với overlapped I/O. Monitor chỉ
phát relative path/action/extension, không đọc file content và không watch cả ổ
đĩa. Khi buffer/IO bị lỗi, caller có thể restart/reconcile thay vì tin rằng event
stream hoàn hảo như một sinh vật huyền thoại.
"""

import ctypes
import os
import threading
import time
from ctypes import wintypes
from pathlib import Path
from typing import Callable

from runtime.ai_orchestrated.environment_event_bus import NormalizedEnvironmentEvent

FILE_LIST_DIRECTORY = 0x0001
FILE_SHARE_READ = 0x00000001
FILE_SHARE_WRITE = 0x00000002
FILE_SHARE_DELETE = 0x00000004
OPEN_EXISTING = 3
FILE_FLAG_BACKUP_SEMANTICS = 0x02000000
FILE_FLAG_OVERLAPPED = 0x40000000
FILE_NOTIFY_CHANGE_FILE_NAME = 0x00000001
FILE_NOTIFY_CHANGE_DIR_NAME = 0x00000002
FILE_NOTIFY_CHANGE_LAST_WRITE = 0x00000010
WAIT_OBJECT_0 = 0x00000000
WAIT_FAILED = 0xFFFFFFFF

_ACTIONS = {1: "created", 2: "deleted", 3: "modified", 4: "renamed_from", 5: "renamed_to"}


class OVERLAPPED(ctypes.Structure):
    _fields_ = [
        ("Internal", ctypes.c_size_t),
        ("InternalHigh", ctypes.c_size_t),
        ("Offset", wintypes.DWORD),
        ("OffsetHigh", wintypes.DWORD),
        ("hEvent", wintypes.HANDLE),
    ]


class FilesystemMonitor:
    def __init__(self, workspace: str | os.PathLike[str], callback: Callable[[NormalizedEnvironmentEvent], None], *, recursive: bool = True) -> None:
        self.workspace = Path(workspace).resolve()
        self.callback = callback
        self.recursive = bool(recursive)
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._stop_handle: int | None = None

    @property
    def available(self) -> bool:
        return os.name == "nt" and self.workspace.is_dir()

    def start(self) -> "FilesystemMonitor":
        if not self.available or (self._thread and self._thread.is_alive()):
            return self
        self._stop.clear()
        try:  # pragma: no cover - Windows E2E
            self._stop_handle = int(ctypes.windll.kernel32.CreateEventW(None, True, False, None) or 0)
        except Exception:
            self._stop_handle = None
        self._thread = threading.Thread(target=self._run, name=f"eris-fs-{self.workspace.name}", daemon=True)
        self._thread.start()
        return self

    def stop(self) -> None:
        self._stop.set()
        if os.name == "nt" and self._stop_handle:
            try:
                ctypes.windll.kernel32.SetEvent(wintypes.HANDLE(self._stop_handle))
            except Exception:
                pass
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=1.5)
        if os.name == "nt" and self._stop_handle:
            try:
                ctypes.windll.kernel32.CloseHandle(wintypes.HANDLE(self._stop_handle))
            except Exception:
                pass
        self._stop_handle = None

    def emit_path(self, relative_path: str, action: str, *, now: float | None = None) -> NormalizedEnvironmentEvent:
        safe = Path(relative_path).as_posix()[:500]
        event_type = "file_saved" if action == "modified" else f"file_{action}"
        event = NormalizedEnvironmentEvent(
            timestamp=time.time() if now is None else float(now),
            source="filesystem",
            event_type=event_type,
            action=action,
            role="workspace_file",
            title=safe,
            semantic=True,
            source_confidence=0.98,
            confidence=0.98,
            novelty=0.62,
            importance=0.26,
            user_relevance=0.72,
            attention_level=1,
            metadata={"workspace": str(self.workspace), "relative_path": safe, "extension": Path(safe).suffix.lower()},
            evidence_refs=(f"filesystem:{action}:{safe}",),
        )
        self.callback(event)
        return event

    def _parse_buffer(self, raw: bytes) -> None:
        offset = 0
        while offset + 12 <= len(raw):
            next_offset = int.from_bytes(raw[offset:offset + 4], "little")
            action_code = int.from_bytes(raw[offset + 4:offset + 8], "little")
            name_len = int.from_bytes(raw[offset + 8:offset + 12], "little")
            end = offset + 12 + name_len
            if end > len(raw):
                break
            name = raw[offset + 12:end].decode("utf-16-le", "replace")
            try:
                self.emit_path(name, _ACTIONS.get(action_code, "changed"))
            except Exception:
                pass
            if next_offset == 0:
                break
            offset += next_offset

    def _run(self) -> None:  # pragma: no cover - Windows E2E
        kernel32 = ctypes.windll.kernel32
        INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value
        handle = kernel32.CreateFileW(
            str(self.workspace),
            FILE_LIST_DIRECTORY,
            FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE,
            None,
            OPEN_EXISTING,
            FILE_FLAG_BACKUP_SEMANTICS | FILE_FLAG_OVERLAPPED,
            None,
        )
        if handle in (0, INVALID_HANDLE_VALUE, -1):
            return

        io_event = kernel32.CreateEventW(None, True, False, None)
        if not io_event:
            kernel32.CloseHandle(handle)
            return
        buffer = ctypes.create_string_buffer(64 * 1024)
        returned = wintypes.DWORD()
        notify_filter = FILE_NOTIFY_CHANGE_FILE_NAME | FILE_NOTIFY_CHANGE_DIR_NAME | FILE_NOTIFY_CHANGE_LAST_WRITE
        wait_handles = (wintypes.HANDLE * 2)(wintypes.HANDLE(io_event), wintypes.HANDLE(self._stop_handle or 0))

        try:
            while not self._stop.is_set():
                kernel32.ResetEvent(io_event)
                overlapped = OVERLAPPED()
                overlapped.hEvent = io_event
                ok = kernel32.ReadDirectoryChangesW(
                    handle,
                    ctypes.byref(buffer),
                    len(buffer),
                    self.recursive,
                    notify_filter,
                    None,
                    ctypes.byref(overlapped),
                    None,
                )
                if not ok:
                    break

                wait = int(kernel32.WaitForMultipleObjects(2, wait_handles, False, 30_000))
                if wait == WAIT_OBJECT_0 + 1:
                    try:
                        kernel32.CancelIoEx(handle, ctypes.byref(overlapped))
                    except Exception:
                        pass
                    break
                if wait == WAIT_FAILED:
                    break
                if wait != WAIT_OBJECT_0:
                    # Timeout chỉ là sanity point, cancel request cũ rồi đăng ký lại.
                    try:
                        kernel32.CancelIoEx(handle, ctypes.byref(overlapped))
                    except Exception:
                        pass
                    continue

                returned.value = 0
                if not kernel32.GetOverlappedResult(handle, ctypes.byref(overlapped), ctypes.byref(returned), False):
                    continue
                if returned.value:
                    self._parse_buffer(buffer.raw[: returned.value])
        finally:
            try:
                kernel32.CancelIoEx(handle, None)
            except Exception:
                pass
            try:
                kernel32.CloseHandle(io_event)
            except Exception:
                pass
            try:
                kernel32.CloseHandle(handle)
            except Exception:
                pass


__all__ = ["FilesystemMonitor"]
