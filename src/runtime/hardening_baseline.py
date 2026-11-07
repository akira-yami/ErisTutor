# -*- coding: utf-8 -*-
"""Baseline có version cho canary hardening.

Rollback ``> 20% so với baseline`` chỉ có nghĩa khi baseline được đo và lưu cùng
release. Dùng hai biến môi trường đoán mò thì vẫn chạy được, nhưng đó là bói toán
có syntax. Module này chụp các rate ổn định từ observability và lưu JSON rõ nguồn.
"""
from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Mapping


def _bare(name: str) -> str:
    return str(name or "").split("{", 1)[0]


def _series(snapshot: Mapping[str, Any], metric: str) -> list[Mapping[str, Any]]:
    return [value for key, value in snapshot.items() if _bare(key) == metric and isinstance(value, Mapping)]


def _sum(snapshot: Mapping[str, Any], metric: str) -> float:
    return sum(float(item.get("sum", item.get("gauge", 0.0)) or 0.0) for item in _series(snapshot, metric))


def _count(snapshot: Mapping[str, Any], metric: str) -> float:
    return sum(float(item.get("count", 0.0) or 0.0) for item in _series(snapshot, metric))


def _p95(snapshot: Mapping[str, Any], metric: str) -> float:
    return max((float(item.get("p95", 0.0) or 0.0) for item in _series(snapshot, metric)), default=0.0)


def _rate(snapshot: Mapping[str, Any], numerator: str, denominator: str) -> tuple[float, int]:
    numer = _sum(snapshot, numerator)
    denom = _sum(snapshot, denominator)
    if denom <= 0:
        denom = _count(snapshot, denominator)
    return (numer / denom if denom > 0 else 0.0, int(denom))


@dataclass(frozen=True, slots=True)
class HardeningBaseline:
    schema_version: int
    captured_at: float
    source: str
    public_turns: int
    action_samples: int
    repair_samples: int
    false_success_action_rate: float
    repair_loop_rate: float
    chat_p95_ms: float
    tts_p95_ms: float
    same_language_fallback_rate: float

    @classmethod
    def from_snapshot(cls, snapshot: Mapping[str, Any], *, source: str = "") -> "HardeningBaseline":
        false_rate, action_samples = _rate(snapshot, "false_success_action_rate", "action_confirmation_total")
        repair_rate, repair_samples = _rate(snapshot, "repair_loop_rate", "repair_state_event_total")
        fallback_total = _sum(snapshot, "tts_fallback_total")
        fallback_same = _sum(snapshot, "tts_fallback_same_language_rate")
        fallback_rate = fallback_same / fallback_total if fallback_total > 0 else 1.0
        public_turns = int(_sum(snapshot, "hardening_public_turn_total") or _count(snapshot, "director_turn_total"))
        return cls(
            schema_version=1,
            captured_at=time.time(),
            source=str(source or "").strip(),
            public_turns=public_turns,
            action_samples=action_samples,
            repair_samples=repair_samples,
            false_success_action_rate=round(false_rate, 8),
            repair_loop_rate=round(repair_rate, 8),
            chat_p95_ms=round(max(_p95(snapshot, "end_to_end_latency_ms"), _p95(snapshot, "chat_latency_ms")), 4),
            tts_p95_ms=round(max(_p95(snapshot, "tts_latency_ms"), _p95(snapshot, "tts_end_to_end_latency_ms")), 4),
            same_language_fallback_rate=round(fallback_rate, 8),
        )

    @classmethod
    def load(cls, path: str | Path) -> "HardeningBaseline":
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        if not isinstance(payload, Mapping):
            raise ValueError("Baseline phải là JSON object")
        return cls(
            schema_version=int(payload.get("schema_version", 1) or 1),
            captured_at=float(payload.get("captured_at", 0.0) or 0.0),
            source=str(payload.get("source", "") or ""),
            public_turns=int(payload.get("public_turns", 0) or 0),
            action_samples=int(payload.get("action_samples", 0) or 0),
            repair_samples=int(payload.get("repair_samples", 0) or 0),
            false_success_action_rate=float(payload.get("false_success_action_rate", 0.0) or 0.0),
            repair_loop_rate=float(payload.get("repair_loop_rate", 0.0) or 0.0),
            chat_p95_ms=float(payload.get("chat_p95_ms", 0.0) or 0.0),
            tts_p95_ms=float(payload.get("tts_p95_ms", 0.0) or 0.0),
            same_language_fallback_rate=float(payload.get("same_language_fallback_rate", 1.0) or 1.0),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def save(self, path: str | Path) -> Path:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_suffix(target.suffix + ".tmp")
        temporary.write_text(json.dumps(self.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(target)
        return target


__all__ = ["HardeningBaseline"]
