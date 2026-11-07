from __future__ import annotations

import json

from memory.claim_extractor import ClaimExtractor
from memory.claims.claim_builder import ClaimBuilder, PREDICATE_DEFAULTS, RELATION_TO_PREDICATE
from memory.memory_query_compiler import MemoryQueryCompiler
from memory.memory_services import MemoryRuntimeServices
from memory.predicate_catalog import BUILTIN_PREDICATES, FrameRoute
from memory.predicate_ontology import PREDICATES
from memory.predicate_registry import PredicateRegistry
from memory.query_intent import QUERY_TARGETS, SemanticQueryIntentCompiler
from memory.semantic_memory import SemanticMemoryStore
from memory.understanding.semantic_parser import StructuredFrameParser


def test_legacy_views_are_generated_from_one_catalog() -> None:
    builtin_names = {item.predicate for item in BUILTIN_PREDICATES}
    assert set(PREDICATES) == builtin_names
    assert set(PREDICATE_DEFAULTS) == builtin_names
    assert all(
        RELATION_TO_PREDICATE[(route.frame_type, route.relation)] == item.predicate
        for item in BUILTIN_PREDICATES
        for route in item.frame_routes
        if not route.target_kind
    )
    expected_targets = {
        item.ontology.answer_target
        for item in BUILTIN_PREDICATES
        if item.fact_key and item.ontology.answer_target
    }
    assert set(QUERY_TARGETS) == expected_targets


def test_structured_predicate_uses_local_registry_end_to_end(monkeypatch) -> None:
    monkeypatch.setenv("ERIS_EMBEDDING_ENABLED", "0")
    registry = PredicateRegistry()
    builder = ClaimBuilder(registry=registry)
    extractor = ClaimExtractor(parser=StructuredFrameParser(), builder=builder)
    payload = {
        "frames": [
            {
                "frame_type": "preference",
                "relation": "favorite_drink",
                "subject": "user",
                "object": "cà phê sữa",
                "object_type": "drink",
                "modality": "preference",
                "temporal": "stable",
                "confidence": 0.93,
                "fact_key": "user.favorite_drink",
                "aliases": ["đồ uống yêu thích", "tôi thích uống gì"],
                "display_name": "đồ uống yêu thích",
                "cardinality": "multi",
            }
        ]
    }
    claims = extractor.extract("tôi thích cà phê sữa", llm_structured=payload)
    assert claims and claims[0].claim.predicate == "favorite_drink"
    assert registry.by_fact_key("user.favorite_drink") is not None

    result = MemoryQueryCompiler(registry=registry).compile("tôi thích uống gì")
    assert result.best is not None
    assert result.best.target.fact_key == "user.favorite_drink"


def test_generic_disclosure_works_for_runtime_predicate() -> None:
    registry = PredicateRegistry()
    registry.register_provisional(
        predicate="favorite_drink",
        fact_key="user.favorite_drink",
        aliases=("đồ uống yêu thích", "thích uống"),
        disclosure_cues=("thích uống",),
        display_name="đồ uống yêu thích",
    )
    resolution = registry.resolve_disclosure("tôi thích uống cà phê")
    assert resolution is not None
    assert resolution.fact_key == "user.favorite_drink"


def test_frame_route_can_specialize_by_target_kind() -> None:
    registry = PredicateRegistry()
    location = registry.resolve_frame("preference", "likes", target_kind="location")
    generic = registry.resolve_frame("preference", "likes", target_kind="topic")
    assert location is not None and location.predicate == "preference_location"
    assert generic is not None and generic.predicate == "general_preference"


def test_same_fact_key_merges_into_canonical_owner() -> None:
    registry = PredicateRegistry()
    registry.register_provisional(
        predicate="preferred_name",
        fact_key="user.name",
        aliases=("tên xưng hô",),
    )
    canonical = registry.by_fact_key("user.name")
    assert canonical is not None
    assert canonical.predicate == "name_is"
    assert "tên xưng hô" in canonical.aliases
    assert registry.get("preferred_name") is None


def test_registry_v2_persistence_roundtrip(tmp_path) -> None:
    path = tmp_path / "predicates.json"
    registry = PredicateRegistry(persistence_path=str(path))
    registry.register_provisional(
        predicate="favorite_weather",
        fact_key="user.favorite_weather",
        aliases=("thời tiết yêu thích",),
        frame_routes=(FrameRoute("preference", "favorite_weather"),),
        display_name="thời tiết yêu thích",
        persist=True,
    )
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["version"] == 2

    restored = PredicateRegistry(persistence_path=str(path))
    item = restored.by_fact_key("user.favorite_weather")
    assert item is not None
    assert item.frame_routes[0].relation == "favorite_weather"
    assert item.surface.display_name == "thời tiết yêu thích"


def test_dynamic_query_intent_reads_registry_not_static_table() -> None:
    registry = PredicateRegistry()
    registry.register_provisional(
        predicate="favorite_weather",
        fact_key="user.favorite_weather",
        aliases=("thời tiết yêu thích",),
        query_examples=("tôi thích thời tiết gì",),
        object_type="weather",
        display_name="thời tiết yêu thích",
    )
    intent = SemanticQueryIntentCompiler(allow_fallback=True, registry=registry).compile("tôi thích thời tiết gì")
    assert intent.is_known
    assert intent.target_predicates == ("favorite_weather",)
    assert intent.answer_target == "user.favorite_weather"


def test_services_keep_registries_isolated(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("ERIS_EMBEDDING_ENABLED", "0")
    one = MemoryRuntimeServices.build(registry_path=str(tmp_path / "one.json"))
    two = MemoryRuntimeServices.build(registry_path=str(tmp_path / "two.json"))
    one.registry.register_provisional(
        predicate="favorite_drink",
        fact_key="user.favorite_drink",
        aliases=("đồ uống yêu thích",),
    )
    assert one.registry.by_fact_key("user.favorite_drink") is not None
    assert two.registry.by_fact_key("user.favorite_drink") is None


def test_semantic_store_uses_injected_cardinality_for_new_fact(tmp_path) -> None:
    registry = PredicateRegistry()
    registry.register_provisional(
        predicate="primary_editor",
        fact_key="user.primary_editor",
        aliases=("editor chính",),
        cardinality="single",
        display_name="editor chính",
    )
    store = SemanticMemoryStore(str(tmp_path / "memory.json"), registry=registry)
    assert store._is_current_fact_replacement(
        "user.primary_editor",
        old_value="VS Code",
        new_value="Zed",
        confidence=0.90,
    ) is True
