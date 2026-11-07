from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from runtime.audit import AuditEvent, AuditEventKind, ForensicStore, ReplayEngine


def test_forensic_store_is_append_only_and_replays_deterministically(tmp_path: Path) -> None:
    ids = iter(f"audit-{i}" for i in range(10))
    store = ForensicStore(tmp_path / "audit.db", clock=lambda: 10.0, id_factory=lambda: next(ids))
    store.append(AuditEvent("e1", "eris", AuditEventKind.OBSERVATION, "o1", 1.0, {"fact": 1}))
    store.append(AuditEvent("e2", "eris", AuditEventKind.DECISION, "d1", 2.0, {"action": "observe"}, ("o1",)))
    assert store.verify_chain("eris")
    first = ReplayEngine(store).replay("eris")
    second = ReplayEngine(store).replay("eris")
    assert first.projection_hash == second.projection_hash
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        store._connection.execute("DELETE FROM audit_events WHERE event_id = 'e1'")


def test_causal_trace_follows_refs(tmp_path: Path) -> None:
    store = ForensicStore(tmp_path / "audit.db")
    store.append(AuditEvent("e1", "eris", "observation", "o1", 1.0, {"fact": 1}))
    store.append(AuditEvent("e2", "eris", "decision", "d1", 2.0, {"action": "observe"}, ("o1",)))
    trace = store.causal_trace("d1")
    assert {event.entity_id for event in trace.events} == {"d1", "o1"}
    assert trace.chain_valid
