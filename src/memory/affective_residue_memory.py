"""Affective Residue Memory: dư âm cảm xúc dưới dạng pressure.

Không lưu "ERIS buồn" như fact công khai. Lưu pressure: tension, attachment residue,
exposure recoil, unfinished loop. Dư âm ảnh hưởng nhịp nói/im lặng, không tự động thành câu thoại.
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


def clamp11(value: float) -> float:
    return max(-1.0, min(1.0, float(value)))



from .json_store_io import atomic_write_json


@dataclass(slots=True)
class AffectiveResidueItem:
    id: str
    residue_type: str
    description: str
    intensity: float = 0.0
    valence: float = 0.0
    confidence: float = 0.60
    evidence_ids: list[str] = field(default_factory=list)
    first_seen_at: str = ""
    updated_at: str = ""
    observations: int = 1
    decay_policy: str = "normal"
    surface_policy: str = "never_direct_claim"
    pressure_effects: dict[str, float] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "residue_type": self.residue_type,
            "description": self.description,
            "intensity": round(float(self.intensity), 4),
            "valence": round(float(self.valence), 4),
            "confidence": round(float(self.confidence), 4),
            "evidence_ids": list(self.evidence_ids),
            "first_seen_at": self.first_seen_at,
            "updated_at": self.updated_at,
            "observations": int(self.observations),
            "decay_policy": self.decay_policy,
            "surface_policy": self.surface_policy,
            "pressure_effects": dict(self.pressure_effects),
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AffectiveResidueItem":
        return cls(
            id=str(data.get("id") or f"res_{uuid.uuid4().hex[:12]}"),
            residue_type=str(data.get("residue_type") or data.get("type") or "affective_residue"),
            description=compact_text(data.get("description") or data.get("text") or ""),
            intensity=clamp01(float(data.get("intensity", 0.0) or 0.0)),
            valence=clamp11(float(data.get("valence", 0.0) or 0.0)),
            confidence=clamp01(float(data.get("confidence", 0.60) or 0.60)),
            evidence_ids=[str(item) for item in data.get("evidence_ids") or data.get("evidence") or [] if str(item).strip()],
            first_seen_at=str(data.get("first_seen_at") or ""),
            updated_at=str(data.get("updated_at") or ""),
            observations=int(data.get("observations", 1) or 1),
            decay_policy=str(data.get("decay_policy") or "normal"),
            surface_policy=str(data.get("surface_policy") or "never_direct_claim"),
            pressure_effects={str(k): float(v) for k, v in dict(data.get("pressure_effects") or {}).items()},
            metadata=dict(data.get("metadata") or {}),
        )

    def recall_line(self) -> str:
        return compact_text(
            f"{self.residue_type}: {self.description} (intensity={self.intensity:.2f}, policy={self.surface_policy})",
            limit=280,
        )


class AffectiveResidueMemoryStore:
    def __init__(self, path: str, *, max_items: int = 120):
        self.path = path
        self.max_items = max(20, int(max_items))

    def load(self) -> list[AffectiveResidueItem]:
        if not os.path.isfile(self.path) or os.path.getsize(self.path) <= 0:
            return []
        with open(self.path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        raw_items = data.get("items") if isinstance(data, dict) else data
        if not isinstance(raw_items, list):
            return []
        return [AffectiveResidueItem.from_dict(item) for item in raw_items if isinstance(item, dict)]

    def save(self, items: list[AffectiveResidueItem]) -> None:
        items.sort(key=lambda item: (item.intensity + item.confidence, item.updated_at), reverse=True)
        atomic_write_json(self.path, {"version": 1, "items": [item.to_dict() for item in items[: self.max_items]]})

    def apply_commit(self, request: Any) -> AffectiveResidueItem | None:
        description = compact_text(getattr(request, "content", "") or "", limit=420)
        if not description:
            return None
        now = iso_now()
        confidence = clamp01(float(getattr(request, "confidence", 0.60) or 0.60))
        reason = compact_text(getattr(request, "reason", "") or "", limit=260)
        residue_type = self._infer_type(description, reason)
        intensity = max(0.25, confidence)
        valence = self._infer_valence(description, reason)
        pressure_effects = self._pressure_effects(residue_type, intensity)
        evidence_ids = [str(item) for item in getattr(request, "evidence_ids", []) or [] if str(item).strip()]
        key = f"{residue_type}:{description}".lower()
        items = self.load()
        for item in items:
            if f"{item.residue_type}:{item.description}".lower() == key:
                item.observations += 1
                item.confidence = clamp01(max(item.confidence, confidence) + 0.02)
                item.intensity = max(item.intensity, intensity)
                item.valence = clamp11((item.valence + valence) / 2.0)
                item.updated_at = now
                item.evidence_ids = list(dict.fromkeys([*item.evidence_ids, *evidence_ids]))[-10:]
                self.save(items)
                return item
        item = AffectiveResidueItem(
            id=f"res_{uuid.uuid4().hex[:12]}",
            residue_type=residue_type,
            description=description,
            intensity=intensity,
            valence=valence,
            confidence=confidence,
            evidence_ids=evidence_ids,
            first_seen_at=now,
            updated_at=now,
            decay_policy=str(getattr(request, "decay_policy", "normal") or "normal"),
            surface_policy="never_direct_claim",
            pressure_effects=pressure_effects,
            metadata={"reason": reason},
        )
        items.append(item)
        self.save(items)
        return item

    def recall(self, query: str = "", *, limit: int = 6) -> list[AffectiveResidueItem]:
        items = self.load()
        if not query:
            return items[:limit]
        q = query.lower()
        scored = []
        for item in items:
            hay = f"{item.residue_type} {item.description}".lower()
            score = sum(1 for token in q.split() if token and token in hay)
            if score:
                scored.append((score + item.intensity + item.confidence * 0.2, item))
        scored.sort(key=lambda pair: pair[0], reverse=True)
        return [item for _, item in scored[:limit]]

    def _infer_type(self, description: str, reason: str) -> str:
        text = f"{description} {reason}".lower()
        if any(token in text for token in ("unfinished", "chưa xong", "bỏ dở", "unresolved")):
            return "unfinished_loop"
        if any(token in text for token in ("attachment", "presence", "gọi", "continuity")):
            return "continuity_residue"
        if any(token in text for token in ("overread", "sai", "correction", "repair")):
            return "repair_residue"
        if any(token in text for token in ("exposure", "lộ", "too much", "quá gần")):
            return "exposure_recoil"
        return "affective_echo"

    def _infer_valence(self, description: str, reason: str) -> float:
        text = f"{description} {reason}".lower()
        if any(token in text for token in ("repair", "resolved", "ổn", "accepted")):
            return 0.12
        if any(token in text for token in ("rupture", "sai", "overread", "khó chịu", "unfinished")):
            return -0.12
        return 0.0

    def _pressure_effects(self, residue_type: str, intensity: float) -> dict[str, float]:
        if residue_type == "unfinished_loop":
            return {"reflection_pressure": intensity * 0.7, "speech_restraint": intensity * 0.2}
        if residue_type == "continuity_residue":
            return {"continuity_pressure": intensity * 0.8, "attachment_visibility": intensity * 0.25}
        if residue_type == "repair_residue":
            return {"overread_brake": intensity * 0.8, "repair_priority": intensity * 0.5}
        if residue_type == "exposure_recoil":
            return {"speech_restraint": intensity * 0.8, "distance_pressure": intensity * 0.4}
        return {"background_tension": intensity * 0.4}
