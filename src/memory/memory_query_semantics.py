from __future__ import annotations

"""
memory_query_semantics.py

Semantic hint engine cho memory query.

Cấm để semantic/embedding execute exact recall.
Vector không được quyết định "câu thứ 3 là câu nào". Nó chỉ gợi ý:
- query thuộc vùng nào: exact turn / fact / count / summary / correction
- paraphrase đang trỏ tới role/act/relation nào
- fact_key có thể là gì

Nếu sau này bạn có embedding provider thật, cắm vào đây.
Nếu không, prototype lexical fallback vẫn chạy nhưng luôn bị cap confidence.
"""

from dataclasses import dataclass, field
from typing import Any, Protocol, Sequence
import math
import os
import re
import unicodedata

from .memory_query_ir import ActRef, QueryIntent, RelationRef, RoleRef, SurfaceMode, clamp01
from .predicate_registry import PredicateRegistry, get_default_registry, raw_cosine


class EmbeddingProvider(Protocol):
    """Provider embedding tùy chọn.

    Provider mới nên tách query/document. `embed()` vẫn được hỗ trợ qua adapter
    mềm để model cũ không nổ tung chỉ vì kiến trúc cuối cùng biết phân vai.
    """

    def embed_query(self, text: str) -> Sequence[float]:
        ...

    def embed_document(self, text: str) -> Sequence[float]:
        ...


@dataclass(slots=True)
class SemanticQueryHint:
    """Gợi ý semantic, không phải query cuối."""

    intent_scores: dict[QueryIntent, float] = field(default_factory=dict)
    role_scores: dict[RoleRef, float] = field(default_factory=dict)
    act_scores: dict[ActRef, float] = field(default_factory=dict)
    relation_scores: dict[RelationRef, float] = field(default_factory=dict)
    fact_key_scores: dict[str, float] = field(default_factory=dict)
    surface_scores: dict[SurfaceMode, float] = field(default_factory=dict)
    confidence: float = 0.0
    ambiguity: float = 1.0
    is_fallback: bool = True
    evidence: tuple[str, ...] = ()

    def top_intent(self) -> tuple[QueryIntent, float] | None:
        return _top(self.intent_scores)

    def top_fact_key(self) -> tuple[str, float] | None:
        return _top(self.fact_key_scores)


class MemoryQuerySemanticHintEngine:
    """
    Prototype semantic scorer.

    Có thể chạy ở 2 mode:
    - fallback prototype token similarity: ổn để bootstrap, cap confidence.
    - provider embedding thật: score prototype bằng cosine.
    """

    def __init__(
        self,
        embedding_provider: EmbeddingProvider | None = None,
        *,
        registry: PredicateRegistry | None = None,
        fallback_confidence_cap: float = 0.72,
        embedding_min_cosine: float | None = None,
    ) -> None:
        self.embedding_provider = embedding_provider
        self.registry = registry or get_default_registry()
        self.fallback_confidence_cap = fallback_confidence_cap
        self.embedding_min_cosine = float(
            embedding_min_cosine
            if embedding_min_cosine is not None
            else os.getenv("ERIS_MEMORY_QUERY_MIN_COSINE", "0.38")
        )
        self._prototype_vectors: dict[str, Sequence[float]] = {}

    def analyze(self, text: str) -> SemanticQueryHint:
        norm = normalize_vi(text)

        if self.embedding_provider:
            hint = self._embedding_hint(norm)
            if hint.confidence > 0.0:
                return hint

        return self._fallback_hint(norm)

    # ------------------------------------------------------------------
    # Fallback prototype path
    # ------------------------------------------------------------------

    def _fallback_hint(self, norm: str) -> SemanticQueryHint:
        evidence: list[str] = []
        intent_scores: dict[QueryIntent, float] = {}
        role_scores: dict[RoleRef, float] = {}
        act_scores: dict[ActRef, float] = {}
        relation_scores: dict[RelationRef, float] = {}
        fact_key_scores: dict[str, float] = {}
        surface_scores: dict[SurfaceMode, float] = {}

        for intent, phrases in INTENT_PROTOTYPES.items():
            score = max((_token_similarity(norm, p) for p in phrases), default=0.0)
            if score > 0.18:
                intent_scores[intent] = min(self.fallback_confidence_cap, score)
                evidence.append(f"intent:{intent.value}:{score:.2f}")

        for role, phrases in ROLE_PROTOTYPES.items():
            score = max((_token_similarity(norm, p) for p in phrases), default=0.0)
            if score > 0.18:
                role_scores[role] = min(self.fallback_confidence_cap, score)
                evidence.append(f"role:{role.value}:{score:.2f}")

        for act, phrases in ACT_PROTOTYPES.items():
            score = max((_token_similarity(norm, p) for p in phrases), default=0.0)
            if score > 0.18:
                act_scores[act] = min(self.fallback_confidence_cap, score)
                evidence.append(f"act:{act.value}:{score:.2f}")

        for relation, phrases in RELATION_PROTOTYPES.items():
            score = max((_token_similarity(norm, p) for p in phrases), default=0.0)
            if score > 0.18:
                relation_scores[relation] = min(self.fallback_confidence_cap, score)
                evidence.append(f"relation:{relation.value}:{score:.2f}")

        for fact_key, phrases in self.registry.fact_key_prototypes().items():
            score = max((_token_similarity(norm, p) for p in phrases), default=0.0)
            if score > 0.18:
                fact_key_scores[fact_key] = min(self.fallback_confidence_cap, score)
                evidence.append(f"fact:{fact_key}:{score:.2f}")

        if any(p in norm for p in ("nguyen van", "chinh xac", "cau nao", "la gi")):
            surface_scores[SurfaceMode.EXACT] = 0.64
        if any(p in norm for p in ("tom tat", "noi chung", "gist")):
            surface_scores[SurfaceMode.GIST] = 0.64

        all_scores = (
            list(intent_scores.values())
            + list(role_scores.values())
            + list(act_scores.values())
            + list(relation_scores.values())
            + list(fact_key_scores.values())
        )
        confidence = max(all_scores, default=0.0)
        ambiguity = _ambiguity(all_scores)

        return SemanticQueryHint(
            intent_scores=intent_scores,
            role_scores=role_scores,
            act_scores=act_scores,
            relation_scores=relation_scores,
            fact_key_scores=fact_key_scores,
            surface_scores=surface_scores,
            confidence=confidence,
            ambiguity=ambiguity,
            is_fallback=True,
            evidence=tuple(evidence),
        )

    # ------------------------------------------------------------------
    # Embedding path
    # ------------------------------------------------------------------

    def _embedding_hint(self, norm: str) -> SemanticQueryHint:
        try:
            text_vec = _embed_query(self.embedding_provider, norm)
        except Exception:
            return SemanticQueryHint(confidence=0.0, is_fallback=True, evidence=("embedding_failed",))

        # Embedding path vẫn chỉ score prototype, không execute query.
        intent_scores = self._score_enum_prototypes(text_vec, INTENT_PROTOTYPES, QueryIntent)
        role_scores = self._score_enum_prototypes(text_vec, ROLE_PROTOTYPES, RoleRef)
        act_scores = self._score_enum_prototypes(text_vec, ACT_PROTOTYPES, ActRef)
        relation_scores = self._score_enum_prototypes(text_vec, RELATION_PROTOTYPES, RelationRef)
        fact_key_scores = self._score_string_prototypes(text_vec, self.registry.fact_key_prototypes())

        all_scores = (
            list(intent_scores.values())
            + list(role_scores.values())
            + list(act_scores.values())
            + list(relation_scores.values())
            + list(fact_key_scores.values())
        )
        confidence = max(all_scores, default=0.0)
        return SemanticQueryHint(
            intent_scores=intent_scores,
            role_scores=role_scores,
            act_scores=act_scores,
            relation_scores=relation_scores,
            fact_key_scores=fact_key_scores,
            confidence=confidence,
            ambiguity=_ambiguity(all_scores),
            is_fallback=False,
            evidence=("embedding_prototype_scores",),
        )

    def _score_enum_prototypes(self, text_vec, bank, enum_cls):
        out = {}
        for key, phrases in bank.items():
            score = max((self._prototype_cosine(text_vec, p) for p in phrases), default=0.0)
            if score >= self.embedding_min_cosine:
                out[key] = clamp01(score)
        return out

    def _score_string_prototypes(self, text_vec, bank):
        out = {}
        for key, phrases in bank.items():
            score = max((self._prototype_cosine(text_vec, p) for p in phrases), default=0.0)
            if score >= self.embedding_min_cosine:
                out[key] = clamp01(score)
        return out

    def _prototype_cosine(self, text_vec: Sequence[float], phrase: str) -> float:
        try:
            vec = self._prototype_vectors.get(phrase)
            if vec is None:
                vec = _embed_document(self.embedding_provider, phrase)
                self._prototype_vectors[phrase] = vec
            return cosine(text_vec, vec)
        except Exception:
            return 0.0


# ---------------------------------------------------------------------------
# Prototype bank
# ---------------------------------------------------------------------------


INTENT_PROTOTYPES: dict[QueryIntent, tuple[str, ...]] = {
    QueryIntent.RECALL_TURN: (
        "hoi nay toi hoi gi",
        "em vua tra loi gi",
        "cau hoi thu ba",
        "luc do em noi gi",
        "nho lai cau vua roi",
    ),
    QueryIntent.RECALL_FACT: (
        "toi ten gi",
        "toi sinh thang may",
        "toi dang o dau",
        "nho thong tin cua toi",
        # Preference cũng là fact query, nhưng chỉ là semantic hint.
        # Nó vẫn không được intercept public answer nếu slot/parser không chắc.
        "so thich cua toi la gi",
        "toi thich gi",
        "gu cua toi la gi",
    ),
    QueryIntent.RECALL_COUNT: (
        "toi da hoi bao nhieu cau",
        "dem so cau hoi",
        "co bao nhieu cau hoi",
    ),
    QueryIntent.RECALL_SUMMARY: (
        "tom tat nhung gi toi noi",
        "gan day toi noi ve chu de gi",
        "noi chung hoi nay noi gi",
    ),
    QueryIntent.EXPLAIN_MEMORY_FAILURE: (
        "sao em nho sai",
        "tri nho bi lag",
        "sao lai quen",
    ),
}

ROLE_PROTOTYPES: dict[RoleRef, tuple[str, ...]] = {
    RoleRef.USER: ("toi hoi", "cua toi", "ban hoi", "toi da noi"),
    RoleRef.ASSISTANT: ("em tra loi", "em dap", "em noi", "ban tra loi", "eris noi"),
}

ACT_PROTOTYPES: dict[ActRef, tuple[str, ...]] = {
    ActRef.QUESTION: ("cau hoi", "toi hoi", "hoi gi", "hoi ve"),
    ActRef.ANSWER: ("tra loi", "dap sao", "phan hoi", "reply", "em noi gi"),
    ActRef.CORRECTION: ("khong phai", "y toi la", "em hieu sai", "sua lai"),
    ActRef.FACT_DISCLOSURE: ("toi ten la", "toi sinh thang", "toi hien o", "toi tung o"),
}

RELATION_PROTOTYPES: dict[RelationRef, tuple[str, ...]] = {
    RelationRef.ANSWER_TO: (
        "cau tra loi cho cau hoi",
        "em dap sao luc toi hoi",
        "em tra loi gi khi toi hoi",
        "sau cau hoi do em noi gi",
    ),
    RelationRef.RECENT_PREVIOUS: (
        "truoc do",
        "hoi nay",
        "luc nay",
        "vua roi",
        "gan day",
    ),
}

# Snapshot tương thích cho code ngoài đang import hằng số này. Engine runtime lấy
# prototype trực tiếp từ registry để predicate provisional cũng được query.
FACT_KEY_PROTOTYPES: dict[str, tuple[str, ...]] = get_default_registry().fact_key_prototypes()



# ---------------------------------------------------------------------------
# Math/text helpers
# ---------------------------------------------------------------------------


def normalize_vi(text: str) -> str:
    text = unicodedata.normalize("NFD", text.lower())
    text = "".join(ch for ch in text if unicodedata.category(ch) != "Mn")
    text = re.sub(r"[^a-z0-9\s_.-]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _token_similarity(a: str, b: str) -> float:
    a_tokens = set(normalize_vi(a).split())
    b_tokens = set(normalize_vi(b).split())
    if not a_tokens or not b_tokens:
        return 0.0
    inter = len(a_tokens & b_tokens)
    # Jaccard pha recall để paraphrase ngắn vẫn sống được.
    jaccard = inter / len(a_tokens | b_tokens)
    recall = inter / min(len(a_tokens), len(b_tokens))
    return clamp01(jaccard * 0.45 + recall * 0.55)


def cosine(a: Sequence[float], b: Sequence[float]) -> float:
    """Raw cosine [-1, 1]. Cosine 0 là không liên quan, không phải confidence 0.5."""

    return raw_cosine(a, b)


def _embed_query(provider: Any, text: str) -> Sequence[float]:
    fn = getattr(provider, "embed_query", None)
    if callable(fn):
        return fn(text)
    return provider.embed(text)


def _embed_document(provider: Any, text: str) -> Sequence[float]:
    fn = getattr(provider, "embed_document", None)
    if callable(fn):
        return fn(text)
    return provider.embed(text)


def _ambiguity(scores) -> float:
    values = sorted((float(s) for s in scores), reverse=True)
    if not values:
        return 1.0
    if len(values) == 1:
        return 1.0 - values[0]
    margin = values[0] - values[1]
    return clamp01(1.0 - margin * 1.8)


def _top(scores: dict) -> tuple | None:
    if not scores:
        return None
    return max(scores.items(), key=lambda item: item[1])
