# -*- coding: utf-8 -*-
"""EvidenceBundle nhẹ cho Tutor/Gemini Web relay.

Bản PDF muốn EvidenceBundle + verification handler rõ ràng. Bản này giữ tinh thần
đó nhưng không kéo Gemini API/Google grounding: chỉ chuẩn hoá text Tutor Web trả về,
tách nguồn/claim, chấm orphan claim và tạo record đủ sạch để đưa vào ledger.
"""

from __future__ import annotations

import hashlib
import re
import time
from dataclasses import dataclass, field
from typing import Any, Iterable, Literal
from urllib.parse import urlparse

try:  # pragma: no cover
    from cortex.tutor.secret_scrubber import scrub_secrets
except Exception:  # pragma: no cover
    from .secret_scrubber import scrub_secrets  # type: ignore

try:  # pragma: no cover
    from cortex.tutor.verification_handlers import run_evidence_verification_handlers
except Exception:  # pragma: no cover
    from .verification_handlers import run_evidence_verification_handlers  # type: ignore

SourceType = Literal["official", "government", "academic", "news", "community", "unknown"]
ClaimStatus = Literal["supported", "weakly_supported", "unsupported", "contested"]
VerificationStatus = Literal["passed", "passed_with_warnings", "failed"]

_URL_RE = re.compile(r"https?://[^\s)\]}>\"']+", re.IGNORECASE)
_SOURCE_LINE_RE = re.compile(r"^\s*(?:[-*•]\s*)?(?:ngu[oồ]n|source|sources|tham kh[aả]o|link)\b\s*[:：-]?\s*(.+)$", re.IGNORECASE)
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?。！？])\s+|\n+")
_UNCERTAIN_RE = re.compile(r"\b(chưa chắc|kh[oô]ng r[oõ]|c[oó] th[eể]|chưa x[aá]c minh|kh[oô]ng t[iì]m th[aấ]y|seems?|might|maybe)\b", re.IGNORECASE)
_CONFLICT_RE = re.compile(r"\b(mâu thuẫn|trái ngược|khác nhau|contested|conflict|disagree)\b", re.IGNORECASE)
_DATE_RE = re.compile(r"\b(?:20\d{2}|\d{1,2}[/-]\d{1,2}[/-]\d{2,4})\b")

_GOV_SUFFIXES = (".gov", ".gov.vn", ".mil", ".int")
_ACADEMIC_HINTS = ("arxiv.org", "doi.org", "nature.com", "science.org", "springer.com", "acm.org", "ieee.org", "edu", "scholar.google")
_NEWS_HINTS = ("reuters.com", "apnews.com", "bbc.", "nytimes.com", "theverge.com", "vnexpress.net", "tuoitre.vn", "thanhnien.vn")
_COMMUNITY_HINTS = ("reddit.com", "stackoverflow.com", "medium.com", "blog", "forum", "facebook.com", "x.com", "twitter.com")
_OFFICIAL_HINTS = ("docs.", "developer.", "support.", "help.", "github.com", "ai.google.dev", "openai.com", "anthropic.com", "microsoft.com", "apple.com", "nvidia.com", "godotengine.org")


def _short_id(prefix: str, seed: str) -> str:
    digest = hashlib.sha1(seed.encode("utf-8", errors="ignore")).hexdigest()[:10]
    return f"{prefix}_{digest}"


@dataclass(slots=True)
class EvidenceSource:
    source_id: str
    url: str = ""
    title: str = ""
    domain: str = ""
    source_type: SourceType = "unknown"
    published_at: str | None = None
    retrieved_at: float = 0.0
    freshness_ok: bool = True
    trust_tier: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_id": self.source_id,
            "url": self.url,
            "title": self.title,
            "domain": self.domain,
            "source_type": self.source_type,
            "published_at": self.published_at,
            "retrieved_at": self.retrieved_at,
            "freshness_ok": self.freshness_ok,
            "trust_tier": self.trust_tier,
        }


@dataclass(slots=True)
class EvidenceClaim:
    claim_id: str
    text: str
    support_source_ids: list[str] = field(default_factory=list)
    contradict_source_ids: list[str] = field(default_factory=list)
    status: ClaimStatus = "unsupported"

    def to_dict(self) -> dict[str, Any]:
        return {
            "claim_id": self.claim_id,
            "text": self.text,
            "support_source_ids": list(self.support_source_ids),
            "contradict_source_ids": list(self.contradict_source_ids),
            "status": self.status,
        }


@dataclass(slots=True)
class VerificationReport:
    source_policy_pass: bool = False
    freshness_pass: bool = True
    citation_coverage: float = 0.0
    orphan_claim_count: int = 0
    contested_claim_count: int = 0
    overall_status: VerificationStatus = "failed"
    warnings: list[str] = field(default_factory=list)
    handler_results: list[dict[str, Any]] = field(default_factory=list)
    surface_caution: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_policy_pass": self.source_policy_pass,
            "freshness_pass": self.freshness_pass,
            "citation_coverage": round(float(self.citation_coverage), 4),
            "orphan_claim_count": int(self.orphan_claim_count),
            "contested_claim_count": int(self.contested_claim_count),
            "overall_status": self.overall_status,
            "warnings": list(self.warnings),
            "handler_results": list(self.handler_results),
            "surface_caution": bool(self.surface_caution),
        }


@dataclass(slots=True)
class EvidenceBundle:
    bundle_id: str
    query: str
    mode: str = "research"
    sources: list[EvidenceSource] = field(default_factory=list)
    claims: list[EvidenceClaim] = field(default_factory=list)
    verification: VerificationReport = field(default_factory=VerificationReport)
    created_at: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "bundle_id": self.bundle_id,
            "query": self.query,
            "mode": self.mode,
            "created_at": self.created_at,
            "sources": [source.to_dict() for source in self.sources],
            "claims": [claim.to_dict() for claim in self.claims],
            "verification": self.verification.to_dict(),
        }

    def compact_public_note(self) -> str:
        v = self.verification
        if v.overall_status == "passed":
            return "Nguồn/claim đã qua kiểm tra nhẹ."
        if v.overall_status == "passed_with_warnings":
            return "Nguồn/claim có cảnh báo nhẹ, em không chốt quá tay."
        return "Nguồn/claim chưa đủ sạch, chỉ xem như gợi ý."


def build_evidence_bundle(
    *,
    query: Any,
    raw_answer: Any,
    supported_points: Iterable[str] = (),
    weak_points: Iterable[str] = (),
    mode: str = "research",
    now: float | None = None,
) -> EvidenceBundle:
    """Chuẩn hoá answer Tutor thành bundle tối giản có verification.

    Không giả vờ có citation span chuẩn như API. Nếu câu không gắn nguồn rõ, nó là
    weak/unsupported. Sự trung thực nhàm chán này lại là thứ cứu agent khỏi nói bừa.
    """

    ts = float(now if now is not None else time.time())
    query_text = scrub_secrets(" ".join(str(query or "").split()))[:900]
    raw = scrub_secrets(str(raw_answer or ""))
    sources = _extract_sources(raw, ts)
    claims = _extract_claims(raw, sources=sources, supported_points=supported_points, weak_points=weak_points)
    verification = verify_evidence_bundle_parts(
        query=query_text,
        sources=sources,
        claims=claims,
        raw_answer=raw,
        mode=mode,
    )
    seed = f"{query_text}|{raw[:300]}|{ts:.3f}"
    return EvidenceBundle(
        bundle_id=_short_id("evb", seed),
        query=query_text,
        mode=str(mode or "research"),
        sources=sources,
        claims=claims,
        verification=verification,
        created_at=ts,
    )


def verify_evidence_bundle_parts(
    *,
    query: str = "",
    sources: list[EvidenceSource],
    claims: list[EvidenceClaim],
    raw_answer: str,
    mode: str = "research",
) -> VerificationReport:
    total = len(claims)
    supported = sum(1 for claim in claims if claim.status in {"supported", "weakly_supported"} and claim.support_source_ids)
    orphan = sum(1 for claim in claims if not claim.support_source_ids and claim.status != "contested")
    contested = sum(1 for claim in claims if claim.status == "contested")
    coverage = (supported / total) if total else (1.0 if sources else 0.0)
    source_policy_pass = bool(sources) and any(source.trust_tier >= 1 for source in sources)
    freshness_pass = True
    warnings: list[str] = []

    if not sources:
        warnings.append("Tutor không đưa nguồn/link rõ ràng.")
    if orphan:
        warnings.append(f"Có {orphan} claim chưa gắn nguồn.")
    if contested:
        warnings.append(f"Có {contested} claim có dấu hiệu mâu thuẫn/cần đối chiếu.")
    if mode == "quick_search" and sources and not _DATE_RE.search(raw_answer):
        warnings.append("Search nhanh không thấy mốc ngày rõ trong answer.")

    handler_summary = run_evidence_verification_handlers(
        query=query,
        sources=sources,
        claims=claims,
        raw_answer=raw_answer,
        mode=mode,
    )
    handler_warnings = list(getattr(handler_summary, "warnings", []) or [])
    for warning in handler_warnings:
        if warning not in warnings:
            warnings.append(warning)
    handler_results = handler_summary.to_list() if hasattr(handler_summary, "to_list") else []
    handler_error_count = int(getattr(handler_summary, "error_count", 0) or 0)
    handler_warning_count = int(getattr(handler_summary, "warning_count", 0) or 0)

    if source_policy_pass and coverage >= 0.72 and orphan <= 1 and handler_error_count == 0:
        overall: VerificationStatus = "passed" if not warnings and handler_warning_count == 0 else "passed_with_warnings"
    elif sources and coverage >= 0.35 and handler_error_count == 0:
        overall = "passed_with_warnings"
    else:
        overall = "failed"

    return VerificationReport(
        source_policy_pass=source_policy_pass,
        freshness_pass=freshness_pass,
        citation_coverage=coverage,
        orphan_claim_count=orphan,
        contested_claim_count=contested,
        overall_status=overall,
        warnings=warnings[:8],
        handler_results=handler_results,
        surface_caution=overall != "passed" or bool(warnings),
    )


def _extract_sources(text: str, retrieved_at: float) -> list[EvidenceSource]:
    urls: list[str] = []
    for url in _URL_RE.findall(text or ""):
        urls.append(url.rstrip(".,;"))
    for line in str(text or "").splitlines():
        match = _SOURCE_LINE_RE.match(line)
        if not match:
            continue
        body = match.group(1).strip()
        for url in _URL_RE.findall(body):
            urls.append(url.rstrip(".,;"))
        if body and not _URL_RE.search(body):
            # Tên nguồn không có URL vẫn giữ, nhưng trust thấp. Tutor Web nhiều lúc
            # trích kiểu này, đúng là citations phiên bản nghèo.
            urls.append(body[:180])

    out: list[EvidenceSource] = []
    seen: set[str] = set()
    for raw in urls:
        item = scrub_secrets(raw).strip()
        if not item or item in seen:
            continue
        seen.add(item)
        parsed = urlparse(item if item.startswith("http") else "")
        domain = parsed.netloc.lower().removeprefix("www.") if parsed.netloc else ""
        source_type = _classify_source(domain, item)
        trust = _trust_tier(source_type, domain)
        out.append(EvidenceSource(
            source_id=_short_id("src", item),
            url=item if item.startswith("http") else "",
            title="" if item.startswith("http") else item,
            domain=domain,
            source_type=source_type,
            published_at=_extract_source_date(item),
            retrieved_at=retrieved_at,
            freshness_ok=True,
            trust_tier=trust,
        ))
    return out[:8]


def _extract_claims(
    text: str,
    *,
    sources: list[EvidenceSource],
    supported_points: Iterable[str] = (),
    weak_points: Iterable[str] = (),
) -> list[EvidenceClaim]:
    source_ids = [source.source_id for source in sources]
    claims: list[EvidenceClaim] = []
    seeded: list[str] = [str(p or "").strip() for p in supported_points or () if str(p or "").strip()]
    seeded += [str(p or "").strip() for p in weak_points or () if str(p or "").strip()]
    if not seeded:
        seeded = [sent for sent in _split_sentences(text) if _looks_like_claim(sent)]

    for idx, sentence in enumerate(seeded[:10], 1):
        clean = _clean_claim(sentence)
        if not clean:
            continue
        inline_source_ids = _source_ids_in_text(clean, sources)
        if not inline_source_ids and source_ids and idx <= 4 and not _UNCERTAIN_RE.search(clean):
            # Nguồn gom ở cuối, không inline. Đánh weakly_supported chứ không supported.
            inline_source_ids = source_ids[:2]
            status: ClaimStatus = "weakly_supported"
        elif inline_source_ids:
            status = "supported"
        else:
            status = "unsupported"
        if _UNCERTAIN_RE.search(clean):
            status = "weakly_supported" if inline_source_ids else "unsupported"
        if _CONFLICT_RE.search(clean):
            status = "contested"
        claims.append(EvidenceClaim(
            claim_id=_short_id("cl", f"{idx}:{clean}"),
            text=clean[:360],
            support_source_ids=list(inline_source_ids),
            contradict_source_ids=[],
            status=status,
        ))
    return _dedupe_claims(claims)[:10]


def _source_ids_in_text(text: str, sources: list[EvidenceSource]) -> list[str]:
    found: list[str] = []
    for source in sources:
        if source.url and source.url in text:
            found.append(source.source_id)
        elif source.domain and source.domain in text.lower():
            found.append(source.source_id)
        elif source.title and source.title.lower() in text.lower():
            found.append(source.source_id)
    return found


def _split_sentences(text: str) -> list[str]:
    parts: list[str] = []
    for raw in _SENTENCE_SPLIT_RE.split(str(text or "")):
        clean = raw.strip(" \t-*•0123456789.)")
        if clean:
            parts.append(clean)
    return parts


def _looks_like_claim(text: str) -> bool:
    clean = str(text or "").strip()
    if len(clean) < 18:
        return False
    if _SOURCE_LINE_RE.match(clean):
        return False
    lowered = clean.lower()
    if lowered.startswith(("nguồn", "source", "sources", "tham khảo")):
        return False
    return True


def _clean_claim(text: str) -> str:
    clean = scrub_secrets(str(text or ""))
    clean = _URL_RE.sub("", clean)
    clean = re.sub(r"\s+", " ", clean).strip(" .;:-")
    return clean


def _classify_source(domain: str, raw: str) -> SourceType:
    d = (domain or raw or "").lower()
    if any(suffix in d for suffix in _GOV_SUFFIXES):
        return "government"
    if any(hint in d for hint in _ACADEMIC_HINTS):
        return "academic"
    if any(hint in d for hint in _OFFICIAL_HINTS):
        return "official"
    if any(hint in d for hint in _NEWS_HINTS):
        return "news"
    if any(hint in d for hint in _COMMUNITY_HINTS):
        return "community"
    return "unknown"


def _trust_tier(source_type: SourceType, domain: str) -> int:
    if source_type in {"official", "government", "academic"}:
        return 3
    if source_type == "news":
        return 2
    if source_type == "community":
        return 1
    return 1 if domain else 0


def _extract_source_date(raw: str) -> str | None:
    match = _DATE_RE.search(str(raw or ""))
    return match.group(0) if match else None


def _dedupe_claims(claims: Iterable[EvidenceClaim]) -> list[EvidenceClaim]:
    seen: set[str] = set()
    out: list[EvidenceClaim] = []
    for claim in claims:
        key = claim.text.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(claim)
    return out


__all__ = [
    "EvidenceBundle",
    "EvidenceClaim",
    "EvidenceSource",
    "VerificationReport",
    "build_evidence_bundle",
    "verify_evidence_bundle_parts",
]
