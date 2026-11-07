# -*- coding: utf-8 -*-
"""Release-level causal validation gate cho Eris.

Gộp paired-history, targeted ablation và soak thành một report duy nhất. Gate này
không chấm văn phong. Nó chỉ chấm việc lịch sử có để lại causal effect, có thể bị
ablate đúng mục tiêu và có sống qua restart/replay hay không.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence

from .ablation import AblationReport
from .report import ExperimentReport
from .runner import ExperimentSpec, run_benchmark_suite
from .soak import SoakReport, SoakSpec, run_simulated_soak


@dataclass(frozen=True, slots=True)
class CausalValidationReport:
    paired_reports: tuple[ExperimentReport, ...]
    ablation_reports: tuple[AblationReport, ...]
    soak_report: SoakReport | None
    passed: bool
    failures: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "paired_reports": [item.to_dict() for item in self.paired_reports],
            "ablation_reports": [item.to_dict() for item in self.ablation_reports],
            "soak_report": None if self.soak_report is None else self.soak_report.to_dict(),
            "passed": self.passed,
            "failures": list(self.failures),
        }

    def write_json(self, path: str | Path) -> None:
        Path(path).write_text(json.dumps(self.to_dict(), ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")

    def write_markdown_vi(self, path: str | Path) -> None:
        lines = [
            "# Causal Validation Report",
            "",
            f"- Trạng thái: **{'ĐẠT' if self.passed else 'CHƯA ĐẠT'}**",
            f"- Paired scenarios: {len(self.paired_reports)}",
            f"- Ablation probes: {len(self.ablation_reports)}",
            f"- Soak: {'có' if self.soak_report is not None else 'không'}",
            "",
        ]
        if self.failures:
            lines.extend(("## Failure", ""))
            lines.extend(f"- {item}" for item in self.failures)
        Path(path).write_text("\n".join(lines) + "\n", encoding="utf-8")


async def run_causal_validation(
    *,
    specs: Sequence[ExperimentSpec],
    instance_factory: Callable[[str, int], Any],
    ablations: Iterable[AblationReport] = (),
    soak_spec: SoakSpec | None = None,
    soak_instance_factory: Callable[[str, int], Any] | None = None,
    soak_turn_factory: Callable[[str, int, int], dict[str, Any]] | None = None,
) -> CausalValidationReport:
    paired = await run_benchmark_suite(specs, instance_factory=instance_factory)
    ablation_reports = tuple(ablations)
    soak_report: SoakReport | None = None
    if soak_spec is not None:
        if soak_instance_factory is None or soak_turn_factory is None:
            raise ValueError("soak gate requires instance_factory and turn_factory")
        soak_report = await run_simulated_soak(
            soak_spec,
            instance_factory=soak_instance_factory,
            turn_factory=soak_turn_factory,
        )
    failures: list[str] = []
    failures.extend(f"paired:{item.scenario_id}" for item in paired if not item.passed)
    failures.extend(f"ablation:{item.target_signal}" for item in ablation_reports if not item.passed)
    if soak_report is not None and not soak_report.passed:
        failures.append("soak")
    return CausalValidationReport(
        paired_reports=tuple(paired),
        ablation_reports=ablation_reports,
        soak_report=soak_report,
        passed=not failures,
        failures=tuple(failures),
    )


__all__ = ["CausalValidationReport", "run_causal_validation"]
