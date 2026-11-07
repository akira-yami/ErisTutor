from __future__ import annotations

from dataclasses import dataclass

from runtime.private_life_daemon import PrivateLifeDaemon, PrivateLifeDaemonConfig
from runtime.subconscious_loop import SubconsciousLoop


class FailingWorker:
    name = "failing_worker"
    run_after_synthesis = False
    audit_blackboard_worker = False

    def run(self, context):
        raise RuntimeError("worker exploded")


class FailingAuditWorker:
    name = "failing_audit"
    run_after_synthesis = False
    audit_blackboard_worker = True

    def audit_blackboard(self, blackboard, snapshot, context):
        raise ValueError("audit exploded")


class FailingCore:
    state = None

    def tick(self, **kwargs):
        raise RuntimeError("private life exploded")


class BrokenLogger:
    def warning(self, message, *args):
        raise RuntimeError("logger unavailable")


def test_subconscious_worker_and_audit_failures_are_diagnostic() -> None:
    loop = SubconsciousLoop(workers=(FailingWorker(), FailingAuditWorker()), logger=BrokenLogger())

    result = loop.tick(run_synthesis=False, run_post_workers=False)

    assert result.submitted_count == 0
    diagnostics = loop.diagnostics()
    assert diagnostics["failure_counts"]["worker:failing_worker"] == 1
    assert diagnostics["failure_counts"]["audit:failing_audit"] == 1
    assert diagnostics["last_failures"]["worker:failing_worker"]["error_type"] == "RuntimeError"


def test_private_life_tick_failure_is_visible_without_crashing() -> None:
    daemon = PrivateLifeDaemon(
        core=FailingCore(),
        config=PrivateLifeDaemonConfig(min_interval_seconds=1.0),
        logger=BrokenLogger(),
    )

    tick = daemon.tick(force=True)

    assert tick is not None
    assert tick.ticked is False
    assert tick.reason == "error:RuntimeError"
    assert daemon.diagnostics()["failure_count"] == 1
    assert daemon.diagnostics()["last_error"]["stage"] == "tick"
