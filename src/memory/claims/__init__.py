"""Tầng claim logic: build claim, ontology, entailment."""

from .claim_builder import ClaimBuilder
from ..claim_entailment import ClaimEntailmentDecision, ClaimEntailmentEngine, semantic_claim_from_memory_item
from ..predicate_ontology import EntailmentType, PredicateSpec

__all__ = [
    "ClaimBuilder",
    "ClaimEntailmentDecision",
    "ClaimEntailmentEngine",
    "semantic_claim_from_memory_item",
    "EntailmentType",
    "PredicateSpec",
]
