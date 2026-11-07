from __future__ import annotations

from types import SimpleNamespace

from cortex.subject.contracts import SubjectExecutiveCommit
from cortex.subject.deliberation import CognitiveRequestKind, CognitiveReviewResult
from cortex.subject.service import SubjectService, SubjectServiceConfig
from runtime.mind_loop import MindLoop
from runtime.subject_cognition import BoundedSubjectCognition
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


def _service(d: Determinism) -> SubjectService:
    return SubjectService(
        config=SubjectServiceConfig(subject_id="eris", deterministic=True),
        clock=d.clock,
        id_factory=d.id,
    )


def test_subject_runtime_no_longer_exposes_mutable_executive_binding() -> None:
    from runtime.subject_runtime import SubjectRuntime

    assert not hasattr(SubjectRuntime, "bind_executive")


def test_cognitive_adapter_returns_typed_findings_and_domain_integrates_them() -> None:
    d = Determinism()
    service = _service(d)
    ingress = SubjectIngress(service)
    session = ingress.prepare_user_session(
        "Việc này có chắc không?",
        created_at=d.clock(),
        turn_id="typed-review",
    )
    cognition = BoundedSubjectCognition(clock=d.clock, id_factory=d.id)
    results = cognition.execute(session.frame, (CognitiveRequestKind.VERIFY_EVIDENCE,))

    assert results
    assert all(isinstance(item, CognitiveReviewResult) for item in results)
    revised = MindLoop.run_sync(service.integrate_cognitive_results(session.frame, results))
    assert revised.frame_id != session.frame.frame_id
    assert any("executive_review" in proposal.arguments for proposal in revised.proposals)
    assert not hasattr(service, "review_deliberation")
    MindLoop.run_sync(service.abandon_deliberation(revised, reason="test_cleanup"))


def test_direct_chat_does_not_bind_late_remote_committer_to_critical_path() -> None:
    d = Determinism()
    service = _service(d)
    holder: dict[str, object] = {}
    loop = MindLoop(
        subject=service,
        committer_provider=lambda: holder.get("committer"),  # type: ignore[return-value]
        id_factory=d.id,
    )
    ingress = SubjectIngress(service, mind_loop=loop)

    class Executive:
        enabled = True

        def commit_subject_frame(self, _frame, **_kwargs):
            raise AssertionError("direct chat must not wait on a late remote committer")

    holder["committer"] = Executive()
    decision = ingress.observe_user_turn(
        "Kiểm tra giúp tôi.",
        created_at=d.clock(),
        turn_id="late-provider",
    )
    assert decision is not None
    assert decision.arguments["executive_commit"]["source"] == "neuro_fast_gate"

def test_autonomous_review_recommits_directly_on_revised_frame() -> None:
    """Cognitive review tạo frame mới; committer phải commit lại chính frame mới."""
    d = Determinism()
    service = _service(d)
    loop = MindLoop(subject=service, id_factory=d.id, max_reviews=1)
    ingress = SubjectIngress(service, mind_loop=loop)
    candidate = SimpleNamespace(
        kind="random_chat",
        topic="nhắc lại việc còn dở",
        speech_act="callback",
        tone="warm",
        source="seed_worker",
        confidence=0.8,
        relevance=0.8,
        urgency=0.2,
        evidence_refs=("memory:1",),
        requires_grounding=False,
    )
    affordance = SimpleNamespace(affordance_id="seed:1", owner="random_chat_seed", candidate=candidate)
    blackboard = SimpleNamespace(
        reply_expected=False,
        engagement_active=False,
        public_view=lambda: {"reply_expected": False, "engagement_active": False},
    )
    session = ingress.prepare_autonomous_session(
        blackboard,
        [affordance],
        created_at=d.clock(),
        turn_id="auto-review",
    )

    class Committer:
        enabled = True

        def __init__(self) -> None:
            self.frames: list[str] = []
            self.remote_budget_flags: list[bool] = []

        def commit_subject_frame(self, frame, **kwargs):
            self.frames.append(frame.frame_id)
            self.remote_budget_flags.append(bool(kwargs.get("allow_remote_deliberation", True)))
            selected = frame.coalitions[0]
            return SubjectExecutiveCommit(
                commit_id=f"auto:{len(self.frames)}",
                frame_id=frame.frame_id,
                selected_coalition_id=selected.coalition_id,
                selected_proposal_ref=selected.primary_proposal_ref,
                semantic_commitments=("giữ continuity",),
                cognitive_requests=("preserve_continuity",) if len(self.frames) == 1 else (),
                reason="commit lại sau bounded review",
                confidence=0.8,
                review_conditions=(),
                created_at=d.clock(),
            )

    committer = Committer()
    decision = ingress.complete_session(session, committer=committer)
    assert decision is not None
    # Review revises evidence on a new frame but must preserve the semantic winner.
    # It is not a second election, so the external committer is invoked only once.
    assert len(committer.frames) == 1
    assert committer.remote_budget_flags == [True]
    commit_trace = decision.arguments["executive_commit"]
    assert commit_trace["selected_proposal_ref"]
    assert commit_trace["source"].endswith(":review_lock")
    assert "semantic_winner_locked" in commit_trace["review_conditions"]

