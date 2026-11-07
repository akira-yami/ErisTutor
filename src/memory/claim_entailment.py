from __future__ import annotations

"""
claim_entailment.py
===================

Quyết định claim nào được dùng để trả lời query nào.

Đây là tầng cứu ERIS khỏi retrieval kiểu:
    query: "quê tôi ở đâu"
    memory: "user thích Hà Tĩnh"
    answer: "quê bạn là Hà Tĩnh"

Không. Claim preference không entail hometown. Máy móc phải học phép lịch sự này bằng code.
"""

from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping

try:
    from .semantic_claim import SemanticClaim
    from .predicate_ontology import EntailmentType, entailment_for_target, predicate_for_fact_key
    from .query_intent import SemanticQueryIntent, compile_semantic_query
except Exception:  # pragma: no cover
    from semantic_claim import SemanticClaim  # type: ignore
    from predicate_ontology import EntailmentType, entailment_for_target, predicate_for_fact_key  # type: ignore
    from query_intent import SemanticQueryIntent, compile_semantic_query  # type: ignore


@dataclass(frozen=True, slots=True)
class ClaimEntailmentDecision:
    entailment: EntailmentType
    score: float = 0.0
    reason: str = ""
    warnings: tuple[str, ...] = field(default_factory=tuple)

    @property
    def can_answer(self) -> bool:
        return self.entailment == EntailmentType.EXACT and self.score >= 0.65


class ClaimEntailmentEngine:
    """Engine entailment giữa SemanticClaim và SemanticQueryIntent."""

    def evaluate(self, claim: SemanticClaim, query: SemanticQueryIntent | str) -> ClaimEntailmentDecision:
        intent = compile_semantic_query(query, allow_fallback=True) if isinstance(query, str) else query
        if not intent.is_known:
            return ClaimEntailmentDecision(EntailmentType.UNKNOWN, 0.0, "unknown_query_intent")
        if claim.subject != intent.subject:
            return ClaimEntailmentDecision(EntailmentType.UNKNOWN, 0.0, "subject_mismatch")

        # Nếu query yêu cầu predicate cụ thể mà claim predicate khác, ontology quyết định.
        entail = entailment_for_target(claim, intent.answer_target)
        if entail == EntailmentType.EXACT:
            score = min(1.0, 0.50 + claim.confidence * 0.45 + intent.confidence * 0.15)
            warnings: list[str] = []
            if intent.requires_freshness and claim.valid_until is not None:
                import time
                if claim.valid_until < time.time():
                    return ClaimEntailmentDecision(EntailmentType.FORBIDDEN, 0.0, "claim_expired_for_fresh_query", ("expired",))
            if claim.assertion == "denied":
                warnings.append("claim_is_denial")
            return ClaimEntailmentDecision(entail, score, "exact_predicate_target", tuple(warnings))

        if entail == EntailmentType.FORBIDDEN:
            return ClaimEntailmentDecision(entail, 0.0, "ontology_forbidden")
        if entail == EntailmentType.COMPATIBLE:
            return ClaimEntailmentDecision(entail, 0.35, "compatible_but_not_answer")
        if entail == EntailmentType.CONTRADICTS:
            return ClaimEntailmentDecision(entail, 0.0, "claim_contradicts_target")
        return ClaimEntailmentDecision(entail, 0.0, "not_entailed")

    def rank(self, claims: Iterable[SemanticClaim], query: SemanticQueryIntent | str) -> list[tuple[SemanticClaim, ClaimEntailmentDecision]]:
        out = [(claim, self.evaluate(claim, query)) for claim in claims]
        out.sort(key=lambda pair: pair[1].score, reverse=True)
        return out


def semantic_claim_from_memory_item(item: Any) -> SemanticClaim | None:
    """Lấy SemanticClaim từ SemanticMemoryItem hoặc dict cũ nếu có.

    Ưu tiên metadata.semantic_claim. Nếu item cũ chỉ có fact_key/value thì tạo claim nghèo hơn,
    đủ để entailment không chết. Nghèo nhưng trung thực, vẫn hơn giàu mà bịa.
    """

    data: Mapping[str, Any]
    if isinstance(item, Mapping):
        data = item
    else:
        try:
            data = item.to_dict()
        except Exception:
            data = getattr(item, "__dict__", {}) or {}
    metadata = data.get("metadata") if isinstance(data, Mapping) else {}
    if isinstance(metadata, Mapping) and isinstance(metadata.get("semantic_claim"), Mapping):
        try:
            from .semantic_claim import SemanticClaim
            return SemanticClaim.from_dict(metadata["semantic_claim"])
        except Exception:
            return None

    fact_key = ""
    if isinstance(metadata, Mapping):
        fact_key = str(metadata.get("fact_key") or "")
    if not fact_key:
        return None
    predicate = predicate_for_fact_key(fact_key)
    if not predicate:
        return None
    value = metadata.get("value") if isinstance(metadata, Mapping) else None
    if value in (None, ""):
        value = data.get("claim", "") if isinstance(data, Mapping) else ""
    try:
        from .semantic_claim import build_claim
    except Exception:  # pragma: no cover
        from semantic_claim import build_claim  # type: ignore
    return build_claim(
        subject="user" if fact_key.startswith("user.") else "unknown",
        predicate=predicate,
        object_text=str(value),
        object_value=value,
        object_type="unknown",
        confidence=float(data.get("confidence", 0.60) if isinstance(data, Mapping) else 0.60),
        evidence_text=str(data.get("claim", "") if isinstance(data, Mapping) else ""),
        evidence_ids=tuple(str(x) for x in data.get("evidence_ids", []) if str(x).strip()) if isinstance(data, Mapping) else (),
    )


__all__ = ["ClaimEntailmentDecision", "ClaimEntailmentEngine", "semantic_claim_from_memory_item"]
