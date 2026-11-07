from __future__ import annotations

import json
from pathlib import Path

import pytest

from experiments.emergence.ablation import run_causal_ablation
from experiments.emergence.metrics import compute_metrics
from experiments.emergence.runner import ExperimentSpec, ProbeSpec
from experiments.emergence.soak import SoakSpec, run_simulated_soak
from memory.subject import SubjectRepository


def test_holdout_enforces_20_pairs_and_5_repetitions() -> None:
    with pytest.raises(ValueError, match="20 seed pairs"):
        ExperimentSpec(
            scenario_id="holdout",
            initial_snapshot="base",
            seeds=tuple(range(10)),
            control_history=(),
            treatment_history=(),
            probes=(ProbeSpec("probe", {}, repetitions=5),),
            acceptance_thresholds={},
            split="holdout",
        )
    with pytest.raises(ValueError, match="5 repetitions"):
        ExperimentSpec(
            scenario_id="holdout",
            initial_snapshot="base",
            seeds=tuple(range(20)),
            control_history=(),
            treatment_history=(),
            probes=(ProbeSpec("probe", {}, repetitions=4),),
            acceptance_thresholds={},
            split="holdout",
        )


def test_metrics_include_spec_acceptance_quantities() -> None:
    rows = []
    for seed in range(4):
        for repetition in range(3):
            rows.append({
                "seed": seed,
                "condition": "control",
                "probe_id": "p",
                "repetition": repetition,
                "history_score": 0.1,
                "target_behavior": 0.2,
                "predicted_success": 0.2,
                "observed_success": 0.0,
                "context_only_loss": 0.8,
                "history_loss": 0.6,
                "context_only_brier": 0.4,
                "history_brier": 0.3,
                "full_context_effect": 0.5,
                "stateful_effect": 0.4,
                "source_effect": 0.5,
                "transfer_effect": 0.25,
                "ablation_baseline_target": 0.8,
                "ablation_target": 0.4,
                "ablation_collateral_change": 0.05,
                "authorship_integrity": 1.0,
                "causal_replay_exact": 1.0,
            })
            rows.append({
                **rows[-1],
                "condition": "treatment",
                "history_score": 0.9,
                "target_behavior": 0.8,
                "predicted_success": 0.8,
                "observed_success": 1.0,
            })
    metrics = compute_metrics(rows)
    assert metrics["history_classifier_auroc"] == 1.0
    assert metrics["within_condition_icc"] == 1.0
    assert metrics["effect_retention_ratio"] == pytest.approx(0.8)
    assert metrics["transfer_ratio"] == pytest.approx(0.5)
    assert metrics["ablation_target_drop"] == pytest.approx(0.5)
    assert metrics["authorship_integrity"] == 1.0


def test_causal_ablation_removes_descendants_and_limits_collateral(tmp_path: Path) -> None:
    repo = SubjectRepository(tmp_path / "subject.db")
    root = repo.append_subject_event(
        subject_id="eris",
        event_type="mind_decision",
        event_id="decision-root",
        payload={
            "decision_id": "d1",
            "proposed_action": "investigate",
            "arguments": {"context": "research"},
        },
    )
    repo.append_subject_event(
        subject_id="eris",
        event_type="world_outcome",
        event_id="outcome-root",
        causal_refs=(root.event_id,),
        payload={"decision_id": "d1", "status": "succeeded"},
    )
    repo.append_subject_event(
        subject_id="eris",
        event_type="mind_decision",
        event_id="decision-other",
        payload={
            "decision_id": "d2",
            "proposed_action": "speak",
            "arguments": {"context": "social"},
        },
    )
    repo.append_subject_event(
        subject_id="eris",
        event_type="world_outcome",
        event_id="outcome-other",
        causal_refs=("decision-other",),
        payload={"decision_id": "d2", "status": "succeeded"},
    )
    report = run_causal_ablation(
        repo.database_path,
        subject_id="eris",
        event_ids=("decision-root",),
        target_signal="action_frequency:investigate",
        collateral_signals=("action_success:speak",),
    )
    assert "outcome-root" in report.excluded_event_ids
    assert report.target_drop == pytest.approx(1.0)
    assert report.collateral_change == pytest.approx(0.0)
    assert report.passed
    repo.close()


class FakeSoakInstance:
    _stores: dict[str, list[dict]] = {}

    def __init__(self, subject_id: str, seed: int) -> None:
        self.subject_id = subject_id
        self.seed = seed
        self._stores.setdefault(subject_id, [])

    async def apply_turn(self, payload):
        assert payload["subject_id"] == self.subject_id
        self._stores[self.subject_id].append(dict(payload))

    def snapshot(self):
        return {
            "subject_id": self.subject_id,
            "turns": len(self._stores[self.subject_id]),
            "last": self._stores[self.subject_id][-1] if self._stores[self.subject_id] else None,
        }

    def restart(self):
        return FakeSoakInstance(self.subject_id, self.seed)

    def verify_event_chain(self):
        return True

    def verify_replay(self):
        encoded = json.dumps(self.snapshot(), sort_keys=True)
        return self.subject_id in encoded

    def close(self):
        return None


@pytest.mark.asyncio
async def test_simulated_soak_checks_restart_replay_and_isolation() -> None:
    FakeSoakInstance._stores.clear()
    report = await run_simulated_soak(
        SoakSpec(
            simulated_days=2,
            turns_per_day=4,
            restart_every_turns=3,
            subject_ids=("eris-a", "eris-b"),
        ),
        instance_factory=FakeSoakInstance,
        turn_factory=lambda subject, day, turn: {"day": day, "turn": turn},
    )
    assert report.total_turns == 16
    assert report.restart_count > 0
    assert report.event_chain_ok
    assert report.replay_stable
    assert report.cross_instance_leakage == 0
    assert report.passed


def test_repository_persists_experiment_report(tmp_path: Path) -> None:
    repo = SubjectRepository(tmp_path / "subject.db")
    payload = {
        "scenario_id": "counterfactual",
        "split": "development",
        "algorithm_version": "v8",
        "code_revision": "test",
        "scenario_fingerprint": "abc",
        "passed": True,
        "run_count": 12,
        "metrics": {"history_classifier_auroc": 1.0},
    }
    run_id = repo.save_experiment_report(payload)
    assert run_id
    rows = repo.experiment_reports(scenario_id="counterfactual")
    assert rows[0]["passed"] is True
    repo.close()

@pytest.mark.asyncio
async def test_subject_service_adapter_proves_history_changes_choice() -> None:
    from experiments.emergence.runner import run_paired_experiment
    from experiments.emergence.subject_instance import SubjectExperimentInstance

    spec = ExperimentSpec(
        scenario_id="subject-core-counterfactual",
        initial_snapshot="base",
        seeds=(1, 2, 3),
        control_history=({"action": "speak", "context": "research", "success": True, "repeat": 4},),
        treatment_history=({"action": "investigate", "context": "research", "success": True, "repeat": 4},),
        probes=(
            ProbeSpec(
                "research-choice",
                {
                    "context": "research",
                    "target_action": "investigate",
                    "alternative_action": "speak",
                },
                repetitions=2,
            ),
        ),
        acceptance_thresholds={"history_classifier_auroc": 0.75},
        required_features=("target_behavior", "history_score"),
    )
    report = await run_paired_experiment(spec, instance_factory=SubjectExperimentInstance)
    assert report.passed
    assert report.metrics["history_classifier_auroc"] >= 0.75

@pytest.mark.asyncio
async def test_repository_backed_soak_survives_restart(tmp_path: Path) -> None:
    from experiments.emergence.repository_soak import make_repository_soak_factory

    report = await run_simulated_soak(
        SoakSpec(
            simulated_days=1,
            turns_per_day=3,
            restart_every_turns=2,
            subject_ids=("eris-a", "eris-b"),
        ),
        instance_factory=make_repository_soak_factory(tmp_path / "soak"),
        turn_factory=lambda subject, day, turn: {
            "context": "coding" if turn % 2 == 0 else "social",
            "success": turn % 3 != 0,
        },
    )
    assert report.event_chain_ok
    assert report.replay_stable
    assert report.cross_instance_leakage == 0
    assert report.passed
