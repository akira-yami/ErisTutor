# -*- coding: utf-8 -*-
"""Two-generation journal for durable engagement snapshots.

The registry already signs each snapshot. This boundary adds operational
survivability: keep the last known-good generation, validate journal metadata,
and recover from a torn/corrupt primary snapshot without inventing state.
"""
from __future__ import annotations

import copy
import time
from dataclasses import asdict, dataclass
from typing import Any, Callable, Mapping, MutableMapping

from runtime.subsystem_health import FailureKind, SubsystemHealthRegistry

from .registry import snapshot_digest, validate_snapshot_payload


ContainerProvider = Callable[[], MutableMapping[str, Any] | None]


@dataclass(frozen=True, slots=True)
class SnapshotJournalLoadResult:
    snapshot: Mapping[str, Any]
    source: str
    generation: int
    valid: bool
    repaired_primary: bool = False
    issues: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["snapshot"] = {
            "schema_version": int(self.snapshot.get("schema_version", 0) or 0),
            "revision": int(self.snapshot.get("revision", 0) or 0),
            "event_log_seq": int(self.snapshot.get("event_log_seq", 0) or 0),
        }
        return payload


class EngagementSnapshotJournal:
    """Maintain a primary and last-known-good engagement snapshot."""

    SCHEMA_VERSION = 1
    PRIMARY_KEY = "ai_engagement_state"
    BACKUP_KEY = "ai_engagement_state_backup"
    META_KEY = "ai_engagement_state_journal"

    def __init__(
        self,
        container_provider: ContainerProvider,
        *,
        clock: Callable[[], float] | None = None,
        health_registry: SubsystemHealthRegistry | None = None,
    ) -> None:
        self._container_provider = container_provider
        self._clock = clock or time.time
        self._health = health_registry
        self._last_load = SnapshotJournalLoadResult({}, "empty", 0, True)
        self._last_stage_generation = 0

    @staticmethod
    def _mapping(value: Any) -> Mapping[str, Any]:
        return value if isinstance(value, Mapping) else {}

    @staticmethod
    def _metadata(container: Mapping[str, Any]) -> Mapping[str, Any]:
        value = container.get(EngagementSnapshotJournal.META_KEY, {})
        return value if isinstance(value, Mapping) else {}

    @staticmethod
    def _generation(metadata: Mapping[str, Any]) -> int:
        try:
            return max(0, int(metadata.get("generation", 0) or 0))
        except (TypeError, ValueError):
            return 0

    @staticmethod
    def _metadata_int(metadata: Mapping[str, Any], key: str) -> int:
        try:
            return max(0, int(metadata.get(key, 0) or 0))
        except (TypeError, ValueError):
            return 0

    @staticmethod
    def _metadata_float(metadata: Mapping[str, Any], key: str) -> float:
        try:
            return max(0.0, float(metadata.get(key, 0.0) or 0.0))
        except (TypeError, ValueError):
            return 0.0

    @staticmethod
    def _revision(snapshot: Mapping[str, Any]) -> tuple[int, int]:
        try:
            revision = max(0, int(snapshot.get("revision", 0) or 0))
        except (TypeError, ValueError):
            revision = 0
        try:
            cursor = max(0, int(snapshot.get("event_log_seq", 0) or 0))
        except (TypeError, ValueError):
            cursor = 0
        return revision, cursor

    @staticmethod
    def _valid_snapshot(
        value: Any,
        *,
        expected_digest: str = "",
    ) -> tuple[bool, str, Mapping[str, Any]]:
        snapshot = value if isinstance(value, Mapping) else {}
        if not snapshot:
            return False, "missing", {}
        valid, reason = validate_snapshot_payload(snapshot)
        if not valid:
            return False, reason, snapshot
        actual = snapshot_digest(snapshot)
        expected = str(expected_digest or "").strip()
        if expected and actual != expected:
            return False, "journal_digest_mismatch", snapshot
        return True, reason, snapshot

    def load(self, *, auto_repair: bool = True) -> SnapshotJournalLoadResult:
        container = self._container_provider()
        if container is None:
            result = SnapshotJournalLoadResult({}, "unavailable", 0, False, issues=("container_unavailable",))
            self._last_load = result
            self._record_load_health(result)
            return result

        metadata = self._metadata(container)
        generation = self._generation(metadata)
        primary_ok, primary_reason, primary = self._valid_snapshot(
            container.get(self.PRIMARY_KEY),
            expected_digest=str(metadata.get("primary_digest", "") or ""),
        )
        backup_ok, backup_reason, backup = self._valid_snapshot(
            container.get(self.BACKUP_KEY),
            expected_digest=str(metadata.get("backup_digest", "") or ""),
        )
        primary_revision, primary_cursor = self._revision(primary)
        backup_revision, backup_cursor = self._revision(backup)
        expected_primary_revision = metadata.get("primary_revision")
        expected_primary_cursor = metadata.get("primary_event_log_seq")
        if primary_ok and expected_primary_revision is not None:
            try:
                if primary_revision != max(0, int(expected_primary_revision or 0)):
                    primary_ok, primary_reason = False, "journal_revision_mismatch"
            except (TypeError, ValueError):
                primary_ok, primary_reason = False, "journal_revision_invalid"
        if primary_ok and expected_primary_cursor is not None:
            try:
                if primary_cursor != max(0, int(expected_primary_cursor or 0)):
                    primary_ok, primary_reason = False, "journal_cursor_mismatch"
            except (TypeError, ValueError):
                primary_ok, primary_reason = False, "journal_cursor_invalid"
        if primary_ok and backup_ok and (backup_revision, backup_cursor) > (primary_revision, primary_cursor):
            primary_ok, primary_reason = False, "primary_older_than_backup"

        issues: list[str] = []
        repaired = False
        if primary_ok:
            source = "primary"
            snapshot = primary
        elif backup_ok:
            source = "backup"
            snapshot = backup
            issues.append(f"primary:{primary_reason}")
            if auto_repair:
                container[self.PRIMARY_KEY] = copy.deepcopy(dict(backup))
                repaired = True
                generation += 1
                previous_recoveries = 0
                try:
                    previous_recoveries = self._metadata_int(metadata, "recovery_count")
                except (TypeError, ValueError):
                    previous_recoveries = 0
                metadata = {
                    "schema_version": self.SCHEMA_VERSION,
                    "generation": generation,
                    "updated_at": max(0.0, float(self._clock())),
                    "primary_digest": snapshot_digest(backup),
                    "backup_digest": snapshot_digest(backup),
                    "primary_revision": backup_revision,
                    "primary_event_log_seq": backup_cursor,
                    "backup_revision": backup_revision,
                    "backup_event_log_seq": backup_cursor,
                    "recovered_from_backup": True,
                    "recovery_count": previous_recoveries + 1,
                    "last_recovery_at": max(0.0, float(self._clock())),
                    "last_recovery_reason": primary_reason,
                }
                container[self.META_KEY] = metadata
        else:
            source = "empty"
            snapshot = {}
            if primary_reason != "missing":
                issues.append(f"primary:{primary_reason}")
            if backup_reason != "missing":
                issues.append(f"backup:{backup_reason}")
            if not issues and (container.get(self.PRIMARY_KEY) or container.get(self.BACKUP_KEY)):
                issues.append("snapshot_unreadable")

        valid = source in {"primary", "backup", "empty"} and not (source == "empty" and issues)
        result = SnapshotJournalLoadResult(
            snapshot=copy.deepcopy(dict(snapshot)),
            source=source,
            generation=generation,
            valid=valid,
            repaired_primary=repaired,
            issues=tuple(issues),
        )
        self._last_load = result
        self._last_stage_generation = max(self._last_stage_generation, generation)
        self._record_load_health(result)
        return result

    def stage(self, payload: Mapping[str, Any]) -> int:
        """Stage a signed snapshot and rotate the previous valid primary to backup."""
        valid, reason = validate_snapshot_payload(payload)
        if not valid:
            raise ValueError(f"invalid engagement snapshot: {reason}")
        container = self._container_provider()
        if container is None:
            raise RuntimeError("engagement snapshot container is unavailable")
        metadata = self._metadata(container)
        generation = max(self._generation(metadata), self._last_stage_generation) + 1
        current_ok, _, current = self._valid_snapshot(
            container.get(self.PRIMARY_KEY),
            expected_digest=str(metadata.get("primary_digest", "") or ""),
        )
        next_snapshot = copy.deepcopy(dict(payload))
        next_digest = snapshot_digest(next_snapshot)
        backup_digest = ""
        backup_revision = 0
        backup_cursor = 0
        if current_ok:
            current_copy = copy.deepcopy(dict(current))
            container[self.BACKUP_KEY] = current_copy
            backup_digest = snapshot_digest(current_copy)
            backup_revision, backup_cursor = self._revision(current_copy)
        else:
            backup_ok, _, backup = self._valid_snapshot(
                container.get(self.BACKUP_KEY),
                expected_digest=str(metadata.get("backup_digest", "") or ""),
            )
            if backup_ok:
                backup_digest = snapshot_digest(backup)
                backup_revision, backup_cursor = self._revision(backup)

        next_revision, next_cursor = self._revision(next_snapshot)
        container[self.PRIMARY_KEY] = next_snapshot
        container[self.META_KEY] = {
            "schema_version": self.SCHEMA_VERSION,
            "generation": generation,
            "updated_at": max(0.0, float(self._clock())),
            "primary_digest": next_digest,
            "backup_digest": backup_digest,
            "primary_revision": next_revision,
            "primary_event_log_seq": next_cursor,
            "backup_revision": backup_revision,
            "backup_event_log_seq": backup_cursor,
            "recovered_from_backup": False,
            "recovery_count": self._metadata_int(metadata, "recovery_count"),
            "last_recovery_at": self._metadata_float(metadata, "last_recovery_at"),
            "last_recovery_reason": str(metadata.get("last_recovery_reason", "") or "")[:120],
        }
        self._last_stage_generation = generation
        if self._health is not None:
            self._health.record_success(
                "engagement.snapshot_journal",
                details={
                    "generation": generation,
                    "revision": int(next_snapshot.get("revision", 0) or 0),
                    "event_log_seq": int(next_snapshot.get("event_log_seq", 0) or 0),
                    "backup_available": bool(backup_digest),
                },
            )
        return generation

    def diagnostics(self) -> dict[str, Any]:
        container = self._container_provider()
        metadata = self._metadata(container or {})
        primary_ok, primary_reason, _ = self._valid_snapshot(
            (container or {}).get(self.PRIMARY_KEY),
            expected_digest=str(metadata.get("primary_digest", "") or ""),
        )
        backup_ok, backup_reason, _ = self._valid_snapshot(
            (container or {}).get(self.BACKUP_KEY),
            expected_digest=str(metadata.get("backup_digest", "") or ""),
        )
        has_snapshots = bool((container or {}).get(self.PRIMARY_KEY) or (container or {}).get(self.BACKUP_KEY))
        return {
            "schema_version": self.SCHEMA_VERSION,
            "generation": self._generation(metadata),
            "has_snapshots": has_snapshots,
            "recovery_count": self._metadata_int(metadata, "recovery_count"),
            "last_recovery_at": self._metadata_float(metadata, "last_recovery_at"),
            "last_recovery_reason": str(metadata.get("last_recovery_reason", "") or "")[:120],
            "primary_valid": primary_ok,
            "primary_reason": primary_reason,
            "backup_valid": backup_ok,
            "backup_reason": backup_reason,
            "last_load": self._last_load.to_dict(),
        }

    snapshot = diagnostics

    def _record_load_health(self, result: SnapshotJournalLoadResult) -> None:
        if self._health is None:
            return
        if result.valid and result.source == "primary":
            self._health.record_success(
                "engagement.snapshot_journal",
                details={"source": result.source, "generation": result.generation},
            )
            return
        if result.valid and result.source in {"backup", "empty"}:
            if result.source == "empty" and not result.issues:
                self._health.record_success(
                    "engagement.snapshot_journal",
                    details={"source": "empty", "generation": result.generation},
                )
            else:
                self._health.record_failure(
                    "engagement.snapshot_journal",
                    "; ".join(result.issues or ("primary_recovered_from_backup",)),
                    kind=FailureKind.PERSISTENCE_FAILURE,
                    degraded=True,
                    reason="engagement snapshot journal recovered a non-primary generation",
                    details=result.to_dict(),
                )
            return
        self._health.record_failure(
            "engagement.snapshot_journal",
            "; ".join(result.issues or ("snapshot_journal_unavailable",)),
            kind=FailureKind.PERSISTENCE_FAILURE,
            degraded=True,
            reason="engagement snapshot journal could not provide a valid snapshot",
            details=result.to_dict(),
        )


__all__ = ["EngagementSnapshotJournal", "SnapshotJournalLoadResult"]
