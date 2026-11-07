from __future__ import annotations

"""
memory.predicate_ontology
=========================

Compatibility facade cho ontology cũ.

Nguồn chân lý không còn nằm trong file này. Mọi metadata được sinh từ
``predicate_catalog.py`` / ``PredicateRegistry``. Các hàm cũ vẫn giữ nguyên để
runtime hiện tại không phải đổi hàng loạt trong một lần triển khai.
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Iterable

from .predicate_catalog import BUILTIN_PREDICATES, PredicateDefinition
from .predicate_registry import get_default_registry

try:
    from .semantic_claim import SemanticClaim
except Exception:  # pragma: no cover
    from semantic_claim import SemanticClaim  # type: ignore


class EntailmentType(str, Enum):
    EXACT = "exact"
    COMPATIBLE = "compatible"
    RELATED = "related"
    CONTRADICTS = "contradicts"
    FORBIDDEN = "forbidden"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class PredicateSpec:
    """View phẳng cho code legacy; dữ liệu gốc là PredicateDefinition."""

    name: str
    object_type: str = "unknown"
    modality: str = "fact"
    default_temporal: str = "unknown"
    default_scope: str = "self"
    answer_targets: tuple[str, ...] = ()
    contradicts: tuple[str, ...] = ()
    compatible_with: tuple[str, ...] = ()
    forbidden_for: tuple[str, ...] = ()
    fact_key: str = ""
    storage_layer: str = "semantic_memory"
    default_decay: str = "normal"
    retention_class: str = "medium_term"
    importance: float = 0.50
    aliases: tuple[str, ...] = field(default_factory=tuple)


def _to_spec(item: PredicateDefinition) -> PredicateSpec:
    return PredicateSpec(
        name=item.predicate,
        object_type=item.ontology.object_type,
        modality=item.ontology.modality,
        default_temporal=item.ontology.temporal,
        default_scope=item.ontology.scope,
        answer_targets=(item.ontology.answer_target,) if item.ontology.answer_target else (),
        contradicts=item.ontology.contradicts,
        compatible_with=item.ontology.compatible_with,
        forbidden_for=item.ontology.forbidden_for,
        fact_key=item.fact_key,
        storage_layer=item.retention.storage_layer,
        default_decay=item.retention.default_decay,
        retention_class=item.retention.retention_class,
        importance=item.retention.importance,
        aliases=item.query.aliases,
    )


# Snapshot compatibility của builtin. Predicate runtime provisional vẫn được các
# hàm get_predicate()/predicate_for_fact_key() nhìn thấy qua registry.
PREDICATES: dict[str, PredicateSpec] = {item.predicate: _to_spec(item) for item in BUILTIN_PREDICATES}
FACT_KEY_TO_PREDICATE: dict[str, str] = {item.fact_key: item.predicate for item in BUILTIN_PREDICATES if item.fact_key}
FORBIDDEN_PREDICATE_TO_TARGET: dict[str, set[str]] = {
    item.predicate: set(item.ontology.forbidden_for) for item in BUILTIN_PREDICATES if item.ontology.forbidden_for
}


def get_predicate(name: str) -> PredicateSpec:
    item = get_default_registry().get(name)
    return _to_spec(item) if item is not None else PredicateSpec(name=str(name or "unknown"))


def fact_key_for_predicate(predicate: str) -> str:
    return get_predicate(predicate).fact_key


def predicate_for_fact_key(fact_key: str) -> str:
    item = get_default_registry().by_fact_key(fact_key)
    return item.predicate if item is not None else ""


def predicate_aliases(predicate: str) -> tuple[str, ...]:
    return get_predicate(predicate).aliases


def entailment_for_target(claim: SemanticClaim, target: str) -> EntailmentType:
    item = get_default_registry().get(claim.predicate)
    if item is None:
        return EntailmentType.UNKNOWN
    target = str(target or "")
    policy = item.ontology

    if target == policy.answer_target:
        if claim.assertion == "denied":
            return EntailmentType.CONTRADICTS
        if claim.assertion in {"hypothetical", "quoted"}:
            return EntailmentType.RELATED
        return EntailmentType.EXACT

    if target in policy.forbidden_for:
        return EntailmentType.FORBIDDEN
    if target in _compatible_targets(policy.compatible_with):
        return EntailmentType.COMPATIBLE
    if policy.object_type == "location" and ((target.startswith("user_") and "location" in target) or target == "user_hometown"):
        return EntailmentType.FORBIDDEN
    return EntailmentType.UNKNOWN


def contradicts(a: SemanticClaim, b: SemanticClaim) -> bool:
    if a.subject != b.subject:
        return False
    spec = get_predicate(a.predicate)
    if b.predicate not in spec.contradicts:
        return False
    if a.assertion == "denied" or b.assertion == "denied":
        return True
    return _norm(a.object_text) != _norm(b.object_text)


def is_long_term_predicate(predicate: str) -> bool:
    return get_predicate(predicate).retention_class in {"medium_term", "long_term"}


def storage_layer_for_predicate(predicate: str) -> str:
    return get_predicate(predicate).storage_layer


def default_decay_for_predicate(predicate: str) -> str:
    return get_predicate(predicate).default_decay


def importance_for_predicate(predicate: str) -> float:
    return float(get_predicate(predicate).importance)


def _compatible_targets(predicates: Iterable[str]) -> set[str]:
    return {
        target
        for predicate in predicates
        for target in get_predicate(predicate).answer_targets
        if target
    }


def _norm(text: str) -> str:
    import re
    import unicodedata

    value = unicodedata.normalize("NFD", str(text or "").lower()).replace("đ", "d")
    value = "".join(ch for ch in value if unicodedata.category(ch) != "Mn")
    return " ".join(re.sub(r"[^a-z0-9]+", " ", value).split())


__all__ = [
    "EntailmentType",
    "PredicateSpec",
    "PREDICATES",
    "get_predicate",
    "fact_key_for_predicate",
    "predicate_for_fact_key",
    "predicate_aliases",
    "entailment_for_target",
    "contradicts",
    "is_long_term_predicate",
    "storage_layer_for_predicate",
    "default_decay_for_predicate",
    "importance_for_predicate",
]
