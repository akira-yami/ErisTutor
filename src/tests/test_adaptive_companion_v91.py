from __future__ import annotations

import json
from pathlib import Path

import pytest

from runtime.adaptive_companion import AdaptiveCompanionService


class _FakeMentor:
    def __init__(self, skills=None, risk: float = 0.0):
        self._skills = skills or {}
        self._risk = risk

    def snapshot(self):
        return {
            "model": {
                "skills": self._skills,
                "overreliance_risk": self._risk,
            }
        }


class _FakeOwner:
    def __init__(self, mentor=None):
        self.engineering_mentor_service = mentor
        self.life_horizon_manager = None


def _service(tmp_path: Path, mentor=None) -> AdaptiveCompanionService:
    return AdaptiveCompanionService(
        owner=_FakeOwner(mentor),
        state_path=tmp_path / "adaptive_companion.json",
    )


def test_v91_records_explicit_learning_and_activity_without_claiming_mastery(tmp_path: Path) -> None:
    service = _service(tmp_path)

    directive = service.observe_user_turn(
        "Tôi đang học C# và .NET, còn đồ án hiện tại đang làm DocMind.",
        turn_id="turn-1",
        now=1000.0,
    )

    snap = service.snapshot()
    labels = {item["label"] for item in snap["learning_topics"]}
    activities = {item["label"] for item in snap["activities"]}

    assert any("C#" in label or ".NET" in label for label in labels)
    assert any("DocMind" in label for label in activities)
    assert all(item["stage"] in {"exploring", "learning"} for item in snap["learning_topics"])
    assert all("master" not in json.dumps(item).lower() for item in snap["learning_topics"])
    assert directive.stance in {"companion", "teach_light", "teach_guided"}


def test_v91_fast_delegation_alone_does_not_turn_eris_into_teacher(tmp_path: Path) -> None:
    service = _service(tmp_path)

    directive = service.observe_user_turn("làm luôn đi", turn_id="turn-fast", now=1000.0)

    assert directive.stance == "companion"
    assert directive.teaching_permission == "none"
    assert directive.ask_user_to_reason is False
    assert directive.reason == "fast_execution_is_not_a_learning_failure"


def test_v91_explicit_learning_request_gets_guided_teaching(tmp_path: Path) -> None:
    service = _service(tmp_path)

    directive = service.observe_user_turn(
        "Giải thích cho tôi tại sao dependency injection trong .NET hoạt động như vậy, tôi đang học phần này.",
        turn_id="turn-learn",
        now=1000.0,
    )

    assert directive.stance == "teach_guided"
    assert directive.teaching_permission == "guided"
    assert directive.explanation_depth in {"normal", "deep"}


def test_v91_strong_engineering_evidence_promotes_peer_stance_not_basic_tutorial(tmp_path: Path) -> None:
    mentor = _FakeMentor(
        skills={
            "debugging.root_cause_reasoning": {
                "level": 0.82,
                "confidence": 0.68,
                "evidence_count": 7,
                "independent_evidence_count": 5,
            }
        }
    )
    service = _service(tmp_path, mentor=mentor)

    directive = service.observe_user_turn(
        "Tôi nghĩ lỗi nằm ở root cause phía target acquisition chứ không phải movement, xem reasoning này ổn không?",
        turn_id="turn-peer",
        now=1000.0,
    )

    assert directive.stance == "peer"
    assert directive.explanation_depth == "minimal"
    assert directive.skip_basics is True
    assert directive.technical_assumptions["debugging.root_cause_reasoning"]["stage"] == "strong"


def test_v91_teasing_is_only_an_opportunity_not_voice_authority(tmp_path: Path) -> None:
    service = _service(tmp_path)

    directive = service.observe_user_turn(
        "dcm lại lỗi nữa, build xanh xong vẫn chết runtime",
        turn_id="turn-heat",
        now=1000.0,
    )

    assert directive.stance == "companion"
    assert directive.teasing_opportunity in {"light", "earned"}
    assert directive.teasing_is_authority is False
    assert "relationship" in " ".join(directive.rules).lower()


def test_v91_companion_no_longer_persists_a_shadow_user_memory(tmp_path: Path) -> None:
    service = _service(tmp_path)
    raw = "Tôi đang học ASP.NET Core để làm project quản lý tài liệu. " + ("secret-long-text " * 100)

    directive = service.observe_user_turn(raw, turn_id="turn-persist", now=1000.0)

    # Companion vẫn hiểu ngay turn hiện tại qua hot cache, nhưng durable context
    # phải thuộc Living User Memory chứ không sinh thêm adaptive_companion.json.
    assert any("ASP.NET Core" in item for item in directive.active_learning_topics)
    assert not (tmp_path / "adaptive_companion.json").exists()
    assert "raw_transcript" not in json.dumps(service.snapshot(), ensure_ascii=False)


class _Goal:
    def __init__(self, title: str, description: str = "", level: str = "H2", status: str = "active"):
        self.title = title
        self.description = description
        self.level = level
        self.status = status
        self.strength = 0.8


class _HorizonSnapshot:
    def __init__(self, goals):
        self.goals = tuple(goals)


class _Horizon:
    def snapshot(self, **_kwargs):
        return _HorizonSnapshot([_Goal("Eris Desktop", "Xây technical companion độc lập extension")])


def test_v91_uses_existing_life_horizon_as_activity_context_without_copying_it_to_learning(tmp_path: Path) -> None:
    owner = _FakeOwner()
    owner.life_horizon_manager = _Horizon()
    service = AdaptiveCompanionService(owner=owner, state_path=tmp_path / "companion.json")

    directive = service.observe_user_turn("giờ làm gì tiếp", turn_id="t-horizon", now=1000.0)

    assert any("Eris Desktop" in item for item in directive.active_activities)
    assert not any("Eris Desktop" in item for item in directive.active_learning_topics)


def test_v91_repeated_delegation_only_challenges_when_it_overlaps_active_learning(tmp_path: Path) -> None:
    mentor = _FakeMentor(risk=0.62)
    service = _service(tmp_path, mentor=mentor)
    service.observe_user_turn("Tôi đang học root cause reasoning.", turn_id="learn-root", now=900.0)

    directive = service.observe_user_turn(
        "làm luôn bug root cause này đi",
        turn_id="fast-root",
        now=1000.0,
    )

    assert directive.stance == "challenge_light"
    assert directive.teaching_permission == "challenge"
    assert directive.ask_user_to_reason is True


def test_v91_high_ai_delegation_risk_does_not_challenge_unrelated_fast_task(tmp_path: Path) -> None:
    mentor = _FakeMentor(risk=0.75)
    service = _service(tmp_path, mentor=mentor)
    service.observe_user_turn("Tôi đang học root cause reasoning.", turn_id="learn-root", now=900.0)

    directive = service.observe_user_turn("làm luôn cái icon đi", turn_id="fast-icon", now=1000.0)

    assert directive.stance == "companion"
    assert directive.ask_user_to_reason is False
