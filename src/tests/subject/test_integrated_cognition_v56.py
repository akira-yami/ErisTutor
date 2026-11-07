# -*- coding: utf-8 -*-
from __future__ import annotations

from pathlib import Path

import pytest

from cortex.affective_dynamics import effective_affect_values
from cortex.appraisal_engine import AppraisalEngine
from cortex.causal_affect_models import ExogenousEvent
from cortex.internal_state import PersistentInternalState
from cortex.subject.acceptance import IntegratedAcceptanceEvaluator
from cortex.subject.contracts import (
    AttributionDistribution,
    AttributionTarget,
    ObservationOrigin,
    OrganismicSubstrate,
    OutcomeStatus,
    SubjectAppraisal,
    WorldObservation,
    WorldOutcome,
)
from cortex.subject.initiative import (
    InitiativeState,
    IntentionStatus,
    MotiveKind,
    PendingIntention,
)
from cortex.subject.needs import NeedSystem
from cortex.subject.service import SubjectService, SubjectServiceConfig
from memory.subject import SubjectRepository
from runtime.affective_dynamics_runtime import AffectiveDynamicsRuntime
from runtime.mind_loop import MindLoop


class Determinism:
    def __init__(self) -> None:
        self.now = 10_000.0
        self.index = 0

    def clock(self) -> float:
        self.now += 1.0
        return self.now

    def id(self) -> str:
        self.index += 1
        return f"v56-{self.index}"


class _Logger:
    def debug(self, *_args, **_kwargs):
        pass

    info = debug
    warning = debug


class _AffectHost:
    def __init__(self) -> None:
        self.state = PersistentInternalState()
        self.latest_personality_profile = {}
        self.logger = _Logger()
        self.stage_event_bus = None

    def _get_internal_state(self):
        return self.state

    def _set_internal_state(self, state):
        self.state = state


def _substrate() -> OrganismicSubstrate:
    return OrganismicSubstrate(
        subject_id="eris",
        revision=1,
        captured_at=1.0,
        measurement_refs=("measure:1",),
    )


def _appraisal(congruence: float) -> SubjectAppraisal:
    return SubjectAppraisal(
        appraisal_id=f"a:{congruence}",
        observation_id="obs",
        goal_relevance=0.9,
        novelty=0.3,
        expectation_violation=0.6,
        controllability=0.7,
        relational_significance=0.2,
        identity_significance=0.2,
        future_consequence=0.8,
        uncertainty=0.2,
        affected_need_ids=("competence",),
        attribution=AttributionDistribution({AttributionTarget.UNKNOWN: 1.0}),
        evidence_refs=("e:1",),
        goal_congruence=congruence,
        goal_progress_delta=congruence * 0.7,
        affected_goal_ids=("goal:repair",),
    )


def test_v56_need_pressure_distinguishes_goal_support_from_goal_threat() -> None:
    needs = NeedSystem()
    positive = needs.activate(_substrate(), (_appraisal(+0.8),))
    negative = needs.activate(_substrate(), (_appraisal(-0.8),))
    p = next(item.pressure for item in positive if item.need_id == "competence")
    n = next(item.pressure for item in negative if item.need_id == "competence")
    assert n > p


def test_v56_causal_appraisal_has_signed_goal_relation() -> None:
    event = ExogenousEvent(
        event_id="evt:broken",
        kind="promise_broken",
        actor_id="user",
        facts=("a promised follow-up did not occur",),
        evidence_refs=("turn:1",),
        confidence=0.9,
    )
    frame = AppraisalEngine().appraise(event)[0]
    assert frame.goal_congruence < 0.0
    assert frame.goal_progress_delta < 0.0
    assert frame.affected_goal_ids == ("goal_preserve_shared_continuity",)


def _waiting_intention(*, kind: MotiveKind, intention_id: str, now: float) -> PendingIntention:
    return PendingIntention(
        intention_id=intention_id,
        motive_kind=kind,
        semantic_goal="hiểu rõ điều user cần",
        target="user",
        pressure=0.7,
        commitment_strength=0.8,
        created_at=now - 20,
        updated_at=now - 2,
        evidence_refs=("prior:1",),
        source_origin=ObservationOrigin.INTERNAL,
        status=IntentionStatus.AWAITING_OUTCOME,
        last_expressed_at=now - 2,
        expression_count=1,
    )


@pytest.mark.asyncio
async def test_v56_user_outcome_becomes_goal_relative_appraisal() -> None:
    d = Determinism()
    service = SubjectService(
        config=SubjectServiceConfig(subject_id="eris", deterministic=True, restore_projection=False),
        clock=d.clock,
        id_factory=d.id,
    )
    now = d.clock()
    service.initiative_state = InitiativeState(
        revision=1,
        updated_at=now,
        pending_intentions=(_waiting_intention(kind=MotiveKind.CONNECT, intention_id="intent:connect", now=now),),
    )
    service.note_initiative_user_outcome(
        "tôi hiểu rồi, cảm ơn", now=now + 1, evidence_ref="turn:goal", persist=False
    )
    observation = WorldObservation(
        observation_id="obs:goal",
        subject_id="eris",
        kind="user_turn",
        created_at=now + 1,
        origin=ObservationOrigin.USER,
        context={
            "context_key": "direct_chat",
            "affected_need_ids": ("connection", "coherence"),
            "goal_relevance": 0.9,
            "controllability": 0.8,
        },
        evidence_refs=("turn:goal",),
    )
    frame = await service.prepare_deliberation(observation)
    try:
        assert frame.appraisals[0].goal_congruence > 0.5
        assert frame.appraisals[0].goal_progress_delta > 0.3
        assert "intent:connect" in frame.appraisals[0].affected_goal_ids
    finally:
        await service.abandon_deliberation(frame, reason="test_cleanup")


@pytest.mark.asyncio
async def test_v56_decision_ledger_closes_full_causal_loop_and_restores(tmp_path: Path) -> None:
    d = Determinism()
    repo = SubjectRepository(tmp_path / "subject-v56.db", clock=d.clock, id_factory=d.id)
    service = SubjectService(
        config=SubjectServiceConfig(subject_id="eris", deterministic=True),
        repository=repo,
        clock=d.clock,
        id_factory=d.id,
    )
    observation = WorldObservation(
        observation_id="obs:v56:failure",
        subject_id="eris",
        kind="task_failure",
        created_at=d.clock(),
        origin=ObservationOrigin.WORLD,
        facts=("integration test failed",),
        evidence_refs=("test:v56:failed",),
        context={
            "context_key": "integration",
            "affected_need_ids": ("competence", "coherence"),
            "goal_relevance": 0.95,
            "expectation_violation": 0.85,
            "future_consequence": 0.8,
            "controllability": 0.75,
            "uncertainty": 0.15,
        },
    )
    decision = await service.deliberate(observation)
    request = service.to_action_request(decision)
    outcome = WorldOutcome(
        outcome_id="out:v56:1",
        action_id=request.action_id,
        decision_id=decision.decision_id,
        status=OutcomeStatus.SUCCEEDED,
        observed_changes=("integration repaired",),
        unintended_changes=(),
        denial_reason=None,
        evidence_refs=("test:v56:passed",),
        started_at=d.clock(),
        completed_at=d.clock(),
    )
    await service.observe_outcome(outcome)

    record = service.decision_ledger_view(limit=1)[0]
    assert record["stage"] == "learned"
    assert record["decision_id"] == decision.decision_id
    assert record["outcome_id"] == outcome.outcome_id
    assert record["appraisal_state"]["appraisals"][0]["goal_congruence"] < 0.0
    assert record["before_state"]["substrate_ref"]
    assert record["after_state"]["substrate_ref"]

    restored = SubjectService(
        config=SubjectServiceConfig(subject_id="eris", deterministic=True),
        repository=repo,
        clock=d.clock,
        id_factory=d.id,
    )
    assert restored.decision_ledger_view(limit=1)[0]["outcome_id"] == outcome.outcome_id

    report = IntegratedAcceptanceEvaluator().evaluate(service.decision_ledger.latest(limit=20))
    assert report.passed
    assert report.outcome_coverage == 1.0


def test_v56_goal_appraisal_modulates_affect_once() -> None:
    host = _AffectHost()
    runtime = AffectiveDynamicsRuntime(host, logger=host.logger)
    appraisal = _appraisal(-0.8)
    before = effective_affect_values(host.state)
    first = runtime.ingest_subject_appraisal(appraisal, now=50_000.0)
    after = effective_affect_values(host.state)
    snapshot = host.state.to_dict()
    second = runtime.ingest_subject_appraisal(appraisal, now=50_000.1)

    assert first.applied is True
    assert after["anxiety"] > before["anxiety"]
    assert after["valence"] < before["valence"]
    assert second.applied is False
    assert second.reason == "duplicate_event"
    assert host.state.to_dict() == snapshot
