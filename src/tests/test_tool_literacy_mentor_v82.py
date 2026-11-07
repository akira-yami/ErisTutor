# -*- coding: utf-8 -*-
from __future__ import annotations

import json
from pathlib import Path

from runtime.engineering_mentor import EngineeringMentorService
from runtime.tool_literacy_mentor import EngineeringToolLiteracy


def _flow(code: str, *, ended_at: float = 100.0, changed: bool = True):
    return {
        "schema": "eris.coding.flow.v1",
        "task_id": f"task-{code}",
        "workspace": "C:/repo/eris",
        "status": "completed",
        "started_at": ended_at - 5,
        "ended_at": ended_at,
        "changed_files": ["src/a.py"] if changed else [],
        "tests_seen": 1,
        "failed_test_runs": 1 if code == "repeated_test_failure" else 0,
        "reviewer_seen": code != "review_not_observed",
        "phase_summaries": {"explorer": "Đã đọc call-site."},
        "findings": [{"code": code, "severity": "warning", "message": code}],
    }


def _mentor(tmp_path: Path) -> EngineeringMentorService:
    return EngineeringMentorService(
        path=tmp_path / "skills.json",
        runtime_path=tmp_path / "runtime.json",
        evaluation_path=tmp_path / "evaluation.jsonl",
        learning_path=tmp_path / "learning.json",
        tool_literacy_path=tmp_path / "tools.json",
    )


def test_flow_review_gap_recommends_git_diff_first(tmp_path: Path):
    tools = EngineeringToolLiteracy(path=tmp_path / "tools.json")
    directive = tools.plan(
        "Task code này ổn chưa?",
        decision_mode="hint",
        findings=("review_not_observed",),
        recent_flow=_flow("review_not_observed"),
        now=101.0,
    )
    assert directive.active
    assert directive.primary_tool == "git_diff"
    assert directive.supporting_tools == ("targeted_tests",)
    assert directive.question_vi


def test_repeated_test_failure_uses_targeted_test_not_generic_reading(tmp_path: Path):
    tools = EngineeringToolLiteracy(path=tmp_path / "tools.json")
    directive = tools.plan(
        "Test này cứ fail lại.",
        decision_mode="challenge",
        findings=("repeated_test_failure",),
        recent_flow=_flow("repeated_test_failure"),
        now=101.0,
    )
    assert directive.primary_tool == "targeted_tests"
    assert "debugger" in directive.supporting_tools


def test_performance_question_measures_with_profiler_first(tmp_path: Path):
    tools = EngineeringToolLiteracy(path=tmp_path / "tools.json")
    directive = tools.plan(
        "Game bị lag và tụt FPS, nên đọc file nào?",
        decision_mode="guide",
        now=100.0,
    )
    assert directive.primary_tool == "profiler"
    assert directive.issue_kind == "performance"


def test_godot_runtime_transform_or_collision_uses_remote_scene(tmp_path: Path):
    tools = EngineeringToolLiteracy(path=tmp_path / "tools.json")
    directive = tools.plan(
        "Godot slime bị lệch collision với sprite lúc runtime, kiểm tra sao?",
        decision_mode="guide",
        now=100.0,
    )
    assert directive.primary_tool == "godot_remote_scene"
    assert "godot_debugger" in directive.supporting_tools


def test_static_type_error_starts_from_vscode_diagnostics(tmp_path: Path):
    tools = EngineeringToolLiteracy(path=tmp_path / "tools.json")
    directive = tools.plan(
        "Type error trong VS Code, tôi nên xem gì trước?",
        decision_mode="explain",
        now=100.0,
    )
    assert directive.primary_tool == "vscode_diagnostics"


def test_question_about_debugger_is_not_counted_as_demonstrated_use(tmp_path: Path):
    path = tmp_path / "tools.json"
    tools = EngineeringToolLiteracy(path=path)
    events = tools.observe_user_turn("Debugger dùng sao để xem value?", evidence_ref="q1", now=100.0)
    assert not events
    assert tools.snapshot()["tools"]["debugger"]["demonstrated_count"] == 0


def test_real_tool_use_with_observed_result_counts_as_demonstration(tmp_path: Path):
    path = tmp_path / "tools.json"
    tools = EngineeringToolLiteracy(path=path)
    events = tools.observe_user_turn(
        "Tôi mở git diff và thấy hunk ở file shadow.gd đổi anchor ngoài scope.",
        evidence_ref="turn:diff-evidence",
        now=100.0,
    )
    assert "tool_demonstrated:git_diff" in events
    state = tools.snapshot()["tools"]["git_diff"]
    assert state["demonstrated_count"] == 1
    assert state["confidence"] > 0.05
    raw = path.read_text(encoding="utf-8")
    assert "shadow.gd" not in raw
    assert "turn:diff-evidence" in raw


def test_v82_prompt_contains_one_primary_tool_and_keeps_advisory_boundary(tmp_path: Path):
    mentor = _mentor(tmp_path)
    mentor.observe_coding_flow(_flow("review_not_observed", ended_at=100.0))
    prompt = mentor.prompt_context(
        "Chỉ tôi review task code này cho đúng.", turn_id="v82-1", now=101.0
    )
    assert prompt.startswith("ENGINEERING_EVIDENCE:")
    assert '"tool_literacy"' in prompt
    assert '"tool_id":"git_diff"' in prompt
    assert "advisory only" in prompt
    assert "Do not seize GitPilot control" in prompt


def test_compact_mode_limits_supporting_tool_list(tmp_path: Path):
    tools = EngineeringToolLiteracy(path=tmp_path / "tools.json")
    directive = tools.plan(
        "Runtime error stack trace này debug sao?",
        decision_mode="guide",
        compact=True,
        now=100.0,
    )
    assert directive.primary_tool == "stack_log"
    assert len(directive.supporting_tools) <= 1


def test_snapshot_contains_tool_projection_but_no_raw_chat(tmp_path: Path):
    mentor = _mentor(tmp_path)
    secret = "KHONG_LUU_CAU_NAY_987"
    mentor.prompt_context(
        f"Chỉ tôi dùng debugger đọc runtime error {secret}.", turn_id="safe", now=100.0
    )
    snap = mentor.snapshot()
    assert snap["tool_literacy"]["schema"] == "eris.engineering_tool_literacy.v1"
    assert secret not in json.dumps(snap, ensure_ascii=False)
