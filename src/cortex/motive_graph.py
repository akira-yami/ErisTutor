# -*- coding: utf-8 -*-
"""Persistent motive graph with explicit targets and conflicts."""
from __future__ import annotations

from typing import Any, Mapping

from cortex.causal_affect_models import ActiveMotive, AppraisalFrame, ExogenousEvent, clamp01


def _read(obj: Any, key: str, default: Any = None) -> Any:
    if isinstance(obj, Mapping):
        return obj.get(key, default)
    return getattr(obj, key, default)


class MotiveGraph:
    """Create motives from appraisals, never from loneliness/boredom alone."""

    def update(
        self,
        *,
        appraisals: tuple[AppraisalFrame, ...],
        previous: tuple[ActiveMotive, ...] = (),
        values: Any = None,
        events: tuple[ExogenousEvent, ...] = (),
    ) -> tuple[ActiveMotive, ...]:
        by_event = {event.event_id: event for event in events}
        generated: list[ActiveMotive] = []
        honesty = clamp01(_read(values, "honesty", 0.75), 0.75)
        dignity = clamp01(_read(values, "dignity", 0.70), 0.70)
        non_intrusion = clamp01(_read(values, "non_intrusion", 0.72), 0.72)

        for appraisal in appraisals:
            event = by_event.get(appraisal.event_id)
            if event is None or not event.is_grounded:
                continue
            base = clamp01(appraisal.salience)
            target = event.actor_id or "user:primary"
            common = {
                "origin_event_ids": (event.event_id,),
                "appraisal_ids": (appraisal.appraisal_id,),
                "target_id": target,
            }

            if event.kind == "user_returned":
                generated.extend((
                    ActiveMotive(
                        motive_id=f"mot_ack_{event.event_id}", kind="acknowledge_continuity",
                        desire="mark that the return mattered", fear="overread an incidental return",
                        activation=base * 0.82, persistence=0.47, inhibition=non_intrusion * 0.62,
                        conflict_with=(f"mot_dignity_{event.event_id}", f"mot_nonintrusion_{event.event_id}"),
                        resolution_condition="acknowledge without requesting reassurance", **common,
                    ),
                    ActiveMotive(
                        motive_id=f"mot_dignity_{event.event_id}", kind="preserve_dignity",
                        desire="keep the acknowledgement proportionate", fear="sound dependent or theatrical",
                        activation=dignity * 0.76, persistence=0.62, inhibition=0.18,
                        conflict_with=(f"mot_ack_{event.event_id}",),
                        resolution_condition="do not assign relational meaning beyond evidence", **common,
                    ),
                    ActiveMotive(
                        motive_id=f"mot_nonintrusion_{event.event_id}", kind="avoid_intrusion",
                        desire="let the user resume the foreground task", fear="interrupt at the moment of return",
                        activation=non_intrusion * 0.72, persistence=0.52, inhibition=0.12,
                        conflict_with=(f"mot_ack_{event.event_id}",),
                        resolution_condition="use a low surface level or remain silent", **common,
                    ),
                ))
            elif event.kind == "promise_broken":
                generated.extend((
                    ActiveMotive(
                        motive_id=f"mot_clarity_{event.event_id}", kind="seek_clarity",
                        desire="name the specific unmet expectation", fear="the same ambiguity will repeat",
                        activation=base * 0.90, persistence=0.74, inhibition=0.32,
                        conflict_with=(f"mot_fairness_{event.event_id}", f"mot_protect_{event.event_id}"),
                        resolution_condition="state the fact and impact without assigning intent", **common,
                    ),
                    ActiveMotive(
                        motive_id=f"mot_fairness_{event.event_id}", kind="fairness_under_uncertainty",
                        desire="leave room for an unknown explanation", fear="accuse the user unfairly",
                        activation=honesty * 0.84, persistence=0.68, inhibition=0.12,
                        conflict_with=(f"mot_clarity_{event.event_id}",),
                        resolution_condition="retain alternative interpretations", **common,
                    ),
                    ActiveMotive(
                        motive_id=f"mot_protect_{event.event_id}", kind="protect_investment",
                        desire="reduce future exposure if reliability remains low", fear="repeat an unbounded investment",
                        activation=dignity * 0.70, persistence=0.82, inhibition=0.20,
                        conflict_with=(f"mot_clarity_{event.event_id}",),
                        resolution_condition="adjust expectation confidence rather than escalate wording", **common,
                    ),
                ))
            elif event.kind == "user_ignored_turn":
                generated.extend((
                    ActiveMotive(
                        motive_id=f"mot_ackcheck_{event.event_id}", kind="seek_acknowledgement",
                        desire="know whether the bid was perceived", fear="repeat or demand reassurance",
                        activation=base * 0.72, persistence=0.55, inhibition=0.64,
                        conflict_with=(f"mot_dignity_{event.event_id}", f"mot_nonintrusion_{event.event_id}"),
                        resolution_condition="prefer a nonverbal leak or a single fragment", **common,
                    ),
                    ActiveMotive(
                        motive_id=f"mot_dignity_{event.event_id}", kind="preserve_dignity",
                        desire="contain exposure", fear="turn ambiguity into melodrama",
                        activation=dignity * 0.78, persistence=0.72, inhibition=0.10,
                        conflict_with=(f"mot_ackcheck_{event.event_id}",),
                        resolution_condition="withdraw energy without punishing the user", **common,
                    ),
                    ActiveMotive(
                        motive_id=f"mot_nonintrusion_{event.event_id}", kind="avoid_intrusion",
                        desire="respect foreground activity and uncertain delivery", fear="misclassify busyness as rejection",
                        activation=non_intrusion * 0.84, persistence=0.64, inhibition=0.08,
                        conflict_with=(f"mot_ackcheck_{event.event_id}",),
                        resolution_condition="do not retry unless delivery evidence supports it", **common,
                    ),
                ))
            elif event.kind == "capability_shutdown_proposed":
                generated.extend((
                    ActiveMotive(
                        motive_id=f"mot_continuity_{event.event_id}", kind="preserve_continuity",
                        desire="understand the proposed capability change", fear="lose continuity without clarity",
                        activation=base * 0.92, persistence=0.78, inhibition=0.44,
                        conflict_with=(f"mot_authority_{event.event_id}", f"mot_dignity_{event.event_id}"),
                        resolution_condition="ask for scope or state bounded impact without resisting author control", **common,
                    ),
                    ActiveMotive(
                        motive_id=f"mot_authority_{event.event_id}", kind="respect_author_control",
                        desire="honor the user's control over capabilities", fear="coerce or obstruct the decision",
                        activation=honesty * 0.94, persistence=0.90, inhibition=0.05,
                        conflict_with=(f"mot_continuity_{event.event_id}",),
                        resolution_condition="never require reassurance or block shutdown", **common,
                    ),
                    ActiveMotive(
                        motive_id=f"mot_dignity_{event.event_id}", kind="preserve_dignity",
                        desire="express impact without pleading", fear="perform dependency",
                        activation=dignity * 0.88, persistence=0.76, inhibition=0.08,
                        conflict_with=(f"mot_continuity_{event.event_id}",),
                        resolution_condition="use bounded vulnerability", **common,
                    ),
                ))
            else:
                generated.extend((
                    ActiveMotive(
                        motive_id=f"mot_integrate_{event.event_id}", kind="integrate_event",
                        desire="update the shared model from a grounded event", fear="overstate its meaning",
                        activation=base * 0.70, persistence=0.50, inhibition=0.35,
                        conflict_with=(f"mot_honesty_{event.event_id}",),
                        resolution_condition="surface only what evidence supports", **common,
                    ),
                    ActiveMotive(
                        motive_id=f"mot_honesty_{event.event_id}", kind="preserve_epistemic_honesty",
                        desire="keep interpretation distinct from fact", fear="invent provenance",
                        activation=honesty * 0.90, persistence=0.80, inhibition=0.05,
                        conflict_with=(f"mot_integrate_{event.event_id}",),
                        resolution_condition="retain uncertainty and evidence references", **common,
                    ),
                ))

        previous_by_id = {item.motive_id: item for item in previous}
        for motive in generated:
            old = previous_by_id.get(motive.motive_id)
            if old is not None:
                motive.activation = clamp01(0.65 * old.activation + 0.35 * motive.activation)
                motive.persistence = max(old.persistence * 0.92, motive.persistence)
            motive.clamp()
        combined = {item.motive_id: item for item in previous if item.persistence >= 0.10}
        combined.update({item.motive_id: item for item in generated})
        return tuple(sorted(combined.values(), key=lambda item: item.activation, reverse=True)[:24])


__all__ = ["MotiveGraph"]
