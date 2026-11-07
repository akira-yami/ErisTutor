from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace

from cortex.subject.contracts import ObservationOrigin, SubjectExecutiveCommit, WorldObservation
from cortex.subject.initiative import MotiveKind
from cortex.subject.relational import RelationalState
from cortex.subject.service import SubjectService, SubjectServiceConfig
from runtime.ai_orchestrated.executive_cortex import ExecutiveCortex
from runtime.subject_ingress import SubjectIngress
from runtime.subject_initiative_runtime import (
    build_initiative_runtime_context,
    build_subject_initiative_affordances,
    coalesce_contact_probe_affordances,
)


class Determinism:
    def __init__(self) -> None:
        self.i = 0
        self.now = 1000.0

    def id(self) -> str:
        self.i += 1
        return f"v26-{self.i}"

    def clock(self) -> float:
        return self.now


def _service(d: Determinism) -> SubjectService:
    service = SubjectService(
        config=SubjectServiceConfig(restore_projection=False),
        clock=d.clock,
        id_factory=d.id,
    )
    service.relational_state = RelationalState(
        revision=8,
        updated_at=d.now,
        last_meaningful_exchange_at=d.now - 1800.0,
        affiliation_deficit=0.82,
        interaction_satiety=0.05,
        social_curiosity=0.62,
        partner_availability=0.90,
        interruption_cost=0.06,
        outreach_confidence=0.78,
        reciprocity_expectation=0.72,
        evidence_refs=("relational:v26",),
    )
    return service


def _internal_observation(d: Determinism) -> WorldObservation:
    return WorldObservation(
        observation_id="internal:v26",
        subject_id="eris",
        kind="subject_initiative_tick",
        created_at=d.now,
        origin=ObservationOrigin.INTERNAL,
        context={
            "context_key": "self_initiated",
            "affected_need_ids": ("connection", "coherence"),
            "goal_relevance": 0.62,
            "novelty": 0.05,
            "controllability": 0.90,
            "relational_significance": 0.80,
            "future_consequence": 0.35,
            "uncertainty": 0.72,
            "runtime_context": {
                "partner_available": 0.90,
                "interruption_cost": 0.06,
                "social_curiosity": 0.62,
                "user_model_uncertainty": 0.92,
                "user_model_evidence_refs": ("user-model:thin",),
            },
        },
        evidence_refs=("user-model:thin", "relational:v26"),
    )


def test_understand_user_motive_is_generated_without_random_seed() -> None:
    d = Determinism()
    service = _service(d)
    frame = asyncio.run(service.prepare_deliberation(_internal_observation(d)))

    motives = service.initiative_state.active_motives
    understand = next(item for item in motives if item.kind is MotiveKind.UNDERSTAND_USER)
    assert understand.pressure >= 0.40
    assert "user-model:thin" in understand.evidence_refs
    assert "seed" not in understand.reason.lower()
    # V44: motive vẫn là state thật, nhưng không tự hóa thành một câu hỏi NPC.
    assert not any(
        item.action_kind == "speak"
        and item.arguments.get("speech_act") == "user_probe"
        for item in frame.proposals
    )
    asyncio.run(service.abandon_deliberation(frame, reason="test_cleanup"))


def test_pending_intention_survives_origin_change_from_internal_to_user() -> None:
    d = Determinism()
    service = _service(d)
    first = asyncio.run(service.prepare_deliberation(_internal_observation(d)))
    intention = next(
        item for item in service.initiative_state.pending_intentions
        if item.motive_kind is MotiveKind.UNDERSTAND_USER
    )
    asyncio.run(service.abandon_deliberation(first, reason="test_cleanup"))

    d.now += 5.0
    user = WorldObservation(
        observation_id="user:v26",
        subject_id="eris",
        kind="user_turn",
        created_at=d.now,
        origin=ObservationOrigin.USER,
        context={
            "context_key": "direct_chat",
            "user_text": "ừ",
            "affected_need_ids": ("connection", "coherence"),
            "goal_relevance": 0.8,
            "novelty": 0.2,
            "controllability": 0.9,
            "relational_significance": 0.7,
            "future_consequence": 0.2,
            "uncertainty": 0.5,
            "runtime_context": {
                "user_model_uncertainty": 0.88,
                "user_model_evidence_refs": ("user-model:thin",),
            },
        },
        evidence_refs=("user:v26",),
    )
    second = asyncio.run(service.prepare_deliberation(user))
    after = next(
        item for item in service.initiative_state.pending_intentions
        if item.motive_kind is MotiveKind.UNDERSTAND_USER
    )
    assert after.intention_id == intention.intention_id
    assert after.source_origin is ObservationOrigin.INTERNAL
    # Intention sống qua origin change, nhưng V44 giữ nó ở motive layer cho tới
    # khi có contentful urge; không tự bịa semantic target để phát ngôn.
    assert not any(item.arguments.get("intention_id") == intention.intention_id for item in second.proposals)
    asyncio.run(service.abandon_deliberation(second, reason="test_cleanup"))


def test_subject_initiative_does_not_materialize_generic_probe_without_content() -> None:
    """V12: probe có thể tồn tại như signal, nhưng chưa phải public surface."""
    d = Determinism()
    service = _service(d)
    frame = asyncio.run(service.prepare_deliberation(_internal_observation(d)))
    asyncio.run(service.abandon_deliberation(frame, reason="test_cleanup"))

    affordances = build_subject_initiative_affordances(
        service,
        runtime_context={
            "user_model_uncertainty": 0.92,
            "user_model_evidence_refs": ("user-model:thin",),
        },
        now=d.now + 1.0,
    )
    assert affordances
    assert all(not hasattr(item.candidate, "text") for item in affordances)
    assert all(not hasattr(item.candidate, "speech_text") for item in affordances)


def test_contact_and_user_probe_are_coalesced_before_competition() -> None:
    contact = SimpleNamespace(
        affordance_id="relational:contact:1",
        candidate=SimpleNamespace(
            speech_act="contact_bid", relevance=0.90, confidence=0.90, urgency=0.80
        ),
    )
    probe = SimpleNamespace(
        affordance_id="subject-intention:probe:1",
        candidate=SimpleNamespace(
            speech_act="user_probe", relevance=0.70, confidence=0.72, urgency=0.60
        ),
    )
    task = SimpleNamespace(
        affordance_id="task:1",
        candidate=SimpleNamespace(
            speech_act="share_grounded_runtime_finding", relevance=0.80, confidence=0.90, urgency=0.50
        ),
    )

    thin_model = coalesce_contact_probe_affordances(
        (contact, probe, task),
        runtime_context={"user_model_uncertainty": 0.92},
    )
    assert [item.affordance_id for item in thin_model] == ["subject-intention:probe:1", "task:1"]

    thick_model = coalesce_contact_probe_affordances(
        (contact, probe, task),
        runtime_context={"user_model_uncertainty": 0.25},
    )
    assert [item.affordance_id for item in thick_model] == ["relational:contact:1", "task:1"]


def test_v66_startup_greeting_does_not_create_expression_cooldown() -> None:
    import time

    host = SimpleNamespace(
        _startup_greeting_spoken_at=time.time() - 20.0,
        private_mind_daemon=None,
        private_mind_system=None,
        private_mind_loop=None,
    )
    runtime_context = build_initiative_runtime_context(host)
    assert "startup_social_cooldown_active" not in runtime_context
    assert runtime_context["expression_authority"] == "self_owned"

    # V12 không cấm private curiosity signal; nó chỉ cấm signal tự trở thành
    # public speech. Boundary public được kiểm ở tests/conversation.
    d = Determinism()
    service = _service(d)
    frame = asyncio.run(service.prepare_deliberation(_internal_observation(d)))
    asyncio.run(service.abandon_deliberation(frame, reason="test_cleanup"))
    affordances = build_subject_initiative_affordances(
        service,
        runtime_context={**runtime_context, "user_model_uncertainty": 0.92},
        now=d.now + 1.0,
    )
    assert all(not hasattr(item.candidate, "speech_text") for item in affordances)


def test_chat_and_self_turn_commit_through_same_mindloop_port() -> None:
    d = Determinism()
    service = _service(d)
    ingress = SubjectIngress(service)

    class Executive:
        enabled = True

        def __init__(self) -> None:
            self.modes: list[str] = []

        def commit_subject_frame(self, frame, *, mode="runtime", **_kwargs):
            self.modes.append(mode)
            selected = frame.coalitions[0]
            return SubjectExecutiveCommit(
                commit_id=f"commit:{mode}:{len(self.modes)}",
                frame_id=frame.frame_id,
                selected_coalition_id=selected.coalition_id,
                selected_proposal_ref=selected.primary_proposal_ref,
                semantic_commitments=(),
                cognitive_requests=(),
                reason="same executive port for every origin",
                confidence=0.8,
                review_conditions=(),
                created_at=d.now,
            )

    executive = Executive()
    user_session = ingress.prepare_user_session("chào", created_at=d.now, turn_id="u-v26")
    ingress.complete_session(user_session, committer=executive)

    candidate = SimpleNamespace(
        kind="social_check_in", topic="ask one open question", speech_act="user_probe",
        tone="curious", source="subject_initiative", confidence=0.8, relevance=0.8,
        urgency=0.4, evidence_refs=("user-model:thin",), requires_grounding=False,
    )
    affordance = SimpleNamespace(
        affordance_id="subject-intention:test", owner="subject_initiative", candidate=candidate,
    )
    blackboard = SimpleNamespace(
        reply_expected=False, engagement_active=False, user_busy=False,
        public_view=lambda: {"reply_expected": False, "engagement_active": False, "novelty": 0.1},
    )
    auto_session = ingress.prepare_autonomous_session(
        blackboard, (affordance,), created_at=d.now, turn_id="a-v26",
        planning_context={
            "relational_state": service.relational_view(),
            "initiative_runtime_context": {"user_model_uncertainty": 0.9},
        },
    )
    ingress.complete_session(auto_session, committer=executive)

    assert executive.modes == ["interactive", "autonomous"]


def test_generic_subject_probe_is_not_available_for_commit_without_content() -> None:
    """Subject coalition chỉ là private proposal; RootAgent vẫn là public authority."""
    d = Determinism()
    service = _service(d)
    ingress = SubjectIngress(service)
    frame = asyncio.run(service.prepare_deliberation(_internal_observation(d)))
    asyncio.run(service.abandon_deliberation(frame, reason="prime"))
    affordances = build_subject_initiative_affordances(
        service, runtime_context={"user_model_uncertainty": 0.92}, now=d.now + 1.0
    )
    blackboard = SimpleNamespace(
        reply_expected=False, engagement_active=False, user_busy=False,
        public_view=lambda: {"reply_expected": False, "engagement_active": False, "novelty": 0.1},
    )
    session = ingress.prepare_autonomous_session(
        blackboard, affordances, created_at=d.now + 1.0, turn_id="materialize-v44",
        planning_context={
            "relational_state": service.relational_view(),
            "initiative_runtime_context": {"user_model_uncertainty": 0.92},
        },
    )
    for item in session.frame.coalitions:
        args = dict(item.arguments or {})
        assert "text" not in args
        assert "speech_text" not in args
    ingress.mind_loop.abandon_sync(session, reason="v12_private_signal_only")


def test_production_self_turn_uses_unified_subject_commit_path() -> None:
    """V12: autonomous/self-turn không còn public pipeline riêng.

    Subject runtime được giữ như nguồn signal/evidence; public speech phải quay
    về ConversationKernel/RootAgent, vì vậy test V26 không được soi module đã retire.
    """
    from pathlib import Path
    root = Path(__file__).resolve().parents[2]
    assert not (root / "runtime" / "ai_orchestrated" / "self_turn_pipeline.py").exists()
    architecture = (root / "tests" / "conversation" / "test_architecture_v12.py").read_text(encoding="utf-8")
    assert "test_retired_self_turn_and_social_modules_are_absent" in architecture


def test_seed_is_not_required_for_contentful_urge_wake_source() -> None:
    """V12: urge/seed chỉ là nguồn event; chúng không được tự materialize speech."""
    from pathlib import Path
    root = Path(__file__).resolve().parents[2]
    assert not (root / "runtime" / "ai_orchestrated" / "self_turn_pipeline.py").exists()
    source = (root / "runtime" / "expression_urge_runtime.py").read_text(encoding="utf-8")
    # Expression urge vẫn có thể tồn tại như private/runtime signal, nhưng không
    # được sở hữu public writer/TTS path.
    assert "note_competition" in source
    assert "self_turn_pipeline" not in source


def test_v66_runtime_context_does_not_encode_unanswered_user_as_expression_cost() -> None:
    now = 1_000.0
    host = SimpleNamespace(
        private_mind_daemon=None,
        private_mind_system=None,
        private_mind_loop=None,
        _startup_greeting_spoken_at=0.0,
        _temporal_last_user_turn_at=800.0,
        _temporal_last_assistant_turn_at=940.0,
        last_autonomous_spoken_at=940.0,
    )
    original_time = time.time
    try:
        time.time = lambda: now
        context = build_initiative_runtime_context(host)
    finally:
        time.time = original_time

    assert "recent_unsolicited_speech" not in context
    assert "user_response_rate" not in context
    assert "social_refractory_cost" not in context
    assert context["expression_authority"] == "self_owned"
    assert context["seconds_since_last_autonomous_speech"] == 60.0
