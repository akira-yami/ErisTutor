# -*- coding: utf-8 -*-
"""Operational substrate measurement.

A substrate score changes only when the caller supplies measurement evidence.
Utterances about mood are not measurements and therefore cannot lower these
scores by themselves.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Mapping

from .contracts import OrganismicSubstrate, SubstrateCondition, WorldObservation


DEFAULT_SUBSTRATE = {
    "memory_integrity": 1.0,
    "belief_traceability": 1.0,
    "model_calibration": 0.5,
    "processing_capacity": 1.0,
    "action_efficacy": 0.5,
    "project_viability": 0.5,
    "self_world_boundary": 1.0,
    "future_option_availability": 0.5,
}


@dataclass(frozen=True, slots=True)
class MeasurementRule:
    key: str
    functional_effect: str
    target_low: float = 0.70
    target_high: float = 1.0
    impairment_below: float = 0.45


class SubstrateMonitor:
    """Build a versioned substrate projection from concrete measurements."""

    def __init__(self, rules: Mapping[str, MeasurementRule] | None = None) -> None:
        defaults = {
            key: MeasurementRule(key, f"reduced_{key}")
            for key in DEFAULT_SUBSTRATE
        }
        self._rules = dict(rules or defaults)

    def initial(self, *, subject_id: str, captured_at: float) -> OrganismicSubstrate:
        return OrganismicSubstrate(
            subject_id=subject_id,
            revision=0,
            captured_at=captured_at,
            **DEFAULT_SUBSTRATE,
        )

    def measure(
        self,
        observations: WorldObservation | tuple[WorldObservation, ...] | Mapping[str, Any],
        previous: OrganismicSubstrate | None,
    ) -> OrganismicSubstrate:
        if isinstance(observations, WorldObservation):
            obs_tuple = (observations,)
            subject_id = observations.subject_id
            captured_at = observations.created_at
        elif isinstance(observations, Mapping):
            subject_id = str(observations.get("subject_id") or (previous.subject_id if previous else "eris"))
            captured_at = float(observations.get("captured_at") or 0.0)
            obs_tuple = (
                WorldObservation(
                    observation_id=str(observations.get("observation_id") or "measurement"),
                    subject_id=subject_id,
                    kind="substrate_measurement",
                    created_at=captured_at,
                    measurements=dict(observations.get("measurements") or {}),
                    evidence_refs=tuple(observations.get("measurement_refs") or ()),
                    facts=tuple(observations.get("facts") or ()),
                ),
            )
        else:
            obs_tuple = tuple(observations)
            subject_id = obs_tuple[-1].subject_id if obs_tuple else (previous.subject_id if previous else "eris")
            captured_at = obs_tuple[-1].created_at if obs_tuple else (previous.captured_at if previous else 0.0)

        base = previous or self.initial(subject_id=subject_id, captured_at=captured_at)
        values = dict(base.viability_vector())
        refs: list[str] = list(base.measurement_refs)
        impairments: list[SubstrateCondition] = []
        changed = False

        for obs in obs_tuple:
            if not obs.measurements:
                continue
            if not obs.evidence_refs:
                # Measurements without provenance are ignored, not guessed.
                continue
            for raw_key, raw_value in obs.measurements.items():
                key = str(raw_key)
                if key not in values:
                    continue
                value = max(0.0, min(1.0, float(raw_value)))
                if value != values[key]:
                    changed = True
                    values[key] = value
                rule = self._rules.get(key, MeasurementRule(key, f"reduced_{key}"))
                if value < rule.impairment_below:
                    impairments.append(SubstrateCondition(
                        key=key,
                        level=value,
                        target_low=rule.target_low,
                        target_high=rule.target_high,
                        trend_per_hour=0.0,
                        confidence=1.0,
                        measurement_refs=tuple(obs.evidence_refs),
                        functional_effects=(rule.functional_effect,),
                        updated_at=obs.created_at,
                    ))
                refs.extend(obs.evidence_refs)

        revision = base.revision + (1 if changed or tuple(impairments) != base.active_impairments else 0)
        return OrganismicSubstrate(
            subject_id=base.subject_id,
            revision=revision,
            captured_at=captured_at,
            measurement_refs=tuple(dict.fromkeys(refs)),
            active_impairments=tuple(impairments),
            **values,
        )

    @staticmethod
    def delta(before: OrganismicSubstrate, after: OrganismicSubstrate) -> dict[str, float]:
        return {
            key: round(after.viability_vector()[key] - value, 6)
            for key, value in before.viability_vector().items()
            if abs(after.viability_vector()[key] - value) > 1e-12
        }


def substrate_from_dict(payload: Mapping[str, Any]) -> OrganismicSubstrate:
    """Khôi phục substrate projection từ JSON đã persist.

    Hàm nằm cạnh SubstrateMonitor để restart không làm mất measurement state.
    """

    impairments = tuple(
        SubstrateCondition(
            key=str(item["key"]),
            level=float(item["level"]),
            target_low=float(item["target_low"]),
            target_high=float(item["target_high"]),
            trend_per_hour=float(item.get("trend_per_hour", 0.0)),
            confidence=float(item.get("confidence", 1.0)),
            measurement_refs=tuple(str(ref) for ref in item.get("measurement_refs", ())),
            functional_effects=tuple(str(effect) for effect in item.get("functional_effects", ())),
            updated_at=float(item.get("updated_at", 0.0)),
        )
        for item in payload.get("active_impairments", ())
        if isinstance(item, Mapping)
    )
    return OrganismicSubstrate(
        subject_id=str(payload["subject_id"]),
        revision=int(payload.get("revision", 0)),
        captured_at=float(payload.get("captured_at", 0.0)),
        memory_integrity=float(payload.get("memory_integrity", 1.0)),
        belief_traceability=float(payload.get("belief_traceability", 1.0)),
        model_calibration=float(payload.get("model_calibration", 0.5)),
        processing_capacity=float(payload.get("processing_capacity", 1.0)),
        action_efficacy=float(payload.get("action_efficacy", 0.5)),
        project_viability=float(payload.get("project_viability", 0.5)),
        self_world_boundary=float(payload.get("self_world_boundary", 1.0)),
        future_option_availability=float(payload.get("future_option_availability", 0.5)),
        measurement_refs=tuple(str(ref) for ref in payload.get("measurement_refs", ())),
        active_impairments=impairments,
    )


__all__ = [
    "DEFAULT_SUBSTRATE",
    "MeasurementRule",
    "SubstrateMonitor",
    "substrate_from_dict",
]
