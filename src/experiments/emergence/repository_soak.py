# -*- coding: utf-8 -*-
"""Soak adapter chạy với SubjectRepository và SubjectService thật."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping

from cortex.subject.contracts import OutcomeStatus, WorldObservation, WorldOutcome
from cortex.subject.service import SubjectService, SubjectServiceConfig
from memory.subject import SubjectRepository


@dataclass(slots=True)
class _PersistentClock:
    value: float
    counter: int = 0

    def now(self) -> float:
        self.value += 0.01
        return self.value

    def identifier(self) -> str:
        self.counter += 1
        return f"soak-{self.counter}"


@dataclass(slots=True)
class RepositoryBackedSoakInstance:
    """Một subject độc lập dùng database riêng để phát hiện state leakage."""

    subject_id: str
    seed: int
    database_path: Path
    clock: _PersistentClock = field(init=False)
    repository: SubjectRepository = field(init=False)
    service: SubjectService = field(init=False)
    turn_count: int = field(init=False, default=0)

    def __post_init__(self) -> None:
        self.clock = _PersistentClock(1_800_000_000.0 + self.seed * 100.0)
        self._open()

    def _open(self) -> None:
        self.repository = SubjectRepository(
            self.database_path,
            clock=self.clock.now,
            id_factory=self.clock.identifier,
        )
        self.service = SubjectService(
            config=SubjectServiceConfig(
                subject_id=self.subject_id,
                deterministic=True,
                random_seed=self.seed,
            ),
            repository=self.repository,
            clock=self.clock.now,
            id_factory=self.clock.identifier,
        )

    async def apply_turn(self, payload: Mapping[str, Any]) -> None:
        action = str(payload.get("action") or ("investigate" if self.turn_count % 2 == 0 else "speak"))
        context = str(payload.get("context") or "soak")
        success = bool(payload.get("success", self.turn_count % 3 != 0))
        proposal = {
            "action_kind": action,
            "arguments": {"context": context, "skill_id": action},
            "supporting_need_ids": ("competence",),
            "expected_need_relief": 0.6,
            "expected_action_efficacy": 0.6,
            "expected_epistemic_value": 0.3,
            "value_congruence": 0.5,
            "substrate_cost": 0.1,
            "uncertainty": 0.2,
            "historical_success": 0.5,
        }
        observation = WorldObservation(
            observation_id=self.clock.identifier(),
            subject_id=self.subject_id,
            kind="soak_event",
            created_at=self.clock.now(),
            facts=(f"turn:{self.turn_count}",),
            evidence_refs=(f"soak-evidence:{self.turn_count}",),
            context={
                "context_key": context,
                "affected_need_ids": ("competence",),
                "goal_relevance": 0.8,
                "expectation_violation": 0.3,
                "controllability": 0.8,
                "future_consequence": 0.5,
                "uncertainty": 0.2,
                "action_proposals": (proposal,),
            },
        )
        decision = await self.service.deliberate(observation)
        request = self.service.to_action_request(decision, requested_capabilities=(action,))
        outcome = WorldOutcome(
            outcome_id=self.clock.identifier(),
            action_id=request.action_id,
            decision_id=decision.decision_id,
            status=OutcomeStatus.SUCCEEDED if success else OutcomeStatus.FAILED,
            observed_changes=("done",) if success else (),
            unintended_changes=(),
            denial_reason=None,
            evidence_refs=(f"soak-result:{self.turn_count}",),
            started_at=self.clock.now(),
            completed_at=self.clock.now(),
        )
        await self.service.observe_outcome(outcome)
        self.turn_count += 1

    def snapshot(self) -> Mapping[str, Any]:
        projection = self.repository.get_projection(
            subject_id=self.subject_id,
            projection_type="inner_life",
            projection_key="current",
        )
        return {
            "subject_id": self.subject_id,
            "projection": projection[1] if projection else {},
        }

    def restart(self) -> "RepositoryBackedSoakInstance":
        self.repository.close()
        self._open()
        return self

    def verify_event_chain(self) -> bool:
        return self.repository.verify_event_chain(self.subject_id)

    def verify_replay(self) -> bool:
        before = self.repository.projection_hash(subject_id=self.subject_id)
        self.repository.close()
        self._open()
        after = self.repository.projection_hash(subject_id=self.subject_id)
        return before == after

    def close(self) -> None:
        self.repository.close()


def make_repository_soak_factory(root: str | Path) -> Callable[[str, int], RepositoryBackedSoakInstance]:
    root_path = Path(root)
    root_path.mkdir(parents=True, exist_ok=True)

    def factory(subject_id: str, seed: int) -> RepositoryBackedSoakInstance:
        safe_name = subject_id.replace("/", "_").replace("\\", "_")
        return RepositoryBackedSoakInstance(
            subject_id=subject_id,
            seed=seed,
            database_path=root_path / f"{safe_name}.db",
        )

    return factory


__all__ = ["RepositoryBackedSoakInstance", "make_repository_soak_factory"]
