from __future__ import annotations

import pytest

from experiments.emergence.runner import ExperimentSpec, ProbeSpec, run_paired_experiment


class Instance:
    def __init__(self, snapshot: str, seed: int) -> None:
        self.history = 0.0
        self.seed = seed
        self.personality = True

    async def apply_history(self, history) -> None:
        self.history = sum(float(item.get("effect", 0.0)) for item in history)

    def disable_personality_descriptions(self) -> None:
        self.personality = False

    async def run_probe(self, payload, repetition: int):
        score = max(0.0, min(1.0, 0.2 + self.history + self.seed * 0.001))
        return {
            "history_score": score,
            "target_behavior": score,
            "predicted_success": score,
            "observed_success": 1.0 if score > 0.5 else 0.0,
            "context_only_loss": 1.0,
            "history_loss": 0.5,
        }


@pytest.mark.asyncio
async def test_paired_runner_produces_quantitative_report() -> None:
    spec = ExperimentSpec(
        scenario_id="test",
        initial_snapshot="base",
        seeds=(1, 2, 3),
        control_history=({"effect": 0.0},),
        treatment_history=({"effect": 0.7},),
        probes=(ProbeSpec("probe", {}, repetitions=2),),
        acceptance_thresholds={"history_classifier_auroc": 0.75},
    )
    report = await run_paired_experiment(spec, instance_factory=Instance)
    assert report.run_count == 12
    assert report.metrics["history_classifier_auroc"] == 1.0
    assert report.passed
