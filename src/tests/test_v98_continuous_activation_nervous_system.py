# -*- coding: utf-8 -*-
"""V98 regression: shared activation phải là dynamics thật, không phải score mới."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from runtime.activation_nervous_system_v98 import (
    ACTIVATION_FIELDS,
    ActivationSnapshot,
    ContinuousDynamicsRuntime,
    ExpressionMotorV98,
    HostActivationBridgeV98,
    ModulationEvent,
    SharedActivationBus,
)


def _replay() -> dict[str, float]:
    bus = SharedActivationBus(clock=lambda: 0.0)
    runtime = ContinuousDynamicsRuntime(bus, tick_interval_sec=0.075, clock=lambda: 0.0)
    bus.submit(ModulationEvent(
        source="test_uncertainty",
        targets={"uncertainty": 0.92, "concern_salience": 0.68},
        impulses={"social_need": 0.18},
        strength=0.80,
        half_life_seconds=1.2,
        ttl_seconds=4.0,
        created_at=0.0,
        event_id="fixed-event",
    ))
    for i in range(1, 81):
        runtime.tick_once(now=i * 0.075, dt=0.075)
    return dict(bus.snapshot().values)


def test_bus_has_nine_control_fields_and_only_event_ingress() -> None:
    bus = SharedActivationBus(clock=lambda: 10.0)
    before = bus.snapshot()
    assert tuple(before.values.keys()) == ACTIVATION_FIELDS

    # submit chỉ xếp modulation vào bus; subsystem không được set thẳng state.
    assert bus.submit(ModulationEvent(source="unit", targets={"curiosity": 1.0})) is True
    after_submit = bus.snapshot()
    assert after_submit.values["curiosity"] == before.values["curiosity"]

    with pytest.raises(TypeError):
        bus.submit({"source": "khong_duoc_set_thang"})  # type: ignore[arg-type]


def test_activation_has_inertia_recurrence_and_does_not_reset_per_cycle() -> None:
    bus = SharedActivationBus(clock=lambda: 0.0)
    runtime = ContinuousDynamicsRuntime(bus, tick_interval_sec=0.075, clock=lambda: 0.0)
    baseline = bus.snapshot().value("curiosity")

    # Không target curiosity trực tiếp. Uncertainty/concern phải recurrent-couple
    # sang curiosity, chứng minh đây không chỉ là bảng target độc lập.
    bus.submit(ModulationEvent(
        source="novel_evidence",
        targets={"uncertainty": 0.95, "concern_salience": 0.78},
        strength=0.90,
        half_life_seconds=1.0,
        ttl_seconds=2.5,
        created_at=0.0,
        event_id="novel-1",
    ))
    for i in range(1, 31):
        runtime.tick_once(now=i * 0.075, dt=0.075)
    excited = bus.snapshot().value("curiosity")
    assert excited > baseline

    # Event hết hạn, state nguội dần nhưng không reset tức thì về baseline.
    runtime.tick_once(now=3.0, dt=0.075)
    still_alive = bus.snapshot().value("curiosity")
    assert still_alive > baseline
    assert still_alive < 1.0


def test_replay_is_deterministic_without_random_intention_roulette() -> None:
    first = _replay()
    second = _replay()
    assert first == second


def test_runtime_tick_contract_is_50_to_100_ms() -> None:
    bus = SharedActivationBus(clock=lambda: 0.0)
    too_fast = ContinuousDynamicsRuntime(bus, tick_interval_sec=0.001, clock=lambda: 0.0)
    too_slow = ContinuousDynamicsRuntime(bus, tick_interval_sec=2.0, clock=lambda: 0.0)
    assert too_fast.tick_interval_sec == pytest.approx(0.05)
    assert too_slow.tick_interval_sec == pytest.approx(0.10)


def test_expression_motor_is_parallel_and_shadow_cannot_call_public_callback() -> None:
    called = []
    motor = ExpressionMotorV98(shadow=True, callback=called.append, clock=lambda: 100.0)
    snapshot = ActivationSnapshot(
        revision=7,
        timestamp=100.0,
        tick_count=20,
        active_modulators=2,
        values={
            "curiosity": 0.96,
            "boredom": 0.90,
            "social_need": 0.96,
            "task_focus": 0.04,
            "concern_salience": 0.93,
            "uncertainty": 0.82,
            "expressive_pressure": 0.99,
            "inhibition": 0.02,
            "fatigue": 0.02,
        },
    )
    intents = motor.evaluate(snapshot, now=100.0)
    channels = {intent.channel for intent in intents}

    # Không "chọn winner": nhiều actuator có thể cùng được kích hoạt.
    assert "body" in channels
    assert "micro_expression" in channels
    assert "vocalization" in channels
    assert called == []
    vocal = next(intent for intent in intents if intent.channel == "vocalization")
    assert vocal.action_hint == "speech_impulse_without_semantic_content"
    assert vocal.shadow is True


def test_host_bridge_projects_legacy_state_only_as_modulation_event() -> None:
    class FakeInternal:
        def to_dict(self):
            return {
                "curiosity": 0.72,
                "boredom": 0.20,
                "social_pressure": 0.58,
                "fatigue": 0.14,
                "restraint_pressure": 0.31,
                "silence_pressure": 0.44,
            }

    class FakeConcern:
        def snapshot(self, **_kwargs):
            return ({"activation": 0.81},)

    host = SimpleNamespace(
        _get_internal_state=lambda: FakeInternal(),
        rolling_internal_state=SimpleNamespace(to_dict=lambda: {}),
        last_observer_frame=SimpleNamespace(
            possible_error=0.33,
            focus_kind="code",
            activity_intent="coding",
            salience=0.80,
        ),
        active_concern_runtime=FakeConcern(),
        persistent_individual_runtime=None,
        _chat_processing=False,
        interaction_clock=None,
        _temporal_last_user_turn_at=0.0,
        _temporal_last_assistant_turn_at=0.0,
    )
    bridge = HostActivationBridgeV98(host, clock=lambda: 50.0)
    (event,) = bridge.collect(now=50.0)

    assert isinstance(event, ModulationEvent)
    assert event.source == "host_projection_v98"
    assert event.targets["curiosity"] == pytest.approx(0.72)
    assert event.targets["task_focus"] >= 0.70
    assert event.targets["concern_salience"] >= 0.81
    assert not event.impulses
