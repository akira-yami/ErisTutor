from __future__ import annotations

"""
memory.judgement.consolidation
==============================

Consolidation policy nhẹ cho claim memory.

Mục tiêu: không phải turn nào cũng thành ký ức ổn định. Feedback lặp lại, claim
mơ hồ, hoặc preference yếu nên có cơ hội nằm ở candidate trước rồi promote sau.
Hiện file này là policy thuần, chưa đụng store trực tiếp. Store muốn dùng thì gọi
`should_promote_candidate(...)` hoặc `merge_strength(...)`.
"""

from dataclasses import dataclass
from typing import Any, Mapping


@dataclass(frozen=True, slots=True)
class ConsolidationDecision:
    promote: bool
    merge: bool = True
    confidence_delta: float = 0.0
    reason: str = ""


def should_promote_candidate(candidate: Mapping[str, Any], *, observations: int = 1) -> ConsolidationDecision:
    """Quyết định candidate có nên promote lên memory ổn định không.

    Dùng cho các mảnh kiểu tone feedback/vibe/logic chưa parse sạch. Một lần nói
    thì giữ dấu vết; lặp lại hoặc có confidence đủ mới promote. Không phải câu nào
    user thở ra cũng thành kinh thánh local.
    """

    importance = _float(candidate.get("importance"), 0.0)
    confidence = _float(candidate.get("confidence"), 0.0)
    domain = str(candidate.get("domain_guess") or "")
    if observations >= 2 and importance >= 0.60 and confidence >= 0.50:
        return ConsolidationDecision(True, confidence_delta=0.08, reason="repeated_candidate")
    if domain in {"user_convention", "procedural_rule"} and importance >= 0.80:
        return ConsolidationDecision(True, confidence_delta=0.05, reason="explicit_convention")
    return ConsolidationDecision(False, reason="candidate_not_ready")


def merge_strength(existing_confidence: float, new_confidence: float, observations: int) -> float:
    """Tăng confidence có giới hạn khi claim được quan sát lặp lại."""

    base = max(_float(existing_confidence), _float(new_confidence))
    bonus = min(0.18, max(0, observations - 1) * 0.04)
    return min(0.98, base + bonus)


def _float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return default


__all__ = ["ConsolidationDecision", "should_promote_candidate", "merge_strength"]
