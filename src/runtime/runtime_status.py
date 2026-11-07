# -*- coding: utf-8 -*-
"""Coarse runtime-status events for the visible Eris presence pill.

These events are operational telemetry only. They intentionally expose phase,
health and completion state without exposing hidden reasoning or private text.
"""
from __future__ import annotations

from typing import Any


_ALLOWED_TONES = {"info", "active", "success", "warning", "error", "muted"}


def emit_runtime_status(
    owner: Any,
    *,
    activity: str,
    label: str,
    active: bool = True,
    tone: str = "info",
    priority: int = 50,
    notice_ms: int = 0,
    phase: str = "",
    detail: str = "",
) -> bool:
    """Emit a public-safe operational status through ``eris.stage``.

    ``priority`` lets foreground work win over background jobs when both are
    active. ``notice_ms`` keeps a completed status visible briefly so a deliberate
    silence or soft failure does not vanish before a human can notice it.
    """

    emit = getattr(owner, "_emit_stage_protocol_state", None)
    if not callable(emit):
        return False

    clean_activity = str(activity or "runtime").strip().lower()[:80] or "runtime"
    clean_label = " ".join(str(label or "").split()).strip()[:180]
    clean_tone = str(tone or "info").strip().lower()
    if clean_tone not in _ALLOWED_TONES:
        clean_tone = "info"

    try:
        clean_priority = max(0, min(100, int(priority)))
    except (TypeError, ValueError):
        clean_priority = 50
    try:
        clean_notice = max(0, min(30_000, int(notice_ms)))
    except (TypeError, ValueError):
        clean_notice = 0

    data = {
        "activity": clean_activity,
        "label": clean_label,
        "active": bool(active),
        "tone": clean_tone,
        "priority": clean_priority,
        "notice_ms": clean_notice,
    }
    clean_phase = str(phase or "").strip().lower()[:80]
    clean_detail = " ".join(str(detail or "").split()).strip()[:220]
    if clean_phase:
        data["phase"] = clean_phase
    if clean_detail:
        data["detail"] = clean_detail

    try:
        emit("runtime_status", reason=clean_activity, data=data)
        return True
    except Exception:
        # Status reporting must never become a new way to crash the runtime.
        return False


__all__ = ["emit_runtime_status"]
