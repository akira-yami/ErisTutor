from __future__ import annotations

from dataclasses import replace

from runtime.engagements import (
    ContinuityPolicy,
    EngagementProfile,
    EngagementRuntime,
    FocusMode,
    InactivityBehavior,
    InitiativeMode,
    InteractionAction,
    InteractionContract,
    LifecycleStatus,
)


def test_global_event_cursor_is_monotonic_across_engagements() -> None:
    runtime = EngagementRuntime()
    monitor = runtime.observe_user_message(
        "Theo dõi deployment cho đến khi xong.",
        now=10.0,
        binding={"resource_id": "deploy:prod"},
    )
    chess = runtime.observe_user_message(
        "Hãy hỗ trợ chơi cờ vua với tôi trong cả ván.",
        now=20.0,
    )
    assert monitor is not None and chess is not None

    events = runtime.registry.events_since(0)
    cursors = [event.log_seq for event in events]
    assert cursors == sorted(cursors)
    assert len(cursors) == len(set(cursors))

    cursor = runtime.registry.event_cursor
    runtime.emit_domain_event(
        "chess.move_played",
        engagement_id=chess.engagement_id,
        payload={"move_number": 2, "detail": "e4 e5"},
        now=30.0,
    )
    delta = runtime.registry.events_since(cursor)
    assert delta
    assert all(event.log_seq > cursor for event in delta)
    assert {event.engagement_id for event in delta} == {chess.engagement_id}


def test_schema_v2_event_log_migrates_to_global_cursor() -> None:
    runtime = EngagementRuntime()
    runtime.observe_user_message(
        "Hãy hỗ trợ code với tôi trong cả phiên.",
        now=10.0,
    )
    snapshot = runtime.registry.snapshot()
    snapshot["schema_version"] = 2
    snapshot.pop("event_log_seq", None)
    for event in snapshot["events"]:
        event.pop("log_seq", None)

    restored = EngagementRuntime(initial_state=snapshot)
    events = restored.registry.events_since(0)

    assert events
    assert [event.log_seq for event in events] == list(range(1, len(events) + 1))
    assert restored.registry.event_cursor == len(events)


def test_monitor_terminal_state_is_delivered_once_then_acknowledged() -> None:
    runtime = EngagementRuntime()
    monitor = runtime.observe_user_message(
        "Theo dõi deployment cho đến khi xong.",
        now=10.0,
        binding={"resource_id": "deploy:prod"},
    )
    assert monitor is not None
    runtime.observe_assistant_output("Tôi sẽ theo dõi.", now=11.0)

    completed = runtime.emit_domain_event(
        "deployment.completed",
        engagement_id=monitor.engagement_id,
        payload={"status": "success", "detail": "production is live"},
        now=30.0,
    )
    assert completed is not None
    assert completed.lifecycle == LifecycleStatus.COMPLETED
    assert completed.focus == FocusMode.SUSPENDED

    decision = runtime.interaction_decision(
        now=40.0,
        seconds_since_user=30.0,
        seconds_since_assistant=29.0,
    )
    assert decision.action == InteractionAction.NOTIFY
    assert decision.engagement_id == monitor.engagement_id
    assert decision.required is True

    runtime.observe_assistant_output("Deployment đã hoàn tất.", now=41.0)
    assert runtime.registry.pending_terminal() == ()
    after_ack = runtime.interaction_decision(
        now=60.0,
        seconds_since_user=50.0,
        seconds_since_assistant=19.0,
    )
    assert after_ack.due is False


def test_targeted_pause_and_resume_preserves_background_focus() -> None:
    runtime = EngagementRuntime()
    monitor = runtime.observe_user_message(
        "Theo dõi deployment cho đến khi xong.",
        now=10.0,
    )
    chess = runtime.observe_user_message(
        "Hãy hỗ trợ chơi cờ vua với tôi trong cả ván.",
        now=20.0,
    )
    assert monitor is not None and chess is not None
    assert monitor.focus == FocusMode.BACKGROUND
    assert chess.focus == FocusMode.FOREGROUND

    paused = runtime.observe_user_message("Tạm dừng theo dõi deployment.", now=30.0)
    assert paused is not None
    assert paused.engagement_id == monitor.engagement_id
    assert paused.lifecycle == LifecycleStatus.PAUSED
    assert runtime.registry.foreground().engagement_id == chess.engagement_id

    resumed = runtime.observe_user_message("Tiếp tục theo dõi deployment.", now=40.0)
    assert resumed is not None
    assert resumed.engagement_id == monitor.engagement_id
    assert resumed.lifecycle == LifecycleStatus.ACTIVE
    assert resumed.focus == FocusMode.BACKGROUND
    assert runtime.registry.foreground().engagement_id == chess.engagement_id


def test_completed_engagement_is_not_implicitly_reopened_by_same_request() -> None:
    runtime = EngagementRuntime()
    first = runtime.observe_user_message(
        "Hãy hỗ trợ code với tôi trong cả phiên.",
        now=10.0,
    )
    assert first is not None
    runtime.emit_domain_event(
        "coding.session.completed",
        engagement_id=first.engagement_id,
        now=20.0,
    )

    second = runtime.observe_user_message(
        "Hãy hỗ trợ code với tôi trong cả phiên.",
        now=30.0,
    )
    assert second is not None
    assert second.engagement_id != first.engagement_id


def test_profile_can_explicitly_reopen_recent_completed_engagement() -> None:
    runtime = EngagementRuntime()
    profile = EngagementProfile(
        profile_id="music_practice",
        adapter_id="generic",
        kind="music_practice",
        phrases=("luyện đàn", "luyen dan"),
        contract=InteractionContract(
            role="music_coach",
            initiative=InitiativeMode.GUIDED,
        ),
        continuity=ContinuityPolicy(
            reopen_completed=True,
            resume_window_seconds=600.0,
        ),
        priority=72,
    )
    runtime.register_profile(profile)
    first = runtime.observe_user_message("Hãy luyện đàn với tôi cả buổi.", now=10.0)
    assert first is not None
    runtime.registry.transition(first.engagement_id, LifecycleStatus.COMPLETED, now=20.0)

    reopened = runtime.observe_user_message("Hãy luyện đàn với tôi cả buổi.", now=100.0)
    assert reopened is not None
    assert reopened.engagement_id == first.engagement_id
    assert reopened.lifecycle == LifecycleStatus.ACTIVE


def test_inactivity_policy_pauses_foreground_but_monitor_can_keep_running() -> None:
    runtime = EngagementRuntime()
    chess = runtime.observe_user_message(
        "Hãy hỗ trợ chơi cờ vua với tôi trong cả ván.",
        now=10.0,
    )
    monitor = runtime.observe_user_message(
        "Theo dõi deployment cho đến khi xong.",
        now=20.0,
    )
    assert chess is not None and monitor is not None

    chess_state = runtime.registry.get(chess.engagement_id)
    assert chess_state is not None
    runtime.registry._engagements[chess.engagement_id] = replace(
        chess_state,
        continuity=replace(
            chess_state.continuity,
            inactivity_timeout_seconds=120.0,
            inactivity_behavior=InactivityBehavior.PAUSE,
        ),
    )

    transitioned = runtime.registry.reconcile_inactivity(now=200.0)
    assert {item.engagement_id for item in transitioned} == {chess.engagement_id}
    assert runtime.registry.get(chess.engagement_id).lifecycle == LifecycleStatus.PAUSED
    assert runtime.registry.get(monitor.engagement_id).lifecycle == LifecycleStatus.ACTIVE


def test_domain_waiting_and_resume_events_drive_lifecycle() -> None:
    runtime = EngagementRuntime()
    study = runtime.observe_user_message(
        "Hãy hướng dẫn tôi học từng bước trong cả buổi.",
        now=10.0,
    )
    assert study is not None

    waiting = runtime.emit_domain_event(
        "step.blocked",
        engagement_id=study.engagement_id,
        payload={"detail": "waiting for user answer"},
        now=20.0,
    )
    assert waiting is not None
    assert waiting.lifecycle == LifecycleStatus.WAITING

    resumed = runtime.emit_domain_event(
        "task.resumed",
        engagement_id=study.engagement_id,
        now=30.0,
    )
    assert resumed is not None
    assert resumed.lifecycle == LifecycleStatus.ACTIVE


def test_registry_diagnostics_exposes_lifecycle_and_cursor() -> None:
    runtime = EngagementRuntime()
    engagement = runtime.observe_user_message(
        "Hãy hỗ trợ chơi cờ vua với tôi trong cả ván.",
        now=10.0,
    )
    assert engagement is not None
    runtime.registry.transition(engagement.engagement_id, LifecycleStatus.PAUSED, now=20.0)

    diagnostics = runtime.registry.diagnostics(now=30.0)
    panel = runtime.panel_view(now=30.0)

    assert diagnostics["event_cursor"] == runtime.registry.event_cursor
    assert diagnostics["lifecycle_counts"]["paused"] == 1
    assert panel["diagnostics"]["engagement_count"] == 1
    assert panel["resumable"][0]["engagement_id"] == engagement.engagement_id
