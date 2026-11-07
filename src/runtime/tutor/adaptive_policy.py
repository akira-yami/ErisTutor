# -*- coding: utf-8 -*-
"""LEARN-04: quyết định CHIẾN THUẬT trên bằng chứng bền vững, không lập Root mới.

Boundary:
- LearningService vẫn chọn curriculum step, sở hữu evidence/mastery/review.
- Module này chỉ *điều chỉnh bước chưa phát* theo lịch sử quan sát có nguồn.
- Không cho BKT shadow (chưa hiệu chỉnh) tự mở mastery gate hoặc gắn nhãn người học.
- Không sinh câu chữ public, không tự chạy khi người dùng không gọi Tutor qua Root.
- Mọi kết quả xác định từ SQLite; không dựa vào suy đoán từ một user turn.
"""
from __future__ import annotations

from dataclasses import replace
from typing import Sequence

from runtime.learning_core.contracts import LearningEvidence
from runtime.learning_core.records import independent
from runtime.learning_core.policy import TutorStep

# Không tính 'em hiểu rồi', exposure hoặc cặp bài trong một interaction là chứng cứ
# cho việc giảm hỗ trợ. Evidence chấm bài do LearningService ghi mới có giá trị.
_GRADED = frozenset({
    "quiz_correct", "quiz_incorrect", "feynman_pass", "feynman_fail",
    "application", "paraphrase", "misconception",
})


def _recent_independent(evidence: Sequence[LearningEvidence]) -> tuple[LearningEvidence, ...]:
    """Một đại diện MỚI NHẤT mỗi họ tương tác, không đếm lại cùng câu hỏi."""
    newest = sorted(evidence, key=lambda x: (x.recorded_at, x.evidence_id), reverse=True)
    seen_interactions: set[str] = set()
    seen_turns: set[str] = set()
    seen_legacy: set[str] = set()
    accepted: list[LearningEvidence] = []
    for ev in newest:
        if ev.evidence_type not in _GRADED:
            continue
        # Cùng pending question có thể tạo nhiều rows, chỉ lấy một row mới nhất.
        # Chỉ cần *một* trong hai định danh trùng là evidence có tương quan.
        # Không mặc định hai interaction_id khác nhau trong cùng một turn là
        # hai lần giải độc lập. Với legacy thiếu cả hai, fallback evidence_id.
        if ((ev.interaction_id and ev.interaction_id in seen_interactions)
                or (ev.turn_id and ev.turn_id in seen_turns)
                or (not ev.interaction_id and not ev.turn_id and ev.evidence_id in seen_legacy)):
            continue
        if ev.interaction_id:
            seen_interactions.add(ev.interaction_id)
        if ev.turn_id:
            seen_turns.add(ev.turn_id)
        if not ev.interaction_id and not ev.turn_id:
            seen_legacy.add(ev.evidence_id)
        accepted.append(ev)
    return tuple(accepted[:8])


def choose_adaptive_step(
    step: TutorStep,
    *,
    evidence: Sequence[LearningEvidence] = (),
    repeated_error_count: int = 0,
    knowledge_type: str = "concept",
) -> TutorStep:
    """Policy thuần; quyết định có thể replay từ cùng dữ liệu sau restart.

    Không thay các bước review/prerequisite/advance/complete/wait, vì chúng có
    precedence cao hơn chiến thuật. Không tự gọi LLM hoặc sửa bản ghi học tập.
    """
    if step.kind not in {"teach", "practice"} or step.reason == "prerequisite_unmet":
        return step
    attempts = _recent_independent(evidence)
    if not attempts:
        return step

    last = attempts[0]
    if last.correct and not independent(last):
        return replace(step, kind="assess", reason="adaptive_transfer_probe",
                       interaction_type="application")
    if not last.correct:
        # Lỗi một lần chỉ là tín hiệu sửa đúng khái niệm đang hỏi; ít nhất hai
        # interaction độc lập mới được nói đây là lỗi *tái diễn*.
        if repeated_error_count >= 1:
            return replace(step, kind="teach", reason="adaptive_repair_repeated_error",
                           interaction_type="short_answer")
        return replace(step, kind="teach", reason="adaptive_repair_after_failure",
                       interaction_type="short_answer")

    # Lượt đúng nhưng strength yếu không phải lý do cắt gợi ý hoặc bỏ giảng.
    # Đặc biệt không cho một "application" strength=.2 thành căn cứ cá nhân hóa.
    positives = [ev for ev in attempts if ev.correct and ev.strength >= 0.65 and independent(ev)]
    if not positives or not last.correct or last.strength < 0.65:
        return step
    transfer = any(ev.evidence_type == "application" and ev.correct for ev in positives)
    if len(positives) >= 2 and not transfer and knowledge_type in {"concept", "procedure", "design"}:
        # Hai bài giống nhau không đủ chứng minh transfer: kiểm tra một biến thể
        # KHÔNG gợi ý trước. Chỉ kiểm tra, không nâng mastery thay kernel.
        return replace(step, kind="assess", reason="adaptive_transfer_probe",
                       interaction_type="application")
    if step.kind == "teach":
        # Đã có một đáp án đúng: không lặp lại lecture, chuyển sang tự thực hành;
        # không nói người học "đã hiểu" khi bằng chứng còn mong manh.
        return replace(step, kind="practice", reason="adaptive_independent_practice",
                       interaction_type="application")
    return step


__all__ = ["choose_adaptive_step"]
