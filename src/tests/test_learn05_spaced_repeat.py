# -*- coding: utf-8 -*-
"""LEARN-05: deterministic repeated learning across restart, no fake recall.

Không đo chất lượng lời giải DeepSeek thật. Test này chứng minh trạng thái
S/R, idempotency, audit và điều phối review qua policy Root -> Tutor.
"""
from __future__ import annotations

import sqlite3

from runtime.learning_core import LearningService, LearningStore, LearningPath, KnowledgePoint
from runtime.learning_core.review_memory import ReviewMemory, retrievability
from runtime.tutor.executive import TutorExecutive

DAY = 86400.0
T = 1_800_000_000.0


def service(db):
    s = LearningService(LearningStore(db))
    if s.active_path() is None:
        s.register_path(LearningPath("repeat-python", "Repeat", "Ôn kiến thức có bằng chứng", (
            KnowledgePoint("for", "For loop", "concept"),
            KnowledgePoint("while", "While loop", "concept"),
        )))
        assert s.activate_path("repeat-python")
    return s


def write(s, i, *, type="quiz_correct", correct=True, strength=.95, at=T,
          review=False, hints=0, kp="for", interaction=None, turn=None):
    return s.commit_evidence(
        kp, type, correct=correct, strength=strength,
        review_recall=review, hint_count=hints, now=at,
        interaction_id=interaction or f"ix-{i}", turn_id=turn or f"turn-{i}",
    )


def test_no_review_from_self_report_or_wrong_new_lesson(tmp_path):
    s = service(tmp_path / "first.db")
    assert write(s, 1, type="self_report", at=T)
    assert write(s, 2, type="quiz_incorrect", correct=False, strength=0, at=T+1)
    assert s.review_snapshot(["for"]) == {}
    assert s.review_memory("for") is None
    assert s.next_tutor_step(now=T+7*DAY).kind != "review"


def test_study_success_seeds_early_review_but_practice_cannot_push_due(tmp_path):
    s = service(tmp_path / "practice.db")
    assert write(s, 1, at=T)
    first = s.review_snapshot(["for"])["for"]
    assert first["memory"]["recall_count"] == 0
    assert abs(first["due_at"] - (T + DAY)) < 1
    assert write(s, 2, at=T + DAY / 3, strength=.99, type="application")
    assert write(s, 3, at=T + DAY / 2, strength=.7, type="feynman_pass")
    after = s.review_snapshot(["for"])["for"]
    assert after["due_at"] == first["due_at"]
    assert after["memory"]["stability_days"] == first["memory"]["stability_days"]
    assert s.next_tutor_step(now=T + DAY + 1).kind == "review"


def test_real_recall_extends_and_updates_R_with_time_and_restart(tmp_path):
    db = tmp_path / "long.db"
    s = service(db)
    write(s, 1, at=T)
    original = s.review_snapshot(["for"])["for"]
    before_r = retrievability(s._store.load_review_memory("for"), now=T + DAY)
    assert .89 < before_r < .91
    assert write(s, 2, at=T+DAY, review=True)
    extended = s.review_snapshot(["for"])["for"]
    assert extended["due_at"] > original["due_at"]
    assert extended["memory"]["stability_days"] > 1
    assert extended["memory"]["recall_count"] == 1
    assert s.due_review_items(now=T+DAY+1) == []
    before_restart = extended["memory"]
    s._store.close()
    reopened = service(db)
    assert reopened.review_snapshot(["for"])["for"]["memory"] == before_restart
    assert reopened.next_tutor_step(now=extended["due_at"]+1).kind == "review"


def test_hint_assisted_correct_not_expand_and_fail_cuts_stability(tmp_path):
    s = service(tmp_path / "hint.db")
    write(s, 1)
    assert write(s, 2, at=T+DAY, review=True, hints=2)
    hinted = s.review_snapshot(["for"])["for"]
    assert hinted["memory"]["stability_days"] == 1
    assert hinted["due_at"] < T + 2 * DAY
    assert write(s, 3, at=T+DAY+DAY/2, review=True,
                 type="quiz_incorrect", correct=False, strength=0)
    failed = s.review_snapshot(["for"])["for"]
    assert failed["memory"]["stability_days"] < hinted["memory"]["stability_days"]
    assert failed["memory"]["lapse_count"] == 1
    assert failed["due_at"] < T + 2 * DAY
    assert failed["memory"]["anchor_at"] == T  # sai không reset đồng hồ nhớ


def test_semantic_retry_and_same_question_cannot_multiply_recall(tmp_path):
    db = tmp_path / "duplicate.db"
    s = service(db)
    write(s, 1)
    assert write(s, 2, at=T+DAY, review=True, interaction="q-2", turn="a-2")
    state = s.review_snapshot(["for"])["for"]
    assert write(s, 2, at=T+DAY, review=True, interaction="q-2", turn="a-2") is None
    # Model gửi assessment khác type/turn cho cùng câu hỏi -> ledger có audit,
    # nhưng KHÔNG được cộng thêm lượt nhớ.
    assert write(s, 3, at=T+DAY+5, review=True, type="application",
                 interaction="q-2", turn="a-3")
    same_q = s.review_snapshot(["for"])["for"]
    assert same_q["memory"]["recall_count"] == 1
    assert same_q["due_at"] == state["due_at"]
    s._store.close()
    again = service(db)
    assert again.review_snapshot(["for"])["for"]["memory"]["recall_count"] == 1


def test_due_prioritized_by_fresh_risk_and_bounded(tmp_path):
    s = service(tmp_path / "risk.db")
    write(s, 1, at=T, kp="for")
    write(s, 2, at=T+DAY/2, kp="while")
    due = s.due_review_items(now=T+2*DAY)
    assert [x.kp_id for x in due] == ["for", "while"]
    assert due[0].forgetting_risk > due[1].forgetting_risk > 0
    step = s.next_tutor_step(now=T+2*DAY)
    assert step.kind == "review" and step.kp_id == "for"
    # Root nhận đề xuất, còn quyền nói nằm bên Root/capability.
    move = TutorExecutive(s).plan_next_move("Ôn bài", turn_id="ask", now=T+2*DAY)
    assert move.policy_step.kind == "review"
    assert move.move.kind == "review"
    assert "không gợi ý" in move.move.instruction_vi.lower()


def test_deterministic_review_flag_survives_sqlite_restart(tmp_path):
    db = tmp_path / "det.db"
    s = service(db)
    write(s, 1)
    q = s.pose_interaction("for", "multiple_choice", "2 + 2?", "4",
                           options=("4", "5"), correct_index=0,
                           review_recall=True, now=T+DAY, expires_in_seconds=3600)
    s._store.close()
    r = service(db)
    saved = r._store.load_interaction(q.interaction_id)
    assert saved.review_recall is True
    answer = r.resolve_interaction(q.interaction_id, 0, turn_id="answer-d", now=T+DAY+2)
    assert answer is not None
    assert r.review_snapshot(["for"])["for"]["memory"]["recall_count"] == 1
    assert r.resolve_interaction(q.interaction_id, 0, turn_id="answer-d", now=T+DAY+3) is None


def test_old_sqlite_migrates_non_destructively(tmp_path):
    db = tmp_path / "old.db"
    old = sqlite3.connect(str(db))
    old.execute("CREATE TABLE interactions(interaction_id TEXT PRIMARY KEY)")
    old.commit(); old.close()
    # A minimal damaged schema is not a supported db; simulate a real V1 DB
    # by constructing the actual old schema from current DDL without new column.
    from runtime.learning_core.store import _DDL
    db.unlink()
    sql = _DDL.replace(",\n    review_recall INTEGER NOT NULL DEFAULT 0", "")
    old = sqlite3.connect(str(db)); old.executescript(sql); old.commit(); old.close()
    s = service(db)
    assert write(s, 1)
    cols = {x[1] for x in s._store._db().execute("PRAGMA table_info(interactions)")}
    assert "review_recall" in cols
    assert s.get_mastery("for").attempt_count == 1


def test_root_tutor_review_requires_successful_delivery_receipt(tmp_path):
    """Receipt V12: phát hỏng không được biến câu hỏi dự định thành recall."""
    import time
    from runtime.capabilities.tutor import TutorCapability
    from runtime.conversation.contracts import ConversationEvent, DeliveryReceipt
    from runtime.tutor.contracts import SemanticAssessment
    from runtime.tutor.reasoner import ConservativeTutorReasoner

    class Assessor(ConservativeTutorReasoner):
        def assess_response(self, observation, state, previous_move):
            return SemanticAssessment(
                kp_id=previous_move.kp_id, verdict="pass", confidence=.96,
                evidence_type="quiz_correct", strength=.94,
            )

    s = service(tmp_path / "root-review.db")
    t = time.time()
    assert write(s, 1, at=t - 3*DAY)
    cap = TutorCapability(TutorExecutive(s, reasoner=Assessor()))
    first = cap.invoke_for_root(arguments={}, event=ConversationEvent(
        event_id="t1", session_id="s", kind="user_turn", text="Ôn bài for"
    ), recent_history=[])
    assert first["teaching_move"]["kind"] == "review"
    assert first["expects_learner_response"]
    cap.observe_delivery_receipt(DeliveryReceipt(
        delivery_id="failed", session_id="s", generation=1,
        move_id="root:a", successful=False,
    ))
    cap.invoke_for_root(arguments={"response_to_previous": True}, event=ConversationEvent(
        event_id="t2", session_id="s", kind="user_turn", text="Đáp án 4"
    ), recent_history=[])
    assert s.review_memory("for")["recall_count"] == 0

    second = cap.invoke_for_root(arguments={}, event=ConversationEvent(
        event_id="t3", session_id="s", kind="user_turn", text="Thử lại"
    ), recent_history=[])
    assert second["teaching_move"]["kind"] == "review"
    cap.observe_delivery_receipt(DeliveryReceipt(
        delivery_id="ok", session_id="s", generation=2,
        move_id="root:b", successful=True,
    ))
    assessed = cap.invoke_for_root(arguments={"response_to_previous": True}, event=ConversationEvent(
        event_id="t4", session_id="s", kind="user_turn", text="Đáp án 4"
    ), recent_history=[])
    assert assessed["assessment"]["verdict"] == "pass"
    assert s.review_memory("for")["recall_count"] == 1
