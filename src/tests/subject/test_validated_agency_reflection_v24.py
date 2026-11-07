from __future__ import annotations

from cortex.subject.agency import LearnedAgencyPlanner
from cortex.subject.beliefs import BeliefService
from cortex.subject.competence import CompetenceService, PracticePlanner
from cortex.subject.contracts import BeliefProposal, BeliefSourceKind, ReflectiveMismatch, VisibilityScope
from cortex.subject.reflection import ReflectiveSelfRegulator


def _ids():
    index = 0
    while True:
        index += 1
        yield f"id-{index}"


def test_learned_agency_only_emerges_after_measured_gap() -> None:
    ids = _ids()
    competence = CompetenceService(lambda: next(ids), lambda: 10.0)
    agency = LearnedAgencyPlanner(competence, PracticePlanner(lambda: next(ids), lambda: 10.0))
    # Chưa đủ trial thì không tự tạo project luyện tập.
    for i in range(2):
        competence.update("coding", 0.8, 0.0, skill_id="coding", context_key="repo", result_ref=f"r{i}", evidence_refs=(f"e{i}",))
    assert agency.project().gaps == ()

    competence.update("coding", 0.8, 0.0, skill_id="coding", context_key="repo", result_ref="r2", evidence_refs=("e2",))
    projection = agency.project()
    assert projection.gaps
    assert projection.practice_plan is not None
    project_bonus, epistemic_bonus = agency.proposal_bias(action_kind="practice", skill_id="coding", context_key="repo")
    assert project_bonus > 0
    assert epistemic_bonus > 0
    unrelated = agency.proposal_bias(action_kind="speak", skill_id="coding", context_key="repo")
    assert unrelated == (0.0, 0.0)


def test_reflective_mismatch_weakens_narrative_and_reopens_shared_evidence() -> None:
    ids = _ids()
    beliefs = BeliefService(lambda: next(ids), lambda: 20.0)
    narrative = beliefs.revise(
        BeliefProposal(
            proposal_id="p1",
            subject="self",
            predicate="interprets_action",
            object_json={"explanation": "tôi luôn kiểm chứng"},
            confidence=0.8,
            source_kind=BeliefSourceKind.SELF_INTERPRETATION,
            evidence_refs=("e-shared",),
            visibility_scope=VisibilityScope.REFLECTIVE,
        )
    ).belief
    alternative = beliefs.revise(
        BeliefProposal(
            proposal_id="p2",
            subject="world",
            predicate="counter_pattern",
            object_json={"seen": True},
            confidence=0.5,
            source_kind=BeliefSourceKind.INFERENCE,
            evidence_refs=("e-shared",),
            visibility_scope=VisibilityScope.PRIVATE,
        )
    ).belief
    mismatch = ReflectiveMismatch(
        mismatch_id="m1",
        narrative_belief_ids=(narrative.belief_id,),
        observed_action_ids=("a1",),
        prediction_error=0.8,
        contexts=("repo",),
        alternative_models=("alt",),
        status="open",
    )
    receipt = ReflectiveSelfRegulator().apply(beliefs, (mismatch,))
    assert narrative.belief_id in receipt.weakened_belief_ids
    assert alternative.belief_id in receipt.reopened_belief_ids
    assert beliefs.get(narrative.belief_id).confidence < narrative.confidence
    assert beliefs.get(alternative.belief_id).retrieval_salience > alternative.retrieval_salience


def test_subject_service_self_view_exposes_learned_agency_without_mutating_ids() -> None:
    from cortex.subject.service import SubjectService

    ids = _ids()
    service = SubjectService(id_factory=lambda: next(ids), clock=lambda: 30.0)
    for i in range(3):
        service.competence.update(
            "coding", 0.85, 0.0,
            skill_id="coding", context_key="repo",
            result_ref=f"r{i}", evidence_refs=(f"e{i}",),
        )
    first = service.self_view()["learned_agency"]
    second = service.self_view()["learned_agency"]
    assert first == second
    assert first["gaps"]
    assert first["practice_plan"]["plan_id"] == second["practice_plan"]["plan_id"]
