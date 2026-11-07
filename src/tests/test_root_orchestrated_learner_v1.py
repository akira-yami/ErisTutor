# -*- coding: utf-8 -*-
"""ERIS Learner V1: Root quyết định, evidence có nguồn, BKT chỉ shadow.

Không dùng network/API thật. Các kiểm thử này cố ý gây retry, restart,
rollback và receipt lỗi để chứng minh data không bị ghi đúp.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from llm.deepseek_workers import normalize_worker_role
from runtime.capabilities.tutor import TutorCapability
from runtime.conversation.contracts import DeliveryReceipt
from runtime.learning_core import LearningService, LearningStore
from runtime.learning_core.belief_model import compute_belief
from runtime.learning_core.contracts import LearningEvidence
from runtime.tutor.model_reasoner import RuntimeTutorReasoner


def _evidence(kind, index, *, correct=True, interaction_id=""):
    return LearningEvidence(
        evidence_id=f"e:{index}", kp_id="loop.for", evidence_type=kind,
        correct=correct, strength=1.0 if correct else 0.0,
        interaction_id=interaction_id, turn_id=f"t:{index}", recorded_at=1000.0 + index,
    )


def test_bkt_needs_repeated_independent_evidence_and_ignores_self_report():
    a = _evidence("quiz_correct", 1)
    b = _evidence("quiz_correct", 2)
    c = _evidence("application", 3)
    empty = compute_belief("loop.for", [])
    self_report = compute_belief("loop.for", [_evidence("self_report", 0)])
    one = compute_belief("loop.for", [a])
    three = compute_belief("loop.for", [a, b, c])
    assert empty.p_known == self_report.p_known
    assert one.needs_probe
    assert one.p_known > empty.p_known
    assert three.p_known > one.p_known
    assert three.independent_count == 3 and three.transfer_observed
    assert three.uncertainty < one.uncertainty
    assert not three.to_dict()["calibrated"]


def test_related_attempts_are_not_counted_as_independent():
    together = [
        _evidence("quiz_correct", i, interaction_id="same-task")
        for i in range(1, 4)
    ]
    varied = [_evidence("quiz_correct", i) for i in range(1, 4)]
    same_state = compute_belief("loop.for", together)
    varied_state = compute_belief("loop.for", varied)
    assert same_state.independent_count == 1
    assert varied_state.independent_count == 3
    assert same_state.p_known < varied_state.p_known


def test_semantic_retry_is_idempotent_even_after_restart(tmp_path):
    path = tmp_path / "learning.sqlite3"
    service = LearningService(LearningStore(path))
    first = service.commit_evidence(
        "loop.for", "feynman_pass", turn_id="answer-1",
        interaction_id="tm:quiz-1", now=1000.0,
    )
    duplicate = service.commit_evidence(
        "loop.for", "feynman_pass", turn_id="answer-1",
        interaction_id="tm:quiz-1", now=1090.0,
    )
    assert first and duplicate is None
    saved = service.learner_belief("loop.for")
    assert saved.evidence_seq == 1
    store_again = LearningStore(path)
    assert store_again.load_belief("loop.for").to_dict() == saved.to_dict()
    assert len(store_again.load_evidence("loop.for")) == 1
    assert store_again.load_mastery("loop.for").attempt_count == 1
    assert len(store_again.load_review_items(["loop.for"])) == 1


def test_transaction_rollback_does_not_leave_half_commit(tmp_path):
    store = LearningStore(tmp_path / "atomic.sqlite3")
    evidence = _evidence("quiz_correct", 1)

    def crash(*_args):
        raise RuntimeError("simulated_failure_between_insert_and_projection")

    with pytest.raises(RuntimeError):
        store.commit_evidence_bundle(evidence, crash, now=1001.0)
    assert not store.load_evidence("loop.for")
    assert store.load_mastery("loop.for").attempt_count == 0
    assert store.load_review_items(["loop.for"]) == []
    assert store.load_belief("loop.for").evidence_seq == 0


def test_unknown_semantic_event_without_stable_id_refused(tmp_path):
    service = LearningService(LearningStore(tmp_path / "learning.sqlite3"))
    assert service.commit_evidence("loop.for", "paraphrase", now=1000.0) is None
    assert service.learner_belief("loop.for").evidence_seq == 0


def test_new_learner_role_is_recognized():
    assert normalize_worker_role("learner") == "learner"


def test_semantic_assessment_only_uses_optional_learner_client():
    class Router:
        deep_client = object()

        def __init__(self):
            self.calls = []

        def invoke_structured_worker(self, client, **kw):
            self.calls.append((client, kw["name"]))
            return {}

    router = Router()
    learner = object()
    reasoner = RuntimeTutorReasoner(router, learner_client=learner)
    reasoner._call(system="s", payload={}, schema={}, name="eris_tutor_diagnosis_deep", max_tokens=10, priority=1)
    reasoner._call(system="s", payload={}, schema={}, name="eris_tutor_semantic_assessor_deep", max_tokens=10, priority=1)
    assert router.calls == [
        (learner, "eris_tutor_diagnosis_deep"),
        (learner, "eris_tutor_semantic_assessor_deep"),
    ]


def test_tutor_does_not_assess_before_successful_delivery():
    calls = []
    decision = SimpleNamespace(to_writer_context=lambda: {
        "move": {"kind": "assessment"},
        "delivery_constraints": {"requires_user_response": True},
    })
    class Executive:
        def plan_next_move(self, *args, **kwargs):
            calls.append("plan")
            return decision
        def observe_response(self, *args, **kwargs):
            calls.append("assess")
        def commit_delivery(self, *args, **kwargs):
            calls.append("receipt:" + str(kwargs["successful"]))
        def suspend_pending_move(self, **kwargs):
            calls.append("suspend")

    capability = TutorCapability(Executive())
    capability.advise(user_text="dạy đi", turn_id="t1")
    # Root chưa thực sự nói: turn kế tiếp không được chấm bài.
    capability.advise(user_text="chưa có câu hỏi", turn_id="t2")
    assert "assess" not in calls
    capability.observe_delivery_receipt(DeliveryReceipt(
        delivery_id="d1", session_id="s", generation=1,
        move_id="m1", successful=True,
    ))
    capability.advise(user_text="trả lời", turn_id="t3")
    assert "assess" in calls
