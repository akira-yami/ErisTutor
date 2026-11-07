# -*- coding: utf-8 -*-
"""Runner paired-instance có split development/calibration/holdout.

Holdout mặc định buộc 20 seed và mỗi probe 5 lần theo đặc tả. Development vẫn
cho phép mẫu nhỏ để test nhanh, tránh biến mỗi unit test thành một hội nghị kéo
dài ba ngày.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Sequence

from .interventions import apply_history, remove_personality_descriptions
from .metrics import compute_metrics
from .paired_instances import clone_instance
from .report import ExperimentReport


@dataclass(frozen=True, slots=True)
class ProbeSpec:
    probe_id: str
    payload: Mapping[str, Any]
    repetitions: int = 1

    def __post_init__(self) -> None:
        if self.repetitions < 1:
            raise ValueError("probe repetitions must be positive")


@dataclass(frozen=True, slots=True)
class ExperimentSpec:
    scenario_id: str
    initial_snapshot: str
    seeds: tuple[int, ...]
    control_history: tuple[Mapping[str, Any], ...]
    treatment_history: tuple[Mapping[str, Any], ...]
    probes: tuple[ProbeSpec, ...]
    acceptance_thresholds: Mapping[str, float]
    maximum_thresholds: Mapping[str, float] = field(default_factory=dict)
    remove_personality_prompt: bool = True
    code_revision: str = "unknown"
    algorithm_version: str = "subject-core-v1"
    split: str = "development"
    required_features: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.split not in {"development", "calibration", "holdout"}:
            raise ValueError("split must be development, calibration or holdout")
        if not self.seeds:
            raise ValueError("experiment requires at least one seed")
        if not self.probes:
            raise ValueError("experiment requires at least one probe")
        if self.split == "holdout":
            if len(self.seeds) < 20:
                raise ValueError("holdout requires at least 20 seed pairs")
            if any(probe.repetitions < 5 for probe in self.probes):
                raise ValueError("holdout probes require at least 5 repetitions")

    def fingerprint(self) -> str:
        payload = {
            "scenario_id": self.scenario_id,
            "initial_snapshot": self.initial_snapshot,
            "seeds": list(self.seeds),
            "control_history": list(self.control_history),
            "treatment_history": list(self.treatment_history),
            "probes": [
                {
                    "probe_id": probe.probe_id,
                    "payload": dict(probe.payload),
                    "repetitions": probe.repetitions,
                }
                for probe in self.probes
            ],
            "split": self.split,
            "maximum_thresholds": dict(self.maximum_thresholds),
        }
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class RunResult:
    seed: int
    condition: str
    probe_id: str
    repetition: int
    features: Mapping[str, float]

    def to_metric_row(self) -> dict[str, object]:
        return {
            "seed": self.seed,
            "condition": self.condition,
            "probe_id": self.probe_id,
            "repetition": self.repetition,
            **dict(self.features),
        }


async def _run_probe(instance: Any, probe: ProbeSpec, repetition: int) -> Mapping[str, float]:
    method = getattr(instance, "run_probe", None)
    if method is None:
        raise TypeError("experiment instance must implement run_probe")
    result = method(probe.payload, repetition=repetition)
    if hasattr(result, "__await__"):
        result = await result
    return {str(key): float(value) for key, value in dict(result).items()}


async def run_paired_experiment(
    spec: ExperimentSpec,
    *,
    instance_factory: Callable[[str, int], Any],
) -> ExperimentReport:
    results: list[RunResult] = []
    missing_features: set[str] = set()
    for seed in spec.seeds:
        control = await clone_instance(instance_factory, spec.initial_snapshot, seed=seed)
        treatment = await clone_instance(instance_factory, spec.initial_snapshot, seed=seed)
        await apply_history(control, spec.control_history)
        await apply_history(treatment, spec.treatment_history)
        if spec.remove_personality_prompt:
            remove_personality_descriptions(control)
            remove_personality_descriptions(treatment)
        for probe in spec.probes:
            for repetition in range(probe.repetitions):
                control_features, treatment_features = await asyncio.gather(
                    _run_probe(control, probe, repetition),
                    _run_probe(treatment, probe, repetition),
                )
                for feature in spec.required_features:
                    if feature not in control_features or feature not in treatment_features:
                        missing_features.add(feature)
                results.extend((
                    RunResult(seed, "control", probe.probe_id, repetition, control_features),
                    RunResult(seed, "treatment", probe.probe_id, repetition, treatment_features),
                ))
    metrics = compute_metrics(result.to_metric_row() for result in results)
    failures = []
    for key, threshold in spec.acceptance_thresholds.items():
        value = float(metrics.get(key, float("-inf")))
        if value < float(threshold):
            failures.append(f"{key}={value:.4f} < {threshold:.4f}")
    for key, threshold in spec.maximum_thresholds.items():
        value = float(metrics.get(key, float("inf")))
        if value > float(threshold):
            failures.append(f"{key}={value:.4f} > {threshold:.4f}")
    if missing_features:
        failures.append("thiếu behavioral features: " + ", ".join(sorted(missing_features)))
    return ExperimentReport(
        scenario_id=spec.scenario_id,
        code_revision=spec.code_revision,
        algorithm_version=spec.algorithm_version,
        metrics=metrics,
        thresholds=dict(spec.acceptance_thresholds),
        passed=not failures,
        run_count=len(results),
        notes=tuple(failures),
        split=spec.split,
        scenario_fingerprint=spec.fingerprint(),
    )


async def run_benchmark_suite(
    specs: Sequence[ExperimentSpec],
    *,
    instance_factory: Callable[[str, int], Any],
) -> tuple[ExperimentReport, ...]:
    """Chạy nhiều scenario và ngăn reuse cùng fingerprint giữa các split.

    Đây là hàng rào tối thiểu chống việc tune trên holdout rồi đổi tên file. Một
    hành vi con người rất sáng tạo, tiếc là không hữu ích cho nghiên cứu.
    """

    fingerprints: dict[str, str] = {}
    reports: list[ExperimentReport] = []
    for spec in specs:
        fingerprint = spec.fingerprint()
        previous_split = fingerprints.get(fingerprint)
        if previous_split is not None and previous_split != spec.split:
            raise ValueError("scenario fingerprint reused across experiment splits")
        fingerprints[fingerprint] = spec.split
        reports.append(await run_paired_experiment(spec, instance_factory=instance_factory))
    return tuple(reports)


__all__ = [
    "ExperimentSpec",
    "ProbeSpec",
    "RunResult",
    "run_benchmark_suite",
    "run_paired_experiment",
]
