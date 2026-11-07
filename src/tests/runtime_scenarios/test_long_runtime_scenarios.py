from __future__ import annotations

from runtime.engagements import InteractionAction

from .harness import RuntimeScenarioHarness


def test_long_engagement_survives_fifteen_minutes_and_restart() -> None:
    harness = RuntimeScenarioHarness()
    harness.provider.replies.append("Được, tôi sẽ bám cả phiên code này.")
    harness.user("Hãy hỗ trợ code với tôi trong lúc sửa lỗi này.", surface_key="editor:workspace-a")

    harness.advance(9 * 60)
    harness.domain_event(
        "domain.progress",
        payload={"detail": "tests failed", "file": "service.py", "test_status": "failed"},
    )
    before_restart = harness.runtime.planner_context(now=harness.clock.now())

    harness.restart()
    harness.advance(6 * 60)
    after_restart = harness.runtime.planner_context(now=harness.clock.now())
    decision = harness.decision()

    assert before_restart["foreground"]["kind"] == "coding"
    assert "service.py" in before_restart["foreground"]["current_state_summary"]
    assert after_restart["foreground"]["objective"].startswith("Hãy hỗ trợ code")
    assert after_restart["foreground"]["age_seconds"] == 15 * 60
    assert decision.action == InteractionAction.SPEAK
    assert decision.anchor_kind == "engagement:coding"


def test_background_monitor_does_not_steal_attention_until_state_changes() -> None:
    harness = RuntimeScenarioHarness()
    harness.provider.replies.extend(["Tôi sẽ theo dõi build.", "Đưa ván cờ đây."])
    monitor = harness.runtime.observe_user_message(
        "Theo dõi build cho đến khi xong và báo tôi khi trạng thái đổi.",
        now=harness.clock.now(),
    )
    assert monitor is not None
    harness.runtime.observe_assistant_output("Tôi sẽ theo dõi build.", now=harness.clock.now())
    harness.last_assistant_at = harness.clock.now()

    harness.advance(10.0)
    chess = harness.runtime.observe_user_message(
        "Hãy hỗ trợ chơi cờ vua với tôi trong cả ván.",
        now=harness.clock.now(),
    )
    assert chess is not None
    harness.runtime.observe_assistant_output("Đưa ván cờ đây.", now=harness.clock.now())
    harness.last_user_at = harness.clock.now()
    harness.last_assistant_at = harness.clock.now()

    harness.advance(10 * 60)
    quiet_decision = harness.decision()
    assert quiet_decision.engagement_kind == "chess"
    assert quiet_decision.action == InteractionAction.SPEAK

    harness.speak("Nước vừa rồi không đến mức phạm tội, chỉ hơi đáng ngờ.")
    harness.domain_event(
        "domain.progress",
        engagement_id=monitor.engagement_id,
        payload={"detail": "build succeeded", "result": "success"},
    )
    harness.advance(5.0)
    changed_decision = harness.decision()

    assert changed_decision.engagement_id == monitor.engagement_id
    assert changed_decision.action == InteractionAction.NOTIFY
    assert changed_decision.reason == "engagement_event:domain.progress"


def test_persistence_failure_is_visible_and_later_recovers() -> None:
    harness = RuntimeScenarioHarness()
    harness.runtime.observe_user_message(
        "Hãy hướng dẫn tôi học từng bước trong cả buổi.",
        now=harness.clock.now(),
    )

    harness.persistence.fail_next = OSError("read-only filesystem")
    harness.advance(1.0)
    harness.runtime.emit_domain_event(
        "task.progress",
        payload={"detail": "completed first section", "step": 2},
        now=harness.clock.now(),
    )

    degraded = harness.health.get("engagement.persistence")
    assert degraded.status.value == "degraded"
    assert degraded.failure_kind.value == "persistence_failure"

    harness.advance(1.0)
    harness.runtime.emit_domain_event(
        "task.progress",
        payload={"detail": "completed second section", "step": 3},
        now=harness.clock.now(),
    )

    recovered = harness.health.get("engagement.persistence")
    assert recovered.status.value == "healthy"
    assert recovered.total_failures == 1
    assert harness.persistence.snapshots


def test_shutdown_flush_is_deterministic() -> None:
    harness = RuntimeScenarioHarness()
    harness.runtime.observe_user_message(
        "Theo dõi deployment cho đến khi xong.",
        now=harness.clock.now(),
    )

    harness.shutdown()

    assert harness.closed is True
    assert harness.persistence.latest["engagements"]
    assert harness.health.get("scenario.runtime").status.value == "stopped"
    assert harness.health.get("scenario.persistence").status.value == "healthy"
