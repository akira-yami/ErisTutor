from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from memory.living_user import LivingUserMemoryService
from runtime.adaptive_companion import AdaptiveCompanionService


class _Owner:
    def __init__(self, living_user=None):
        self.engineering_mentor_service = None
        self.life_horizon_manager = None
        self.living_user = living_user


def _learning_model(topic, activity):
    return SimpleNamespace(propose_profile_operations=lambda payload: {"operations": [
        {"op":"add", "path":"learning.active_topics", "value":topic, "confidence":0.9, "explicitness":"explicit", "cardinality":"multi"},
        {"op":"add", "path":"projects.current_activities", "value":activity, "confidence":0.9, "explicitness":"explicit", "cardinality":"multi"},
    ]})


def test_living_user_is_canonical_owner_for_explicit_learning_and_current_activity(tmp_path: Path) -> None:
    """Learning/activity phải đi vào Living User Memory, không nằm riêng ở Companion."""
    living = LivingUserMemoryService(tmp_path / "living", model_adapter=_learning_model("C# / .NET", "DocMind"))

    result = living.observe_turn(
        "Tôi đang học C# và .NET, còn đồ án hiện tại đang làm DocMind.",
        evidence_ids=("turn-learning-1",),
    )

    assert result.applied >= 2
    learning = living.profile_store.get("learning.active_topics")
    activities = living.profile_store.get("projects.current_activities")
    assert any("C#" in str(item.value) or ".NET" in str(item.value) for item in learning)
    assert any("DocMind" in str(item.value) for item in activities)
    assert all(item.cardinality == "multi" for item in (*learning, *activities))
    # Đây là context đang diễn ra, không phải mastery/skill estimate.
    assert all(not item.path.startswith("technical.experience") for item in (*learning, *activities))


def test_adaptive_companion_reads_canonical_living_user_context_after_restart(tmp_path: Path) -> None:
    """Companion mới vẫn hiểu user sau restart dù không có state shadow riêng."""
    living = LivingUserMemoryService(tmp_path / "living", model_adapter=_learning_model("ASP.NET Core", "DocMind"))
    living.observe_turn(
        "Tôi đang học ASP.NET Core và đang làm project DocMind.",
        evidence_ids=("turn-1",),
    )

    state_path = tmp_path / "adaptive_companion_v91.json"
    service = AdaptiveCompanionService(owner=_Owner(living), state_path=state_path)
    directive = service.observe_user_turn("giờ làm gì tiếp", turn_id="turn-2", now=2_000.0)

    assert any("ASP.NET Core" in item for item in directive.active_learning_topics)
    assert any("DocMind" in item for item in directive.active_activities)
    # Consolidation: AdaptiveCompanion không tạo thêm durable user-memory file nữa.
    assert not state_path.exists()


def test_adaptive_companion_keeps_only_hot_session_cache_when_living_user_is_not_ready(tmp_path: Path) -> None:
    """Background curator có trễ thì cùng session vẫn nối mạch, nhưng không ghi shadow memory."""
    state_path = tmp_path / "adaptive_companion_v91.json"
    service = AdaptiveCompanionService(owner=_Owner(), state_path=state_path)

    first = service.observe_user_turn("Tôi đang học root cause reasoning.", turn_id="t1", now=1_000.0)
    second = service.observe_user_turn("làm luôn bug root cause này đi", turn_id="t2", now=1_010.0)

    assert any("root cause reasoning" in item.lower() for item in first.active_learning_topics)
    assert any("root cause reasoning" in item.lower() for item in second.active_learning_topics)
    assert not state_path.exists()


def test_legacy_v91_state_is_read_only_migration_input_not_rewritten(tmp_path: Path) -> None:
    """File V91 cũ được đọc để không mất continuity, nhưng không còn là source-of-truth."""
    state_path = tmp_path / "adaptive_companion_v91.json"
    legacy = {
        "schema": "eris.adaptive_companion.v91",
        "learning_topics": [
            {
                "topic_id": "learning:legacy",
                "label": "dependency injection",
                "stage": "learning",
                "confidence": 0.5,
                "evidence_count": 2,
                "last_seen_at": 1_900.0,
                "source_refs": ["old-turn"],
            }
        ],
        "activities": [],
    }
    state_path.write_text(json.dumps(legacy, ensure_ascii=False), encoding="utf-8")
    before = state_path.read_text(encoding="utf-8")

    service = AdaptiveCompanionService(owner=_Owner(), state_path=state_path)
    directive = service.observe_user_turn("tiếp đi", turn_id="new-turn", now=2_000.0)

    assert any("dependency injection" in item.lower() for item in directive.active_learning_topics)
    assert state_path.read_text(encoding="utf-8") == before


def test_living_user_competence_claim_is_context_only_not_teaching_authority(tmp_path: Path) -> None:
    """Profile có thể nhớ self-report kinh nghiệm, nhưng không được lấn skill evidence model."""
    from memory.living_user import ProfileOperation

    living = LivingUserMemoryService(tmp_path / "living")
    living.profile_store.apply((ProfileOperation(
        op="add",
        path="technical.experience_level",
        value="advanced",
        confidence=0.98,
        explicitness="explicit",
        evidence_ids=("turn-skill-self-report",),
        reason="user self-report",
        source="test",
    ),))

    rendered = living.compile_context("giải thích architecture cho tôi").render_prompt_context()

    assert "technical.experience_level" in rendered
    assert "không được dùng" in rendered.lower()
    assert "mức dạy" in rendered.lower() or "skip" in rendered.lower()
    assert "evidence" in rendered.lower()


def test_plain_why_question_gets_answer_not_forced_tutor_mode(tmp_path: Path) -> None:
    service = AdaptiveCompanionService(owner=_Owner(), state_path=tmp_path / "unused.json")

    directive = service.observe_user_turn(
        "Tại sao dependency injection trong .NET lại hoạt động như vậy?",
        turn_id="why-1",
        now=3_000.0,
    )

    assert directive.stance != "teach_guided"
    assert directive.ask_user_to_reason is False
    assert directive.reason == "user_requested_explanation_not_mentor_mode"


def test_explicit_teach_request_still_allows_guided_mode(tmp_path: Path) -> None:
    service = AdaptiveCompanionService(owner=_Owner(), state_path=tmp_path / "unused.json")

    directive = service.observe_user_turn(
        "Dạy tôi dependency injection từ gốc, tôi đang học phần này.",
        turn_id="teach-1",
        now=3_000.0,
    )

    assert directive.stance == "teach_guided"
    assert directive.teaching_permission == "guided"


def test_domain_word_learning_does_not_mean_user_wants_a_lesson(tmp_path: Path) -> None:
    """'Machine/deep learning' là tên domain, không phải lời mời Eris mở lớp."""
    service = AdaptiveCompanionService(owner=_Owner(), state_path=tmp_path / "unused.json")

    directive = service.observe_user_turn(
        "Deep learning model này lỗi shape tensor ở đâu vậy?",
        turn_id="domain-learning-1",
        now=3_100.0,
    )

    assert directive.stance != "teach_guided"
    assert directive.ask_user_to_reason is False
