# -*- coding: utf-8 -*-
"""Identity, commitment and continuity reconciliation."""
from __future__ import annotations

import hashlib
import json
from typing import Any, Callable, Mapping

from .contracts import ActionAuthorship, CommitmentLedger, SelfContinuityRecord


class ContinuityService:
    def __init__(self, id_factory: Callable[[], str], clock: Callable[[], float]) -> None:
        self._id_factory = id_factory
        self._clock = clock

    @staticmethod
    def snapshot_ref(snapshot: Mapping[str, Any]) -> str:
        payload = json.dumps(snapshot, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        return hashlib.sha256(payload).hexdigest()

    def reconcile(
        self,
        snapshot: Mapping[str, Any],
        history: tuple[Mapping[str, Any], ...] = (),
        *,
        identity_key: str = "eris",
        commitments: tuple[CommitmentLedger, ...] = (),
        authorship: tuple[ActionAuthorship, ...] = (),
        evidence_refs: tuple[str, ...] = (),
    ) -> SelfContinuityRecord:
        prior_snapshot = history[-1] if history else {}
        prior_ref = self.snapshot_ref(prior_snapshot) if prior_snapshot else ""
        current_ref = self.snapshot_ref(snapshot)
        shared_keys = set(prior_snapshot) & set(snapshot)
        changed = sum(1 for key in shared_keys if prior_snapshot.get(key) != snapshot.get(key))
        rupture = changed / max(1, len(shared_keys)) if prior_snapshot else 0.0
        final_authorship = [record for record in authorship if record.finalized_at is not None]
        consistency = (
            sum(1.0 if record.claimed_as_self else 0.0 for record in final_authorship) / len(final_authorship)
            if final_authorship else 1.0
        )
        claim = "continuous_with_prior_snapshot" if rupture < 0.35 else "continuity_rupture_requires_repair"
        return SelfContinuityRecord(
            record_id=self._id_factory(),
            identity_key=identity_key,
            prior_snapshot_ref=prior_ref,
            current_snapshot_ref=current_ref,
            continuity_claim=claim,
            linked_belief_ids=tuple(str(item) for item in snapshot.get("belief_ids", ())),
            linked_value_ids=tuple(str(item) for item in snapshot.get("value_ids", ())),
            linked_commitment_ids=tuple(commitment.commitment_id for commitment in commitments),
            linked_action_ids=tuple(record.action_id for record in authorship),
            rupture_score=max(0.0, min(1.0, rupture)),
            repair_status="not_needed" if rupture < 0.35 else "open",
            authorship_consistency=max(0.0, min(1.0, consistency)),
            evidence_refs=evidence_refs,
            created_at=self._clock(),
        )
