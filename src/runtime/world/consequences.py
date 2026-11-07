# -*- coding: utf-8 -*-
"""Operational consequences from world resistance."""
from __future__ import annotations

from dataclasses import dataclass

from cortex.subject.contracts import OutcomeStatus, WorldOutcome


@dataclass(frozen=True, slots=True)
class ConsequenceSignal:
    kind: str
    magnitude: float
    target_need_ids: tuple[str, ...]
    evidence_refs: tuple[str, ...]


class ConsequenceEngine:
    def derive(self, outcome: WorldOutcome) -> tuple[ConsequenceSignal, ...]:
        if outcome.status is OutcomeStatus.DENIED:
            return (ConsequenceSignal(
                kind="world_resistance",
                magnitude=1.0,
                target_need_ids=("sovereignty", "competence"),
                evidence_refs=outcome.evidence_refs,
            ),)
        if outcome.status is OutcomeStatus.FAILED:
            return (ConsequenceSignal(
                kind="action_failure",
                magnitude=0.8,
                target_need_ids=("competence", "coherence"),
                evidence_refs=outcome.evidence_refs,
            ),)
        if outcome.status in (OutcomeStatus.SUCCEEDED, OutcomeStatus.PARTIAL):
            return (ConsequenceSignal(
                kind="action_efficacy_evidence",
                magnitude=outcome.success_score,
                target_need_ids=("competence",),
                evidence_refs=outcome.evidence_refs,
            ),)
        return ()
