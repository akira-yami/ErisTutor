"""Isolated SQLite shadow registry: observe and compare; NEVER commit actions.

This prototype is intentionally NOT connected to bootstrap, Executive or
ConversationKernel. Consumers must supply a separate shadow SQLite path and
monotonic source state revision; this module does not write the V94 state DB.
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Sequence

from runtime.ai_orchestrated.atomic_state import SQLiteAtomicStateStore
from runtime.autonomy.goal_contracts import (
    ShadowDiff, ShadowGoalObservation, ShadowObservationReport,
)
from runtime.autonomy.goal_projection import as_legacy_record, detect_missing, project_goal


class ShadowGoalRegistry:
    SCHEMA = "eris.autonomy.goal_registry.shadow.v1"

    def __init__(
        self,
        path: str | Path,
        *,
        state_key: str = "autonomy:goal-shadow:v1",
        clock=None,
        max_known: int = 20_000,
    ) -> None:
        self._clock = clock or time.time
        self._store = SQLiteAtomicStateStore(path, clock=self._clock)
        self._state_key = str(state_key)
        self._max_known = max(1, int(max_known))

    def snapshot(self) -> tuple[int, tuple[ShadowGoalObservation, ...]]:
        state = self._store.load(self._state_key)
        if state is None:
            return 0, ()
        records = state.payload.get("goals", {})
        return state.revision, tuple(
            ShadowGoalObservation.from_mapping(raw)
            for _, raw in sorted(records.items())
        )

    def observe_legacy(
        self,
        records: Sequence[Any],
        *,
        source_revision: int,
        observation_id: str,
    ) -> ShadowObservationReport:
        """Persist read-only comparisons of a V94 state snapshot.

        - `source_revision` MUST be the authoritative RuntimeMindState.version.
        - Replayed/out-of-order snapshots are ignored.
        - A bounded legacy projection losing an old goal is NOT a delete.
        - This API cannot adopt, execute, abandon, or surface autonomous goals.
        """
        if source_revision < 0:
            raise ValueError("source_revision must be non-negative")
        if not str(observation_id).strip():
            raise ValueError("observation_id is required")
        prepared = [as_legacy_record(item) for item in records]
        ids = [item["goal_id"] for item in prepared]
        if len(set(ids)) != len(ids):
            raise ValueError("snapshot contains duplicate goal_id")
        now = float(self._clock())
        with self._store.transaction(self._state_key, {
            "schema": self.SCHEMA,
            "last_source_revision": -1,
            "last_observation_id": "",
            "goals": {},
        }) as tx:
            state = dict(tx.payload)
            last_revision = int(state.get("last_source_revision", -1))
            if source_revision <= last_revision:
                reason = (
                    "duplicate" if (source_revision == last_revision and
                                    str(observation_id) == str(state.get("last_observation_id", "")))
                    else "stale"
                )
                return ShadowObservationReport(
                    applied=False,
                    status=reason,
                    source_revision=source_revision,
                    shadow_revision=tx.revision,
                    total_known=len(state.get("goals", {})),
                )
            old_goals = dict(state.get("goals", {}))
            updated: dict[str, dict[str, Any]] = {}
            diffs: list[ShadowDiff] = []
            for record in prepared:
                goal_id = record["goal_id"]
                value, local_diffs = project_goal(
                    old_goals.get(goal_id), record,
                    source_revision=source_revision, observed_at=now,
                )
                updated[goal_id] = value
                diffs.extend(local_diffs)
            missing, missing_diffs = detect_missing(
                old_goals, set(updated), source_revision=source_revision,
            )
            updated.update(missing)
            diffs.extend(missing_diffs)
            if len(updated) > self._max_known:
                raise RuntimeError("shadow registry max_known exceeded: refuses silent goal truncation")
            tx.stage({
                "schema": self.SCHEMA,
                "last_source_revision": source_revision,
                "last_observation_id": str(observation_id),
                "goals": updated,
                "last_diffs": [
                    {"code": item.code, "goal_id": item.goal_id, "detail": item.detail}
                    for item in diffs
                ],
            })
            new_revision = tx.revision + 1
        return ShadowObservationReport(
            applied=True,
            status="applied",
            source_revision=source_revision,
            shadow_revision=new_revision,
            total_known=len(updated),
            diffs=tuple(diffs),
        )
