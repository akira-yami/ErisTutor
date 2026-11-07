# -*- coding: utf-8 -*-
"""Observer hậu điều kiện cho system action.

Executor chỉ chứng minh được lệnh đã được dispatch. Module này cố đọc lại trạng
thái hệ điều hành để nâng action lên ``observed``. Không có telemetry thì giữ
``dispatched``; tuyệt đối không đoán thành công chỉ vì API không ném exception.
"""

from __future__ import annotations

import ctypes
import os
import re
import time
from dataclasses import dataclass, field
from typing import Any, Mapping, Protocol

from actions.contracts import ActionExecutionResult, ActionExecutionStatus


@dataclass(frozen=True, slots=True)
class ActionObservation:
    confirmed: bool
    reason: str = ""
    evidence: tuple[str, ...] = ()
    data: Mapping[str, str] = field(default_factory=dict)


class ActionObserver(Protocol):
    def observe(self, result: ActionExecutionResult, *, timeout_s: float = 0.45) -> ActionObservation: ...


class DefaultActionObserver:
    """Observer phòng thủ cho Windows; platform khác trả advisory-only."""

    _OBSERVABLE = {"focus_window", "copy_text", "set_volume"}

    def observe(self, result: ActionExecutionResult, *, timeout_s: float = 0.45) -> ActionObservation:
        if not result.success or result.status == ActionExecutionStatus.FAILED:
            return ActionObservation(False, "action_failed")
        if result.action_name not in self._OBSERVABLE:
            return ActionObservation(False, "no_postcondition_adapter")
        if os.name != "nt":
            return ActionObservation(False, "observer_platform_unsupported")

        deadline = time.monotonic() + max(0.05, min(float(timeout_s), 2.0))
        last = ActionObservation(False, "postcondition_not_observed")
        while True:
            try:
                if result.action_name == "focus_window":
                    last = self._observe_foreground_window(result)
                elif result.action_name == "copy_text":
                    last = self._observe_clipboard(result)
                elif result.action_name == "set_volume":
                    last = self._observe_volume(result)
            except Exception as exc:  # telemetry không được làm action hỏng thêm
                last = ActionObservation(False, f"observer_error:{type(exc).__name__}")
            if last.confirmed or time.monotonic() >= deadline:
                return last
            time.sleep(0.04)

    @staticmethod
    def _observe_foreground_window(result: ActionExecutionResult) -> ActionObservation:
        user32 = ctypes.windll.user32
        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return ActionObservation(False, "foreground_window_missing")
        length = user32.GetWindowTextLengthW(hwnd)
        buff = ctypes.create_unicode_buffer(max(1, length + 1))
        user32.GetWindowTextW(hwnd, buff, len(buff))
        actual = buff.value.strip()
        expected = str(result.data.get("matched_title") or result.action_input or "").strip()
        if actual and expected and (expected.lower() in actual.lower() or actual.lower() in expected.lower()):
            return ActionObservation(
                True,
                "foreground_title_matches",
                (f"win32:foreground_title:{actual[:120]}",),
                {"observed_title": actual},
            )
        return ActionObservation(False, "foreground_title_mismatch", data={"observed_title": actual})

    @staticmethod
    def _read_clipboard_text() -> str:
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        cf_unicode_text = 13
        if not user32.OpenClipboard(None):
            return ""
        try:
            handle = user32.GetClipboardData(cf_unicode_text)
            if not handle:
                return ""
            pointer = kernel32.GlobalLock(handle)
            if not pointer:
                return ""
            try:
                return ctypes.wstring_at(pointer)
            finally:
                kernel32.GlobalUnlock(handle)
        finally:
            user32.CloseClipboard()

    def _observe_clipboard(self, result: ActionExecutionResult) -> ActionObservation:
        actual = self._read_clipboard_text()
        expected = str(result.action_input or "")
        if actual == expected:
            return ActionObservation(
                True,
                "clipboard_exact_match",
                (f"win32:clipboard_length:{len(actual)}",),
                {"observed_length": str(len(actual))},
            )
        return ActionObservation(
            False,
            "clipboard_mismatch",
            data={"observed_length": str(len(actual))},
        )

    @staticmethod
    def _observe_volume(result: ActionExecutionResult) -> ActionObservation:
        """Dùng pycaw nếu project đã cài; thiếu dependency thì giữ dispatched."""
        try:
            from ctypes import POINTER, cast
            from comtypes import CLSCTX_ALL
            from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume
        except Exception:
            return ActionObservation(False, "volume_telemetry_unavailable")

        devices = AudioUtilities.GetSpeakers()
        interface = devices.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
        endpoint = cast(interface, POINTER(IAudioEndpointVolume))
        raw = str(result.action_input or result.data.get("level") or "").strip().lower()
        if raw in {"mute", "silent", "tat am", "tat tieng"}:
            muted = bool(endpoint.GetMute())
            if muted:
                return ActionObservation(True, "volume_mute_observed", ("pycaw:mute:true",), {"muted": "true"})
            return ActionObservation(False, "volume_mute_not_observed", data={"muted": "false"})
        match = re.search(r"\d+", raw)
        if not match:
            return ActionObservation(False, "volume_target_unknown")
        target = max(0, min(100, int(match.group(0))))
        actual = int(round(float(endpoint.GetMasterVolumeLevelScalar()) * 100.0))
        if abs(actual - target) <= 4:
            return ActionObservation(
                True,
                "volume_level_observed",
                (f"pycaw:volume:{actual}",),
                {"observed_level": str(actual)},
            )
        return ActionObservation(False, "volume_level_mismatch", data={"observed_level": str(actual)})


def apply_observation(result: ActionExecutionResult, observation: ActionObservation) -> ActionExecutionResult:
    result.observer_reason = observation.reason
    if observation.data:
        result.data.update({str(k): str(v) for k, v in observation.data.items()})
    if observation.confirmed:
        result.status = ActionExecutionStatus.OBSERVED
        result.observed = True
        result.dispatched = True
        result.evidence = tuple(dict.fromkeys((*result.evidence, *observation.evidence)))
    return result


__all__ = ["ActionObservation", "ActionObserver", "DefaultActionObserver", "apply_observation"]
