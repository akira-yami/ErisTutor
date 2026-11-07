# -*- coding: utf-8 -*-
"""Các hợp đồng domain cho một vòng deliberation có điều khiển.

Module này cố ý không biết LLM, runtime hay tool cụ thể. Cognitive layer chỉ được
trả về các adjustment có kiểu; Subject Core kiểm tra và tích hợp chúng. Nhờ vậy
Executive có thể yêu cầu suy nghĩ lại mà không được phép tự ý viết lại substrate,
evidence hoặc lịch sử bằng một đoạn văn nghe có vẻ hợp lý.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import StrEnum
from typing import Any, Mapping, Sequence

from .coalitions import CoalitionArbiter
from .contracts import (
    ActionProposal,
    EvidenceRef,
    Score01,
    SubjectDeliberationFrame,
    clamp01,
    ensure_non_empty,
    freeze_mapping,
)


class CognitiveRequestKind(StrEnum):
    """Các thao tác nhận thức bounded mà Executive có thể yêu cầu."""

    VERIFY_EVIDENCE = "verify_evidence"
    REDUCE_INTRUSION = "reduce_intrusion"
    PRESERVE_CONTINUITY = "preserve_continuity"
    ESTIMATE_CAPABILITY = "estimate_capability"
    INSPECT_DISSENT = "inspect_dissent"

    @classmethod
    def normalize_many(cls, values: Sequence[str]) -> tuple["CognitiveRequestKind", ...]:
        normalized: list[CognitiveRequestKind] = []
        for value in values:
            try:
                item = cls(str(value or "").strip().lower())
            except ValueError:
                continue
            if item not in normalized:
                normalized.append(item)
        return tuple(normalized)


@dataclass(frozen=True, slots=True)
class ProposalAdjustment:
    """Một thay đổi bounded dành cho đúng một proposal đã tồn tại.

    Mọi delta đều bị giới hạn ở [-1, 1], còn giá trị sau tích hợp vẫn được clamp
    về [0, 1]. Cognitive layer không được tạo proposal mới hoặc đổi action_kind.
    """

    proposal_ref: str
    expected_need_relief_delta: float = 0.0
    expected_action_efficacy_delta: float = 0.0
    expected_epistemic_value_delta: float = 0.0
    value_congruence_delta: float = 0.0
    substrate_cost_delta: float = 0.0
    uncertainty_delta: float = 0.0
    commitment_conflict_delta: float = 0.0
    argument_annotations: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        ensure_non_empty(self.proposal_ref, field_name="proposal_ref")
        for field_name in (
            "expected_need_relief_delta",
            "expected_action_efficacy_delta",
            "expected_epistemic_value_delta",
            "value_congruence_delta",
            "substrate_cost_delta",
            "uncertainty_delta",
            "commitment_conflict_delta",
        ):
            value = float(getattr(self, field_name))
            if value < -1.0 or value > 1.0:
                raise ValueError(f"{field_name} must be in [-1, 1]")
        object.__setattr__(self, "argument_annotations", freeze_mapping(self.argument_annotations or {}))


@dataclass(frozen=True, slots=True)
class CognitiveReviewResult:
    """Kết quả có provenance của một cognitive capability."""

    result_id: str
    frame_id: str
    request_kind: CognitiveRequestKind
    summary: str
    confidence: Score01
    adjustments: tuple[ProposalAdjustment, ...] = ()
    evidence_refs: tuple[EvidenceRef, ...] = ()

    def __post_init__(self) -> None:
        ensure_non_empty(self.result_id, field_name="result_id")
        ensure_non_empty(self.frame_id, field_name="frame_id")
        ensure_non_empty(self.summary, field_name="summary")
        clamp01(self.confidence, field_name="confidence")


class DeliberationReviewIntegrator:
    """Tích hợp cognitive findings vào frame mà không trao quyền domain cho runtime."""

    def __init__(self, arbiter: CoalitionArbiter) -> None:
        self._arbiter = arbiter

    @staticmethod
    def _bounded(value: float) -> float:
        return max(0.0, min(1.0, float(value)))

    def integrate(
        self,
        frame: SubjectDeliberationFrame,
        results: Sequence[CognitiveReviewResult],
        *,
        frame_id: str,
        created_at: float,
    ) -> SubjectDeliberationFrame:
        if not results:
            return frame
        by_proposal: dict[str, list[tuple[CognitiveReviewResult, ProposalAdjustment]]] = {}
        for result in results:
            if result.frame_id != frame.frame_id:
                raise ValueError("cognitive result targets another deliberation frame")
            for adjustment in result.adjustments:
                by_proposal.setdefault(adjustment.proposal_ref, []).append((result, adjustment))

        known = {proposal.proposal_id for proposal in frame.proposals}
        unknown = set(by_proposal) - known
        if unknown:
            raise ValueError(f"cognitive result references unknown proposals: {sorted(unknown)}")

        revised: list[ActionProposal] = []
        for proposal in frame.proposals:
            annotations: dict[str, Any] = dict(proposal.arguments)
            review_trace: dict[str, Any] = dict(annotations.get("executive_review") or {})
            relief = proposal.expected_need_relief
            efficacy = proposal.expected_action_efficacy
            epistemic = proposal.expected_epistemic_value
            value_congruence = proposal.value_congruence
            substrate_cost = proposal.substrate_cost
            uncertainty = proposal.uncertainty
            commitment_conflict = proposal.commitment_conflict

            for result, adjustment in by_proposal.get(proposal.proposal_id, ()):
                # Confidence giảm biên tác động nhưng không thể làm delta đổi dấu.
                weight = max(0.0, min(1.0, result.confidence))
                relief += adjustment.expected_need_relief_delta * weight
                efficacy += adjustment.expected_action_efficacy_delta * weight
                epistemic += adjustment.expected_epistemic_value_delta * weight
                value_congruence += adjustment.value_congruence_delta * weight
                substrate_cost += adjustment.substrate_cost_delta * weight
                uncertainty += adjustment.uncertainty_delta * weight
                commitment_conflict += adjustment.commitment_conflict_delta * weight
                if adjustment.argument_annotations:
                    review_trace[result.request_kind.value] = dict(adjustment.argument_annotations)
                else:
                    review_trace[result.request_kind.value] = result.summary

            if review_trace:
                annotations["executive_review"] = review_trace
            revised.append(
                replace(
                    proposal,
                    arguments=annotations,
                    expected_need_relief=self._bounded(relief),
                    expected_action_efficacy=self._bounded(efficacy),
                    expected_epistemic_value=self._bounded(epistemic),
                    value_congruence=self._bounded(value_congruence),
                    substrate_cost=self._bounded(substrate_cost),
                    uncertainty=self._bounded(uncertainty),
                    commitment_conflict=self._bounded(commitment_conflict),
                )
            )

        return SubjectDeliberationFrame(
            frame_id=frame_id,
            observation=frame.observation,
            substrate=frame.substrate,
            appraisals=frame.appraisals,
            activations=frame.activations,
            proposals=tuple(revised),
            coalitions=self._arbiter.rank(tuple(revised)),
            context_key=frame.context_key,
            belief_refs=frame.belief_refs,
            value_refs=frame.value_refs,
            created_at=created_at,
            revision=frame.revision,
        )


__all__ = [
    "CognitiveRequestKind",
    "CognitiveReviewResult",
    "DeliberationReviewIntegrator",
    "ProposalAdjustment",
]
