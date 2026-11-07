# -*- coding: utf-8 -*-
"""Memory policy cho kết quả Tutor research/search.

Mặc định Eris KHÔNG ghi kết quả web vào long-term memory. Khi user bật
ERIS_TUTOR_RESEARCH_PERSIST=1, lớp này vẫn chặn các bundle nguồn yếu/mâu thuẫn
để tránh biến một câu trả lời tạm thời thành ký ức khảo cổ. Đúng, ngay cả bộ nhớ
cũng cần hàng rào, vì web rất thích mục nát theo lịch.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping


@dataclass(frozen=True, slots=True)
class ResearchMemoryDecision:
    should_persist: bool
    reason: str
    score: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "should_persist": bool(self.should_persist),
            "reason": self.reason,
            "score": round(float(self.score), 4),
        }


def should_persist_research_answer(
    config: Any,
    bundle: Mapping[str, Any] | dict[str, Any] | None,
    *,
    mode: str = "research",
    explicit_memory_request: bool = False,
) -> ResearchMemoryDecision:
    """Quyết định có cho digest long-term memory hay không.

    - Nếu user/chủ dự án không bật persist: chặn.
    - Search nhanh về dữ kiện thời điểm: chặn trừ khi explicit_memory_request.
    - Bundle failed/nguồn rỗng/citation quá thấp: chặn.
    - Bundle pass hoặc pass_with_warnings nhẹ: cho ghi nếu persist bật.
    """

    persist_enabled = bool(getattr(config, "research_persist_answers", False))
    try:
        from runtime.runtime_settings_store import runtime_bool
        # Runtime setting chỉ có quyền BẬT thêm persist, không được âm thầm tắt
        # config test/explicit config đã bật. Không thì unit test thành trò bói file
        # settings còn sót lại, một loại nghệ thuật không ai xin.
        persist_enabled = persist_enabled or bool(runtime_bool("tutor", "persist_research_memory", default=False))
    except Exception:
        pass
    if not persist_enabled and not explicit_memory_request:
        return ResearchMemoryDecision(False, "persist_disabled", 0.0)

    data = dict(bundle or {})
    verification = dict(data.get("verification") or {})
    sources = list(data.get("sources") or [])
    claims = list(data.get("claims") or [])
    status = str(verification.get("overall_status") or "unknown")
    coverage = _safe_float(verification.get("citation_coverage", 0.0))
    orphan = _safe_int(verification.get("orphan_claim_count", 0))
    contested = _safe_int(verification.get("contested_claim_count", 0))
    trusted = sum(1 for source in sources if _safe_int(dict(source).get("trust_tier", 0)) >= 2)

    if explicit_memory_request:
        # Vẫn không ghi nếu hoàn toàn không có bundle, nhưng hạ ngưỡng vì user đang
        # yêu cầu nhớ. Ghi nhớ một thứ "có nguồn yếu" vẫn tốt hơn ghi nhớ một ảo ảnh.
        if sources and claims and status != "failed":
            return ResearchMemoryDecision(True, "explicit_memory_request", min(1.0, 0.55 + coverage))
        return ResearchMemoryDecision(False, "explicit_memory_but_unverified", coverage)

    if str(mode or "") == "quick_search":
        return ResearchMemoryDecision(False, "quick_search_ephemeral", coverage)
    if not sources:
        return ResearchMemoryDecision(False, "no_sources", coverage)
    if status == "failed":
        return ResearchMemoryDecision(False, "verification_failed", coverage)
    if contested > 0:
        return ResearchMemoryDecision(False, "contested_claims", coverage)
    if coverage < 0.58:
        return ResearchMemoryDecision(False, "low_citation_coverage", coverage)
    if orphan > 1:
        return ResearchMemoryDecision(False, "too_many_orphan_claims", coverage)
    if trusted <= 0 and status != "passed":
        return ResearchMemoryDecision(False, "no_trusted_source", coverage)

    score = coverage + (0.12 if trusted else 0.0) - (0.05 * orphan)
    return ResearchMemoryDecision(True, "verified_research_persist", max(0.0, min(1.0, score)))


def _safe_float(value: Any) -> float:
    try:
        return float(value)
    except Exception:
        return 0.0


def _safe_int(value: Any) -> int:
    try:
        return int(value)
    except Exception:
        return 0


__all__ = ["ResearchMemoryDecision", "should_persist_research_answer"]
