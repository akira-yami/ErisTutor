# -*- coding: utf-8 -*-
from pathlib import Path

from runtime.capability_projection import CapabilityProjectionService
from runtime.engineering_mentor import EngineeringMentorService, UserEngineeringSkillModel


def test_duplicate_turn_ref_does_not_inflate_independent_evidence(tmp_path: Path):
    model = UserEngineeringSkillModel(path=tmp_path / "skills.json")
    text = "Tôi nghĩ lỗi do runtime/coordinator.py gọi service sai lifecycle."
    model.observe_user_turn(text, evidence_ref="turn:same", now=100.0)
    model.observe_user_turn(text, evidence_ref="turn:same", now=101.0)
    skill = model.snapshot()["skills"]["debugging.root_cause_reasoning"]
    assert skill["evidence_count"] == 1
    assert skill["independent_evidence_count"] == 1


def test_all_declared_capability_axes_have_real_positive_paths(tmp_path: Path):
    model = UserEngineeringSkillModel(path=tmp_path / "skills.json")
    model.observe_user_turn("Tôi nghĩ lỗi nằm ở src/runtime.py function dispatch().", evidence_ref="loc", now=1)
    model.observe_user_turn("Boundary layer nào chịu trách nhiệm giữa service và runtime?", evidence_ref="arch", now=2)
    model.observe_user_turn("Scope task này chỉ sửa src/a.py, đừng đụng module khác.", evidence_ref="scope", now=3)
    model.observe_user_turn("Review evidence GitPilot và diff trước khi tin output agent.", evidence_ref="ev", now=4)
    snap = model.snapshot()["skills"]
    assert snap["debugging.failure_localization"]["evidence_count"] >= 1
    assert snap["architecture.system_boundaries"]["evidence_count"] >= 1
    assert snap["ai_operation.task_scoping"]["evidence_count"] >= 1
    assert snap["ai_operation.evidence_review"]["evidence_count"] >= 1


def test_candidate_misconception_becomes_counter_evidence(tmp_path: Path):
    mentor = EngineeringMentorService(
        path=tmp_path / "skills.json",
        runtime_path=tmp_path / "runtime.json",
        evaluation_path=tmp_path / "evaluation.jsonl",
        learning_path=tmp_path / "learning.json",
        tool_literacy_path=tmp_path / "tools.json",
    )
    # Seed real verification evidence first.
    mentor.observe_user_turn("Review diff và test task code này.", turn_id="seed", now=10)
    before = mentor.model.snapshot()["skills"]["verification.testing"]["level"]
    mentor.observe_user_turn("Tests pass là đủ, khỏi review diff.", turn_id="mis", now=20)
    after = mentor.model.snapshot()["skills"]["verification.testing"]
    assert after["counter_evidence_count"] >= 1
    assert after["level"] < before


def test_group_score_hidden_when_only_one_of_three_dimensions_is_observed(tmp_path: Path):
    state = {
        "model": {"skills": {
            "debugging.failure_localization": {"level": .8, "confidence": .7, "evidence_count": 3},
            "debugging.call_flow_tracing": {"level": .5, "confidence": .05, "evidence_count": 0},
            "debugging.root_cause_reasoning": {"level": .5, "confidence": .05, "evidence_count": 0},
        }},
        "tool_literacy": {"tools": {}}, "mentor_calibration": {"metrics": {}},
    }
    snap = CapabilityProjectionService(history_path=tmp_path / "h.jsonl").build(state, now=1000)
    group = next(x for x in snap["nhom_nang_luc"] if x["id"] == "debugging")
    assert group["da_du_bang_chung"] is False
    assert group["phan_tram"] is None
    assert group["ky_nang_da_quan_sat"] == 1
    assert group["tong_ky_nang"] == 3


def test_reasoning_ownership_risk_decays_over_time(tmp_path: Path):
    model = UserEngineeringSkillModel(path=tmp_path / "skills.json")
    for i in range(4):
        model.observe_user_turn("Fix code đi, làm luôn.", evidence_ref=f"b:{i}", now=100+i)
    before = model.overreliance_risk
    model.last_risk_calibrated_at = 104
    model.decay_stale_confidence(now=104 + 30*86400)
    assert 0 < model.overreliance_risk < before
