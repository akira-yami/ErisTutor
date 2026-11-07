from __future__ import annotations

"""
memory.claims.claim_builder
===========================

Một pipeline duy nhất biến MeaningFrame thành SemanticClaim.

ClaimBuilder không còn giữ RELATION_TO_PREDICATE hay PREDICATE_DEFAULTS riêng.
Mọi route/default/retention lấy từ PredicateRegistry, tức catalog canonical.
Structured model và fallback parser đều đi qua cùng một đường build; khác nhau chỉ
ở metadata đầu vào, không còn hai bộ luật song song rồi lâu lâu cãi nhau như họp tổ.
"""

from dataclasses import dataclass, field
import time
from types import MappingProxyType
from typing import Any, Iterable, Mapping

try:
    from ..name_validation import is_valid_name_claim
    from ..predicate_catalog import BUILTIN_PREDICATES
    from ..predicate_registry import PredicateRegistry, canonicalize_predicate, get_default_registry
    from ..semantic_claim import ExtractedClaim, build_claim
    from ..understanding.meaning_frame import MeaningFrame, MeaningParseResult
except Exception:  # pragma: no cover
    from name_validation import is_valid_name_claim  # type: ignore
    from predicate_catalog import BUILTIN_PREDICATES  # type: ignore
    from predicate_registry import PredicateRegistry, canonicalize_predicate, get_default_registry  # type: ignore
    from semantic_claim import ExtractedClaim, build_claim  # type: ignore
    from understanding.meaning_frame import MeaningFrame, MeaningParseResult  # type: ignore


# Compatibility view được SINH từ catalog, không còn là nguồn chân lý.
RELATION_TO_PREDICATE = MappingProxyType({
    (route.frame_type, route.relation): item.predicate
    for item in BUILTIN_PREDICATES
    for route in item.frame_routes
    if not route.target_kind
})
PREDICATE_DEFAULTS = MappingProxyType({
    item.predicate: {
        "object_type": item.ontology.object_type,
        "modality": item.ontology.modality,
        "temporal": item.ontology.temporal,
        "stability": item.ontology.stability,
        "confidence_floor": item.retention.confidence_floor,
        "valid_seconds": item.retention.valid_seconds,
    }
    for item in BUILTIN_PREDICATES
})


@dataclass(slots=True)
class ClaimBuilder:
    """Builder deterministic, nhận registry qua dependency injection."""

    registry: PredicateRegistry = field(default_factory=get_default_registry)

    def build_many(
        self,
        result: MeaningParseResult,
        *,
        evidence_ids: Iterable[str] = (),
        source: str = "user_text",
    ) -> list[ExtractedClaim]:
        evidence_tuple = tuple(evidence_ids)
        out = [
            item
            for frame in result.frames
            if (item := self.build_one(frame, evidence_ids=evidence_tuple, source=source)) is not None
        ]
        return self._dedupe(out)

    def build_one(
        self,
        frame: MeaningFrame,
        *,
        evidence_ids: tuple[str, ...] = (),
        source: str = "user_text",
    ) -> ExtractedClaim | None:
        if frame.polarity in {"hypothetical", "quoted"}:
            return None

        raw = self._raw(frame)
        target_kind = frame.target.kind if frame.target is not None else str(raw.get("object_type") or "")
        fact_key = str(raw.get("fact_key") or "").strip()
        relation = str(raw.get("predicate") or frame.relation or fact_key).strip()
        aliases = self._string_tuple(raw.get("aliases"))
        query_examples = self._string_tuple(raw.get("query_examples"))

        definition = self.registry.resolve_frame(
            frame.frame_type,
            relation,
            target_kind=target_kind,
            allow_open=self._structured_enough(frame, raw),
            fact_key=fact_key,
            aliases=aliases,
            query_examples=query_examples,
            display_name=str(raw.get("display_name") or ""),
            object_type=str(raw.get("object_type") or target_kind or "unknown"),
            modality=str(raw.get("modality") or frame.modality or "fact"),
            temporal=str(raw.get("time_scope") or raw.get("temporal") or frame.time_scope or "unknown"),
            cardinality=str(raw.get("cardinality") or "multi"),
            sensitivity=str(raw.get("sensitivity") or "normal"),
            confidence=float(raw.get("confidence") or frame.certainty or 0.0),
        )
        if definition is None:
            return None

        object_value, object_text = self._object_payload(frame, raw)
        if object_value in (None, "") and definition.predicate not in {"greeted", "asked_about"}:
            return None

        evidence_text = str(
            raw.get("_source_evidence")
            or raw.get("evidence_text")
            or raw.get("evidence_span")
            or frame.evidence_span
            or object_text
        ).strip()

        if definition.fact_key in {"user.name", "user.nickname"}:
            if not is_valid_name_claim(
                object_value,
                evidence_text,
                nickname=(definition.fact_key == "user.nickname"),
            ):
                return None

        ontology = definition.ontology
        retention = definition.retention
        temporal = self._prefer_specific(
            str(raw.get("time_scope") or raw.get("temporal") or frame.time_scope or "unknown"),
            ontology.temporal,
            generic="unknown",
        )
        stability = self._prefer_specific(
            str(raw.get("stability") or frame.stability or "unknown"),
            ontology.stability,
            generic="unknown",
        )
        modality = self._prefer_specific(
            str(raw.get("modality") or frame.modality or ""),
            ontology.modality,
            generic="",
        )
        confidence = self._confidence(frame, raw, retention.confidence_floor)
        valid_seconds = raw.get("valid_seconds", retention.valid_seconds)
        valid_until = time.time() + float(valid_seconds) if valid_seconds not in (None, "", 0, 0.0) else None
        commit_hint = self._commit_hint(str(raw.get("storage_target") or retention.storage_layer))

        claim = build_claim(
            subject=str(raw.get("subject") or frame.subject or "user"),
            predicate=definition.predicate,
            object_text=str(object_text),
            object_value=object_value,
            object_type=str(raw.get("object_type") or ontology.object_type or target_kind or "unknown"),
            assertion=str(raw.get("assertion") or frame.polarity or "asserted"),  # type: ignore[arg-type]
            modality=modality,  # type: ignore[arg-type]
            temporal=temporal,  # type: ignore[arg-type]
            scope=str(raw.get("scope") or frame.scope or ontology.scope or "self"),  # type: ignore[arg-type]
            source="user_text" if source == "user_text" else "inference",  # type: ignore[arg-type]
            confidence=confidence,
            evidence_text=evidence_text,
            evidence_ids=evidence_ids,
            valid_until=valid_until,
            stability=stability,  # type: ignore[arg-type]
            qualifiers=self._qualifiers(frame, raw, definition, commit_hint),
        )
        return ExtractedClaim(
            claim=claim,
            extraction_confidence=confidence,
            extraction_method="canonical_claim_builder",
            commit_hint=commit_hint,
            warnings=frame.warnings,
        )

    def _raw(self, frame: MeaningFrame) -> dict[str, Any]:
        value = (frame.slots or {}).get("_raw") or {}
        return dict(value) if isinstance(value, Mapping) else {}

    def _structured_enough(self, frame: MeaningFrame, raw: Mapping[str, Any]) -> bool:
        # Model có schema/fact_key hoặc parser structured đủ confidence mới được
        # tạo predicate provisional. Fallback regex mơ hồ chỉ dùng builtin routes.
        return bool(
            raw.get("fact_key")
            or raw.get("memory_type")
            or raw.get("predicate")
            or (frame.source != "user_text" and float(frame.certainty) >= 0.55)
            or (raw and float(raw.get("confidence") or frame.certainty) >= 0.55)
        )

    def _object_payload(self, frame: MeaningFrame, raw: Mapping[str, Any]) -> tuple[Any, str]:
        value = raw.get("value")
        if value in (None, "") and frame.target is not None:
            value = frame.target.text
        if value in (None, ""):
            value = raw.get("object") or raw.get("object_value") or raw.get("claim")
        text = str(
            raw.get("object_text")
            or (frame.target.text if frame.target is not None else "")
            or raw.get("claim")
            or value
            or frame.evidence_span
        ).strip()
        return value if value not in (None, "") else text, text

    def _confidence(self, frame: MeaningFrame, raw: Mapping[str, Any], floor: float) -> float:
        confidence = max(float(raw.get("confidence") or frame.certainty or 0.0), float(floor or 0.0))
        if frame.target is not None:
            confidence = confidence * 0.82 + float(frame.target.confidence) * 0.18
        return max(0.0, min(1.0, confidence))

    def _commit_hint(self, storage: str) -> str:
        value = str(storage or "semantic").strip().lower()
        mapping = {
            "semantic": "semantic",
            "semantic_memory": "semantic",
            "episodic": "episodic",
            "episodic_memory": "episodic",
            "procedural": "procedural",
            "procedural_memory": "procedural",
            "affective_residue": "affective_residue",
            "relational": "relational",
            "candidate": "candidate",
            "memory_candidate": "candidate",
            "session_turn_ledger": "episodic",
        }
        return mapping.get(value, "semantic")

    def _qualifiers(
        self,
        frame: MeaningFrame,
        raw: Mapping[str, Any],
        definition: Any,
        commit_hint: str,
    ) -> dict[str, Any]:
        decay = str(raw.get("decay") or definition.retention.default_decay or "normal")
        decay_policy = {
            "slow": "slow",
            "normal": "normal",
            "fast": "fast",
            "ephemeral": "session_only",
        }.get(decay, decay)
        surface_fn = str(raw.get("surface_fn") or "background")
        surface_policy = {
            "can_answer": "surface_if_contextual",
            "background": "silent_bias",
            "never": "never_direct_claim",
        }.get(surface_fn, str(raw.get("surface_policy") or "silent_bias"))
        return {
            "meaning_frame": frame.to_dict(),
            "pipeline": "canonical_claim_builder.v2",
            "schema_version": definition.schema_version,
            "predicate_source": definition.source,
            "provisional_predicate": definition.provisional,
            "fact_key": definition.fact_key,
            "aliases": list(definition.query.aliases),
            "query_examples": list(definition.query.query_examples),
            "cardinality": definition.ontology.cardinality,
            "display_name": definition.surface.display_name,
            "sensitivity": definition.surface.sensitivity,
            "storage_target": str(raw.get("storage_target") or definition.retention.storage_layer),
            "commit_hint": commit_hint,
            "importance": float(raw.get("importance") or definition.retention.importance),
            "decay": decay,
            "decay_policy": decay_policy,
            "surface_fn": surface_fn,
            "surface_policy": surface_policy,
            "memory_type": str(raw.get("memory_type") or definition.ontology.modality),
            "why": str(raw.get("why") or ""),
        }

    def _dedupe(self, items: list[ExtractedClaim]) -> list[ExtractedClaim]:
        seen: set[tuple[str, str, str, str]] = set()
        out: list[ExtractedClaim] = []
        for item in items:
            claim = item.claim
            key = (claim.subject, claim.predicate, claim.object_text.casefold(), claim.assertion)
            if key not in seen:
                seen.add(key)
                out.append(item)
        return out

    @staticmethod
    def _string_tuple(value: Any) -> tuple[str, ...]:
        return tuple(str(item) for item in value or () if str(item).strip()) if isinstance(value, (list, tuple, set)) else ()

    @staticmethod
    def _prefer_specific(value: str, default: str, *, generic: str) -> str:
        return value if value and value != generic else default


__all__ = ["ClaimBuilder", "RELATION_TO_PREDICATE", "PREDICATE_DEFAULTS"]
