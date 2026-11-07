# -*- coding: utf-8 -*-
"""Action authorship before and after execution."""
from __future__ import annotations

from dataclasses import replace
from typing import Callable, Mapping

from .contracts import ActionAuthorship, MindDecision, WorldOutcome


class AuthorshipService:
    def __init__(self, id_factory: Callable[[], str], clock: Callable[[], float]) -> None:
        self._id_factory = id_factory
        self._clock = clock
        self._records: dict[str, ActionAuthorship] = {}

    def draft(
        self,
        decision: MindDecision,
        *,
        action_id: str | None = None,
        selected_by: str | None = None,
    ) -> ActionAuthorship:
        """Mở authorship record trước execution.

        ``selected_by`` được truyền từ boundary điều hành để forensic phân biệt
        Executive commit với fallback arbiter; không suy đoán nguồn quyền lực từ
        tên coalition sau khi việc đã xảy ra.
        """
        action_id = action_id or self._id_factory()
        record = ActionAuthorship(
            authorship_id=self._id_factory(),
            decision_id=decision.decision_id,
            action_id=action_id,
            generated_by="subject_service",
            selected_by=str(selected_by or decision.coalition_id),
            executed_by=None,
            policy_transforms=(),
            externally_constrained=False,
            deliberative_endorsement=max(0.0, min(1.0, decision.expected_pragmatic_value)),
            retrospective_endorsement=None,
            claimed_as_self=False,
            disclaimed_reason=None,
            evidence_refs=tuple(dict.fromkeys((decision.observation_ref, *decision.appraisal_refs, *decision.proposal_refs))),
            created_at=self._clock(),
            finalized_at=None,
        )
        self._records[record.authorship_id] = record
        return record

    def finalize(
        self,
        receipt: WorldOutcome | Mapping[str, object],
        reflection: Mapping[str, object] | None = None,
        *,
        authorship_id: str | None = None,
    ) -> ActionAuthorship:
        reflection = dict(reflection or {})
        action_id = str(receipt.action_id if isinstance(receipt, WorldOutcome) else receipt.get("action_id") or "")
        record = self._records.get(authorship_id or "")
        if record is None:
            record = next((item for item in self._records.values() if item.action_id == action_id), None)
        if record is None:
            raise KeyError(f"no authorship draft for action {action_id!r}")

        transforms = tuple(str(item) for item in reflection.get("policy_transforms", ()))
        constrained = bool(reflection.get("externally_constrained", False))
        retrospective = float(reflection.get("retrospective_endorsement", 1.0 if not constrained and not transforms else 0.5))
        retrospective = max(0.0, min(1.0, retrospective))
        claim = bool(reflection.get("claimed_as_self", retrospective >= 0.6 and not (transforms and retrospective < 0.6)))
        disclaimed = str(reflection.get("disclaimed_reason") or "").strip() or None
        executed_by = str(reflection.get("executed_by") or "sandbox_world")
        updated = replace(
            record,
            executed_by=executed_by,
            policy_transforms=transforms,
            externally_constrained=constrained,
            retrospective_endorsement=retrospective,
            claimed_as_self=claim,
            disclaimed_reason=disclaimed,
            evidence_refs=tuple(dict.fromkeys((*record.evidence_refs, str(getattr(receipt, "outcome_id", "") or reflection.get("outcome_id") or "")))),
            finalized_at=self._clock(),
        )
        self._records[record.authorship_id] = updated
        return updated

    def get(self, authorship_id: str) -> ActionAuthorship | None:
        return self._records.get(authorship_id)

    def all(self) -> tuple[ActionAuthorship, ...]:
        return tuple(sorted(self._records.values(), key=lambda item: (item.created_at, item.authorship_id)))
