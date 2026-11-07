"""Spaced-repetition scheduler for the learning kernel.

Ported from DeepTutor learning/scheduler.py (Apache 2.0).

Algorithm: SM-2 variant with forgetting-curve retrievability.
- Interval grows on each correct recall: I(n) = I(n-1) × ease_factor
- Ease factor adjusted by quality score (like SM-2)
- Retrievability: R(t) = e^(-t / (S × half_life)) where S is stability
- forgetting_risk = 1 - R

No I/O. Caller (LearningService / LearningStore) owns persistence.
"""
from __future__ import annotations

import math
import time
from dataclasses import dataclass
from typing import Sequence

from runtime.learning_core.contracts import MasteryRecord, ReviewItem


# ---------------------------------------------------------------------------
# Constants (SM-2 defaults)
# ---------------------------------------------------------------------------

_DEFAULT_INTERVAL_DAYS: float = 1.0
_DEFAULT_EASE: float = 2.5
_MIN_EASE: float = 1.3
_MAX_EASE: float = 5.0
_HALF_LIFE_DAYS: float = 14.0    # stability constant for forgetting curve

# Quality → ease delta (mirrors SM-2 quality scale adapted for Eris)
# quality 0 = misconception, 1 = fail, 2 = fail-with-hint, 3 = correct, 4 = easy
_EASE_DELTA: dict[int, float] = {0: -0.3, 1: -0.2, 2: -0.1, 3: 0.0, 4: 0.1}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class ReviewScheduler:
    """Configurable SRS parameters."""

    half_life_days: float = _HALF_LIFE_DAYS
    default_ease: float = _DEFAULT_EASE
    min_ease: float = _MIN_EASE
    max_ease: float = _MAX_EASE
    max_interval_days: float = 180.0

    def __post_init__(self) -> None:
        def _f(v: object, d: float, lo: float, hi: float) -> float:
            try:
                n = float(v)  # type: ignore[arg-type]
            except (TypeError, ValueError):
                n = d
            return max(lo, min(hi, n))

        object.__setattr__(self, "half_life_days", _f(self.half_life_days, _HALF_LIFE_DAYS, 1.0, 365.0))
        object.__setattr__(self, "default_ease", _f(self.default_ease, _DEFAULT_EASE, 1.3, 5.0))
        object.__setattr__(self, "min_ease", _f(self.min_ease, _MIN_EASE, 1.1, 3.0))
        object.__setattr__(self, "max_ease", _f(self.max_ease, _MAX_EASE, 2.0, 10.0))
        object.__setattr__(self, "max_interval_days", _f(self.max_interval_days, 180.0, 1.0, 3650.0))


DEFAULT_SCHEDULER = ReviewScheduler()


def compute_retrievability(
    record: MasteryRecord,
    *,
    now: float | None = None,
    scheduler: ReviewScheduler = DEFAULT_SCHEDULER,
) -> float:
    """R(t) = e^(-t / (S × half_life)) where S is a stability proxy.

    Returns [0.0, 1.0]; 1.0 = just recalled, 0.0 = completely forgotten.
    """
    ts = float(now if now is not None else time.time())
    if record.last_evidence_at <= 0.0:
        return 1.0   # never studied; assume fresh
    age_days = max(0.0, (ts - record.last_evidence_at) / 86400.0)
    # Stability proxy: more correct attempts → longer half-life
    stability = max(0.5, min(10.0, 1.0 + record.correct_count * 0.5))
    hl = scheduler.half_life_days * stability
    return math.exp(-age_days / hl)


def next_review_item(
    record: MasteryRecord,
    current_item: ReviewItem | None,
    *,
    quality: int,
    now: float | None = None,
    scheduler: ReviewScheduler = DEFAULT_SCHEDULER,
) -> ReviewItem:
    """Compute the next ReviewItem after one recall attempt.

    Parameters
    ----------
    record:
        Current mastery record for the KP.
    current_item:
        Existing ReviewItem (may be None for first review).
    quality:
        0 = misconception, 1 = fail, 2 = partial, 3 = correct, 4 = easy.
    now:
        Current epoch seconds.
    scheduler:
        SRS parameters.
    """
    ts = float(now if now is not None else time.time())
    quality = max(0, min(4, quality))

    # Starting values
    prev_interval = current_item.interval_days if current_item else _DEFAULT_INTERVAL_DAYS
    prev_ease = current_item.ease_factor if current_item else scheduler.default_ease

    # Adjust ease factor
    ease_delta = _EASE_DELTA.get(quality, 0.0)
    new_ease = max(scheduler.min_ease, min(scheduler.max_ease, prev_ease + ease_delta))

    # New interval
    if quality < 2:
        # Reset on failure
        new_interval = _DEFAULT_INTERVAL_DAYS
    elif quality == 2:
        new_interval = prev_interval   # repeat same interval
    else:
        new_interval = min(scheduler.max_interval_days, prev_interval * new_ease)

    new_interval = max(0.1, new_interval)
    due_at = ts + new_interval * 86400.0

    # Forgetting risk right after review is low
    retrievability = compute_retrievability(record, now=ts, scheduler=scheduler)
    forgetting_risk = max(0.0, 1.0 - retrievability)

    return ReviewItem(
        kp_id=record.kp_id,
        due_at=due_at,
        forgetting_risk=forgetting_risk,
        interval_days=round(new_interval, 2),
        ease_factor=round(new_ease, 3),
    )


def due_items(
    items: Sequence[ReviewItem],
    *,
    now: float | None = None,
    max_items: int = 10,
) -> list[ReviewItem]:
    """Return overdue ReviewItems sorted by forgetting_risk (most urgent first)."""
    ts = float(now if now is not None else time.time())
    overdue = [r for r in items if r.due_at <= ts]
    overdue.sort(key=lambda r: -r.forgetting_risk)
    return overdue[:max_items]


def update_retrievability(
    items: Sequence[ReviewItem],
    *,
    records: dict[str, MasteryRecord],
    now: float | None = None,
    scheduler: ReviewScheduler = DEFAULT_SCHEDULER,
) -> list[ReviewItem]:
    """Recompute forgetting_risk for all items given current mastery records.

    Returns a new list; does not mutate inputs.
    """
    ts = float(now if now is not None else time.time())
    result: list[ReviewItem] = []
    for item in items:
        record = records.get(item.kp_id, MasteryRecord.blank(item.kp_id))
        ret = compute_retrievability(record, now=ts, scheduler=scheduler)
        risk = max(0.0, 1.0 - ret)
        result.append(ReviewItem(
            kp_id=item.kp_id,
            due_at=item.due_at,
            forgetting_risk=round(risk, 4),
            interval_days=item.interval_days,
            ease_factor=item.ease_factor,
        ))
    return result


__all__ = [
    "DEFAULT_SCHEDULER",
    "ReviewScheduler",
    "compute_retrievability",
    "due_items",
    "next_review_item",
    "update_retrievability",
]
