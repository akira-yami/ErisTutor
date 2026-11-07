"""Recency-weighted mastery computation and hard mastery gates.

Ported from DeepTutor learning/mastery.py (Apache 2.0).
Key differences from the original:
- Uses Eris frozen-dataclass conventions (no Pydantic).
- `compute_mastery` takes an immutable evidence list rather than mutating DB.
- Confidence cap is a separate parameter, not stored separately.
- No DB I/O; caller (LearningService / LearningStore) handles persistence.
"""

from __future__ import annotations

import math
import time
from runtime.learning_core.records import independent, evidence_metadata
from dataclasses import dataclass
from typing import Sequence

from runtime.learning_core.contracts import (
    MASTERY_THRESHOLDS,
    KnowledgePoint,
    LearningEvidence,
    MasteryRecord,
)


# ---------------------------------------------------------------------------
# Mastery computation constants (mirrors DeepTutor values)
# ---------------------------------------------------------------------------

# Recency half-life for evidence weighting (seconds)
_HALF_LIFE_SECONDS: float = 7 * 24 * 3600.0  # 1 week

# Minimum / maximum evidence contribution per item
_EVIDENCE_WEIGHT_MIN: float = 0.10
_EVIDENCE_WEIGHT_MAX: float = 1.00

# Confidence cap: evidence alone cannot push mastery past this unless
# gate_passed() is also True (Feynman or quiz with threshold).
_CONFIDENCE_CAP: float = 0.95

# Mastery decay applied when a misconception is recorded
_MISCONCEPTION_DECAY: float = 0.05

# Strength bonus per evidence type
_STRENGTH_MAP: dict[str, float] = {
    "quiz_correct": 1.00,
    "feynman_pass": 1.00,
    "application": 0.85,
    "paraphrase": 0.70,
    "quiz_incorrect": 0.00,
    "feynman_fail": 0.00,
    "misconception": 0.00,
    "self_report": 0.20,
    "exposure": 0.10,
}


@dataclass(frozen=True, slots=True)
class MasteryPolicy:
    """Configurable parameters for mastery computation.

    Default values match DeepTutor's published thresholds.
    """

    half_life_seconds: float = _HALF_LIFE_SECONDS
    confidence_cap: float = _CONFIDENCE_CAP
    misconception_decay: float = _MISCONCEPTION_DECAY

    def __post_init__(self) -> None:
        def _f(v: object, d: float, lo: float, hi: float) -> float:
            try:
                n = float(v)  # type: ignore[arg-type]
            except (TypeError, ValueError):
                n = d
            return max(lo, min(hi, n))

        object.__setattr__(
            self, "half_life_seconds", _f(self.half_life_seconds, _HALF_LIFE_SECONDS, 3600.0, 365 * 86400.0)
        )
        object.__setattr__(self, "confidence_cap", _f(self.confidence_cap, _CONFIDENCE_CAP, 0.5, 1.0))
        object.__setattr__(self, "misconception_decay", _f(self.misconception_decay, _MISCONCEPTION_DECAY, 0.0, 0.5))


DEFAULT_POLICY = MasteryPolicy()


# ---------------------------------------------------------------------------
# Pure computation functions
# ---------------------------------------------------------------------------


def _recency_weight(evidence_at: float, now: float, half_life: float) -> float:
    """Exponential decay weight; older evidence contributes less."""
    age = max(0.0, now - evidence_at)
    if half_life <= 0.0:
        return 1.0
    return math.exp(-math.log(2) * age / half_life)


def compute_mastery(
    kp_id: str,
    evidence_list: Sequence[LearningEvidence],
    *,
    now: float | None = None,
    policy: MasteryPolicy = DEFAULT_POLICY,
) -> MasteryRecord:
    """Pure function: compute a new MasteryRecord from all evidence for a KP.

    Does NOT read or write any store. Caller must persist the result.

    Algorithm (mirrors DeepTutor recency-weighted mastery):
    1. Weight each evidence item by recency.
    2. Positive evidence (strength > 0) contributes proportionally.
    3. Misconceptions decay the accumulated score.
    4. Score is capped at confidence_cap unless gate is separately passed.
    5. Stage is assigned from the final mastery value.
    """
    ts = float(now if now is not None else time.time())
    kp_evidence = [e for e in evidence_list if e.kp_id == kp_id]

    if not kp_evidence:
        return MasteryRecord.blank(kp_id)

    # Sort chronologically
    kp_evidence = sorted(kp_evidence, key=lambda e: e.recorded_at)

    weighted_sum = 0.0
    weight_total = 0.0
    attempt_count = 0
    correct_count = 0
    error_codes: list[str] = []
    misconception_count = 0
    last_at = 0.0
    success_families: set[str] = set()
    success_turns: set[str] = set()
    # Keep the latest independent observation per explicit item family for
    # scoring. All attempts remain in the audit count, but reopening the same
    # template cannot drown out failures on other skills/items.
    scored = {}
    for ev in kp_evidence:
        if independent(ev):
            family = evidence_metadata(ev).get("item_family_id")
            scored[("family", family) if family else ("evidence", ev.evidence_id)] = ev
    scored_ids = {ev.evidence_id for ev in scored.values()}
    independent_attempts = 0

    for ev in kp_evidence:
        w = _recency_weight(ev.recorded_at, ts, policy.half_life_seconds)
        w = max(_EVIDENCE_WEIGHT_MIN, min(_EVIDENCE_WEIGHT_MAX, w))
        base_strength = _STRENGTH_MAP.get(ev.evidence_type, 0.0)
        # Use the stored strength field for types that support variable strength
        effective_strength = base_strength * max(0.0, min(1.0, ev.strength)) if base_strength > 0.0 else 0.0
        if not independent(ev):
            effective_strength = 0.0
        eligible = ev.evidence_id in scored_ids
        if eligible:
            weighted_sum += w * effective_strength
            weight_total += w

        if ev.evidence_type not in {"exposure", "self_report"}:
            attempt_count += 1
            if eligible:
                independent_attempts += 1
            family = evidence_metadata(ev).get("item_family_id") or ev.interaction_id or ev.turn_id or ev.evidence_id
            if (
                ev.correct
                and eligible
                and family not in success_families
                and (not ev.turn_id or ev.turn_id not in success_turns)
            ):
                correct_count += 1
                success_families.add(family)
                if ev.turn_id:
                    success_turns.add(ev.turn_id)

        if ev.evidence_type == "misconception" and eligible:
            misconception_count += 1
            if ev.error_code:
                error_codes.append(ev.error_code)

        last_at = max(last_at, ev.recorded_at)

    # Raw mastery from weighted evidence
    raw = (weighted_sum / weight_total) if weight_total > 0.0 else 0.0

    # Apply misconception decay (each misconception reduces raw score)
    for _ in range(misconception_count):
        raw -= policy.misconception_decay * max(0.0, raw)

    mastery = max(0.0, min(policy.confidence_cap, raw))

    # Confidence: reflects consistency of recent performance
    recent_correct = len(
        {
            evidence_metadata(e).get("item_family_id") or e.interaction_id or e.turn_id or e.evidence_id
            for e in sorted(scored.values(), key=lambda e: e.recorded_at)[-5:]
            if e.evidence_type in {"quiz_correct", "feynman_pass", "application"} and e.correct and independent(e)
        }
    )
    confidence = min(1.0, recent_correct / 5.0)

    stage = _assign_stage(mastery, independent_attempts, correct_count)

    return MasteryRecord(
        kp_id=kp_id,
        mastery=round(mastery, 4),
        confidence=round(confidence, 4),
        stage=stage,
        attempt_count=attempt_count,
        correct_count=correct_count,
        last_evidence_at=last_at,
        error_codes=tuple(dict.fromkeys(error_codes[-8:])),
    )


def _assign_stage(mastery: float, attempts: int, correct: int) -> str:
    """Map (mastery, attempts, correct) → stage name."""
    if mastery >= 0.90 and attempts >= 3 and correct >= 3:
        return "retained"
    if mastery >= 0.70 and attempts >= 2:
        return "mastered"
    if mastery >= 0.40 or attempts >= 1:
        return "practicing"
    return "learning"


def gate_passed(
    record: MasteryRecord,
    kp: KnowledgePoint,
    *,
    has_feynman_pass: bool = False,
) -> bool:
    """Return True if the learner has genuinely passed the mastery gate.

    Hard gate (DeepTutor convention):
    - fact / procedure: mastery >= threshold (default 0.90) AND >= 2 quiz-correct
    - concept / design: mastery >= threshold (0.75 / 0.70) AND has_feynman_pass
    """
    threshold = MASTERY_THRESHOLDS.get(kp.kp_type, 0.75)
    if record.mastery < threshold:
        return False
    if kp.kp_type in {"fact", "procedure"}:
        return record.correct_count >= 2
    # concept / design require Feynman explanation
    return has_feynman_pass


def update_mastery_from_evidence(
    record: MasteryRecord,
    new_evidence: LearningEvidence,
    kp: KnowledgePoint,
    *,
    all_evidence: Sequence[LearningEvidence],
    now: float | None = None,
    policy: MasteryPolicy = DEFAULT_POLICY,
) -> MasteryRecord:
    """Convenience: recompute mastery after appending one new evidence item.

    Caller owns persistence; this is a pure function.
    """
    combined = [*all_evidence, new_evidence]
    return compute_mastery(kp.kp_id, combined, now=now, policy=policy)


__all__ = [
    "DEFAULT_POLICY",
    "MasteryPolicy",
    "compute_mastery",
    "gate_passed",
    "update_mastery_from_evidence",
]
