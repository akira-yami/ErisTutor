# -*- coding: utf-8 -*-
"""Đẩy affect runtime sang stage protocol theo nhịp có kiểm soát."""

from __future__ import annotations

import os
import time
from collections.abc import Mapping
from typing import Any

from runtime.stage_protocol_adapter import affect_state_event
from cortex.affective_dynamics import effective_affect_values


_AFFECT_KEYS = (
    "valence",
    "arousal",
    "curiosity",
    "boredom",
    "social_energy",
    "attachment",
    "irritation",
    "protectiveness",
    "confidence",
    "fatigue",
    "tenderness",
    "delight",
    "sadness",
    "anxiety",
    "playfulness",
    "embarrassment",
    "cognitive_tension",
    "ontological_dread",
    "self_coherence",
    "vulnerability",
    "restraint",
    "withdrawal",
    "conflict_tension",
    "shadow_intensity",
)


def _enabled() -> bool:
    raw = str(os.getenv("ERIS_LIVE2D_AFFECT_BRIDGE", "1") or "").strip().lower()
    return raw not in {"", "0", "false", "off", "no", "none"}


def _float_env(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)) or default)
    except (TypeError, ValueError):
        return default


def _extract_affect(runtime_owner: Any) -> dict[str, float]:
    getter = getattr(runtime_owner, "_get_internal_state", None)
    state = getter() if callable(getter) else None
    if state is None:
        return {}
    to_dict = getattr(state, "to_dict", None)
    raw = to_dict() if callable(to_dict) else None
    if not isinstance(raw, Mapping):
        raw = {
            key: getattr(state, key)
            for key in _AFFECT_KEYS
            if hasattr(state, key)
        }
    raw = dict(raw)
    # V43: chỉ overlay projection mới khi đây thật sự là PersistentInternalState
    # có affective_state. Harness/plugin legacy có to_dict riêng phải giữ nguyên
    # số của nó, không được bị default V43 vô tình đè thành 0.38/0.16.
    if hasattr(state, "affective_state"):
        raw.update(effective_affect_values(state))
    return _clean_affect(raw)


def _clean_affect(value: Mapping[str, Any] | None) -> dict[str, float]:
    if not isinstance(value, Mapping):
        return {}
    clean: dict[str, float] = {}
    for key in _AFFECT_KEYS:
        if key not in value:
            continue
        try:
            numeric = float(value[key])
        except (TypeError, ValueError):
            continue
        if numeric != numeric:
            continue
        clean[key] = max(-1.0, min(1.0, numeric)) if key == "valence" else max(0.0, min(1.0, numeric))
    return clean


def _signature(affect: Mapping[str, float], thought_pressure: float) -> tuple[int, ...]:
    # Bucket 0.04-0.05 để bỏ drift vi mô. Body không cần nhận 60 bản tin chỉ vì
    # confidence nhúc nhích 0.003, dù telemetry có thể rất xúc động về chuyện đó.
    values = [int(round(float(affect.get(key, 0.0)) * 24.0)) for key in _AFFECT_KEYS]
    values.append(int(round(max(0.0, min(1.0, float(thought_pressure))) * 20.0)))
    return tuple(values)


def emit_runtime_affect(
    runtime_owner: Any,
    *,
    affect: Mapping[str, Any] | None = None,
    source: str = "runtime",
    thought_pressure: float = 0.0,
    confidence: float = 0.0,
    diary_updated: bool = False,
    performance_intent: Any = None,
    force: bool = False,
) -> bool:
    """Gửi affect nếu state đổi đủ hoặc đến heartbeat.

    Hàm fail-soft vì body là output phụ; một cửa sổ Live2D không được quyền làm
    chết cognition chính, bất kể nó có mái tóc physics đẹp đến đâu.
    """

    if runtime_owner is None or not _enabled():
        return False
    bus = getattr(runtime_owner, "stage_event_bus", None)
    emit = getattr(bus, "emit", None)
    if not callable(emit):
        return False

    clean = _clean_affect(affect) if affect is not None else _extract_affect(runtime_owner)
    intent_id = ""
    thread_id = ""
    if performance_intent is not None:
        if isinstance(performance_intent, Mapping):
            raw_intent = performance_intent
        else:
            to_dict = getattr(performance_intent, "to_dict", None)
            try:
                raw_intent = to_dict() if callable(to_dict) else {}
            except Exception:
                raw_intent = {}
        if isinstance(raw_intent, Mapping):
            intent_id = str(raw_intent.get("intent_id", "") or "")
            thread_id = str(raw_intent.get("thread_id", "") or "")
            for key in ("vulnerability", "restraint"):
                if key in raw_intent:
                    try:
                        clean[key] = max(0.0, min(1.0, float(raw_intent[key])))
                    except (TypeError, ValueError):
                        pass
            # A body bridge may receive the internal tendency, but not prose or
            # hidden reasoning. Keep it to numeric pressure.
            prohibited = tuple(raw_intent.get("prohibited_moves", ()) or ())
            if prohibited:
                clean["conflict_tension"] = max(
                    clean.get("conflict_tension", 0.0),
                    min(1.0, 0.35 + 0.04 * len(prohibited)),
                )
    if not clean:
        return False

    now = time.monotonic()
    min_interval = max(0.25, _float_env("ERIS_LIVE2D_AFFECT_MIN_INTERVAL_SEC", 1.5))
    heartbeat = max(min_interval, _float_env("ERIS_LIVE2D_AFFECT_HEARTBEAT_SEC", 45.0))
    last_at = float(getattr(runtime_owner, "_live2d_affect_last_at", -1.0e9) or -1.0e9)
    current_signature = _signature(clean, thought_pressure)
    last_signature = getattr(runtime_owner, "_live2d_affect_signature", None)

    if not force:
        age = max(0.0, now - last_at)
        if age < min_interval:
            return False
        if current_signature == last_signature and age < heartbeat and not diary_updated:
            return False

    event = affect_state_event(
        affect=clean,
        source=source,
        thought_pressure=thought_pressure,
        confidence=confidence,
        diary_updated=diary_updated,
        intent_id=intent_id,
        thread_id=thread_id,
    )
    try:
        sent = bool(emit(event))
    except Exception:
        return False
    if sent:
        setattr(runtime_owner, "_live2d_affect_last_at", now)
        setattr(runtime_owner, "_live2d_affect_signature", current_signature)
    return sent


__all__ = ["emit_runtime_affect"]
