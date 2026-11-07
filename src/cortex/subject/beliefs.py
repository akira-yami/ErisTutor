# -*- coding: utf-8 -*-
"""Evidence-traceable belief projection."""
from __future__ import annotations

from dataclasses import replace
from typing import Any, Callable, Iterable, Mapping

from .contracts import Belief, BeliefProposal, BeliefRevision, BeliefSourceKind, VisibilityScope


class BeliefService:
    def __init__(self, id_factory: Callable[[], str], clock: Callable[[], float]) -> None:
        self._id_factory = id_factory
        self._clock = clock
        self._beliefs: dict[str, Belief] = {}

    def all(self) -> tuple[Belief, ...]:
        return tuple(sorted(self._beliefs.values(), key=lambda belief: (belief.updated_at, belief.belief_id)))

    def get(self, belief_id: str) -> Belief | None:
        return self._beliefs.get(belief_id)


    def restore(self, records: Iterable[Belief | Mapping[str, Any]]) -> None:
        """Khôi phục belief projection từ snapshot đã kiểm tra.

        Raw event vẫn là nguồn forensic; hàm này chỉ dựng projection để Eris không
        mất toàn bộ điều đã học sau mỗi lần khởi động lại.
        """
        restored: dict[str, Belief] = {}
        for record in records:
            if isinstance(record, Belief):
                belief = record
            else:
                belief = Belief(
                    belief_id=str(record["belief_id"]),
                    subject=str(record["subject"]),
                    predicate=str(record["predicate"]),
                    object_json=record.get("object_json"),
                    confidence=float(record.get("confidence", 0.5)),
                    source_kind=BeliefSourceKind(str(record.get("source_kind", BeliefSourceKind.INFERENCE.value))),
                    evidence_refs=tuple(str(item) for item in record.get("evidence_refs", ())),
                    counter_evidence_refs=tuple(str(item) for item in record.get("counter_evidence_refs", ())),
                    accessibility=float(record.get("accessibility", 1.0)),
                    salience=float(record.get("salience", 0.5)),
                    retrieval_salience=float(record.get("retrieval_salience", 0.5)),
                    visibility_scope=VisibilityScope(str(record.get("visibility_scope", VisibilityScope.PRIVATE.value))),
                    ownership=str(record.get("ownership", "self")),
                    status=str(record.get("status", "active")),
                    created_at=float(record.get("created_at", 0.0)),
                    updated_at=float(record.get("updated_at", 0.0)),
                    supersedes_id=(str(record["supersedes_id"]) if record.get("supersedes_id") else None),
                    predictive_commitments=tuple(str(item) for item in record.get("predictive_commitments", ())),
                )
            restored[belief.belief_id] = belief
        self._beliefs = restored

    def to_projection(self) -> list[dict[str, Any]]:
        return [
            {
                "belief_id": belief.belief_id,
                "subject": belief.subject,
                "predicate": belief.predicate,
                "object_json": belief.object_json,
                "confidence": belief.confidence,
                "source_kind": belief.source_kind.value,
                "evidence_refs": list(belief.evidence_refs),
                "counter_evidence_refs": list(belief.counter_evidence_refs),
                "accessibility": belief.accessibility,
                "salience": belief.salience,
                "retrieval_salience": belief.retrieval_salience,
                "visibility_scope": belief.visibility_scope.value,
                "ownership": belief.ownership,
                "status": belief.status,
                "created_at": belief.created_at,
                "updated_at": belief.updated_at,
                "supersedes_id": belief.supersedes_id,
                "predictive_commitments": list(belief.predictive_commitments),
            }
            for belief in self.all()
        ]

    def revise(self, proposal: BeliefProposal, evidence: Iterable[str] = ()) -> BeliefRevision:
        evidence_refs = tuple(dict.fromkeys((*proposal.evidence_refs, *tuple(evidence))))
        if proposal.source_kind is not BeliefSourceKind.CONFABULATION_CANDIDATE and not evidence_refs:
            raise ValueError("belief revision requires evidence")

        prior = self._beliefs.get(proposal.supersedes_id or "")
        contradiction = bool(prior and (
            prior.subject != proposal.subject
            or prior.predicate != proposal.predicate
            or prior.object_json != proposal.object_json
        ))
        confidence = proposal.confidence
        if prior and not contradiction:
            confidence = min(1.0, 0.65 * prior.confidence + 0.35 * proposal.confidence + min(0.05, len(evidence_refs) * 0.01))
        elif prior and contradiction:
            confidence = max(0.05, proposal.confidence - min(0.20, prior.confidence * 0.20))

        belief = Belief(
            belief_id=self._id_factory(),
            subject=proposal.subject,
            predicate=proposal.predicate,
            object_json=proposal.object_json,
            confidence=confidence,
            source_kind=proposal.source_kind,
            evidence_refs=evidence_refs,
            counter_evidence_refs=proposal.counter_evidence_refs,
            visibility_scope=proposal.visibility_scope,
            created_at=self._clock(),
            updated_at=self._clock(),
            supersedes_id=prior.belief_id if prior else proposal.supersedes_id,
            predictive_commitments=proposal.predictive_commitments,
        )
        if prior:
            self._beliefs[prior.belief_id] = replace(prior, status="superseded", updated_at=self._clock())
        self._beliefs[belief.belief_id] = belief
        return BeliefRevision(
            prior_belief_id=prior.belief_id if prior else None,
            belief=belief,
            confidence_delta=belief.confidence - (prior.confidence if prior else 0.0),
            contradiction_detected=contradiction,
        )


    def weaken_narrative(
        self,
        belief_ids: Iterable[str],
        *,
        confidence_drop: float,
        retrieval_drop: float,
    ) -> tuple[str, ...]:
        """Hạ confidence/retrieval của self-narrative dự đoán sai.

        Raw evidence không đổi; đây chỉ là projection update có thể rebuild.
        """
        changed: list[str] = []
        for belief_id in tuple(dict.fromkeys(str(x) for x in belief_ids)):
            belief = self._beliefs.get(belief_id)
            if belief is None or belief.status != "active":
                continue
            if belief.source_kind is not BeliefSourceKind.SELF_INTERPRETATION:
                continue
            self._beliefs[belief_id] = replace(
                belief,
                confidence=max(0.05, belief.confidence - max(0.0, confidence_drop)),
                retrieval_salience=max(0.05, belief.retrieval_salience - max(0.0, retrieval_drop)),
                updated_at=self._clock(),
            )
            changed.append(belief_id)
        return tuple(changed)

    def reopen_by_evidence(
        self,
        evidence_refs: Iterable[str],
        *,
        accessibility_boost: float,
        retrieval_boost: float,
        exclude_ids: Iterable[str] = (),
    ) -> tuple[str, ...]:
        """Mở lại belief có chung evidence khi narrative hiện tại thất bại.

        Không tạo evidence mới và không thay source_kind. Cơ chế này mô phỏng việc
        một phần ký ức trước đây ít accessible trở lại đáng chú ý sau prediction error.
        """
        refs = set(str(x) for x in evidence_refs if str(x).strip())
        excluded = set(str(x) for x in exclude_ids)
        if not refs:
            return ()
        changed: list[str] = []
        for belief_id, belief in tuple(self._beliefs.items()):
            if belief_id in excluded or belief.status != "active":
                continue
            if not refs.intersection((*belief.evidence_refs, *belief.counter_evidence_refs)):
                continue
            updated = replace(
                belief,
                accessibility=min(1.0, belief.accessibility + max(0.0, accessibility_boost)),
                retrieval_salience=min(1.0, belief.retrieval_salience + max(0.0, retrieval_boost)),
                updated_at=self._clock(),
            )
            if updated != belief:
                self._beliefs[belief_id] = updated
                changed.append(belief_id)
        return tuple(changed)

    def accessible(self, *, minimum_accessibility: float = 0.5, public_only: bool = False) -> tuple[Belief, ...]:
        output = []
        for belief in self._beliefs.values():
            if belief.status != "active" or belief.accessibility < minimum_accessibility:
                continue
            if public_only and belief.visibility_scope.value != "public":
                continue
            output.append(belief)
        return tuple(sorted(output, key=lambda belief: (-belief.retrieval_salience, -belief.confidence, belief.belief_id)))
