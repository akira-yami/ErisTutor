# -*- coding: utf-8 -*-
"""Metrics nhẹ cho research/search relay.

Không benchmark hoành tráng, chỉ gom vài số đúng PDF: citation coverage,
orphan claim, source tier, fallback honesty. Đủ để log/panel biết Eris vừa tra
được cái gì thay vì vỗ ngực như một dashboard SaaS quá tự tin.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping


@dataclass(slots=True)
class ResearchMetrics:
    mode: str = "research"
    source_count: int = 0
    claim_count: int = 0
    trusted_source_count: int = 0
    citation_coverage: float = 0.0
    orphan_claim_count: int = 0
    contested_claim_count: int = 0
    verification_status: str = "unknown"
    warning_count: int = 0
    should_surface_cautiously: bool = True
    tags: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "source_count": int(self.source_count),
            "claim_count": int(self.claim_count),
            "trusted_source_count": int(self.trusted_source_count),
            "citation_coverage": round(float(self.citation_coverage), 4),
            "orphan_claim_count": int(self.orphan_claim_count),
            "contested_claim_count": int(self.contested_claim_count),
            "verification_status": self.verification_status,
            "warning_count": int(self.warning_count),
            "should_surface_cautiously": bool(self.should_surface_cautiously),
            "tags": list(self.tags),
        }


def build_research_metrics(bundle: Mapping[str, Any] | dict[str, Any] | None, *, mode: str = "research") -> ResearchMetrics:
    data = dict(bundle or {})
    sources = list(data.get("sources") or [])
    claims = list(data.get("claims") or [])
    verification = dict(data.get("verification") or {})
    trusted = sum(1 for source in sources if int(_safe_int(source.get("trust_tier", 0))) >= 2)
    status = str(verification.get("overall_status") or "unknown")
    warnings = list(verification.get("warnings") or [])
    handler_results = list(verification.get("handler_results") or [])
    warning_count = len(warnings) + sum(1 for item in handler_results if not item.get("passed") and item.get("severity") == "warning")
    tags: list[str] = []
    if trusted:
        tags.append("trusted_source_seen")
    if status == "failed":
        tags.append("verify_failed")
    if int(_safe_int(verification.get("orphan_claim_count", 0))) > 0:
        tags.append("orphan_claims")
    if int(_safe_int(verification.get("contested_claim_count", 0))) > 0:
        tags.append("contested_claims")
    return ResearchMetrics(
        mode=str(mode or data.get("mode") or "research"),
        source_count=len(sources),
        claim_count=len(claims),
        trusted_source_count=trusted,
        citation_coverage=float(_safe_float(verification.get("citation_coverage", 0.0))),
        orphan_claim_count=int(_safe_int(verification.get("orphan_claim_count", 0))),
        contested_claim_count=int(_safe_int(verification.get("contested_claim_count", 0))),
        verification_status=status,
        warning_count=warning_count,
        should_surface_cautiously=status != "passed" or warning_count > 0,
        tags=tags,
    )


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


__all__ = ["ResearchMetrics", "build_research_metrics"]
