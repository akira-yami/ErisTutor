from __future__ import annotations

from types import SimpleNamespace

from runtime.engagements import EngagementRuntime, InteractionAction


def _activity(*, surface: str, app: str, session: str, event_key: str):
    event = SimpleNamespace(evidence_refs=(f"screen:{event_key}",))
    return SimpleNamespace(
        new_event_keys=(event_key,),
        phase="editing",
        activity_kind="coding",
        recent_events=(event,),
        surface_key=surface,
        app=app,
        window_title="service.py",
        session_id=session,
    )


def test_activity_evidence_routes_to_matching_engagement_only() -> None:
    runtime = EngagementRuntime()
    monitor = runtime.observe_user_message(
        "Theo dõi build cho đến khi xong.",
        now=10.0,
        binding={"resource_id": "build:42"},
    )
    coding = runtime.observe_user_message(
        "Hãy hỗ trợ code với tôi trong lúc sửa lỗi này.",
        now=20.0,
        binding={"surface_key": "editor:a", "app": "code"},
    )
    assert monitor is not None and coding is not None

    runtime.observe_activity(
        _activity(surface="editor:a", app="code", session="activity-1", event_key="delta:1"),
        now=30.0,
    )

    events = runtime.registry.events_since(0)
    activity_targets = [event.engagement_id for event in events if event.kind == "activity.changed"]
    assert activity_targets == [coding.engagement_id]
    assert runtime.registry.get(monitor.engagement_id).last_event_kind != "activity.changed"


def test_bound_domain_event_reaches_background_monitor() -> None:
    runtime = EngagementRuntime()
    monitor = runtime.observe_user_message(
        "Theo dõi deployment cho đến khi xong.",
        now=10.0,
        binding={"resource_id": "deploy:prod"},
    )
    coding = runtime.observe_user_message(
        "Hãy hỗ trợ code với tôi trong lúc sửa lỗi này.",
        now=20.0,
        binding={"workspace_id": "repo:eris"},
    )
    assert monitor is not None and coding is not None
    runtime.observe_assistant_output("Tôi đang bám phiên code.", now=21.0)

    routed = runtime.emit_evidence_event(
        "deployment.completed",
        binding={"resource_id": "deploy:prod"},
        payload={"detail": "production deployment succeeded", "result": "success"},
        now=40.0,
    )
    decision = runtime.interaction_decision(
        now=50.0,
        seconds_since_user=30.0,
        seconds_since_assistant=29.0,
    )

    assert [item.engagement_id for item in routed] == [monitor.engagement_id]
    assert runtime.registry.get(coding.engagement_id).last_event_kind == "assistant.spoke"
    assert decision.engagement_id == monitor.engagement_id
    assert decision.action == InteractionAction.NOTIFY


def test_evidence_binding_survives_persistence_round_trip() -> None:
    runtime = EngagementRuntime()
    engagement = runtime.observe_user_message(
        "Hãy hỗ trợ code với tôi trong cả phiên.",
        now=10.0,
        binding={
            "workspace_id": "repo:eris",
            "resource_id": "file:runtime/coordinator.py",
            "tags": ["python", "runtime"],
        },
    )
    assert engagement is not None

    restored = EngagementRuntime(initial_state=runtime.registry.snapshot())
    rebound = restored.registry.get(engagement.engagement_id)

    assert rebound is not None
    assert rebound.evidence_binding.workspace_id == "repo:eris"
    assert rebound.evidence_binding.resource_id == "file:runtime/coordinator.py"
    assert rebound.evidence_binding.tags == ("python", "runtime")
