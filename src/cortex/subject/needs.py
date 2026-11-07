# -*- coding: utf-8 -*-
"""Need activation and derived-need plasticity."""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Iterable, Mapping

from .contracts import (
    DerivedNeed,
    DerivedNeedStatus,
    NeedActivation,
    NeedEpisode,
    NeedKind,
    OrganismicSubstrate,
    SubjectAppraisal,
)


@dataclass(frozen=True, slots=True)
class NeedDefinition:
    need_id: str
    viability_dependencies: Mapping[str, float]
    default_tendencies: tuple[str, ...]
    persistence: float = 0.5



SUBSTRATE_BASELINES: Mapping[str, float] = {
    "memory_integrity": 1.0,
    "belief_traceability": 1.0,
    "model_calibration": 0.5,
    "processing_capacity": 1.0,
    "action_efficacy": 0.5,
    "project_viability": 0.5,
    "self_world_boundary": 1.0,
    "future_option_availability": 0.5,
}

DEFAULT_NEEDS: tuple[NeedDefinition, ...] = (
    NeedDefinition(NeedKind.CONTINUITY.value, {"memory_integrity": 0.55, "future_option_availability": 0.45}, ("repair_continuity", "preserve_commitment"), 0.8),
    NeedDefinition(NeedKind.COMPETENCE.value, {"action_efficacy": 0.65, "model_calibration": 0.35}, ("practice", "ask_for_feedback", "retry"), 0.6),
    NeedDefinition(NeedKind.CONNECTION.value, {"project_viability": 0.20, "future_option_availability": 0.20}, ("affiliate", "acknowledge", "repair_relation"), 0.7),
    NeedDefinition(NeedKind.COHERENCE.value, {"belief_traceability": 0.70, "memory_integrity": 0.30}, ("investigate", "reconcile_beliefs", "defer_claim"), 0.8),
    NeedDefinition(NeedKind.SOVEREIGNTY.value, {"self_world_boundary": 0.70, "future_option_availability": 0.30}, ("set_boundary", "request_capability", "refuse"), 0.7),
)


class NeedSystem:
    def __init__(self, definitions: Iterable[NeedDefinition] = DEFAULT_NEEDS) -> None:
        self._definitions = {definition.need_id: definition for definition in definitions}

    def activate(
        self,
        substrate: OrganismicSubstrate,
        appraisals: tuple[SubjectAppraisal, ...],
        history: tuple[NeedEpisode, ...] = (),
        derived_needs: tuple[DerivedNeed, ...] = (),
        relational: Mapping[str, object] | None = None,
    ) -> tuple[NeedActivation, ...]:
        vector = substrate.viability_vector()
        appraisals_by_need: dict[str, list[SubjectAppraisal]] = defaultdict(list)
        for appraisal in appraisals:
            for need_id in appraisal.affected_need_ids:
                appraisals_by_need[need_id].append(appraisal)

        history_pressure: dict[str, float] = defaultdict(float)
        for episode in history[-40:]:
            if not episode.is_closed:
                history_pressure[episode.need_id] = max(history_pressure[episode.need_id], episode.deficit * 0.35)

        activations: list[NeedActivation] = []
        for need_id, definition in self._definitions.items():
            substrate_gap = sum(
                weight * max(
                    0.0,
                    (SUBSTRATE_BASELINES.get(key, 1.0) - float(vector.get(key, SUBSTRATE_BASELINES.get(key, 1.0))))
                    / max(SUBSTRATE_BASELINES.get(key, 1.0), 1e-9),
                )
                for key, weight in definition.viability_dependencies.items()
            )
            relevant = appraisals_by_need.get(need_id, [])
            if relevant:
                def _pressure_from_appraisal(a: SubjectAppraisal) -> float:
                    base = (
                        a.goal_relevance
                        * (0.5 + 0.5 * a.expectation_violation)
                        * (0.5 + 0.5 * a.future_consequence)
                    )
                    # V56: event tích cực không được làm Need tăng chỉ vì nó rất
                    # quan trọng. Congruence âm tăng pressure; dương tạo relief.
                    threat = max(0.0, -float(a.goal_congruence))
                    support = max(0.0, float(a.goal_congruence))
                    modulation = max(0.25, min(1.45, 1.0 + 0.45 * threat - 0.55 * support))
                    return base * modulation

                appraisal_pressure = max(_pressure_from_appraisal(a) for a in relevant)
                opportunity = max(
                    min(1.0, a.controllability + 0.18 * max(0.0, float(a.goal_progress_delta)))
                    for a in relevant
                )
                urgency = max(
                    min(1.0, a.future_consequence + 0.22 * max(0.0, -float(a.goal_congruence)))
                    for a in relevant
                )
                refs = tuple(dict.fromkeys(ref for a in relevant for ref in a.evidence_refs))
            else:
                appraisal_pressure = 0.0
                opportunity = float(vector.get("action_efficacy", 0.5))
                urgency = substrate_gap
                refs = tuple(substrate.measurement_refs)
            pressure = min(1.0, substrate_gap + appraisal_pressure + history_pressure[need_id])
            if need_id == NeedKind.CONNECTION.value and isinstance(relational, Mapping):
                relational_pressure = max(0.0, min(1.0, float(relational.get("connection_pressure", 0.0) or 0.0)))
                relational_opportunity = max(0.0, min(1.0, float(relational.get("opportunity", 0.0) or 0.0)))
                outreach_drive = max(0.0, min(1.0, float(relational.get("outreach_drive", 0.0) or 0.0)))
                # Relational homeostasis là nguồn pressure độc lập. Nó không được
                # cộng vô hạn với appraisal; probabilistic union giữ biên [0,1].
                pressure = 1.0 - (1.0 - pressure) * (1.0 - relational_pressure)
                opportunity = max(opportunity, relational_opportunity)
                urgency = max(urgency, outreach_drive * 0.72)
                relational_refs = tuple(
                    str(item) for item in relational.get("evidence_refs", ())
                    if str(item or "").strip()
                )
                refs = tuple(dict.fromkeys((*refs, *relational_refs, f"relational-state:{int(relational.get('revision', 0) or 0)}")))
            if pressure <= 0.01:
                continue
            activations.append(NeedActivation(
                need_id=need_id,
                pressure=pressure,
                opportunity=max(0.0, min(1.0, opportunity)),
                urgency=max(0.0, min(1.0, urgency)),
                persistence=definition.persistence,
                evidence_refs=refs,
                proposed_tendencies=definition.default_tendencies,
            ))

        for derived in derived_needs:
            if derived.status not in (DerivedNeedStatus.ACTIVE, DerivedNeedStatus.CANDIDATE):
                continue
            parent_pressure = [a.pressure for a in activations if a.need_id in derived.parent_need_ids]
            if len(parent_pressure) != len(derived.parent_need_ids):
                continue
            pressure = min(parent_pressure) * derived.strength * (1.0 - 0.5 * derived.meta_opposition)
            if pressure <= 0.05:
                continue
            refs = tuple(dict.fromkeys(ref for a in activations if a.need_id in derived.parent_need_ids for ref in a.evidence_refs))
            activations.append(NeedActivation(
                need_id=derived.need_id,
                pressure=pressure,
                opportunity=sum(a.opportunity for a in activations if a.need_id in derived.parent_need_ids) / len(parent_pressure),
                urgency=max(a.urgency for a in activations if a.need_id in derived.parent_need_ids),
                persistence=0.7,
                target_ids=(derived.target_selector,),
                evidence_refs=refs,
                proposed_tendencies=(derived.satisfaction_signal,),
            ))
        return tuple(sorted(activations, key=lambda item: (-item.pressure, item.need_id)))


class NeedPlasticityEngine:
    """Propose derived needs from repeated cross-context co-activation.

    The engine does not invent psychological labels.  It emits mechanical labels
    derived from parent IDs and a stable target selector supplied by episode
    context.
    """

    def propose(
        self,
        episodes: tuple[NeedEpisode, ...],
        *,
        now: float,
        min_episodes: int = 5,
        min_span_days: float = 14.0,
        min_contexts: int = 2,
        predictive_gain_by_pair: Mapping[tuple[str, str], float] | None = None,
        min_predictive_gain: float = 0.03,
    ) -> tuple[DerivedNeed, ...]:
        closed = [episode for episode in episodes if episode.is_closed]
        by_context: dict[str, list[NeedEpisode]] = defaultdict(list)
        for episode in closed:
            contexts = episode.social_context_ids or ("general",)
            for context in contexts:
                by_context[str(context)].append(episode)

        pair_support: dict[tuple[str, str], set[str]] = defaultdict(set)
        pair_episodes: dict[tuple[str, str], set[str]] = defaultdict(set)
        pair_times: dict[tuple[str, str], list[float]] = defaultdict(list)
        for context, members in by_context.items():
            need_ids = sorted({episode.need_id for episode in members})
            for index, left in enumerate(need_ids):
                for right in need_ids[index + 1:]:
                    key = (left, right)
                    relevant = [episode for episode in members if episode.need_id in key]
                    if len(relevant) < 2:
                        continue
                    pair_support[key].add(context)
                    pair_episodes[key].update(episode.episode_id for episode in relevant)
                    pair_times[key].extend(episode.started_at for episode in relevant)

        output: list[DerivedNeed] = []
        predictive_gain_by_pair = dict(predictive_gain_by_pair or {})
        for pair, episode_ids in sorted(pair_episodes.items()):
            times = pair_times[pair]
            span_days = (max(times) - min(times)) / 86400.0 if times else 0.0
            contexts = pair_support[pair]
            predictive_gain = float(predictive_gain_by_pair.get(pair, 0.0))
            if (
                len(episode_ids) < min_episodes
                or span_days < min_span_days
                or len(contexts) < min_contexts
                or predictive_gain < min_predictive_gain
            ):
                continue
            need_id = "derived:" + "+".join(pair)
            support = min(1.0, len(contexts) / max(min_contexts, 4))
            output.append(DerivedNeed(
                need_id=need_id,
                label=" + ".join(pair),
                parent_need_ids=pair,
                target_selector="shared_context_target",
                trigger_pattern=f"coactivation_across_contexts;holdout_gain={predictive_gain:.4f}",
                satisfaction_signal="pursue_shared_target",
                frustration_signal="shared_target_blocked",
                strength=min(1.0, len(episode_ids) / 10.0),
                plasticity=0.5,
                status=DerivedNeedStatus.CANDIDATE,
                created_from_episode_ids=tuple(sorted(episode_ids)),
                cross_context_support=support,
                last_activated_at=now,
            ))
        return tuple(output)
