# -*- coding: utf-8 -*-
"""Rollout/kill-switch policy for the PDF inference architecture.

PDF annotation, pages 18-20
---------------------------
The report explicitly forbids a big-bang cutover.  Broker lanes need shadow,
canary and kill-switch controls so optional work can be disabled before the
public chat path is touched.  This module is intentionally small and free of
runtime singletons: every decision is derived from explicit environment values
and a deterministic request bucket.

Configuration examples::

    ERIS_BROKER_MODE=enforce
    ERIS_BROKER_MODE_POST_OBSERVER=shadow
    ERIS_BROKER_MODE_CHAT_MAIN=canary
    ERIS_BROKER_CANARY_PERCENT_CHAT_MAIN=10
    ERIS_BROKER_KILL_VISION_AMBIENT=1

Modes:
- ``enforce``: normal admission/reservation/backpressure;
- ``shadow``: calculate and log the decision, but do not block the provider call;
- ``canary``: enforce for a stable percentage of request scopes, shadow for control;
- ``disabled``: optional lanes are dropped; required lanes use emergency bypass.

The required-lane bypass exists only as a rollback escape hatch.  It is loudly
instrumented because quietly bypassing the control plane would be the sort of
"temporary" fix that survives three product generations.
"""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from enum import Enum
from typing import Any


class BrokerRolloutMode(str, Enum):
    DISABLED = "disabled"
    SHADOW = "shadow"
    CANARY = "canary"
    ENFORCE = "enforce"

    @classmethod
    def parse(cls, value: Any, default: "BrokerRolloutMode" | None = None) -> "BrokerRolloutMode":
        fallback = default or cls.ENFORCE
        text = str(value or "").strip().lower()
        aliases = {
            "0": cls.DISABLED,
            "off": cls.DISABLED,
            "kill": cls.DISABLED,
            "mirror": cls.SHADOW,
            "preview": cls.SHADOW,
            "on": cls.ENFORCE,
            "1": cls.ENFORCE,
            "enabled": cls.ENFORCE,
        }
        if text in aliases:
            return aliases[text]
        try:
            return cls(text)
        except ValueError:
            return fallback


@dataclass(frozen=True, slots=True)
class BrokerRolloutDecision:
    lane: str
    configured_mode: str
    effective_mode: str
    enforce: bool
    shadow: bool
    disabled: bool
    killed: bool
    bucket: float
    canary_percent: float
    reason: str


def _lane_key(lane: str) -> str:
    return str(lane or "transport").strip().upper().replace("-", "_") or "TRANSPORT"


def _env_bool(name: str, default: bool = False) -> bool:
    raw = str(os.getenv(name, "") or "").strip().lower()
    if not raw:
        return bool(default)
    return raw in {"1", "true", "yes", "on"}


def _env_float(name: str, default: float) -> float:
    raw = str(os.getenv(name, "") or "").strip()
    if not raw:
        return float(default)
    try:
        return float(raw)
    except (TypeError, ValueError):
        return float(default)


def _stable_bucket(lane: str, scope: str) -> float:
    material = f"{lane}:{scope or 'anonymous'}".encode("utf-8", errors="ignore")
    digest = hashlib.sha256(material).digest()
    number = int.from_bytes(digest[:8], "big", signed=False)
    return (number / float(2**64 - 1)) * 100.0


def broker_rollout_for_request(request: Any) -> BrokerRolloutDecision:
    lane = str(getattr(request, "lane", "transport") or "transport").strip().lower()
    key = _lane_key(lane)
    global_mode = BrokerRolloutMode.parse(os.getenv("ERIS_BROKER_MODE", "enforce"))
    configured = BrokerRolloutMode.parse(
        os.getenv(f"ERIS_BROKER_MODE_{key}", ""),
        global_mode,
    )
    killed = _env_bool(f"ERIS_BROKER_KILL_{key}", False)
    if killed:
        configured = BrokerRolloutMode.DISABLED

    percent = max(
        0.0,
        min(100.0, _env_float(f"ERIS_BROKER_CANARY_PERCENT_{key}", 5.0)),
    )
    scope = str(
        getattr(request, "cancel_scope", "")
        or getattr(request, "request_id", "")
        or "anonymous"
    )
    bucket = _stable_bucket(lane, scope)

    if configured == BrokerRolloutMode.CANARY:
        enforce = bucket < percent
        effective = BrokerRolloutMode.ENFORCE if enforce else BrokerRolloutMode.SHADOW
        reason = "canary_selected" if enforce else "canary_control_shadow"
    else:
        effective = configured
        reason = "lane_kill_switch" if killed else f"configured_{configured.value}"

    return BrokerRolloutDecision(
        lane=lane,
        configured_mode=configured.value,
        effective_mode=effective.value,
        enforce=effective == BrokerRolloutMode.ENFORCE,
        shadow=effective == BrokerRolloutMode.SHADOW,
        disabled=effective == BrokerRolloutMode.DISABLED,
        killed=killed,
        bucket=round(bucket, 4),
        canary_percent=percent,
        reason=reason,
    )


__all__ = [
    "BrokerRolloutDecision",
    "BrokerRolloutMode",
    "broker_rollout_for_request",
]
