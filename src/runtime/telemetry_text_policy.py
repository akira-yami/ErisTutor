# -*- coding: utf-8 -*-
"""Shared policy for separating semantic screen evidence from generic telemetry."""
from __future__ import annotations

import re


_GENERIC_TELEMETRY_PREFIXES = (
    "active window is ",
    "focus kind is ",
    "there has been no input for ",
    "browser target is visible:",
    "context classification:",
)


def is_generic_telemetry_line(text: str) -> bool:
    lowered = str(text or "").strip().lower()
    if not lowered:
        return True
    if lowered.startswith(_GENERIC_TELEMETRY_PREFIXES):
        return True
    if re.fullmatch(r"\d+ browser tabs are visible", lowered):
        return True
    if re.fullmatch(r"\d+ windows are open", lowered):
        return True
    return False


__all__ = ["is_generic_telemetry_line"]
