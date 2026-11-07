from __future__ import annotations

"""Unresolved memory candidate store cho ERIS.

Store này giữ các mảnh quan trọng nhưng chưa parse được thành semantic/procedural/
episodic fact ổn định. Nó là vùng đệm giữa "user bảo nhớ" và "ERIS được phép
nói đã nhớ chắc".

Không surface candidate như fact chắc. Candidate chỉ là dấu vết có evidence,
importance và lý do cần consolidation. Một hệ thống nhớ tử tế không giả vờ cái
mơ hồ là chân lý, dù con người làm chuyện đó mỗi ngày và gọi nó là trực giác.
"""

import json
import os
import re
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Iterable, Literal, Mapping

CandidateStatus = Literal["unresolved", "partial", "failed", "promoted", "archived"]


def iso_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime())


def compact_text(value: Any, *, limit: int = 900) -> str:
    text = " ".join(str(value or "").split())
    if len(text) > limit:
        return text[: limit - 3].rstrip() + "..."
    return text


def clamp01(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except Exception:
        number = default
    if number != number:  # NaN
        number = default
    return max(0.0, min(1.0, number))



from .json_store_io import atomic_write_json


def fingerprint_text(text: str) -> str:
    import hashlib

    normalized = re.sub(r"\s+", " ", str(text or "").strip().lower())
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:20]


def _as_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value] if value.strip() else []
    if isinstance(value, Iterable) and not isinstance(value, (Mapping, bytes, bytearray)):
        return [str(item) for item in value if str(item).strip()]
    return [str(value)] if str(value).strip() else []


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


@dataclass(slots=True)
class UnresolvedMemoryCandidate:
    id: str
    raw_text: str
    importance: float = 0.85
    confidence: float = 0.65
    domain_guess: str = "unknown"
    candidate_fact_keys: list[str] = field(default_factory=list)
    extraction_status: CandidateStatus = "unresolved"
    evidence_ids: list[str] = field(default_factory=list)
    source_turn_id: str = ""
    needs_consolidation: bool = True
    consolidation_attempts: int = 0
    promoted_target: str = ""
    promoted_fact_key: str = ""
    created_at: str = ""
    updated_at: str = ""
    decay_policy: str = "slow"
    reason: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "raw_text": self.raw_text,
            "importance": round(float(self.importance), 4),
            "confidence": round(float(self.confidence), 4),
            "domain_guess": self.domain_guess,
            "candidate_fact_keys": list(self.candidate_fact_keys),
            "extraction_status": self.extraction_status,
            "evidence_ids": list(self.evidence_ids),
            "source_turn_id": self.source_turn_id,
            "needs_consolidation": bool(self.needs_consolidation),
            "consolidation_attempts": int(self.consolidation_attempts),
            "promoted_target": self.promoted_target,
            "promoted_fact_key": self.promoted_fact_key,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "decay_policy": self.decay_policy,
            "reason": self.reason,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "UnresolvedMemoryCandidate":
        status = str(data.get("extraction_status") or "unresolved")
        if status not in {"unresolved", "partial", "failed", "promoted", "archived"}:
            status = "unresolved"
        return cls(
            id=str(data.get("id") or f"cand_{uuid.uuid4().hex[:12]}"),
            raw_text=compact_text(data.get("raw_text") or data.get("content") or "", limit=1200),
            importance=clamp01(data.get("importance", 0.85), default=0.85),
            confidence=clamp01(data.get("confidence", 0.65), default=0.65),
            domain_guess=compact_text(data.get("domain_guess") or "unknown", limit=80),
            candidate_fact_keys=_as_list(data.get("candidate_fact_keys"))[:8],
            extraction_status=status,  # type: ignore[arg-type]
            evidence_ids=_as_list(data.get("evidence_ids")),
            source_turn_id=str(data.get("source_turn_id") or ""),
            needs_consolidation=bool(data.get("needs_consolidation", status in {"unresolved", "partial", "failed"})),
            consolidation_attempts=max(0, int(data.get("consolidation_attempts", 0) or 0)),
            promoted_target=str(data.get("promoted_target") or ""),
            promoted_fact_key=str(data.get("promoted_fact_key") or ""),
            created_at=str(data.get("created_at") or iso_now()),
            updated_at=str(data.get("updated_at") or iso_now()),
            decay_policy=str(data.get("decay_policy") or "slow"),
            reason=compact_text(data.get("reason") or "", limit=360),
            metadata=dict(data.get("metadata") or {}),
        )

    @property
    def fingerprint(self) -> str:
        return fingerprint_text(self.raw_text)

    def recall_line(self) -> str:
        return compact_text(
            f"candidate[{self.extraction_status}; importance={self.importance:.2f}; domain={self.domain_guess}]: {self.raw_text}",
            limit=360,
        )


class UnresolvedMemoryStore:
    """JSON store cho unresolved memory candidates."""

    def __init__(self, path: str, *, max_items: int = 500):
        self.path = path
        self.max_items = max(50, int(max_items))

    def load(self) -> list[UnresolvedMemoryCandidate]:
        if not os.path.isfile(self.path) or os.path.getsize(self.path) <= 0:
            return []
        with open(self.path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        raw_items = data.get("items") if isinstance(data, dict) else data
        if not isinstance(raw_items, list):
            return []
        return self._sort_items([UnresolvedMemoryCandidate.from_dict(item) for item in raw_items if isinstance(item, Mapping)])

    def save(self, items: list[UnresolvedMemoryCandidate]) -> None:
        payload = {
            "version": 1,
            "kind": "memory_candidate",
            "updated_at": iso_now(),
            "items": [item.to_dict() for item in self._prune(items)],
        }
        atomic_write_json(self.path, payload)

    def apply_commit(self, request: Any) -> UnresolvedMemoryCandidate | None:
        metadata = _mapping(getattr(request, "metadata", {}) or {})
        raw_content = metadata.get("raw_content")
        data = _mapping(raw_content)
        if not data:
            data = _mapping(getattr(request, "content", None))

        raw_text = compact_text(
            data.get("raw_text")
            or metadata.get("raw_user_text")
            or getattr(request, "content", "")
            or "",
            limit=1200,
        )
        if not raw_text:
            return None

        now = iso_now()
        evidence_ids = _as_list(getattr(request, "evidence_ids", None) or data.get("evidence_ids"))
        source_turn_id = str(data.get("source_turn_id") or (evidence_ids[0] if evidence_ids else ""))
        candidate = UnresolvedMemoryCandidate(
            id=f"cand_{uuid.uuid4().hex[:12]}",
            raw_text=raw_text,
            importance=clamp01(data.get("importance", metadata.get("importance", 0.85)), default=0.85),
            confidence=clamp01(data.get("confidence", getattr(request, "confidence", 0.65)), default=0.65),
            domain_guess=str(data.get("domain_guess") or metadata.get("domain_guess") or "unknown"),
            candidate_fact_keys=_as_list(data.get("candidate_fact_keys") or metadata.get("candidate_fact_keys"))[:8],
            extraction_status=str(data.get("extraction_status") or metadata.get("extraction_status") or "unresolved"),  # type: ignore[arg-type]
            evidence_ids=evidence_ids,
            source_turn_id=source_turn_id,
            needs_consolidation=bool(data.get("needs_consolidation", metadata.get("needs_consolidation", True))),
            created_at=now,
            updated_at=now,
            decay_policy=str(getattr(request, "decay_policy", "slow") or "slow"),
            reason=compact_text(data.get("reason") or getattr(request, "reason", "") or "unresolved_memory_candidate", limit=360),
            metadata={
                **metadata,
                "source_module": getattr(request, "source_module", "") or metadata.get("source_module", ""),
                "commit_reason": getattr(request, "reason", "") or metadata.get("commit_reason", ""),
            },
        )

        items = self.load()
        existing = self._find_existing(items, candidate)
        if existing is not None:
            self._merge(existing, candidate, now=now)
            self.save(items)
            return existing
        items.append(candidate)
        self.save(items)
        return candidate

    def candidates_for_consolidation(self, *, limit: int = 20, max_attempts: int = 5) -> list[UnresolvedMemoryCandidate]:
        items = [
            item for item in self.load()
            if item.needs_consolidation
            and item.extraction_status in {"unresolved", "partial", "failed"}
            and item.consolidation_attempts < max_attempts
        ]
        items.sort(key=lambda item: (item.importance, item.confidence, -item.consolidation_attempts), reverse=True)
        return items[: max(1, int(limit))]

    def mark_attempted(self, candidate_id: str, *, status: CandidateStatus = "failed", reason: str = "") -> None:
        items = self.load()
        for item in items:
            if item.id != candidate_id:
                continue
            item.consolidation_attempts += 1
            item.extraction_status = status
            item.updated_at = iso_now()
            if reason:
                item.metadata["last_consolidation_reason"] = compact_text(reason, limit=260)
            if item.consolidation_attempts >= 5 and status == "failed":
                item.needs_consolidation = False
            self.save(items)
            return

    def mark_promoted(self, candidate_id: str, *, target: str, fact_key: str = "") -> None:
        items = self.load()
        for item in items:
            if item.id != candidate_id:
                continue
            item.extraction_status = "promoted"
            item.needs_consolidation = False
            item.promoted_target = target
            item.promoted_fact_key = fact_key
            item.updated_at = iso_now()
            item.metadata["promoted_at"] = item.updated_at
            self.save(items)
            return

    def search(self, query: str = "", *, limit: int = 8, include_promoted: bool = False) -> list[UnresolvedMemoryCandidate]:
        items = self.load()
        if not include_promoted:
            items = [item for item in items if item.extraction_status != "promoted"]
        if not query:
            return items[:limit]
        tokens = {tok for tok in re.findall(r"[a-z0-9_]+", query.lower()) if len(tok) >= 3}
        scored: list[tuple[float, UnresolvedMemoryCandidate]] = []
        for item in items:
            hay = " ".join([item.raw_text, item.domain_guess, " ".join(item.candidate_fact_keys)]).lower()
            overlap = sum(1 for token in tokens if token in hay)
            if overlap or not tokens:
                score = overlap + item.importance + item.confidence - item.consolidation_attempts * 0.05
                scored.append((score, item))
        scored.sort(key=lambda pair: pair[0], reverse=True)
        return [item for _, item in scored[:limit]]

    def _find_existing(
        self,
        items: list[UnresolvedMemoryCandidate],
        candidate: UnresolvedMemoryCandidate,
    ) -> UnresolvedMemoryCandidate | None:
        fp = candidate.fingerprint
        for item in items:
            if item.fingerprint == fp:
                return item
        return None

    def _merge(self, old: UnresolvedMemoryCandidate, new: UnresolvedMemoryCandidate, *, now: str) -> None:
        old.importance = max(old.importance, new.importance)
        old.confidence = max(old.confidence, new.confidence)
        old.evidence_ids = list(dict.fromkeys([*old.evidence_ids, *new.evidence_ids]))[-12:]
        old.candidate_fact_keys = list(dict.fromkeys([*old.candidate_fact_keys, *new.candidate_fact_keys]))[:8]
        old.updated_at = now
        old.needs_consolidation = old.needs_consolidation or new.needs_consolidation
        old.metadata.update({k: v for k, v in new.metadata.items() if k not in old.metadata})

    def _prune(self, items: list[UnresolvedMemoryCandidate]) -> list[UnresolvedMemoryCandidate]:
        sorted_items = self._sort_items(items)
        protected = [item for item in sorted_items if item.importance >= 0.90 and item.extraction_status != "promoted"]
        rest = [item for item in sorted_items if item not in protected]
        kept = [*protected, *rest]
        return kept[: self.max_items]

    def _sort_items(self, items: list[UnresolvedMemoryCandidate]) -> list[UnresolvedMemoryCandidate]:
        status_weight = {"unresolved": 0.8, "partial": 0.7, "failed": 0.45, "promoted": 0.25, "archived": 0.1}
        return sorted(
            items,
            key=lambda item: (
                item.importance,
                item.confidence,
                status_weight.get(item.extraction_status, 0.0),
                -item.consolidation_attempts,
                item.updated_at,
            ),
            reverse=True,
        )
