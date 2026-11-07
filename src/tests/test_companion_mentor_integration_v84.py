# -*- coding: utf-8 -*-
from __future__ import annotations

import json
from pathlib import Path

from runtime.companion_mentor_orchestrator import CompanionMentorRoleOrchestrator
from runtime.engineering_mentor import EngineeringMentorService, MentorDecision


def _flow(*, code: str = "", ended_at: float = 100.0) -> dict:
    findings = []
    if code:
        findings.append({"code": code, "severity": "warning", "message": code})
    return {
        "schema": "eris.coding.flow.v1",
        "task_id": "task-v84",
        "workspace": "C:/repo/eris",
        "status": "completed",
        "started_at": 90.0,
        "ended_at": ended_at,
        "changed_files": ["src/runtime/example.py"],
        "tests_seen": 1,
        "failed_test_runs": 0,
        "reviewer_seen": True,
        "phase_summaries": {"reviewer": "No blocking issue."},
        "findings": findings,
    }


def _mentor(tmp_path: Path) -> EngineeringMentorService:
    return EngineeringMentorService(
        path=tmp_path / "skills.json",
        runtime_path=tmp_path / "runtime.json",
        evaluation_path=tmp_path / "evaluation.jsonl",
        learning_path=tmp_path / "learning.json",
        tool_literacy_path=tmp_path / "tools.json",
    )


def test_explicit_learning_is_mentor_first_not_three_personas():
    router = CompanionMentorRoleOrchestrator()
    decision = MentorDecision(
        relevant=True,
        mode="guide",
        reason="user_requested_learning",
        user_signals=("learning_intent",),
    )
    role = router.route("Chỉ tôi trace call flow lỗi này.", decision=decision, flow=_flow(), now=101.0)
    assert role.primary_role == "mentor"
    assert role.supporting_functions[0] == "companion"
    assert "supervisor" in role.supporting_functions
    assert "MỘT Eris" in role.delivery_rule_vi
    assert role.max_role_transitions <= 2


def test_status_question_is_supervisor_first():
    router = CompanionMentorRoleOrchestrator()
    decision = MentorDecision(relevant=True, mode="discuss", reason="coding_discussion")
    role = router.route("GitPilot sửa gì rồi, xong chưa?", decision=decision, flow=_flow(), now=101.0)
    assert role.primary_role == "supervisor"
    assert role.surface_cue == "status_check"
    assert "companion" in role.supporting_functions
    assert "evidence" in role.opening_rule_vi.casefold()


def test_heated_bug_language_prefers_companion_first_without_psychologizing():
    router = CompanionMentorRoleOrchestrator()
    decision = MentorDecision(
        relevant=True,
        mode="challenge",
        reason="coding_flow_contains_teachable_failure",
        grounded_findings=("patch_before_inspection",),
    )
    role = router.route("dcm bug gì nữa vậy", decision=decision, flow=_flow(code="patch_before_inspection"), now=101.0)
    assert role.primary_role == "companion"
    assert role.surface_cue == "heated_language"
    assert role.supporting_functions == ("supervisor", "mentor")
    text = (role.opening_rule_vi + role.transition_rule_vi + role.delivery_rule_vi).casefold()
    assert "psychoanalyze" in text
    assert "persona" in text


def test_warn_mode_is_strict_mentor_with_supervisor_evidence():
    router = CompanionMentorRoleOrchestrator()
    decision = MentorDecision(
        relevant=True,
        mode="warn",
        firmness="firm",
        reason="repeated_blind_delegation_with_review_risk",
        user_signals=("blind_execution_request",),
        overreliance_risk=0.5,
    )
    role = router.route("cứ làm đi", decision=decision, flow=_flow(), now=101.0)
    assert role.primary_role == "mentor"
    assert role.mentor_intensity == "strict"
    assert "supervisor" in role.supporting_functions
    assert "không block" in role.transition_rule_vi.casefold()


def test_fast_execution_without_risk_stays_companion_not_classroom():
    router = CompanionMentorRoleOrchestrator()
    decision = MentorDecision(
        relevant=True,
        mode="discuss",
        reason="execution_request_without_learning_signal",
        user_signals=("blind_execution_request",),
    )
    role = router.route("làm luôn đi", decision=decision, flow={}, now=101.0)
    assert role.primary_role == "companion"
    assert role.mentor_intensity == "none"
    assert "mentor" not in role.supporting_functions
    assert "không giảng đạo" in role.opening_rule_vi.casefold()


def test_role_change_is_metadata_not_forced_stickiness():
    router = CompanionMentorRoleOrchestrator()
    status = MentorDecision(relevant=True, mode="discuss", reason="coding_discussion")
    first = router.route("xong chưa?", decision=status, flow=_flow(), now=100.0)
    learn = MentorDecision(
        relevant=True, mode="guide", reason="user_requested_learning", user_signals=("learning_intent",)
    )
    second = router.route("giải thích tại sao", decision=learn, flow=_flow(), now=110.0)
    assert first.primary_role == "supervisor"
    assert second.primary_role == "mentor"
    assert second.metadata["role_changed_recently"] is True


def test_v84_router_remains_legacy_utility_but_engineering_mentor_no_longer_uses_it_for_delivery(tmp_path: Path):
    mentor = _mentor(tmp_path)
    mentor.observe_coding_flow(_flow(code="patch_before_inspection", ended_at=100.0))
    prompt = mentor.prompt_context("dcm bug gì nữa vậy", turn_id="v84-1", now=101.0)
    assert prompt.startswith("ENGINEERING_EVIDENCE:")
    payload = json.loads(prompt.split("\n", 1)[1])
    assert payload["role"] == "technical_evidence_provider"
    assert payload["delivery_owner"] == "adaptive_companion"
    assert "role_orchestration" not in payload


def test_explicit_teaching_prompt_v84_is_learning_evidence_not_role_switch(tmp_path: Path):
    mentor = _mentor(tmp_path)
    mentor.observe_coding_flow(_flow(ended_at=100.0))
    prompt = mentor.prompt_context(
        "Chỉ tôi cách đọc call flow lỗi code này.", turn_id="v84-learn", now=101.0
    )
    payload = json.loads(prompt.split("\n", 1)[1])
    assert payload["decision"]["reason"] == "user_requested_learning"
    assert payload["delivery_owner"] == "adaptive_companion"
    assert "kind" in payload["adaptive_learning"]["directive"]


def test_non_engineering_chat_does_not_inject_engineering_evidence(tmp_path: Path):
    mentor = _mentor(tmp_path)
    assert mentor.prompt_context("Hôm nay ăn gì?", turn_id="v84-normal", now=101.0) == ""
    assert mentor.snapshot()["last_role_directive"]["active"] is False


def test_snapshot_marks_v84_role_router_deprecated_without_raw_user_text(tmp_path: Path):
    mentor = _mentor(tmp_path)
    secret = "dcm bug gì nữa vậy KHONG_LUU_RAW_V84"
    mentor.observe_coding_flow(_flow(code="scope_expansion", ended_at=100.0))
    mentor.prompt_context(secret, turn_id="v84-private", now=101.0)
    snap = mentor.snapshot()
    assert snap["role_orchestration"]["deprecated"] is True
    assert snap["role_orchestration"]["delivery_owner"] == "adaptive_companion"
    assert "KHONG_LUU_RAW_V84" not in str(snap)
    audit = (tmp_path / "evaluation.jsonl").read_text(encoding="utf-8")
    assert '"event":"decision"' in audit
    assert "KHONG_LUU_RAW_V84" not in audit


def test_non_engineering_turn_keeps_legacy_role_projection_inactive(tmp_path: Path):
    mentor = _mentor(tmp_path)
    mentor.observe_coding_flow(_flow(ended_at=100.0))
    assert mentor.prompt_context("GitPilot xong chưa?", turn_id="tech", now=101.0)
    assert mentor.snapshot()["last_role_directive"]["active"] is False
    assert mentor.prompt_context("Hôm nay ăn gì?", turn_id="normal", now=102.0) == ""
    assert mentor.snapshot()["last_role_directive"]["active"] is False
