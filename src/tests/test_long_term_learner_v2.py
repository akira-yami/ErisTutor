# -*- coding: utf-8 -*-
"""LEARN-02/03 gộp: cá nhân hóa có nguồn, restart, idempotency và Root projection.

Không kết nối provider; tests kiểm chứng giao kèo dữ liệu, không chấm chất
lượng sư phạm của DeepSeek trên người học thật.
"""
from __future__ import annotations

from types import SimpleNamespace

from memory.living_user.models import ProfileOperation, UserMemoryScope
from memory.living_user.store import UserProfileStore
from runtime.capabilities.tutor import TutorCapability
from runtime.learning_core import KnowledgePoint, LearningPath, LearningService, LearningStore
from runtime.tutor.learner_context import (
    learner_continuity_snapshot, learner_profile_from_living_user,
)


def _record(store, name, value, *, evidence="turn-1", explicitness="explicit", confidence=.96,
            sensitivity="normal", scope=None):
    return store.apply([ProfileOperation(
        op="reinforce", path=name, value=value, confidence=confidence,
        explicitness=explicitness, evidence_ids=(evidence,),
        source="user_explicit", sensitivity=sensitivity,
        scope=scope or UserMemoryScope(),
    )], checkpoint_reason="learner_v2_test")


def _living(path):
    store = UserProfileStore(path)
    return SimpleNamespace(profile_store=store, config=SimpleNamespace(enabled=True))


def _learning(path):
    service = LearningService(LearningStore(path))
    service.register_path(LearningPath(
        path_id="python-basics", title="Python cơ bản", goal="Dùng vòng lặp for chính xác",
        knowledge_points=(KnowledgePoint("for.loop", "Vòng lặp for"),),
    ), now=1000)
    assert service.activate_path("python-basics")
    return service


def test_living_user_preferences_are_read_only_persist_and_can_be_corrected(tmp_path):
    filename = tmp_path / "living_profile.json"
    user = _living(filename)
    assert _record(user.profile_store, "learning.preferred_pace", "slow").changed
    assert _record(user.profile_store, "learning.preferred_style", "procedural", evidence="t2").changed
    first = learner_profile_from_living_user(user)
    assert first.preferred_pace == "slow" and first.preferred_style == "procedural"
    # Truy vấn chỉ đọc không tăng version LivingUser và không tạo fact mới.
    version = user.profile_store.version
    assert learner_profile_from_living_user(user) == first
    assert user.profile_store.version == version

    reopened = _living(filename)
    assert learner_profile_from_living_user(reopened) == first
    # User đã sửa/forget thì Tutor không được dùng bản cache cũ.
    assert reopened.profile_store.forget("learning.preferred_pace", hard=False, reason="user_forget")
    assert learner_profile_from_living_user(reopened).preferred_pace == "normal"
    assert learner_profile_from_living_user(user).preferred_pace == "slow"  # store ở tiến trình cũ chưa reload
    assert learner_profile_from_living_user(_living(filename)).preferred_pace == "normal"


def test_reject_low_confidence_inferences_sensitive_scopes_and_unknown_fields(tmp_path):
    user = _living(tmp_path / "p.json")
    store = user.profile_store
    _record(store, "learning.preferred_pace", "fast", explicitness="inferred", confidence=.99)
    _record(store, "learning.preferred_style", "procedural", confidence=.99,
            sensitivity="sensitive", evidence="t2")
    _record(store, "learning.active_domain", "python", confidence=.95,
            scope=UserMemoryScope("project", "project-a"), evidence="t3")
    _record(store, "communication.response_rules", "slow", evidence="t4")
    profile = learner_profile_from_living_user(user)
    assert profile.preferred_pace == "normal"
    assert profile.preferred_style == "conceptual"
    assert profile.active_domain == ""
    scoped = learner_profile_from_living_user(user, project_id="project-a")
    assert scoped.active_domain == "python"
    assert learner_profile_from_living_user(user, project_id="project-b").active_domain == ""


def test_live_capability_passes_profile_to_both_tutor_steps(tmp_path):
    user = _living(tmp_path / "living.json")
    _record(user.profile_store, "learning.preferred_pace", "slow")
    _record(user.profile_store, "learning.native_language", "vi", evidence="t2")
    received = []

    class Executive:
        learning = None
        def plan_next_move(self, *args, **kwargs):
            received.append(("plan", kwargs["learner"].preferred_pace))
            return None
        def observe_response(self, *args, **kwargs):
            received.append(("assess", kwargs["learner"].preferred_pace))
            return None

    capability = TutorCapability(Executive(), living_user_source=lambda: user)
    capability.advise(user_text="Học tiếp", turn_id="turn-2", response_to_previous=True)
    assert received == [("assess", "slow"), ("plan", "slow")]
    user.profile_store.forget("learning.preferred_pace", hard=False, reason="user_forget")
    capability.advise(user_text="Học lại", turn_id="turn-3")
    assert received[-1] == ("plan", "normal")


def test_restart_keeps_evidence_mastery_belief_and_review_without_extra_writes(tmp_path):
    filename = tmp_path / "learning.sqlite3"
    service = _learning(filename)
    assert service.commit_evidence(
        "for.loop", "quiz_incorrect", turn_id="answer-1",
        interaction_id="question-1", now=1000,
    ) is not None
    assert service.commit_evidence(
        "for.loop", "quiz_correct", turn_id="answer-2",
        interaction_id="question-2", now=2000,
    ) is not None
    assert service.commit_evidence(
        "for.loop", "application", turn_id="answer-3",
        interaction_id="question-3", now=3000,
    ) is not None
    before = learner_continuity_snapshot(service)
    assert before["knowledge"][0]["attempt_count"] == 3
    assert before["knowledge"][0]["independent_evidence_count"] == 3
    assert before["knowledge"][0]["transfer_observed"]
    assert not before["calibrated"]
    # Tạo LearningService mới cùng SQLite; active path, evidence, belief phải trở lại.
    service._store.close()
    reopened = LearningService(LearningStore(filename))
    after = learner_continuity_snapshot(reopened)
    assert after == before
    assert reopened.commit_evidence(
        "for.loop", "quiz_correct", turn_id="answer-2",
        interaction_id="question-2", now=9000,
    ) is None
    assert learner_continuity_snapshot(reopened) == after
    assert len(reopened._store.load_evidence("for.loop")) == 3


def test_profile_and_learning_survive_restart_together_without_merging_trust(tmp_path):
    p = tmp_path / "p.json"
    d = tmp_path / "l.sqlite3"
    user = _living(p)
    _record(user.profile_store, "learning.preferred_pace", "fast", evidence="explicit-pref")
    learning = _learning(d)
    learning.commit_evidence("for.loop", "quiz_correct", turn_id="ans1", interaction_id="q1", now=1000)
    learning._store.close()
    snapshot = learner_continuity_snapshot(LearningService(LearningStore(d)), source=_living(p))
    assert snapshot["learner_preferences"]["preferred_pace"] == "fast"
    assert snapshot["knowledge"][0]["attempt_count"] == 1
    assert snapshot["knowledge"][0]["needs_probe"]
    assert snapshot["knowledge"][0]["p_known_shadow"] > .20
    assert snapshot["note"].startswith("Ước lượng")


def test_does_not_push_learning_when_no_active_path(tmp_path):
    service = LearningService(LearningStore(tmp_path / "empty.sqlite3"))
    assert learner_continuity_snapshot(service) == {}
    assert learner_profile_from_living_user(None).preferred_pace == "normal"


def test_root_payload_actually_contains_bounded_learner_context(tmp_path):
    """Chặn lỗi chỉ thêm assembler key nhưng Root filter bỏ mất."""
    from runtime.conversation.agent import RuntimeModelRootAgent
    from runtime.conversation.contracts import ConversationEvent
    learning = _learning(tmp_path / "l.sqlite3")
    projection = learner_continuity_snapshot(learning)
    payload = RuntimeModelRootAgent._payload(
        event=ConversationEvent(event_id="user:1", session_id="s", kind="user_turn", text="Học lại"),
        recent_history=(), note="", runtime_context={"learner_continuity": projection},
        tool_results=(), step_index=0,
    )
    assert payload["runtime_context"]["learner_continuity"]["active_path_id"] == "python-basics"
    assert payload["runtime_context"]["learner_continuity"]["knowledge"][0]["needs_probe"]


def test_repeated_misconception_requires_independent_attempts_even_after_restart(tmp_path):
    db = tmp_path / "learning.sqlite3"
    service = _learning(db)
    # Nhiều evidence từ một bài KHÔNG đủ để kết luận lỗi tái diễn.
    service.commit_evidence("for.loop", "misconception", correct=False, error_code="off_by_one",
                            interaction_id="q1", turn_id="t1", now=1000)
    service.commit_evidence("for.loop", "misconception", correct=False, error_code="off_by_one",
                            interaction_id="q1", turn_id="t1b", now=1200)
    assert service.repeated_error_patterns("for.loop") == []
    service.commit_evidence("for.loop", "misconception", correct=False, error_code="off_by_one",
                            interaction_id="q2", turn_id="t2", now=1700)
    seen = service.repeated_error_patterns("for.loop")
    assert seen == [{"error_code": "off_by_one", "independent_count": 2,
                     "status": "repeat_observed_not_diagnosis"}]
    service._store.close()
    recovered = LearningService(LearningStore(db))
    assert recovered.repeated_error_patterns("for.loop") == seen
    assert learner_continuity_snapshot(recovered)["knowledge"][0]["repeated_error_hypotheses"] == seen
