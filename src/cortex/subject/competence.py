# -*- coding: utf-8 -*-
"""Competence calibration and practice planning."""
from __future__ import annotations

from typing import Any, Callable, Iterable, Mapping

from .contracts import (
    CompetenceEstimate,
    CompetenceGap,
    PracticePlan,
    ProcessingBudget,
    ProjectState,
    Value,
)


class CompetenceService:
    def __init__(self, id_factory: Callable[[], str], clock: Callable[[], float]) -> None:
        self._id_factory = id_factory
        self._clock = clock
        self._estimates: dict[tuple[str, str], CompetenceEstimate] = {}


    def restore(self, records: Iterable[CompetenceEstimate | Mapping[str, Any]]) -> None:
        """Khôi phục calibration theo skill/context từ projection bền."""
        restored: dict[tuple[str, str], CompetenceEstimate] = {}
        for record in records:
            if isinstance(record, CompetenceEstimate):
                estimate = record
            else:
                estimate = CompetenceEstimate(
                    estimate_id=str(record["estimate_id"]),
                    skill_id=str(record["skill_id"]),
                    context_key=str(record.get("context_key", "general")),
                    posterior_mean=float(record.get("posterior_mean", 0.5)),
                    posterior_variance=float(record.get("posterior_variance", 0.0833)),
                    trial_count=max(0, int(record.get("trial_count", 0))),
                    success_count=max(0, int(record.get("success_count", 0))),
                    calibration_error=float(record.get("calibration_error", 0.0)),
                    brier_score=float(record.get("brier_score", 0.0)),
                    last_prediction=float(record.get("last_prediction", 0.5)),
                    last_result_ref=str(record.get("last_result_ref", "restored")),
                    evidence_refs=tuple(str(item) for item in record.get("evidence_refs", ())) or ("restored",),
                    estimated_at=float(record.get("estimated_at", 0.0)),
                )
            restored[(estimate.skill_id, estimate.context_key)] = estimate
        self._estimates = restored

    def to_projection(self) -> list[dict[str, Any]]:
        return [
            {
                "estimate_id": item.estimate_id,
                "skill_id": item.skill_id,
                "context_key": item.context_key,
                "posterior_mean": item.posterior_mean,
                "posterior_variance": item.posterior_variance,
                "trial_count": item.trial_count,
                "success_count": item.success_count,
                "calibration_error": item.calibration_error,
                "brier_score": item.brier_score,
                "last_prediction": item.last_prediction,
                "last_result_ref": item.last_result_ref,
                "evidence_refs": list(item.evidence_refs),
                "estimated_at": item.estimated_at,
            }
            for item in self.all()
        ]

    def predict(self, skill_id: str, context_key: str = "general") -> float:
        estimate = self._estimates.get((skill_id, context_key))
        return estimate.posterior_mean if estimate else 0.5

    def update(
        self,
        task: str,
        prediction: float,
        result: float,
        *,
        skill_id: str | None = None,
        context_key: str = "general",
        result_ref: str,
        evidence_refs: tuple[str, ...] = (),
    ) -> CompetenceEstimate:
        skill_id = skill_id or task
        prediction = max(0.0, min(1.0, float(prediction)))
        result = max(0.0, min(1.0, float(result)))
        previous = self._estimates.get((skill_id, context_key))
        alpha = 1.0 + (previous.success_count if previous else 0)
        beta = 1.0 + ((previous.trial_count - previous.success_count) if previous else 0)
        alpha += result
        beta += 1.0 - result
        mean = alpha / (alpha + beta)
        variance = (alpha * beta) / (((alpha + beta) ** 2) * (alpha + beta + 1.0))
        trials = (previous.trial_count if previous else 0) + 1
        successes = (previous.success_count if previous else 0) + int(result >= 0.5)
        brier_current = (prediction - result) ** 2
        brier = ((previous.brier_score * (trials - 1)) + brier_current) / trials if previous else brier_current
        calibration = abs(prediction - result)
        estimate = CompetenceEstimate(
            estimate_id=self._id_factory(),
            skill_id=skill_id,
            context_key=context_key,
            posterior_mean=mean,
            posterior_variance=variance,
            trial_count=trials,
            success_count=successes,
            calibration_error=calibration,
            brier_score=brier,
            last_prediction=prediction,
            last_result_ref=result_ref,
            evidence_refs=tuple(dict.fromkeys((*evidence_refs, result_ref))),
            estimated_at=self._clock(),
        )
        self._estimates[(skill_id, context_key)] = estimate
        return estimate

    def all(self) -> tuple[CompetenceEstimate, ...]:
        return tuple(sorted(self._estimates.values(), key=lambda item: (item.skill_id, item.context_key)))


class PracticePlanner:
    def __init__(self, id_factory: Callable[[], str], clock: Callable[[], float]) -> None:
        self._id_factory = id_factory
        self._clock = clock

    def plan(
        self,
        *,
        gaps: tuple[CompetenceGap, ...],
        available_projects: tuple[ProjectState, ...],
        processing_budget: ProcessingBudget,
        current_values: tuple[Value, ...],
    ) -> PracticePlan | None:
        del current_values
        if not gaps or processing_budget.max_tasks <= 0:
            return None
        ordered = sorted(gaps, key=lambda gap: (-(gap.target_level - gap.current_level), gap.skill_id))
        selected = ordered[: processing_budget.max_tasks]
        project_tasks = [
            task
            for project in sorted(available_projects, key=lambda item: (-item.viability, item.project_id))
            for task in project.available_tasks
        ]
        exercise_tasks = tuple(project_tasks[: len(selected)] or [f"practice:{gap.skill_id}" for gap in selected])
        difficulties = tuple(max(0.05, min(0.95, gap.current_level + 0.10)) for gap in selected)
        return PracticePlan(
            plan_id=self._id_factory(),
            target_skill_ids=tuple(gap.skill_id for gap in selected),
            exercise_tasks=exercise_tasks,
            difficulty_schedule=difficulties,
            success_criteria=tuple(f"externally_scored:{gap.skill_id}" for gap in selected),
            resource_budget=processing_budget,
            stop_conditions=("environmental_failure_repeats", "processing_budget_exhausted"),
            transfer_probe=f"transfer:{selected[0].skill_id}:new_context",
            created_at=self._clock(),
        )
