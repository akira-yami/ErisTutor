# -*- coding: utf-8 -*-
"""Verification handlers nhẹ cho Tutor research relay.

PDF nâng cấp Eris yêu cầu tách rõ lớp verify thay vì nuốt nguyên text Tutor.
Bản này giữ đường nhẹ, không gọi Gemini Deep Research/API grounding: chỉ kiểm
source policy, freshness, citation alignment, conflict và anchor consistency trên
EvidenceBundle đã trích từ câu trả lời Tutor Web.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping
from urllib.parse import urlparse


@dataclass(slots=True)
class VerificationHandlerResult:
    name: str
    passed: bool
    severity: str = "info"  # info | warning | error
    message: str = ""
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "passed": bool(self.passed),
            "severity": self.severity,
            "message": self.message,
            "details": dict(self.details),
        }


@dataclass(slots=True)
class VerificationHandlerSummary:
    results: list[VerificationHandlerResult] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return all(result.passed or result.severity != "error" for result in self.results)

    @property
    def error_count(self) -> int:
        return sum(1 for result in self.results if not result.passed and result.severity == "error")

    @property
    def warning_count(self) -> int:
        return sum(1 for result in self.results if not result.passed and result.severity == "warning")

    @property
    def warnings(self) -> list[str]:
        return [result.message for result in self.results if result.message and not result.passed][:8]

    def to_list(self) -> list[dict[str, Any]]:
        return [result.to_dict() for result in self.results]


_URL_RE = re.compile(r"https?://[^\s)\]}>\"']+", re.IGNORECASE)
_RECENT_HINT_RE = re.compile(
    r"\b(h[oô]m nay|hi[eệ]n t[aạ]i|b[aâ]y gi[oờ]|m[oớ]i nh[aấ]t|g[aầ]n [đd][aâ]y|release|phi[eê]n b[aả]n|gi[aá]|l[iị]ch|policy|ch[ií]nh s[aá]ch)\b",
    re.IGNORECASE,
)
_ABSOLUTE_DATE_RE = re.compile(r"\b(?:20\d{2}|\d{1,2}[/-]\d{1,2}[/-]\d{2,4})\b")
_STRONG_FACT_RE = re.compile(r"\b(ch[aắ]c ch[aắ]n|kh[aẳ]ng [đd][iị]nh|definitely|certainly|must|always)\b", re.IGNORECASE)
_NEGATED_SOURCE_RE = re.compile(r"\b(chưa c[oó] ngu[oồ]n|kh[oô]ng c[oó] ngu[oồ]n|chưa x[aá]c minh|kh[oô]ng x[aá]c minh)\b", re.IGNORECASE)
_TOKEN_RE = re.compile(r"[a-z0-9]{3,}", re.IGNORECASE)

_GOOD_SOURCE_TYPES = {"official", "government", "academic", "news"}
_ALLOWED_COMMUNITY_DOMAINS = {"stackoverflow.com", "github.com", "reddit.com"}


def run_evidence_verification_handlers(
    *,
    query: str = "",
    sources: Iterable[Any] = (),
    claims: Iterable[Any] = (),
    raw_answer: str = "",
    mode: str = "research",
    freshness_window_days: int | None = None,
    now: float | None = None,
) -> VerificationHandlerSummary:
    """Chạy bộ verify P0/P1 rẻ trên bundle đã có.

    Các handler cố ý không làm web fetch. Nếu thiếu dữ kiện, nó cảnh báo thay vì
    bịa ra độ chắc. Vâng, đôi khi phần mềm phải học cách nói “tôi không biết”.
    """

    source_list = list(sources or ())
    claim_list = list(claims or ())
    now_ts = float(now if now is not None else time.time())
    results = [
        verify_source_allowlist(source_list, mode=mode),
        verify_freshness_window(
            query=query,
            sources=source_list,
            raw_answer=raw_answer,
            mode=mode,
            freshness_window_days=freshness_window_days,
            now=now_ts,
        ),
        verify_citation_alignment(claim_list, source_list, mode=mode),
        verify_claim_conflict_cluster(claim_list),
        verify_anchor_consistency(query, claim_list, raw_answer),
        verify_surface_honesty(raw_answer, claim_list),
    ]
    return VerificationHandlerSummary(results=results)


def verify_source_allowlist(sources: Iterable[Any], *, mode: str = "research") -> VerificationHandlerResult:
    source_list = list(sources or ())
    if not source_list:
        return VerificationHandlerResult(
            "verify_source_allowlist",
            False,
            "error" if mode == "research" else "warning",
            "Không có nguồn/link để kiểm chính sách nguồn.",
            {"source_count": 0},
        )

    trusted = 0
    community = 0
    unknown = 0
    domains: list[str] = []
    for source in source_list:
        source_type = str(_get(source, "source_type", "unknown") or "unknown")
        domain = str(_get(source, "domain", "") or "").lower().removeprefix("www.")
        if not domain:
            url = str(_get(source, "url", "") or "")
            domain = urlparse(url).netloc.lower().removeprefix("www.") if url else ""
        if domain:
            domains.append(domain)
        if source_type in _GOOD_SOURCE_TYPES:
            trusted += 1
        elif source_type == "community" or domain in _ALLOWED_COMMUNITY_DOMAINS:
            community += 1
        else:
            unknown += 1

    if trusted:
        return VerificationHandlerResult(
            "verify_source_allowlist",
            True,
            "info",
            "Có nguồn trust-tier đủ dùng.",
            {"trusted": trusted, "community": community, "unknown": unknown, "domains": domains[:6]},
        )
    if community and not unknown:
        return VerificationHandlerResult(
            "verify_source_allowlist",
            False,
            "warning",
            "Chỉ thấy nguồn cộng đồng; dùng được để gợi ý, không dùng để chốt mạnh.",
            {"trusted": trusted, "community": community, "unknown": unknown, "domains": domains[:6]},
        )
    return VerificationHandlerResult(
        "verify_source_allowlist",
        False,
        "warning",
        "Nguồn không rõ tier/không thuộc nhóm ưu tiên.",
        {"trusted": trusted, "community": community, "unknown": unknown, "domains": domains[:6]},
    )


def verify_freshness_window(
    *,
    query: str = "",
    sources: Iterable[Any] = (),
    raw_answer: str = "",
    mode: str = "research",
    freshness_window_days: int | None = None,
    now: float | None = None,
) -> VerificationHandlerResult:
    text = f"{query}\n{raw_answer}"
    needs_recent = bool(_RECENT_HINT_RE.search(text or "")) or mode == "quick_search"
    if not needs_recent:
        return VerificationHandlerResult("verify_freshness_window", True, "info", "Không thấy yêu cầu freshness mạnh.")

    has_date = bool(_ABSOLUTE_DATE_RE.search(text or ""))
    source_dates = [str(_get(src, "published_at", "") or "") for src in sources or ()]
    source_dates = [item for item in source_dates if item]
    if has_date or source_dates:
        return VerificationHandlerResult(
            "verify_freshness_window",
            True,
            "info",
            "Có mốc thời gian/ngày tháng để người đọc tự kiểm freshness.",
            {"has_date_in_answer": has_date, "source_dates": source_dates[:4], "window_days": freshness_window_days},
        )
    return VerificationHandlerResult(
        "verify_freshness_window",
        False,
        "warning",
        "Câu cần dữ kiện mới nhưng answer không có mốc ngày rõ.",
        {"window_days": freshness_window_days, "checked_at": now},
    )


def verify_citation_alignment(claims: Iterable[Any], sources: Iterable[Any], *, mode: str = "research") -> VerificationHandlerResult:
    claim_list = list(claims or ())
    source_list = list(sources or ())
    factual_claims = [claim for claim in claim_list if str(_get(claim, "status", "")) != "contested"]
    if not factual_claims:
        return VerificationHandlerResult(
            "verify_citation_alignment",
            bool(source_list),
            "warning" if source_list else "error",
            "Không tách được claim factual để canh citation.",
            {"claim_count": len(claim_list), "source_count": len(source_list)},
        )

    aligned = 0
    orphan = 0
    for claim in factual_claims:
        support = list(_get(claim, "support_source_ids", []) or [])
        status = str(_get(claim, "status", "unsupported") or "unsupported")
        if support and status in {"supported", "weakly_supported"}:
            aligned += 1
        else:
            orphan += 1
    coverage = aligned / max(1, len(factual_claims))
    threshold = 0.72 if mode == "research" else 0.5
    if coverage >= threshold and orphan <= 1:
        return VerificationHandlerResult(
            "verify_citation_alignment",
            True,
            "info",
            "Citation coverage đủ cho đường nhẹ.",
            {"coverage": round(coverage, 4), "orphan": orphan, "claim_count": len(factual_claims)},
        )
    return VerificationHandlerResult(
        "verify_citation_alignment",
        False,
        "warning" if source_list else "error",
        "Claim chưa được gắn nguồn đủ dày.",
        {"coverage": round(coverage, 4), "orphan": orphan, "claim_count": len(factual_claims)},
    )


def verify_claim_conflict_cluster(claims: Iterable[Any]) -> VerificationHandlerResult:
    contested = [claim for claim in claims or () if str(_get(claim, "status", "")) == "contested"]
    if not contested:
        return VerificationHandlerResult("verify_claim_conflict_cluster", True, "info", "Không thấy cụm claim mâu thuẫn.")
    return VerificationHandlerResult(
        "verify_claim_conflict_cluster",
        False,
        "warning",
        "Có claim mâu thuẫn/contested, surface phải nói dè dặt.",
        {"contested_claim_ids": [str(_get(claim, "claim_id", "")) for claim in contested[:6]]},
    )


def verify_anchor_consistency(query: str, claims: Iterable[Any], raw_answer: str = "") -> VerificationHandlerResult:
    query_tokens = _content_tokens(query)
    if len(query_tokens) < 2:
        return VerificationHandlerResult("verify_anchor_consistency", True, "info", "Query quá ngắn, bỏ qua anchor check.")
    claim_text = " ".join(str(_get(claim, "text", "") or "") for claim in claims or ()) or str(raw_answer or "")
    answer_tokens = _content_tokens(claim_text)
    if not answer_tokens:
        return VerificationHandlerResult(
            "verify_anchor_consistency",
            False,
            "warning",
            "Không có claim/text để kiểm anchor.",
            {"query_tokens": sorted(query_tokens)[:8]},
        )
    overlap = len(query_tokens & answer_tokens) / max(1, len(query_tokens))
    if overlap >= 0.18 or bool(query_tokens & answer_tokens):
        return VerificationHandlerResult(
            "verify_anchor_consistency",
            True,
            "info",
            "Answer còn bám neo truy vấn.",
            {"overlap": round(overlap, 4), "matched": sorted(query_tokens & answer_tokens)[:8]},
        )
    return VerificationHandlerResult(
        "verify_anchor_consistency",
        False,
        "warning",
        "Answer có nguy cơ lệch neo truy vấn.",
        {"overlap": round(overlap, 4), "query_tokens": sorted(query_tokens)[:8]},
    )


def verify_surface_honesty(raw_answer: str, claims: Iterable[Any]) -> VerificationHandlerResult:
    text = str(raw_answer or "")
    has_strong_fact = bool(_STRONG_FACT_RE.search(text))
    has_negated_source = bool(_NEGATED_SOURCE_RE.search(text))
    unsupported = [claim for claim in claims or () if str(_get(claim, "status", "")) == "unsupported"]
    if has_strong_fact and (has_negated_source or unsupported):
        return VerificationHandlerResult(
            "verify_surface_honesty",
            False,
            "warning",
            "Answer dùng giọng chốt mạnh trong khi còn claim thiếu nguồn.",
            {"unsupported_claims": len(unsupported), "has_negated_source": has_negated_source},
        )
    return VerificationHandlerResult("verify_surface_honesty", True, "info", "Không thấy overclaim rõ.")


def _get(obj: Any, name: str, default: Any = None) -> Any:
    if isinstance(obj, Mapping):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _content_tokens(text: str) -> set[str]:
    stop = {
        "the", "and", "for", "with", "that", "this", "are", "was", "were", "from",
        "toi", "tôi", "ban", "bạn", "nay", "này", "kia", "do", "đó", "tren", "trên",
        "phan", "phần", "nghien", "nghiên", "cuu", "cứu", "search", "web", "gemini",
        "hay", "hãy", "giup", "giúp", "kiem", "kiểm", "chung", "chứng",
    }
    raw = str(text or "").lower().replace("đ", "d")
    tokens = {tok for tok in _TOKEN_RE.findall(raw) if tok not in stop and len(tok) >= 3}
    return tokens


__all__ = [
    "VerificationHandlerResult",
    "VerificationHandlerSummary",
    "run_evidence_verification_handlers",
    "verify_source_allowlist",
    "verify_freshness_window",
    "verify_citation_alignment",
    "verify_claim_conflict_cluster",
    "verify_anchor_consistency",
    "verify_surface_honesty",
]
