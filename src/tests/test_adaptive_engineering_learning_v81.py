# -*- coding: utf-8 -*-
from __future__ import annotations

import json
from pathlib import Path

from runtime.adaptive_engineering_learning import (
    AdaptiveEngineeringLearning,
    LearningDirective,
)
from runtime.engineering_mentor import EngineeringMentorService


def _flow(code: str, *, ended_at: float = 100.0):
    return {
        "schema": "eris.coding.flow.v1",
        "task_id": f"task-{code}",
        "workspace": "C:/repo/eris",
        "status": "completed",
        "started_at": ended_at - 4,
        "ended_at": ended_at,
        "changed_files": ["src/a.py"],
        "tests_seen": 1,
        "failed_test_runs": 0,
        "reviewer_seen": True,
        "phase_summaries": {"explorer": "Đã inspect call site."},
        "findings": [{"code": code, "severity": "warning", "message": code}],
    }


def _mentor(tmp_path: Path) -> EngineeringMentorService:
    return EngineeringMentorService(
        path=tmp_path / "skills.json",
        runtime_path=tmp_path / "runtime.json",
        evaluation_path=tmp_path / "evaluation.jsonl",
        learning_path=tmp_path / "learning.json",
    )


def test_agent_flow_creates_exposure_but_never_user_mastery(tmp_path: Path):
    learning = AdaptiveEngineeringLearning(path=tmp_path / "learning.json")
    learning.note_coding_flow(_flow("patch_before_inspection", ended_at=100.0))
    state = learning.snapshot()["concepts"]["debugging.inspect_before_patch"]
    assert state["exposures"] == 1
    assert state["demonstrated_count"] == 0
    assert state["misconception_count"] == 0
    assert state["mastery"] == 0.30


def test_self_report_understood_is_not_mastery_evidence(tmp_path: Path):
    learning = AdaptiveEngineeringLearning(path=tmp_path / "learning.json")
    directive = LearningDirective(
        "teach_back", "debugging.inspect_before_patch", "x", "due", "ask", True
    )
    learning.register_prompt(directive, session_serial=1, now=100.0)
    before = learning.snapshot()["concepts"]["debugging.inspect_before_patch"]["mastery"]
    learning.observe_user_turn(
        "Ừ hiểu rồi.", feedback=("mentor_understood",), evidence_ref="t2", now=101.0
    )
    after = learning.snapshot()["concepts"]["debugging.inspect_before_patch"]
    assert after["mastery"] == before
    assert after["self_report_count"] == 1
    assert after["demonstrated_count"] == 0


def test_grounded_hypothesis_is_real_learning_evidence(tmp_path: Path):
    learning = AdaptiveEngineeringLearning(path=tmp_path / "learning.json")
    learning.observe_user_turn(
        "Tôi nghĩ lỗi do src/a.py gọi ShadowManager sai.",
        signals=("grounded_hypothesis",),
        evidence_ref="turn:hypothesis",
        now=200.0,
    )
    snap = learning.snapshot()["concepts"]
    assert snap["debugging.trace_with_evidence"]["demonstrated_count"] == 1
    assert snap["debugging.root_cause_before_retry"]["demonstrated_count"] == 1
    assert snap["debugging.trace_with_evidence"]["mastery"] > 0.30


def test_misconception_assertion_is_candidate_but_question_is_not(tmp_path: Path):
    learning = AdaptiveEngineeringLearning(path=tmp_path / "learning.json")
    events = learning.observe_user_turn(
        "Test pass là xong, khỏi review diff.",
        signals=("verification_intent",),
        evidence_ref="turn:wrong",
        now=300.0,
    )
    assert "candidate_misconception:tests_pass_means_done" in events
    state = learning.snapshot()["concepts"]["verification.targeted_tests"]
    assert state["misconception_count"] == 1
    # Câu sai có keyword verification không được tự cộng mastery cho chính concept đó.
    assert state["demonstrated_count"] == 0

    other = AdaptiveEngineeringLearning(path=tmp_path / "question.json")
    events2 = other.observe_user_turn(
        "Test pass là xong, khỏi review diff đúng không?",
        signals=("verification_intent",),
        evidence_ref="turn:question",
        now=301.0,
    )
    assert not any(x.startswith("candidate_misconception:") for x in events2)


def test_spaced_review_only_appears_for_related_current_focus(tmp_path: Path):
    learning = AdaptiveEngineeringLearning(path=tmp_path / "learning.json")
    learning.note_coding_flow(_flow("patch_before_inspection", ended_at=100.0))
    due = 100.0 + 24 * 3600.0 + 1
    directive = learning.plan(
        decision_mode="challenge",
        focus_skills=("debugging.failure_localization",),
        findings=("patch_before_inspection",),
        signals=(),
        session_serial=1,
        now=due,
    )
    assert directive.kind == "teach_back"
    assert directive.concept_id == "debugging.inspect_before_patch"
    assert directive.due is True

    unrelated = learning.plan(
        decision_mode="challenge",
        focus_skills=("architecture.system_boundaries",),
        findings=(),
        signals=(),
        session_serial=1,
        now=due,
    )
    assert unrelated.kind == "none"


def test_adaptive_prompt_is_limited_and_same_concept_has_cooldown(tmp_path: Path):
    learning = AdaptiveEngineeringLearning(path=tmp_path / "learning.json")
    learning.note_coding_flow(_flow("patch_before_inspection", ended_at=100.0))
    now = 100.0 + 24 * 3600.0 + 1
    first = learning.plan(
        decision_mode="challenge",
        focus_skills=("debugging.failure_localization",),
        findings=("patch_before_inspection",),
        session_serial=4,
        now=now,
    )
    assert first.active
    learning.register_prompt(first, session_serial=4, now=now)
    second = learning.plan(
        decision_mode="challenge",
        focus_skills=("debugging.failure_localization",),
        findings=("patch_before_inspection",),
        session_serial=4,
        now=now + 10,
    )
    assert not second.active


def test_teachback_application_evidence_updates_prompted_concept(tmp_path: Path):
    learning = AdaptiveEngineeringLearning(path=tmp_path / "learning.json")
    learning.register_prompt(
        LearningDirective("teach_back", "debugging.inspect_before_patch", "x", "due", "ask", True),
        session_serial=1,
        now=100.0,
    )
    events = learning.observe_user_turn(
        "Tôi sẽ đọc call site trước rồi mới sửa patch.",
        evidence_ref="turn:answer",
        now=110.0,
    )
    state = learning.snapshot()["concepts"]["debugging.inspect_before_patch"]
    assert "teachback_evidence" in events
    assert state["demonstrated_count"] == 1
    assert state["mastery"] > 0.30
    assert state["next_review_at"] > 110.0


def test_v81_prompt_includes_adaptive_directive_but_keeps_coding_boundary(tmp_path: Path):
    mentor = _mentor(tmp_path)
    mentor.observe_coding_flow(_flow("patch_before_inspection", ended_at=100.0))
    prompt = mentor.prompt_context(
        "Chỉ tôi cách debug lỗi code này, tại sao phải inspect trước?",
        turn_id="t1",
        now=101.0,
    )
    assert prompt.startswith("ENGINEERING_EVIDENCE:")
    assert '"adaptive_learning"' in prompt
    assert '"kind":"micro_practice"' in prompt
    assert "Do not seize GitPilot control" in prompt


def test_candidate_misconception_gets_check_not_hard_user_label(tmp_path: Path):
    mentor = _mentor(tmp_path)
    prompt = mentor.prompt_context(
        "Test pass là xong, khỏi review diff.", turn_id="wrong-1", now=100.0
    )
    assert prompt
    assert '"reason":"candidate_misconception_needs_check"' in prompt
    assert '"kind":"correct_candidate_misconception"' in prompt
    assert "candidate misconception" in prompt.casefold()


def test_learning_projection_persists_without_raw_chat(tmp_path: Path):
    path = tmp_path / "learning.json"
    learning = AdaptiveEngineeringLearning(path=path)
    secret = "KHONG_DUOC_LUU_RAW_123"
    learning.observe_user_turn(
        f"Tôi nghĩ {secret} lỗi do src/a.py gọi Service sai.",
        signals=("grounded_hypothesis",),
        evidence_ref="turn:safe-ref",
        now=400.0,
    )
    raw = path.read_text(encoding="utf-8")
    assert secret not in raw
    assert "turn:safe-ref" in raw
    reloaded = AdaptiveEngineeringLearning(path=path)
    assert reloaded.snapshot()["concepts"]["debugging.trace_with_evidence"]["demonstrated_count"] == 1


def test_mentor_snapshot_exposes_learning_state_read_only_projection(tmp_path: Path):
    mentor = _mentor(tmp_path)
    mentor.observe_coding_flow(_flow("scope_expansion", ended_at=100.0))
    snap = mentor.snapshot()
    assert snap["adaptive_learning"]["schema"] == "eris.engineering_learning.v1"
    assert "ai.scope_before_execution" in snap["adaptive_learning"]["concepts"]
    assert "source code" not in json.dumps(snap, ensure_ascii=False).casefold()
