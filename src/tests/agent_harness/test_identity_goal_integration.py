# -*- coding: utf-8 -*-
"""Kiểm chứng toàn vòng Subject → Executive → Work → Outcome → Subject."""
from __future__ import annotations

import time
from pathlib import Path
from types import SimpleNamespace

from cortex.subject.contracts import ObservationOrigin
from cortex.subject.initiative import InitiativeState, PendingIntention, MotiveKind
from cortex.subject.service import SubjectService, SubjectServiceConfig
from runtime.ai_orchestrated.executive_cortex import ExecutiveCortex
from runtime.agent_harness.identity_goal_driver import IdentityGoalDriver
from runtime.agent_harness.identity_goal_runtime import ensure_identity_goal_driver
from runtime.agent_harness.unified_sessions import UnifiedAgentSessions
from runtime.autonomy.goal_events import GoalEventLedger
from runtime.mind_loop import MindLoop


def _subject():
    subject = SubjectService(config=SubjectServiceConfig(restore_projection=False))
    now = time.time()
    subject.initiative_state = InitiativeState(
        revision=7, updated_at=now,
        pending_intentions=(PendingIntention(
            intention_id="p2-demo", motive_kind=MotiveKind.EXPLORE,
            semantic_goal="Xác minh một bài toán trong sandbox", target="math",
            pressure=.95, commitment_strength=.98,
            created_at=now, updated_at=now, evidence_refs=("problem:1",),
            source_origin=ObservationOrigin.INTERNAL,
        ),),
    )
    return subject


def _driver(tmp_path: Path, *, planner=None, motor=None):
    subject = _subject()
    # Executive thật, không trả semantic commit dựng sẵn.
    executive = ExecutiveCortex(SimpleNamespace(nervous_system_v103=motor), None,
                                invoke_structured=lambda *_a, **_k: None)
    mind = MindLoop(subject=subject, committer_provider=lambda: executive)
    ledger_path = tmp_path / "goals.sqlite"
    sessions = UnifiedAgentSessions(tmp_path / "jobs.sqlite", planner=planner or (
        lambda _ctx: {"kind": "complete", "text": "Kết quả bước đã kiểm tra", "action_id": "", "arguments": {}}
    ), goal_ledger_path=ledger_path)
    driver = IdentityGoalDriver(subject, mind, sessions, ledger=GoalEventLedger(ledger_path))
    return driver, sessions, subject


def _until(driver, predicate, timeout=5.0):
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        last = driver.pump()
        if predicate():
            return last
        time.sleep(.05)
    raise AssertionError(f"Quá thời gian: {last}")


def test_executive_commit_thuc_tu_khoi_dong_work_va_phan_tinh(tmp_path, monkeypatch):
    monkeypatch.setenv("ERIS_GOAL_MAX_JOBS", "2")
    driver, sessions, subject = _driver(tmp_path)
    try:
        goal_id = "eris:intention:p2-demo"
        assert driver.pump()["status"] == "decision_scheduled"
        _until(driver, lambda: bool(driver.ledger.get(goal_id)))
        goal = driver.ledger.get(goal_id)
        assert goal.status == "active"
        assert goal.adoption_commit_id.startswith("neuro-commit:")
        assert goal.payload["adoption_proof"]["source"] in {"neuro_fast_gate", "neuro_local_semantic_merge"}
        assert goal.payload["candidate"]["evidence_refs"][-1].startswith("subject-decision:")
        # Hết ngân sách work -> Subject thực sự chọn tạm dừng thay vì spin vô hạn.
        _until(driver, lambda: (g := driver.ledger.get(goal_id)) is not None and g.status == "suspended")
        assert sessions._goal_bridge.job_count(goal_id) == 2
        with driver.ledger._db() as db:
            feedback = db.execute("SELECT status FROM autonomy_subject_feedback").fetchall()
            decisions = db.execute("SELECT status FROM autonomy_goal_reviews").fetchall()
        assert len(feedback) == 2 and all(row[0] == "learned" for row in feedback)
        assert decisions[-1][0] == "suspended"
        assert subject.decision_ledger_view()
    finally:
        driver.close()


def test_khong_nhan_goal_neu_executive_chon_hanh_dong_khac(tmp_path):
    driver, sessions, subject = _driver(tmp_path)
    class OtherDecision:
        proposed_action = "observe"
        arguments = {"goal_id": "eris:intention:p2-demo"}
        decision_id = "other-decision"
    driver.mind_loop.deliberate_sync = lambda *_a, **_k: OtherDecision()
    try:
        assert driver._decide_and_start(subject.initiative_state.pending_intentions[0]) is None
        assert driver.ledger.list_goals() == ()
        assert sessions.recent() == []
    finally:
        driver.close()


def test_goals_feedback_khong_tran_qua_speech_pipeline(tmp_path):
    driver, sessions, subject = _driver(tmp_path)
    try:
        goal = driver._decide_and_start(subject.initiative_state.pending_intentions[0])
        assert goal and goal.job_id
        _until(driver, lambda: bool((driver.ledger.get(goal.goal_id).payload.get("last_outcome"))))
        assert sessions.snapshot(goal.job_id)["status"] == "completed"
        assert driver.ledger.get(goal.goal_id).payload.get("last_outcome")
        # Dữ liệu goal/work không tự tạo public turn/TTS.
        assert not hasattr(driver, "speech_authority")
    finally:
        driver.close()


def test_link_epoch_chan_job_cu_sau_suspend_resume(tmp_path):
    driver, sessions, subject = _driver(tmp_path)
    try:
        adopted = driver._decide_and_start(subject.initiative_state.pending_intentions[0])
        goal = driver.ledger.get(adopted.goal_id)
        from runtime.autonomy.goal_events import SemanticCommitProof
        proof = SemanticCommitProof(commit_id="manual-suspend", source="test_only",
            identity_revision=goal.payload["candidate"]["identity_revision"], motive_ids=("explore",),
            evidence_refs=("manual",), episode_id="test")
        goal = driver.ledger.transition(goal.goal_id, expected_revision=goal.revision,
            status="suspended", event_id="test:suspend", proof=proof)
        goal = driver.ledger.transition(goal.goal_id, expected_revision=goal.revision,
            status="active", event_id="test:resume", proof=proof)
        assert not sessions._goal_bridge.goal_fresh(adopted.job_id)
    finally:
        driver.close()


def test_runtime_lay_dung_singleton_work_session(tmp_path, monkeypatch):
    monkeypatch.setenv("ERIS_AUTONOMY_V3_DB", str(tmp_path / "goals.sqlite"))
    subject = _subject()
    sessions = UnifiedAgentSessions(tmp_path / "jobs.sqlite",
        planner=lambda _ctx: {"kind": "complete", "text": "ok", "action_id": "", "arguments": {}},
        goal_ledger_path=tmp_path / "goals.sqlite")
    fake_executor = SimpleNamespace(_parallel_work_service=sessions)
    host = SimpleNamespace(subject_service=subject, subject_mind_loop=MindLoop(subject=subject),
                           action_executor=fake_executor)
    driver = ensure_identity_goal_driver(host)
    try:
        assert driver is ensure_identity_goal_driver(host)
        assert driver.sessions is sessions
    finally:
        driver.close()


def test_v103_expression_motor_khong_chiem_quyen_goal_work(tmp_path):
    class ExpressionMotor:
        calls = 0
        def selection_for_frame(self, frame):
            self.calls += 1
            raise RuntimeError("V103 expression không có quyền chọn structured goal")
    motor = ExpressionMotor()
    driver, sessions, subject = _driver(tmp_path, motor=motor)
    try:
        adopted = driver._decide_and_start(subject.initiative_state.pending_intentions[0])
        assert adopted and adopted.job_id
        assert motor.calls == 0
    finally:
        driver.close()


def test_runtime_thuc_su_dung_action_executor_p0_p1(tmp_path, monkeypatch):
    from actions.executor import ActionExecutor
    monkeypatch.setenv("ERIS_WORK_LANE_DB", str(tmp_path / "work.sqlite"))
    monkeypatch.setenv("ERIS_AUTONOMY_V3_DB", str(tmp_path / "goal.sqlite"))
    subject = _subject()
    executor = ActionExecutor()
    host = SimpleNamespace(subject_service=subject, subject_mind_loop=MindLoop(subject=subject),
                           action_executor=executor, ai_model_router=None,
                           _wake_autonomous_loop=lambda _reason: None)
    executor.bind_cognitive_runtime(host)
    driver = ensure_identity_goal_driver(host)
    try:
        assert driver is not None
        assert driver.sessions is executor._parallel_work_service
        assert driver.pump()["status"] == "decision_scheduled"
        _until(driver, lambda: bool(driver.ledger.list_goals()))
        # Không có model online: Work Session dừng needs_review, không giả kết quả.
        goal = driver.ledger.list_goals()[0]
        assert goal.adoption_commit_id
    finally:
        driver.close()
