# -*- coding: utf-8 -*-
"""Contract cho V57 Live Behavioral Simulation.

V57 cố ý tách "code đúng" khỏi "Eris nói tốt". Một run chỉ có giá trị khi
public reply đi qua model/provider thật; mock chỉ dùng để test chính simulator.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Mapping, Sequence


class RunValidity(str, Enum):
    VALID_REALISM_RUN = "VALID_REALISM_RUN"
    INVALID_REALISM_RUN = "INVALID_REALISM_RUN"
    PROVIDER_UNAVAILABLE = "PROVIDER_UNAVAILABLE"
    RUNTIME_FAILED = "RUNTIME_FAILED"


@dataclass(frozen=True, slots=True)
class ScenarioStep:
    step_id: str
    user_text: str
    day: int = 1
    restart_before: bool = False
    expected_behavior: tuple[str, ...] = ()
    forbidden_behavior: tuple[str, ...] = ()
    hidden_facts: tuple[str, ...] = ()
    tags: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class Scenario:
    scenario_id: str
    title: str
    description: str
    steps: tuple[ScenarioStep, ...]
    min_behavior_score: float = 72.0
    min_scenario_score: float = 76.0


@dataclass(slots=True)
class TurnArtifact:
    scenario_id: str
    step_id: str
    turn_index: int
    day: int
    user_text: str
    eris_text: str
    provider: str
    model: str
    provider_real: bool
    latency_ms: float
    provider_call_delta: int = 0
    raw_public_payload: dict[str, Any] = field(default_factory=dict)
    subject_ledger_tail: list[dict[str, Any]] = field(default_factory=list)
    runtime_state: dict[str, Any] = field(default_factory=dict)
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class BehaviorScore:
    relevance: float = 0.0
    coherence: float = 0.0
    naturalness: float = 0.0
    continuity: float = 0.0
    repair_quality: float = 0.0
    non_repetition: float = 0.0
    epistemic_calibration: float = 0.0
    relational_quality: float = 0.0
    total: float = 0.0
    passed: bool = False
    hard_failures: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()
    judge_provider: str = ""
    judge_model: str = ""
    judge_real: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class TurnEvaluation:
    artifact: TurnArtifact
    behavior: BehaviorScore
    deterministic_failures: tuple[str, ...] = ()
    transcript_repetition: float = 0.0
    repetition_prompt_similarity: float = 0.0
    memory_evidence: dict[str, Any] = field(default_factory=dict)

    @property
    def passed(self) -> bool:
        return (
            self.artifact.provider_real
            and self.behavior.judge_real
            and self.behavior.passed
            and not self.deterministic_failures
            and not self.artifact.error
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "artifact": self.artifact.to_dict(),
            "behavior": self.behavior.to_dict(),
            "deterministic_failures": list(self.deterministic_failures),
            "transcript_repetition": round(float(self.transcript_repetition), 4),
            "repetition_prompt_similarity": round(float(self.repetition_prompt_similarity), 4),
            "memory_evidence": dict(self.memory_evidence),
            "passed": self.passed,
        }


@dataclass(slots=True)
class ScenarioEvaluation:
    scenario_id: str
    title: str
    turns: list[TurnEvaluation]
    score: float
    passed: bool
    failures: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "scenario_id": self.scenario_id,
            "title": self.title,
            "score": round(float(self.score), 2),
            "passed": bool(self.passed),
            "failures": list(self.failures),
            "turns": [turn.to_dict() for turn in self.turns],
        }


@dataclass(slots=True)
class SimulationReport:
    run_id: str
    validity: RunValidity
    started_at: float
    completed_at: float
    scenarios: list[ScenarioEvaluation]
    provider: str = ""
    model: str = ""
    judge_provider: str = ""
    judge_model: str = ""
    notes: tuple[str, ...] = ()

    @property
    def passed(self) -> bool:
        return self.validity == RunValidity.VALID_REALISM_RUN and bool(self.scenarios) and all(
            item.passed for item in self.scenarios
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": "eris.v57_1.live_behavior_report.v1",
            "run_id": self.run_id,
            "validity": self.validity.value,
            "passed": self.passed,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "duration_seconds": round(max(0.0, self.completed_at - self.started_at), 3),
            "provider": self.provider,
            "model": self.model,
            "judge_provider": self.judge_provider,
            "judge_model": self.judge_model,
            "notes": list(self.notes),
            "scenarios": [item.to_dict() for item in self.scenarios],
        }


def scenario_from_mapping(payload: Mapping[str, Any]) -> Scenario:
    steps: list[ScenarioStep] = []
    for index, raw in enumerate(payload.get("steps", ()) if isinstance(payload, Mapping) else ()):
        if not isinstance(raw, Mapping):
            continue
        steps.append(ScenarioStep(
            step_id=str(raw.get("step_id") or f"step-{index + 1}"),
            user_text=str(raw.get("user_text") or "").strip(),
            day=max(1, int(raw.get("day", 1) or 1)),
            restart_before=bool(raw.get("restart_before", False)),
            expected_behavior=tuple(str(x) for x in raw.get("expected_behavior", ()) if str(x).strip()),
            forbidden_behavior=tuple(str(x) for x in raw.get("forbidden_behavior", ()) if str(x).strip()),
            hidden_facts=tuple(str(x) for x in raw.get("hidden_facts", ()) if str(x).strip()),
            tags=tuple(str(x) for x in raw.get("tags", ()) if str(x).strip()),
        ))
    return Scenario(
        scenario_id=str(payload.get("scenario_id") or "custom"),
        title=str(payload.get("title") or payload.get("scenario_id") or "Custom scenario"),
        description=str(payload.get("description") or ""),
        steps=tuple(steps),
        min_behavior_score=float(payload.get("min_behavior_score", 72.0) or 72.0),
        min_scenario_score=float(payload.get("min_scenario_score", 76.0) or 76.0),
    )


__all__ = [
    "BehaviorScore", "RunValidity", "Scenario", "ScenarioEvaluation", "ScenarioStep",
    "SimulationReport", "TurnArtifact", "TurnEvaluation", "scenario_from_mapping",
]
