# -*- coding: utf-8 -*-
"""Deterministic forensic replay."""
from __future__ import annotations

import hashlib
from typing import Any, Callable, Mapping

from memory.subject.repository import canonical_json
from .contracts import AuditEvent, ReplayResult
from .forensic_store import ForensicStore


Reducer = Callable[[dict[str, Any], AuditEvent], dict[str, Any]]


def default_reducer(state: dict[str, Any], event: AuditEvent) -> dict[str, Any]:
    result = dict(state)
    kind = event.kind.value if hasattr(event.kind, "value") else str(event.kind)
    bucket = list(result.get(kind, []))
    bucket.append({"entity_id": event.entity_id, "payload": dict(event.payload), "event_id": event.event_id})
    result[kind] = bucket
    result["last_event_id"] = event.event_id
    return result


class ReplayEngine:
    def __init__(self, store: ForensicStore, reducer: Reducer = default_reducer) -> None:
        self.store = store
        self.reducer = reducer

    def replay(self, subject_id: str, *, initial: Mapping[str, Any] | None = None) -> ReplayResult:
        state = dict(initial or {})
        events = self.store.query(subject_id=subject_id, limit=100000)
        for event in events:
            state = self.reducer(state, event)
        projection_hash = hashlib.sha256(canonical_json(state).encode("utf-8")).hexdigest()
        return ReplayResult(
            subject_id=subject_id,
            event_count=len(events),
            projection=state,
            projection_hash=projection_hash,
            chain_valid=self.store.verify_chain(subject_id),
        )
