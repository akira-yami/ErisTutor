from __future__ import annotations

from runtime.ai_orchestrated.commentary_cadence import CommentaryCadence
from runtime.engagements import EngagementRuntime, InteractionAction, LifecycleStatus


def test_vietnamese_chess_request_creates_durable_engagement() -> None:
    runtime = EngagementRuntime()

    engagement = runtime.observe_user_message(
        "Eris, hãy hỗ trợ chơi cờ vua với tôi trong cả ván.",
        now=1_000.0,
    )

    assert engagement is not None
    assert engagement.kind == "chess"
    assert engagement.adapter_id == "chess"
    assert engagement.contract.role == "chess_companion"
    assert engagement.contract.preferences["light_mockery_allowed"] is True
    assert engagement.lifecycle == LifecycleStatus.ACTIVE


def test_engagement_remains_anchored_beyond_ten_minutes() -> None:
    runtime = EngagementRuntime()
    runtime.observe_user_message(
        "Hãy đồng hành và hỗ trợ tôi chơi cờ trong cả ván.",
        now=100.0,
    )

    context = runtime.planner_context(now=701.0)
    decision = runtime.interaction_decision(
        now=701.0,
        seconds_since_user=601.0,
        seconds_since_assistant=601.0,
    )

    assert context["active"] is True
    assert context["foreground"]["kind"] == "chess"
    assert context["foreground"]["age_seconds"] == 601.0
    assert decision.action == InteractionAction.SPEAK
    assert decision.due is True
    assert decision.required is True
    assert decision.anchor_kind == "engagement:chess"


def test_registry_supports_foreground_and_background_engagements() -> None:
    runtime = EngagementRuntime()
    monitor = runtime.observe_user_message(
        "Theo dõi build cho đến khi xong và báo tôi khi trạng thái đổi.",
        now=10.0,
    )
    chess = runtime.observe_user_message(
        "Hãy hỗ trợ chơi cờ vua với tôi trong cả ván.",
        now=20.0,
    )

    context = runtime.planner_context(now=30.0)

    assert monitor is not None and monitor.kind == "monitoring"
    assert chess is not None and chess.kind == "chess"
    assert context["foreground"]["kind"] == "chess"
    assert [item["kind"] for item in context["background"]] == ["monitoring"]


def test_monitoring_engagement_does_not_chatter_without_change() -> None:
    runtime = EngagementRuntime()
    runtime.observe_user_message(
        "Theo dõi deployment cho đến khi xong.",
        now=10.0,
    )

    decision = runtime.interaction_decision(
        now=1_010.0,
        seconds_since_user=1_000.0,
        seconds_since_assistant=1_000.0,
    )

    assert decision.action == InteractionAction.WAIT
    assert decision.due is False
    assert decision.reason == "waiting_for_meaningful_event"


def test_domain_event_updates_adapter_projection_and_triggers_policy() -> None:
    runtime = EngagementRuntime()
    engagement = runtime.observe_user_message(
        "Hãy hỗ trợ code với tôi trong lúc sửa lỗi này.",
        now=100.0,
    )
    assert engagement is not None
    runtime.observe_assistant_output("Đưa lỗi đây, đừng bắt tôi đoán bằng thần giao cách cảm.", now=105.0)
    runtime.emit_domain_event(
        "domain.progress",
        payload={"detail": "Tests failed", "file": "service.py", "test_status": "failed"},
        now=130.0,
    )

    context = runtime.planner_context(now=140.0)
    decision = runtime.interaction_decision(
        now=140.0,
        seconds_since_user=40.0,
        seconds_since_assistant=35.0,
    )

    assert "service.py" in context["foreground"]["current_state_summary"]
    assert decision.due is True
    assert decision.reason.startswith("engagement_event:")


def test_lifecycle_control_and_persistence_round_trip() -> None:
    snapshots: list[dict] = []
    runtime = EngagementRuntime(persist=lambda payload: snapshots.append(dict(payload)))
    runtime.observe_user_message(
        "Hãy hướng dẫn tôi học từng bước trong cả buổi.",
        now=1.0,
    )
    paused = runtime.observe_user_message("Tạm dừng.", now=2.0)

    assert paused is not None
    assert paused.lifecycle == LifecycleStatus.PAUSED
    assert snapshots

    restored = EngagementRuntime(initial_state=snapshots[-1])
    active = restored.registry.active()
    assert active == ()
    assert restored.registry.snapshot()["engagements"][0]["lifecycle"] == "paused"


def test_commentary_cadence_uses_engagement_policy_as_anchor() -> None:
    runtime = EngagementRuntime()
    runtime.observe_user_message(
        "Hãy hỗ trợ chơi cờ vua với tôi trong cả ván.",
        now=100.0,
    )
    decision = runtime.interaction_decision(
        now=701.0,
        seconds_since_user=601.0,
        seconds_since_assistant=601.0,
    )

    expectation = CommentaryCadence().evaluate(
        now=701.0,
        last_commentary_at=100.0,
        session_started_at=100.0,
        seconds_since_user=601.0,
        seconds_since_assistant=601.0,
        discourse_open=False,
        perception_delta=False,
        activity_event=False,
        has_public_seed=False,
        telemetry={},
        engagement_decision=decision,
    )

    assert expectation.due is True
    assert expectation.required is True
    assert expectation.engagement_kind == "chess"
    assert expectation.anchor_kind == "engagement:chess"


def test_monitoring_policy_suppresses_generic_companion_timer() -> None:
    runtime = EngagementRuntime()
    runtime.observe_user_message(
        "Theo dõi deployment cho đến khi xong.",
        now=10.0,
    )
    decision = runtime.interaction_decision(
        now=1_010.0,
        seconds_since_user=1_000.0,
        seconds_since_assistant=1_000.0,
    )

    expectation = CommentaryCadence().evaluate(
        now=1_010.0,
        last_commentary_at=10.0,
        session_started_at=10.0,
        seconds_since_user=1_000.0,
        seconds_since_assistant=1_000.0,
        discourse_open=False,
        perception_delta=False,
        activity_event=False,
        has_public_seed=False,
        telemetry={},
        engagement_decision=decision,
    )

    assert expectation.due is False
    assert expectation.reason == "waiting_for_meaningful_event"
