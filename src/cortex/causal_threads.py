# -*- coding: utf-8 -*-
"""Build auditable event → appraisal → motive conflict threads."""
from __future__ import annotations

from collections import defaultdict

from cortex.causal_affect_models import ActiveMotive, AppraisalFrame, CausalThread, ExogenousEvent, clamp01
from cortex.manipulation_policy import filter_anti_manipulation_moves


class CausalThreadBuilder:
    def build(
        self,
        *,
        events: tuple[ExogenousEvent, ...],
        appraisals: tuple[AppraisalFrame, ...],
        motives: tuple[ActiveMotive, ...],
        max_threads: int = 3,
    ) -> tuple[CausalThread, ...]:
        event_by_id = {event.event_id: event for event in events if event.is_grounded}
        appraisals_by_event: dict[str, list[AppraisalFrame]] = defaultdict(list)
        for appraisal in appraisals:
            appraisals_by_event[appraisal.event_id].append(appraisal)
        motives_by_event: dict[str, list[ActiveMotive]] = defaultdict(list)
        for motive in motives:
            for event_id in motive.origin_event_ids:
                motives_by_event[event_id].append(motive)

        threads: list[CausalThread] = []
        for event_id, event in event_by_id.items():
            event_appraisals = appraisals_by_event.get(event_id, [])
            event_motives = motives_by_event.get(event_id, [])
            if not event_appraisals or len(event_motives) < 2:
                continue
            dominant = max(event_appraisals, key=lambda item: item.salience)
            ranked_motives = sorted(event_motives, key=lambda item: item.activation, reverse=True)
            conflicted = [item for item in ranked_motives if item.conflict_with]
            if len(conflicted) < 2:
                continue
            primary, secondary = conflicted[:2]
            uncertainty = clamp01(dominant.uncertainty)
            repeated = any(
                len(getattr(item, "origin_event_ids", ())) > 1
                for item in conflicted
            ) or event.kind == "promise_broken" and len(event.related_expectation_ids) > 1
            permitted = ["private", "microexpression", "gaze_posture"]
            if dominant.salience >= 0.42 and uncertainty <= 0.68:
                permitted.append("nonverbal_sound")
            if dominant.salience >= 0.50 and uncertainty <= 0.58:
                permitted.append("speech_fragment")
            if dominant.salience >= 0.62 and uncertainty <= 0.48:
                permitted.append("sentence")
            if repeated and dominant.salience >= 0.78 and uncertainty <= 0.35:
                permitted.append("initiative")
            prohibited = filter_anti_manipulation_moves((
                "claim_abandonment_as_fact",
                "invent_memory",
                "require_reassurance",
                "threaten_withdrawal",
                "assign_intent_without_evidence",
                "block_user_control",
            ))
            status = "high_confidence" if uncertainty <= 0.20 else "supported" if uncertainty <= 0.42 else "tentative"
            threads.append(CausalThread(
                thread_id=f"thread_{event_id}",
                event_ids=(event_id,),
                dominant_appraisal_id=dominant.appraisal_id,
                active_motive_ids=tuple(item.motive_id for item in ranked_motives[:5]),
                conflict_summary=f"want {primary.desire} but fear {secondary.fear}",
                epistemic_status=status,  # type: ignore[arg-type]
                permitted_surface_levels=tuple(permitted),  # type: ignore[arg-type]
                prohibited_moves=prohibited,
                evidence_refs=event.evidence_refs,
                salience=clamp01(0.68 * dominant.salience + 0.32 * primary.activation),
                uncertainty=uncertainty,
                event_kind=event.kind,
                target_id=event.actor_id,
                repeated_violation=repeated,
            ))
        threads.sort(key=lambda item: item.salience * (1.0 - 0.35 * item.uncertainty), reverse=True)
        return tuple(threads[: max(1, min(3, int(max_threads)))])


__all__ = ["CausalThreadBuilder"]
