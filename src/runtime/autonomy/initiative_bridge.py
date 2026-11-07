"""Read-only projection: Subject intentions are candidates, not adopted goals.

No implicit elevation of a thought into a durable command. A later audited
Executive/Subject semantic commit must explicitly adopt a selected candidate.
"""
from __future__ import annotations

from .goal_events import GoalCandidate


def candidate_from_intention(intention, *, identity_revision: int, success_criteria: str) -> GoalCandidate:
    if str(getattr(getattr(intention, 'status', None), 'value', getattr(intention, 'status', ''))).lower() != 'pending':
        raise ValueError('only pending intention can be projected as a candidate')
    return GoalCandidate(
        goal_id='eris:intention:' + str(intention.intention_id),
        title=str(intention.semantic_goal),
        success_criteria=success_criteria,
        identity_revision=identity_revision,
        motive_ids=(str(getattr(getattr(intention, 'motive_kind', ''), 'value', getattr(intention, 'motive_kind', ''))),),
        evidence_refs=tuple(getattr(intention, 'evidence_refs', ()) or ()),
    )
