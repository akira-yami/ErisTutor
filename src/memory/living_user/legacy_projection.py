# -*- coding: utf-8 -*-
"""Projection tương thích API ``cortex.private_mind.UserModelStore``.

Mục tiêu là cho private mind đọc living profile mới mà không cần thay cả subsystem
trong một lần. Projection không tạo nguồn chân lý thứ hai; mọi mutation đi thẳng
về UserProfileStore canonical.
"""
from __future__ import annotations

from typing import Any, Iterable, Mapping

from .models import ProfileOperation, UserMemoryScope
from .service import LivingUserMemoryService


class LivingUserModelProjection:
    def __init__(self, service: LivingUserMemoryService) -> None:
        self.service = service

    def __len__(self) -> int:
        return len(self.service.profile_store.all())

    def all(self) -> tuple[Any, ...]:
        return self.service.profile_store.all()

    def by_facet(self, facet: str) -> tuple[Any, ...]:
        prefix = _facet_prefix(facet)
        return self.service.profile_store.query_prefix(prefix)

    def model_thickness(self) -> float:
        records = self.service.profile_store.all()
        if not records:
            return 0.0
        roots = {r.path.split(".", 1)[0] for r in records}
        average = sum(r.trust_weight for r in records) / len(records)
        coverage = min(1.0, len(roots) / 7.0)
        volume = min(1.0, len(records) / 18.0)
        return min(1.0, average * 0.55 + coverage * 0.25 + volume * 0.20)

    def snapshot_for_dossier(self, *, limit: int = 12) -> dict[str, Any]:
        records = self.service.profile_store.all()[: max(0, limit)]
        facets: dict[str, list[dict[str, Any]]] = {}
        for record in records:
            facet = _path_facet(record.path)
            facets.setdefault(facet, []).append({
                "claim": f"{record.path}: {record.value}",
                "confidence": round(record.confidence, 3),
                "recurrence": record.recurrence,
                "evidence_ids": list(record.evidence_ids[:6]),
                "explicitness": record.explicitness,
            })
        return {
            "version": self.service.profile_store.version,
            "belief_count": len(records),
            "model_thickness": round(self.model_thickness(), 3),
            "facets": facets,
        }

    def update_from_fragments(self, fragments: Iterable[Any]) -> tuple[Any, ...]:
        ops: list[ProfileOperation] = []
        for fragment in fragments:
            ids = tuple(getattr(fragment, "evidence_ids", ()) or ())
            claim = str(getattr(fragment, "claim", "") or "").strip()
            if not ids or not claim:
                continue
            metadata = dict(getattr(fragment, "metadata", {}) or {})
            facet = str(metadata.get("facet") or "general")
            ops.append(ProfileOperation(
                op="reinforce",
                path=f"relationship.private_mind.{facet}",
                value=claim,
                confidence=float(getattr(fragment, "confidence", 0.5) or 0.5),
                explicitness="inferred",
                scope=UserMemoryScope("private"),
                cardinality="multi",
                evidence_ids=ids,
                reason="PrivateMind evaluator củng cố model về user.",
                source=str(getattr(fragment, "source", "private_mind") or "private_mind"),
            ))
        result = self.service.profile_store.apply(ops, checkpoint_reason="private_mind_fragments")
        records = []
        for record_id in result.record_ids:
            record = next((x for x in self.service.profile_store.all(include_inactive=True) if x.id == record_id), None)
            if record is not None:
                records.append(record)
        return tuple(records)

    def decay(self, *, now: float | None = None) -> None:
        self.service.profile_store.decay(now=now)

    def checkpoint(self) -> int:
        # Store tự checkpoint theo transaction. Trả current version cho API cũ.
        return self.service.profile_store.version

    def revert_to(self, version: int) -> bool:
        return self.service.profile_store.revert_to(version)

    def reset_to_baseline(self) -> None:
        self.service.profile_store.forget("", hard=False, reason="reset_to_baseline")
        # ``forget('')`` cố ý không chạy; xóa theo từng root để tránh API hard-delete vô tình.
        roots = {r.path.split(".", 1)[0] for r in self.service.profile_store.all()}
        for root in roots:
            self.service.profile_store.forget(root, hard=False, reason="reset_to_baseline")


def _facet_prefix(facet: str) -> str:
    mapping = {
        "interaction_style": "communication",
        "topic_affinity": "preferences",
        "treats_eris_as": "relationship",
        "leaving_pattern": "relationship",
        "recognition": "relationship",
        "neo_reliability": "relationship",
    }
    return mapping.get(str(facet or "general"), "relationship")


def _path_facet(path: str) -> str:
    if path.startswith("communication."):
        return "interaction_style"
    if path.startswith("preferences."):
        return "topic_affinity"
    if path.startswith("relationship.private_mind."):
        return path.rsplit(".", 1)[-1]
    if path.startswith("relationship."):
        return "treats_eris_as"
    return "general"


__all__ = ["LivingUserModelProjection"]
