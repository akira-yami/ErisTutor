# -*- coding: utf-8 -*-
"""Backend tương đồng câu đa ngôn ngữ cho semantic-equivalence gate.

Mục tiêu là có một contract thật, nhưng không biến việc cài một model embedding
thành điều kiện để Eris khởi động. Backend chạy theo thứ tự:

1. ``sentence-transformers`` + model local nếu đã được cài/cached;
2. fallback deterministic bằng character n-gram + token overlap;
3. không bao giờ tải model qua mạng trong hot path.

Nhờ vậy production có thể dùng LaBSE/SBERT đa ngôn ngữ, còn máy dev thiếu model
vẫn có một điểm số ổn định và trace rõ là đang degraded. Ít nhất hệ thống biết
mình đang đo bằng thước nhựa, thay vì gọi nó là kính hiển vi.
"""

from __future__ import annotations

import math
import os
import re
import threading
import unicodedata
from collections import Counter
from dataclasses import dataclass
from difflib import SequenceMatcher
from functools import lru_cache
from typing import Any


@dataclass(frozen=True, slots=True)
class SemanticSimilarityResult:
    score: float
    backend: str
    degraded: bool = False
    reason: str = ""


def _backend_mode() -> str:
    value = str(os.environ.get("ERIS_SEMANTIC_BACKEND", "fallback") or "fallback").strip().lower()
    aliases = {
        "off": "fallback",
        "disabled": "fallback",
        "local": "sentence_transformers",
        "sbert": "sentence_transformers",
        "labse": "sentence_transformers",
        "lexical": "fallback",
        "heuristic": "fallback",
    }
    return aliases.get(value, value if value in {"auto", "sentence_transformers", "fallback"} else "auto")


def _normalize(text: str) -> str:
    raw = unicodedata.normalize("NFKC", str(text or "")).lower()
    raw = re.sub(r"[^\w\s]", " ", raw, flags=re.UNICODE)
    return " ".join(raw.split())


@lru_cache(maxsize=4096)
def _char_ngrams(text: str, n: int = 3) -> Counter[str]:
    normalized = f"  {_normalize(text)}  "
    if not normalized.strip():
        return Counter()
    return Counter(normalized[index : index + n] for index in range(max(0, len(normalized) - n + 1)))


def _cosine_counter(left: Counter[str], right: Counter[str]) -> float:
    if not left or not right:
        return 0.0
    dot = sum(value * right.get(key, 0) for key, value in left.items())
    left_norm = math.sqrt(sum(value * value for value in left.values()))
    right_norm = math.sqrt(sum(value * value for value in right.values()))
    if left_norm <= 0.0 or right_norm <= 0.0:
        return 0.0
    return max(0.0, min(1.0, dot / (left_norm * right_norm)))


def _fallback_similarity(left: str, right: str) -> float:
    left_norm = _normalize(left)
    right_norm = _normalize(right)
    if not left_norm or not right_norm:
        return 0.0
    if left_norm == right_norm:
        return 1.0

    ngram_score = _cosine_counter(_char_ngrams(left_norm), _char_ngrams(right_norm))
    left_tokens = set(left_norm.split())
    right_tokens = set(right_norm.split())
    union = left_tokens | right_tokens
    token_score = len(left_tokens & right_tokens) / max(1, len(union))
    sequence_score = SequenceMatcher(None, left_norm, right_norm).ratio()
    return max(0.0, min(1.0, 0.56 * ngram_score + 0.24 * token_score + 0.20 * sequence_score))


class MultilingualSemanticSimilarity:
    """Lazy semantic scorer, tuyệt đối không tải model qua mạng.

    ``sentence-transformers`` chỉ được dùng nếu package và model local đều có.
    Khi không có, ``degraded=True`` được trả về để gate chọn threshold phù hợp.
    """

    def __init__(self, *, mode: str | None = None, model_name: str | None = None) -> None:
        self.mode = str(mode or _backend_mode()).strip().lower()
        self.model_name = str(
            model_name
            or os.environ.get(
                "ERIS_SEMANTIC_EMBEDDING_MODEL",
                "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
            )
        ).strip()
        self._model: Any | None = None
        self._model_attempted = False
        self._lock = threading.RLock()

    def _load_model(self) -> Any | None:
        if self.mode == "fallback":
            return None
        with self._lock:
            if self._model_attempted:
                return self._model
            self._model_attempted = True
            try:
                from sentence_transformers import SentenceTransformer

                # local_files_only là hàng rào quan trọng: một turn chat không có
                # quyền tự kéo vài trăm MB model chỉ vì Director muốn thơ hơn.
                try:
                    self._model = SentenceTransformer(self.model_name, local_files_only=True)
                except TypeError:
                    # Phiên bản cũ không nhận local_files_only. Chỉ cho load nếu
                    # user đã chỉ rõ một path local tồn tại.
                    if os.path.exists(self.model_name):
                        self._model = SentenceTransformer(self.model_name)
                    else:
                        self._model = None
            except Exception:
                self._model = None
            return self._model

    @staticmethod
    def _vector_cosine(left: Any, right: Any) -> float:
        try:
            left_values = [float(item) for item in left]
            right_values = [float(item) for item in right]
        except Exception:
            return 0.0
        if not left_values or len(left_values) != len(right_values):
            return 0.0
        dot = sum(a * b for a, b in zip(left_values, right_values))
        left_norm = math.sqrt(sum(a * a for a in left_values))
        right_norm = math.sqrt(sum(b * b for b in right_values))
        if left_norm <= 0.0 or right_norm <= 0.0:
            return 0.0
        return max(0.0, min(1.0, dot / (left_norm * right_norm)))

    @lru_cache(maxsize=2048)
    def score(self, left: str, right: str) -> SemanticSimilarityResult:
        source = str(left or "").strip()
        candidate = str(right or "").strip()
        if not source or not candidate:
            return SemanticSimilarityResult(0.0, "empty", True, "empty_input")
        if _normalize(source) == _normalize(candidate):
            return SemanticSimilarityResult(1.0, "exact", False, "normalized_exact")

        model = self._load_model()
        if model is not None:
            try:
                vectors = model.encode(
                    [source, candidate],
                    normalize_embeddings=True,
                    show_progress_bar=False,
                )
                score = self._vector_cosine(vectors[0], vectors[1])
                return SemanticSimilarityResult(score, "sentence_transformers", False, self.model_name)
            except Exception as exc:
                fallback = _fallback_similarity(source, candidate)
                return SemanticSimilarityResult(
                    fallback,
                    "char_ngram_fallback",
                    True,
                    f"embedding_failed:{type(exc).__name__}",
                )

        fallback = _fallback_similarity(source, candidate)
        reason = "forced_fallback" if self.mode == "fallback" else "embedding_unavailable_local"
        return SemanticSimilarityResult(fallback, "char_ngram_fallback", True, reason)


GLOBAL_SEMANTIC_SIMILARITY = MultilingualSemanticSimilarity()


__all__ = [
    "GLOBAL_SEMANTIC_SIMILARITY",
    "MultilingualSemanticSimilarity",
    "SemanticSimilarityResult",
]
