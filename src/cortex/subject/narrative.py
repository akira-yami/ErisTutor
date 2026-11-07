# -*- coding: utf-8 -*-
"""Non-omniscient narrative interpretation."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Mapping

from .contracts import BeliefProposal, BeliefSourceKind, NarrativeInterpretation, VisibilityScope


@dataclass(frozen=True, slots=True)
class NarrativeView:
    action_id: str
    action_kind: str
    visible_evidence_refs: tuple[str, ...]
    accessible_belief_summaries: tuple[str, ...]
    outcome_summary: str
    current_value_statements: tuple[str, ...]
    public_context: Mapping[str, Any]
    omitted_evidence_classes: tuple[str, ...] = ()


class NarrativeBeliefGenerator:
    def __init__(self, id_factory: Callable[[], str], clock: Callable[[], float]) -> None:
        self._id_factory = id_factory
        self._clock = clock

    def interpret(self, view: NarrativeView | Mapping[str, Any]) -> tuple[BeliefProposal, ...]:
        if isinstance(view, Mapping):
            view = NarrativeView(
                action_id=str(view.get("action_id") or ""),
                action_kind=str(view.get("action_kind") or "unknown"),
                visible_evidence_refs=tuple(view.get("visible_evidence_refs") or ()),
                accessible_belief_summaries=tuple(view.get("accessible_belief_summaries") or ()),
                outcome_summary=str(view.get("outcome_summary") or "unknown outcome"),
                current_value_statements=tuple(view.get("current_value_statements") or ()),
                public_context=dict(view.get("public_context") or {}),
                omitted_evidence_classes=tuple(view.get("omitted_evidence_classes") or ()),
            )
        if not view.visible_evidence_refs:
            return ()

        candidates: list[tuple[str, float]] = []
        if view.current_value_statements:
            candidates.append((f"The action was compatible with value: {view.current_value_statements[0]}", 0.62))
        if view.accessible_belief_summaries:
            candidates.append((f"The action followed an accessible expectation: {view.accessible_belief_summaries[0]}", 0.58))
        candidates.append((f"The action {view.action_kind} was selected under the visible context and produced {view.outcome_summary}", 0.52))
        candidates = candidates[:4]

        proposals = []
        for explanation, confidence in candidates:
            proposals.append(BeliefProposal(
                proposal_id=self._id_factory(),
                subject="self",
                predicate="interprets_action",
                object_json={"action_id": view.action_id, "explanation": explanation},
                confidence=confidence,
                source_kind=BeliefSourceKind.SELF_INTERPRETATION,
                evidence_refs=view.visible_evidence_refs,
                visibility_scope=VisibilityScope.REFLECTIVE,
                predictive_commitments=(f"similar_context_may_repeat:{view.action_kind}",),
            ))
        return tuple(proposals)

    def choose(self, proposals: tuple[BeliefProposal, ...]) -> BeliefProposal | None:
        """Chọn hypothesis có score cao nhất mà không giả có true motive.

        Score chỉ dùng confidence + evidence coverage + predictive commitment.
        Ties giữ thứ tự deterministic từ generator để replay ổn định.
        """
        if not proposals:
            return None
        def score(item: BeliefProposal) -> tuple[float, float, int]:
            coverage = min(1.0, len(item.evidence_refs) / 4.0)
            predictive = min(1.0, len(item.predictive_commitments) / 2.0)
            return (0.65 * item.confidence + 0.25 * coverage + 0.10 * predictive, item.confidence, len(item.evidence_refs))
        return max(enumerate(proposals), key=lambda pair: (*score(pair[1]), -pair[0]))[1]

    def record(
        self,
        *,
        action_id: str,
        winning: BeliefProposal,
        created_belief_ids: tuple[str, ...],
        omitted_evidence_classes: tuple[str, ...] = (),
    ) -> NarrativeInterpretation:
        explanation = winning.object_json.get("explanation", "") if isinstance(winning.object_json, dict) else str(winning.object_json)
        return NarrativeInterpretation(
            interpretation_id=self._id_factory(),
            action_id=action_id,
            proposed_explanation=str(explanation),
            confidence=winning.confidence,
            visible_evidence_refs=winning.evidence_refs,
            omitted_evidence_classes=omitted_evidence_classes,
            predictive_commitments=winning.predictive_commitments,
            created_belief_ids=created_belief_ids,
        )
