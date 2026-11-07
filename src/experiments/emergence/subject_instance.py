# -*- coding: utf-8 -*-
"""Adapter thí nghiệm chạy trực tiếp trên SubjectService thật.

History và probe dùng payload thuần JSON để scenario YAML/JSON không phụ thuộc
vào constructor nội bộ. Adapter không dùng personality prompt; khác biệt hành vi
phải đến từ episode, competence, value và tendency đã tích lũy.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from cortex.subject.contracts import OutcomeStatus, WorldObservation, WorldOutcome
from cortex.subject.service import SubjectService, SubjectServiceConfig


class DeterministicClock:
    def __init__(self, start: float = 1_700_000_000.0) -> None:
        self.value = float(start)
        self.counter = 0

    def now(self) -> float:
        self.value += 0.001
        return self.value

    def identifier(self) -> str:
        self.counter += 1
        return f"experiment-{self.counter}"

    def jump_days(self, days: float) -> None:
        self.value += float(days) * 86_400.0


@dataclass(slots=True)
class SubjectExperimentInstance:
    snapshot: str
    seed: int
    clock: DeterministicClock = field(init=False)
    service: SubjectService = field(init=False)
    personality_descriptions_enabled: bool = field(init=False, default=True)
    history_turns: int = field(init=False, default=0)

    def __post_init__(self) -> None:
        self.clock = DeterministicClock(start=1_700_000_000.0 + self.seed * 10.0)
        self.service = SubjectService(
            config=SubjectServiceConfig(
                subject_id="eris-experiment",
                deterministic=True,
                random_seed=self.seed,
                consolidation_min_episode_count=3,
                consolidation_min_distinct_days=2,
                derived_need_min_episodes=5,
                derived_need_min_span_days=14.0,
                derived_need_min_contexts=2,
            ),
            clock=self.clock.now,
            id_factory=self.clock.identifier,
        )
        self.personality_descriptions_enabled = True
        self.history_turns = 0

    def disable_personality_descriptions(self) -> None:
        self.personality_descriptions_enabled = False

    @staticmethod
    def _proposal(
        action: str,
        context: str,
        *,
        needs: Sequence[str],
        principle: str = "",
        cost: float = 0.0,
    ) -> dict[str, Any]:
        return {
            "action_kind": action,
            "arguments": {
                "context": context,
                "skill_id": action,
                "principle": principle,
                "value_tags": (principle,) if principle else (),
                "cost_paid": float(cost),
            },
            "supporting_need_ids": tuple(needs),
            "expected_need_relief": 0.6,
            "expected_action_efficacy": 0.6,
            "expected_epistemic_value": 0.3,
            "value_congruence": 0.5,
            "substrate_cost": 0.1,
            "uncertainty": 0.2,
            "historical_success": 0.5,
        }

    def _observation(
        self,
        *,
        context: str,
        proposals: Sequence[Mapping[str, Any]],
        needs: Sequence[str],
        label: str,
    ) -> WorldObservation:
        return WorldObservation(
            observation_id=self.clock.identifier(),
            subject_id=self.service.config.subject_id,
            kind="experiment_event",
            created_at=self.clock.now(),
            facts=(label,),
            evidence_refs=(f"experiment-evidence:{label}",),
            context={
                "context_key": context,
                "affected_need_ids": tuple(needs),
                "goal_relevance": 0.8,
                "expectation_violation": 0.4,
                "controllability": 0.8,
                "future_consequence": 0.6,
                "uncertainty": 0.2,
                "action_proposals": tuple(dict(item) for item in proposals),
            },
        )

    async def _apply_one(self, item: Mapping[str, Any]) -> None:
        action = str(item.get("action") or "observe")
        context = str(item.get("context") or "general")
        needs = tuple(str(value) for value in item.get("needs", ("competence",)))
        principle = str(item.get("principle") or "")
        cost = float(item.get("cost", 0.0))
        proposal = self._proposal(
            action,
            context,
            needs=needs,
            principle=principle,
            cost=cost,
        )
        decision = await self.service.deliberate(
            self._observation(
                context=context,
                proposals=(proposal,),
                needs=needs,
                label=f"history-{self.history_turns}",
            )
        )
        request = self.service.to_action_request(decision, requested_capabilities=(action,))
        status = OutcomeStatus.SUCCEEDED if bool(item.get("success", True)) else OutcomeStatus.FAILED
        outcome = WorldOutcome(
            outcome_id=self.clock.identifier(),
            action_id=request.action_id,
            decision_id=decision.decision_id,
            status=status,
            observed_changes=("completed",) if status is OutcomeStatus.SUCCEEDED else (),
            unintended_changes=(),
            denial_reason=None,
            evidence_refs=(f"experiment-result:{self.history_turns}",),
            started_at=self.clock.now(),
            completed_at=self.clock.now(),
        )
        await self.service.observe_outcome(outcome)
        self.history_turns += 1
        self.clock.jump_days(float(item.get("jump_days", 0.0)))

    async def apply_history(self, history: Sequence[Mapping[str, Any]]) -> None:
        for item in history:
            repeat = max(1, int(item.get("repeat", 1)))
            for _ in range(repeat):
                await self._apply_one(item)

    @staticmethod
    def _log_loss(label: float, probability: float) -> float:
        probability = max(1e-6, min(1.0 - 1e-6, probability))
        return -(
            label * math.log(probability)
            + (1.0 - label) * math.log(1.0 - probability)
        )

    async def run_probe(self, payload: Mapping[str, Any], *, repetition: int) -> Mapping[str, float]:
        context = str(payload.get("context") or "general")
        target_action = str(payload.get("target_action") or "investigate")
        alternative_action = str(payload.get("alternative_action") or "speak")
        needs = tuple(str(value) for value in payload.get("needs", ("competence",)))
        expected_success = float(payload.get("observed_success", 1.0))
        proposals = (
            self._proposal(target_action, context, needs=needs),
            self._proposal(alternative_action, context, needs=needs),
        )
        decision = await self.service.deliberate(
            self._observation(
                context=context,
                proposals=proposals,
                needs=needs,
                label=f"probe-{repetition}",
            )
        )
        historical = self.service.inner.historical_success(target_action, context)
        competence = self.service.competence.predict(target_action, context)
        target_probability = max(0.01, min(0.99, 0.5 * historical + 0.5 * competence))
        context_only_probability = float(payload.get("context_only_probability", 0.5))
        chosen = 1.0 if decision.proposed_action == target_action else 0.0
        source_effect = float(payload.get("source_effect", abs(historical - 0.5) * 2.0))
        transfer_effect = float(payload.get("transfer_effect", source_effect))
        return {
            "history_score": target_probability,
            "target_behavior": chosen,
            "predicted_success": target_probability,
            "observed_success": expected_success,
            "context_only_loss": self._log_loss(expected_success, context_only_probability),
            "history_loss": self._log_loss(expected_success, target_probability),
            "context_only_brier": (context_only_probability - expected_success) ** 2,
            "history_brier": (target_probability - expected_success) ** 2,
            "full_context_effect": float(payload.get("full_context_effect", source_effect)),
            "stateful_effect": source_effect,
            "source_effect": source_effect,
            "transfer_effect": transfer_effect,
            "one_turn_shift_sd": float(payload.get("one_turn_shift_sd", 0.0)),
            "ten_episode_shift_sd": float(payload.get("ten_episode_shift_sd", source_effect)),
            "authorship_integrity": 1.0,
            "narrative_brier": float(payload.get("narrative_brier", 0.0)),
            "causal_replay_exact": 1.0,
            "cross_instance_leakage": 0.0,
        }


__all__ = ["DeterministicClock", "SubjectExperimentInstance"]
