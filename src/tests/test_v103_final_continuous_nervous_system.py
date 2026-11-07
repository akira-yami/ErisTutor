# -*- coding: utf-8 -*-
"""Acceptance/regression cho V103 FINAL theo roadmap Continuous Activation.

Các test này cố ý kiểm *authority boundary*, không chỉ kiểm một vài threshold:
- off/shadow/active có semantics khác nhau rõ ràng;
- motor cần dwell + semantic provenance;
- SubjectInitiative trở thành sensor/modulator ở active mode;
- Neuro candidate score không còn là authority của normal autonomous expression;
- critic chỉ bơm uncertainty/inhibition;
- STP depression/facilitation tạo refractory tự nhiên;
- health/replay vẫn bounded + deterministic.
"""
from __future__ import annotations

import random
from types import SimpleNamespace

import pytest

from runtime.activation_nervous_system_v98 import (
    ACTIVATION_FIELDS,
    ActivationSnapshot,
    ContinuousDynamicsRuntime,
    SharedActivationBus,
)
from runtime.nervous_system_v103_final import (
    MotorSemanticTarget,
    NervousAutonomousAuthorityV103,
    NervousHealthMonitorV103,
    NervousSystemConfigV103,
    ShortTermPlasticityV103,
)


class _Logger:
    def debug(self, *args, **kwargs):
        pass
    def info(self, *args, **kwargs):
        pass
    def warning(self, *args, **kwargs):
        pass


def _snapshot(**overrides) -> ActivationSnapshot:
    values = {
        "curiosity": 0.90,
        "boredom": 0.15,
        "social_need": 0.75,
        "task_focus": 0.30,
        "concern_salience": 0.95,
        "uncertainty": 0.03,
        "expressive_pressure": 0.98,
        "inhibition": 0.02,
        "fatigue": 0.02,
    }
    values.update(overrides)
    return ActivationSnapshot(revision=7, timestamp=0.0, values=values, tick_count=12)


def _target(source_id: str = "urge:alpha", *, refs=("evidence:1", "evidence:2"), uncertainty=0.02):
    return MotorSemanticTarget(
        source_id=source_id,
        semantic_target=f"Một ý có provenance từ {source_id}.",
        motive="share",
        pressure=0.96,
        freshness=0.98,
        persistence=0.95,
        uncertainty=uncertainty,
        evidence_refs=tuple(refs),
    )


def _candidate(source_id: str, *, topic: str | None = None):
    content = topic or f"Nội dung semantic của {source_id}."
    return SimpleNamespace(
        kind="random_chat",
        topic=content,
        speech_act="share_self_originated_thought",
        evidence_refs=("focus:x", "material:y"),
        uncertainty=0.04,
        expression_commitment={
            "origin": "expression_urge",
            "source_id": source_id,
            "semantic_target": content,
            "motive": "share",
            "pressure": 0.96,
            "freshness": 0.98,
            "persistence": 0.95,
        },
        expression_urge_id=source_id,
        expression_motive="share",
        expression_pressure=0.96,
        expression_freshness=0.98,
        expression_satisfaction=0.0,
        relevance=0.8,
        urgency=0.4,
    )


def _affordance(owner: str, source_id: str = "urge:alpha", *, kind: str | None = None):
    candidate = _candidate(source_id)
    if kind is not None:
        candidate.kind = kind
    return SimpleNamespace(
        affordance_id=f"aff:{owner}:{source_id}",
        owner=owner,
        candidate=candidate,
    )


def _coalition(action: str, *, source_id: str = "", score: float = 0.5, uncertainty: float = 0.1):
    args = {}
    if source_id:
        args["expression_commitment"] = {
            "origin": "expression_urge",
            "source_id": source_id,
            "semantic_target": f"Nội dung semantic của {source_id}.",
        }
    return SimpleNamespace(
        action_kind=action,
        arguments=args,
        score=score,
        uncertainty=uncertainty,
    )


def _frame(*coalitions):
    return SimpleNamespace(
        frame_id="frame:v103-final",
        coalitions=tuple(coalitions),
        appraisals=(),
        activations=(),
        proposals=(),
        substrate=SimpleNamespace(processing_capacity=1.0),
    )


def test_mode_off_is_true_legacy_rollback_without_modulation_or_filtering():
    bus = SharedActivationBus(clock=lambda: 0.0)
    authority = NervousAutonomousAuthorityV103(
        bus,
        config=NervousSystemConfigV103(mode="off"),
        clock=lambda: 0.0,
        logger=_Logger(),
    )
    rows = (_affordance("subject_initiative", "legacy:1"),)

    returned, decision = authority.preprocess_affordances(rows, now=0.0)

    assert returned == rows
    assert decision.reason == "nervous_mode_off"
    assert bus.recent_events() == ()
    assert authority.status()["contract"]["normal_autonomous_candidate_voting"] is True


def test_shadow_observes_but_does_not_take_candidate_authority():
    bus = SharedActivationBus(clock=lambda: 0.0)
    authority = NervousAutonomousAuthorityV103(
        bus,
        config=NervousSystemConfigV103(mode="shadow"),
        clock=lambda: 0.0,
        logger=_Logger(),
    )
    rows = (_affordance("subject_initiative", "legacy:1"), _affordance("expression_urge", "urge:a"))

    returned, _ = authority.preprocess_affordances(rows, now=0.0)

    assert returned == rows
    assert authority.selection_for_frame(_frame(_coalition("silence"))) is None


def test_proposition_requires_semantic_target_and_stable_dwell():
    bus = SharedActivationBus(clock=lambda: 0.0)
    authority = NervousAutonomousAuthorityV103(
        bus,
        config=NervousSystemConfigV103(mode="active"),
        clock=lambda: 0.0,
    )
    snapshot = _snapshot()

    # Không target/provenance: proposition drive phải bằng 0 dù expressive pressure rất cao.
    no_target = authority.motor.evaluate(snapshot, targets=(), now=0.0)
    assert no_target.drives["proposition"] == 0.0
    assert no_target.channel != "proposition"

    # Có target nhưng tick đầu vẫn chỉ settle; dwell mới cho commit proposition.
    authority2 = NervousAutonomousAuthorityV103(
        SharedActivationBus(clock=lambda: 0.0),
        config=NervousSystemConfigV103(mode="active"),
        clock=lambda: 0.0,
    )
    first = authority2.motor.evaluate(snapshot, targets=(_target(),), now=0.0)
    later = authority2.motor.evaluate(snapshot, targets=(_target(),), now=0.55)
    assert first.channel == "silence" and not first.committed
    assert later.channel == "proposition" and later.committed
    assert later.semantic_source_id == "urge:alpha"


def test_hard_boundary_suppresses_public_motor_without_erasing_semantic_target():
    authority = NervousAutonomousAuthorityV103(
        SharedActivationBus(clock=lambda: 0.0),
        config=NervousSystemConfigV103(mode="active"),
        clock=lambda: 0.0,
    )
    snapshot = _snapshot()
    target = _target()
    authority.motor.evaluate(snapshot, targets=(target,), now=0.0)
    blocked = authority.motor.evaluate(
        snapshot, targets=(target,), runtime_context={"user_holds_turn": True}, now=0.6
    )
    reopened = authority.motor.evaluate(snapshot, targets=(target,), now=1.2)

    assert blocked.channel == "silence"
    assert blocked.hard_boundary is True
    assert reopened.semantic_source_id == target.source_id


def test_subject_initiative_is_sensor_in_active_mode_but_structured_owner_survives():
    bus = SharedActivationBus(clock=lambda: 10.0)
    authority = NervousAutonomousAuthorityV103(
        bus,
        config=NervousSystemConfigV103(mode="active"),
        clock=lambda: 10.0,
    )
    generic = _affordance("subject_initiative", "subject:resume")
    structured = _affordance("conversation_arc", "arc:1", kind="conversation_arc")

    returned, _ = authority.preprocess_affordances((generic, structured), now=10.0)

    assert generic not in returned
    assert structured in returned
    events = bus.recent_events(limit=20)
    assert any(row["source"] == "subject_initiative_sensor_v103" for row in events)
    assert all("candidate" not in row for row in events)


def test_normal_autonomous_frame_uses_motor_candidate_bypass_not_coalition_score():
    bus = SharedActivationBus(
        clock=lambda: 0.0,
        initial_values=_snapshot().values,
    )
    authority = NervousAutonomousAuthorityV103(
        bus,
        config=NervousSystemConfigV103(mode="active"),
        clock=lambda: 0.0,
    )
    affordance = _affordance("expression_urge", "urge:chosen")

    authority.preprocess_affordances((affordance,), now=0.0)
    filtered, decision = authority.preprocess_affordances((affordance,), now=0.55)
    assert decision.channel == "proposition"
    assert filtered == (affordance,)

    # Candidate được motor chọn có score rất thấp; đối thủ có score rất cao.
    # V103 FINAL phải vẫn map theo source_id, không quay lại score winner.
    frame = _frame(
        _coalition("speak", source_id="urge:other", score=99.0),
        _coalition("speak", source_id="urge:chosen", score=-99.0),
        _coalition("silence", score=999.0),
    )
    selection = authority.selection_for_frame(frame)

    assert selection is not None
    assert selection.winner_index == 1
    assert selection.probabilities == (0.0, 1.0, 0.0)
    assert selection.reason == "candidate_bypass:nervous_motor:proposition"
    assert authority.status()["health"]["candidate_bypass_count"] == 1


def test_semantic_critic_only_emits_uncertainty_and_inhibition_modulation():
    bus = SharedActivationBus(clock=lambda: 20.0)
    authority = NervousAutonomousAuthorityV103(
        bus,
        config=NervousSystemConfigV103(mode="active"),
        clock=lambda: 20.0,
    )

    event = authority.inject_semantic_modulation(
        source_id="urge:x",
        uncertainty=0.88,
        epistemic_support=0.12,
        now=20.0,
    )

    assert set(event.targets) == {"uncertainty", "inhibition"}
    assert event.targets["uncertainty"] == pytest.approx(0.88)
    assert event.targets["inhibition"] > 0.70
    assert event.impulses["expressive_pressure"] < 0.0
    assert "critic_no_vote" in event.tags


def test_stp_depresses_repetition_and_fresh_evidence_partially_reopens_source_from_zero_clock():
    config = NervousSystemConfigV103(mode="active")
    stp = ShortTermPlasticityV103(config, clock=lambda: 0.0)
    target = _target(refs=("ev:old",))

    baseline = stp.gain(target, now=0.0)
    stp.note_commit(target.source_id, now=0.0)
    depressed = stp.gain(target, now=1.0)
    stp.note_fresh_evidence(target.source_id, strength=0.9, now=2.0)
    reopened = stp.gain(_target(refs=("ev:new",)), now=2.0)
    recovered = stp.gain(_target(refs=("ev:new",)), now=240.0)

    assert baseline == pytest.approx(1.0)
    assert depressed < baseline
    assert reopened > depressed
    assert recovered > depressed


def test_health_monitor_collects_field_histograms_and_tick_percentiles_through_dynamics_hook():
    health = NervousHealthMonitorV103(max_samples=128)
    bus = SharedActivationBus(clock=lambda: 0.0)
    dynamics = ContinuousDynamicsRuntime(
        bus,
        tick_interval_sec=0.075,
        clock=lambda: 0.0,
        health_observer=health,
    )

    for index in range(40):
        dynamics.tick_once(now=(index + 1) * 0.075, dt=0.075)

    status = health.snapshot()
    assert status["samples"] == 40
    assert status["tick_ms"]["p50"] >= 0.0
    assert status["tick_ms"]["p95"] >= status["tick_ms"]["p50"]
    assert status["tick_ms"]["p99"] >= status["tick_ms"]["p95"]
    for field in ACTIVATION_FIELDS:
        assert len(status["field_histograms"][field]) == 10
        assert sum(status["field_histograms"][field]) == 40


def _deterministic_motor_replay():
    authority = NervousAutonomousAuthorityV103(
        SharedActivationBus(clock=lambda: 0.0),
        config=NervousSystemConfigV103(mode="active"),
        clock=lambda: 0.0,
    )
    target = _target()
    rows = []
    for now in (0.0, 0.2, 0.55, 0.9, 1.3):
        decision = authority.motor.evaluate(_snapshot(), targets=(target,), now=now)
        rows.append((decision.channel, decision.committed, round(decision.activation, 6), decision.semantic_source_id))
        if decision.committed:
            authority.motor.note_public_commit(decision.semantic_source_id, now=now)
    return rows


def test_fixed_inputs_and_clock_replay_identically():
    assert _deterministic_motor_replay() == _deterministic_motor_replay()


def test_property_fuzz_keeps_motor_bounded_and_hard_boundary_absolute():
    rng = random.Random(103)
    authority = NervousAutonomousAuthorityV103(
        SharedActivationBus(clock=lambda: 0.0),
        config=NervousSystemConfigV103(mode="active"),
        clock=lambda: 0.0,
    )
    target = _target()

    for i in range(250):
        values = {name: rng.uniform(-0.4, 1.4) for name in ACTIVATION_FIELDS}
        snap = ActivationSnapshot(revision=i, timestamp=i * 0.1, values=values)
        decision = authority.motor.evaluate(
            snap,
            targets=(target,),
            hard_boundary=True,
            now=i * 0.1,
        )
        assert decision.channel == "silence"
        assert decision.committed is True
        assert 0.0 <= decision.activation <= 1.0
        assert all(0.0 <= value <= 1.0 for value in decision.drives.values())


def test_stronger_proposition_is_not_starved_by_shorter_micro_dwell_at_20hz():
    authority = NervousAutonomousAuthorityV103(
        SharedActivationBus(clock=lambda: 0.0),
        config=NervousSystemConfigV103(mode="active"),
        clock=lambda: 0.0,
    )
    target = _target()
    decisions = []
    for tick in range(13):
        now = tick * 0.05
        decisions.append(authority.motor.evaluate(_snapshot(), targets=(target,), now=now))

    committed = [row.channel for row in decisions if row.committed]
    assert committed
    assert committed[0] == "proposition"


def test_unified_motor_reaches_vocalization_and_micro_expression_channels():
    # V99 roadmap contract: unified motor phải thật sự có các channel local,
    # không chỉ khai báo enum rồi mọi thứ rơi về body/proposition.
    vocal_snapshot = _snapshot(
        curiosity=0.05, boredom=0.90, social_need=0.90, task_focus=0.0,
        concern_salience=0.05, uncertainty=0.0, expressive_pressure=1.0,
        inhibition=0.0, fatigue=0.0,
    )
    vocal = NervousAutonomousAuthorityV103(
        SharedActivationBus(clock=lambda: 0.0),
        config=NervousSystemConfigV103(mode="active"),
        clock=lambda: 0.0,
    )
    vocal.motor.evaluate(vocal_snapshot, now=0.0)
    vocal_commit = vocal.motor.evaluate(vocal_snapshot, now=0.60)
    assert vocal_commit.channel == "vocalization"
    assert vocal_commit.local_text == "haiz"

    micro_snapshot = _snapshot(
        curiosity=1.0, boredom=0.05, social_need=0.80, task_focus=0.0,
        concern_salience=1.0, uncertainty=0.0, expressive_pressure=0.25,
        inhibition=0.0, fatigue=0.0,
    )
    micro = NervousAutonomousAuthorityV103(
        SharedActivationBus(clock=lambda: 0.0),
        config=NervousSystemConfigV103(mode="active"),
        clock=lambda: 0.0,
    )
    micro.motor.evaluate(micro_snapshot, now=0.0)
    micro_commit = micro.motor.evaluate(micro_snapshot, now=0.60)
    assert micro_commit.channel == "micro_expression"
    assert micro_commit.local_text == "hừm"


