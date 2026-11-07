from __future__ import annotations

import copy
import random

from runtime.engagements import EngagementRuntime
from runtime.engagements.snapshot_journal import EngagementSnapshotJournal


def test_deterministic_snapshot_journal_chaos_recovers_last_known_good_generation():
    rng = random.Random(340034)
    container: dict = {}
    clock = {"now": 100.0}
    journal = EngagementSnapshotJournal(lambda: container, clock=lambda: clock["now"])
    runtime = EngagementRuntime(clock=lambda: clock["now"])
    runtime.observe_user_message("Eris, hãy hỗ trợ code lâu dài với tôi", now=clock["now"])

    committed_revisions: list[int] = []
    for index in range(1, 41):
        clock["now"] += 1.0
        current = runtime.registry.foreground()
        assert current is not None
        runtime.emit_domain_event(
            "code.file_modified",
            engagement_id=current.engagement_id,
            payload={"path": f"module_{index}.py", "delta": rng.randint(1, 50)},
            now=clock["now"],
        )
        snapshot = runtime.registry.snapshot()
        journal.stage(snapshot)
        committed_revisions.append(snapshot["revision"])

        if index % 7 == 0:
            # A torn/corrupt primary must fall back exactly one known-good generation.
            previous = copy.deepcopy(container[journal.BACKUP_KEY])
            container[journal.PRIMARY_KEY]["revision"] += 10_000
            recovered = journal.load(auto_repair=True)
            assert recovered.source == "backup"
            assert recovered.repaired_primary is True
            assert recovered.snapshot["snapshot_digest"] == previous["snapshot_digest"]
            runtime = EngagementRuntime(initial_state=recovered.snapshot, clock=lambda: clock["now"])

    diagnostics = journal.diagnostics()
    assert diagnostics["primary_valid"] is True
    assert diagnostics["backup_valid"] is True
    assert diagnostics["recovery_count"] == 5
    assert committed_revisions == sorted(committed_revisions)


def test_corrupt_journal_metadata_does_not_crash_diagnostics_or_staging():
    container: dict = {}
    journal = EngagementSnapshotJournal(lambda: container)
    runtime = EngagementRuntime()
    runtime.observe_user_message("hãy theo dõi deployment này")
    journal.stage(runtime.registry.snapshot())
    container[journal.META_KEY]["generation"] = "not-an-int"
    container[journal.META_KEY]["recovery_count"] = object()
    container[journal.META_KEY]["last_recovery_at"] = object()

    diagnostics = journal.diagnostics()
    generation = journal.stage(runtime.registry.snapshot())

    assert diagnostics["generation"] == 0
    assert diagnostics["recovery_count"] == 0
    assert diagnostics["last_recovery_at"] == 0.0
    assert generation == 2
