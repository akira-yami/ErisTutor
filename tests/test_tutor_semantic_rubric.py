from __future__ import annotations

import base64
import time
from types import SimpleNamespace
import pytest

from runtime.learning_core import LearningService, LearningStore
from runtime.tutor.activities import LearningActivities
from runtime.tutor.contracts import SemanticAssessment


def encode_text_answer(text: str) -> str:
    raw = base64.urlsafe_b64encode(text.encode("utf-8")).decode("ascii").rstrip("=")
    return f"t:{raw}"


class FakeAssessor:
    def __init__(self, verdict="pass", confidence=0.85, summary="Mental model tốt."):
        self.verdict = verdict
        self.confidence = confidence
        self.summary = summary
        self.called = 0

    def assess_response(self, observation, state, move):
        self.called += 1
        return SemanticAssessment(
            kp_id=move.kp_id,
            verdict=self.verdict,
            confidence=self.confidence,
            evidence_type="feynman_pass" if self.verdict == "pass" else "feynman_fail",
            strength=0.9 if self.verdict == "pass" else 0.1,
            summary=self.summary,
            misconception="" if self.verdict == "pass" else "Nhầm lẫn điều kiện",
        )


def test_reflection_with_semantic_assessor_pass_commits_mastery(tmp_path):
    store = LearningStore(tmp_path / "learning.sqlite3")
    learning = LearningService(store)
    assessor = FakeAssessor(verdict="pass", confidence=0.88)
    activities = LearningActivities(learning, assessor=assessor)

    event = SimpleNamespace(session_id="s-1", event_id="e-1", generation=1, text="")
    item = activities.create(event=event, preset="writing.explanation")

    receipt = SimpleNamespace(successful=True, session_id="s-1", generation=1)
    assert activities.mark_delivered(item["activity_id"], receipt=receipt)

    user_text = "Khi tự làm trước, não bộ kích hoạt retrieval practice và phát hiện điểm mù nhận thức."
    encoded = encode_text_answer(user_text)
    answer_event = SimpleNamespace(
        session_id="s-1", event_id="e-2", generation=1,
        text=activities.command(item, "answer", encoded)
    )

    result = activities.handle_event(answer_event)
    assert result["status"] == "completed"
    assert result["committed"] is True
    assert result["grading_status"] == "semantic_rubric"
    assert result["attempt"]["result"] == "correct"
    assert result["attempt"]["evidence_type"] == "feynman_pass"
    assert result["attempt"]["rubric_version"] == "feynman-rubric-v1"
    assert assessor.called == 1

    # Verify attempt in store
    attempts = store.load_attempts(item["kp_id"])
    assert len(attempts) == 1
    assert attempts[0]["result"] == "correct"
    assert attempts[0]["evidence_type"] == "feynman_pass"


def test_reflection_low_confidence_falls_back_to_ungraded(tmp_path):
    store = LearningStore(tmp_path / "learning.sqlite3")
    learning = LearningService(store)
    assessor = FakeAssessor(verdict="pass", confidence=0.55)  # Under 0.70 threshold
    activities = LearningActivities(learning, assessor=assessor)

    event = SimpleNamespace(session_id="s-1", event_id="e-1", generation=1, text="")
    item = activities.create(event=event, preset="writing.explanation")

    receipt = SimpleNamespace(successful=True, session_id="s-1", generation=1)
    activities.mark_delivered(item["activity_id"], receipt=receipt)

    encoded = encode_text_answer("em nghĩ là nên làm trước")
    answer_event = SimpleNamespace(
        session_id="s-1", event_id="e-2", generation=1,
        text=activities.command(item, "answer", encoded)
    )

    result = activities.handle_event(answer_event)
    assert result["status"] == "completed"
    assert result["grading_status"] == "ungraded"
    assert result["attempt"]["result"] == "ungraded"
    assert result["attempt"]["evidence_type"] == "exposure"
