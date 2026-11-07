"""Typed, read-only diagnostics for the initial goal registry shadow prototype.

These contracts describe LEGACY observations, not adopted autonomous goals.
A later semantic adoption/commit protocol must be introduced separately.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping


@dataclass(frozen=True, slots=True)
class ShadowGoalObservation:
    goal_id: str
    owner: str
    text: str
    legacy_state: str
    shadow_expected_state: str
    reason: str
    first_seen_at: float
    last_seen_at: float
    last_seen_source_revision: int
    missing_since_revision: int | None
    evidence_refs: tuple[str, ...]

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> "ShadowGoalObservation":
        return cls(
            goal_id=str(raw["goal_id"]),
            owner=str(raw.get("owner", "unknown")),
            text=str(raw.get("text", "")),
            legacy_state=str(raw.get("legacy_state", "")),
            shadow_expected_state=str(raw.get("shadow_expected_state", "")),
            reason=str(raw.get("reason", "")),
            first_seen_at=float(raw.get("first_seen_at", 0.0)),
            last_seen_at=float(raw.get("last_seen_at", 0.0)),
            last_seen_source_revision=int(raw.get("last_seen_source_revision", -1)),
            missing_since_revision=(
                None if raw.get("missing_since_revision") is None
                else int(raw["missing_since_revision"])
            ),
            evidence_refs=tuple(str(x) for x in raw.get("evidence_refs", ())),
        )


@dataclass(frozen=True, slots=True)
class ShadowDiff:
    code: str
    goal_id: str
    detail: str


@dataclass(frozen=True, slots=True)
class ShadowObservationReport:
    applied: bool
    status: str  # applied | duplicate | stale
    source_revision: int
    shadow_revision: int
    total_known: int
    diffs: tuple[ShadowDiff, ...] = ()
