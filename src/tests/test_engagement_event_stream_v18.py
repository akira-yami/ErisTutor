from __future__ import annotations

from dataclasses import replace

from runtime.engagements import EngagementRuntime
from runtime.engagements.contracts import LifecycleStatus
from runtime.policy_settings import EngagementSettings


def _runtime(*, max_events: int = 24, **settings) -> EngagementRuntime:
    return EngagementRuntime(
        clock=lambda: 100.0,
        settings=EngagementSettings(max_events=max_events, **settings),
    )


def test_named_consumer_checkpoint_is_persisted_and_monotonic() -> None:
    runtime = _runtime()
    engagement = runtime.observe_user_message("Hãy hỗ trợ code với tôi lâu dài", now=1.0)
    assert engagement is not None
    runtime.emit_domain_event("code.tests_failed", engagement_id=engagement.engagement_id, now=2.0)

    batch = runtime.read_events("diagnostics")
    assert batch.events
    assert batch.next_cursor == runtime.registry.event_cursor
    assert runtime.acknowledge_events("diagnostics", batch.next_cursor) == batch.next_cursor
    assert runtime.acknowledge_events("diagnostics", 0) == batch.next_cursor

    restored = EngagementRuntime(initial_state=runtime.registry.snapshot(), clock=lambda: 100.0)
    assert restored.registry.consumer_cursor("diagnostics") == batch.next_cursor
    assert restored.read_events("diagnostics").events == ()


def test_event_stream_reports_retention_gap_instead_of_hiding_it() -> None:
    runtime = _runtime(max_events=24)
    engagement = runtime.observe_user_message("Hãy hỗ trợ code với tôi lâu dài", now=1.0)
    assert engagement is not None
    for index in range(40):
        runtime.emit_domain_event(
            "code.file_modified",
            engagement_id=engagement.engagement_id,
            payload={"index": index},
            now=2.0 + index,
        )

    batch = runtime.read_events("slow-consumer", cursor=0, limit=10)
    assert batch.has_gap is True
    assert batch.lost_event_count > 0
    assert batch.events[0].log_seq == batch.earliest_available_cursor
    assert len(batch.events) == 10


def test_compaction_never_removes_unacknowledged_terminal_delivery() -> None:
    runtime = _runtime(max_terminal_engagements=0, terminal_retention_seconds=0.0)
    engagement = runtime.observe_user_message("Theo dõi build này cho tôi", now=1.0)
    assert engagement is not None
    runtime.emit_domain_event("build.completed", engagement_id=engagement.engagement_id, now=2.0)

    assert runtime.registry.get(engagement.engagement_id) is not None
    assert runtime.registry.compact(now=100.0, max_terminal_engagements=0, terminal_retention_seconds=0.0) == ()

    decision = runtime.interaction_decision(
        now=20.0,
        seconds_since_user=20.0,
        seconds_since_assistant=20.0,
    )
    assert decision.due is True
    runtime.observe_assistant_output("Build xong rồi.", now=21.0)
    removed = runtime.registry.compact(
        now=100.0,
        max_terminal_engagements=0,
        terminal_retention_seconds=0.0,
    )
    assert removed == (engagement.engagement_id,)


def test_compaction_keeps_live_engagements_and_newest_terminal_history() -> None:
    runtime = _runtime(max_terminal_engagements=1, terminal_retention_seconds=9999.0)
    live = runtime.observe_user_message("Hãy hỗ trợ code với tôi lâu dài", now=1.0)
    assert live is not None

    terminal_ids = []
    for index in range(3):
        proposal = runtime.detector.propose(f"Theo dõi build {index} cho tôi")
        assert proposal is not None
        monitor = runtime.registry.create(replace(proposal, semantic_key=f"monitor:{index}"), now=10.0 + index)
        runtime.registry.transition(monitor.engagement_id, LifecycleStatus.COMPLETED, now=20.0 + index)
        runtime.registry.append_event(monitor.engagement_id, "assistant.spoke", now=30.0 + index)
        terminal_ids.append(monitor.engagement_id)

    removed = runtime.registry.compact(
        now=40.0,
        max_terminal_engagements=1,
        terminal_retention_seconds=9999.0,
    )
    assert runtime.registry.get(live.engagement_id) is not None
    assert len(removed) == 2
    assert runtime.registry.get(terminal_ids[-1]) is not None


def test_event_stream_diagnostics_expose_lag_and_window() -> None:
    runtime = _runtime()
    engagement = runtime.observe_user_message("Hãy hỗ trợ code với tôi lâu dài", now=1.0)
    assert engagement is not None
    first = runtime.read_events("panel")
    runtime.acknowledge_events("panel", first.next_cursor)
    runtime.emit_domain_event("code.tests_failed", engagement_id=engagement.engagement_id, now=2.0)

    diagnostics = runtime.diagnostics(now=3.0)
    kernel = diagnostics["kernel"]
    assert kernel["consumer_lag"]["panel"] >= 1
    assert diagnostics["event_stream"]["latest_cursor"] == runtime.registry.event_cursor
    assert diagnostics["event_stream"]["earliest_cursor"] <= runtime.registry.event_cursor


def test_registry_normalizes_legacy_focus_and_reports_no_invariant_violation() -> None:
    runtime = _runtime()
    first = runtime.observe_user_message("Hãy hỗ trợ code với tôi lâu dài", now=1.0)
    assert first is not None
    snapshot = runtime.registry.snapshot()
    duplicate = dict(snapshot["engagements"][0])
    duplicate["engagement_id"] = "legacy_duplicate"
    duplicate["semantic_key"] = "legacy:duplicate"
    duplicate["focus"] = "foreground"
    duplicate["lifecycle"] = "active"
    snapshot["engagements"].append(duplicate)

    restored = EngagementRuntime(initial_state=snapshot, clock=lambda: 100.0)
    assert len([item for item in restored.registry.active() if item.focus.value == "foreground"]) == 1
    assert restored.registry.validate_invariants() == ()


def test_set_focus_cannot_make_terminal_engagement_visible() -> None:
    runtime = _runtime()
    engagement = runtime.observe_user_message("Theo dõi build này cho tôi", now=1.0)
    assert engagement is not None
    completed = runtime.registry.transition(engagement.engagement_id, LifecycleStatus.COMPLETED, now=2.0)
    assert completed is not None
    unchanged = runtime.registry.set_focus(engagement.engagement_id, completed.resume_focus, now=3.0)
    assert unchanged is not None
    assert unchanged.focus.value == "suspended"
    assert runtime.registry.validate_invariants() == ()
