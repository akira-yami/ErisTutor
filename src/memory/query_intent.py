from __future__ import annotations

"""
memory.query_intent
===================

Semantic query intent được dựng trực tiếp từ PredicateRegistry.

``QUERY_TARGETS`` vẫn tồn tại như compatibility snapshot cho code cũ, nhưng compiler
không dùng một bảng target thủ công nữa. Predicate provisional có fact_key và
lexicon sẽ trở thành query target ngay trong cùng phiên chạy.
"""

from dataclasses import dataclass, field
from typing import Any, Callable, Mapping

from .predicate_catalog import BUILTIN_PREDICATES, PredicateDefinition
from .predicate_registry import PredicateRegistry, get_default_registry

try:
    from .understanding.entity_linker import normalize
except Exception:  # pragma: no cover
    from understanding.entity_linker import normalize  # type: ignore


@dataclass(frozen=True, slots=True)
class SemanticQueryIntent:
    raw_text: str
    subject: str = "user"
    answer_target: str = "unknown"
    target_predicates: tuple[str, ...] = ()
    expected_object_type: str = "unknown"
    modality: str = "fact"
    temporal: str = "unknown"
    strict_entailment: bool = True
    requires_freshness: bool = False
    confidence: float = 0.0
    evidence: tuple[str, ...] = field(default_factory=tuple)

    @property
    def is_known(self) -> bool:
        return bool(self.answer_target and self.answer_target != "unknown" and self.target_predicates)


@dataclass(frozen=True, slots=True)
class QueryTargetFrame:
    answer_target: str
    target_predicates: tuple[str, ...]
    expected_object_type: str
    modality: str = "fact"
    temporal: str = "unknown"
    requires_freshness: bool = False
    strict_entailment: bool = True
    fallback_cues: tuple[str, ...] = ()
    fallback_anti_cues: tuple[str, ...] = ()
    base_confidence: float = 0.70


def _frame_from_definition(item: PredicateDefinition) -> QueryTargetFrame:
    return QueryTargetFrame(
        answer_target=item.ontology.answer_target or item.fact_key or item.predicate,
        target_predicates=(item.predicate,),
        expected_object_type=item.ontology.object_type,
        modality=item.ontology.modality,
        temporal=item.ontology.temporal,
        requires_freshness=item.query.requires_freshness,
        strict_entailment=item.query.strict_entailment,
        fallback_cues=item.query_phrases(),
        fallback_anti_cues=item.query.anti_cues,
        base_confidence=item.query.base_confidence,
    )


QUERY_TARGETS: dict[str, QueryTargetFrame] = {
    item.ontology.answer_target: _frame_from_definition(item)
    for item in BUILTIN_PREDICATES
    if item.fact_key and item.ontology.answer_target
}


class QueryIntentParserProtocol:
    def parse(self, text: str) -> Mapping[str, Any] | None:  # pragma: no cover
        raise NotImplementedError


@dataclass(slots=True)
class SemanticQueryIntentCompiler:
    structured_parser: Callable[[str], Mapping[str, Any] | None] | None = None
    threshold: float = 0.64
    allow_fallback: bool = False
    registry: PredicateRegistry = field(default_factory=get_default_registry)

    def compile(self, text: str, *, structured: Mapping[str, Any] | None = None) -> SemanticQueryIntent:
        raw = str(text or "").strip()
        if not raw:
            return SemanticQueryIntent(raw_text=raw, confidence=0.0, evidence=("empty_query",))

        payload = structured
        if payload is None and self.structured_parser is not None:
            try:
                payload = self.structured_parser(raw)
            except Exception:
                payload = None
        if payload:
            intent = self._from_structured(raw, payload)
            if intent.is_known and intent.confidence >= self.threshold:
                return intent

        if not self.allow_fallback:
            return SemanticQueryIntent(raw_text=raw, confidence=0.0, evidence=("structured_query_required",))
        return self._fallback_compile(raw)

    def _from_structured(self, raw: str, data: Mapping[str, Any]) -> SemanticQueryIntent:
        target_name = str(data.get("answer_target") or data.get("target") or "").strip()
        predicates_raw = data.get("target_predicates") or data.get("predicates")
        predicates = tuple(str(x) for x in predicates_raw if str(x).strip()) if isinstance(predicates_raw, (list, tuple)) else ()

        item = self._definition_for_structured(target_name, predicates)
        if item is None:
            return SemanticQueryIntent(raw_text=raw, confidence=0.0, evidence=("structured_query_unknown_target",))
        frame = _frame_from_definition(item)
        confidence = _clamp01(data.get("confidence", data.get("certainty", frame.base_confidence)))
        return SemanticQueryIntent(
            raw_text=raw,
            subject=str(data.get("subject") or "user"),
            answer_target=frame.answer_target,
            target_predicates=predicates or frame.target_predicates,
            expected_object_type=str(data.get("expected_object_type") or frame.expected_object_type),
            modality=str(data.get("modality") or frame.modality),
            temporal=str(data.get("temporal") or frame.temporal),
            strict_entailment=bool(data.get("strict_entailment", frame.strict_entailment)),
            requires_freshness=bool(data.get("requires_freshness", frame.requires_freshness)),
            confidence=confidence,
            evidence=("structured_query_parser",),
        )

    def _definition_for_structured(self, target_name: str, predicates: tuple[str, ...]) -> PredicateDefinition | None:
        if target_name:
            direct = self.registry.by_fact_key(target_name) or self.registry.get(target_name)
            if direct is not None:
                return direct
            for item in self.registry.all():
                if item.ontology.answer_target == target_name:
                    return item
        for predicate in predicates:
            item = self.registry.get(predicate)
            if item is not None:
                return item
        return None

    def _fallback_compile(self, raw: str) -> SemanticQueryIntent:
        norm = normalize(raw)
        best: tuple[float, PredicateDefinition, tuple[str, ...]] | None = None
        for item in self.registry.all():
            if not item.fact_key:
                continue
            score, evidence = self._score(norm, item)
            if best is None or score > best[0]:
                best = (score, item, evidence)
        if best is None or best[0] < self.threshold:
            return SemanticQueryIntent(raw_text=raw, confidence=0.0, evidence=("query:unknown",))
        score, item, evidence = best
        frame = _frame_from_definition(item)
        return SemanticQueryIntent(
            raw_text=raw,
            answer_target=frame.answer_target,
            target_predicates=frame.target_predicates,
            expected_object_type=frame.expected_object_type,
            modality=frame.modality,
            temporal=frame.temporal,
            strict_entailment=frame.strict_entailment,
            requires_freshness=frame.requires_freshness,
            confidence=min(0.94, score),
            evidence=evidence,
        )

    def _score(self, norm: str, item: PredicateDefinition) -> tuple[float, tuple[str, ...]]:
        evidence: list[str] = []
        score = 0.0
        hay = f" {norm} "
        target = item.ontology.answer_target or item.fact_key
        for cue in item.query_phrases():
            clean = normalize(cue)
            if clean and (f" {clean} " in hay or clean in norm):
                score = max(score, item.query.base_confidence)
                evidence.append(f"fallback_query:{target}:{clean}")
        for anti in item.query.anti_cues:
            clean = normalize(anti)
            if clean and (f" {clean} " in hay or clean in norm):
                score -= 0.30
                evidence.append(f"fallback_query:{target}:anti:{clean}")
        return max(0.0, score), tuple(evidence)


def compile_semantic_query(
    text: str,
    *,
    structured: Mapping[str, Any] | None = None,
    allow_fallback: bool = False,
    registry: PredicateRegistry | None = None,
) -> SemanticQueryIntent:
    return SemanticQueryIntentCompiler(
        allow_fallback=allow_fallback,
        registry=registry or get_default_registry(),
    ).compile(text, structured=structured)


def normalize_vi(text: str) -> str:
    return normalize(text)


def _clamp01(value: Any) -> float:
    try:
        number = float(value)
    except Exception:
        number = 0.0
    if number != number or number in (float("inf"), float("-inf")):
        number = 0.0
    return max(0.0, min(1.0, number))


__all__ = [
    "SemanticQueryIntent",
    "SemanticQueryIntentCompiler",
    "QueryTargetFrame",
    "QUERY_TARGETS",
    "compile_semantic_query",
    "normalize_vi",
]
