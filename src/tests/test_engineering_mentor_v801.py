# -*- coding: utf-8 -*-
from __future__ import annotations

from pathlib import Path

from runtime.engineering_mentor import EngineeringMentorService


def _mentor(tmp_path: Path) -> EngineeringMentorService:
    return EngineeringMentorService(
        path=tmp_path / "skills.json",
        runtime_path=tmp_path / "runtime.json",
        evaluation_path=tmp_path / "evaluation.jsonl",
    )


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
        "phase_summaries": {"explorer": "Đã đọc call site và implementation."},
        "findings": [{"code": code, "severity": "warning", "message": code}],
    }


def _delivery(mentor: EngineeringMentorService) -> dict:
    return mentor.snapshot()["last_decision"]


def test_same_proactive_lesson_is_suppressed_inside_cooldown(tmp_path: Path):
    mentor = _mentor(tmp_path)
    mentor.observe_coding_flow(_flow("patch_before_inspection", ended_at=100.0))
    first = mentor.prompt_context("Lỗi code này giờ sao?", turn_id="t1", now=101.0)
    second = mentor.prompt_context("Vụ code này còn gì nữa?", turn_id="t2", now=102.0)
    assert first
    assert not second
    assert _delivery(mentor)["suppression_reason"] == "same_lesson_cooldown"


def test_explicit_learning_bypasses_same_lesson_cooldown(tmp_path: Path):
    mentor = _mentor(tmp_path)
    mentor.observe_coding_flow(_flow("patch_before_inspection", ended_at=100.0))
    assert mentor.prompt_context("Lỗi code này giờ sao?", turn_id="t1", now=101.0)
    second = mentor.prompt_context(
        "Chỉ tôi cách debug lỗi này, tại sao phải đọc call flow trước?",
        turn_id="t2",
        now=102.0,
    )
    assert second
    assert _delivery(mentor)["suppression_reason"] == ""


def test_user_can_pause_proactive_mentoring_without_increasing_ai_risk(tmp_path: Path):
    mentor = _mentor(tmp_path)
    mentor.observe_coding_flow(_flow("scope_expansion", ended_at=100.0))
    assert mentor.prompt_context("Lỗi code này xem sao?", turn_id="t1", now=101.0)
    risk_before = mentor.model.snapshot()["overreliance_risk"]

    optout = mentor.prompt_context("Biết rồi, đừng giảng, cứ làm đi.", turn_id="t2", now=102.0)
    risk_after = mentor.model.snapshot()["overreliance_risk"]
    assert not optout
    assert _delivery(mentor)["suppression_reason"] == "user_paused_proactive_mentoring"
    assert risk_after == risk_before

    mentor.observe_coding_flow(_flow("no_structured_verification", ended_at=103.0))
    paused = mentor.prompt_context("Task code này ổn chưa?", turn_id="t3", now=104.0)
    assert not paused
    assert _delivery(mentor)["suppression_reason"] == "user_paused_proactive_mentoring"


def test_concise_feedback_makes_next_explicit_teaching_compact(tmp_path: Path):
    mentor = _mentor(tmp_path)
    mentor.observe_coding_flow(_flow("scope_expansion", ended_at=100.0))
    assert mentor.prompt_context("Lỗi code này xem sao?", turn_id="t1", now=101.0)
    assert not mentor.prompt_context("Nói ngắn thôi, dài quá.", turn_id="t2", now=102.0)

    prompt = mentor.prompt_context(
        "Giải thích tại sao task này scope bị phình?", turn_id="t3", now=103.0
    )
    assert prompt
    assert '"verbosity":"compact"' in prompt


def test_proactive_intervention_budget_limits_spam(tmp_path: Path):
    mentor = _mentor(tmp_path)
    codes = [
        "patch_before_inspection",
        "scope_expansion",
        "no_structured_verification",
        "review_not_observed",
    ]
    outputs = []
    for idx, code in enumerate(codes):
        now = 100.0 + idx * 2
        mentor.observe_coding_flow(_flow(code, ended_at=now))
        outputs.append(
            mentor.prompt_context("Task code này có vấn đề gì?", turn_id=f"t{idx}", now=now + 0.5)
        )
    assert all(outputs[:3])
    assert not outputs[3]
    assert _delivery(mentor)["suppression_reason"] == "proactive_intervention_budget"


def test_long_idle_starts_new_session_and_clears_cooldown(tmp_path: Path):
    mentor = _mentor(tmp_path)
    mentor.observe_coding_flow(_flow("patch_before_inspection", ended_at=100.0))
    assert mentor.prompt_context("Lỗi code này giờ sao?", turn_id="t1", now=101.0)

    later = 101.0 + 2 * 3600.0 + 10
    mentor.observe_coding_flow(_flow("patch_before_inspection", ended_at=later - 1))
    assert mentor.prompt_context("Lỗi code này giờ sao?", turn_id="t2", now=later)
    assert mentor.ledger.snapshot()["session_serial"] >= 2


def test_same_turn_is_idempotent_and_does_not_double_count_risk(tmp_path: Path):
    mentor = _mentor(tmp_path)
    mentor.observe_user_turn("Fix code đi, làm luôn.", turn_id="same", now=100.0)
    risk1 = mentor.model.snapshot()["overreliance_risk"]
    mentor.observe_user_turn("Fix code đi, làm luôn.", turn_id="same", now=100.5)
    risk2 = mentor.model.snapshot()["overreliance_risk"]
    assert risk2 == risk1


def test_evaluation_log_contains_metadata_not_raw_chat(tmp_path: Path):
    mentor = _mentor(tmp_path)
    mentor.observe_coding_flow(_flow("scope_expansion", ended_at=100.0))
    secret_sentence = "Lỗi code này có chuỗi bí mật KHONG_DUOC_LUU_RAW"
    assert mentor.prompt_context(secret_sentence, turn_id="eval-1", now=101.0)
    data = (tmp_path / "evaluation.jsonl").read_text(encoding="utf-8")
    assert "KHONG_DUOC_LUU_RAW" not in data
    assert '"event":"decision"' in data
    assert '"signature"' in data


def test_evaluation_log_rotates_instead_of_growing_forever(tmp_path: Path):
    mentor = _mentor(tmp_path)
    mentor.ledger.evaluation_max_bytes = 64
    evaluation = tmp_path / "evaluation.jsonl"
    evaluation.write_text("x" * 100, encoding="utf-8")
    mentor.observe_coding_flow(_flow("scope_expansion", ended_at=100.0))
    assert mentor.prompt_context("Lỗi code này xem sao?", turn_id="rotate", now=101.0)
    assert evaluation.exists()
    assert evaluation.with_suffix(".jsonl.1").exists()
    assert "x" * 100 not in evaluation.read_text(encoding="utf-8")


def test_mentor_snapshot_exposes_runtime_audit_without_raw_chat(tmp_path: Path):
    mentor = _mentor(tmp_path)
    mentor.observe_coding_flow(_flow("scope_expansion", ended_at=100.0))
    mentor.prompt_context("Lỗi code này xem sao?", turn_id="audit", now=101.0)
    snap = mentor.snapshot()
    assert snap["runtime"]["schema"] == "eris.engineering_mentor.runtime.v1"
    assert snap["last_policy_decision"]["relevant"] is True
    assert "Lỗi code này" not in str(snap)
