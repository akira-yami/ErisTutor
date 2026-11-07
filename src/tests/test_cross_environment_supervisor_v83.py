# -*- coding: utf-8 -*-
from __future__ import annotations

import json
from pathlib import Path

from runtime.cross_environment_supervisor import CrossEnvironmentSupervisor
from runtime.engineering_mentor import EngineeringMentorService
from runtime.tool_literacy_mentor import EngineeringToolLiteracy


def _flow(*, ended_at: float = 100.0, files=None, status: str = "completed") -> dict:
    return {
        "schema": "eris.coding.flow.v1",
        "task_id": "task-v83",
        "workspace": "C:/repo/game",
        "status": status,
        "started_at": 90.0,
        "ended_at": ended_at if status != "running" else 0.0,
        "changed_files": list(files or ["scripts/slime_shadow.gd"]),
        "tests_seen": 1,
        "failed_test_runs": 0,
        "reviewer_seen": True,
        "phase_summaries": {"reviewer": "Không có blocking issue tĩnh."},
        "findings": [],
    }


def _env(
    *,
    app: str = "Godot Engine",
    process: str = "godot.exe",
    object_name: str = "Game Debug",
    task_hint: str = "inspecting runtime node state",
    last_outcome: str = "unknown",
    age: float = 1.0,
    confidence: float = 0.92,
    events=None,
) -> dict:
    return {
        "schema": "environment_awareness.v1",
        "fresh": age <= 120.0,
        "age_seconds": age,
        "confidence": confidence,
        "focused_app": app,
        "focused_process": process,
        "focused_object": object_name,
        "activity": "debugging",
        "task_hint": task_hint,
        "phase": "runtime_validation",
        "last_action": "run game",
        "last_outcome": last_outcome,
        "evidence_refs": ["env:focus", "env:task"],
        "recent_events": list(events or []),
    }


def _mentor(tmp_path: Path) -> EngineeringMentorService:
    return EngineeringMentorService(
        path=tmp_path / "skills.json",
        runtime_path=tmp_path / "runtime.json",
        evaluation_path=tmp_path / "evaluation.jsonl",
        learning_path=tmp_path / "learning.json",
        tool_literacy_path=tmp_path / "tools.json",
    )


def test_stale_environment_is_not_correlated_with_recent_code():
    sup = CrossEnvironmentSupervisor()
    directive = sup.analyze(
        "vẫn lỗi",
        coding_flow=_flow(),
        environment=_env(age=240.0, last_outcome="runtime error"),
        now=101.0,
    )
    assert not directive.active


def test_godot_runtime_state_after_gd_change_prefers_remote_scene():
    sup = CrossEnvironmentSupervisor()
    directive = sup.analyze(
        "bóng slime vẫn lệch lúc runtime",
        coding_flow=_flow(files=["scripts/shadow_manager.gd"]),
        environment=_env(task_hint="compare node transform collision runtime"),
        now=101.0,
    )
    assert directive.active
    assert directive.correlation_kind == "runtime_state_after_change"
    assert directive.primary_tool_hint == "godot_remote_scene"
    assert "git_diff" in directive.supporting_tool_hints


def test_godot_failure_after_change_prefers_engine_debugger_not_causal_claim():
    sup = CrossEnvironmentSupervisor()
    directive = sup.analyze(
        "game vẫn lỗi",
        coding_flow=_flow(),
        environment=_env(last_outcome="runtime error after reproduce"),
        now=101.0,
    )
    payload = directive.to_dict()
    assert directive.primary_tool_hint == "godot_debugger"
    assert directive.correlation_kind == "runtime_failure_after_change"
    assert "không phải bằng chứng nhân quả" in payload["epistemic_rule"]
    assert "patch gây" not in directive.rationale_vi.casefold()


def test_performance_cross_environment_prefers_profiler():
    sup = CrossEnvironmentSupervisor()
    directive = sup.analyze(
        "sau patch game tụt FPS",
        coding_flow=_flow(),
        environment=_env(task_hint="runtime frame spike"),
        now=101.0,
    )
    assert directive.primary_tool_hint == "profiler"
    assert directive.correlation_kind == "post_change_performance_validation"


def test_vscode_review_question_correlates_recent_change_to_git_diff():
    sup = CrossEnvironmentSupervisor()
    directive = sup.analyze(
        "GitPilot sửa xong chưa, đổi gì vậy?",
        coding_flow=_flow(files=["src/a.py", "src/b.py"]),
        environment=_env(
            app="Visual Studio Code",
            process="Code.exe",
            object_name="a.py - Visual Studio Code",
            task_hint="reviewing code changes",
        ),
        now=101.0,
    )
    assert directive.active
    assert directive.primary_tool_hint == "git_diff"
    assert directive.correlation_kind == "ide_change_review"


def test_environment_hint_only_fills_gap_and_explicit_tool_intent_still_wins(tmp_path: Path):
    tools = EngineeringToolLiteracy(path=tmp_path / "tools.json")
    hint = CrossEnvironmentSupervisor().analyze(
        "game lag thì profiler đọc sao?",
        coding_flow=_flow(),
        environment=_env(task_hint="collision transform runtime"),
        now=101.0,
    ).to_dict()
    directive = tools.plan(
        "game lag thì profiler đọc sao?",
        decision_mode="guide",
        recent_flow=_flow(),
        environment_hint=hint,
        now=101.0,
    )
    assert directive.primary_tool == "profiler"
    assert directive.issue_kind == "performance"


def test_mentor_v83_uses_world_environment_to_bridge_generic_runtime_question(tmp_path: Path):
    mentor = _mentor(tmp_path)
    mentor.observe_coding_flow(_flow(ended_at=100.0))
    prompt = mentor.prompt_context(
        "Chỉ tôi kiểm tra vì sao nó vẫn lệch sau khi sửa.",
        turn_id="v83-mentor",
        now=101.0,
        environment=_env(task_hint="runtime node transform collision"),
    )
    assert prompt.startswith("ENGINEERING_EVIDENCE:")
    payload = json.loads(prompt.split("\n", 1)[1])
    assert payload["cross_environment"]["authority"] == "evidence_only"
    assert payload["cross_environment"]["directive"]["active"] is True
    assert payload["cross_environment"]["directive"]["primary_tool_hint"] == "godot_remote_scene"
    assert payload["tool_literacy"]["directive"]["primary"]["tool_id"] == "godot_remote_scene"
    assert "Correlation" in payload["cross_environment"]["rule"]
    assert "Do not seize GitPilot control" in prompt


def test_mentor_without_environment_keeps_v82_behavior(tmp_path: Path):
    mentor = _mentor(tmp_path)
    mentor.observe_coding_flow(_flow(ended_at=100.0, files=["src/a.py"]))
    prompt = mentor.prompt_context(
        "Chỉ tôi review task này.", turn_id="no-env", now=101.0
    )
    payload = json.loads(prompt.split("\n", 1)[1])
    assert payload["cross_environment"]["directive"]["active"] is False
    assert payload["tool_literacy"]["directive"]["primary"]["tool_id"] == "git_diff"


def test_orchestrator_passes_canonical_world_state_projection_to_mentor():
    source = Path(__file__).parents[1] / "runtime" / "ai_orchestrated" / "orchestrator.py"
    text = source.read_text(encoding="utf-8")
    assert "environment_compatibility_view" in text
    assert "environment=mentor_environment" in text
    assert "ensure_world_state_service" in text
    assert "from runtime.world_state import attach_world_state_projection" in text
