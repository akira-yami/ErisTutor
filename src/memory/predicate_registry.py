from __future__ import annotations

"""
memory.predicate_registry
=========================

Registry runtime cho catalog predicate mở.

Catalog builtin nằm duy nhất trong ``predicate_catalog.py``. Registry chỉ làm các
việc runtime: hợp nhất schema provisional, resolve query/frame, persistence và
hydrate. Các module khác nhận registry qua dependency injection; proxy global ở
cuối file chỉ giữ tương thích cho code cũ, không còn khởi tạo singleton khi import.
"""

from contextlib import contextmanager
from dataclasses import replace
import json
import math
import os
import re
import threading
import unicodedata
from typing import Any, Iterable, Iterator, Mapping, Sequence

from .predicate_catalog import (
    BUILTIN_PREDICATES,
    FrameRoute,
    OntologyPolicy,
    PredicateDefinition,
    QueryLexicon,
    RetentionPolicy,
    SurfacePolicy,
)

# Tên cũ được giữ để các import bên ngoài không vỡ. Kiểu dữ liệu thật đã được
# tách policy trong PredicateDefinition.
RegisteredPredicate = PredicateDefinition


class PredicateConflictError(ValueError):
    """Schema mới đụng fact_key/predicate của schema khác mà không có canonical merge."""


class PredicateResolution:
    __slots__ = ("predicate", "fact_key", "score", "source", "matched_phrase")

    def __init__(self, predicate: str, fact_key: str, score: float, source: str, matched_phrase: str = "") -> None:
        self.predicate = predicate
        self.fact_key = fact_key
        self.score = score
        self.source = source
        self.matched_phrase = matched_phrase

    def __repr__(self) -> str:  # pragma: no cover - chỉ phục vụ log/debug
        return (
            f"PredicateResolution(predicate={self.predicate!r}, fact_key={self.fact_key!r}, "
            f"score={self.score:.3f}, source={self.source!r})"
        )


class PredicateRegistry:
    """Registry thread-safe, có governance cho predicate provisional."""

    def __init__(
        self,
        *,
        persistence_path: str | None = None,
        builtins: Iterable[PredicateDefinition] | None = None,
    ) -> None:
        self._lock = threading.RLock()
        self._items: dict[str, PredicateDefinition] = {}
        self._fact_to_predicate: dict[str, str] = {}
        self._route_index: dict[tuple[str, str], list[tuple[int, str, str]]] = {}
        self._alias_owners: dict[str, set[str]] = {}
        self.persistence_path = str(
            persistence_path if persistence_path is not None else os.getenv("ERIS_PREDICATE_REGISTRY_PATH", "")
        ).strip()
        for item in (BUILTIN_PREDICATES if builtins is None else tuple(builtins)):
            self.register(item, persist=False)
        self._load_persisted()

    # ------------------------------------------------------------------
    # Đăng ký và governance
    # ------------------------------------------------------------------

    def register(self, item: PredicateDefinition, *, persist: bool = False) -> PredicateDefinition:
        normalized = self._normalize(item)
        if not normalized.predicate:
            raise ValueError("predicate rỗng")

        with self._lock:
            old = self._items.get(normalized.predicate)
            if old is not None:
                normalized = self._merge(old, normalized)

            fact_owner = self._fact_to_predicate.get(normalized.fact_key) if normalized.fact_key else None
            if fact_owner and fact_owner != normalized.predicate:
                # Hai predicate cùng fact_key là một schema collision, không phải
                # “đa dạng ngôn ngữ”. Alias phải merge vào predicate canonical.
                owner = self._items[fact_owner]
                if normalized.provisional:
                    normalized = self._merge(owner, replace(normalized, predicate=owner.predicate))
                else:
                    raise PredicateConflictError(
                        f"fact_key {normalized.fact_key!r} đã thuộc predicate {fact_owner!r}"
                    )

            self._items[normalized.predicate] = normalized
            self._rebuild_indexes_locked()

        if persist:
            self.save()
        return normalized

    def register_provisional(
        self,
        *,
        predicate: str,
        fact_key: str = "",
        aliases: Iterable[str] = (),
        query_examples: Iterable[str] = (),
        object_type: str = "unknown",
        modality: str = "fact",
        temporal: str = "unknown",
        cardinality: str = "multi",
        display_name: str = "",
        surface_template: str = "",
        missing_template: str = "",
        sensitivity: str = "normal",
        source: str = "semantic_model",
        persist: bool | None = None,
        frame_routes: Iterable[FrameRoute | Mapping[str, Any]] = (),
        answer_target: str = "",
        stability: str = "unknown",
        storage_layer: str = "semantic_memory",
        default_decay: str = "normal",
        retention_class: str = "medium_term",
        importance: float = 0.50,
        confidence_floor: float = 0.0,
        valid_seconds: float | None = None,
        anti_cues: Iterable[str] = (),
        disclosure_cues: Iterable[str] = (),
        requires_freshness: bool = False,
        strict_entailment: bool = True,
    ) -> PredicateDefinition:
        clean_fact = canonicalize_fact_key(fact_key)
        clean_predicate = canonicalize_predicate(predicate or clean_fact)
        routes: list[FrameRoute] = []
        for route in frame_routes:
            if isinstance(route, FrameRoute):
                routes.append(route)
            elif isinstance(route, Mapping):
                routes.append(FrameRoute(**dict(route)))
        item = PredicateDefinition(
            predicate=clean_predicate,
            fact_key=clean_fact,
            frame_routes=tuple(routes),
            ontology=OntologyPolicy(
                object_type=str(object_type or "unknown"),
                modality=str(modality or "fact"),
                temporal=str(temporal or "unknown"),
                cardinality="single" if str(cardinality) == "single" else "multi",
                stability=str(stability or "unknown"),
                answer_target=str(answer_target or clean_fact or clean_predicate),
            ),
            query=QueryLexicon(
                aliases=_unique(aliases),
                query_examples=_unique(query_examples),
                anti_cues=_unique(anti_cues),
                disclosure_cues=_unique(disclosure_cues or aliases),
                strict_entailment=bool(strict_entailment),
                requires_freshness=bool(requires_freshness),
            ),
            retention=RetentionPolicy(
                storage_layer=str(storage_layer or "semantic_memory"),
                default_decay=str(default_decay or "normal"),
                retention_class=str(retention_class or "medium_term"),
                importance=_clamp01(importance),
                confidence_floor=_clamp01(confidence_floor),
                valid_seconds=float(valid_seconds) if valid_seconds not in (None, "") else None,
            ),
            surface=SurfacePolicy(
                display_name=str(display_name or humanize_fact_key(clean_fact) or humanize_predicate(clean_predicate)),
                found_template=str(surface_template or ""),
                missing_template=str(missing_template or ""),
                sensitivity=str(sensitivity or "normal"),
            ),
            provisional=True,
            source=str(source or "semantic_model"),
            schema_version=2,
        )
        should_persist = bool(self.persistence_path) if persist is None else bool(persist)
        return self.register(item, persist=should_persist)

    def promote(self, predicate: str, *, persist: bool = True) -> PredicateDefinition | None:
        """Đưa predicate provisional thành schema đã duyệt mà không đổi identity."""

        with self._lock:
            old = self._items.get(canonicalize_predicate(predicate))
            if old is None:
                return None
            promoted = replace(old, provisional=False, source="runtime_promoted", schema_version=max(2, old.schema_version))
            self._items[promoted.predicate] = promoted
            self._rebuild_indexes_locked()
        if persist:
            self.save(include_promoted=True)
        return promoted

    # ------------------------------------------------------------------
    # Lookup / resolve
    # ------------------------------------------------------------------

    def get(self, predicate: str) -> PredicateDefinition | None:
        with self._lock:
            return self._items.get(canonicalize_predicate(predicate))

    def by_fact_key(self, fact_key: str) -> PredicateDefinition | None:
        key = canonicalize_fact_key(fact_key)
        with self._lock:
            predicate = self._fact_to_predicate.get(key)
            return self._items.get(predicate) if predicate else None

    def all(self) -> tuple[PredicateDefinition, ...]:
        with self._lock:
            return tuple(self._items.values())

    def resolve_frame(
        self,
        frame_type: str,
        relation: str,
        *,
        target_kind: str = "",
        allow_open: bool = False,
        fact_key: str = "",
        aliases: Iterable[str] = (),
        query_examples: Iterable[str] = (),
        display_name: str = "",
        object_type: str = "unknown",
        modality: str = "fact",
        temporal: str = "unknown",
        cardinality: str = "multi",
        sensitivity: str = "normal",
        confidence: float = 0.0,
    ) -> PredicateDefinition | None:
        """Resolve MeaningFrame bằng route từ catalog.

        Structured frame đủ confidence có thể tạo schema provisional. Fallback
        lexical mơ hồ không được tự sinh ontology.
        """

        raw_relation = str(relation or "").strip()
        direct = self.get(raw_relation) or self.by_fact_key(raw_relation)
        if direct is not None:
            return direct

        key = (str(frame_type or ""), raw_relation)
        candidates: list[tuple[int, PredicateDefinition]] = []
        with self._lock:
            for priority, route_kind, predicate in self._route_index.get(key, ()):  # exact frame/relation
                if route_kind and route_kind != str(target_kind or ""):
                    continue
                item = self._items.get(predicate)
                if item is not None:
                    candidates.append((priority, item))
        if candidates:
            candidates.sort(key=lambda row: row[0], reverse=True)
            return candidates[0][1]

        if not allow_open or confidence < 0.55 or not raw_relation:
            return None
        clean_fact = canonicalize_fact_key(fact_key)
        clean_predicate = canonicalize_predicate(raw_relation)
        if not clean_fact and str(frame_type or "") not in {"session_event", "unknown"}:
            clean_fact = f"user.{clean_predicate.removesuffix('_is')}"
        return self.register_provisional(
            predicate=clean_predicate,
            fact_key=clean_fact,
            aliases=aliases,
            query_examples=query_examples,
            display_name=display_name,
            object_type=object_type or target_kind or "unknown",
            modality=modality,
            temporal=temporal,
            cardinality=cardinality,
            sensitivity=sensitivity,
            frame_routes=(FrameRoute(str(frame_type or "unknown"), raw_relation, str(target_kind or "")),),
            source="structured_semantic_frame",
        )

    def fact_key_prototypes(self) -> dict[str, tuple[str, ...]]:
        return {item.fact_key: item.query_phrases() for item in self.all() if item.fact_key}

    def resolve_fact_reference(
        self,
        text: str,
        *,
        min_lexical_score: float = 0.46,
    ) -> PredicateResolution | None:
        """Resolve một đoạn text thành fact_key mà không bắt buộc là câu hỏi.

        Dùng cho migration/legacy recall cue. Commit path mới phải truyền fact_key
        structured, không dựa vào hàm này để tự phong sự thật.
        """

        norm = normalize_vi(text)
        if not norm:
            return None
        lexical = self._rank_lexical(norm)
        if not lexical or lexical[0][0] < min_lexical_score:
            return None
        score, item, phrase = lexical[0]
        margin = score - (lexical[1][0] if len(lexical) > 1 else 0.0)
        if score < 0.82 and margin < 0.10:
            return None
        return PredicateResolution(item.predicate, item.fact_key, _clamp01(score), "registry_reference", phrase)

    def resolve_fact_query(
        self,
        text: str,
        *,
        embedding_provider: Any | None = None,
        min_lexical_score: float = 0.46,
        min_cosine: float | None = None,
    ) -> PredicateResolution | None:
        raw = str(text or "").strip()
        norm = normalize_vi(raw)
        if not norm or not looks_like_fact_question(raw, norm):
            return None

        lexical = self._rank_lexical(norm)
        if lexical and lexical[0][0] >= min_lexical_score:
            score, item, phrase = lexical[0]
            margin = score - (lexical[1][0] if len(lexical) > 1 else 0.0)
            if score >= 0.82 or margin >= 0.16 or embedding_provider is None:
                return PredicateResolution(item.predicate, item.fact_key, _clamp01(score), "registry_lexical", phrase)

        if embedding_provider is not None:
            threshold = float(min_cosine if min_cosine is not None else os.getenv("ERIS_MEMORY_QUERY_MIN_COSINE", "0.38"))
            vector_result = self._resolve_with_embedding(raw, embedding_provider, threshold)
            if vector_result is not None:
                if lexical and lexical[0][1].predicate == vector_result.predicate:
                    return PredicateResolution(
                        vector_result.predicate,
                        vector_result.fact_key,
                        _clamp01(max(vector_result.score, lexical[0][0] * 0.92)),
                        "registry_hybrid",
                        lexical[0][2] or vector_result.matched_phrase,
                    )
                return vector_result

        if lexical and lexical[0][0] >= min_lexical_score:
            score, item, phrase = lexical[0]
            return PredicateResolution(item.predicate, item.fact_key, _clamp01(score), "registry_lexical_ambiguous", phrase)
        return None

    def resolve_disclosure(self, text: str) -> PredicateResolution | None:
        """Nhận diện self-disclosure bằng grammar chung + lexicon của registry.

        Không có danh sách ``if user.gender/user.name/...``. Predicate provisional
        có disclosure cue cũng tự tham gia ngay.
        """

        raw = str(text or "").strip()
        norm = normalize_vi(raw)
        if not norm or looks_like_fact_question(raw, norm):
            return None
        padded = f" {norm} "
        if not any(marker in padded for marker in (" toi ", " tui ", " minh ", " tao ", " em ", " cua toi ", " cua tui ", " cua minh ")):
            return None
        # Một câu tự khai thường có copula/trạng thái/sở thích. Đây là grammar,
        # không phải ontology của từng fact.
        declaration_markers = (
            " la ", " ten ", " goi ", " sinh ", " tuoi ", " dang ", " hien ",
            " song ", " que ", " thich ", " ghet ", " muon ", " hoc ", " lam ",
        )
        if not any(marker in padded for marker in declaration_markers):
            return None

        ranked: list[tuple[float, PredicateDefinition, str]] = []
        for item in self.all():
            if not item.fact_key:
                continue
            phrases = _unique((*item.query.disclosure_cues, *item.aliases))
            best_score = 0.0
            best_phrase = ""
            for phrase in phrases:
                score = _lexical_phrase_score(norm, phrase)
                if score > best_score:
                    best_score, best_phrase = score, phrase
            if best_score:
                ranked.append((best_score, item, best_phrase))
        ranked.sort(key=lambda row: row[0], reverse=True)
        if not ranked or ranked[0][0] < 0.48:
            return None
        score, item, phrase = ranked[0]
        return PredicateResolution(item.predicate, item.fact_key, min(0.97, max(0.72, score)), "registry_disclosure", phrase)

    # ------------------------------------------------------------------
    # Surface / store helpers
    # ------------------------------------------------------------------

    def hydrate_from_memory_items(self, items: Iterable[Any]) -> int:
        count = 0
        for memory_item in items:
            fact_key = canonicalize_fact_key(getattr(memory_item, "fact_key", ""))
            if not fact_key or self.by_fact_key(fact_key) is not None:
                continue
            metadata = getattr(memory_item, "metadata", {})
            metadata = dict(metadata) if isinstance(metadata, Mapping) else {}
            semantic_claim = metadata.get("semantic_claim")
            semantic_claim = dict(semantic_claim) if isinstance(semantic_claim, Mapping) else {}
            predicate = str(metadata.get("predicate") or semantic_claim.get("predicate") or f"fact_{canonicalize_predicate(fact_key)}")
            aliases = [str(x) for x in getattr(memory_item, "aliases", ()) or () if str(x).strip()]
            aliases.extend(str(x) for x in metadata.get("aliases", ()) or () if str(x).strip())
            self.register_provisional(
                predicate=predicate,
                fact_key=fact_key,
                aliases=aliases,
                query_examples=metadata.get("query_examples", ()) or aliases,
                object_type=str(metadata.get("object_type") or semantic_claim.get("object_type") or "unknown"),
                modality=str(metadata.get("modality") or semantic_claim.get("modality") or "fact"),
                temporal=str(metadata.get("temporal") or semantic_claim.get("temporal") or "unknown"),
                cardinality=str(metadata.get("cardinality") or "multi"),
                display_name=str(metadata.get("display_name") or humanize_fact_key(fact_key)),
                sensitivity=str(metadata.get("sensitivity") or "normal"),
                source="semantic_memory_hydration",
                persist=False,
            )
            count += 1
        return count

    def render_fact(self, fact_key: str, value: Any) -> str:
        item = self.by_fact_key(fact_key)
        clean_value = str(value).strip()
        if item and item.surface.found_template:
            return item.surface.found_template.format(value=clean_value)
        label = item.surface.display_name if item else humanize_fact_key(fact_key)
        return f"Bạn từng nói {label} của mình là {clean_value}." if label else f"Bạn từng nói thông tin đó là {clean_value}."

    def missing_text(self, fact_key: str) -> str:
        item = self.by_fact_key(fact_key)
        if item and item.surface.missing_template:
            return item.surface.missing_template
        label = item.surface.display_name if item else humanize_fact_key(fact_key)
        return f"Tôi chưa nhớ bạn từng nói rõ {label} của mình." if label and label != "thông tin" else "Tôi chưa nhớ bạn từng nói rõ thông tin đó."

    def is_single_value_fact(self, fact_key: str) -> bool:
        item = self.by_fact_key(fact_key)
        return bool(item and item.ontology.cardinality == "single")

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def save(self, *, include_promoted: bool = False) -> None:
        if not self.persistence_path:
            return
        with self._lock:
            dynamic = [
                item.to_dict()
                for item in self._items.values()
                if item.provisional or (include_promoted and item.source == "runtime_promoted")
            ]
        directory = os.path.dirname(os.path.abspath(self.persistence_path))
        os.makedirs(directory, exist_ok=True)
        temp = self.persistence_path + ".tmp"
        with open(temp, "w", encoding="utf-8") as handle:
            json.dump({"version": 2, "predicates": dynamic}, handle, ensure_ascii=False, indent=2)
        os.replace(temp, self.persistence_path)

    def _load_persisted(self) -> None:
        if not self.persistence_path or not os.path.isfile(self.persistence_path):
            return
        try:
            with open(self.persistence_path, "r", encoding="utf-8") as handle:
                data = json.load(handle)
            rows = data.get("predicates", []) if isinstance(data, Mapping) else []
            for row in rows:
                if isinstance(row, Mapping):
                    self.register(PredicateDefinition.from_dict(row), persist=False)
        except Exception:
            # Registry hỏng không được làm runtime chết; store còn evidence để hydrate.
            return

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _rank_lexical(self, norm: str) -> list[tuple[float, PredicateDefinition, str]]:
        rows: list[tuple[float, PredicateDefinition, str]] = []
        for item in self.all():
            if not item.fact_key:
                continue
            best_score, best_phrase = 0.0, ""
            for phrase in item.query_phrases():
                score = _lexical_phrase_score(norm, phrase)
                if score > best_score:
                    best_score, best_phrase = score, phrase
            rows.append((best_score, item, best_phrase))
        rows.sort(key=lambda row: row[0], reverse=True)
        return rows

    def _resolve_with_embedding(self, text: str, provider: Any, threshold: float) -> PredicateResolution | None:
        try:
            query_vec = _embed_query(provider, text)
        except Exception:
            return None
        if not _has_vector(query_vec):
            return None
        best: tuple[float, PredicateDefinition] | None = None
        for item in self.all():
            if not item.fact_key:
                continue
            document = (
                f"Thuộc tính ký ức: {item.surface.display_name}. "
                f"Fact key: {item.fact_key}. Cách hỏi: {'; '.join(item.query_phrases())}."
            )
            try:
                score = raw_cosine(query_vec, _embed_document(provider, document))
            except Exception:
                continue
            if best is None or score > best[0]:
                best = (score, item)
        if best is None or best[0] < threshold:
            return None
        score, item = best
        return PredicateResolution(item.predicate, item.fact_key, _clamp01(score), "registry_embedding", item.surface.display_name)

    def _normalize(self, item: PredicateDefinition) -> PredicateDefinition:
        predicate = canonicalize_predicate(item.predicate)
        fact_key = canonicalize_fact_key(item.fact_key)
        ontology = replace(
            item.ontology,
            object_type=str(item.ontology.object_type or "unknown"),
            modality=str(item.ontology.modality or "fact"),
            temporal=str(item.ontology.temporal or "unknown"),
            scope=str(item.ontology.scope or "self"),
            cardinality="single" if item.ontology.cardinality == "single" else "multi",
            answer_target=str(item.ontology.answer_target or fact_key or predicate),
            contradicts=_unique(item.ontology.contradicts),
            compatible_with=_unique(item.ontology.compatible_with),
            forbidden_for=_unique(item.ontology.forbidden_for),
        )
        query = replace(
            item.query,
            aliases=_unique(item.query.aliases),
            query_examples=_unique(item.query.query_examples),
            anti_cues=_unique(item.query.anti_cues),
            disclosure_cues=_unique(item.query.disclosure_cues),
            base_confidence=_clamp01(item.query.base_confidence),
        )
        retention = replace(
            item.retention,
            importance=_clamp01(item.retention.importance),
            confidence_floor=_clamp01(item.retention.confidence_floor),
        )
        surface = replace(
            item.surface,
            display_name=str(item.surface.display_name or humanize_fact_key(fact_key) or humanize_predicate(predicate)),
        )
        routes = tuple(
            FrameRoute(str(r.frame_type or ""), str(r.relation or ""), str(r.target_kind or ""), int(r.priority))
            for r in item.frame_routes
            if str(r.relation or "").strip()
        )
        return replace(
            item,
            predicate=predicate,
            fact_key=fact_key,
            frame_routes=routes,
            ontology=ontology,
            query=query,
            retention=retention,
            surface=surface,
            source=str(item.source or "runtime"),
            schema_version=max(1, int(item.schema_version or 1)),
        )

    def _merge(self, old: PredicateDefinition, new: PredicateDefinition) -> PredicateDefinition:
        # Builtin đã duyệt không bị provisional rỗng ghi đè. Metadata mới chỉ
        # bổ sung alias/route và lấp các trường generic.
        ontology = OntologyPolicy(
            object_type=_prefer(new.ontology.object_type, old.ontology.object_type, generic="unknown"),
            modality=_prefer(new.ontology.modality, old.ontology.modality, generic=""),
            temporal=_prefer(new.ontology.temporal, old.ontology.temporal, generic="unknown"),
            scope=_prefer(new.ontology.scope, old.ontology.scope, generic=""),
            cardinality=old.ontology.cardinality if old.ontology.cardinality == "single" and new.provisional else new.ontology.cardinality,
            stability=_prefer(new.ontology.stability, old.ontology.stability, generic="unknown"),
            answer_target=_prefer(new.ontology.answer_target, old.ontology.answer_target, generic=""),
            contradicts=_unique((*old.ontology.contradicts, *new.ontology.contradicts)),
            compatible_with=_unique((*old.ontology.compatible_with, *new.ontology.compatible_with)),
            forbidden_for=_unique((*old.ontology.forbidden_for, *new.ontology.forbidden_for)),
        )
        query = QueryLexicon(
            aliases=_unique((*old.query.aliases, *new.query.aliases)),
            query_examples=_unique((*old.query.query_examples, *new.query.query_examples)),
            anti_cues=_unique((*old.query.anti_cues, *new.query.anti_cues)),
            disclosure_cues=_unique((*old.query.disclosure_cues, *new.query.disclosure_cues)),
            base_confidence=max(old.query.base_confidence, new.query.base_confidence),
            strict_entailment=old.query.strict_entailment if old.query.strict_entailment is False else new.query.strict_entailment,
            requires_freshness=old.query.requires_freshness or new.query.requires_freshness,
        )
        retention = RetentionPolicy(
            storage_layer=_prefer(new.retention.storage_layer, old.retention.storage_layer, generic=""),
            default_decay=_prefer(new.retention.default_decay, old.retention.default_decay, generic=""),
            retention_class=_prefer(new.retention.retention_class, old.retention.retention_class, generic=""),
            importance=max(old.retention.importance, new.retention.importance),
            confidence_floor=max(old.retention.confidence_floor, new.retention.confidence_floor),
            valid_seconds=new.retention.valid_seconds if new.retention.valid_seconds is not None else old.retention.valid_seconds,
        )
        surface = SurfacePolicy(
            display_name=_prefer(new.surface.display_name, old.surface.display_name, generic="thông tin"),
            found_template=new.surface.found_template or old.surface.found_template,
            missing_template=new.surface.missing_template or old.surface.missing_template,
            sensitivity=_prefer(new.surface.sensitivity, old.surface.sensitivity, generic="normal"),
        )
        return PredicateDefinition(
            predicate=old.predicate,
            fact_key=new.fact_key or old.fact_key,
            frame_routes=_unique_routes((*old.frame_routes, *new.frame_routes)),
            ontology=ontology,
            query=query,
            retention=retention,
            surface=surface,
            provisional=old.provisional and new.provisional,
            source=new.source or old.source,
            schema_version=max(old.schema_version, new.schema_version),
        )

    def _rebuild_indexes_locked(self) -> None:
        self._fact_to_predicate = {}
        self._route_index = {}
        self._alias_owners = {}
        for predicate, item in self._items.items():
            if item.fact_key:
                self._fact_to_predicate[item.fact_key] = predicate
            for route in item.frame_routes:
                self._route_index.setdefault((route.frame_type, route.relation), []).append(
                    (route.priority, route.target_kind, predicate)
                )
            for phrase in item.query_phrases():
                self._alias_owners.setdefault(normalize_vi(phrase), set()).add(predicate)


# ---------------------------------------------------------------------------
# Lazy default service và compatibility proxy
# ---------------------------------------------------------------------------

_DEFAULT_LOCK = threading.RLock()
_DEFAULT_REGISTRY: PredicateRegistry | None = None


def get_default_registry() -> PredicateRegistry:
    global _DEFAULT_REGISTRY
    if _DEFAULT_REGISTRY is None:
        with _DEFAULT_LOCK:
            if _DEFAULT_REGISTRY is None:
                _DEFAULT_REGISTRY = PredicateRegistry()
    return _DEFAULT_REGISTRY


def set_default_registry(registry: PredicateRegistry | None) -> None:
    global _DEFAULT_REGISTRY
    with _DEFAULT_LOCK:
        _DEFAULT_REGISTRY = registry


@contextmanager
def using_default_registry(registry: PredicateRegistry) -> Iterator[PredicateRegistry]:
    previous = get_default_registry()
    set_default_registry(registry)
    try:
        yield registry
    finally:
        set_default_registry(previous)


class _DefaultRegistryProxy:
    """Compatibility cho ``GLOBAL_PREDICATE_REGISTRY`` mà không eager singleton."""

    def __getattr__(self, name: str) -> Any:
        return getattr(get_default_registry(), name)

    def __repr__(self) -> str:  # pragma: no cover
        return repr(get_default_registry())


GLOBAL_PREDICATE_REGISTRY = _DefaultRegistryProxy()


# ---------------------------------------------------------------------------
# Helpers public
# ---------------------------------------------------------------------------


def canonicalize_predicate(value: str) -> str:
    text = normalize_vi(value).replace(".", "_").replace("-", "_")
    return re.sub(r"_+", "_", text.replace(" ", "_")).strip("_")[:96]


def canonicalize_fact_key(value: str) -> str:
    clean = str(value or "").strip().lower().replace("-", "_")
    clean = re.sub(r"[^a-z0-9._]+", "_", clean)
    clean = re.sub(r"[._]+", lambda m: "." if "." in m.group(0) else "_", clean).strip("._")
    return clean[:128]


def humanize_predicate(predicate: str) -> str:
    text = canonicalize_predicate(predicate).removesuffix("_is").replace("_", " ")
    return text or "thông tin"


def humanize_fact_key(fact_key: str) -> str:
    clean = canonicalize_fact_key(fact_key)
    if not clean:
        return "thông tin"
    tail = clean.split(".")[-1].replace("_", " ")
    translations = {
        "gender identity": "giới tính",
        "gender": "giới tính",
        "name": "tên",
        "nickname": "biệt danh",
        "birth month": "tháng sinh",
        "birth year": "năm sinh",
        "current location": "vị trí hiện tại",
        "living location": "nơi ở",
        "hometown": "quê quán",
        "response style": "cách trả lời mong muốn",
    }
    return translations.get(tail, tail or "thông tin")


def looks_like_fact_question(raw: str, norm: str | None = None) -> bool:
    norm = norm or normalize_vi(raw)
    if "?" in str(raw or ""):
        return True
    padded = f" {norm} "
    has_question_word = any(marker in padded for marker in (" gi ", " nao ", " may ", " dau ", " khong ", " ai ", " bao nhieu "))
    has_memory_marker = any(marker in norm for marker in ("nho", "biet", "con nho", "co nho", "tung noi", "tung bao"))
    has_self = any(marker in padded for marker in (" toi ", " tui ", " minh ", " cua toi ", " cua tui ", " cua minh "))
    return has_question_word and (has_memory_marker or has_self)


def normalize_vi(text: str) -> str:
    value = unicodedata.normalize("NFD", str(text or "").lower()).replace("đ", "d")
    value = "".join(ch for ch in value if unicodedata.category(ch) != "Mn")
    value = re.sub(r"[^a-z0-9\s_.-]+", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def raw_cosine(a: Sequence[float], b: Sequence[float]) -> float:
    if not _has_vector(a) or not _has_vector(b) or len(a) != len(b):
        return 0.0
    dot = sum(float(x) * float(y) for x, y in zip(a, b))
    na = math.sqrt(sum(float(x) * float(x) for x in a))
    nb = math.sqrt(sum(float(y) * float(y) for y in b))
    if na <= 1e-12 or nb <= 1e-12:
        return 0.0
    return max(-1.0, min(1.0, dot / (na * nb)))


def _has_vector(value: Any) -> bool:
    try:
        return value is not None and len(value) > 0
    except Exception:
        return False


def _embed_query(provider: Any, text: str) -> Sequence[float]:
    fn = getattr(provider, "embed_query", None)
    return fn(text) if callable(fn) else provider.embed(text)


def _embed_document(provider: Any, text: str) -> Sequence[float]:
    fn = getattr(provider, "embed_document", None)
    return fn(text) if callable(fn) else provider.embed(text)


def _lexical_phrase_score(norm_text: str, phrase: str) -> float:
    phrase_norm = normalize_vi(phrase)
    if not phrase_norm:
        return 0.0
    padded = f" {norm_text} "
    if f" {phrase_norm} " in padded:
        return min(0.98, 0.86 + min(0.10, max(0, len(phrase_norm.split()) - 1) * 0.025))
    left, right = set(norm_text.split()), set(phrase_norm.split())
    if not left or not right:
        return 0.0
    inter = len(left & right)
    if inter == 0:
        return 0.0
    return _clamp01((inter / len(left | right)) * 0.30 + (inter / len(right)) * 0.55 + (inter / len(left)) * 0.15)


def _unique(values: Iterable[str]) -> tuple[str, ...]:
    out: list[str] = []
    seen: set[str] = set()
    for value in values:
        clean = str(value or "").strip()
        key = normalize_vi(clean)
        if clean and key and key not in seen:
            seen.add(key)
            out.append(clean)
    return tuple(out)


def _unique_routes(routes: Iterable[FrameRoute]) -> tuple[FrameRoute, ...]:
    out: list[FrameRoute] = []
    seen: set[tuple[str, str, str]] = set()
    for route in routes:
        key = (route.frame_type, route.relation, route.target_kind)
        if key not in seen:
            seen.add(key)
            out.append(route)
    return tuple(out)


def _prefer(new: str, old: str, *, generic: str) -> str:
    return str(new) if str(new or "") and str(new) != generic else str(old)


def _clamp01(value: Any) -> float:
    try:
        number = float(value)
    except Exception:
        return 0.0
    if math.isnan(number) or math.isinf(number):
        return 0.0
    return max(0.0, min(1.0, number))


__all__ = [
    "RegisteredPredicate",
    "PredicateDefinition",
    "PredicateResolution",
    "PredicateConflictError",
    "PredicateRegistry",
    "GLOBAL_PREDICATE_REGISTRY",
    "get_default_registry",
    "set_default_registry",
    "using_default_registry",
    "canonicalize_predicate",
    "canonicalize_fact_key",
    "humanize_predicate",
    "humanize_fact_key",
    "looks_like_fact_question",
    "normalize_vi",
    "raw_cosine",
]
