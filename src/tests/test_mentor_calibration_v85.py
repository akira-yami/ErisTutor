# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import urllib.error
import urllib.request
from pathlib import Path

from runtime.adaptive_companion import AdaptiveCompanionService
from runtime.engineering_mentor import EngineeringMentorService, UserEngineeringSkillModel
from runtime.eris_code_observer_link import ErisCodeObserverLink
from runtime.mentor_calibration import MentorCalibrationService, build_user_visible_mentor_profile


def _mentor(tmp_path: Path) -> EngineeringMentorService:
    return EngineeringMentorService(path=tmp_path / "skills.json")




class _CompanionOwner:
    """Owner tối thiểu để test đúng pipeline delivery mới của Companion."""

    def __init__(self, mentor: EngineeringMentorService) -> None:
        self.engineering_mentor_service = mentor
        self.life_horizon_manager = None
        self.logger = None


def _deliver_teaching_turn(
    mentor: EngineeringMentorService,
    tmp_path: Path,
    *,
    text: str = "Dạy tôi scope task code này từ gốc.",
    turn_id: str = "i1",
    now: float = 101.0,
) -> AdaptiveCompanionService:
    """Mô phỏng runtime thật: Mentor chuẩn bị evidence, Companion mới commit delivery.

    V85 cũ từng ghi calibration ngay khi Mentor *đề xuất* dạy. Sau consolidation,
    chỉ một intervention thật sự được Companion chọn mới được tính vào calibration.
    """
    assert mentor.prompt_context(text, turn_id=turn_id, now=now)
    companion = AdaptiveCompanionService(
        owner=_CompanionOwner(mentor),
        state_path=tmp_path / "adaptive_companion_test.json",
    )
    directive = companion.observe_user_turn(text, turn_id=turn_id, now=now + 0.01)
    assert directive.stance == "teach_guided"
    assert directive.teaching_permission == "guided"
    return companion

def _flow(code: str, *, ended_at: float = 100.0) -> dict:
    return {
        "schema": "eris.coding.flow.v1",
        "task_id": "task-v85",
        "workspace": "C:/repo/eris",
        "status": "completed",
        "started_at": ended_at - 5,
        "ended_at": ended_at,
        "changed_files": ["src/a.py"],
        "tests_seen": 1,
        "failed_test_runs": 0,
        "reviewer_seen": True,
        "phase_summaries": {"explorer": "inspected", "reviewer": "reviewed"},
        "findings": [{"code": code, "severity": "warning", "message": code}] if code else [],
    }


def test_calibration_helpful_outcome_uses_explicit_feedback_only(tmp_path: Path):
    mentor = _mentor(tmp_path)
    mentor.observe_coding_flow(_flow("scope_expansion"))
    _deliver_teaching_turn(mentor, tmp_path)
    mentor.observe_user_turn("Ừ đúng rồi, tìm ra rồi.", turn_id="r1", now=102.0)
    snap = mentor.calibration.snapshot()
    assert snap["metrics"]["helpful"] == 1
    assert snap["metrics"]["interventions"] == 1


def test_topic_change_does_not_fake_ignored_or_helpful_outcome(tmp_path: Path):
    mentor = _mentor(tmp_path)
    mentor.observe_coding_flow(_flow("scope_expansion"))
    _deliver_teaching_turn(mentor, tmp_path)
    mentor.observe_user_turn("Tối nay ăn gì?", turn_id="r1", now=102.0)
    snap = mentor.calibration.snapshot()
    assert snap["pending_count"] == 1
    assert snap["metrics"]["helpful"] == 0
    assert snap["metrics"]["unknown"] == 0


def test_expired_feedback_becomes_unknown_not_failure(tmp_path: Path):
    cal = MentorCalibrationService(path=tmp_path / "cal.json")
    cal.register_intervention(
        intervention_id="i1", mode="hint", reason="x", focus_skills=("debugging.failure_localization",), now=100.0
    )
    _ = cal.directive(now=100.0 + cal.feedback_window_seconds + 1)
    snap = cal.snapshot()
    assert snap["metrics"]["unknown"] == 1
    assert snap["pending_count"] == 0


def test_user_correction_lowers_skill_confidence_not_level(tmp_path: Path):
    model = UserEngineeringSkillModel(path=tmp_path / "skills.json")
    model.observe_user_turn(
        "Tôi nghĩ lỗi do src/a.py gọi service sai.", evidence_ref="e1", now=100.0
    )
    before = model.snapshot()["skills"]["debugging.root_cause_reasoning"]
    model.apply_user_correction(
        ["debugging.root_cause_reasoning"], kind="user_corrected_eris", evidence_ref="corr", now=101.0
    )
    after = model.snapshot()["skills"]["debugging.root_cause_reasoning"]
    assert after["level"] == before["level"]
    assert after["confidence"] < before["confidence"]


def test_user_already_knew_is_not_mastery_proof(tmp_path: Path):
    model = UserEngineeringSkillModel(path=tmp_path / "skills.json")
    before = model.snapshot()["skills"]["debugging.failure_localization"]
    model.apply_user_correction(
        ["debugging.failure_localization"], kind="user_already_knew", evidence_ref="corr", now=101.0
    )
    after = model.snapshot()["skills"]["debugging.failure_localization"]
    assert after["level"] == before["level"]
    assert after["evidence_count"] == before["evidence_count"]


def test_stale_confidence_decays_without_changing_level(tmp_path: Path):
    model = UserEngineeringSkillModel(path=tmp_path / "skills.json")
    model.observe_user_turn(
        "Tôi nghĩ lỗi do src/a.py gọi service sai.", evidence_ref="e1", now=100.0
    )
    before = model.snapshot()["skills"]["debugging.root_cause_reasoning"]
    model.decay_stale_confidence(now=100.0 + 90 * 86400.0)
    after = model.snapshot()["skills"]["debugging.root_cause_reasoning"]
    assert after["level"] == before["level"]
    assert after["confidence"] < before["confidence"]


def test_repeated_corrections_switch_calibration_to_cautious(tmp_path: Path):
    cal = MentorCalibrationService(path=tmp_path / "cal.json")
    for idx in range(3):
        cal.register_intervention(
            intervention_id=f"i{idx}", mode="challenge", reason="x", focus_skills=("debugging.failure_localization",), now=100.0 + idx * 10
        )
        text = "Sai rồi, không phải vậy." if idx < 2 else "Đúng rồi, tìm ra rồi."
        cal.observe_user_response(text, now=101.0 + idx * 10)
    directive = cal.directive(now=140.0)
    assert directive.confidence_mode == "cautious"
    assert directive.recent_correction_rate >= 0.66


def test_repeated_annoyance_requests_reduce_proactive_friction(tmp_path: Path):
    cal = MentorCalibrationService(path=tmp_path / "cal.json")
    for idx in range(4):
        cal.register_intervention(
            intervention_id=f"i{idx}", mode="hint", reason="x", proactive=True, now=100.0 + idx * 10
        )
        feedback = ("mentor_opt_out",) if idx < 2 else ()
        text = "đừng giảng" if idx < 2 else "đúng rồi, tìm ra rồi"
        cal.observe_user_response(text, feedback=feedback, now=101.0 + idx * 10)
    directive = cal.directive(now=150.0)
    assert directive.reduce_proactive_friction is True
    assert directive.recent_annoyance_rate >= 0.5


def test_profile_hides_internal_notes_and_marks_low_confidence_unknown(tmp_path: Path):
    model = UserEngineeringSkillModel(path=tmp_path / "skills.json")
    snap = model.snapshot()
    profile = build_user_visible_mentor_profile(snap, {"metrics": {}} , now=100.0)
    assert profile["schema"] == "eris.mentor_profile.v1"
    assert profile["skills"][0]["label_vi"] == "chưa đủ evidence"
    assert "notes" not in json.dumps(profile, ensure_ascii=False)
    assert profile["controls"]["loopback_write_api_exposed"] is False


def test_in_process_reset_is_supported_without_write_http_api(tmp_path: Path):
    mentor = _mentor(tmp_path)
    mentor.model.observe_user_turn(
        "Tôi nghĩ lỗi do src/a.py gọi service sai.", evidence_ref="e1", now=100.0
    )
    assert mentor.reset_skill_estimate("debugging.root_cause_reasoning") is True
    raw = mentor.model.snapshot()["skills"]["debugging.root_cause_reasoning"]
    assert raw["evidence_count"] == 0
    assert raw["level"] == 0.5


def test_mentor_profile_endpoint_is_read_only(tmp_path: Path):
    mentor = _mentor(tmp_path)
    link = ErisCodeObserverLink(
        lambda workspace: {"protocol": "eris.link.v2", "workspace": workspace},
        mentor_provider=mentor.snapshot,
        port=0,
    )
    assert link.start()
    assert link._server is not None
    port = link._server.server_address[1]
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/v1/mentor/profile", timeout=2) as response:
            payload = json.loads(response.read().decode("utf-8"))
            assert payload["schema"] == "eris.mentor_profile.v1"
            assert payload["controls"]["loopback_write_api_exposed"] is False
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/v1/mentor/profile",
            data=b"{}",
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            urllib.request.urlopen(req, timeout=2)
        except urllib.error.HTTPError as exc:
            assert exc.code == 405
        else:
            raise AssertionError("mentor profile must stay read-only on Eris Link")
    finally:
        link.stop()


def test_calibration_persistence_never_contains_raw_user_text(tmp_path: Path):
    mentor = _mentor(tmp_path)
    mentor.observe_coding_flow(_flow("scope_expansion"))
    _deliver_teaching_turn(mentor, tmp_path)
    secret = "Sai rồi, không phải vậy KHONG_LUU_RAW_V85"
    mentor.observe_user_turn(secret, turn_id="r1", now=102.0)
    raw = (tmp_path / "engineering_mentor_calibration.json").read_text(encoding="utf-8")
    assert "KHONG_LUU_RAW_V85" not in raw
    assert "user_corrected_eris" in raw


def test_v85_prompt_exposes_calibration_without_new_coding_authority(tmp_path: Path):
    mentor = _mentor(tmp_path)
    mentor.observe_coding_flow(_flow("patch_before_inspection"))
    prompt = mentor.prompt_context("Lỗi code này giờ sao?", turn_id="i1", now=101.0)
    assert "mentor_calibration" in prompt
    assert "feedback user" in prompt.casefold()
    assert "Do not seize GitPilot control" in prompt


def test_explicit_verification_stays_evidence_hint_after_delivery_moves_to_companion(tmp_path: Path):
    mentor = _mentor(tmp_path)
    prompt = mentor.prompt_context("Review diff và test code này giúp tôi.", turn_id="verify", now=101.0)
    assert prompt.startswith("ENGINEERING_EVIDENCE:")
    payload = json.loads(prompt.split("\n", 1)[1])
    assert payload["decision"]["reason"] == "user_requested_verification"
    assert payload["delivery_owner"] == "adaptive_companion"
    assert mentor.snapshot()["last_role_directive"]["active"] is False
