# -*- coding: utf-8 -*-
"""
runtime/panel_snapshot.py
=========================

Ghi tín hiệu nội-tâm-SỐNG (chỉ tồn tại trong runtime) ra ``memory_core/
panel_snapshot.json`` để Eris Panel hiển thị thật: cảm xúc (appraisal), giằng co
biểu đạt (N1), và lần đọc-bạn gần nhất (tom_read) + một nhật ký tín hiệu ngắn.

Nguyên tắc:
- MẶC ĐỊNH BẬT để panel có dữ liệu ngay khi chạy. Có thể tắt rõ bằng
  ``ERIS_PANEL_SNAPSHOT=0`` nếu cần zero I/O.
- Throttle: ghi tối đa mỗi ``ERIS_PANEL_SNAPSHOT_MIN_SEC`` giây (mặc định 0.8s)
  để vòng nhận thức không biến thành máy ghi đĩa.
- Atomic (tmp+replace), không bao giờ ném: lỗi gì cũng nuốt.
- Chỉ là quan sát; KHÔNG ảnh hưởng quyết định/giọng nói của Eris.
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
from pathlib import Path
from typing import Any

_lock = threading.RLock()
_last_write = 0.0
_signal_log: list[dict[str, str]] = []
_MAX_SIGNALS = 8

_TRUE = {"1", "true", "yes", "on"}
_FALSE = {"0", "false", "no", "off", "none"}


def panel_snapshot_enabled() -> bool:
    raw = os.getenv("ERIS_PANEL_SNAPSHOT")
    if raw is None or not str(raw).strip():
        return True
    value = str(raw).strip().lower()
    if value in _FALSE:
        return False
    return value in _TRUE


def _min_interval() -> float:
    try:
        return max(0.0, float(os.getenv("ERIS_PANEL_SNAPSHOT_MIN_SEC", "0.8") or 0.8))
    except (TypeError, ValueError):
        return 0.8


def _path() -> Path:
    raw = os.getenv("ERIS_MEMORY_CORE_DIR", "").strip()
    base = Path(raw) if raw else Path(__file__).resolve().parents[2] / "memory_core"
    return base / "panel_snapshot.json"


def _f(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _parse_confidence(text: str) -> float:
    m = re.search(r"~\s*([01](?:\.\d+)?)", str(text or ""))
    return _f(m.group(1)) if m else 0.0


def add_signal(kind: str) -> None:
    """Ghi một tín hiệu vừa bắn (vd 'expanded', 'interior', 'tom_read')."""
    if not panel_snapshot_enabled():
        return
    try:
        ts = time.strftime("%H:%M:%S")
        with _lock:
            _signal_log.append({"kind": str(kind)[:24], "time": ts})
            del _signal_log[:-_MAX_SIGNALS]
    except (TypeError, ValueError):
        return



def write_social_observation(
    observation: dict[str, Any],
    *,
    relationship_state: dict[str, Any] | None = None,
    cognitive_views: dict[str, Any] | None = None,
) -> None:
    """Gắn telemetry social observer vào snapshot mà không đụng public output.

    Observer hoàn tất bất đồng bộ sau lượt nói, nên hàm này merge vào snapshot
    hiện có thay vì dựng lại affect/N1. Một model phụ chậm không nên xóa bảng
    đồng hồ chỉ vì nó vừa mang về một social event, điều tưởng hiển nhiên nhưng
    máy tính thích biến hiển nhiên thành nghề toàn thời gian.
    """
    if not panel_snapshot_enabled():
        return
    try:
        now = time.time()
        payload = _read_existing_snapshot()
        payload["ts"] = now
        payload["social_observer"] = dict(observation or {})
        if relationship_state is not None:
            payload["relationship_state"] = dict(relationship_state)
        if cognitive_views is not None:
            payload["cognitive_views"] = dict(cognitive_views)
        add_signal("social_observer")
        with _lock:
            payload["signal_log"] = list(_signal_log)
            path = _path()
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(path.suffix + ".tmp")
            tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            tmp.replace(path)
    except OSError:
        return


def _read_existing_snapshot() -> dict[str, Any]:
    try:
        raw = json.loads(_path().read_text(encoding="utf-8"))
        return dict(raw) if isinstance(raw, dict) else {}
    except (OSError, UnicodeError, json.JSONDecodeError):
        return {}

def write(
    *,
    appraisal: Any = None,
    expression_gate: dict | None = None,
    tom_read: str = "",
    channel: str = "",
    signal: str = "",
) -> None:
    """Ghi snapshot (throttled). An toàn gọi mỗi chu kỳ nói."""
    if not panel_snapshot_enabled():
        return
    now = time.time()
    global _last_write
    with _lock:
        if (now - _last_write) < _min_interval():
            # vẫn nhận signal vào log dù bỏ qua ghi đĩa lần này
            if signal:
                add_signal(signal)
            return
        _last_write = now
    if signal:
        add_signal(signal)
    if tom_read:
        add_signal("tom_read")

    gate = expression_gate or {}
    affect = {
        "live": appraisal is not None,
        "valence": round(_f(getattr(appraisal, "valence", 0.0)), 3),
        "arousal": round(_f(getattr(appraisal, "arousal", 0.0)), 3),
        "tenderness": round(_f(getattr(appraisal, "tenderness", 0.0)), 3),
        "threat": round(_f(getattr(appraisal, "threat", 0.0)), 3),
        "stance": str(getattr(appraisal, "stance", "") or "—"),
    }
    n1 = {
        "live": bool(gate),
        "want": round(_f(gate.get("want_to_say")), 3),
        "suppress": round(_f(gate.get("suppression_pressure")), 3),
        "allowed": round(_f(gate.get("allowed_expression")), 3),
        "last_event": str(gate.get("last_event", "") or ""),
    }
    tom = {
        "live": bool(str(tom_read or "").strip()),
        "text": str(tom_read or "").strip(),
        "confidence": _parse_confidence(tom_read),
    }
    with _lock:
        signals = list(_signal_log)
    payload = {
        "ts": now,
        "channel": channel,
        "affect": affect,
        "n1": n1,
        "tom_read": tom,
        "signal_log": signals,
    }
    try:
        path = _path()
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(path)
    except OSError:
        return
