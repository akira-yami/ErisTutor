# -*- coding: utf-8 -*-
"""Bridge research artifact -> session/discourse ledger record.

Tách riêng để TutorDaemon không tự chế JSON research_thread mỗi nơi một kiểu.
Đây là bản nhẹ đúng hướng PDF: thread có query, provider_path, bundle id,
verified claim ids, open warnings và trạng thái verify.
"""

from __future__ import annotations

import time
from typing import Any, Mapping

try:  # pragma: no cover
    from cortex.tutor.secret_scrubber import scrub_secrets
except Exception:  # pragma: no cover
    def scrub_secrets(text: Any) -> str:  # type: ignore
        return str(text or "")


def build_research_thread_record(
    *,
    topic: Any,
    mode: str = "research",
    bundle: Mapping[str, Any] | dict[str, Any] | None = None,
    provider_prefix: str = "tutor_gemini_web",
) -> dict[str, Any]:
    data = dict(bundle or {})
    verification = dict(data.get("verification") or {})
    claims = list(data.get("claims") or [])
    verified_claim_ids = [
        str(claim.get("claim_id"))
        for claim in claims
        if claim.get("status") in {"supported", "weakly_supported"}
    ][:8]
    warnings = [str(item) for item in (verification.get("warnings") or []) if str(item).strip()]
    mode_norm = "quick_search" if str(mode or "") == "quick_search" else "research"
    bundle_id = str(data.get("bundle_id") or "")
    return {
        "thread_id": bundle_id or f"research_{int(time.time())}",
        "anchor_turn_ids": [],
        "standalone_query": scrub_secrets(topic)[:900],
        "provider_path": f"{provider_prefix}_{mode_norm}",
        "evidence_bundle_id": bundle_id,
        "verified_claim_ids": verified_claim_ids,
        "open_questions": warnings[:5],
        "status": str(verification.get("overall_status") or "unknown"),
        "citation_coverage": verification.get("citation_coverage", 0.0),
        "orphan_claim_count": verification.get("orphan_claim_count", 0),
        "contested_claim_count": verification.get("contested_claim_count", 0),
        "source_policy_pass": bool(verification.get("source_policy_pass", False)),
        "freshness_pass": bool(verification.get("freshness_pass", True)),
        "mode": mode_norm,
        "created_at": data.get("created_at", time.time()),
    }


__all__ = ["build_research_thread_record"]
