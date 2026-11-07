# -*- coding: utf-8 -*-
"""P2: chỉ suy ra *tín hiệu học tập* từ bằng chứng, không chẩn đoán người học.

Một lần trả lời đúng có gợi ý không chứng minh đã làm được độc lập.
Kết quả ở đây là read-model cho Tutor/Web UI, không phải mastery truth store.
"""
from __future__ import annotations

from typing import Any, Mapping, Sequence


def evidence_signals(attempts: Sequence[Mapping[str, Any]], *, patterns=(), due=False) -> dict[str, Any]:
    """Phân nhóm các lần làm theo họ câu hỏi, tránh đếm lặp cùng một bài.

    Ưu tiên bản mới nhất trong mỗi họ bài; ungraded không được tính là đáp án
    đúng/sai. Mức hỗ trợ chỉ đọc từ ledger, không do model tự gán.
    """
    rows = [r for r in attempts if not r.get("superseded_by")]
    families: dict[str, Mapping[str, Any]] = {}
    for row in sorted(rows, key=lambda r: (float(r.get("recorded_at") or 0), str(r.get("attempt_id") or ""))):
        key = str(row.get("item_family_id") or row.get("interaction_id") or row.get("attempt_id") or "")
        if key:
            families[key] = row
    graded = [r for r in families.values() if r.get("result") in {"correct", "incorrect"}]
    independent = [r for r in graded if r.get("assistance") == "independent" and not r.get("answer_revealed")]
    correct = sum(r.get("result") == "correct" for r in independent)
    incorrect = sum(r.get("result") == "incorrect" for r in independent)
    assisted = len(graded) - len(independent)
    # Chỉ đưa ra *giả thuyết* khi có lỗi lặp có evidence, không gắn nhãn năng lực.
    if patterns and incorrect >= 2:
        status, next_action = "possible_repeated_gap", "repair_then_independent_probe"
    elif due:
        status, next_action = "review_due", "independent_recall"
    elif incorrect > 0 and correct == 0:
        status, next_action = "needs_repair", "targeted_explanation_then_probe"
    elif not independent and assisted:
        status, next_action = "needs_independent_probe", "new_unassisted_item"
    elif not graded:
        status, next_action = "unassessed", "diagnostic_probe"
    else:
        status, next_action = "evidence_available", "continue_curriculum"
    return {
        "status": status,
        "next_action": next_action,
        "independent_families": len(independent),
        "independent_correct": correct,
        "independent_incorrect": incorrect,
        "assisted_families": assisted,
        "repeated_error_hypotheses": list(patterns)[:4],
        "caution": "Dấu hiệu dựa trên bài làm, chưa phải kết luận về năng lực.",
    }


__all__ = ["evidence_signals"]
