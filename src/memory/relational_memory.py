"""Relational Memory: nhịp quan hệ, rupture, repair, khoảng cách.

Đây không phải nơi lưu "user is sad". Nó lưu pattern có bằng chứng: user sửa ERIS khi overread,
user thích câu trả lời kỹ thuật trực tiếp, rupture cần repair. Tức là ký ức quan hệ, không phải bói cảm xúc.
"""

from __future__ import annotations

import json
import os
import time
import uuid
from dataclasses import dataclass, field
from typing import Any


def iso_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime())


def compact_text(value: Any, *, limit: int = 360) -> str:
    text = " ".join(str(value or "").split())
    if len(text) > limit:
        return text[: limit - 3].rstrip() + "..."
    return text


def clamp01(value: float) -> float:
    return max(0.0, min(1.0, float(value)))



from .json_store_io import atomic_write_json


@dataclass(slots=True)
class RelationalMemoryItem:
    id: str
    pattern: str
    claim: str
    confidence: float = 0.65
    trust_delta: float = 0.0
    distance_delta: float = 0.0
    rupture_weight: float = 0.0
    repair_weight: float = 0.0
    evidence_ids: list[str] = field(default_factory=list)
    observations: int = 1
    first_seen_at: str = ""
    updated_at: str = ""
    decay_policy: str = "normal"
    surface_policy: str = "internal_bias"
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "pattern": self.pattern,
            "claim": self.claim,
            "confidence": round(float(self.confidence), 4),
            "trust_delta": round(float(self.trust_delta), 4),
            "distance_delta": round(float(self.distance_delta), 4),
            "rupture_weight": round(float(self.rupture_weight), 4),
            "repair_weight": round(float(self.repair_weight), 4),
            "evidence_ids": list(self.evidence_ids),
            "observations": int(self.observations),
            "first_seen_at": self.first_seen_at,
            "updated_at": self.updated_at,
            "decay_policy": self.decay_policy,
            "surface_policy": self.surface_policy,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "RelationalMemoryItem":
        return cls(
            id=str(data.get("id") or f"rel_{uuid.uuid4().hex[:12]}"),
            pattern=str(data.get("pattern") or "relationship_pattern"),
            claim=compact_text(data.get("claim") or data.get("text") or ""),
            confidence=clamp01(float(data.get("confidence", 0.65) or 0.65)),
            trust_delta=float(data.get("trust_delta", 0.0) or 0.0),
            distance_delta=float(data.get("distance_delta", 0.0) or 0.0),
            rupture_weight=clamp01(float(data.get("rupture_weight", 0.0) or 0.0)),
            repair_weight=clamp01(float(data.get("repair_weight", 0.0) or 0.0)),
            evidence_ids=[str(item) for item in data.get("evidence_ids") or data.get("evidence") or [] if str(item).strip()],
            observations=int(data.get("observations", 1) or 1),
            first_seen_at=str(data.get("first_seen_at") or ""),
            updated_at=str(data.get("updated_at") or ""),
            decay_policy=str(data.get("decay_policy") or "normal"),
            surface_policy=str(data.get("surface_policy") or "internal_bias"),
            metadata=dict(data.get("metadata") or {}),
        )

    def recall_line(self) -> str:
        return compact_text(f"{self.pattern}: {self.claim} (confidence={self.confidence:.2f})", limit=280)


class RelationalMemoryStore:
    def __init__(self, path: str, *, max_items: int = 120):
        self.path = path
        self.max_items = max(20, int(max_items))

    def load(self) -> list[RelationalMemoryItem]:
        if not os.path.isfile(self.path) or os.path.getsize(self.path) <= 0:
            return []
        with open(self.path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        raw_items = data.get("items") if isinstance(data, dict) else data
        if not isinstance(raw_items, list):
            return []
        return [RelationalMemoryItem.from_dict(item) for item in raw_items if isinstance(item, dict)]

    def save(self, items: list[RelationalMemoryItem]) -> None:
        items.sort(key=lambda item: (item.confidence + item.rupture_weight + item.repair_weight, item.updated_at), reverse=True)
        atomic_write_json(self.path, {"version": 1, "items": [item.to_dict() for item in items[: self.max_items]]})

    def apply_commit(self, request: Any) -> RelationalMemoryItem | None:
        claim = compact_text(getattr(request, "content", "") or "", limit=420)
        if not claim:
            return None
        now = iso_now()
        confidence = clamp01(float(getattr(request, "confidence", 0.65) or 0.65))
        reason = compact_text(getattr(request, "reason", "") or "", limit=260)
        pattern = self._infer_pattern(claim, reason)
        evidence_ids = [str(item) for item in getattr(request, "evidence_ids", []) or [] if str(item).strip()]
        key = f"{pattern}:{claim}".lower()
        items = self.load()
        for item in items:
            if f"{item.pattern}:{item.claim}".lower() == key:
                item.observations += 1
                item.confidence = clamp01(max(item.confidence, confidence) + 0.025)
                item.updated_at = now
                item.evidence_ids = list(dict.fromkeys([*item.evidence_ids, *evidence_ids]))[-10:]
                self.save(items)
                return item
        item = RelationalMemoryItem(
            id=f"rel_{uuid.uuid4().hex[:12]}",
            pattern=pattern,
            claim=claim,
            confidence=confidence,
            trust_delta=self._trust_delta(pattern),
            distance_delta=self._distance_delta(pattern),
            rupture_weight=0.7 if "rupture" in pattern else 0.0,
            repair_weight=0.7 if "repair" in pattern else 0.0,
            evidence_ids=evidence_ids,
            first_seen_at=now,
            updated_at=now,
            decay_policy=str(getattr(request, "decay_policy", "normal") or "normal"),
            surface_policy="internal_bias",
            metadata={"reason": reason},
        )
        items.append(item)
        self.save(items)
        return item

    def recall(self, query: str = "", *, limit: int = 6) -> list[RelationalMemoryItem]:
        items = self.load()
        if not query:
            return items[:limit]
        q = query.lower()
        scored = []
        for item in items:
            hay = f"{item.pattern} {item.claim}".lower()
            score = sum(1 for token in q.split() if token and token in hay)
            if score:
                scored.append((score + item.confidence, item))
        scored.sort(key=lambda pair: pair[0], reverse=True)
        return [item for _, item in scored[:limit]]

    def _infer_pattern(self, claim: str, reason: str) -> str:
        text = f"{claim} {reason}".lower()
        if any(token in text for token in ("sai", "wrong", "correction", "không phải", "misread", "overread")):
            return "overread_correction"
        if any(token in text for token in ("rupture", "khó chịu", "angry", "bực", "distance")):
            return "relationship_rupture"
        if any(token in text for token in ("repair", "xin lỗi", "sửa", "acknowledge")):
            return "relationship_repair"
        if any(token in text for token in ("technical", "code", "architecture", "debug")):
            return "technical_respect_rhythm"
        return "relationship_reflection"

    def _trust_delta(self, pattern: str) -> float:
        if "repair" in pattern:
            return 0.04
        if "rupture" in pattern:
            return -0.06
        if "correction" in pattern:
            return 0.01
        return 0.0

    def _distance_delta(self, pattern: str) -> float:
        if "rupture" in pattern:
            return 0.08
        if "repair" in pattern:
            return -0.03
        return 0.0
