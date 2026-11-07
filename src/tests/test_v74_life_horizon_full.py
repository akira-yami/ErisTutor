# -*- coding: utf-8 -*-
from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace

from cortex.subject.expression_commitment import commitment_payload, hard_expression_boundary
from cortex.subject.neuro_control import NeuroActionSelector
from cortex.subject.service import SubjectService, SubjectServiceConfig
from runtime.conversation_courtesy import ConversationCourtesyContract
from runtime.horizon_arbiter import HorizonArbiter
from runtime.life_horizon import HorizonGoal, LifeHorizonManager
from runtime.review_lock import ReviewLock
from runtime.subject_ingress import SubjectIngress


def test_courtesy_waits_after_question_and_releases_on_user_turn() -> None:
    courtesy = ConversationCourtesyContract(timeout_seconds=180)
    state = courtesy.observe_assistant_commit(
        "Anh muốn em ưu tiên phần nào trước?",
        speech_act="user_probe",
        turn_ref="assistant:4",
        now=100.0,
    )
    assert state.awaiting_user_answer is True
    assert courtesy.runtime_context(now=120.0)["courtesy_hold"] is True
    assert courtesy.runtime_context(now=120.0)["pending_question_id"] == "assistant:4"

    released = courtesy.observe_user_turn(turn_ref="user:5", now=121.0)
    assert released.awaiting_user_answer is False
    assert courtesy.runtime_context(now=121.0)["courtesy_hold"] is False


def test_courtesy_timeout_is_bounded_not_permanent() -> None:
    courtesy = ConversationCourtesyContract(timeout_seconds=15)
    courtesy.observe_assistant_commit("Còn phần test thì sao?", speech_act="question", now=100.0)
    assert courtesy.runtime_context(now=110.0)["courtesy_hold"] is True
    assert courtesy.runtime_context(now=116.0)["courtesy_hold"] is False
    assert courtesy.snapshot(now=116.0).last_release_reason == "courtesy_timeout"


def test_courtesy_is_hard_expression_boundary() -> None:
    blocked, reason = hard_expression_boundary({"awaiting_user_answer": True})
    assert blocked is True
    assert reason == "awaiting_user_answer"


def test_subject_ingress_carries_courtesy_and_horizon_into_runtime_context(tmp_path) -> None:
    service = SubjectService(config=SubjectServiceConfig(subject_id="eris", deterministic=True))
    ingress = SubjectIngress(service)
    session = ingress.prepare_user_session(
        "tiếp tục nhé",
        created_at=1000.0,
        turn_id="u:1",
        planning_context={
            "conversation_courtesy": {
                "awaiting_user_answer": True,
                "courtesy_hold": True,
                "pending_question_id": "a:0",
            },
            "life_horizon": {
                "schema": "eris.life_horizon.v74",
                "revision": 3,
                "goals": [],
            },
        },
    )
    runtime = dict(session.frame.observation.context["runtime_context"])
    assert runtime["courtesy_hold"] is True
    assert runtime["pending_question_id"] == "a:0"
    assert runtime["life_horizon"]["revision"] == 3


def test_life_horizon_persists_h3_h4_and_project_plan(tmp_path) -> None:
    path = tmp_path / "life_horizon.json"
    manager = LifeHorizonManager(state_path=path, enabled=True)
    manager.upsert_goal(
        "h4.reliability",
        level="H4",
        title="Tăng độ tin cậy dài hạn",
        strength=0.88,
        evidence_refs=("self-model:reliability",),
    )
    manager.upsert_goal(
        "h3.reduce_hallucination",
        level="H3",
        title="Giảm ảo giác trong nhiều phiên",
        strength=0.84,
        parent_goal_id="h4.reliability",
        evidence_refs=("test:hallucination",),
    )

    reloaded = LifeHorizonManager(state_path=path, enabled=True)
    snapshot = reloaded.snapshot()
    by_id = {item.goal_id: item for item in snapshot.goals}
    assert by_id["h4.reliability"].level == "H4"
    assert by_id["h3.reduce_hallucination"].parent_goal_id == "h4.reliability"
    assert path.exists()


def test_refresh_projects_private_life_into_h2_h4(tmp_path) -> None:
    project = SimpleNamespace(
        origin="derived", title="Ổn định TTS", question="Làm sao không drop segment?",
        status="active", interest=0.9, support_count=4,
        source_refs=("private-project:tts",), evidence_ids=("test:tts",),
    )
    anchor = SimpleNamespace(
        origin="derived", retired_reason="", kind="identity", title="Ưu tiên độ tin cậy",
        question="Làm sao giảm khẳng định thiếu bằng chứng?", stance="verify first",
        pressure=0.72, continuity=0.86, source_refs=("subject:value",), evidence_ids=("ev:value",),
    )
    owner = SimpleNamespace(
        private_life_core=SimpleNamespace(
            state=SimpleNamespace(projects={"tts": project}, anchors={"reliable": anchor})
        ),
        conversation_handoff_runtime=None,
        logger=None,
    )
    manager = LifeHorizonManager(owner, state_path=tmp_path / "state.json", enabled=True)
    snapshot = manager.refresh_from_runtime(now=1000.0)
    assert any(item.level == "H2" and "TTS" in item.title for item in snapshot.goals)
    assert any(item.level == "H4" and "tin cậy" in item.title for item in snapshot.goals)


def _base_frame():
    service = SubjectService(config=SubjectServiceConfig(subject_id="eris", deterministic=True))
    session = SubjectIngress(service).prepare_user_session(
        "x",
        created_at=1000.0,
        turn_id="u:x",
        planning_context={},
    )
    return session.frame


def test_horizon_arbiter_boosts_aligned_project_and_reduces_unrelated_banter() -> None:
    frame = _base_frame()
    base = frame.coalitions[0]
    aligned = replace(
        base,
        coalition_id="coal:aligned",
        primary_proposal_ref="prop:aligned",
        proposal_refs=("prop:aligned",),
        action_kind="speak",
        score=0.50,
        arguments={
            **dict(base.arguments),
            "speech_act": "resume_shared_goal",
            "candidate_kind": "continuation",
            "semantic_target": "sửa pipeline TTS để không drop segment",
        },
    )
    random = replace(
        base,
        coalition_id="coal:random",
        primary_proposal_ref="prop:random",
        proposal_refs=("prop:random",),
        action_kind="speak",
        score=0.50,
        arguments={
            **dict(base.arguments),
            "speech_act": "casual_curiosity",
            "candidate_kind": "random_chat",
            "semantic_target": "hỏi một chuyện ngẫu nhiên không liên quan",
        },
    )
    snapshot = SimpleNamespace(
        revision=7,
        goals=(HorizonGoal(
            goal_id="h2.tts",
            level="H2",
            title="sửa pipeline TTS",
            description="không drop segment",
            status="active",
            strength=0.92,
        ),),
    )
    adjusted = HorizonArbiter().adjust_frame(replace(frame, coalitions=(random, aligned)), snapshot)
    by_id = {item.coalition_id: item for item in adjusted.coalitions}
    assert by_id["coal:aligned"].score > by_id["coal:random"].score
    assert by_id["coal:aligned"].arguments["horizon_goal_ids"] == ("h2.tts",)


def test_courtesy_boundary_breaks_expression_commitment_family_and_selects_silence() -> None:
    frame = _base_frame()
    base = frame.coalitions[0]
    committed = replace(
        base,
        coalition_id="coal:speak",
        primary_proposal_ref="prop:speak",
        proposal_refs=("prop:speak",),
        action_kind="speak",
        score=2.0,
        arguments={
            **dict(base.arguments),
            "speech_act": "user_probe",
            "semantic_target": "hỏi tiếp user",
            "expression_commitment": commitment_payload(
                origin="test", source_id="c1", semantic_target="hỏi tiếp user",
                pressure=0.95, freshness=0.95, persistence=0.95,
            ),
        },
    )
    silence = replace(
        base,
        coalition_id="coal:silence",
        primary_proposal_ref="prop:silence",
        proposal_refs=("prop:silence",),
        action_kind="silence",
        score=0.1,
        arguments={**dict(base.arguments), "speech_act": "silence"},
    )
    obs = replace(
        frame.observation,
        context={
            **dict(frame.observation.context),
            "runtime_context": {"awaiting_user_answer": True, "courtesy_hold": True},
        },
    )
    blocked_frame = replace(frame, observation=obs, coalitions=(committed, silence))
    result = NeuroActionSelector().select(blocked_frame)
    assert result.winner(blocked_frame).action_kind == "silence"


def test_review_lock_preserves_semantic_intent_until_invalidated() -> None:
    frame = _base_frame()
    base = frame.coalitions[0]
    first = replace(
        base,
        coalition_id="coal:first",
        primary_proposal_ref="prop:first",
        proposal_refs=("prop:first",),
        action_kind="speak",
        arguments={**dict(base.arguments), "speech_act": "user_probe", "semantic_target": "hỏi user"},
    )
    second = replace(
        base,
        coalition_id="coal:second",
        primary_proposal_ref="prop:second",
        proposal_refs=("prop:second",),
        action_kind="speak",
        arguments={**dict(base.arguments), "speech_act": "share_opinion", "semantic_target": "tự kể chuyện"},
    )
    frame = replace(frame, coalitions=(first, second))
    selection = NeuroActionSelector().select(frame)
    # Đảm bảo lock đúng candidate đầu bất kể score baseline trong fixture.
    selection = replace(selection, winner_index=0)
    lock = ReviewLock()
    state = lock.lock(frame, selection, now=100.0)
    assert state.speech_act == "user_probe"
    assert lock.allows(frame.frame_id, first, now=100.0) is True
    assert lock.allows(frame.frame_id, second, now=100.0) is False
    lock.invalidate(frame.frame_id, "new_user_turn", now=101.0)
    assert lock.allows(frame.frame_id, second) is True


def test_project_operation_receipt_updates_real_h2_progress(tmp_path) -> None:
    manager = LifeHorizonManager(state_path=tmp_path / "state.json", enabled=True)
    manager.upsert_goal("h2.repo", level="H2", title="Sửa repo", strength=0.9, progress=0.2)
    evidence = SimpleNamespace(evidence_id="git-diff:123")
    result = SimpleNamespace(
        capability_id="project_run_tests",
        operation_id="op-1",
        verified=True,
        success=True,
        executed=True,
        completed_at=500.0,
        evidence=(evidence,),
    )
    manager.note_project_operation(result, now=500.0)
    snap = manager.snapshot()
    goal = next(item for item in snap.goals if item.goal_id == "h2.repo")
    assert goal.progress > 0.2
    assert "git-diff:123" in goal.evidence_refs
    assert any(item.action_id == "project-operation.op-1" and item.status == "completed" for item in snap.next_actions)
