# -*- coding: utf-8 -*-
"""Định dạng báo cáo định lượng cho emergence harness."""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Mapping


@dataclass(frozen=True, slots=True)
class ExperimentReport:
    scenario_id: str
    code_revision: str
    algorithm_version: str
    metrics: Mapping[str, float]
    thresholds: Mapping[str, float]
    passed: bool
    run_count: int
    notes: tuple[str, ...] = ()
    split: str = "development"
    scenario_fingerprint: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def write_json(self, path: str | Path) -> None:
        Path(path).write_text(
            json.dumps(self.to_dict(), ensure_ascii=False, sort_keys=True, indent=2),
            encoding="utf-8",
        )

    def write_markdown_vi(self, path: str | Path) -> None:
        status = "ĐẠT" if self.passed else "CHƯA ĐẠT"
        lines = [
            f"# Báo cáo thí nghiệm: {self.scenario_id}",
            "",
            f"- Trạng thái: **{status}**",
            f"- Split: `{self.split}`",
            f"- Algorithm: `{self.algorithm_version}`",
            f"- Code revision: `{self.code_revision}`",
            f"- Số run: **{self.run_count}**",
            "",
            "## Metric",
            "",
            "| Metric | Giá trị | Ngưỡng tối thiểu |",
            "|---|---:|---:|",
        ]
        for key in sorted(self.metrics):
            threshold = self.thresholds.get(key)
            threshold_text = "-" if threshold is None else f"{threshold:.6f}"
            lines.append(f"| `{key}` | {float(self.metrics[key]):.6f} | {threshold_text} |")
        if self.notes:
            lines.extend(("", "## Ghi chú", ""))
            lines.extend(f"- {item}" for item in self.notes)
        Path(path).write_text("\n".join(lines) + "\n", encoding="utf-8")
