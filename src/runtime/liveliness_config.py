# -*- coding: utf-8 -*-
"""Configuration for the autonomous-liveliness redesign.

All values are read from ``ERIS_LIVELINESS_*`` environment variables. Every
reader clamps to a valid range and falls back to the documented default when a
value is absent, malformed, or out of range; it never raises into the idle loop.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# Max idle interval between cycles in the current system (seconds).
MAX_IDLE_INTERVAL_SEC = 120.0

_warned_keys: set[str] = set()


def _warn_once(key: str, raw: str, default: object) -> None:
    if key in _warned_keys:
        return
    _warned_keys.add(key)
    logger.warning(
        "[liveliness_config] %s=%r invalid or out of range; using default %r",
        key,
        raw,
        default,
    )


def _read_float(key: str, default: float, *, lo: float, hi: float) -> float:
    raw = os.getenv(key)
    if raw is None:
        return default
    try:
        value = float(raw.strip())
    except (ValueError, AttributeError):
        _warn_once(key, raw, default)
        return default
    if value != value or value in (float("inf"), float("-inf")):  # NaN / inf
        _warn_once(key, raw, default)
        return default
    if value < lo or value > hi:
        _warn_once(key, raw, default)
        return min(max(value, lo), hi)
    return value


def _read_int(key: str, default: int, *, lo: int, hi: int) -> int:
    raw = os.getenv(key)
    if raw is None:
        return default
    try:
        parsed = float(raw.strip())
    except (ValueError, AttributeError):
        _warn_once(key, raw, default)
        return default
    if parsed != parsed or parsed in (float("inf"), float("-inf")):  # NaN / inf
        _warn_once(key, raw, default)
        return default
    value = int(parsed)
    if value < lo or value > hi:
        _warn_once(key, raw, default)
        return min(max(value, lo), hi)
    return value


@dataclass(frozen=True, slots=True)
class LivelinessConfig:
    vision_fresh_sec: float = 30.0
    seed_max_len: int = 240
    repeat_window_sec: float = 180.0
    sim_threshold_pct: int = 78
    max_line_repeat: int = 3
    max_alt_seeds: int = 3
    min_grounded_per_hr: int = 20
    subinterval_sec: float = 300.0
    min_grounded_pct: int = 60
    decision_retention: int = 100
    body_only_min_gap_sec: float = 90.0

    @property
    def sim_threshold(self) -> float:
        """Similarity threshold as a 0.0-1.0 fraction."""
        return self.sim_threshold_pct / 100.0


def load_liveliness_config() -> LivelinessConfig:
    """Read the config from the environment with clamping and defaults."""

    repeat_window = _read_float(
        "ERIS_LIVELINESS_REPEAT_WINDOW_SEC", 180.0, lo=MAX_IDLE_INTERVAL_SEC, hi=3600.0
    )
    return LivelinessConfig(
        vision_fresh_sec=_read_float("ERIS_LIVELINESS_VISION_FRESH_SEC", 30.0, lo=1.0, hi=600.0),
        seed_max_len=_read_int("ERIS_LIVELINESS_SEED_MAX_LEN", 240, lo=1, hi=2000),
        repeat_window_sec=repeat_window,
        sim_threshold_pct=_read_int("ERIS_LIVELINESS_SIM_THRESHOLD_PCT", 78, lo=0, hi=100),
        max_line_repeat=_read_int("ERIS_LIVELINESS_MAX_LINE_REPEAT", 3, lo=1, hi=10),
        max_alt_seeds=_read_int("ERIS_LIVELINESS_MAX_ALT_SEEDS", 3, lo=1, hi=10),
        min_grounded_per_hr=_read_int("ERIS_LIVELINESS_MIN_GROUNDED_PER_HR", 20, lo=0, hi=1000),
        subinterval_sec=_read_float("ERIS_LIVELINESS_SUBINTERVAL_SEC", 300.0, lo=1.0, hi=3600.0),
        min_grounded_pct=_read_int("ERIS_LIVELINESS_MIN_GROUNDED_PCT", 60, lo=0, hi=100),
        decision_retention=_read_int("ERIS_LIVELINESS_DECISION_RETENTION", 100, lo=1, hi=100000),
        body_only_min_gap_sec=_read_float(
            "ERIS_LIVELINESS_BODY_ONLY_MIN_GAP_SEC", 90.0, lo=0.0, hi=3600.0
        ),
    )
