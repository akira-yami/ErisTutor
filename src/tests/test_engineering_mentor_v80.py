# -*- coding: utf-8 -*-
from __future__ import annotations

import json
from pathlib import Path

from runtime.engineering_mentor import EngineeringMentorService, UserEngineeringSkillModel


def _flow(*, findings=(), status="completed"):
    return {
        "schema": "eris.coding.flow.v1",
        "task_id": "task-1",
        "workspace": "C:/repo/eris",
        "status": status,
        "started_at": 35.0,
        "ended_at": 39.0,
        "changed_files": ["src/a.py"],
        "tests_seen": 1,
        "failed_test_runs": 0,
        "reviewer_seen": True,
        "phase_summaries": {"explorer": "Đã đọc call site và implementation."},
        "findings": [
            {"code": code, "severity": "warning", "message": code.replace("_", " ")}
            for code in findings
        ],
    }


def test_asking_to_learn_never_reduces_skill(tmp_path: Path):
    model = UserEngineeringSkillModel(path=tmp_path / "skills.json")
    before = model.snapshot()["skills"]["debugging.root_cause_reasoning"]["level"]
    signals = model.observe_user_turn(
        "Chỉ tôi cách debug lỗi này, tại sao flow đi từ service sang runtime vậy?",
        evidence_ref="turn:1",
        now=10.0,
    )
    after = model.snapshot()["skills"]["debugging.root_cause_reasoning"]["level"]
    assert "learning_intent" in signals
    assert after >= before


def test_blind_delegation_tracks_risk_but_not_skill_penalty(tmp_path: Path):
    model = UserEngineeringSkillModel(path=tmp_path / "skills.json")
    before = {k: v["level"] for k, v in model.snapshot()["skills"].items()}
    for i in range(4):
        signals = model.observe_user_turn("Fix code đi, làm luôn.", evidence_ref=f"turn:{i}", now=20.0 + i)
        assert "blind_execution_request" in signals
    after = model.snapshot()
    assert after["overreliance_risk"] > 0
    assert {k: v["level"] for k, v in after["skills"].items()} == before


def test_grounded_hypothesis_is_positive_debugging_evidence(tmp_path: Path):
    model = UserEngineeringSkillModel(path=tmp_path / "skills.json")
    before = model.snapshot()["skills"]["debugging.root_cause_reasoning"]
    signals = model.observe_user_turn(
        "Tôi nghĩ lỗi do runtime/coordinator.py gọi service sai lifecycle, có phải vậy không?",
        evidence_ref="turn:hypothesis",
        now=30.0,
    )
    after = model.snapshot()["skills"]["debugging.root_cause_reasoning"]
    assert "grounded_hypothesis" in signals
    assert after["level"] > before["level"]
    assert after["evidence_count"] == before["evidence_count"] + 1


def test_flow_problem_can_trigger_firm_challenge_without_claiming_user_is_weak(tmp_path: Path):
    mentor = EngineeringMentorService(path=tmp_path / "skills.json")
    mentor.observe_coding_flow(_flow(findings=("patch_before_inspection",)))
    decision = mentor.observe_user_turn("Lỗi code này giờ nên làm sao?", turn_id="turn:2", now=40.0)
    assert decision.mode == "challenge"
    assert decision.firmness == "firm"
    prompt = mentor.prompt_context("Lỗi code này giờ nên làm sao?", turn_id="turn:3", now=41.0)
    assert "patch_before_inspection" in prompt
    assert "Do not claim a skill weakness" in prompt


def test_repeated_blind_execution_can_trigger_warn_mode(tmp_path: Path):
    mentor = EngineeringMentorService(path=tmp_path / "skills.json")
    for i in range(4):
        mentor.observe_user_turn("Fix code đi, làm luôn.", turn_id=f"turn:{i}", now=100.0 + i)
    decision = mentor.observe_user_turn("Fix code đi, làm luôn.", turn_id="turn:5", now=106.0)
    assert decision.mode == "warn"
    assert decision.firmness == "firm"


def test_recent_flow_does_not_pollute_unrelated_chat(tmp_path: Path):
    mentor = EngineeringMentorService(path=tmp_path / "skills.json")
    mentor.observe_coding_flow(_flow(findings=("scope_expansion",)))
    decision = mentor.observe_user_turn("Tối nay ăn gì nhỉ?", turn_id="turn:food", now=200.0)
    assert decision.relevant is False
    assert mentor.prompt_context("Tối nay ăn gì nhỉ?", turn_id="turn:food2", now=201.0) == ""


def test_model_persists_without_raw_chat(tmp_path: Path):
    path = tmp_path / "skills.json"
    first = UserEngineeringSkillModel(path=path)
    first.observe_user_turn(
        "Tôi nghĩ lỗi do src/a.py gọi service sai, review diff với test giúp tôi.",
        evidence_ref="turn:persist",
        now=300.0,
    )
    raw = path.read_text(encoding="utf-8")
    assert "Tôi nghĩ lỗi" not in raw
    second = UserEngineeringSkillModel(path=path)
    snap = second.snapshot()
    assert snap["skills"]["debugging.root_cause_reasoning"]["evidence_count"] >= 1
    assert snap["skills"]["verification.diff_review"]["evidence_count"] >= 1


def test_agent_flow_never_changes_user_skill_or_overreliance(tmp_path: Path):
    model = UserEngineeringSkillModel(path=tmp_path / "skills.json")
    before = model.snapshot()
    model.note_agent_flow(_flow(findings=("repeated_test_failure", "scope_expansion")), now=400.0)
    after = model.snapshot()
    assert {k: v["level"] for k, v in after["skills"].items()} == {k: v["level"] for k, v in before["skills"].items()}
    assert after["overreliance_risk"] == before["overreliance_risk"]


def test_mentor_state_endpoint_is_read_only(tmp_path: Path):
    import urllib.error
    import urllib.request
    from runtime.eris_code_observer_link import ErisCodeObserverLink

    mentor = EngineeringMentorService(path=tmp_path / "skills.json")
    link = ErisCodeObserverLink(
        lambda workspace: {"protocol": "eris.link.v2", "workspace": workspace},
        mentor_provider=mentor.snapshot,
        port=0,
    )
    assert link.start()
    assert link._server is not None
    port = link._server.server_address[1]
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/v1/mentor/state", timeout=2) as response:
            payload = json.loads(response.read().decode("utf-8"))
            assert payload["schema"] == "eris.engineering_mentor.v1"

        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/v1/mentor/state",
            data=b"{}",
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            urllib.request.urlopen(req, timeout=2)
        except urllib.error.HTTPError as exc:
            assert exc.code == 405
        else:
            raise AssertionError("mentor state must be read-only")
    finally:
        link.stop()
