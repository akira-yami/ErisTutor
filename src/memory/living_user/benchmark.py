# -*- coding: utf-8 -*-
"""Benchmark nhỏ, reproducible cho living user memory.

Unit test kiểm tra từng hàm. Benchmark kiểm tra hành vi end-to-end: setup profile,
compile context, đo recall của path cần có và false-memory từ path bị cấm. File
case là JSONL để người dùng bổ sung hội thoại thật mà không sửa code runner.
"""
from __future__ import annotations

import json
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping

from .models import ProfileOperation, UserMemoryScope
from .service import LivingUserMemoryService


@dataclass(frozen=True, slots=True)
class BenchmarkMetrics:
    cases: int
    recall_at_k: float
    false_memory_rate: float
    clarification_accuracy: float
    passed: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "cases": self.cases, "recall_at_k": round(self.recall_at_k, 5),
            "false_memory_rate": round(self.false_memory_rate, 5),
            "clarification_accuracy": round(self.clarification_accuracy, 5), "passed": self.passed,
        }


class LivingUserMemoryBenchmark:
    def load_cases(self, path: str | Path) -> tuple[dict[str, Any], ...]:
        rows = []
        with Path(path).open("r", encoding="utf-8") as handle:
            for line in handle:
                stripped = line.strip()
                if stripped and not stripped.startswith("#"):
                    raw = json.loads(stripped)
                    if isinstance(raw, Mapping):
                        rows.append(dict(raw))
        return tuple(rows)

    def run(self, cases: Iterable[Mapping[str, Any]]) -> BenchmarkMetrics:
        case_rows = tuple(dict(case) for case in cases)
        total = hits = false_hits = expected_false_slots = clarification_hits = clarification_total = passed = 0
        expected_path_slots = 0
        for case in case_rows:
            total += 1
            with tempfile.TemporaryDirectory(prefix="eris_memory_bench_") as temp:
                service = LivingUserMemoryService(temp)
                operations = []
                for index, raw in enumerate(case.get("profile") or ()):
                    if not isinstance(raw, Mapping):
                        continue
                    operations.append(ProfileOperation(
                        op=str(raw.get("op") or "add"), path=str(raw.get("path") or ""), value=raw.get("value"),  # type: ignore[arg-type]
                        confidence=float(raw.get("confidence") or 0.9), explicitness=str(raw.get("explicitness") or "explicit"),  # type: ignore[arg-type]
                        sensitivity=str(raw.get("sensitivity") or "normal"),  # type: ignore[arg-type]
                        scope=UserMemoryScope.from_value(raw.get("scope")), cardinality=str(raw.get("cardinality") or "single"),  # type: ignore[arg-type]
                        evidence_ids=(str(raw.get("evidence_id") or f"bench_{total}_{index}"),), reason="benchmark setup", source="benchmark",
                    ))
                service.profile_store.apply(operations, checkpoint_reason="benchmark_setup")
                packet = service.compile_context(str(case.get("query") or ""), project_id=str(case.get("project_id") or ""))
                selected = {item.path for item in packet.items}
                expected = set(str(x) for x in case.get("expected_paths") or ())
                excluded = set(str(x) for x in case.get("excluded_paths") or ())
                hits += len(expected & selected)
                expected_path_slots += max(1, len(expected))
                false_hits += len(excluded & selected)
                expected_false_slots += max(1, len(excluded))
                expected_mode = str(case.get("expected_clarification") or "")
                if expected_mode:
                    clarification_total += 1
                    actual_modes = {item.path: getattr(item, "use_mode", "direct") for item in packet.items}
                    actual = next(iter(actual_modes.values()), "omit")
                    if actual == expected_mode:
                        clarification_hits += 1
                case_pass = (not expected or expected.issubset(selected)) and not (excluded & selected)
                passed += int(case_pass)
        return BenchmarkMetrics(
            cases=total, recall_at_k=hits / max(1, expected_path_slots),
            false_memory_rate=false_hits / max(1, expected_false_slots),
            clarification_accuracy=clarification_hits / max(1, clarification_total), passed=passed,
        )


__all__ = ["BenchmarkMetrics", "LivingUserMemoryBenchmark"]
