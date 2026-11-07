# -*- coding: utf-8 -*-
"""V99 regression: activation phải bind vào object có provenance, không roulette topic."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from cortex.observer_contracts import ObserverFrame
from runtime.activation_nervous_system_v98 import SharedActivationBus
from runtime.motivational_focus_v99 import (
    FocusObject,
    HostFocusObjectBridgeV99,
    MotivationalFocusRuntimeV99,
)


def _bus(**overrides):
    values = {
        "curiosity": 0.78,
        "boredom": 0.22,
        "social_need": 0.66,
        "task_focus": 0.44,
        "concern_salience": 0.58,
        "uncertainty": 0.64,
        "expressive_pressure": 0.30,
        "inhibition": 0.24,
        "fatigue": 0.12,
    }
    values.update(overrides)
    return SharedActivationBus(initial_values=values, clock=lambda: 0.0)


def test_multiple_objects_bind_in_parallel_without_winner() -> None:
    bus = _bus()
    objects = (
        FocusObject(
            object_id="concern:1",
            kind="active_concern",
            label="Một lỗi animation chưa hiểu rõ",
            motive_kind="develop_concern",
            source="test",
            salience=0.82,
            evidence_refs=("concern:1",),
            created_at=1.0,
        ),
        FocusObject(
            object_id="user-model:coverage-gap",
            kind="epistemic_gap",
            label="Mô hình người dùng còn mỏng",
            motive_kind="understand_user",
            source="test",
            salience=0.72,
            evidence_refs=("user-model:thickness:0.2",),
            created_at=1.0,
        ),
    )
    runtime = MotivationalFocusRuntimeV99(
        bus,
        source_provider=lambda **_kwargs: objects,
        clock=lambda: 1.0,
    )
    runtime.tick_once(now=1.0, dt=0.4)
    snap = runtime.snapshot()

    assert snap["selection_mode"] == "parallel_no_winner"
    assert snap["selected_object_id"] is None
    assert {row["object_id"] for row in snap["focuses"]} == {
        "concern:1", "user-model:coverage-gap"
    }
    assert all(row["private_activation"] > 0.0 for row in snap["focuses"])


def test_same_activation_resonates_differently_by_motive_not_randomly() -> None:
    # Curiosity/social/uncertainty cao, task focus thấp -> understand_user phải
    # cộng hưởng mạnh hơn support_active_work với cùng feature object.
    bus = _bus(curiosity=0.95, social_need=0.92, uncertainty=0.90, task_focus=0.06, concern_salience=0.20)
    rows = (
        FocusObject(
            object_id="a",
            kind="test",
            label="A",
            motive_kind="understand_user",
            source="test",
            salience=0.80,
            novelty=0.50,
            uncertainty=0.60,
            actionability=0.50,
            user_relevance=0.80,
            evidence_refs=("e:a",),
        ),
        FocusObject(
            object_id="b",
            kind="test",
            label="B",
            motive_kind="support_active_work",
            source="test",
            salience=0.80,
            novelty=0.50,
            uncertainty=0.60,
            actionability=0.50,
            user_relevance=0.80,
            evidence_refs=("e:b",),
        ),
    )
    runtime = MotivationalFocusRuntimeV99(bus, source_provider=lambda **_: rows, clock=lambda: 2.0)
    runtime.tick_once(now=2.0, dt=0.4)
    by_id = {row["object_id"]: row for row in runtime.snapshot()["focuses"]}
    assert by_id["a"]["private_activation"] > by_id["b"]["private_activation"]

    # Replay cùng inputs phải ra y hệt; không có RNG chọn motive.
    runtime2 = MotivationalFocusRuntimeV99(bus, source_provider=lambda **_: rows, clock=lambda: 2.0)
    runtime2.tick_once(now=2.0, dt=0.4)
    by_id2 = {row["object_id"]: row for row in runtime2.snapshot()["focuses"]}
    assert by_id == by_id2


def test_private_interest_can_stay_high_while_surface_readiness_is_low() -> None:
    bus = _bus(
        curiosity=0.90,
        concern_salience=0.88,
        expressive_pressure=0.18,
        inhibition=0.96,
        task_focus=0.82,
    )
    obj = FocusObject(
        object_id="concern:quiet",
        kind="active_concern",
        label="Một concern đáng tiếp tục nghĩ riêng",
        motive_kind="develop_concern",
        source="test",
        salience=0.94,
        novelty=0.70,
        uncertainty=0.65,
        evidence_refs=("concern:quiet",),
    )
    runtime = MotivationalFocusRuntimeV99(bus, source_provider=lambda **_: (obj,), clock=lambda: 3.0)
    for i in range(8):
        runtime.tick_once(now=3.0 + i * 0.4, dt=0.4)
    row = runtime.snapshot()["focuses"][0]
    assert row["private_activation"] > 0.45
    assert row["surface_readiness"] < row["private_activation"] * 0.20


def test_missing_evidence_releases_focus_instead_of_resetting_or_recharging() -> None:
    bus = _bus()
    active = {"on": True}
    obj = FocusObject(
        object_id="future:calendar:1",
        kind="future_event",
        label="Một lịch hẹn có evidence thật",
        motive_kind="protect_user_future",
        source="calendar",
        salience=0.85,
        evidence_refs=("calendar:event:1",),
        ttl_seconds=3.0,
    )
    runtime = MotivationalFocusRuntimeV99(
        bus,
        source_provider=lambda **_: (obj,) if active["on"] else (),
        clock=lambda: 0.0,
    )
    for i in range(6):
        runtime.tick_once(now=1.0 + i * 0.4, dt=0.4)
    before = runtime.snapshot()["focuses"][0]["private_activation"]

    active["on"] = False
    runtime.tick_once(now=3.8, dt=0.4)
    after_one = runtime.snapshot()["focuses"][0]["private_activation"]
    assert 0.0 < after_one < before  # continuity: không reset, cũng không recharge.

    for i in range(100):
        runtime.tick_once(now=4.2 + i * 0.4, dt=0.4)
    assert runtime.snapshot()["focus_count"] == 0


def test_direct_future_object_requires_provenance_and_does_not_mutate_activation() -> None:
    bus = _bus()
    before = bus.snapshot().to_dict()
    runtime = MotivationalFocusRuntimeV99(bus, clock=lambda: 10.0)

    assert runtime.submit(FocusObject(
        object_id="weather:tomorrow",
        kind="future_event",
        label="Ngày mai có mưa",
        motive_kind="protect_user_future",
        source="weather",
        salience=0.8,
        evidence_refs=(),  # Không source -> cấm bind, tránh tự bịa chăm sóc user.
    )) is False

    assert runtime.submit(FocusObject(
        object_id="weather:tomorrow",
        kind="future_event",
        label="Forecast source báo có khả năng mưa ngày mai",
        motive_kind="protect_user_future",
        source="weather",
        salience=0.8,
        evidence_refs=("weather-api:forecast:tomorrow",),
    )) is True
    runtime.tick_once(now=10.0, dt=0.4)

    assert runtime.snapshot()["focus_count"] == 1
    # V99 là downstream read-only; không tạo feedback loop tự nuôi activation.
    assert bus.snapshot().to_dict() == before


def test_host_bridge_binds_concern_goal_user_gap_and_observer_only_from_existing_state() -> None:
    class ConcernRuntime:
        def snapshot(self, **_kwargs):
            return ({
                "concern_id": "c1",
                "subject": "Hyou auto-aim đang lệch ở cự ly gần",
                "state": "simmering",
                "activation": 0.78,
                "attractor_strength": 0.72,
                "persistence": 0.66,
                "novelty": 0.42,
                "tension": 0.58,
                "evidence_refs": ("combat-log:22",),
            },)

    goal = SimpleNamespace(
        state="active",
        owner="user",
        text="Sửa auto-aim Hyou",
        goal_id="g1",
        evidence_refs=("turn:9",),
    )
    reducer = SimpleNamespace(snapshot=lambda: SimpleNamespace(goal_records=(goal,)))
    store = SimpleNamespace(snapshot_for_dossier=lambda limit=24: {
        "model_thickness": 0.18,
        "belief_count": 3,
    })
    system = SimpleNamespace(user_model_store=store)
    daemon = SimpleNamespace(system=system)
    observer = ObserverFrame(
        focus_kind="code",
        activity_intent="debugging",
        salience=0.74,
        novelty=0.55,
        ambiguity=0.20,
        possible_error=0.18,
        user_presence=0.85,
        evidence=["uia:code-window:1"],
    )
    host = SimpleNamespace(
        active_concern_runtime=ConcernRuntime(),
        ai_state_reducer=reducer,
        private_mind_daemon=daemon,
        private_mind_loop=None,
        private_mind_system=None,
        last_observer_frame=observer,
    )

    bridge = HostFocusObjectBridgeV99(host, clock=lambda: 20.0)
    rows = bridge.collect(now=20.0)
    by_kind = {row.kind: row for row in rows}

    assert {"active_concern", "active_goal", "epistemic_gap", "environment_focus"} <= set(by_kind)
    assert by_kind["active_concern"].label == "Hyou auto-aim đang lệch ở cự ly gần"
    assert "combat-log:22" in by_kind["active_concern"].evidence_refs
    assert by_kind["active_goal"].motive_kind == "support_active_work"
    assert by_kind["epistemic_gap"].motive_kind == "understand_user"
    assert "no_private_fact_inference" in by_kind["epistemic_gap"].tags
    assert by_kind["environment_focus"].motive_kind == "inspect_environment"


def test_tick_interval_is_deliberately_slower_than_v98_nervous_tick() -> None:
    bus = _bus()
    too_fast = MotivationalFocusRuntimeV99(bus, tick_interval_sec=0.01)
    too_slow = MotivationalFocusRuntimeV99(bus, tick_interval_sec=5.0)
    assert too_fast.tick_interval_sec == pytest.approx(0.20)
    assert too_slow.tick_interval_sec == pytest.approx(1.00)
