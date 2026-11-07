# -*- coding: utf-8 -*-
"""LEARN-05: mô hình ghi nhớ D/S/R **heuristic**, không tự nhận là FSRS.

Mastery = hiểu kiến thức; retrievability = ước lượng nhớ lại tại thời điểm t.
Chỉ evidence có nguồn và được Tutor/interaction đánh dấu recall mới được tăng
stability. Các lượt giải thích, hint, self-report không được cộng lần ôn.

Hàm thuần; SQLite transaction do LearningStore quản lý. Các thông số là mặc
định thử nghiệm, CHƯA hiệu chỉnh trên dữ liệu người học thật.
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass
from typing import Mapping

from runtime.learning_core.contracts import LearningEvidence, ReviewItem
from runtime.learning_core.records import independent as is_independent

DAY = 86400.0
_SUCCESS = frozenset({"quiz_correct", "feynman_pass", "application", "paraphrase"})
_FAILURE = frozenset({"quiz_incorrect", "feynman_fail", "misconception"})


def _bounded(value: float, low: float, high: float) -> float:
    try:
        v = float(value)
    except (ValueError, TypeError):
        v = low
    if not math.isfinite(v):
        v = low
    return max(low, min(high, v))


@dataclass(frozen=True, slots=True)
class ReviewMemory:
    kp_id: str
    difficulty: float = 5.0  # proxy [1, 10], không phải IRT difficulty
    stability_days: float = 1.0  # t sao cho R(t) xấp xỉ 90%
    anchor_at: float = 0.0  # lần thực hành đáng tin/recall gần nhất
    recall_count: int = 0
    lapse_count: int = 0

    @classmethod
    def from_mapping(cls, data: Mapping[str, object]) -> "ReviewMemory":
        return cls(
            kp_id=str(data.get("kp_id", ""))[:120],
            difficulty=_bounded(data.get("difficulty", 5), 1, 10),
            stability_days=_bounded(data.get("stability_days", 1), .1, 365),
            anchor_at=_bounded(data.get("anchor_at", 0), 0, 1e12),
            recall_count=max(0, min(1000000, int(data.get("recall_count", 0)))),
            lapse_count=max(0, min(1000000, int(data.get("lapse_count", 0)))),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "kp_id": self.kp_id,
            "difficulty": round(self.difficulty, 3),
            "stability_days": round(self.stability_days, 4),
            "anchor_at": self.anchor_at,
            "recall_count": self.recall_count,
            "lapse_count": self.lapse_count,
        }


def retrievability(memory: ReviewMemory, *, now: float) -> float:
    """Exponential forgetting proxy; 0.9 at elapsed == stability_days."""
    if memory.anchor_at <= 0:
        return 0.0  # không có quan sát, KHÔNG gán 100% remember
    age = max(0.0, now - memory.anchor_at) / DAY
    return _bounded(math.exp(math.log(.9) * age / memory.stability_days), 0, 1)


def _is_review(evidence: LearningEvidence) -> tuple[bool, int]:
    try:
        data = json.loads(evidence.metadata)
    except (TypeError, ValueError):
        return False, 0
    if not isinstance(data, dict) or data.get("review_recall") is not True:
        return False, 0
    try:
        hints = int(data.get("hint_count", 0))
    except (TypeError, ValueError):
        hints = 0
    return True, max(0, min(10, hints))


def update_review_memory(
    evidence: LearningEvidence,
    current_item: ReviewItem | None,
    current_memory: ReviewMemory | None,
    *,
    now: float,
    max_interval_days: float = 180.0,
) -> tuple[ReviewItem | None, ReviewMemory | None]:
    """Cập nhật lịch CHỈ từ recall hợp lệ; evidence khác không kéo giãn lịch.

    - Một success ban đầu khởi tạo lịch ôn nhưng không cộng recall_count.
    - Chỉ review đã phát + người học trả lời + assessment đủ chắc mới có
      metadata `review_recall` (gắn phía TutorExecutive/LearningService).
    - Không giả vờ đã thực hành nếu chỉ nhận exposure/self_report.
    - Lượt answer sai thông thường không tự reset lịch đã ấn định.
    """
    reviewed, hint_count = _is_review(evidence)
    if not is_independent(evidence):
        return current_item, current_memory
    good = evidence.correct and evidence.evidence_type in _SUCCESS and evidence.strength >= .65
    bad = not evidence.correct and evidence.evidence_type in _FAILURE
    if current_item is None and current_memory is None:
        # Weak-but-grated positive may seed an EARLY check, never a long interval.
        # Do not seed from self-report / exposure or any negative initial answer.
        initial_positive = (evidence.correct and evidence.evidence_type in _SUCCESS
                            and evidence.strength > 0)
        if not initial_positive and not (reviewed and bad):
            return None, None
        initial_s = 1.0 if good else (.25 if initial_positive else .1)
        memory = ReviewMemory(evidence.kp_id, stability_days=initial_s,
                              anchor_at=now if initial_positive else 0.0,
                              recall_count=1 if reviewed else 0,
                              lapse_count=1 if reviewed and bad else 0)
        delay = initial_s if initial_positive else .1
        return ReviewItem(evidence.kp_id, now + delay * DAY, 0.0 if good else .1,
                          interval_days=round(delay, 2), ease_factor=2.5), memory

    # Dữ liệu từ phiên bản cũ vẫn đọc được mà không chạy migration phá hủy:
    # item cũ là baseline cho S, nhưng không cho lần đọc tự ghi vào DB.
    if current_memory is None:
        current_memory = ReviewMemory(
            kp_id=evidence.kp_id,
            stability_days=_bounded(current_item.interval_days if current_item else 1.0, .1, 365),
            anchor_at=max(0, current_item.due_at - current_item.interval_days * DAY)
            if current_item else 0,
        )
    if not reviewed:
        return current_item, current_memory
    if not (good or bad or (evidence.correct and evidence.evidence_type in _SUCCESS)):
        return current_item, current_memory

    old = current_memory
    quality = _bounded(evidence.strength, 0, 1) if good else 0.0
    # Partial hoặc được gợi ý không phải một lần tự nhớ lại mạnh.
    independent = good and hint_count == 0
    if independent:
        # Tăng có giới hạn để tránh nhảy quá lớn khi dataset ít.
        gain = 1.45 + .55 * quality + .12 * min(3, old.recall_count)
        stability = min(max_interval_days, old.stability_days * gain)
        difficulty = max(1.0, old.difficulty - .18 * quality)
        delay = stability
    elif good:
        # Có gợi ý: không tính là tự nhớ. Giữ S và sớm kiểm tra độc lập.
        stability = old.stability_days
        difficulty = old.difficulty
        delay = min(1.0, max(.1, stability * .35))
    else:
        stability = max(.1, old.stability_days * .35)
        difficulty = min(10.0, old.difficulty + .4)
        delay = .1  # kiểm tra lại sau một khoảng, không nói ngay lập tức

    memory = ReviewMemory(
        kp_id=evidence.kp_id, difficulty=difficulty, stability_days=stability,
        anchor_at=now if independent else old.anchor_at,
        recall_count=old.recall_count + 1,
        lapse_count=old.lapse_count + int(not good),
    )
    risk = round(1 - retrievability(memory, now=now), 4)
    return ReviewItem(
        kp_id=evidence.kp_id, due_at=now + delay * DAY,
        forgetting_risk=risk, interval_days=round(delay, 2),
        ease_factor=current_item.ease_factor if current_item else 2.5,
    ), memory


def with_current_risk(item: ReviewItem, memory: ReviewMemory | None, *, now: float) -> ReviewItem:
    if memory is None:
        # legacy: không có anchor thật; tránh lấy stale risk làm hiện tại
        return item
    from dataclasses import replace
    return replace(item, forgetting_risk=round(1 - retrievability(memory, now=now), 4))


__all__ = ["ReviewMemory", "retrievability", "update_review_memory", "with_current_risk"]
