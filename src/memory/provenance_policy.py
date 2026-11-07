# -*- coding: utf-8 -*-
"""Policy provenance và trạng thái nhận thức cho ký ức public."""

from __future__ import annotations

import hashlib
import os
import threading
import time
from dataclasses import dataclass
from enum import Enum
from typing import Iterable




def _guard_enabled() -> bool:
    try:
        from runtime.rollout_control import feature_enabled
        return feature_enabled("memory_provenance_guard")
    except Exception:
        return os.environ.get("ERIS_MEMORY_PROVENANCE_GUARD", "1").strip().lower() not in {
            "0", "false", "off", "no",
        }

class PublicMemoryMode(str, Enum):
    ASSERT = "assert"
    HEDGED = "hedged"
    ASK_CLARIFY = "ask_clarify"


@dataclass(frozen=True, slots=True)
class ProvenanceDecision:
    mode: PublicMemoryMode
    reason: str


@dataclass(slots=True)
class _Contested:
    expires_at: float
    turns_left: int


class ContestedClaimStore:
    """Chặn claim vừa bị user sửa quay lại trong vài turn kế tiếp."""

    def __init__(self, *, ttl_s: float = 60.0, turns: int = 2) -> None:
        self.ttl_s = max(1.0, float(ttl_s))
        self.turns = max(1, int(turns))
        self._items: dict[str, _Contested] = {}
        self._lock = threading.RLock()

    @staticmethod
    def fingerprint(text: str) -> str:
        normalized = " ".join(str(text or "").lower().split())
        return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:20]

    def mark(self, text: str, *, now: float | None = None) -> None:
        if not str(text or "").strip():
            return
        ts = time.monotonic() if now is None else float(now)
        with self._lock:
            self._items[self.fingerprint(text)] = _Contested(ts + self.ttl_s, self.turns)

    def is_contested(self, text: str, *, consume_turn: bool = False, now: float | None = None) -> bool:
        ts = time.monotonic() if now is None else float(now)
        key = self.fingerprint(text)
        with self._lock:
            item = self._items.get(key)
            if item is None:
                return False
            if item.expires_at <= ts or item.turns_left <= 0:
                self._items.pop(key, None)
                return False
            if consume_turn:
                item.turns_left -= 1
            return True


class MemoryProvenancePolicy:
    def decide(self, *, source: str, confidence: float, evidence: Iterable[str] = (), text: str = "") -> ProvenanceDecision:
        source_key = str(source or "").lower()
        confidence = max(0.0, min(1.0, float(confidence or 0.0)))
        if not _guard_enabled():
            decision = ProvenanceDecision(PublicMemoryMode.ASSERT, "guard_disabled")
        elif GLOBAL_CONTESTED_CLAIMS.is_contested(text, consume_turn=True):
            decision = ProvenanceDecision(PublicMemoryMode.ASK_CLARIFY, "contested_claim_ttl")
        else:
            evidence_count = len(tuple(evidence or ()))
            if confidence >= 0.86 and evidence_count >= 1 and "semantic" in source_key:
                decision = ProvenanceDecision(PublicMemoryMode.ASSERT, "stable_semantic_fact")
            elif confidence >= 0.55 and evidence_count >= 1:
                decision = ProvenanceDecision(PublicMemoryMode.HEDGED, "limited_provenance")
            else:
                decision = ProvenanceDecision(PublicMemoryMode.ASK_CLARIFY, "weak_or_missing_provenance")
        try:
            from runtime.hardening_metrics import record_memory_provenance
            record_memory_provenance(
                mode=decision.mode.value, reason=decision.reason, source=source_key, confidence=confidence
            )
        except Exception:
            pass
        return decision

    @staticmethod
    def render(text: str, decision: ProvenanceDecision) -> str:
        clean = str(text or "").strip()
        if decision.mode == PublicMemoryMode.ASSERT:
            return clean
        if decision.mode == PublicMemoryMode.HEDGED:
            return f"Tôi đang nhớ là {clean[:1].lower() + clean[1:] if clean else clean} Nếu tôi nhớ nhầm, bạn sửa tôi."
        return "Ký ức này đang yếu hoặc vừa bị sửa. Bạn xác nhận lại giúp tôi thay vì để tôi đoán tiếp."


GLOBAL_CONTESTED_CLAIMS = ContestedClaimStore(
    ttl_s=float(os.environ.get("ERIS_MEMORY_CONTESTED_TTL_S", "60") or 60),
    turns=int(os.environ.get("ERIS_MEMORY_CONTESTED_TURNS", "2") or 2),
)
GLOBAL_MEMORY_PROVENANCE_POLICY = MemoryProvenancePolicy()


__all__ = [
    "ContestedClaimStore", "GLOBAL_CONTESTED_CLAIMS", "GLOBAL_MEMORY_PROVENANCE_POLICY",
    "MemoryProvenancePolicy", "ProvenanceDecision", "PublicMemoryMode",
]
