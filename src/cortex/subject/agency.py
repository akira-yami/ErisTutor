# -*- coding: utf-8 -*-
"""Learned Agency: biến calibration năng lực thành mục tiêu luyện tập có provenance.

Không tạo "đam mê học hỏi" bằng prompt. Module chỉ phát hiện khoảng cách năng lực
đã được đo qua outcome thật, rồi đề xuất practice plan bounded. Nếu lịch sử đổi,
projection này đổi theo; nó không phải một personality field bền riêng.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
from typing import Iterable

from .competence import CompetenceService, PracticePlanner
from .contracts import CompetenceEstimate, CompetenceGap, PracticePlan, ProcessingBudget, ProjectState, Value


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


@dataclass(frozen=True, slots=True)
class AgencyGap:
    skill_id: str
    context_key: str
    current_level: float
    target_level: float
    calibration_error: float
    trial_count: int
    evidence_refs: tuple[str, ...]
    pressure: float

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class LearnedAgencyProjection:
    gaps: tuple[AgencyGap, ...]
    practice_plan: PracticePlan | None
    transfer_candidates: tuple[str, ...]
    generated_from_estimate_ids: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "gaps": [item.to_dict() for item in self.gaps],
            "practice_plan": asdict(self.practice_plan) if self.practice_plan else None,
            "transfer_candidates": list(self.transfer_candidates),
            "generated_from_estimate_ids": list(self.generated_from_estimate_ids),
        }


class LearnedAgencyPlanner:
    """Dẫn xuất practice plan từ competence posterior và calibration error."""

    def __init__(self, competence: CompetenceService, planner: PracticePlanner | None = None) -> None:
        self._competence = competence
        # Giữ arg planner để không phá caller cũ, nhưng projection không gọi id_factory
        # của planner. View dẫn xuất không được tiêu thụ ID causal chỉ vì UI đọc state.
        self._planner = planner

    @staticmethod
    def _stable_plan_id(gaps: tuple[AgencyGap, ...]) -> str:
        material = "|".join(
            f"{item.skill_id}:{item.context_key}:{item.current_level:.6f}:{item.target_level:.6f}:{item.trial_count}"
            for item in gaps
        )
        return "practice." + hashlib.sha1(material.encode("utf-8")).hexdigest()[:16]

    @staticmethod
    def _gap_from_estimate(estimate: CompetenceEstimate) -> AgencyGap | None:
        # Hai trial vẫn quá dễ bị một lần hên/xui thao túng. Ba trial là ngưỡng
        # bootstrap; pressure tăng cùng bất định hiệu chỉnh và khoảng cách năng lực.
        if estimate.trial_count < 3:
            return None
        deficit = max(0.0, 0.68 - estimate.posterior_mean)
        calibration = max(0.0, estimate.calibration_error - 0.18)
        if deficit < 0.05 and calibration < 0.10:
            return None
        target = _clamp01(max(0.68, estimate.posterior_mean + 0.12))
        pressure = _clamp01(0.65 * deficit / 0.68 + 0.35 * estimate.calibration_error)
        return AgencyGap(
            skill_id=estimate.skill_id,
            context_key=estimate.context_key,
            current_level=estimate.posterior_mean,
            target_level=target,
            calibration_error=estimate.calibration_error,
            trial_count=estimate.trial_count,
            evidence_refs=estimate.evidence_refs,
            pressure=pressure,
        )

    def project(
        self,
        *,
        available_projects: Iterable[ProjectState] = (),
        current_values: Iterable[Value] = (),
        max_tasks: int = 2,
        max_seconds: int = 90,
        max_cost: float = 0.0,
    ) -> LearnedAgencyProjection:
        estimates = self._competence.all()
        gaps = tuple(
            sorted(
                (gap for estimate in estimates if (gap := self._gap_from_estimate(estimate)) is not None),
                key=lambda item: (-item.pressure, item.skill_id, item.context_key),
            )
        )
        contract_gaps = tuple(
            CompetenceGap(
                skill_id=item.skill_id,
                target_level=item.target_level,
                current_level=item.current_level,
                context_key=item.context_key,
            )
            for item in gaps[: max(0, int(max_tasks))]
        )
        projects = tuple(available_projects)
        budget = ProcessingBudget(
            max_tasks=max(0, int(max_tasks)),
            max_seconds=max(0, int(max_seconds)),
            max_cost=max(0.0, float(max_cost)),
        )
        plan = None
        if contract_gaps and budget.max_tasks > 0:
            project_tasks = [
                task
                for project in sorted(projects, key=lambda item: (-item.viability, item.project_id))
                for task in project.available_tasks
            ]
            exercise_tasks = tuple(
                project_tasks[: len(contract_gaps)]
                or [f"practice:{gap.skill_id}" for gap in contract_gaps]
            )
            plan = PracticePlan(
                plan_id=self._stable_plan_id(gaps[: len(contract_gaps)]),
                target_skill_ids=tuple(gap.skill_id for gap in contract_gaps),
                exercise_tasks=exercise_tasks,
                difficulty_schedule=tuple(
                    max(0.05, min(0.95, gap.current_level + 0.10)) for gap in contract_gaps
                ),
                success_criteria=tuple(f"externally_scored:{gap.skill_id}" for gap in contract_gaps),
                resource_budget=budget,
                stop_conditions=("environmental_failure_repeats", "processing_budget_exhausted"),
                transfer_probe=f"transfer:{contract_gaps[0].skill_id}:new_context",
                created_at=max((estimate.estimated_at for estimate in estimates), default=0.0),
            )
        transfers = tuple(
            f"{item.skill_id}:{item.context_key}->new_context"
            for item in gaps
            if item.trial_count >= 5 and item.current_level >= 0.45
        )[:8]
        return LearnedAgencyProjection(
            gaps=gaps[:12],
            practice_plan=plan,
            transfer_candidates=transfers,
            generated_from_estimate_ids=tuple(item.estimate_id for item in estimates),
        )

    def proposal_bias(self, *, action_kind: str, skill_id: str, context_key: str) -> tuple[float, float]:
        """Trả (project_contribution, epistemic_bonus) bounded cho proposal hiện tại.

        Practice/investigate chỉ được ưu tiên nếu competence thật đang có gap. Không
        có gap thì hàm trả 0, tránh biến "học" thành đức tính tác giả cấy sẵn.
        """
        matching = [
            gap
            for estimate in self._competence.all()
            if (gap := self._gap_from_estimate(estimate)) is not None
            and gap.skill_id == skill_id
            and gap.context_key in {context_key, "general"}
        ]
        if not matching:
            return 0.0, 0.0
        pressure = max(item.pressure for item in matching)
        if action_kind == "practice":
            return min(0.28, 0.28 * pressure), min(0.22, 0.22 * pressure)
        if action_kind in {"investigate", "observe", "verify"}:
            return min(0.14, 0.14 * pressure), min(0.18, 0.18 * pressure)
        return 0.0, 0.0


__all__ = ["AgencyGap", "LearnedAgencyPlanner", "LearnedAgencyProjection"]
