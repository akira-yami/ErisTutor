from __future__ import annotations

"""
memory_retention_policy.py
==========================

Tầng quyết định ký ức được giữ bao lâu.

Worthiness trả lời: "có nên lưu không?"
Retention trả lời: "lưu như loại gì, decay thế nào, bao giờ prune/demote?"

Đừng để mỗi item tự gán decay_policy theo cảm hứng. Đó là cách câu chào sống lâu hơn
thông tin tên người dùng, một nền văn minh đáng bị tắt nguồn.
"""

from dataclasses import dataclass, field
from typing import Any

try:
    from .semantic_claim import SemanticClaim
    from .predicate_ontology import get_predicate, default_decay_for_predicate
    from .memory_worthiness import WorthinessDecision
except Exception:  # pragma: no cover
    from semantic_claim import SemanticClaim  # type: ignore
    from predicate_ontology import get_predicate, default_decay_for_predicate  # type: ignore
    from memory_worthiness import WorthinessDecision  # type: ignore


@dataclass(frozen=True, slots=True)
class RetentionDecision:
    accept: bool
    target_layer: str
    status: str
    decay_policy: str
    retention_class: str
    importance: float
    revisit_after_seconds: float | None = None
    prune_after_seconds: float | None = None
    promote_if_observed: int = 1
    demote_if_unaccessed_days: int | None = None
    reason: str = ""
    warnings: tuple[str, ...] = field(default_factory=tuple)


class MemoryRetentionPolicy:
    """Policy retention cho SemanticClaim."""

    def decide(self, claim: SemanticClaim, worthiness: WorthinessDecision | None = None) -> RetentionDecision:
        spec = get_predicate(claim.predicate)
        worthiness = worthiness or WorthinessDecision(
            accept=True,
            target_layer=spec.storage_layer,
            status="active",
            importance=spec.importance,
            reason="implicit_worthiness",
        )

        if not worthiness.accept:
            return RetentionDecision(
                accept=False,
                target_layer=worthiness.target_layer,
                status=worthiness.status,
                decay_policy="session_only",
                retention_class="rejected",
                importance=worthiness.importance,
                reason=worthiness.reason,
                warnings=worthiness.warnings,
            )

        decay = default_decay_for_predicate(claim.predicate)
        retention = spec.retention_class
        prune_after: float | None = None
        revisit_after: float | None = None
        demote_days: int | None = None
        promote_if = 1
        warnings = list(worthiness.warnings)

        if retention == "candidate" or worthiness.target_layer == "memory_candidate":
            # Candidate là vùng chờ để consolidator chưng cất lại. Nó được giữ đủ
            # lâu để worker nền có cơ hội xử lý, nhưng không được surface như fact
            # ổn định. Một chút kỷ luật, thật kỳ lạ là lại cần viết ra.
            decay = "candidate"
            revisit_after = 3600.0
            prune_after = 86400.0 * 14
            demote_days = 14
            promote_if = 1
            warnings.append("candidate_requires_consolidation")
        elif retention == "short_term":
            # current_location, age_observed... không được sống dai như name/hometown.
            prune_after = 3600.0 if claim.predicate == "current_location_is" else 86400.0 * 30
            revisit_after = prune_after
            demote_days = 1
            decay = "fast"
            warnings.append("short_term_claim_requires_freshness")
        elif retention == "session":
            prune_after = 86400.0
            decay = "session_only"
        elif retention == "medium_term":
            demote_days = 60
            promote_if = 2 if claim.confidence < 0.72 else 1
        elif retention == "long_term":
            demote_days = 365
            decay = "slow"

        return RetentionDecision(
            accept=True,
            target_layer=worthiness.target_layer,
            status=worthiness.status,
            decay_policy=decay,
            retention_class=retention,
            importance=max(worthiness.importance, spec.importance),
            revisit_after_seconds=revisit_after,
            prune_after_seconds=prune_after,
            promote_if_observed=promote_if,
            demote_if_unaccessed_days=demote_days,
            reason=worthiness.reason or "retention_policy",
            warnings=tuple(dict.fromkeys(warnings)),
        )


def retention_metadata(decision: RetentionDecision) -> dict[str, Any]:
    return {
        "retention_class": decision.retention_class,
        "revisit_after_seconds": decision.revisit_after_seconds,
        "prune_after_seconds": decision.prune_after_seconds,
        "promote_if_observed": decision.promote_if_observed,
        "demote_if_unaccessed_days": decision.demote_if_unaccessed_days,
        "retention_reason": decision.reason,
        "retention_warnings": list(decision.warnings),
    }


__all__ = ["RetentionDecision", "MemoryRetentionPolicy", "retention_metadata"]
