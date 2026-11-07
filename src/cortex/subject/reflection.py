# -*- coding: utf-8 -*-
"""Reflective Self: sửa cách truy cập self-narrative khi prediction thất bại.

Không có ``true_motive``. Mismatch chỉ cho phép hạ độ tin cậy/độ nổi của narrative
đã dự đoán sai và mở lại evidence bị chìm. Raw event/provenance không bao giờ bị sửa.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Iterable

from .beliefs import BeliefService
from .contracts import ReflectiveMismatch


@dataclass(frozen=True, slots=True)
class ReflectiveRevisionReceipt:
    mismatch_ids: tuple[str, ...]
    weakened_belief_ids: tuple[str, ...]
    reopened_belief_ids: tuple[str, ...]
    evidence_refs: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


class ReflectiveSelfRegulator:
    def apply(
        self,
        beliefs: BeliefService,
        mismatches: Iterable[ReflectiveMismatch],
    ) -> ReflectiveRevisionReceipt:
        mismatch_items = tuple(item for item in mismatches if item.status == "open")
        if not mismatch_items:
            return ReflectiveRevisionReceipt((), (), (), ())
        target_ids = tuple(dict.fromkeys(
            belief_id
            for mismatch in mismatch_items
            for belief_id in mismatch.narrative_belief_ids
        ))
        evidence_refs: list[str] = []
        for belief_id in target_ids:
            belief = beliefs.get(belief_id)
            if belief is not None:
                evidence_refs.extend(belief.evidence_refs)
                evidence_refs.extend(belief.counter_evidence_refs)
        severity = max(item.prediction_error for item in mismatch_items)
        weakened = beliefs.weaken_narrative(
            target_ids,
            confidence_drop=min(0.22, 0.08 + 0.18 * severity),
            retrieval_drop=min(0.30, 0.10 + 0.24 * severity),
        )
        reopened = beliefs.reopen_by_evidence(
            tuple(dict.fromkeys(evidence_refs)),
            accessibility_boost=min(0.24, 0.08 + 0.16 * severity),
            retrieval_boost=min(0.30, 0.10 + 0.20 * severity),
            exclude_ids=target_ids,
        )
        return ReflectiveRevisionReceipt(
            mismatch_ids=tuple(item.mismatch_id for item in mismatch_items),
            weakened_belief_ids=weakened,
            reopened_belief_ids=reopened,
            evidence_refs=tuple(dict.fromkeys(evidence_refs)),
        )


__all__ = ["ReflectiveRevisionReceipt", "ReflectiveSelfRegulator"]
