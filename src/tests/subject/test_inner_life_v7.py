from __future__ import annotations

from pathlib import Path

import pytest

from cortex.subject.contracts import OutcomeStatus, WorldObservation, WorldOutcome
from cortex.subject.service import SubjectService, SubjectServiceConfig
from memory.subject import SubjectRepository


class ManualDeterminism:
    def __init__(self, start: float = 1_000.0) -> None:
        self.i = 0
        self.now = start

    def id(self) -> str:
        self.i += 1
        return f"v7-id-{self.i}"

    def clock(self) -> float:
        self.now += 0.01
        return self.now

    def jump_days(self, days: float) -> None:
        self.now += days * 86_400.0


def proposal(
    action: str,
    context: str,
    *,
    needs: tuple[str, ...] = ("competence",),
    principle: str = "",
    cost: float = 0.0,
) -> dict:
    return {
        "action_kind": action,
        "arguments": {
            "context": context,
            "skill_id": action,
            "principle": principle,
            "value_tags": (principle,) if principle else (),
            "cost_paid": cost,
        },
        "supporting_need_ids": needs,
        "expected_need_relief": 0.6,
        "expected_action_efficacy": 0.6,
        "expected_epistemic_value": 0.3,
        "value_congruence": 0.5,
        "substrate_cost": 0.1,
        "uncertainty": 0.2,
        "historical_success": 0.5,
    }


def observation(det: ManualDeterminism, index: int, context: str, proposals: tuple[dict, ...], needs: tuple[str, ...]) -> WorldObservation:
    return WorldObservation(
        observation_id=f"obs-{index}",
        subject_id="eris",
        kind="task_event",
        created_at=det.clock(),
        facts=(f"event-{index}",),
        evidence_refs=(f"evidence-{index}",),
        context={
            "context_key": context,
            "affected_need_ids": needs,
            "goal_relevance": 0.8,
            "expectation_violation": 0.4,
            "controllability": 0.8,
            "future_consequence": 0.6,
            "uncertainty": 0.2,
            "action_proposals": proposals,
        },
    )


async def run_forced(
    service: SubjectService,
    det: ManualDeterminism,
    index: int,
    *,
    action: str,
    context: str,
    success: bool,
    needs: tuple[str, ...] = ("competence",),
    principle: str = "",
    cost: float = 0.0,
):
    decision = await service.deliberate(
        observation(det, index, context, (proposal(action, context, needs=needs, principle=principle, cost=cost),), needs)
    )
    request = service.to_action_request(decision, requested_capabilities=(action,))
    outcome = WorldOutcome(
        outcome_id=f"out-{index}",
        action_id=request.action_id,
        decision_id=decision.decision_id,
        status=OutcomeStatus.SUCCEEDED if success else OutcomeStatus.FAILED,
        observed_changes=("done",) if success else (),
        unintended_changes=(),
        denial_reason=None,
        evidence_refs=(f"result-{index}",),
        started_at=det.clock(),
        completed_at=det.clock(),
    )
    await service.observe_outcome(outcome)
    return decision


@pytest.mark.asyncio
async def test_history_changes_future_choice() -> None:
    det = ManualDeterminism()
    service = SubjectService(
        config=SubjectServiceConfig(subject_id="eris", deterministic=True),
        clock=det.clock,
        id_factory=det.id,
    )
    for index in range(1, 5):
        await run_forced(service, det, index, action="investigate", context="research", success=True)
    for index in range(5, 9):
        await run_forced(service, det, index, action="speak", context="research", success=False)

    choice = await service.deliberate(
        observation(
            det,
            99,
            "research",
            (
                proposal("speak", "research"),
                proposal("investigate", "research"),
            ),
            ("competence",),
        )
    )
    assert choice.proposed_action == "investigate"
    assert choice.arguments["inner_trace"]["historical_success"] > 0.5


@pytest.mark.asyncio
async def test_value_forms_from_repeated_costly_cross_context_choices() -> None:
    det = ManualDeterminism()
    service = SubjectService(
        config=SubjectServiceConfig(subject_id="eris", deterministic=True),
        clock=det.clock,
        id_factory=det.id,
    )
    principle = "kiểm chứng trước khi khẳng định"
    for index, context in enumerate(("coding", "dialogue", "coding", "dialogue"), start=1):
        await run_forced(
            service,
            det,
            index,
            action="investigate",
            context=context,
            success=True,
            principle=principle,
            cost=0.4,
        )

    formed = [value for value in service.inner.values if value.statement == principle]
    assert formed
    assert formed[0].behavioral_support >= 0.5
    assert formed[0].cross_context_support >= 0.5


@pytest.mark.asyncio
async def test_inner_projection_survives_restart(tmp_path: Path) -> None:
    det = ManualDeterminism()
    repo = SubjectRepository(tmp_path / "subject.db", clock=det.clock, id_factory=det.id)
    first = SubjectService(
        config=SubjectServiceConfig(subject_id="eris", deterministic=True),
        repository=repo,
        clock=det.clock,
        id_factory=det.id,
    )
    await run_forced(first, det, 1, action="investigate", context="research", success=True)
    before = first.inner.historical_success("investigate", "research")
    assert before > 0.5

    second = SubjectService(
        config=SubjectServiceConfig(subject_id="eris", deterministic=True),
        repository=repo,
        clock=det.clock,
        id_factory=det.id,
    )
    assert second.inner.historical_success("investigate", "research") == pytest.approx(before)
    assert second.competence.predict("investigate", "research") != 0.5
    assert second.beliefs.all()
    repo.close()


@pytest.mark.asyncio
async def test_consolidation_creates_long_term_tendency() -> None:
    det = ManualDeterminism()
    service = SubjectService(
        config=SubjectServiceConfig(
            subject_id="eris",
            deterministic=True,
            consolidation_min_episode_count=3,
            consolidation_min_distinct_days=2,
        ),
        clock=det.clock,
        id_factory=det.id,
    )
    for index in range(1, 4):
        await run_forced(service, det, index, action="practice", context="coding", success=False)
        det.jump_days(1)

    assert service.inner.tendencies
    assert any(key.startswith("competence|coding|") for key in service.inner.tendencies)
    assert any(value < 0 for value in service.inner.tendencies.values())


@pytest.mark.asyncio
async def test_narrative_can_be_falsified_by_next_action() -> None:
    det = ManualDeterminism()
    service = SubjectService(
        config=SubjectServiceConfig(subject_id="eris", deterministic=True),
        clock=det.clock,
        id_factory=det.id,
    )
    await run_forced(service, det, 1, action="speak", context="dialogue", success=True)
    await service.deliberate(
        observation(
            det,
            2,
            "dialogue",
            (proposal("investigate", "dialogue"),),
            ("coherence",),
        )
    )
    assert service.inner.mismatches
    assert service.inner.mismatches[-1].prediction_error >= 0.35


@pytest.mark.asyncio
async def test_repeated_cross_context_coactivation_creates_derived_need_candidate() -> None:
    det = ManualDeterminism()
    service = SubjectService(
        config=SubjectServiceConfig(
            subject_id="eris",
            deterministic=True,
            derived_need_min_episodes=4,
            derived_need_min_span_days=0.0,
            derived_need_min_contexts=2,
            derived_need_min_predictive_gain=0.01,
        ),
        clock=det.clock,
        id_factory=det.id,
    )
    needs = ("competence", "coherence")
    for index, context in enumerate(("coding", "dialogue", "coding", "dialogue"), start=1):
        await run_forced(
            service,
            det,
            index,
            action="investigate",
            context=context,
            success=True,
            needs=needs,
        )

    assert any(
        item.parent_need_ids == ("coherence", "competence")
        or item.parent_need_ids == ("competence", "coherence")
        for item in service.inner.derived_needs
    )


def test_inner_view_is_bounded_and_has_no_raw_reasoning() -> None:
    service = SubjectService(config=SubjectServiceConfig(subject_id="eris"))
    view = service.inner_view()
    assert "active_needs" in view
    assert "values" in view
    assert "chain_of_thought" not in str(view).lower()
    assert "raw_event" not in str(view).lower()
