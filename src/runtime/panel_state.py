# -*- coding: utf-8 -*-
"""
runtime/panel_state.py
======================

Dựng snapshot trạng thái cho Eris Panel (buồng lái nội tâm dễ thương).

Đọc các file đã persist trong ``memory_core/`` (không đụng runtime đang chạy):
  - ``development_profile.json`` -> trục long-arc (N2),
  - ``anchor_state.json``        -> attachment_longarc,
  - ``private_concerns.json``    -> dòng nội tâm,
  - ``panel_snapshot.json``      -> tín hiệu SỐNG (affect / N1 / tom_read / log).
+ trạng thái cờ chiều-sâu. Field vắng -> demo nhẹ + ``live=False`` để UI làm mờ.
Không bao giờ ném.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any


def memory_core_dir() -> Path:
    raw = os.getenv("ERIS_MEMORY_CORE_DIR", "").strip()
    if raw:
        return Path(raw)
    return Path(__file__).resolve().parents[2] / "memory_core"


def _read_json(name: str) -> Any:
    try:
        return json.loads((memory_core_dir() / name).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None


def _flags() -> dict[str, bool]:
    try:
        from cortex.depth_flags import all_states

        return all_states()
    except (ImportError, AttributeError, RuntimeError):
        return {}


_AXIS_VI = {
    "warmth_temperature": ("ấm hơn", "nguội hơn"),
    "preferred_distance": ("xa hơn", "gần hơn"),  # dương = xa
    "read_boldness": ("dám đọc hơn", "dè dặt hơn"),
    "attachment_visibility": ("lộ gắn bó hơn", "giấu gắn bó hơn"),
}


def _development() -> dict[str, Any]:
    data = _read_json("development_profile.json")
    axes_out: list[dict[str, Any]] = []
    if isinstance(data, dict):
        axes = data.get("axes") or {}
        if isinstance(axes, dict):
            for name, payload in axes.items():
                try:
                    delta = float((payload or {}).get("delta", 0.0))
                except (TypeError, ValueError):
                    delta = 0.0
                if abs(delta) < 1e-6:
                    continue
                pos, neg = _AXIS_VI.get(name, (f"{name}+", f"{name}-"))
                axes_out.append({"label": pos if delta > 0 else neg, "delta": round(delta, 3)})
    return {
        "live": bool(axes_out),
        "axes": axes_out or [
            {"label": "ấm hơn", "delta": 0.0},
            {"label": "gần hơn", "delta": 0.0},
        ],
        "sentence": "Tôi vẫn gần như lúc mới gặp bạn." if not axes_out else "So với lúc đầu, tôi đã đổi đôi chút.",
    }


def _attachment() -> dict[str, Any]:
    data = _read_json("anchor_state.json")
    val, live = 0.0, False
    if isinstance(data, dict):
        try:
            val = float(data.get("attachment_longarc", 0.0) or 0.0)
            live = True
        except (TypeError, ValueError):
            pass
    return {"live": live, "value": round(val, 3)}


def _interiority() -> dict[str, Any]:
    items: list[dict[str, Any]] = []
    concerns = _read_json("private_concerns.json")
    if isinstance(concerns, dict):
        for c in (concerns.get("concerns") or [])[:5]:
            if not isinstance(c, dict):
                continue
            items.append({
                "theme": str(c.get("theme") or "")[:60],
                "recurrence": int(c.get("recurrence") or 0),
                "pressure": round(float(c.get("pressure") or 0.0), 2),
                "status": str(c.get("status") or "open"),
            })
    return {"live": bool(items), "items": items or [
        {"theme": "chưa có bận tâm nào được ghi", "recurrence": 0, "pressure": 0.0, "status": "—"},
    ]}


def _context_tags() -> list[str]:
    hour = time.localtime().tm_hour
    part = "sáng" if 5 <= hour < 12 else "chiều" if 12 <= hour < 18 else "tối"
    return [part, "quiet", "just you"]


def _snapshot(max_age_s: float = 120.0) -> dict[str, Any]:
    """Đọc panel_snapshot.json (tín hiệu sống). {} nếu vắng/cũ/hỏng."""
    data = _read_json("panel_snapshot.json")
    if not isinstance(data, dict):
        return {}
    try:
        if (time.time() - float(data.get("ts", 0.0))) > max_age_s:
            return {}
    except (TypeError, ValueError):
        return {}
    return data


def build_panel_state() -> dict[str, Any]:
    flags = _flags()
    flags_on = sum(1 for v in flags.values() if v)
    flags_total = len(flags) or 6
    dev = _development()
    snap = _snapshot()

    affect = snap.get("affect") if isinstance(snap.get("affect"), dict) else None
    tom = snap.get("tom_read") if isinstance(snap.get("tom_read"), dict) else None
    n1 = snap.get("n1") if isinstance(snap.get("n1"), dict) else None
    signal_log = snap.get("signal_log") if isinstance(snap.get("signal_log"), list) else []
    social_observer = snap.get("social_observer") if isinstance(snap.get("social_observer"), dict) else {}
    relationship_state = snap.get("relationship_state") if isinstance(snap.get("relationship_state"), dict) else {}
    channel = str(snap.get("channel") or "chat") if snap else "chat"

    return {
        "ts": time.time(),
        "identity": {
            "name": "ERIS",
            "channel": channel,
            "model": "gemini",
            "flags_on": flags_on,
            "flags_total": flags_total,
            "alive": bool(snap),
        },
        "affect": affect or {"live": False, "valence": 0.0, "arousal": 0.0, "stance": "—", "tenderness": 0.0},
        "tom_read": tom or {"live": False, "text": "chưa đọc được gì", "confidence": 0.0},
        "n1": n1 or {"live": False, "want": 0.0, "suppress": 0.0, "allowed": 0.0, "last_event": ""},
        "n2": {"live": dev["live"], "axes": dev["axes"], "sentence": dev["sentence"], "attachment": _attachment()},
        "interiority": _interiority(),
        "signal_log": signal_log,
        "social_observer": social_observer,
        "relationship_state": relationship_state,
        "context": _context_tags(),
        "flags": flags,
    }
