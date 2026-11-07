from __future__ import annotations

"""Stable semantic identity and visual stability tracking for screen regions."""

import hashlib
from dataclasses import dataclass
from typing import Any


@dataclass(slots=True)
class RegionState:
    region_id: str
    owner_pid: int = 0
    hwnd: int = 0
    semantic_role: str = "unknown"
    bounds: tuple[int, int, int, int] | None = None
    preferred_source: str = "structured"
    fallback_source: str = "ocr"
    stable_fingerprint: str = ""
    pending_fingerprint: str = ""
    pending_samples: int = 0
    stable_since_ms: int = 0
    last_change_ms: int = 0
    change_rate_ema: float = 0.0
    mechanical_rate_ema: float = 0.0
    semantic_rate_ema: float = 0.0
    sensitive: bool = False
    last_attention_level: int = 0


class RegionMemory:
    def __init__(self, *, alpha: float = 0.2, min_samples: int = 2, stable_ms: int = 300) -> None:
        self.alpha = max(0.01, min(1.0, float(alpha)))
        self.min_samples = max(1, int(min_samples))
        self.stable_ms = max(0, int(stable_ms))
        self._regions: dict[str, RegionState] = {}

    @staticmethod
    def make_region_id(*, process_name: str = "", pid: int = 0, hwnd: int = 0, provider: str = "native", node_id: str = "", role: str = "unknown") -> str:
        proc = (process_name or "process").lower().replace(" ", "_")
        identity = node_id or role or "region"
        return f"{proc}:{int(pid)}:hwnd_{int(hwnd)}:{provider}:{identity}"

    @staticmethod
    def fingerprint(value: Any) -> str:
        raw = " ".join(str(value or "").split()).lower()
        return hashlib.sha256(raw.encode("utf-8", "ignore")).hexdigest()[:24]

    def get(self, region_id: str) -> RegionState | None:
        return self._regions.get(str(region_id))

    def update(
        self,
        region_id: str,
        *,
        fingerprint: str,
        now_ms: int,
        mechanical: bool = False,
        semantic: bool = False,
        attention_level: int = 0,
        **identity: Any,
    ) -> tuple[RegionState, bool]:
        key = str(region_id or "unknown")
        state = self._regions.get(key)
        if state is None:
            state = RegionState(region_id=key)
            self._regions[key] = state
        for field_name in ("owner_pid", "hwnd", "semantic_role", "bounds", "preferred_source", "fallback_source", "sensitive"):
            if field_name in identity and identity[field_name] is not None:
                setattr(state, field_name, identity[field_name])

        changed = bool(state.stable_fingerprint and fingerprint != state.stable_fingerprint)
        target = 1.0 if changed else 0.0
        state.change_rate_ema = (1.0 - self.alpha) * state.change_rate_ema + self.alpha * target
        state.mechanical_rate_ema = (1.0 - self.alpha) * state.mechanical_rate_ema + self.alpha * (1.0 if mechanical else 0.0)
        state.semantic_rate_ema = (1.0 - self.alpha) * state.semantic_rate_ema + self.alpha * (1.0 if semantic else 0.0)
        state.last_attention_level = max(0, min(4, int(attention_level)))

        if not state.stable_fingerprint:
            state.stable_fingerprint = fingerprint
            state.stable_since_ms = int(now_ms)
            return state, True
        if fingerprint == state.stable_fingerprint:
            state.pending_fingerprint = ""
            state.pending_samples = 0
            return state, True
        if fingerprint != state.pending_fingerprint:
            state.pending_fingerprint = fingerprint
            state.pending_samples = 1
            state.last_change_ms = int(now_ms)
            return state, False
        state.pending_samples += 1
        stable = state.pending_samples >= self.min_samples and int(now_ms) - state.last_change_ms >= self.stable_ms
        if stable:
            state.stable_fingerprint = fingerprint
            state.pending_fingerprint = ""
            state.pending_samples = 0
            state.stable_since_ms = int(now_ms)
        return state, stable

    def snapshot(self) -> tuple[RegionState, ...]:
        return tuple(self._regions.values())


__all__ = ["RegionMemory", "RegionState"]
