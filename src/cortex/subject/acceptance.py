# -*- coding: utf-8 -*-
"""Acceptance/invariant evaluator cho kiến trúc tích hợp V56.

Báo cáo PDF không chỉ yêu cầu thêm module mà còn yêu cầu invariants, scenario
simulation và soak test. Module này đọc Decision Ledger như dữ liệu kiểm toán,
không chạm vào policy runtime.
"""
from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from typing import Any, Iterable, Mapping

from .decision_ledger import DecisionLedgerRecord


def _as_record(value: DecisionLedgerRecord | Mapping[str, Any]) -> Mapping[str, Any]:
    return value.to_dict() if isinstance(value, DecisionLedgerRecord) else value


def _finite_bounded(value: Any, low: float, high: float) -> bool:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return False
    return isfinite(number) and low <= number <= high


@dataclass(frozen=True, slots=True)
class InvariantCheck:
    name: str
    passed: bool
    detail: str

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "passed": self.passed, "detail": self.detail}


@dataclass(frozen=True, slots=True)
class IntegratedAcceptanceReport:
    record_count: int
    completed_count: int
    decision_coverage: float
    outcome_coverage: float
    mean_abs_goal_congruence: float
    max_need_pressure: float
    checks: tuple[InvariantCheck, ...]

    @property
    def passed(self) -> bool:
        return bool(self.checks) and all(item.passed for item in self.checks)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": "eris.integrated_acceptance.v1",
            "passed": self.passed,
            "record_count": self.record_count,
            "completed_count": self.completed_count,
            "metrics": {
                "decision_coverage": round(self.decision_coverage, 6),
                "outcome_coverage": round(self.outcome_coverage, 6),
                "mean_abs_goal_congruence": round(self.mean_abs_goal_congruence, 6),
                "max_need_pressure": round(self.max_need_pressure, 6),
            },
            "checks": [item.to_dict() for item in self.checks],
        }


class IntegratedAcceptanceEvaluator:
    """Kiểm tra các invariant cơ bản của causal loop dài hạn."""

    def evaluate(
        self,
        records: Iterable[DecisionLedgerRecord | Mapping[str, Any]],
    ) -> IntegratedAcceptanceReport:
        rows = [dict(_as_record(item)) for item in records]
        if not rows:
            return IntegratedAcceptanceReport(
                record_count=0,
                completed_count=0,
                decision_coverage=0.0,
                outcome_coverage=0.0,
                mean_abs_goal_congruence=0.0,
                max_need_pressure=0.0,
                checks=(InvariantCheck("non_empty_trace", False, "ledger không có record"),),
            )

        observation_ids = [str(item.get("observation_id") or "") for item in rows]
        unique_observations = len(set(observation_ids)) == len(observation_ids) and all(observation_ids)

        signed_ok = True
        need_ok = True
        causal_order_ok = True
        state_refs_ok = True
        congruences: list[float] = []
        need_pressures: list[float] = []
        decision_count = 0
        completed_count = 0

        for row in rows:
            decision_id = str(row.get("decision_id") or "")
            outcome_id = str(row.get("outcome_id") or "")
            if decision_id:
                decision_count += 1
            if outcome_id:
                completed_count += 1
                if not decision_id:
                    causal_order_ok = False
            before = row.get("before_state") if isinstance(row.get("before_state"), Mapping) else {}
            appraised = row.get("appraisal_state") if isinstance(row.get("appraisal_state"), Mapping) else {}
            after = row.get("after_state") if isinstance(row.get("after_state"), Mapping) else {}
            if not str(before.get("substrate_ref") or "") or not str(appraised.get("substrate_ref") or ""):
                state_refs_ok = False
            if outcome_id and not str(after.get("substrate_ref") or ""):
                state_refs_ok = False

            appraisals = appraised.get("appraisals", ()) if isinstance(appraised, Mapping) else ()
            for appraisal in appraisals if isinstance(appraisals, (list, tuple)) else ():
                if not isinstance(appraisal, Mapping):
                    continue
                for key in ("goal_congruence", "goal_progress_delta"):
                    value = appraisal.get(key, 0.0)
                    if not _finite_bounded(value, -1.0, 1.0):
                        signed_ok = False
                try:
                    congruences.append(abs(float(appraisal.get("goal_congruence", 0.0) or 0.0)))
                except (TypeError, ValueError):
                    signed_ok = False

            needs = appraised.get("needs", {}) if isinstance(appraised, Mapping) else {}
            for state in needs.values() if isinstance(needs, Mapping) else ():
                if not isinstance(state, Mapping):
                    continue
                pressure = state.get("pressure", 0.0)
                if not _finite_bounded(pressure, 0.0, 1.0):
                    need_ok = False
                try:
                    need_pressures.append(float(pressure))
                except (TypeError, ValueError):
                    need_ok = False

            modulators = appraised.get("modulators", {}) if isinstance(appraised, Mapping) else {}
            for value in modulators.values() if isinstance(modulators, Mapping) else ():
                if not _finite_bounded(value, 0.0, 1.0):
                    need_ok = False

        count = len(rows)
        checks = (
            InvariantCheck(
                "unique_observation",
                unique_observations,
                "mỗi observation chỉ có một causal trace" if unique_observations else "phát hiện observation trùng/mất ID",
            ),
            InvariantCheck(
                "signed_appraisal_bounded",
                signed_ok,
                "goal congruence/progress nằm trong [-1,1]" if signed_ok else "appraisal có NaN/Inf/vượt biên",
            ),
            InvariantCheck(
                "need_and_modulator_bounded",
                need_ok,
                "pressure/modulator nằm trong [0,1]" if need_ok else "pressure/modulator runaway",
            ),
            InvariantCheck(
                "causal_order",
                causal_order_ok,
                "outcome luôn có decision trước" if causal_order_ok else "outcome xuất hiện không có decision",
            ),
            InvariantCheck(
                "state_provenance",
                state_refs_ok,
                "snapshot có substrate provenance" if state_refs_ok else "snapshot thiếu substrate_ref",
            ),
        )
        return IntegratedAcceptanceReport(
            record_count=count,
            completed_count=completed_count,
            decision_coverage=decision_count / max(1, count),
            outcome_coverage=completed_count / max(1, count),
            mean_abs_goal_congruence=(sum(congruences) / len(congruences) if congruences else 0.0),
            max_need_pressure=max(need_pressures, default=0.0),
            checks=checks,
        )


__all__ = [
    "IntegratedAcceptanceEvaluator",
    "IntegratedAcceptanceReport",
    "InvariantCheck",
]
