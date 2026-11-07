from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace

import pytest

from cortex.subject.contracts import (
    SubjectExecutiveCommit,
    WorldObservation,
)
from cortex.subject.service import SubjectService, SubjectServiceConfig
from runtime.ai_orchestrated.executive_cortex import (
    ExecutiveCortex,
    ExecutiveDecision,
    ExecutiveOutcome,
)
from runtime.subject_ingress import SubjectIngress


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


def _observation(clock) -> WorldObservation:
    return WorldObservation(
        observation_id="obs-executive",
        subject_id="eris",
        kind="user_turn",
        created_at=clock(),
        evidence_refs=("turn:1",),
        context={
            "context_key": "direct_chat",
            "user_text": "Kiểm tra lại rồi hãy trả lời.",
            "affected_need_ids": ("connection", "coherence"),
            "goal_relevance": 0.9,
            "controllability": 0.8,
            "uncertainty": 0.4,
            "action_proposals": (
                {
                    "proposal_id": "proposal:speak",
                    "action_kind": "speak",
                    "arguments": {"semantic_content": "trả lời ngay", "skill_id": "dialogue"},
                    "supporting_need_ids": ("connection",),
                    "expected_need_relief": 0.90,
                    "expected_action_efficacy": 0.90,
                    "expected_epistemic_value": 0.10,
                    "value_congruence": 0.60,
                    "substrate_cost": 0.05,
                    "uncertainty": 0.20,
                },
                {
                    "proposal_id": "proposal:investigate",
                    "action_kind": "investigate",
                    "arguments": {"query": "kiểm tra lại", "skill_id": "investigation"},
                    "supporting_need_ids": ("coherence",),
                    "expected_need_relief": 0.45,
                    "expected_action_efficacy": 0.65,
                    "expected_epistemic_value": 0.90,
                    "value_congruence": 0.55,
                    "substrate_cost": 0.15,
                    "uncertainty": 0.35,
                },
            ),
        },
    )


@pytest.mark.asyncio
async def test_executive_can_commit_non_top_coalition_and_owns_authorship() -> None:
    d = Determinism()
    service = SubjectService(
        config=SubjectServiceConfig(subject_id="eris", deterministic=True),
        clock=d.clock,
        id_factory=d.id,
    )
    frame = await service.prepare_deliberation(_observation(d.clock))
    investigate = next(item for item in frame.coalitions if item.action_kind == "investigate")
    commit = SubjectExecutiveCommit(
        commit_id="commit:investigate",
        frame_id=frame.frame_id,
        selected_coalition_id=investigate.coalition_id,
        selected_proposal_ref=investigate.primary_proposal_ref,
        semantic_commitments=("kiểm chứng trước khi khẳng định",),
        cognitive_requests=("inspect_evidence",),
        reason="uncertainty makes epistemic value decisive",
        confidence=0.82,
        review_conditions=("new_evidence",),
        created_at=d.clock(),
    )
    decision = await service.finalize_deliberation(frame, commit)

    assert decision.proposed_action == "investigate"
    assert decision.arguments["executive_commit"]["commit_id"] == "commit:investigate"
    authorship = service.authorship.all()[-1]
    assert authorship.selected_by == "executive_cortex:commit:investigate"


@pytest.mark.asyncio
async def test_subject_rejects_executive_commit_outside_prepared_frame() -> None:
    d = Determinism()
    service = SubjectService(
        config=SubjectServiceConfig(subject_id="eris", deterministic=True),
        clock=d.clock,
        id_factory=d.id,
    )
    frame = await service.prepare_deliberation(_observation(d.clock))
    commit = SubjectExecutiveCommit(
        commit_id="commit:invented",
        frame_id=frame.frame_id,
        selected_coalition_id="coalition:not-offered",
        selected_proposal_ref="proposal:not-offered",
        semantic_commitments=(),
        cognitive_requests=(),
        reason="invented option",
        confidence=0.5,
        review_conditions=(),
        created_at=d.clock(),
    )
    with pytest.raises(ValueError, match="not offered"):
        await service.finalize_deliberation(frame, commit)
    await service.abandon_deliberation(frame, reason="test_cleanup")


def test_user_ingress_no_longer_injects_fixed_action_scores() -> None:
    d = Determinism()
    service = SubjectService(
        config=SubjectServiceConfig(subject_id="eris", deterministic=True),
        clock=d.clock,
        id_factory=d.id,
    )
    ingress = SubjectIngress(service)
    session = ingress.prepare_user_session(
        "Anh còn nhớ chuyện trước không?",
        created_at=d.clock(),
        turn_id="turn-dynamic",
        planning_context={"memory_available": True},
    )
    frame = session.frame
    assert "action_proposals" not in frame.observation.context
    assert {item.action_kind for item in frame.proposals} >= {"speak", "investigate"}
    assert all("inner_trace" in item.arguments for item in frame.proposals)
    ingress.mind_loop.abandon_sync(session, reason="test_cleanup")


def test_parallel_neuro_advice_maps_ephemeral_choice_back_to_exact_subject_ids() -> None:
    d = Determinism()
    service = SubjectService(
        config=SubjectServiceConfig(subject_id="eris", deterministic=True),
        clock=d.clock,
        id_factory=d.id,
    )
    ingress = SubjectIngress(service)
    session = ingress.prepare_user_session(
        "Kiểm tra giúp tôi.",
        created_at=d.clock(),
        turn_id="turn-exact",
    )
    frame = session.frame
    coalitions = tuple(
        replace(item, score=(1.0 - 0.01 * index), uncertainty=0.72)
        for index, item in enumerate(frame.coalitions)
    )
    appraisals = tuple(
        replace(item, future_consequence=0.92, identity_significance=max(item.identity_significance, 0.82))
        for item in frame.appraisals
    )
    frame = replace(frame, coalitions=coalitions, appraisals=appraisals)
    selected_index = len(frame.coalitions) - 1
    selected = frame.coalitions[selected_index]

    def invoke(_client, **kwargs):
        assert all("coalition_id" not in item for item in kwargs["payload"]["choices"])
        return {
            "choice_index": selected_index,
            "semantic_commitments": ["giữ đúng ý định đã chọn"],
            "cognitive_requests": [],
            "reason": "selected from current semantic choices",
            "confidence": 0.77,
            "review_conditions": ["new_evidence"],
            "should_follow_up": True,
            "material_delta": 0.8,
            "followup_brief": "surface the material correction",
        }

    cortex = ExecutiveCortex(SimpleNamespace(), object(), invoke_structured=invoke, clock=d.clock)
    fast = cortex.commit_subject_frame(frame, user_text="Kiểm tra giúp tôi.", snapshot={})
    assert fast is not None
    assert fast.source == "neuro_fast_gate"

    advice = cortex.review_subject_frame(
        frame,
        baseline_commit=fast,
        user_text="Kiểm tra giúp tôi.",
        snapshot={},
    )
    assert advice is not None
    assert advice.commit.selected_coalition_id == selected.coalition_id
    assert advice.commit.selected_proposal_ref == selected.primary_proposal_ref

def test_autonomous_subject_frame_is_committed_directly_without_backward_binding() -> None:
    d = Determinism()
    service = SubjectService(
        config=SubjectServiceConfig(subject_id="eris", deterministic=True),
        clock=d.clock,
        id_factory=d.id,
    )
    ingress = SubjectIngress(service)
    candidate = SimpleNamespace(
        kind="random_chat",
        topic="gợi lại một chuyện cũ có liên quan",
        speech_act="gentle_callback",
        tone="warm",
        source="seed_worker",
        confidence=0.8,
        relevance=0.75,
        urgency=0.2,
        evidence_refs=("memory:episode:1",),
        requires_grounding=False,
    )
    affordance = SimpleNamespace(
        affordance_id="seed:1",
        owner="random_chat_seed",
        candidate=candidate,
    )
    blackboard = SimpleNamespace(
        reply_expected=False,
        engagement_active=False,
        public_view=lambda: {"reply_expected": False, "engagement_active": False},
    )
    session = ingress.prepare_autonomous_session(
        blackboard,
        [affordance],
        created_at=d.clock(),
        turn_id="autonomous:1",
    )
    frame = session.frame
    cortex = ExecutiveCortex(SimpleNamespace(), None, invoke_structured=lambda *_a, **_k: {})
    # V30: không còn bind ngược một ExecutiveOutcome vào Subject frame.
    # Subject/Neuro commit trực tiếp từ chính frame hiện tại.
    commit = cortex.commit_subject_frame(frame, mode="autonomous")
    assert commit is not None
    mind_decision = ingress.mind_loop.complete_sync(session, initial_commit=commit)
    assert mind_decision.coalition_id == commit.selected_coalition_id
    assert any(item.coalition_id == mind_decision.coalition_id for item in frame.coalitions)


def test_interactive_user_turn_never_runs_blocking_executive_review() -> None:
    d = Determinism()
    service = SubjectService(
        config=SubjectServiceConfig(subject_id="eris", deterministic=True),
        clock=d.clock,
        id_factory=d.id,
    )
    ingress = SubjectIngress(service)

    class _Executive:
        enabled = True

        def __init__(self) -> None:
            self.calls = 0

        def commit_subject_frame(self, _frame, **_kwargs):
            self.calls += 1
            raise AssertionError("interactive remote review must not sit on the public latency path")

    executive = _Executive()
    decision = ingress.observe_user_turn(
        "Việc này có chắc không?",
        created_at=d.clock(),
        turn_id="turn-review",
        committer=executive,
    )
    assert executive.calls == 0
    assert decision.arguments["executive_commit"]["source"] == "neuro_fast_gate"
