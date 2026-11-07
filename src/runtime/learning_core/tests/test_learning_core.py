"""Unit tests for the learning_core kernel.

Tests are pure (no I/O, no SQLite, no LLM calls) except for LearningStore
which gets a temp-file DB. All tests should run in < 1 second total.

Run with:
    python -m pytest src/runtime/learning_core/tests/ -v
"""
from __future__ import annotations

import math
import sys
import time
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Add project root to path so imports work without install
# ---------------------------------------------------------------------------
_PROJECT_ROOT = Path(__file__).parent.parent.parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT / "src"))


# ---------------------------------------------------------------------------
# contracts
# ---------------------------------------------------------------------------

class TestContracts:
    def test_knowledge_point_type_normalisation(self):
        from runtime.learning_core.contracts import KnowledgePoint, MASTERY_THRESHOLDS
        kp = KnowledgePoint(kp_id="test.kp", title="Test KP", kp_type="FACT")
        assert kp.kp_type == "fact"
        assert kp.mastery_threshold == MASTERY_THRESHOLDS["fact"]

    def test_knowledge_point_invalid_type_defaults(self):
        from runtime.learning_core.contracts import KnowledgePoint
        kp = KnowledgePoint(kp_id="x", title="X", kp_type="unknown_type")
        assert kp.kp_type == "concept"

    def test_mastery_record_bounds(self):
        from runtime.learning_core.contracts import MasteryRecord
        r = MasteryRecord(kp_id="x", mastery=1.5, confidence=-0.1)
        assert r.mastery == 1.0
        assert r.confidence == 0.0

    def test_mastery_record_blank(self):
        from runtime.learning_core.contracts import MasteryRecord
        r = MasteryRecord.blank("y")
        assert r.kp_id == "y"
        assert r.mastery == 0.0
        assert r.stage == "unknown"

    def test_learning_evidence_type_normalised(self):
        from runtime.learning_core.contracts import LearningEvidence
        ev = LearningEvidence(
            evidence_id="ev1", kp_id="k1", evidence_type="QUIZ_CORRECT"
        )
        # Normalisation lowercases; if not in EVIDENCE_TYPES falls back to "exposure"
        assert ev.evidence_type in {"quiz_correct", "exposure"}

    def test_pending_interaction_is_choice(self):
        from runtime.learning_core.contracts import PendingInteraction
        pi = PendingInteraction(
            interaction_id="i1", path_id="p1", kp_id="k1",
            interaction_type="multiple_choice",
            question_text="Q?", expected_answer="A",
            options=("A", "B", "C"), correct_index=0,
        )
        assert pi.is_choice is True

    def test_pending_interaction_feynman_not_choice(self):
        from runtime.learning_core.contracts import PendingInteraction
        pi = PendingInteraction(
            interaction_id="i2", path_id="p1", kp_id="k1",
            interaction_type="feynman_explain",
            question_text="Explain X", expected_answer="conceptual",
        )
        assert pi.is_choice is False

    def test_learning_path_requires_kp(self):
        from runtime.learning_core.contracts import LearningPath
        with pytest.raises(ValueError):
            LearningPath(path_id="p", title="T", goal="G", knowledge_points=())


# ---------------------------------------------------------------------------
# mastery
# ---------------------------------------------------------------------------

class TestMastery:
    def _make_evidence(self, kp_id: str, etype: str, correct: bool = True, at: float = 0.0):
        from runtime.learning_core.contracts import LearningEvidence
        return LearningEvidence(
            evidence_id=f"ev:{etype}:{at}",
            kp_id=kp_id,
            evidence_type=etype,
            strength=1.0 if correct else 0.0,
            correct=correct,
            recorded_at=at,
        )

    def test_no_evidence_returns_blank(self):
        from runtime.learning_core.mastery import compute_mastery
        r = compute_mastery("kp1", [], now=1000.0)
        assert r.mastery == 0.0
        assert r.stage == "unknown"

    def test_quiz_correct_raises_mastery(self):
        from runtime.learning_core.mastery import compute_mastery
        ev = self._make_evidence("kp1", "quiz_correct", correct=True, at=1000.0)
        r = compute_mastery("kp1", [ev], now=1000.0)
        assert r.mastery > 0.0
        assert r.correct_count == 1

    def test_misconception_reduces_mastery(self):
        from runtime.learning_core.mastery import compute_mastery
        ev_correct = self._make_evidence("kp1", "quiz_correct", correct=True, at=500.0)
        ev_misc = self._make_evidence("kp1", "misconception", correct=False, at=1000.0)
        r_clean = compute_mastery("kp1", [ev_correct], now=1000.0)
        r_misc = compute_mastery("kp1", [ev_correct, ev_misc], now=1000.0)
        assert r_misc.mastery < r_clean.mastery

    def test_gate_passed_fact_requires_2_corrects(self):
        from runtime.learning_core.contracts import KnowledgePoint, MasteryRecord
        from runtime.learning_core.mastery import gate_passed
        kp = KnowledgePoint(kp_id="k1", title="K1", kp_type="fact")
        r_one = MasteryRecord(kp_id="k1", mastery=0.95, correct_count=1)
        r_two = MasteryRecord(kp_id="k1", mastery=0.95, correct_count=2)
        assert gate_passed(r_one, kp) is False
        assert gate_passed(r_two, kp) is True

    def test_gate_passed_concept_requires_feynman(self):
        from runtime.learning_core.contracts import KnowledgePoint, MasteryRecord
        from runtime.learning_core.mastery import gate_passed
        kp = KnowledgePoint(kp_id="k2", title="K2", kp_type="concept")
        r = MasteryRecord(kp_id="k2", mastery=0.80, correct_count=3)
        assert gate_passed(r, kp, has_feynman_pass=False) is False
        assert gate_passed(r, kp, has_feynman_pass=True) is True


# ---------------------------------------------------------------------------
# grading
# ---------------------------------------------------------------------------

class TestGrading:
    def _make_choice_interaction(self, correct_index: int = 0):
        from runtime.learning_core.contracts import PendingInteraction
        return PendingInteraction(
            interaction_id="i1", path_id="p1", kp_id="k1",
            interaction_type="multiple_choice",
            question_text="Q?", expected_answer="A",
            options=("A", "B", "C", "D"),
            correct_index=correct_index,
        )

    def test_correct_integer_answer(self):
        from runtime.learning_core.grading import grade_choice
        interaction = self._make_choice_interaction(correct_index=1)
        result = grade_choice(interaction, 1)
        assert result.correct is True
        assert result.quality == 3

    def test_wrong_integer_answer(self):
        from runtime.learning_core.grading import grade_choice
        interaction = self._make_choice_interaction(correct_index=1)
        result = grade_choice(interaction, 2)
        assert result.correct is False
        assert result.quality == 1

    def test_letter_answer(self):
        from runtime.learning_core.grading import grade_choice
        interaction = self._make_choice_interaction(correct_index=0)
        result = grade_choice(interaction, "a")
        assert result.correct is True

    def test_string_digit_answer(self):
        from runtime.learning_core.grading import grade_choice
        interaction = self._make_choice_interaction(correct_index=2)
        result = grade_choice(interaction, "2")
        assert result.correct is True

    def test_text_match_answer(self):
        from runtime.learning_core.grading import grade_choice
        interaction = self._make_choice_interaction(correct_index=0)
        result = grade_choice(interaction, "A")
        assert result.correct is True

    def test_non_choice_raises(self):
        from runtime.learning_core.contracts import PendingInteraction
        from runtime.learning_core.grading import grade_choice
        pi = PendingInteraction(
            interaction_id="i2", path_id="p1", kp_id="k1",
            interaction_type="feynman_explain",
            question_text="Q", expected_answer="A",
        )
        with pytest.raises(ValueError):
            grade_choice(pi, "anything")


# ---------------------------------------------------------------------------
# scheduler
# ---------------------------------------------------------------------------

class TestScheduler:
    def test_compute_retrievability_just_reviewed(self):
        from runtime.learning_core.contracts import MasteryRecord
        from runtime.learning_core.scheduler import compute_retrievability
        now = 1_000_000.0
        r = MasteryRecord(kp_id="k1", last_evidence_at=now, correct_count=2)
        ret = compute_retrievability(r, now=now)
        assert ret > 0.99   # just studied

    def test_compute_retrievability_old_study(self):
        from runtime.learning_core.contracts import MasteryRecord
        from runtime.learning_core.scheduler import compute_retrievability
        # Use realistic Unix epoch (2026-01-01) to avoid negative timestamp clamping
        now = 1_767_225_600.0   # approx 2026-01-01
        old = now - 30 * 86400.0   # 30 days ago
        r = MasteryRecord(kp_id="k1", last_evidence_at=old, correct_count=1)
        ret = compute_retrievability(r, now=now)
        assert ret < 0.5

    def test_due_items_filters_correctly(self):
        from runtime.learning_core.contracts import ReviewItem
        from runtime.learning_core.scheduler import due_items
        now = 1_000_000.0
        items = [
            ReviewItem(kp_id="k1", due_at=now - 100, forgetting_risk=0.9),
            ReviewItem(kp_id="k2", due_at=now + 100, forgetting_risk=0.5),
            ReviewItem(kp_id="k3", due_at=now - 50, forgetting_risk=0.7),
        ]
        overdue = due_items(items, now=now)
        assert len(overdue) == 2
        assert overdue[0].kp_id == "k1"   # highest forgetting_risk first

    def test_next_review_item_correct_increases_interval(self):
        from runtime.learning_core.contracts import MasteryRecord, ReviewItem
        from runtime.learning_core.scheduler import next_review_item
        now = 1_000_000.0
        record = MasteryRecord(kp_id="k1", correct_count=2, last_evidence_at=now)
        current = ReviewItem(kp_id="k1", due_at=now, interval_days=3.0, ease_factor=2.5)
        next_item = next_review_item(record, current, quality=3, now=now)
        assert next_item.interval_days > current.interval_days

    def test_next_review_item_fail_resets_interval(self):
        from runtime.learning_core.contracts import MasteryRecord, ReviewItem
        from runtime.learning_core.scheduler import next_review_item
        now = 1_000_000.0
        record = MasteryRecord(kp_id="k1", correct_count=2, last_evidence_at=now)
        current = ReviewItem(kp_id="k1", due_at=now, interval_days=14.0, ease_factor=2.5)
        next_item = next_review_item(record, current, quality=1, now=now)
        assert next_item.interval_days == 1.0   # reset on fail


# ---------------------------------------------------------------------------
# pending
# ---------------------------------------------------------------------------

class TestPending:
    def _make_interaction(self, iid: str = "i1", path_id: str = "p1", kp_id: str = "k1"):
        from runtime.learning_core.contracts import PendingInteraction
        return PendingInteraction(
            interaction_id=iid, path_id=path_id, kp_id=kp_id,
            interaction_type="multiple_choice",
            question_text="Q?", expected_answer="A",
            options=("A", "B"), correct_index=0,
            expires_at=time.time() + 300,
        )

    def test_add_and_resolve(self):
        from runtime.learning_core.pending import PendingInteractionLedger
        ledger = PendingInteractionLedger()
        ix = self._make_interaction()
        assert ledger.add(ix) is True
        result = ledger.resolve("i1", 0)
        assert result is not None
        assert result.grading.correct is True

    def test_double_add_idempotent(self):
        from runtime.learning_core.pending import PendingInteractionLedger
        ledger = PendingInteractionLedger()
        ix = self._make_interaction()
        assert ledger.add(ix) is True
        assert ledger.add(ix) is False

    def test_resolve_twice_returns_none_second_time(self):
        from runtime.learning_core.pending import PendingInteractionLedger
        ledger = PendingInteractionLedger()
        ix = self._make_interaction()
        ledger.add(ix)
        ledger.resolve("i1", 0)
        assert ledger.resolve("i1", 0) is None

    def test_wrong_answer_graded_incorrect(self):
        from runtime.learning_core.pending import PendingInteractionLedger
        ledger = PendingInteractionLedger()
        ix = self._make_interaction()
        ledger.add(ix)
        result = ledger.resolve("i1", 1)   # wrong
        assert result is not None
        assert result.grading.correct is False

    def test_expired_interaction_not_resolvable(self):
        from runtime.learning_core.contracts import PendingInteraction
        from runtime.learning_core.pending import PendingInteractionLedger
        ledger = PendingInteractionLedger()
        now = time.time()
        ix = PendingInteraction(
            interaction_id="ix_exp", path_id="p1", kp_id="k1",
            interaction_type="multiple_choice",
            question_text="Q", expected_answer="A",
            options=("A", "B"), correct_index=0,
            expires_at=now - 10,   # already expired
        )
        ledger.add(ix)
        result = ledger.resolve("ix_exp", 0, now=now + 1)
        assert result is None   # expired → None


# ---------------------------------------------------------------------------
# policy
# ---------------------------------------------------------------------------

class TestPolicy:
    def _make_path(self, n_kps: int = 3):
        from runtime.learning_core.contracts import KnowledgePoint, LearningPath
        kps = tuple(
            KnowledgePoint(kp_id=f"kp{i}", title=f"KP {i}", kp_type="concept")
            for i in range(n_kps)
        )
        return LearningPath(path_id="path1", title="Test Path", goal="Learn stuff", knowledge_points=kps)

    def test_returns_probe_on_cold_start(self):
        from runtime.learning_core.policy import next_step
        path = self._make_path(3)
        step = next_step(path, "kp0", {}, now=1000.0)
        assert step.kind == "probe"
        assert step.kp_id == "kp0"

    def test_returns_teach_after_probe(self):
        from runtime.learning_core.contracts import MasteryRecord
        from runtime.learning_core.policy import next_step, _KPSession
        path = self._make_path(3)
        records = {"kp0": MasteryRecord.blank("kp0")}
        # Simulate: already probed once (assess_count = 1)
        session_counts = {"kp0": _KPSession(assess_count=1)}
        step = next_step(path, "kp0", records, session_counts=session_counts, now=1000.0)
        assert step.kind == "teach"

    def test_complete_when_all_gates_passed(self):
        from runtime.learning_core.contracts import KnowledgePoint, LearningPath, MasteryRecord
        from runtime.learning_core.policy import next_step, _KPSession
        kps = tuple(
            KnowledgePoint(kp_id=f"kp{i}", title=f"KP {i}", kp_type="fact")
            for i in range(2)
        )
        path = LearningPath(path_id="p", title="T", goal="G", knowledge_points=kps)
        records = {
            "kp0": MasteryRecord(kp_id="kp0", mastery=0.95, correct_count=3),
            "kp1": MasteryRecord(kp_id="kp1", mastery=0.95, correct_count=3),
        }
        step = next_step(path, "kp0", records, now=1000.0)
        assert step.kind == "complete"

    def test_due_review_takes_priority(self):
        from runtime.learning_core.contracts import ReviewItem
        from runtime.learning_core.policy import next_step
        path = self._make_path(3)
        due = [ReviewItem(kp_id="kp1", due_at=999.0, forgetting_risk=0.9)]
        step = next_step(path, "kp0", {}, due_reviews=due, now=1000.0)
        assert step.kind == "review"
        assert step.kp_id == "kp1"


# ---------------------------------------------------------------------------
# activation
# ---------------------------------------------------------------------------

class TestActivation:
    def test_explicit_learning_no_path(self):
        from runtime.learning_core.activation import decide_activation, ActivationCooldown
        decision = decide_activation(
            "explain this to me",
            user_signals=["learning_intent"],
            cooldown=ActivationCooldown(),
            now=1000.0,
        )
        assert decision.level in {"episode", "microteach"}

    def test_suppress_on_topic_change(self):
        from runtime.learning_core.activation import decide_activation, ActivationCooldown
        decision = decide_activation(
            "let's talk about something else",
            user_signals=["topic_change", "learning_intent"],
            cooldown=ActivationCooldown(),
            now=1000.0,
        )
        assert decision.level == "normal"

    def test_due_review_triggers(self):
        from runtime.learning_core.activation import decide_activation, ActivationCooldown, _REVIEW_COOLDOWN
        # Ensure last_review_at is old enough to pass cooldown
        cd = ActivationCooldown()
        cd.last_review_at = 0.0   # never reviewed
        decision = decide_activation(
            "ok",
            has_due_reviews=True,
            due_review_kp_id="kp1",
            due_review_path_id="p1",
            due_review_risk=0.85,
            cooldown=cd,
            now=1_767_225_600.0,
        )
        assert decision.level == "due_review"
        assert decision.kp_id == "kp1"

    def test_cooldown_suppresses_episode(self):
        from runtime.learning_core.activation import decide_activation, ActivationCooldown
        cd = ActivationCooldown()
        cd.last_episode_at = 999.0   # just triggered
        decision = decide_activation(
            "explain",
            user_signals=["learning_intent"],
            active_path_id="p1",
            active_kp_id="kp1",
            cooldown=cd,
            now=1000.0,   # only 1s since last episode
        )
        # Should fall back to microteach or normal
        assert decision.level in {"microteach", "normal"}

    def test_snooze_suppresses_all_teaching(self):
        from runtime.learning_core.activation import decide_activation, ActivationCooldown
        cd = ActivationCooldown()
        cd.snooze(600, now=1000.0)
        decision = decide_activation(
            "explain",
            user_signals=["learning_intent"],
            has_due_reviews=True,
            due_review_kp_id="kp1",
            due_review_risk=0.99,
            cooldown=cd,
            now=1001.0,
        )
        assert decision.level == "normal"
