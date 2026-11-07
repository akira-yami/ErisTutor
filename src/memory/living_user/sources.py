# -*- coding: utf-8 -*-
"""Theo dõi và giải thích nguồn của living user memory."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable

from .models import EvidenceReference, ProfileRecord, compact_text


@dataclass(slots=True)
class MemoryExplanation:
    path: str
    value: Any
    confidence: float
    explicitness: str
    sources: tuple[EvidenceReference, ...] = ()
    note: str = ""


class MemorySourceTracker:
    """Resolve evidence id về ledger/episode/semantic item.

    Tracker đọc store đã có, không sao chép raw chat sang profile file. Một profile
    record chỉ giữ evidence id; lúc cần giải thích mới dereference nguồn.
    """

    def __init__(self, *, event_ledger: Any = None, episodic_store: Any = None, semantic_store: Any = None) -> None:
        self.event_ledger = event_ledger
        self.episodic_store = episodic_store
        self.semantic_store = semantic_store

    def resolve(self, evidence_ids: Iterable[str], *, limit: int = 12) -> tuple[EvidenceReference, ...]:
        wanted = tuple(dict.fromkeys(str(x) for x in evidence_ids if str(x).strip()))[: max(1, limit)]
        if not wanted:
            return ()
        found: dict[str, EvidenceReference] = {}
        self._resolve_event_ledger(wanted, found)
        self._resolve_episodic(wanted, found)
        self._resolve_semantic(wanted, found)
        return tuple(found[eid] for eid in wanted if eid in found)

    def explain(self, record: ProfileRecord) -> MemoryExplanation:
        sources = self.resolve(record.evidence_ids)
        if record.explicitness == "explicit":
            note = "Thông tin được người dùng nói rõ hoặc sửa trực tiếp."
        elif record.explicitness == "observed":
            note = "Pattern được củng cố từ nhiều hành vi/lượt tương tác."
        else:
            note = "Suy luận thận trọng; không nên phát biểu như fact chắc chắn."
        return MemoryExplanation(
            path=record.path,
            value=record.value,
            confidence=record.confidence,
            explicitness=record.explicitness,
            sources=sources,
            note=note,
        )

    def _resolve_event_ledger(self, wanted: tuple[str, ...], found: dict[str, EvidenceReference]) -> None:
        loader = getattr(self.event_ledger, "load", None)
        if not callable(loader):
            return
        try:
            items = loader()
        except Exception:
            return
        wanted_set = set(wanted)
        for item in reversed(items):
            candidates = {str(getattr(item, "id", "") or ""), *[str(x) for x in getattr(item, "evidence_ids", ()) or ()]}
            hits = wanted_set & candidates
            for eid in hits:
                found.setdefault(eid, EvidenceReference(
                    id=eid,
                    source_type="event_ledger",
                    excerpt=compact_text(getattr(item, "content", ""), 500),
                    timestamp=float(getattr(item, "timestamp", 0.0) or 0.0),
                    metadata={"source": str(getattr(item, "source", "") or ""), "frame_id": str(getattr(item, "frame_id", "") or "")},
                ))

    def _resolve_episodic(self, wanted: tuple[str, ...], found: dict[str, EvidenceReference]) -> None:
        loader = getattr(self.episodic_store, "load", None)
        if not callable(loader):
            return
        try:
            items = loader()
        except Exception:
            return
        wanted_set = set(wanted)
        for item in reversed(items):
            candidates = {str(getattr(item, "id", "") or ""), *[str(x) for x in getattr(item, "evidence_ids", ()) or ()]}
            for eid in wanted_set & candidates:
                found.setdefault(eid, EvidenceReference(
                    id=eid,
                    source_type="episodic_memory",
                    excerpt=compact_text(getattr(item, "summary", "") or getattr(item, "recall_line", lambda: "")(), 500),
                    metadata={"frame_id": str(getattr(item, "frame_id", "") or "")},
                ))

    def _resolve_semantic(self, wanted: tuple[str, ...], found: dict[str, EvidenceReference]) -> None:
        loader = getattr(self.semantic_store, "load", None)
        if not callable(loader):
            return
        try:
            items = loader()
        except Exception:
            return
        wanted_set = set(wanted)
        for item in reversed(items):
            candidates = {str(getattr(item, "id", "") or ""), *[str(x) for x in getattr(item, "evidence_ids", ()) or ()]}
            for eid in wanted_set & candidates:
                found.setdefault(eid, EvidenceReference(
                    id=eid,
                    source_type="semantic_memory",
                    excerpt=compact_text(getattr(item, "claim", ""), 500),
                    metadata={"fact_key": str(getattr(item, "fact_key", "") or ""), "status": str(getattr(item, "status", "") or "")},
                ))


__all__ = ["MemoryExplanation", "MemorySourceTracker"]
