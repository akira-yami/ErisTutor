"""Server-side pending interaction ledger.

Ported from DeepTutor pending.py (Apache 2.0).

Invariants:
1. Exactly one pending interaction per (path_id, kp_id) at a time.
2. An interaction can only be answered once (idempotent by interaction_id).
3. Expired interactions are auto-cancelled on next access.
4. The expected_answer is NEVER exposed to the public writer.

The ledger is an in-memory dict backed by LearningStore. This module is
pure Python with no I/O; it operates on plain dicts/dataclasses.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, replace
from typing import Any

from runtime.learning_core.contracts import PendingInteraction
from runtime.learning_core.grading import GradingResult, grade_choice


# ---------------------------------------------------------------------------
# Resolution result
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class ResolutionResult:
    """Result of resolving (grading) a pending interaction."""

    interaction: PendingInteraction     # updated with status = "answered"
    grading: GradingResult
    evidence_type: str                  # maps to LearningEvidence.evidence_type

    def to_dict(self) -> dict[str, Any]:
        return {
            "interaction": self.interaction.to_dict(),
            "grading": self.grading.to_dict(),
            "evidence_type": self.evidence_type,
        }


# ---------------------------------------------------------------------------
# Ledger
# ---------------------------------------------------------------------------

class PendingInteractionLedger:
    """In-memory ledger of pending interactions for one session.

    Key: interaction_id → PendingInteraction

    Thread safety: caller must hold a lock when using this in a multi-threaded
    context. The ledger itself does no locking.
    """

    def __init__(self) -> None:
        self._items: dict[str, PendingInteraction] = {}

    def restore_from_store(self, interaction: PendingInteraction) -> None:
        """Refresh a cache entry from authoritative SQLite after restart/rollback."""
        self._items[interaction.interaction_id] = interaction

    # ------------------------------------------------------------------
    def add(self, interaction: PendingInteraction) -> bool:
        """Register a new interaction.

        Returns False if an interaction with the same interaction_id already
        exists (idempotent add).
        """
        if interaction.interaction_id in self._items:
            return False
        # Cancel any existing pending interaction for the same (path, kp)
        to_cancel = [
            iid for iid, item in self._items.items()
            if item.path_id == interaction.path_id
            and item.kp_id == interaction.kp_id
            and item.status == "pending"
        ]
        for iid in to_cancel:
            self._items[iid] = replace(self._items[iid], status="cancelled")

        self._items[interaction.interaction_id] = interaction
        return True

    # ------------------------------------------------------------------
    def get_pending(self, path_id: str, kp_id: str, *, now: float | None = None) -> PendingInteraction | None:
        """Return the active pending interaction for a (path, kp) pair, or None."""
        ts = float(now if now is not None else time.time())
        self._expire_locked(ts)
        for item in self._items.values():
            if item.path_id == path_id and item.kp_id == kp_id and item.status == "pending":
                return item
        return None

    # ------------------------------------------------------------------
    def resolve(
        self,
        interaction_id: str,
        user_answer: str | int,
        *,
        turn_id: str = "",
        now: float | None = None,
    ) -> ResolutionResult | None:
        """Grade one pending interaction. Returns None if not found or already resolved."""
        ts = float(now if now is not None else time.time())
        self._expire_locked(ts)
        item = self._items.get(interaction_id)
        if item is None or item.status != "pending":
            return None

        if item.is_choice:
            grading = grade_choice(item, user_answer)
            evidence_type = "quiz_correct" if grading.correct else "quiz_incorrect"
        else:
            # Non-choice: caller must provide a qualitative result through
            # LearningService (semantic assessor). Treat any non-empty answer
            # as a "short_answer" pending semantic grading.
            grading = GradingResult(
                interaction_id=interaction_id,
                correct=True,   # optimistic; semantic assessor will override
                quality=2,
                notes="pending_semantic_assessment",
            )
            evidence_type = "paraphrase"

        updated = replace(item, status="answered", answered_at=ts, turn_id=turn_id[:120])
        self._items[interaction_id] = updated
        return ResolutionResult(interaction=updated, grading=grading, evidence_type=evidence_type)

    # ------------------------------------------------------------------
    def cancel_for_kp(self, path_id: str, kp_id: str) -> int:
        """Cancel all pending interactions for a (path, kp) pair. Returns count."""
        count = 0
        for iid, item in list(self._items.items()):
            if item.path_id == path_id and item.kp_id == kp_id and item.status == "pending":
                self._items[iid] = replace(item, status="cancelled")
                count += 1
        return count

    # ------------------------------------------------------------------
    def snapshot(self) -> list[dict[str, Any]]:
        """Public snapshot (no expected_answer exposed)."""
        return [item.to_dict() for item in self._items.values()]

    def all_pending(self) -> list[PendingInteraction]:
        return [item for item in self._items.values() if item.status == "pending"]

    # ------------------------------------------------------------------
    def _expire_locked(self, now: float) -> None:
        for iid, item in list(self._items.items()):
            if item.status == "pending" and 0 < item.expires_at <= now:
                self._items[iid] = replace(item, status="expired")


# ---------------------------------------------------------------------------
# Convenience standalone function
# ---------------------------------------------------------------------------

def resolve_pending(
    ledger: PendingInteractionLedger,
    interaction_id: str,
    user_answer: str | int,
    *,
    turn_id: str = "",
    now: float | None = None,
) -> ResolutionResult | None:
    """Resolve one pending interaction from a ledger. Thin wrapper."""
    return ledger.resolve(interaction_id, user_answer, turn_id=turn_id, now=now)


__all__ = [
    "PendingInteractionLedger",
    "ResolutionResult",
    "resolve_pending",
]
