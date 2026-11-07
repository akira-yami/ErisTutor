from __future__ import annotations

"""
memory_worthiness.py
====================

Tầng quyết định claim có đáng được đưa vào memory hay không.

Extractor chỉ nói "tôi thấy một claim". Worthiness policy nói:
- claim này lưu dài hạn được không;
- claim này chỉ nên nằm trong session ledger;
- claim này nên vào candidate vì chưa đủ chắc;
- claim này nên bị reject thẳng.

Đây là hàng rào chống bệnh "mọi câu user nói đều là ký ức sâu sắc".
"""

from dataclasses import dataclass, field

try:
    from .semantic_claim import SemanticClaim
    from .predicate_ontology import get_predicate, storage_layer_for_predicate, importance_for_predicate
except Exception:  # pragma: no cover
    from semantic_claim import SemanticClaim  # type: ignore
    from predicate_ontology import get_predicate, storage_layer_for_predicate, importance_for_predicate  # type: ignore


@dataclass(frozen=True, slots=True)
class WorthinessDecision:
    accept: bool
    target_layer: str = "reject"
    status: str = "rejected"
    importance: float = 0.0
    reason: str = ""
    warnings: tuple[str, ...] = field(default_factory=tuple)


class MemoryWorthinessPolicy:
    """Policy chặn ghi nhớ bừa trước khi retention tính decay."""

    min_semantic_confidence: float = 0.62
    min_candidate_confidence: float = 0.48

    def decide(self, claim: SemanticClaim) -> WorthinessDecision:
        spec = get_predicate(claim.predicate)
        warnings: list[str] = []

        if claim.assertion in {"hypothetical", "quoted"}:
            return WorthinessDecision(False, reason="non_assertive_claim", warnings=("hypothetical_or_quoted",))

        # Greeting/question/reaction lẻ không phải semantic long-term.
        # Những thứ này thuộc SessionTurnLedger, không được đẩy vào SemanticMemory
        # chỉ vì chúng có vẻ giống một "fact". Ký ức dài hạn không phải thùng rác
        # của transcript, dù transcript rất thích giả vờ mình quan trọng.
        if claim.predicate in {"greeted", "asked_about"}:
            return WorthinessDecision(
                False,
                target_layer="session_turn_ledger",
                status="session_only",
                importance=spec.importance,
                reason="session_event_not_long_term_memory",
            )

        # User có ý định lưu nhưng parser chưa bóc được fact sạch. Đưa vào
        # candidate để consolidator xử lý lúc rảnh, không ép thành semantic fact
        # trong hot path. Đây là vùng đệm chính thống, không phải lời hứa "đã nhớ".
        if claim.predicate == "remember_unresolved":
            if claim.confidence < self.min_candidate_confidence:
                return WorthinessDecision(False, reason="unresolved_memory_request_too_weak")
            return WorthinessDecision(
                True,
                target_layer="memory_candidate",
                status="candidate",
                importance=max(0.60, spec.importance, claim.confidence * 0.80),
                reason="explicit_memory_request_unresolved_candidate",
                warnings=("needs_consolidation", "not_stable_memory_yet"),
            )

        # mentioned_location là dấu vết ngữ cảnh, không trả lời quê/nơi ở/sở thích.
        if claim.predicate == "mentioned_location":
            if claim.confidence >= self.min_candidate_confidence:
                return WorthinessDecision(
                    True,
                    target_layer="memory_candidate",
                    status="candidate",
                    importance=0.25,
                    reason="mentioned_location_quarantined",
                    warnings=("not_answerable_as_living_or_hometown",),
                )
            return WorthinessDecision(False, reason="weak_mentioned_location")

        # Claim hiện tại có TTL thì vẫn lưu được, nhưng không nên thành fact ổn định.
        if claim.temporal == "current" and claim.stability == "transient":
            if claim.confidence < 0.60:
                return WorthinessDecision(False, reason="transient_claim_too_weak")
            return WorthinessDecision(
                True,
                target_layer="semantic_memory",
                status="tentative",
                importance=max(0.45, spec.importance),
                reason="short_lived_semantic_claim",
                warnings=("requires_freshness_on_recall",),
            )

        if claim.confidence < self.min_semantic_confidence:
            if claim.confidence >= self.min_candidate_confidence:
                return WorthinessDecision(
                    True,
                    target_layer="memory_candidate",
                    status="candidate",
                    importance=max(0.30, spec.importance * 0.70),
                    reason="semantic_claim_confidence_low_candidate",
                )
            return WorthinessDecision(False, reason="confidence_too_low")

        target = storage_layer_for_predicate(claim.predicate)
        if target == "session_turn_ledger":
            return WorthinessDecision(False, target_layer=target, status="session_only", reason="session_only_predicate")

        # Preference/fact/instruction/feedback ổn định mới có quyền vào long-term.
        if claim.modality in {"fact", "preference", "instruction", "feedback", "belief"}:
            return WorthinessDecision(
                True,
                target_layer=target,
                status="active" if claim.confidence >= 0.72 else "tentative",
                importance=max(importance_for_predicate(claim.predicate), claim.confidence * 0.70),
                reason="claim_is_memory_worthy",
                warnings=tuple(warnings),
            )

        return WorthinessDecision(False, reason="modality_not_memory_worthy")


__all__ = ["WorthinessDecision", "MemoryWorthinessPolicy"]
