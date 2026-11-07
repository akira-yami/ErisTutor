"""Navigation policy: decide the next tutor step given mastery state.

Ported from DeepTutor learning/policy.py and mastery_loop.yaml (Apache 2.0).

This is a pure state-machine; it has no I/O and does not call LLMs.
All non-deterministic parts (generating question text, explanation text)
are deferred to caller-supplied generators.

Step kinds (mirrors DeepTutor):
  probe       – assess current KP before teaching
  teach       – explain / present the KP
  practice    – give an application task
  assess      – quiz (choice) or Feynman explanation
  review      – spaced-repetition recall
  advance     – move to next KP in path
  complete    – all KPs passed, path done
  wait        – policy yields floor to user / companion
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Literal, Sequence

from runtime.learning_core.contracts import (
    KnowledgePoint,
    LearnerProfile,
    LearningPath,
    MasteryRecord,
    ReviewItem,
)
from runtime.learning_core.mastery import MasteryPolicy, DEFAULT_POLICY, gate_passed


# ---------------------------------------------------------------------------
# Step kinds
# ---------------------------------------------------------------------------

StepKind = Literal[
    "probe", "teach", "practice", "assess", "review", "advance", "complete", "wait"
]

STEP_KINDS: frozenset[str] = frozenset({
    "probe", "teach", "practice", "assess", "review", "advance", "complete", "wait"
})


@dataclass(frozen=True, slots=True)
class TutorStep:
    """The policy's output: what the tutor should do next."""

    kind: StepKind
    kp_id: str
    reason: str
    path_id: str = ""
    # hint for LLM call: style of interaction to generate
    interaction_type: str = "short_answer"
    # whether this can run autonomously or requires user acknowledgement first
    requires_user_ack: bool = False

    def to_dict(self) -> dict:
        return {
            "kind": self.kind,
            "kp_id": self.kp_id,
            "reason": self.reason,
            "path_id": self.path_id,
            "interaction_type": self.interaction_type,
            "requires_user_ack": self.requires_user_ack,
        }


# ---------------------------------------------------------------------------
# Policy config
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class NavigationPolicy:
    """Typed policy parameters for next-step selection.

    Default values mirror DeepTutor's published mastery thresholds and
    step ordering heuristics.
    """

    # After how many expose-without-probe turns to force a probe
    max_teach_before_assess: int = 2
    # Minimum mastery before skipping probe and going straight to teach
    probe_skip_threshold: float = 0.60
    # How many consecutive corrects before advancing KP
    advance_threshold_corrects: int = 2
    mastery_policy: MasteryPolicy = DEFAULT_POLICY

    def __post_init__(self) -> None:
        def _i(v: object, d: int, lo: int, hi: int) -> int:
            try:
                n = int(v)  # type: ignore[arg-type]
            except (TypeError, ValueError, OverflowError):
                n = d
            return max(lo, min(hi, n))

        def _f(v: object, d: float, lo: float, hi: float) -> float:
            try:
                n = float(v)  # type: ignore[arg-type]
            except (TypeError, ValueError):
                n = d
            return max(lo, min(hi, n))

        object.__setattr__(self, "max_teach_before_assess", _i(self.max_teach_before_assess, 2, 1, 8))
        object.__setattr__(self, "probe_skip_threshold", _f(self.probe_skip_threshold, 0.60, 0.0, 1.0))
        object.__setattr__(self, "advance_threshold_corrects", _i(self.advance_threshold_corrects, 2, 1, 6))


DEFAULT_NAV_POLICY = NavigationPolicy()


# ---------------------------------------------------------------------------
# Internal session state (ephemeral – not persisted)
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class _KPSession:
    """Ephemeral per-KP counter for one tutor session."""

    teach_count: int = 0
    assess_count: int = 0
    consecutive_correct: int = 0
    has_feynman_pass: bool = False

    def taught(self) -> "_KPSession":
        return replace(self, teach_count=self.teach_count + 1)

    def assessed(self, correct: bool) -> "_KPSession":
        feynman = self.has_feynman_pass  # preserved
        cc = (self.consecutive_correct + 1) if correct else 0
        return replace(
            self,
            assess_count=self.assess_count + 1,
            consecutive_correct=cc,
        )

    def feynman_passed(self) -> "_KPSession":
        return replace(self, has_feynman_pass=True)


# ---------------------------------------------------------------------------
# Main policy function
# ---------------------------------------------------------------------------

def next_step(
    path: LearningPath,
    current_kp_id: str,
    mastery_records: dict[str, MasteryRecord],
    *,
    due_reviews: Sequence[ReviewItem] = (),
    session_counts: dict[str, "_KPSession"] | None = None,
    learner: LearnerProfile | None = None,
    policy: NavigationPolicy = DEFAULT_NAV_POLICY,
    now: float | None = None,
) -> TutorStep:
    """Pure function: select the next tutor step.

    Parameters
    ----------
    path:
        The active LearningPath.
    current_kp_id:
        The KP currently being worked on.
    mastery_records:
        Map kp_id → MasteryRecord (may be incomplete; missing = blank).
    due_reviews:
        ReviewItems from the SRS that are overdue (already filtered by caller).
    session_counts:
        Ephemeral per-KP session counters for this session only.
    learner:
        Learner preferences (affects interaction_type selection).
    policy:
        Configurable thresholds.
    now:
        Current timestamp (for expiry checks; not used in pure logic here).
    """
    counts = session_counts or {}
    learner = learner or LearnerProfile()

    # ── 1. Due review takes highest priority ────────────────────────────────
    overdue = [r for r in due_reviews if r.kp_id in {kp.kp_id for kp in path.knowledge_points}]
    if overdue:
        top = sorted(overdue, key=lambda r: -r.forgetting_risk)[0]
        return TutorStep(
            kind="review",
            kp_id=top.kp_id,
            reason="spaced_review_due",
            path_id=path.path_id,
            interaction_type=_pick_review_type(learner),
        )

    # ── 2. Resolve current KP (or first unmastered) ─────────────────────────
    kp = _resolve_current_kp(path, current_kp_id, mastery_records, policy)
    if kp is None:
        # All KPs passed
        return TutorStep(kind="complete", kp_id="", reason="all_kps_mastered", path_id=path.path_id)

    record = mastery_records.get(kp.kp_id, MasteryRecord.blank(kp.kp_id))
    session = counts.get(kp.kp_id, _KPSession())

    # ── 3. Prerequisite check ───────────────────────────────────────────────
    unmet = _unmet_prerequisites(kp, path, mastery_records, policy)
    if unmet:
        # Redirect to first unmet prerequisite
        prereq_kp = next((k for k in path.knowledge_points if k.kp_id == unmet[0]), None)
        if prereq_kp:
            return TutorStep(
                kind="teach",
                kp_id=prereq_kp.kp_id,
                reason="prerequisite_unmet",
                path_id=path.path_id,
                interaction_type="short_answer",
            )

    # ── 4. Probe: assess before teaching if learner might already know ───────
    if (
        record.mastery < policy.probe_skip_threshold
        and session.teach_count == 0
        and session.assess_count == 0
        and record.attempt_count == 0
    ):
        return TutorStep(
            kind="probe",
            kp_id=kp.kp_id,
            reason="cold_start_probe",
            path_id=path.path_id,
            interaction_type=_pick_probe_type(kp),
        )

    # ── 5. After teach × N, must assess ─────────────────────────────────────
    if session.teach_count >= policy.max_teach_before_assess and session.assess_count == 0:
        return TutorStep(
            kind="assess",
            kp_id=kp.kp_id,
            reason="assess_after_teach",
            path_id=path.path_id,
            interaction_type=_pick_assess_type(kp, learner),
        )

    # ── 6. Gate check: if assessment shows mastery, advance ─────────────────
    if gate_passed(record, kp, has_feynman_pass=session.has_feynman_pass):
        next_kp = _next_kp(path, kp.kp_id)
        if next_kp is None:
            return TutorStep(kind="complete", kp_id=kp.kp_id, reason="all_kps_mastered", path_id=path.path_id)
        return TutorStep(
            kind="advance",
            kp_id=next_kp.kp_id,
            reason="gate_passed",
            path_id=path.path_id,
            interaction_type="short_answer",
        )

    # ── 7. Enough consecutive corrects → advance ─────────────────────────────
    if session.consecutive_correct >= policy.advance_threshold_corrects:
        next_kp = _next_kp(path, kp.kp_id)
        if next_kp is None:
            return TutorStep(kind="complete", kp_id=kp.kp_id, reason="consecutive_correct", path_id=path.path_id)
        return TutorStep(
            kind="advance",
            kp_id=next_kp.kp_id,
            reason="consecutive_correct",
            path_id=path.path_id,
        )

    # ── 8. Default: teach the current KP ────────────────────────────────────
    return TutorStep(
        kind="teach",
        kp_id=kp.kp_id,
        reason="teach_current_kp",
        path_id=path.path_id,
        interaction_type="short_answer",
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _resolve_current_kp(
    path: LearningPath,
    current_kp_id: str,
    records: dict[str, MasteryRecord],
    policy: NavigationPolicy,
) -> KnowledgePoint | None:
    """Return the KP to work on, or None if all are mastered."""
    # Try requested KP first
    for kp in path.knowledge_points:
        if kp.kp_id == current_kp_id:
            record = records.get(kp.kp_id, MasteryRecord.blank(kp.kp_id))
            if not gate_passed(record, kp):
                return kp

    # Otherwise find first unmastered KP in order
    for kp in path.knowledge_points:
        record = records.get(kp.kp_id, MasteryRecord.blank(kp.kp_id))
        if not gate_passed(record, kp):
            return kp
    return None


def _unmet_prerequisites(
    kp: KnowledgePoint,
    path: LearningPath,
    records: dict[str, MasteryRecord],
    policy: NavigationPolicy,
) -> list[str]:
    """Return prerequisite kp_ids that haven't passed their gate yet."""
    path_kp_map = {k.kp_id: k for k in path.knowledge_points}
    unmet: list[str] = []
    for prereq_id in kp.prerequisites:
        prereq_kp = path_kp_map.get(prereq_id)
        if prereq_kp is None:
            continue
        record = records.get(prereq_id, MasteryRecord.blank(prereq_id))
        if not gate_passed(record, prereq_kp):
            unmet.append(prereq_id)
    return unmet


def _next_kp(path: LearningPath, current_kp_id: str) -> KnowledgePoint | None:
    for i, kp in enumerate(path.knowledge_points):
        if kp.kp_id == current_kp_id:
            if i + 1 < len(path.knowledge_points):
                return path.knowledge_points[i + 1]
            return None
    return None


def _pick_probe_type(kp: KnowledgePoint) -> str:
    if kp.kp_type in {"fact", "procedure"}:
        return "multiple_choice"
    return "feynman_explain"


def _pick_assess_type(kp: KnowledgePoint, learner: LearnerProfile) -> str:
    if kp.kp_type in {"fact", "procedure"}:
        return "multiple_choice"
    if learner.preferred_style == "procedural":
        return "application"
    return "feynman_explain"


def _pick_review_type(learner: LearnerProfile) -> str:
    if learner.preferred_style == "procedural":
        return "application"
    return "multiple_choice"


__all__ = [
    "DEFAULT_NAV_POLICY",
    "NavigationPolicy",
    "StepKind",
    "TutorStep",
    "next_step",
]
