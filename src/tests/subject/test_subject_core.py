from __future__ import annotations

from pathlib import Path

import pytest

from cortex.subject.authorship import AuthorshipService
from cortex.subject.coalitions import CoalitionArbiter
from cortex.subject.consolidation import TemporalConsolidator
from cortex.subject.contracts import (
    ActionProposal,
    AttributionDistribution,
    AttributionTarget,
    BeliefSourceKind,
    MindDecision,
    NeedEpisode,
    OutcomeStatus,
    WorldObservation,
    WorldOutcome,
)
from cortex.subject.service import SubjectService, SubjectServiceConfig
from memory.subject import SubjectRepository


class Determinism:
    def __init__(self) -> None:
        self.index = 0
        self.now = 1000.0

    def id(self) -> str:
        self.index += 1
        return f"id-{self.index}"

    def clock(self) -> float:
        self.now += 1.0
        return self.now


def test_attribution_distribution_must_sum_to_one() -> None:
    with pytest.raises(ValueError):
        AttributionDistribution({AttributionTarget.SELF: 0.7, AttributionTarget.UNKNOWN: 0.1})


def test_coalition_keeps_losing_need_as_dissent() -> None:
    det = Determinism()
    arbiter = CoalitionArbiter(id_factory=det.id, clock=det.clock, deterministic=True)
    winner = ActionProposal("p1", "investigate", {}, ("coherence",), 0.9, 0.8, 0.8, 0.5, 0.0, 0.1)
    loser = ActionProposal("p2", "speak", {}, ("connection",), 0.2, 0.4, 0.1, 0.5, 0.0, 0.5)
    decision = arbiter.choose((winner, loser), observation_ref="o", appraisal_refs=("a",), substrate_ref="s")
    assert decision.proposed_action == "investigate"
    assert decision.dissenting_need_ids == ("connection",)


def test_consolidation_caps_structural_step() -> None:
    det = Determinism()
    distribution = AttributionDistribution({AttributionTarget.SELF: 1.0})
    episodes = tuple(
        NeedEpisode(
            episode_id=f"e{i}", need_id="competence", started_at=i * 86400.0, ended_at=i * 86400.0 + 10,
            deficit=1.0, satisfaction=0.0, controllability=0.8, predictability=0.8,
            attribution=distribution, evidence_refs=(f"ref{i}",), social_context_ids=("coding",),
            consolidation_state="pending",
        )
        for i in range(3)
    )
    patch = TemporalConsolidator(det.id, det.clock).consolidate(episodes)
    assert patch.tendency_deltas
    assert max(abs(value) for value in patch.tendency_deltas.values()) <= 0.08


@pytest.mark.asyncio
async def test_subject_service_full_causal_slice(tmp_path: Path) -> None:
    det = Determinism()
    repo = SubjectRepository(tmp_path / "subject.db", clock=det.clock, id_factory=det.id)
    service = SubjectService(
        config=SubjectServiceConfig(subject_id="eris", deterministic=True),
        repository=repo,
        clock=det.clock,
        id_factory=det.id,
    )
    observation = WorldObservation(
        observation_id="obs-1",
        subject_id="eris",
        kind="task_failure",
        created_at=det.clock(),
        facts=("compiler returned nonzero",),
        evidence_refs=("compiler-log:1",),
        measurements={"action_efficacy": 0.35},
        context={
            "affected_need_ids": ("competence", "coherence"),
            "goal_relevance": 0.9,
            "expectation_violation": 0.8,
            "controllability": 0.7,
            "uncertainty": 0.2,
            "context_key": "coding",
            "action_proposals": (
                {
                    "action_kind": "practice",
                    "arguments": {"task": "repair", "requested_capabilities": ("practice",)},
                    "supporting_need_ids": ("competence",),
                    "expected_need_relief": 0.8,
                    "expected_action_efficacy": 0.7,
                    "expected_epistemic_value": 0.4,
                    "value_congruence": 0.6,
                    "substrate_cost": 0.2,
                    "uncertainty": 0.2,
                },
            ),
        },
    )
    decision = await service.deliberate(observation)
    request = service.to_action_request(decision, requested_capabilities=("practice",))
    outcome = WorldOutcome(
        outcome_id="outcome-1",
        action_id=request.action_id,
        decision_id=decision.decision_id,
        status=OutcomeStatus.SUCCEEDED,
        observed_changes=("repair completed",),
        unintended_changes=(),
        denial_reason=None,
        evidence_refs=("test:passed",),
        started_at=det.clock(),
        completed_at=det.clock(),
    )
    receipt = await service.observe_outcome(outcome)
    assert receipt.closed_episode_ids
    assert receipt.belief_revision_ids
    assert service.authorship.get(receipt.authorship_id).claimed_as_self is True
    assert repo.verify_event_chain("eris")
    event_types = [event.event_type for event in repo.events(subject_id="eris")]
    assert "raw_observation" in event_types
    assert "mind_decision" in event_types
    assert "world_outcome" in event_types
    assert "narrative_belief" in event_types


def test_authorship_cannot_claim_hidden_transform_without_endorsement() -> None:
    det = Determinism()
    service = AuthorshipService(det.id, det.clock)
    decision = MindDecision(
        "d", "c", "speak", {}, 0.7, 0.1, 0.2, ("connection",), (), (), (), "o", ("a",), "s", ("p",), det.clock()
    )
    draft = service.draft(decision, action_id="action")
    outcome = WorldOutcome("out", "action", "d", OutcomeStatus.SUCCEEDED, (), (), None, ("ref",), det.clock(), det.clock())
    finalized = service.finalize(outcome, {"policy_transforms": ("rewritten",), "retrospective_endorsement": 0.4, "claimed_as_self": False}, authorship_id=draft.authorship_id)
    assert finalized.claimed_as_self is False
