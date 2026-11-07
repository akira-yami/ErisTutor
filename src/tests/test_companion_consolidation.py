# -*- coding: utf-8 -*-
"""Acceptance cho đợt Companion Consolidation.

Các test này khóa đúng boundary mới:
- AdaptiveCompanion là owner duy nhất của delivery/stance;
- EngineeringMentor chỉ cung cấp evidence + learning/tool hints;
- Desktop không tự đọc CodingObservation/ProjectTrajectory nữa;
- calibration cũ phải ảnh hưởng delivery thông qua Companion, không qua một role router thứ hai.
"""
from __future__ import annotations

import json
from pathlib import Path

from runtime.adaptive_companion import AdaptiveCompanionService
from runtime.engineering_mentor import EngineeringMentorService


ROOT = Path(__file__).resolve().parents[1]


class _CalibrationDirective:
    def __init__(self, *, reduce: bool = False, cautious: bool = False):
        self.reduce_proactive_friction = reduce
        self.confidence_mode = "cautious" if cautious else "normal"
        self.reason = "test"


class _Calibration:
    def __init__(self, *, reduce: bool = False, cautious: bool = False):
        self._directive = _CalibrationDirective(reduce=reduce, cautious=cautious)

    def directive(self, **_kwargs):
        return self._directive


class _MentorStub:
    def __init__(self, *, skills=None, risk: float = 0.0, reduce: bool = False, cautious: bool = False):
        self._skills = skills or {}
        self._risk = risk
        self.calibration = _Calibration(reduce=reduce, cautious=cautious)

    def snapshot(self):
        return {"model": {"skills": self._skills, "overreliance_risk": self._risk}}


class _Owner:
    def __init__(self, mentor=None):
        self.engineering_mentor_service = mentor
        self.life_horizon_manager = None
        self.logger = None


def test_companion_is_single_delivery_owner_and_exposes_that_contract(tmp_path: Path) -> None:
    service = AdaptiveCompanionService(owner=_Owner(), state_path=tmp_path / "companion.json")
    directive = service.observe_user_turn("giờ làm gì tiếp", turn_id="c1", now=1000.0)
    payload = directive.to_dict()

    assert payload["delivery_owner"] == "adaptive_companion"
    assert payload["mentor_is_evidence_provider"] is True
    assert payload["authorities"]["tone_and_teasing_permission"] == "relationship_plus_affective_voice"
    assert any("delivery authority" in rule.lower() for rule in payload["rules"])


def test_recent_mentor_annoyance_reduces_proactive_teaching_in_companion_owner(tmp_path: Path) -> None:
    mentor = _MentorStub(reduce=True)
    service = AdaptiveCompanionService(owner=_Owner(mentor), state_path=tmp_path / "companion.json")

    directive = service.observe_user_turn(
        "root cause này có vẻ nằm ở target acquisition chứ không phải movement",
        turn_id="c2",
        now=1000.0,
    )

    assert directive.stance == "companion"
    assert directive.teaching_permission == "none"
    assert directive.reason == "mentor_calibration_reduced_proactive_teaching"


def test_cautious_calibration_prevents_peer_overclaim_even_with_old_strong_skill(tmp_path: Path) -> None:
    mentor = _MentorStub(
        cautious=True,
        skills={
            "debugging.root_cause_reasoning": {
                "level": 0.86,
                "confidence": 0.76,
                "evidence_count": 8,
                "independent_evidence_count": 5,
            }
        },
    )
    service = AdaptiveCompanionService(owner=_Owner(mentor), state_path=tmp_path / "companion.json")

    directive = service.observe_user_turn(
        "Tôi nghĩ root cause ở target acquisition, xem reasoning này ổn không?",
        turn_id="c3",
        now=1000.0,
    )

    assert directive.stance == "companion"
    assert directive.skip_basics is False
    assert directive.reason == "calibration_requires_cautious_skill_assumption"


def test_engineering_mentor_prompt_is_evidence_only_not_second_delivery_brain(tmp_path: Path) -> None:
    mentor = EngineeringMentorService(
        path=tmp_path / "skills.json",
        runtime_path=tmp_path / "runtime.json",
        evaluation_path=tmp_path / "evaluation.jsonl",
        learning_path=tmp_path / "learning.json",
        tool_literacy_path=tmp_path / "tools.json",
        calibration_path=tmp_path / "calibration.json",
    )

    block = mentor.prompt_context(
        "Giải thích cách trace call flow lỗi code này.",
        turn_id="m1",
        now=1000.0,
        environment={},
    )
    assert block.startswith("ENGINEERING_EVIDENCE:\n")
    payload = json.loads(block.split("\n", 1)[1])

    assert payload["delivery_owner"] == "adaptive_companion"
    assert payload["role"] == "technical_evidence_provider"
    assert "role_orchestration" not in payload
    assert "companion_workspace" not in payload
    assert payload["cross_environment"]["authority"] == "evidence_only"
    assert "one_voice_rule" not in json.dumps(payload, ensure_ascii=False)


def test_desktop_runtime_no_longer_boots_or_injects_project_trajectory_from_coding_observation() -> None:
    coordinator = (ROOT / "runtime" / "coordinator.py").read_text(encoding="utf-8")
    orchestrator = (ROOT / "runtime" / "ai_orchestrated" / "orchestrator.py").read_text(encoding="utf-8")
    incoming = (ROOT / "runtime" / "incoming_protocol_runtime.py").read_text(encoding="utf-8")

    assert "ProjectTrajectoryService(" not in coordinator
    assert "project_trajectory_context" not in orchestrator
    assert "project_trajectory.decorate" not in incoming


def test_explicit_learning_still_wins_when_calibration_is_cautious(tmp_path: Path) -> None:
    mentor = _MentorStub(
        cautious=True,
        skills={
            "debugging.root_cause_reasoning": {
                "level": 0.90,
                "confidence": 0.80,
                "evidence_count": 10,
                "independent_evidence_count": 6,
            }
        },
    )
    service = AdaptiveCompanionService(owner=_Owner(mentor), state_path=tmp_path / "companion.json")

    directive = service.observe_user_turn(
        "Giải thích cho tôi kỹ root cause reasoning, tôi đang học phần này.",
        turn_id="c4",
        now=1000.0,
    )

    assert directive.stance == "teach_guided"
    assert directive.teaching_permission == "guided"


def test_legacy_extension_observation_api_cannot_mutate_companion_or_skill_model(tmp_path: Path) -> None:
    mentor = EngineeringMentorService(
        path=tmp_path / "skills.json",
        runtime_path=tmp_path / "runtime.json",
        evaluation_path=tmp_path / "evaluation.jsonl",
        learning_path=tmp_path / "learning.json",
        tool_literacy_path=tmp_path / "tools.json",
        calibration_path=tmp_path / "calibration.json",
    )
    before = mentor.model.snapshot()["skills"]["architecture.system_boundaries"]["evidence_count"]

    mentor.observe_companion_context({
        "workspace": "C:/repo",
        "last_event_at": 1000.0,
        "current_task_id": "hidden-task",
        "active_file": "secret.py",
    })
    signals = mentor.observe_external_user_intent(
        "boundary này sai, chỉ sửa perception layer",
        evidence_ref="legacy-extension",
        now=1000.0,
    )

    after = mentor.model.snapshot()["skills"]["architecture.system_boundaries"]["evidence_count"]
    assert signals == ()
    assert after == before
    assert mentor.snapshot()["last_companion_context"] == {}


class _RecordingCalibration(_Calibration):
    def __init__(self, *, reduce: bool = False, cautious: bool = False):
        super().__init__(reduce=reduce, cautious=cautious)
        self.registered = []

    def register_intervention(self, **kwargs):
        self.registered.append(dict(kwargs))


class _RecordingMentor(_MentorStub):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.calibration = _RecordingCalibration(
            reduce=kwargs.get("reduce", False),
            cautious=kwargs.get("cautious", False),
        )


def test_companion_registers_calibration_only_when_it_actually_allows_teaching(tmp_path: Path) -> None:
    mentor = _RecordingMentor()
    service = AdaptiveCompanionService(owner=_Owner(mentor), state_path=tmp_path / "companion.json")

    service.observe_user_turn("làm luôn đi", turn_id="no-teach", now=1000.0)
    assert mentor.calibration.registered == []

    directive = service.observe_user_turn(
        "Giải thích cho tôi root cause reasoning, tôi đang học phần này.",
        turn_id="teach", now=1010.0,
    )
    assert directive.teaching_permission == "guided"
    assert len(mentor.calibration.registered) == 1
    assert mentor.calibration.registered[0]["intervention_id"] == "teach"
    assert mentor.calibration.registered[0]["primary_role"] == "teach_guided"


def test_engineering_mentor_evidence_projection_does_not_register_delivery_intervention(tmp_path: Path) -> None:
    mentor = EngineeringMentorService(
        path=tmp_path / "skills.json",
        runtime_path=tmp_path / "runtime.json",
        evaluation_path=tmp_path / "evaluation.jsonl",
        learning_path=tmp_path / "learning.json",
        tool_literacy_path=tmp_path / "tools.json",
        calibration_path=tmp_path / "calibration.json",
    )
    assert mentor.calibration.snapshot()["pending_count"] == 0
    assert mentor.prompt_context(
        "Giải thích call flow lỗi này cho tôi.", turn_id="evidence-only", now=1000.0
    )
    assert mentor.calibration.snapshot()["pending_count"] == 0


def test_orchestrator_updates_mentor_evidence_before_companion_selects_current_delivery() -> None:
    source = (ROOT / "runtime" / "ai_orchestrated" / "orchestrator.py").read_text(encoding="utf-8")
    mentor_pos = source.index("mentor_prompt(")
    companion_pos = source.index("adaptive_companion.prompt_context(")
    assert mentor_pos < companion_pos


def test_real_mentor_learning_hint_is_committed_only_after_companion_teaching(tmp_path: Path) -> None:
    mentor = EngineeringMentorService(
        path=tmp_path / "skills.json",
        runtime_path=tmp_path / "runtime.json",
        evaluation_path=tmp_path / "evaluation.jsonl",
        learning_path=tmp_path / "learning.json",
        tool_literacy_path=tmp_path / "tools.json",
        calibration_path=tmp_path / "calibration.json",
    )
    owner = _Owner(mentor)
    companion = AdaptiveCompanionService(owner=owner, state_path=tmp_path / "companion.json")
    mentor.observe_coding_flow({
        "task_id": "flow-real",
        "workspace": "C:/repo",
        "status": "completed",
        "started_at": 990.0,
        "ended_at": 999.0,
        "changed_files": ["src/a.py"],
        "tests_seen": 0,
        "failed_test_runs": 0,
        "reviewer_seen": False,
        "phase_summaries": {},
        "findings": [{"code": "patch_before_inspection", "severity": "warning", "message": "patch before inspect"}],
    })
    text = "Giải thích cho tôi tại sao phải inspect trước khi patch, tôi đang học debug phần này."

    block = mentor.prompt_context(text, turn_id="real-turn", now=1000.0)
    assert block.startswith("ENGINEERING_EVIDENCE:")
    before = mentor.learning.snapshot()["concepts"]["debugging.inspect_before_patch"]["last_prompted_at"]
    assert before == 0.0

    directive = companion.observe_user_turn(text, turn_id="real-turn", now=1000.1)
    after = mentor.learning.snapshot()["concepts"]["debugging.inspect_before_patch"]["last_prompted_at"]

    assert directive.stance == "teach_guided"
    assert after > 0.0
    assert mentor.calibration.snapshot()["pending_count"] == 1
