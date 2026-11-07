# -*- coding: utf-8 -*-
"""Persistent canonical long-term stores for ERIS V3.

The stores are intentionally boring: atomic JSON, evidence IDs, confidence,
status, contradiction links and decay. Long-term memory should be boring. The
exciting version is usually the one that confidently remembers fiction.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
import os
import threading
import time
from typing import Any, Mapping

from .json_store_io import atomic_write_json


def _get(obj: Any, name: str, default: Any = None) -> Any:
    if obj is None:
        return default
    return obj.get(name, default) if isinstance(obj, Mapping) else getattr(obj, name, default)


def _clamp(value: Any, default: float = 0.0) -> float:
    try:
        return max(0.0, min(1.0, float(value)))
    except Exception:
        return max(0.0, min(1.0, default))


def _plain(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Mapping):
        return {str(k): _plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_plain(v) for v in value]
    if hasattr(value, "to_dict"):
        try:
            return _plain(value.to_dict())
        except Exception:
            pass
    return repr(value)


def _content_text(value: Any) -> str:
    if isinstance(value, Mapping):
        for key in ("claim", "text", "summary", "pattern", "description", "content"):
            if str(value.get(key, "") or "").strip():
                return " ".join(str(value.get(key)).split())
        return json.dumps(_plain(value), ensure_ascii=False, sort_keys=True)
    return " ".join(str(value or "").split())


@dataclass(slots=True)
class CanonicalMemoryRecord:
    id: str
    target: str
    content: Any
    content_text: str
    confidence: float
    evidence_ids: list[str] = field(default_factory=list)
    status: str = "active"
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    last_recalled_at: float = 0.0
    recall_count: int = 0
    recurrence_count: int = 1
    decay_policy: str = "normal"
    contradiction_ids: list[str] = field(default_factory=list)
    source_module: str = ""
    tags: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "target": self.target,
            "content": _plain(self.content),
            "content_text": self.content_text,
            "confidence": round(float(self.confidence), 6),
            "evidence_ids": list(self.evidence_ids),
            "status": self.status,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "last_recalled_at": self.last_recalled_at,
            "recall_count": self.recall_count,
            "recurrence_count": self.recurrence_count,
            "decay_policy": self.decay_policy,
            "contradiction_ids": list(self.contradiction_ids),
            "source_module": self.source_module,
            "tags": list(self.tags),
            "metadata": _plain(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "CanonicalMemoryRecord":
        return cls(
            id=str(data.get("id") or ""),
            target=str(data.get("target") or ""),
            content=data.get("content"),
            content_text=str(data.get("content_text") or _content_text(data.get("content"))),
            confidence=_clamp(data.get("confidence", 0.5), 0.5),
            evidence_ids=[str(x) for x in data.get("evidence_ids", []) or [] if str(x).strip()],
            status=str(data.get("status") or "active"),
            created_at=float(data.get("created_at", time.time()) or time.time()),
            updated_at=float(data.get("updated_at", time.time()) or time.time()),
            last_recalled_at=float(data.get("last_recalled_at", 0.0) or 0.0),
            recall_count=int(data.get("recall_count", 0) or 0),
            recurrence_count=int(data.get("recurrence_count", 1) or 1),
            decay_policy=str(data.get("decay_policy") or "normal"),
            contradiction_ids=[str(x) for x in data.get("contradiction_ids", []) or [] if str(x).strip()],
            source_module=str(data.get("source_module") or ""),
            tags=[str(x) for x in data.get("tags", []) or [] if str(x).strip()],
            metadata=dict(data.get("metadata") or {}),
        )


class CanonicalRecordStore:
    def __init__(self, path: str, *, target: str, max_items: int = 500) -> None:
        self.path = path
        self.target = target
        self.max_items = max(20, int(max_items))
        self._lock = threading.RLock()

    def load(self) -> list[CanonicalMemoryRecord]:
        with self._lock:
            if not os.path.isfile(self.path) or os.path.getsize(self.path) <= 0:
                return []
            try:
                with open(self.path, "r", encoding="utf-8") as handle:
                    data = json.load(handle)
            except Exception:
                return []
            raw = data.get("items", []) if isinstance(data, Mapping) else data
            if not isinstance(raw, list):
                return []
            return [CanonicalMemoryRecord.from_dict(item) for item in raw if isinstance(item, Mapping)]

    def save(self, items: list[CanonicalMemoryRecord]) -> None:
        with self._lock:
            os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
            items.sort(key=lambda x: (x.status == "active", x.confidence, x.updated_at), reverse=True)
            atomic_write_json(self.path, {"version": 1, "target": self.target, "items": [x.to_dict() for x in items[: self.max_items]]})

    def apply_commit(self, request: Any) -> CanonicalMemoryRecord | None:
        metadata = dict(_get(request, "metadata", {}) or {})
        raw_content = metadata.get("raw_content", _get(request, "content", ""))
        content_text = _content_text(raw_content)
        if not content_text:
            return None
        confidence = _clamp(_get(request, "confidence", 0.5), 0.5)
        evidence_ids = [str(x) for x in (_get(request, "evidence_ids", []) or []) if str(x).strip()]
        decay_policy = str(_get(request, "decay_policy", "normal") or "normal")
        tags = [str(x) for x in (_get(request, "tags", []) or []) if str(x).strip()]
        source_module = str(_get(request, "source_module", "") or "")
        status = "tentative" if confidence < 0.62 or "tentative" in tags else "active"
        fingerprint = hashlib.sha256((self.target + "\n" + content_text.lower()).encode("utf-8", "ignore")).hexdigest()[:20]
        record_id = f"{self.target}:{fingerprint}"
        now = time.time()
        with self._lock:
            items = self.load()
            existing = next((x for x in items if x.id == record_id), None)
            if existing is not None:
                existing.recurrence_count += 1
                existing.updated_at = now
                existing.confidence = _clamp(max(existing.confidence, confidence) + min(0.08, existing.recurrence_count * 0.005))
                existing.evidence_ids = list(dict.fromkeys([*existing.evidence_ids, *evidence_ids]))[-32:]
                existing.tags = list(dict.fromkeys([*existing.tags, *tags]))[-24:]
                existing.metadata.update(_plain(metadata))
                if existing.status == "tentative" and existing.confidence >= 0.72 and existing.recurrence_count >= 2:
                    existing.status = "active"
                self.save(items)
                return existing
            record = CanonicalMemoryRecord(
                id=record_id,
                target=self.target,
                content=_plain(raw_content),
                content_text=content_text,
                confidence=confidence,
                evidence_ids=evidence_ids,
                status=status,
                created_at=now,
                updated_at=now,
                decay_policy=decay_policy,
                source_module=source_module,
                tags=tags,
                metadata=_plain(metadata),
            )
            items.append(record)
            self.save(items)
            return record

    def recall(self, query: str = "", *, limit: int = 8) -> list[CanonicalMemoryRecord]:
        tokens = {x for x in str(query or "").lower().split() if x}
        now = time.time()
        with self._lock:
            items = self.load()
            scored: list[tuple[float, CanonicalMemoryRecord]] = []
            for item in items:
                if item.status in {"rejected", "decayed"}:
                    continue
                hay = item.content_text.lower()
                overlap = sum(1 for token in tokens if token in hay) / max(1, len(tokens)) if tokens else 0.0
                recency = 1.0 / (1.0 + max(0.0, now - item.updated_at) / 86400.0)
                score = overlap * 0.56 + item.confidence * 0.30 + recency * 0.08 + min(item.recurrence_count, 8) * 0.01
                if not tokens or overlap > 0:
                    scored.append((score, item))
            scored.sort(key=lambda pair: pair[0], reverse=True)
            selected = [item for _, item in scored[: max(1, int(limit))]]
            for item in selected:
                item.last_recalled_at = now
                item.recall_count += 1
            if selected:
                self.save(items)
            return selected

    def decay(self, *, now: float | None = None) -> dict[str, int]:
        now = float(now or time.time())
        half_life_days = {"none": 10_000.0, "slow": 180.0, "normal": 60.0, "fast": 14.0, "session_only": 0.25}
        changed = 0
        removed = 0
        with self._lock:
            items = self.load()
            kept: list[CanonicalMemoryRecord] = []
            for item in items:
                age_days = max(0.0, now - item.updated_at) / 86400.0
                half_life = half_life_days.get(item.decay_policy, 60.0)
                retention = 0.5 ** (age_days / max(0.01, half_life))
                effective = item.confidence * retention
                if effective < 0.18 and item.decay_policy != "none":
                    removed += 1
                    continue
                new_conf = _clamp(effective)
                if abs(new_conf - item.confidence) > 0.01:
                    item.confidence = new_conf
                    changed += 1
                kept.append(item)
            if changed or removed:
                self.save(kept)
        return {"changed": changed, "removed": removed}


class BeliefStore(CanonicalRecordStore):
    def __init__(self, path: str, *, max_items: int = 800) -> None:
        super().__init__(path, target="belief_core", max_items=max_items)


class WorldviewMemoryStore(CanonicalRecordStore):
    def __init__(self, path: str, *, max_items: int = 500) -> None:
        super().__init__(path, target="worldview_memory", max_items=max_items)


class SocioculturalMemoryStore(CanonicalRecordStore):
    def __init__(self, path: str, *, max_items: int = 300) -> None:
        super().__init__(path, target="sociocultural_memory", max_items=max_items)


class SpatialMemoryStore(CanonicalRecordStore):
    def __init__(self, path: str, *, max_items: int = 300) -> None:
        super().__init__(path, target="spatial_memory", max_items=max_items)


class TemporalRhythmMemoryStore(CanonicalRecordStore):
    def __init__(self, path: str, *, max_items: int = 300) -> None:
        super().__init__(path, target="temporal_rhythm_memory", max_items=max_items)


class BodyRhythmMemoryStore(CanonicalRecordStore):
    def __init__(self, path: str, *, max_items: int = 300) -> None:
        super().__init__(path, target="body_rhythm_memory", max_items=max_items)


class ToMHistoryStore(CanonicalRecordStore):
    def __init__(self, path: str, *, max_items: int = 500) -> None:
        super().__init__(path, target="tom_history", max_items=max_items)


class SelfContinuityMemoryStore(CanonicalRecordStore):
    def __init__(self, path: str, *, max_items: int = 300) -> None:
        super().__init__(path, target="self_continuity", max_items=max_items)


class HomeostaticBaselineStore(CanonicalRecordStore):
    def __init__(self, path: str, *, max_items: int = 180) -> None:
        super().__init__(path, target="homeostatic_baseline", max_items=max_items)


__all__ = [
    "CanonicalMemoryRecord", "CanonicalRecordStore", "BeliefStore", "WorldviewMemoryStore",
    "SocioculturalMemoryStore", "SpatialMemoryStore", "TemporalRhythmMemoryStore",
    "BodyRhythmMemoryStore", "ToMHistoryStore", "SelfContinuityMemoryStore", "HomeostaticBaselineStore",
]
