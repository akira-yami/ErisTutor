# -*- coding: utf-8 -*-
from __future__ import annotations

"""ConPTY bridge cho terminal do Eris tự sở hữu.

Ranh giới an toàn rất rõ: class này chỉ biết process do chính nó launch. Nó
không attach/chui vào Windows Terminal hoặc shell bất kỳ đang chạy. Trên
Windows có CreatePseudoConsole thì dùng ConPTY thật; platform khác dùng
``subprocess`` chỉ để CI/test contract semantic.
"""

import ctypes
import os
import subprocess
import threading
import time
from ctypes import wintypes
from pathlib import Path
from typing import Callable, Sequence

from runtime.ai_orchestrated.environment_event_bus import NormalizedEnvironmentEvent


PROC_THREAD_ATTRIBUTE_PSEUDOCONSOLE = 0x00020016
EXTENDED_STARTUPINFO_PRESENT = 0x00080000
CREATE_UNICODE_ENVIRONMENT = 0x00000400
STARTF_USESTDHANDLES = 0x00000100
HANDLE_FLAG_INHERIT = 0x00000001
WAIT_OBJECT_0 = 0x00000000
WAIT_TIMEOUT = 0x00000102
INFINITE = 0xFFFFFFFF


class COORD(ctypes.Structure):
    _fields_ = [("X", ctypes.c_short), ("Y", ctypes.c_short)]


class STARTUPINFOW(ctypes.Structure):
    # ctypes.wintypes không expose STARTUPINFOW trên mọi platform Python, nên
    # định nghĩa tại chỗ để Linux CI vẫn import được module.
    _fields_ = [
        ("cb", wintypes.DWORD),
        ("lpReserved", wintypes.LPWSTR),
        ("lpDesktop", wintypes.LPWSTR),
        ("lpTitle", wintypes.LPWSTR),
        ("dwX", wintypes.DWORD),
        ("dwY", wintypes.DWORD),
        ("dwXSize", wintypes.DWORD),
        ("dwYSize", wintypes.DWORD),
        ("dwXCountChars", wintypes.DWORD),
        ("dwYCountChars", wintypes.DWORD),
        ("dwFillAttribute", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("wShowWindow", wintypes.WORD),
        ("cbReserved2", wintypes.WORD),
        ("lpReserved2", ctypes.c_void_p),
        ("hStdInput", wintypes.HANDLE),
        ("hStdOutput", wintypes.HANDLE),
        ("hStdError", wintypes.HANDLE),
    ]


class STARTUPINFOEXW(ctypes.Structure):
    _fields_ = [
        ("StartupInfo", STARTUPINFOW),
        ("lpAttributeList", ctypes.c_void_p),
    ]


class PROCESS_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("hProcess", wintypes.HANDLE),
        ("hThread", wintypes.HANDLE),
        ("dwProcessId", wintypes.DWORD),
        ("dwThreadId", wintypes.DWORD),
    ]


class SECURITY_ATTRIBUTES(ctypes.Structure):
    _fields_ = [
        ("nLength", wintypes.DWORD),
        ("lpSecurityDescriptor", ctypes.c_void_p),
        ("bInheritHandle", wintypes.BOOL),
    ]


class OwnedTerminalSession:
    def __init__(
        self,
        callback: Callable[[NormalizedEnvironmentEvent], None],
        *,
        cwd: str | None = None,
        cols: int = 120,
        rows: int = 40,
    ) -> None:
        self.callback = callback
        self.cwd = str(Path(cwd).resolve()) if cwd else None
        self.cols = max(40, min(500, int(cols)))
        self.rows = max(10, min(200, int(rows)))

    @property
    def conpty_available(self) -> bool:
        if os.name != "nt":
            return False
        try:  # pragma: no cover - Windows E2E
            kernel32 = ctypes.windll.kernel32
            return all(hasattr(kernel32, name) for name in ("CreatePseudoConsole", "ClosePseudoConsole"))
        except Exception:
            return False

    def _emit_started(self, command: Sequence[str], started: float, backend: str) -> str:
        cmd_class = Path(str(command[0])).name if command else "command"
        self.callback(NormalizedEnvironmentEvent(
            timestamp=started,
            source="owned_terminal",
            event_type="command_started",
            action="executed",
            role="terminal",
            title=cmd_class,
            source_confidence=1.0,
            confidence=1.0,
            novelty=0.55,
            importance=0.32,
            user_relevance=0.82,
            attention_level=1,
            metadata={"command_class": cmd_class, "workspace": self.cwd or "", "backend": backend},
            evidence_refs=(f"terminal:started:{int(started * 1000)}",),
        ))
        return cmd_class

    def _emit_finished(self, cmd_class: str, started: float, returncode: int, backend: str) -> None:
        event_type = "command_succeeded" if returncode == 0 else "command_failed"
        level = 2 if returncode == 0 else 3
        self.callback(NormalizedEnvironmentEvent(
            timestamp=time.time(),
            source="owned_terminal",
            event_type=event_type,
            action="finished",
            role="terminal",
            title=cmd_class,
            source_confidence=1.0,
            confidence=1.0,
            novelty=0.8,
            importance=0.55 if returncode == 0 else 0.82,
            user_relevance=0.9,
            attention_level=level,
            metadata={
                "command_class": cmd_class,
                "exit_code": int(returncode),
                "workspace": self.cwd or "",
                "backend": backend,
            },
            evidence_refs=(f"terminal:finished:{int(started * 1000)}:{returncode}",),
        ))

    def run(self, command: Sequence[str], *, timeout: float | None = None) -> subprocess.CompletedProcess[str]:
        args = [str(item) for item in command]
        if not args:
            raise ValueError("command must not be empty")
        backend = "conpty" if self.conpty_available else "subprocess_fallback"
        started = time.time()
        cmd_class = self._emit_started(args, started, backend)
        if self.conpty_available:
            launch_args = self._wrap_for_conpty(args)
            result = self._run_conpty(launch_args, timeout=timeout)
            result = subprocess.CompletedProcess(args, result.returncode, stdout=result.stdout, stderr=result.stderr)
        else:
            result = subprocess.run(args, cwd=self.cwd, capture_output=True, text=True, timeout=timeout, check=False)
        self._emit_finished(cmd_class, started, int(result.returncode), backend)
        return result

    @staticmethod
    def _wrap_for_conpty(command: Sequence[str]) -> list[str]:
        executable = Path(str(command[0])).name.lower() if command else ""
        if executable in {"cmd", "cmd.exe"}:
            return [str(item) for item in command]
        return ["cmd.exe", "/d", "/s", "/c", f"{subprocess.list2cmdline([str(item) for item in command])} < NUL"]

    @staticmethod
    def _close_handle(kernel32: object, handle: wintypes.HANDLE | int | None) -> None:  # pragma: no cover
        if not handle:
            return
        try:
            ctypes.windll.kernel32.CloseHandle(handle)
        except Exception:
            pass

    def _run_conpty(self, command: Sequence[str], *, timeout: float | None) -> subprocess.CompletedProcess[str]:  # pragma: no cover - Windows E2E
        kernel32 = ctypes.windll.kernel32
        kernel32.CreatePipe.argtypes = [ctypes.POINTER(wintypes.HANDLE), ctypes.POINTER(wintypes.HANDLE), ctypes.c_void_p, wintypes.DWORD]
        kernel32.CreatePipe.restype = wintypes.BOOL
        kernel32.CreatePseudoConsole.argtypes = [COORD, wintypes.HANDLE, wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(ctypes.c_void_p)]
        kernel32.CreatePseudoConsole.restype = ctypes.c_long
        kernel32.ClosePseudoConsole.argtypes = [ctypes.c_void_p]
        kernel32.ClosePseudoConsole.restype = None
        kernel32.InitializeProcThreadAttributeList.argtypes = [ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, ctypes.POINTER(ctypes.c_size_t)]
        kernel32.InitializeProcThreadAttributeList.restype = wintypes.BOOL
        kernel32.UpdateProcThreadAttribute.argtypes = [ctypes.c_void_p, wintypes.DWORD, ctypes.c_size_t, ctypes.c_void_p, ctypes.c_size_t, ctypes.c_void_p, ctypes.c_void_p]
        kernel32.UpdateProcThreadAttribute.restype = wintypes.BOOL
        kernel32.DeleteProcThreadAttributeList.argtypes = [ctypes.c_void_p]
        kernel32.CreateProcessW.argtypes = [wintypes.LPCWSTR, wintypes.LPWSTR, ctypes.c_void_p, ctypes.c_void_p, wintypes.BOOL, wintypes.DWORD, ctypes.c_void_p, wintypes.LPCWSTR, ctypes.c_void_p, ctypes.POINTER(PROCESS_INFORMATION)]
        kernel32.CreateProcessW.restype = wintypes.BOOL
        kernel32.ReadFile.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p]
        kernel32.ReadFile.restype = wintypes.BOOL
        kernel32.WriteFile.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p]
        kernel32.WriteFile.restype = wintypes.BOOL
        kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        kernel32.WaitForSingleObject.restype = wintypes.DWORD
        kernel32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        kernel32.GetExitCodeProcess.restype = wintypes.BOOL
        kernel32.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
        kernel32.TerminateProcess.restype = wintypes.BOOL
        kernel32.SetHandleInformation.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.DWORD]
        kernel32.SetHandleInformation.restype = wintypes.BOOL
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL

        input_read = wintypes.HANDLE()
        input_write = wintypes.HANDLE()
        output_read = wintypes.HANDLE()
        output_write = wintypes.HANDLE()
        hpcon = ctypes.c_void_p()
        attr_buffer = None
        pi = PROCESS_INFORMATION()

        # Parent -> input_write -> ConPTY input_read
        # ConPTY output_write -> output_read -> parent reader thread.
        pipe_attrs = SECURITY_ATTRIBUTES(ctypes.sizeof(SECURITY_ATTRIBUTES), None, True)
        if not kernel32.CreatePipe(ctypes.byref(input_read), ctypes.byref(input_write), ctypes.byref(pipe_attrs), 0):
            raise ctypes.WinError()
        if not kernel32.CreatePipe(ctypes.byref(output_read), ctypes.byref(output_write), ctypes.byref(pipe_attrs), 0):
            self._close_handle(kernel32, input_read); self._close_handle(kernel32, input_write)
            raise ctypes.WinError()
        kernel32.SetHandleInformation(input_write, HANDLE_FLAG_INHERIT, 0)
        kernel32.SetHandleInformation(output_read, HANDLE_FLAG_INHERIT, 0)

        try:
            hr = int(kernel32.CreatePseudoConsole(
                COORD(self.cols, self.rows), input_read, output_write, 0, ctypes.byref(hpcon)
            ))
            if hr < 0 or not hpcon.value:
                raise OSError(f"CreatePseudoConsole failed HRESULT=0x{hr & 0xFFFFFFFF:08X}")

            size = ctypes.c_size_t(0)
            kernel32.InitializeProcThreadAttributeList(None, 1, 0, ctypes.byref(size))
            attr_buffer = ctypes.create_string_buffer(size.value)
            attr_ptr = ctypes.cast(attr_buffer, ctypes.c_void_p)
            if not kernel32.InitializeProcThreadAttributeList(attr_ptr, 1, 0, ctypes.byref(size)):
                raise ctypes.WinError()
            if not kernel32.UpdateProcThreadAttribute(
                attr_ptr,
                0,
                ctypes.c_size_t(PROC_THREAD_ATTRIBUTE_PSEUDOCONSOLE),
                hpcon,
                ctypes.sizeof(ctypes.c_void_p),
                None,
                None,
            ):
                raise ctypes.WinError()

            si = STARTUPINFOEXW()
            si.StartupInfo.cb = ctypes.sizeof(STARTUPINFOEXW)
            si.StartupInfo.dwFlags = STARTF_USESTDHANDLES
            si.StartupInfo.hStdInput = input_read
            si.StartupInfo.hStdOutput = output_write
            si.StartupInfo.hStdError = output_write
            si.lpAttributeList = attr_ptr
            command_line = ctypes.create_unicode_buffer(subprocess.list2cmdline(list(command)))
            ok = kernel32.CreateProcessW(
                None,
                command_line,
                None,
                None,
                True,
                EXTENDED_STARTUPINFO_PRESENT | CREATE_UNICODE_ENVIRONMENT,
                None,
                self.cwd,
                ctypes.byref(si),
                ctypes.byref(pi),
            )
            if not ok:
                raise ctypes.WinError()

            # Parent không ghi stdin trong run() hiện tại nên đóng ngay write-end
            # để chương trình con nhận EOF thay vì treo chờ input mãi.
            self._close_handle(kernel32, input_read)
            input_read = wintypes.HANDLE()
            self._close_handle(kernel32, output_write)
            output_write = wintypes.HANDLE()

            chunks: list[bytes] = []

            def reader() -> None:
                buffer = ctypes.create_string_buffer(8192)
                read = wintypes.DWORD()
                while True:
                    ok_read = kernel32.ReadFile(output_read, buffer, len(buffer), ctypes.byref(read), None)
                    if not ok_read or read.value == 0:
                        break
                    chunks.append(bytes(buffer.raw[: read.value]))

            reader_thread = threading.Thread(target=reader, name="eris-conpty-reader", daemon=True)
            reader_thread.start()

            wait_ms = INFINITE if timeout is None else max(1, int(float(timeout) * 1000.0))
            wait_result = int(kernel32.WaitForSingleObject(pi.hProcess, wait_ms))
            if wait_result == WAIT_TIMEOUT:
                kernel32.TerminateProcess(pi.hProcess, 124)
                kernel32.WaitForSingleObject(pi.hProcess, 2000)
                raise subprocess.TimeoutExpired(list(command), timeout, output=b"".join(chunks))
            if wait_result != WAIT_OBJECT_0:
                raise ctypes.WinError()

            self._close_handle(kernel32, input_write)
            input_write = wintypes.HANDLE()

            exit_code = wintypes.DWORD()
            if not kernel32.GetExitCodeProcess(pi.hProcess, ctypes.byref(exit_code)):
                raise ctypes.WinError()

            # Đóng HPCON làm ReadFile ở parent nhận EOF nếu process con đã thoát.
            kernel32.ClosePseudoConsole(hpcon)
            hpcon = ctypes.c_void_p()
            reader_thread.join(timeout=2.0)
            raw_output = b"".join(chunks)
            try:
                stdout = raw_output.decode("utf-8", "replace")
            except Exception:
                stdout = raw_output.decode(errors="replace")
            return subprocess.CompletedProcess(list(command), int(exit_code.value), stdout=stdout, stderr="")
        finally:
            if attr_buffer is not None:
                try:
                    kernel32.DeleteProcThreadAttributeList(ctypes.cast(attr_buffer, ctypes.c_void_p))
                except Exception:
                    pass
            self._close_handle(kernel32, pi.hThread)
            self._close_handle(kernel32, pi.hProcess)
            self._close_handle(kernel32, input_read)
            self._close_handle(kernel32, input_write)
            self._close_handle(kernel32, output_read)
            self._close_handle(kernel32, output_write)
            if hpcon.value:
                try:
                    kernel32.ClosePseudoConsole(hpcon)
                except Exception:
                    pass


__all__ = ["OwnedTerminalSession"]
