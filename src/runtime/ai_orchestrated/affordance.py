# -*- coding: utf-8 -*-
"""Typed affordances for collect-before-claim autonomous cognition.

Sources may expose a possible move, but no source may reserve the public lane
before the Executive chooses it.  Runtime claims exactly the selected
``affordance_id`` and rejects a stale revision instead of silently selecting a
replacement.  This is intentionally boring concurrency code, because surprise
is charming in conversation and rather less charming in lease ownership.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping


def _compact(value: Any, limit: int = 240) -> str:
    text = " ".join(str(value or "").split()).strip()
    return text if len(text) <= limit else text[: max(0, limit - 1)].rstrip() + "…"


@dataclass(frozen=True, slots=True)
class ExecutiveAffordance:
    affordance_id: str
    owner: str
    revision: int
    candidate: Any
    claim_required: bool = False
    expires_at: float = 0.0
    claim_key: str = ""
    metadata: Mapping[str, Any] = field(default_factory=dict)

    @property
    def available(self) -> bool:
        return bool(self.affordance_id and self.candidate is not None)

    def to_card(self) -> dict[str, Any]:
        candidate = self.candidate
        return {
            "affordance_id": self.affordance_id,
            "owner": self.owner,
            "revision": int(self.revision),
            "claim_required": bool(self.claim_required),
            "expires_at": float(self.expires_at or 0.0),
            "kind": str(getattr(candidate, "kind", "") or ""),
            "semantic_target": _compact(getattr(candidate, "topic", ""), 1200),
            "speech_act": str(getattr(candidate, "speech_act", "") or ""),
            "anchor_type": str(getattr(candidate, "anchor_type", "") or ""),
            "anchor_id": str(getattr(candidate, "anchor_id", "") or ""),
            "source": str(getattr(candidate, "source", "") or self.owner),
            "reason": _compact(getattr(candidate, "reason", ""), 360),
            "confidence": float(getattr(candidate, "confidence", 0.0) or 0.0),
            "relevance": float(getattr(candidate, "relevance", 0.0) or 0.0),
            "urgency": float(getattr(candidate, "urgency", 0.0) or 0.0),
            "intrusion_risk": float(getattr(candidate, "seed_intrusion_risk", 0.0) or 0.0),
            "evidence_refs": list(getattr(candidate, "evidence_refs", ()) or ()),
            "metadata": dict(self.metadata),
        }


__all__ = ["ExecutiveAffordance"]
