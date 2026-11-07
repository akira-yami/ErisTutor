from __future__ import annotations

from dataclasses import dataclass, field

from memory.claim_extractor import ClaimExtractor
from memory.embedding_index import EmbeddingIndex
from memory.memory_query_compiler import MemoryQueryCompiler
from memory.memory_query_executor import MemoryQueryExecutor
from memory.memory_query_semantics import cosine
from memory.memory_recall_policy import MemoryRecallSurfacePolicy
from memory.predicate_registry import PredicateRegistry
from memory.semantic_memory import SemanticMemoryStore
from memory.understanding.semantic_parser import StructuredFrameParser


def test_registry_resolves_gender_without_slot_hardcode() -> None:
    registry = PredicateRegistry()
    result = registry.resolve_fact_query("nhớ tui giới tính gì không")
    assert result is not None
    assert result.fact_key == "user.gender_identity"
    assert result.score >= 0.80


def test_compiler_builds_gender_fact_query(monkeypatch) -> None:
    monkeypatch.setenv("ERIS_EMBEDDING_ENABLED", "0")
    result = MemoryQueryCompiler().compile("nhớ tui giới tính gì không")
    assert result.best is not None
    assert result.best.target.fact_key == "user.gender_identity"
    assert result.best.intercept_permission is True


def test_gender_disclosure_becomes_semantic_claim() -> None:
    rows = ClaimExtractor().extract("giới tính của tôi là nữ")
    assert len(rows) == 1
    claim = rows[0].claim
    assert claim.predicate == "gender_identity_is"
    assert claim.object_text == "nữ"
    assert claim.qualifier_dict()["fact_key"] == "user.gender_identity"


def test_open_structured_predicate_can_be_queried(monkeypatch) -> None:
    monkeypatch.setenv("ERIS_EMBEDDING_ENABLED", "0")
    registry = PredicateRegistry()
    extractor = ClaimExtractor(parser=StructuredFrameParser())
    payload = {
        "frames": [
            {
                "frame_type": "identity",
                "relation": "favorite_weather",
                "subject": "user",
                "object": "trời mưa",
                "object_type": "weather_preference",
                "modality": "preference",
                "temporal": "stable",
                "confidence": 0.91,
                "fact_key": "user.favorite_weather",
                "aliases": ["thời tiết yêu thích", "tôi thích thời tiết gì"],
                "display_name": "thời tiết yêu thích",
                "cardinality": "multi",
            }
        ]
    }

    # ClaimBuilder dùng global registry production. Đăng ký cùng metadata vào
    # registry test để xác nhận compiler không cần thêm enum/prototype.
    rows = extractor.extract("tôi thích trời mưa", llm_structured=payload)
    assert rows and rows[0].claim.qualifier_dict()["fact_key"] == "user.favorite_weather"
    registry.register_provisional(
        predicate="favorite_weather",
        fact_key="user.favorite_weather",
        aliases=("thời tiết yêu thích", "tôi thích thời tiết gì"),
        display_name="thời tiết yêu thích",
    )
    result = MemoryQueryCompiler(registry=registry).compile("tôi thích thời tiết gì")
    assert result.best is not None
    assert result.best.target.fact_key == "user.favorite_weather"


def test_recall_surface_is_natural_for_found_and_missing(monkeypatch) -> None:
    monkeypatch.setenv("ERIS_EMBEDDING_ENABLED", "0")
    compiler = MemoryQueryCompiler()
    executor = MemoryQueryExecutor()
    policy = MemoryRecallSurfacePolicy()

    class Core:
        def __init__(self, data):
            self.data = data

        def lookup_fact(self, fact_key: str):
            return self.data.get(fact_key)

    ir = compiler.compile("nhớ tui tên gì không").best
    assert ir is not None
    found = policy.surface(executor.execute(ir, memory_core=Core({"user.name": "Khang"})))
    missing = policy.surface(executor.execute(ir, memory_core=Core({})))
    assert found.text == "Bạn tên Khang mà."
    assert missing.text == "Tôi chưa nhớ bạn từng nói tên mình."
    assert "semantic" not in missing.text.lower()


def test_cosine_zero_is_not_remapped_to_half() -> None:
    assert cosine((1.0, 0.0), (0.0, 1.0)) == 0.0
    assert cosine((1.0, 0.0), (-1.0, 0.0)) == -1.0


def test_semantic_store_respects_declared_cosine_scale(tmp_path) -> None:
    class FakeIndex:
        score_scale = "cosine"

    store = SemanticMemoryStore(str(tmp_path / "memory.json"), embedding_index=FakeIndex())
    assert store._normalize_vector_score(0.0) == 0.0
    assert store._normalize_vector_score(0.1) == 0.1
    assert store._normalize_vector_score(-0.2) == 0.0


def test_qwen3_adapter_uses_query_instruction_and_last_token() -> None:
    index = EmbeddingIndex(model_name="Qwen/Qwen3-Embedding-0.6B")
    assert index.model_family == "qwen3"
    assert index._resolved_pooling() == "last_token"
    query = index._prepare_text("nhớ tên tôi không", role="query")
    document = index._prepare_text("Tên người dùng là Khang", role="document")
    assert query.startswith("Instruct:")
    assert "\nQuery:" in query
    assert document == "Tên người dùng là Khang"


@dataclass
class _HydrationItem:
    fact_key: str = "user.favorite_drink"
    aliases: tuple[str, ...] = ("đồ uống yêu thích", "tôi thích uống gì")
    metadata: dict = field(default_factory=lambda: {
        "predicate": "favorite_drink",
        "object_type": "drink",
        "display_name": "đồ uống yêu thích",
    })


def test_registry_hydrates_provisional_predicate_from_store_item() -> None:
    registry = PredicateRegistry()
    assert registry.by_fact_key("user.favorite_drink") is None
    assert registry.hydrate_from_memory_items([_HydrationItem()]) == 1
    resolved = registry.resolve_fact_query("tôi thích uống gì")
    assert resolved is not None
    assert resolved.fact_key == "user.favorite_drink"
