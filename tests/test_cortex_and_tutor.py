# -*- coding: utf-8 -*-
"""Comprehensive tests for Mind & Cognition (src/cortex/) and Socratic Tutor (src/runtime/tutor/)."""
from __future__ import annotations

from dataclasses import replace
import importlib
from pathlib import Path
from types import SimpleNamespace

import pytest

from cortex.affective_dynamics import AffectiveDynamicsEngine, effective_affect_values
from cortex.appraisal_engine import AppraisalEngine
from cortex.causal_affect_models import ExogenousEvent
from cortex.contracts import SocialPresenceEvent, TelemetrySnapshot
from cortex.endogenous_drive import EndogenousDriveEngine, RollingInternalState
from cortex.epistemic import EpistemicKernel, EpistemicKernelConfig, EpistemicStore
from cortex.existential_anchor import AnchorSystem
from cortex.internal_state import InternalStateEngine, PersistentInternalState
from cortex.private_life import PrivateLifeCore
from cortex.subconscious import build_phase1234567_workers
from cortex.subject import SubjectService, SubjectServiceConfig
from cortex.subject.contracts import (
    ObservationOrigin,
    SubjectExecutiveCommit,
    WorldObservation,
)
from cortex.thought_stream import ThoughtStream
from cortex.tutor import (
    CuriosityGate,
    GapCollector,
    KnowledgeClassifier,
    QuestionFormulator,
    TutorConfig,
)
from cortex.tutor.research_relay import (
    TutorResearchRelayResult,
    build_gemini_research_prompt,
)
from cortex.tutor.secret_scrubber import scrub_secrets
from memory.subject import SubjectRepository
from runtime.capabilities.tutor import TutorCapability
from runtime.conversation.contracts import DeliveryReceipt
from runtime.coordinator import RuntimeCoordinator
from runtime.learning_core import KnowledgePoint, LearningPath, LearningService, LearningStore
from runtime.learning_core.policy import TutorStep
from runtime.pedagogical_contracts import TeachingConcept, TeachingPlan
from runtime.pedagogical_controller import (
    infer_user_evidence,
    observe_user_turn,
    select_next_action,
    start_episode,
)
from runtime.subject_bootstrap import build_subject_core_components
from runtime.subject_ingress import SubjectIngress
from runtime.tutor import (
    ConservativeTutorReasoner,
    LearnerHypothesis,
    MoveEnvelope,
    RuntimeTutorReasoner,
    SemanticAssessment,
    TeachingMove,
    TutorDiagnosis,
    TutorExecutive,
    TutorGuardrailPolicy,
    TutorObservation,
    TutorState,
)
from runtime.tutor.auto_path import (
    _explicit_pedagogical_activation,
    maybe_autostart_path,
    propose_learning_path,
)


def _sample_path() -> LearningPath:
    return LearningPath(
        path_id="python-async-101",
        title="Async Python cơ bản",
        goal="Hiểu event loop và coroutine",
        knowledge_points=(
            KnowledgePoint(
                kp_id="async.coroutine",
                title="Coroutine là gì",
                kp_type="concept",
                description="Hàm async tạo coroutine object và chỉ chạy khi được await hoặc schedule trên loop.",
            ),
            KnowledgePoint(
                kp_id="async.event_loop",
                title="Event loop điều phối",
                kp_type="procedure",
                description="Event loop chạy từng bước tại các điểm await.",
                prerequisites=("async.coroutine",),
            ),
            KnowledgePoint(
                kp_id="async.gather",
                title="Chạy đồng thời với gather",
                kp_type="procedure",
                description="asyncio.gather gom nhiều awaitable chạy xen kẽ trên cùng event loop.",
                prerequisites=("async.event_loop",),
            ),
        ),
    )


def _service(tmp_path: Path) -> LearningService:
    store = LearningStore(tmp_path / "learning.sqlite3")
    service = LearningService(store=store)
    path = _sample_path()
    service.register_path(path, now=1000.0)
    service.activate_path(path.path_id)
    return service


class _OverEagerReasoner(ConservativeTutorReasoner):
    """Reasoner that proposes an illegal move kind outside the guardrail envelope."""

    def choose_move(
        self,
        observation: TutorObservation,
        state: TutorState,
        diagnosis: TutorDiagnosis,
        envelope: MoveEnvelope,
    ) -> TeachingMove:
        # Propose 'explain' with 20 sentences even when envelope only allows probing
        return TeachingMove(
            move_id="m:overeager",
            kind="explain",
            kp_id=observation.kp_id,
            objective="Giảng giải tràn lan dù đang cần hỏi",
            instruction_vi="Giải thích hết toàn bộ async def và event loop trong một lượt.",
            question_goal="Kiểm tra phân biệt gọi hàm và await",
            max_sentences=20,
            requires_user_response=True,
        )


def test_all_cortex_and_runtime_tutor_modules_import_cleanly() -> None:
    src_root = Path(__file__).resolve().parents[1] / "src"
    count = 0
    for folder in ("cortex", "runtime/tutor", "memory/subject"):
        for py_file in sorted((src_root / folder).rglob("*.py")):
            rel = py_file.relative_to(src_root).with_suffix("")
            parts = list(rel.parts)
            if parts[-1] == "__init__":
                parts = parts[:-1]
            mod_name = ".".join(parts)
            importlib.import_module(mod_name)
            count += 1
    assert count >= 190


def test_cortex_internal_state_and_affect_appraisal() -> None:
    engine = InternalStateEngine()
    state = PersistentInternalState()
    assert isinstance(state, PersistentInternalState)

    snapshot = TelemetrySnapshot(
        captured_at="2026-11-07T18:00:00Z",
        active_window="main.py - ErisTutor",
        active_window_kind="coding",
        focus_summary="Đang viết code Python",
        active_app="Code.exe",
        active_window_title="main.py - ErisTutor",
        idle_seconds=5.0,
        inferred_pos_x=0.5,
    )
    updated = engine.sense_update(
        state,
        channel="chat",
        snapshot=snapshot,
        social_presence_event=SocialPresenceEvent(
            present=True,
            confidence=0.85,
            intensity=0.8,
            kind="user_message",
        ),
        user_text="Cảm ơn bạn nhiều nhé!",
    )
    assert isinstance(updated, PersistentInternalState)

    exogenous = ExogenousEvent(
        event_id="evt:1",
        kind="shared_success",
        actor_id="user",
        facts=("Người học vừa giải đúng bài tập về coroutine",),
        evidence_refs=("turn:1",),
        confidence=0.9,
        timestamp=1000.0,
    )
    frames = AppraisalEngine().appraise(exogenous)
    assert len(frames) >= 1

    affect_engine = AffectiveDynamicsEngine()
    decayed = affect_engine.decay(updated, now=1060.0)
    values = effective_affect_values(decayed)
    assert "valence" in values and "curiosity" in values

    drive_engine = EndogenousDriveEngine()
    rolling = RollingInternalState()
    assert drive_engine is not None and rolling is not None


def test_cortex_subconscious_private_life_and_epistemic(tmp_path: Path) -> None:
    workers = build_phase1234567_workers()
    assert len(workers) >= 7

    stream = ThoughtStream(context_provider=lambda: {})
    assert stream is not None

    private_life = PrivateLifeCore(root_dir=tmp_path)
    assert private_life is not None

    epistemic = EpistemicKernel(
        EpistemicStore(tmp_path / "epistemic.json"),
        config=EpistemicKernelConfig(),
    )
    assert epistemic is not None

    anchor_sys = AnchorSystem()
    assert anchor_sys is not None


def test_cortex_tutor_curiosity_and_research_scrubbing() -> None:
    cfg = TutorConfig()
    gate = CuriosityGate(cfg)
    collector = GapCollector(cfg)
    classifier = KnowledgeClassifier()
    formulator = QuestionFormulator(cfg)
    prompt = build_gemini_research_prompt("Python asyncio event loop")
    assert prompt.lower().startswith("gemini")
    relay_res = TutorResearchRelayResult(
        topic="Python asyncio",
        tutor_prompt=prompt,
        raw_answer="Kết quả từ https://www.python.org/doc về asyncio.",
        usable_summary="Event loop quản lý coroutine.",
    )
    assert gate is not None and collector is not None and classifier is not None and formulator is not None
    scrubbed = scrub_secrets("api_key=sk-1234567890abcdefghijklmnop")
    assert "sk-1234567890" not in scrubbed
    assert "Event loop" in relay_res.answer_text


@pytest.mark.asyncio
async def test_subject_core_deliberation_and_migrations(tmp_path: Path) -> None:
    repo = SubjectRepository(tmp_path / "subject.db")
    try:
        status = repo.migration_status()
        assert len(status) >= 7
        assert all(item["present"] and item["checksum_valid"] for item in status)

        service = SubjectService(
            config=SubjectServiceConfig(subject_id="eris-test"),
            repository=repo,
        )
        obs = WorldObservation(
            observation_id="obs:1",
            subject_id="eris-test",
            origin=ObservationOrigin.USER,
            kind="user_utterance",
            facts=("User hỏi về coroutine trong Python",),
            context={"user_text": "Hãy giải thích về coroutine trong Python"},
            created_at=1000.0,
        )
        frame = await service.prepare_deliberation(obs)
        assert frame.observation.observation_id == "obs:1"
        assert len(frame.proposals) >= 1
        assert len(frame.coalitions) >= 1

        top_coalition = frame.coalitions[0]
        commit = SubjectExecutiveCommit(
            commit_id="commit:1",
            frame_id=frame.frame_id,
            selected_coalition_id=top_coalition.coalition_id,
            selected_proposal_ref=top_coalition.primary_proposal_ref,
            semantic_commitments=("Giải thích về coroutine",),
            cognitive_requests=(),
            reason="Chọn hành động phù hợp nhất để giải đáp và duy trì kết nối",
            confidence=0.88,
            review_conditions=(),
            created_at=1001.0,
            source="test_executive",
        )
        decision = await service.finalize_deliberation(frame, commit)
        assert decision.observation_ref == "obs:1"
        ledger = service.decision_ledger_view(limit=5)
        assert len(ledger) >= 1
    finally:
        repo.close()


def test_subject_bootstrap_and_runtime_coordinator_wiring(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ERIS_SUBJECT_CORE_DATA_DIR", str(tmp_path / "subject_data"))
    monkeypatch.setenv("ERIS_LEARNING_DB_PATH", str(tmp_path / "learning.sqlite3"))
    components = build_subject_core_components(
        data_root=tmp_path / "subject_boot",
        workspace_root=tmp_path,
    )
    try:
        assert components.subject_service.config.subject_id
        assert components.repository.foreign_keys_enabled()
        ingress = SubjectIngress(components.runtime)
        assert ingress is not None
    finally:
        components.close()

    coord = RuntimeCoordinator(
        logger=SimpleNamespace(info=lambda *a, **k: None, warning=lambda *a, **k: None, error=lambda *a, **k: None, debug=lambda *a, **k: None),
        transport=SimpleNamespace(send=lambda *a, **k: None, close=lambda *a, **k: None),
        llm_service=SimpleNamespace(),
        parser=SimpleNamespace(),
        telemetry_service=SimpleNamespace(stop=lambda *a, **k: None),
        memory_core=SimpleNamespace(close=lambda *a, **k: None),
        body_running_fn=lambda: True,
    )
    try:
        assert isinstance(coord.internal_state_engine, InternalStateEngine)
        assert isinstance(coord.internal_state, PersistentInternalState)
        assert coord.subject_service is not None
        assert coord.subject_ingress is not None
        assert coord.learning_service is not None
        assert isinstance(coord.tutor_executive, TutorExecutive)
        assert isinstance(coord.tutor_capability, TutorCapability)
    finally:
        coord.stop()


def test_socratic_tutor_executive_and_guardrails(tmp_path: Path) -> None:
    service = _service(tmp_path)
    try:
        executive = TutorExecutive(
            service,
            reasoner=_OverEagerReasoner(),
            policy=TutorGuardrailPolicy(),
        )
        # First turn on cold start probes before teaching; _OverEagerReasoner tries
        # to force 'explain' with 20 sentences, but TutorGuardrailPolicy clamps it!
        first = executive.plan_next_move("", turn_id="t1", now=1010.0)
        assert first is not None
        assert first.move.kind in first.envelope.allowed_kinds
        assert first.move.max_sentences <= first.envelope.max_sentences

        # Commit delivery -> next turn transitions cleanly
        executive.commit_delivery(
            first,
            successful=True,
            public_text="Khi gọi hàm async def mà chưa await, nó tạo ra gì?",
        )

        second = executive.plan_next_move("Cho mình hỏi thêm", turn_id="t2", now=1020.0)
        assert second is not None
        assert second.move.kind in second.envelope.allowed_kinds
        assert second.move.max_sentences <= second.envelope.max_sentences

        # Writer projection must never leak expected_answer
        projection = second.to_writer_context()
        assert "expected_answer" not in str(projection)
        assert projection["schema"] == "eris.tutor.directive.v1"
    finally:
        service._store.close()


def test_socratic_semantic_assessment_and_adaptive_policy(tmp_path: Path) -> None:
    service = _service(tmp_path)
    try:
        reasoner = RuntimeTutorReasoner(
            SimpleNamespace(
                invoke_structured_worker=lambda **kw: {
                    "understanding": "misconception",
                    "confidence": 0.88,
                    "missing_concepts": ["async.coroutine"],
                    "misconceptions": [
                        {
                            "hypothesis_id": "h:sync_confusion",
                            "kp_id": "async.coroutine",
                            "description": "Nghĩ hàm async chạy đồng bộ ngay khi gọi",
                            "confidence": 0.88,
                            "status": "suspected",
                        }
                    ],
                    "error_pattern": "sync_confusion",
                    "recommended_move": "socratic_question",
                    "rationale": "Cần đặt câu hỏi Socratic để bóc tách bước tạo coroutine và bước await",
                    "kind": "socratic_question",
                    "objective": "Đặt câu hỏi Socratic dẫn dắt user nhận ra coroutine chưa chạy",
                    "instruction_vi": "Đặt câu hỏi Socratic về sự khác biệt giữa gọi hàm async và await.",
                    "question_goal": "Hỏi xem biến nhận giá trị trả về có kiểu gì",
                    "max_sentences": 3,
                    "requires_user_response": True,
                    "passed": False,
                    "score": 0.15,
                    "evidence_type": "probe_answer",
                }
            )
        )
        executive = TutorExecutive(service, reasoner=reasoner)
        turn1 = executive.plan_next_move("", turn_id="t1", now=1000.0)
        assert turn1 is not None
        executive.commit_delivery(
            turn1,
            successful=True,
            public_text="Khi gọi async def f() mà chưa await thì điều gì xảy ra?",
        )
        assessment, turn2 = executive.process_turn(
            "Nó chạy ngay lập tức thân hàm.",
            turn_id="t2",
            response_to_previous_move=True,
            now=1005.0,
        )
        assert turn2 is not None
        assert turn2.move.kind in turn2.envelope.allowed_kinds
        assert assessment is None or isinstance(assessment, SemanticAssessment)
        assert LearnerHypothesis is not None
    finally:
        service._store.close()


def test_auto_path_propose_and_autostart_without_orchestrator(tmp_path: Path) -> None:
    assert _explicit_pedagogical_activation("Hãy dạy tôi về lập trình bất đồng bộ Python") is True
    assert _explicit_pedagogical_activation("Hôm nay trời đẹp quá") is False

    fake_router = SimpleNamespace(
        plan_teaching_episode=lambda **kw: {
            "global_goal": "Làm chủ AsyncIO trong Python",
            "success_evidence": [
                "Phân biệt được coroutine object và việc thực thi trên event loop",
            ],
            "concepts": [
                {
                    "concept_id": "c1",
                    "objective": "Hiểu coroutine",
                    "description": "Khái niệm coroutine và async def",
                    "prerequisites": [],
                },
                {
                    "concept_id": "c2",
                    "objective": "Hiểu event loop",
                    "description": "Cách event loop điều phối coroutine tại điểm await",
                    "prerequisites": ["c1"],
                },
                {
                    "concept_id": "c3",
                    "objective": "Chạy đồng thời với gather",
                    "description": "Kết hợp nhiều task trên cùng event loop",
                    "prerequisites": ["c2"],
                },
            ],
        }
    )
    proposed = propose_learning_path(fake_router, topic_text="Hãy dạy tôi về AsyncIO trong Python")
    assert proposed is not None
    assert len(proposed.knowledge_points) == 3

    store = LearningStore(tmp_path / "autopath.sqlite3")
    learning = LearningService(store=store)
    try:
        host = SimpleNamespace(learning_service=learning, ai_model_router=fake_router)
        started = maybe_autostart_path(
            host,
            user_text="Hãy dạy tôi về AsyncIO trong Python từ gốc",
            now=2000.0,
        )
        assert started is True
        active = learning.active_path()
        assert active is not None
        assert active.path_id == proposed.path_id

        # Generic followup like "dạy tôi tiếp" must NOT overwrite or create a new path
        assert maybe_autostart_path(host, user_text="dạy tôi tiếp", now=2010.0) is False
        assert learning.active_path().path_id == proposed.path_id
    finally:
        store.close()


def test_pedagogical_socratic_controller_cycle() -> None:
    plan = TeachingPlan(
        global_goal="Hiểu AsyncIO",
        concepts=(
            TeachingConcept(
                concept_id="c1",
                objective="Coroutine tạo object chưa chạy ngay",
                description="Gọi async def trả về coroutine object",
                prerequisites=(),
            ),
            TeachingConcept(
                concept_id="c2",
                objective="Await nhường quyền cho event loop",
                description="Điểm nhường điều khiển hợp tác",
                prerequisites=("c1",),
            ),
            TeachingConcept(
                concept_id="c3",
                objective="Gather chạy đồng thời",
                description="Kết hợp nhiều coroutine trên cùng event loop",
                prerequisites=("c2",),
            ),
        ),
    )
    episode = start_episode(plan, now=1000.0)
    action1 = select_next_action(episode, now=1001.0)
    assert action1.kind == "teach_chunk"
    assert action1.directive is not None

    # Misconception -> repair action
    ev_misconception = infer_user_evidence(
        "Nó chỉ có nghĩa là chạy ngay lập tức",
        current_concept=plan.concepts[0],
    )
    assert "misconception_assertion" in ev_misconception
    res2 = observe_user_turn(
        episode,
        "Nó chỉ có nghĩa là chạy ngay lập tức",
        evidence=ev_misconception,
        now=1005.0,
    )
    assert res2.action.kind == "repair"

    # Acknowledgement ("ok") transitions episode to waiting_user -> next action is probe
    res3 = observe_user_turn(
        episode,
        "ok",
        evidence=(),
        now=1010.0,
    )
    assert res3.action.kind == "probe"


def test_tutor_capability_activity_and_receipt_binding(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ERIS_ARTIFACT_WORKSPACE", str(tmp_path / "artifacts"))
    store = LearningStore(tmp_path / "cap.sqlite3")
    learning = LearningService(store=store)
    try:
        cap = TutorCapability(TutorExecutive(learning))
        event = SimpleNamespace(event_id="u:1", session_id="s:1", generation=1, text="Cho mình bài tập Python")
        issued = cap.invoke_for_root(arguments={"operation": "activity"}, event=event, recent_history=[])
        assert issued["status"] == "completed"
        activity_id = issued["activity_id"]
        assert learning._store.load_activity(activity_id)["delivered"] is False

        receipt = DeliveryReceipt(
            delivery_id="del:1",
            move_id="mov:1",
            session_id="s:1",
            generation=1,
            successful=True,
        )
        cap.bind_pending_delivery(
            event_id="u:1",
            session_id="s:1",
            generation=1,
            delivery_id=receipt.delivery_id,
            move_id=receipt.move_id,
            artifact_bundle_ids=[issued["artifact_bundle_id"]],
        )
        cap.observe_delivery_receipt(receipt)
        assert learning._store.load_activity(activity_id)["delivered"] is True
    finally:
        store.close()


@pytest.mark.asyncio
async def test_cortex_and_tutor_edge_cases_and_error_paths(tmp_path: Path) -> None:
    from cortex.subject.contracts import AttributionDistribution, AttributionTarget
    from cortex.subject.service import StaleDeliberationFrame

    # 1. Empty input handling
    assert scrub_secrets("") == ""
    assert scrub_secrets(None) == ""
    assert propose_learning_path(SimpleNamespace(), topic_text="   ") is None
    assert maybe_autostart_path(SimpleNamespace(), user_text="") is False

    # 2. Boundary validation on AttributionDistribution (probabilities must sum to 1.0)
    with pytest.raises(ValueError, match="sum to 1"):
        AttributionDistribution({AttributionTarget.SELF: 0.2, AttributionTarget.ENVIRONMENT: 0.2})

    # 3. Boundary validation on TeachingPlan (< 3 concepts rejected, > 8 concepts rejected)
    with pytest.raises(ValueError, match="concepts_required"):
        TeachingPlan(
            global_goal="Too short",
            concepts=(TeachingConcept("c1", "d1"), TeachingConcept("c2", "d2")),
        )
    with pytest.raises(ValueError, match="concepts_limit"):
        TeachingPlan(
            global_goal="Too many",
            concepts=tuple(TeachingConcept(f"c{i}", f"d{i}") for i in range(9)),
        )

    # 4. Fail-closed on forbidden fields in TeachingPlanBuilder / propose_learning_path
    poisoned_router = SimpleNamespace(
        plan_teaching_episode=lambda **kw: {
            "global_goal": "Poisoned plan",
            "public_reply": "Leaked public utterance",
            "success_evidence": ["ev1"],
            "concepts": [
                {"concept_id": "c1", "objective": "o1", "description": "d1"},
                {"concept_id": "c2", "objective": "o2", "description": "d2"},
                {"concept_id": "c3", "objective": "o3", "description": "d3"},
            ],
        }
    )
    assert propose_learning_path(poisoned_router, topic_text="Dạy mình Python") is None

    # 5. SubjectService mismatched subject_id and StaleDeliberationFrame on superseded revision
    repo = SubjectRepository(tmp_path / "edge_subject.db")
    try:
        service = SubjectService(config=SubjectServiceConfig(subject_id="eris"), repository=repo)
        wrong_obs = WorldObservation(
            observation_id="obs:wrong",
            subject_id="someone-else",
            origin=ObservationOrigin.USER,
            kind="user_utterance",
            created_at=1000.0,
        )
        with pytest.raises(ValueError, match="does not match"):
            await service.prepare_deliberation(wrong_obs)

        obs1 = WorldObservation(
            observation_id="obs:1",
            subject_id="eris",
            origin=ObservationOrigin.USER,
            kind="user_utterance",
            created_at=1001.0,
        )
        obs2 = WorldObservation(
            observation_id="obs:2",
            subject_id="eris",
            origin=ObservationOrigin.USER,
            kind="user_utterance",
            created_at=1002.0,
        )
        frame1 = await service.prepare_deliberation(obs1)
        _frame2 = await service.prepare_deliberation(obs2)
        with pytest.raises(StaleDeliberationFrame):
            await service.integrate_cognitive_results(frame1, ())
    finally:
        repo.close()

    # 6. TutorExecutive empty active path and topic-switch suspension
    empty_store = LearningStore(tmp_path / "empty_tutor.sqlite3")
    empty_learning = LearningService(store=empty_store)
    try:
        exec_empty = TutorExecutive(empty_learning)
        assert exec_empty.plan_next_move("Xin chào", turn_id="t0") is None
        assert exec_empty.observe_response("Trả lời", turn_id="t0") is None
        assert exec_empty.suspend_pending_move() is False
    finally:
        empty_store.close()
