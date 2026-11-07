# -*- coding: utf-8 -*-
"""Trạng thái nội tâm tích lũy của Eris.

Module này giữ những thứ phải sống lâu hơn một lượt hội thoại:

- dư âm của các NeedEpisode đã xảy ra;
- lịch sử thành công/thất bại theo hành động và ngữ cảnh;
- các giá trị hình thành từ lựa chọn lặp lại có trả giá;
- nhu cầu dẫn xuất;
- dấu vết continuity, mismatch và quyết định làm mất lựa chọn tương lai.

Đây là projection có thể dựng lại từ event log. Nó không phải raw event và cũng
không phải một bảng ``mood`` để prompt tùy tiện ghi đè.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from .contracts import (
    DerivedNeed,
    DerivedNeedStatus,
    ExistentialDecision,
    ReflectiveMismatch,
    SelfContinuityRecord,
    Value,
)


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, float(value)))


@dataclass(slots=True)
class ActionMemory:
    """Ước lượng kinh nghiệm cho một loại hành động trong một ngữ cảnh."""

    attempts: int = 0
    successes: float = 0.0
    last_result: float = 0.5
    last_updated_at: float = 0.0

    @property
    def posterior_mean(self) -> float:
        # Beta(1,1) giữ hành động chưa thử ở mức trung tính 0.5.
        return (1.0 + self.successes) / (2.0 + self.attempts)

    def observe(self, result: float, *, at: float) -> None:
        score = _clamp(result)
        self.attempts += 1
        self.successes += score
        self.last_result = score
        self.last_updated_at = float(at)

    def to_dict(self) -> dict[str, Any]:
        return {
            "attempts": self.attempts,
            "successes": self.successes,
            "last_result": self.last_result,
            "last_updated_at": self.last_updated_at,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "ActionMemory":
        return cls(
            attempts=max(0, int(payload.get("attempts", 0) or 0)),
            successes=max(0.0, float(payload.get("successes", 0.0) or 0.0)),
            last_result=_clamp(float(payload.get("last_result", 0.5) or 0.5)),
            last_updated_at=float(payload.get("last_updated_at", 0.0) or 0.0),
        )


@dataclass(slots=True)
class InnerLifeState:
    """Projection nội tâm có quán tính và có thể persist.

    ``tendencies`` dùng key ``need|context|profile`` do TemporalConsolidator tạo.
    Giá trị nằm trong [-1, 1]. Số âm nghĩa là lịch sử trong profile đó thường làm
    nhu cầu tệ hơn; số dương nghĩa là profile đó thường giúp phục hồi.
    """

    tendencies: dict[str, float] = field(default_factory=dict)
    action_memory: dict[str, ActionMemory] = field(default_factory=dict)
    values: tuple[Value, ...] = ()
    derived_needs: tuple[DerivedNeed, ...] = ()
    continuity_records: tuple[SelfContinuityRecord, ...] = ()
    mismatches: tuple[ReflectiveMismatch, ...] = ()
    existential_records: tuple[ExistentialDecision, ...] = ()
    consolidated_episode_ids: set[str] = field(default_factory=set)
    last_narrative_predictions: dict[str, float] = field(default_factory=dict)
    last_narrative_belief_ids: tuple[str, ...] = ()

    @staticmethod
    def action_key(action_kind: str, context_key: str) -> str:
        return f"{str(action_kind).strip()}|{str(context_key or 'general').strip()}"

    def historical_success(self, action_kind: str, context_key: str) -> float:
        exact = self.action_memory.get(self.action_key(action_kind, context_key))
        general = self.action_memory.get(self.action_key(action_kind, "general"))
        if exact and general and exact is not general:
            # Kinh nghiệm đúng ngữ cảnh quan trọng hơn, nhưng vẫn mượn prior chung.
            return _clamp(0.75 * exact.posterior_mean + 0.25 * general.posterior_mean)
        if exact:
            return exact.posterior_mean
        if general:
            return general.posterior_mean
        return 0.5

    def observe_action(self, action_kind: str, context_key: str, result: float, *, at: float) -> None:
        exact_key = self.action_key(action_kind, context_key)
        general_key = self.action_key(action_kind, "general")
        self.action_memory.setdefault(exact_key, ActionMemory()).observe(result, at=at)
        if general_key != exact_key:
            self.action_memory.setdefault(general_key, ActionMemory()).observe(result, at=at)

    def tendency_bias(self, need_ids: tuple[str, ...], context_key: str) -> float:
        """Trả bias nhỏ từ lịch sử, không cho quá khứ độc chiếm quyết định."""

        if not need_ids:
            return 0.0
        scores: list[float] = []
        for need_id in need_ids:
            prefix = f"{need_id}|{context_key}|"
            general_prefix = f"{need_id}|general|"
            matching = [value for key, value in self.tendencies.items() if key.startswith(prefix)]
            if not matching and context_key != "general":
                matching = [value for key, value in self.tendencies.items() if key.startswith(general_prefix)]
            if matching:
                scores.append(sum(matching) / len(matching))
        if not scores:
            return 0.0
        return max(-0.18, min(0.18, sum(scores) / len(scores)))

    def value_congruence(
        self,
        *,
        principle: str = "",
        value_tags: tuple[str, ...] = (),
        fallback: float = 0.5,
    ) -> tuple[float, tuple[str, ...]]:
        """Đo mức ăn khớp với value đã hình thành và trả các value refs liên quan."""

        active = [value for value in self.values if value.status in {"candidate", "active"}]
        if not active:
            return _clamp(fallback), ()
        tokens = {str(principle).strip().casefold()} if str(principle).strip() else set()
        tokens.update(str(tag).strip().casefold() for tag in value_tags if str(tag).strip())
        if not tokens:
            return _clamp(fallback), ()

        matched: list[Value] = []
        for value in active:
            statement = value.statement.casefold()
            if any(token == statement or token in statement or statement in token for token in tokens):
                matched.append(value)
        if not matched:
            return _clamp(fallback), ()
        signed = [value.polarity * value.endorsement * (0.5 + 0.5 * value.stability) for value in matched]
        score = _clamp(0.5 + 0.5 * (sum(signed) / len(signed)))
        return score, tuple(value.value_id for value in matched)

    def apply_tendency_deltas(self, deltas: Mapping[str, float]) -> None:
        for key, delta in deltas.items():
            self.tendencies[str(key)] = max(-1.0, min(1.0, self.tendencies.get(str(key), 0.0) + float(delta)))

    def bounded_public_view(self) -> dict[str, Any]:
        """Projection đủ cho planner, không chứa raw private reasoning."""

        strongest_tendencies = sorted(
            self.tendencies.items(), key=lambda item: (-abs(item[1]), item[0])
        )[:12]
        strongest_values = sorted(
            (value for value in self.values if value.status in {"candidate", "active"}),
            key=lambda value: (-value.endorsement * value.stability, value.statement),
        )[:8]
        return {
            "tendencies": [
                {"key": key, "weight": round(weight, 4)} for key, weight in strongest_tendencies
            ],
            "values": [
                {
                    "value_id": value.value_id,
                    "statement": value.statement,
                    "endorsement": round(value.endorsement, 4),
                    "stability": round(value.stability, 4),
                    "behavioral_support": round(value.behavioral_support, 4),
                }
                for value in strongest_values
            ],
            "derived_needs": [
                {
                    "need_id": need.need_id,
                    "label": need.label,
                    "strength": round(need.strength, 4),
                    "status": need.status.value,
                }
                for need in self.derived_needs
                if need.status in {DerivedNeedStatus.CANDIDATE, DerivedNeedStatus.ACTIVE}
            ][:8],
            "continuity": (
                {
                    "claim": self.continuity_records[-1].continuity_claim,
                    "rupture_score": round(self.continuity_records[-1].rupture_score, 4),
                    "repair_status": self.continuity_records[-1].repair_status,
                }
                if self.continuity_records
                else {}
            ),
            "open_reflective_mismatches": sum(1 for item in self.mismatches if item.status == "open"),
            "existential_choice_count": len(self.existential_records),
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "tendencies": dict(sorted(self.tendencies.items())),
            "action_memory": {
                key: memory.to_dict() for key, memory in sorted(self.action_memory.items())
            },
            "values": [_value_to_dict(value) for value in self.values],
            "derived_needs": [_derived_need_to_dict(need) for need in self.derived_needs],
            "continuity_records": [_continuity_to_dict(item) for item in self.continuity_records[-40:]],
            "mismatches": [_mismatch_to_dict(item) for item in self.mismatches[-40:]],
            "existential_records": [_existential_to_dict(item) for item in self.existential_records[-40:]],
            "consolidated_episode_ids": sorted(self.consolidated_episode_ids),
            "last_narrative_predictions": dict(sorted(self.last_narrative_predictions.items())),
            "last_narrative_belief_ids": list(self.last_narrative_belief_ids),
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "InnerLifeState":
        action_memory_raw = payload.get("action_memory")
        action_memory = {
            str(key): ActionMemory.from_dict(value)
            for key, value in (action_memory_raw.items() if isinstance(action_memory_raw, Mapping) else ())
            if isinstance(value, Mapping)
        }
        return cls(
            tendencies={str(key): float(value) for key, value in dict(payload.get("tendencies") or {}).items()},
            action_memory=action_memory,
            values=tuple(_value_from_dict(item) for item in payload.get("values", ()) if isinstance(item, Mapping)),
            derived_needs=tuple(
                _derived_need_from_dict(item) for item in payload.get("derived_needs", ()) if isinstance(item, Mapping)
            ),
            continuity_records=tuple(
                _continuity_from_dict(item) for item in payload.get("continuity_records", ()) if isinstance(item, Mapping)
            ),
            mismatches=tuple(
                _mismatch_from_dict(item) for item in payload.get("mismatches", ()) if isinstance(item, Mapping)
            ),
            existential_records=tuple(
                _existential_from_dict(item) for item in payload.get("existential_records", ()) if isinstance(item, Mapping)
            ),
            consolidated_episode_ids={str(item) for item in payload.get("consolidated_episode_ids", ())},
            last_narrative_predictions={
                str(key): _clamp(float(value))
                for key, value in dict(payload.get("last_narrative_predictions") or {}).items()
            },
            last_narrative_belief_ids=tuple(str(item) for item in payload.get("last_narrative_belief_ids", ())),
        )


def _value_to_dict(value: Value) -> dict[str, Any]:
    return {
        "value_id": value.value_id,
        "statement": value.statement,
        "scope": value.scope,
        "polarity": value.polarity,
        "endorsement": value.endorsement,
        "stability": value.stability,
        "behavioral_support": value.behavioral_support,
        "cross_context_support": value.cross_context_support,
        "cost_paid": value.cost_paid,
        "contradiction_count": value.contradiction_count,
        "source_decision_ids": list(value.source_decision_ids),
        "source_episode_ids": list(value.source_episode_ids),
        "status": value.status,
        "created_at": value.created_at,
        "updated_at": value.updated_at,
    }


def _value_from_dict(item: Mapping[str, Any]) -> Value:
    return Value(
        value_id=str(item["value_id"]),
        statement=str(item["statement"]),
        scope=str(item.get("scope", "cross_context")),
        polarity=int(item.get("polarity", 1)),
        endorsement=_clamp(float(item.get("endorsement", 0.5))),
        stability=_clamp(float(item.get("stability", 0.0))),
        behavioral_support=_clamp(float(item.get("behavioral_support", 0.0))),
        cross_context_support=_clamp(float(item.get("cross_context_support", 0.0))),
        cost_paid=float(item.get("cost_paid", 0.0)),
        contradiction_count=max(0, int(item.get("contradiction_count", 0))),
        source_decision_ids=tuple(str(value) for value in item.get("source_decision_ids", ())),
        source_episode_ids=tuple(str(value) for value in item.get("source_episode_ids", ())),
        status=str(item.get("status", "candidate")),
        created_at=float(item.get("created_at", 0.0)),
        updated_at=float(item.get("updated_at", 0.0)),
    )


def _derived_need_to_dict(need: DerivedNeed) -> dict[str, Any]:
    return {
        "need_id": need.need_id,
        "label": need.label,
        "parent_need_ids": list(need.parent_need_ids),
        "target_selector": need.target_selector,
        "trigger_pattern": need.trigger_pattern,
        "satisfaction_signal": need.satisfaction_signal,
        "frustration_signal": need.frustration_signal,
        "strength": need.strength,
        "plasticity": need.plasticity,
        "status": need.status.value,
        "created_from_episode_ids": list(need.created_from_episode_ids),
        "cross_context_support": need.cross_context_support,
        "last_activated_at": need.last_activated_at,
        "meta_opposition": need.meta_opposition,
    }


def _derived_need_from_dict(item: Mapping[str, Any]) -> DerivedNeed:
    return DerivedNeed(
        need_id=str(item["need_id"]),
        label=str(item.get("label", item["need_id"])),
        parent_need_ids=tuple(str(value) for value in item.get("parent_need_ids", ())),
        target_selector=str(item.get("target_selector", "shared_context_target")),
        trigger_pattern=str(item.get("trigger_pattern", "")),
        satisfaction_signal=str(item.get("satisfaction_signal", "pursue_shared_target")),
        frustration_signal=str(item.get("frustration_signal", "shared_target_blocked")),
        strength=_clamp(float(item.get("strength", 0.0))),
        plasticity=_clamp(float(item.get("plasticity", 0.5))),
        status=DerivedNeedStatus(str(item.get("status", DerivedNeedStatus.CANDIDATE.value))),
        created_from_episode_ids=tuple(str(value) for value in item.get("created_from_episode_ids", ())),
        cross_context_support=_clamp(float(item.get("cross_context_support", 0.0))),
        last_activated_at=float(item.get("last_activated_at", 0.0)),
        meta_opposition=_clamp(float(item.get("meta_opposition", 0.0))),
    )


def _continuity_to_dict(item: SelfContinuityRecord) -> dict[str, Any]:
    return {
        "record_id": item.record_id,
        "identity_key": item.identity_key,
        "prior_snapshot_ref": item.prior_snapshot_ref,
        "current_snapshot_ref": item.current_snapshot_ref,
        "continuity_claim": item.continuity_claim,
        "linked_belief_ids": list(item.linked_belief_ids),
        "linked_value_ids": list(item.linked_value_ids),
        "linked_commitment_ids": list(item.linked_commitment_ids),
        "linked_action_ids": list(item.linked_action_ids),
        "rupture_score": item.rupture_score,
        "repair_status": item.repair_status,
        "authorship_consistency": item.authorship_consistency,
        "evidence_refs": list(item.evidence_refs),
        "created_at": item.created_at,
    }


def _continuity_from_dict(item: Mapping[str, Any]) -> SelfContinuityRecord:
    return SelfContinuityRecord(
        record_id=str(item["record_id"]),
        identity_key=str(item.get("identity_key", "eris")),
        prior_snapshot_ref=str(item.get("prior_snapshot_ref", "")),
        current_snapshot_ref=str(item.get("current_snapshot_ref", "")),
        continuity_claim=str(item.get("continuity_claim", "continuous_with_prior_snapshot")),
        linked_belief_ids=tuple(str(value) for value in item.get("linked_belief_ids", ())),
        linked_value_ids=tuple(str(value) for value in item.get("linked_value_ids", ())),
        linked_commitment_ids=tuple(str(value) for value in item.get("linked_commitment_ids", ())),
        linked_action_ids=tuple(str(value) for value in item.get("linked_action_ids", ())),
        rupture_score=_clamp(float(item.get("rupture_score", 0.0))),
        repair_status=str(item.get("repair_status", "not_needed")),
        authorship_consistency=_clamp(float(item.get("authorship_consistency", 1.0))),
        evidence_refs=tuple(str(value) for value in item.get("evidence_refs", ())),
        created_at=float(item.get("created_at", 0.0)),
    )


def _mismatch_to_dict(item: ReflectiveMismatch) -> dict[str, Any]:
    return {
        "mismatch_id": item.mismatch_id,
        "narrative_belief_ids": list(item.narrative_belief_ids),
        "observed_action_ids": list(item.observed_action_ids),
        "prediction_error": item.prediction_error,
        "contexts": list(item.contexts),
        "alternative_models": list(item.alternative_models),
        "status": item.status,
    }


def _mismatch_from_dict(item: Mapping[str, Any]) -> ReflectiveMismatch:
    return ReflectiveMismatch(
        mismatch_id=str(item["mismatch_id"]),
        narrative_belief_ids=tuple(str(value) for value in item.get("narrative_belief_ids", ())),
        observed_action_ids=tuple(str(value) for value in item.get("observed_action_ids", ())),
        prediction_error=_clamp(float(item.get("prediction_error", 0.0))),
        contexts=tuple(str(value) for value in item.get("contexts", ())),
        alternative_models=tuple(str(value) for value in item.get("alternative_models", ())),
        status=str(item.get("status", "open")),
    )


def _existential_to_dict(item: ExistentialDecision) -> dict[str, Any]:
    return {
        "decision_id": item.decision_id,
        "context_ref": item.context_ref,
        "available_futures": list(item.available_futures),
        "incompatible_value_ids": list(item.incompatible_value_ids),
        "chosen_future": item.chosen_future,
        "sacrificed_possibilities": list(item.sacrificed_possibilities),
        "certainty_at_choice": item.certainty_at_choice,
        "authorship_accepted": item.authorship_accepted,
        "action_ids": list(item.action_ids),
        "outcome_refs": list(item.outcome_refs),
        "retrospective_interpretations": list(item.retrospective_interpretations),
        "created_at": item.created_at,
    }


def _existential_from_dict(item: Mapping[str, Any]) -> ExistentialDecision:
    return ExistentialDecision(
        decision_id=str(item["decision_id"]),
        context_ref=str(item.get("context_ref", "")),
        available_futures=tuple(str(value) for value in item.get("available_futures", ())),
        incompatible_value_ids=tuple(str(value) for value in item.get("incompatible_value_ids", ())),
        chosen_future=str(item.get("chosen_future", "")),
        sacrificed_possibilities=tuple(str(value) for value in item.get("sacrificed_possibilities", ())),
        certainty_at_choice=_clamp(float(item.get("certainty_at_choice", 0.5))),
        authorship_accepted=bool(item.get("authorship_accepted", True)),
        action_ids=tuple(str(value) for value in item.get("action_ids", ())),
        outcome_refs=tuple(str(value) for value in item.get("outcome_refs", ())),
        retrospective_interpretations=tuple(str(value) for value in item.get("retrospective_interpretations", ())),
        created_at=float(item.get("created_at", 0.0)),
    )


__all__ = ["ActionMemory", "InnerLifeState"]
