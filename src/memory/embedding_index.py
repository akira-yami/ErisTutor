# -*- coding: utf-8 -*-
"""
memory/embedding_index.py
=========================

In-memory embedding index dùng chung cho semantic memory và query compiler.

Điểm quan trọng của bản này:
- Tách encode QUERY và DOCUMENT. Qwen3/E5 cần hai đường khác nhau.
- Tự chọn pooling theo họ model: Qwen3 dùng last-token, MiniLM dùng mean pooling.
- Chuẩn hoá L2 ngay sau pooling.
- Search trả RAW COSINE trong [-1, 1]. Không còn biến cosine 0 thành 0.5.
- Cache có namespace model + role, nên đổi model/prompt không tái dùng vector cũ.

Không cần vector database cho vài trăm item. SQLite/JSON + numpy vẫn đủ, loài
người chưa cần triệu hồi thêm ba container chỉ để nhớ tên một người.
"""

from __future__ import annotations

import hashlib
import logging
import os
import threading
import time
from pathlib import Path
from typing import Any, Callable, Iterable, Literal, Sequence

import numpy as np

logger = logging.getLogger(__name__)

_LOCAL_MODEL_DIR = Path(__file__).resolve().parents[1] / "paraphrase-multilingual-MiniLM-L12-v2"
EmbeddingRole = Literal["query", "document"]


def _cosine_similarity(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """Raw cosine giữa vector a và matrix b (N, D), kết quả [-1, 1]."""

    a_norm = a / (np.linalg.norm(a) + 1e-12)
    b_norms = np.linalg.norm(b, axis=1, keepdims=True) + 1e-12
    return (b / b_norms) @ a_norm


def _short_hash(text: str) -> str:
    return hashlib.md5(text.encode("utf-8"), usedforsecurity=False).hexdigest()[:20]


class EmbeddingIndex:
    """Embedding provider + in-memory search index.

    Contract chính:
    - embed_query(text): vector query có instruction/prefix phù hợp model.
    - embed_document(text): vector document không dùng query instruction.
    - search(...): list[(raw_cosine, item)].

    `embed()` vẫn tồn tại để code cũ không vỡ và mặc định coi text là document.
    """

    DEFAULT_MODEL = "paraphrase-multilingual-MiniLM-L12-v2"
    score_scale = "cosine"

    def __init__(
        self,
        *,
        model_name: str | None = None,
        cache_dir: str | None = None,
        device: str | None = None,
        max_seq_length: int | None = None,
        pooling_strategy: str | None = None,
        query_instruction: str | None = None,
    ) -> None:
        self.model_name = model_name or os.getenv("ERIS_EMBEDDING_MODEL", self.DEFAULT_MODEL)
        self.cache_dir = cache_dir or os.getenv(
            "ERIS_EMBEDDING_CACHE_DIR",
            str(Path("embedding_model")),
        )
        self.model_family = self._detect_model_family(self.model_name)
        self.max_seq_length = int(
            max_seq_length
            if max_seq_length is not None
            else os.getenv("ERIS_EMBEDDING_MAX_LENGTH", "512")
        )
        self.pooling_strategy = str(
            pooling_strategy
            or os.getenv("ERIS_EMBEDDING_POOLING", "auto")
        ).strip().lower()
        self.query_instruction = str(
            query_instruction
            if query_instruction is not None
            else os.getenv(
                "ERIS_EMBEDDING_QUERY_INSTRUCTION",
                "Given a colloquial Vietnamese memory question, retrieve facts or conversation evidence that directly answer it.",
            )
        ).strip()
        self.min_cosine = float(os.getenv("ERIS_EMBEDDING_MIN_COSINE", "0.20"))
        self._device = device or "auto"
        self._model: Any = None
        self._tokenizer: Any = None
        self._loaded = False
        self._load_error = ""
        self._load_failed_signaled = False
        self._vector_cache: dict[str, np.ndarray] = {}
        self._load_lock = threading.Lock()
        self._async_load = False
        self._loader: threading.Thread | None = None

    # ------------------------------------------------------------------
    # Public search contract
    # ------------------------------------------------------------------

    def search(
        self,
        query: str,
        items: list[Any],
        *,
        text_fn: Callable[[Any], str],
        limit: int = 24,
    ) -> list[tuple[float, Any]]:
        """Tìm candidate theo raw cosine.

        Score trả về nằm trong [-1, 1]. Downstream phải biết rõ scale này, không
        được đoán bằng việc nhìn con số rồi cầu nguyện.
        """

        if not items or not str(query or "").strip():
            return []
        if not self._ensure_loaded():
            return []
        try:
            query_vec = self._encode([str(query)], role="query")[0]
        except Exception as exc:
            logger.warning("EmbeddingIndex query encode failed: %s", exc)
            return []

        texts = [str(text_fn(item) or "") for item in items]
        vecs = self._encode_with_cache(texts, role="document")
        if vecs is None or len(vecs) == 0:
            return []

        scores = _cosine_similarity(query_vec, vecs)
        top_indices = np.argsort(scores)[::-1][: max(1, int(limit))]
        results: list[tuple[float, Any]] = []
        for idx in top_indices:
            score = float(scores[idx])
            if score >= self.min_cosine:
                results.append((score, items[int(idx)]))
        return results

    def ensure_vectors(
        self,
        items: list[Any],
        *,
        text_fn: Callable[[Any], str],
    ) -> int:
        """Pre-embed document vào cache, trả số text mới."""

        if not self._ensure_loaded():
            return 0
        texts = [str(text_fn(item) or "") for item in items]
        keys = [self._cache_key(text, "document") for text in texts]
        new_count = sum(1 for key in keys if key not in self._vector_cache)
        if new_count == 0:
            return 0
        try:
            self._encode_with_cache(texts, role="document")
        except Exception as exc:
            logger.warning("EmbeddingIndex ensure_vectors failed: %s", exc)
            return 0
        return new_count

    def clear_cache(self) -> None:
        self._vector_cache.clear()

    def debug_status(self) -> dict[str, Any]:
        return {
            "loaded": self._loaded,
            "load_error": self._load_error,
            "model": self.model_name,
            "model_family": self.model_family,
            "device": self._device,
            "pooling": self._resolved_pooling(),
            "max_seq_length": self.max_seq_length,
            "score_scale": self.score_scale,
            "min_cosine": self.min_cosine,
            "query_instruction_enabled": bool(self.query_instruction),
            "cache_size": len(self._vector_cache),
            "load_failed_signaled": self._load_failed_signaled,
            "loading": bool(self._loader and self._loader.is_alive()),
        }

    # ------------------------------------------------------------------
    # Provider contract
    # ------------------------------------------------------------------

    def embed_query(self, text: str) -> tuple[float, ...]:
        vectors = self._embed_many([str(text or "")], role="query")
        return vectors[0] if vectors else ()

    def embed_document(self, text: str) -> tuple[float, ...]:
        vectors = self._embed_many([str(text or "")], role="document")
        return vectors[0] if vectors else ()

    def embed_documents(self, texts: Iterable[str]) -> list[tuple[float, ...]]:
        return self._embed_many([str(text or "") for text in texts], role="document")

    def embed(self, text: str) -> tuple[float, ...]:
        """Compatibility API: text chung được coi là document."""

        return self.embed_document(text)

    def embed_events(self, texts: Iterable[str]) -> list[tuple[float, ...]]:
        return self.embed_documents(texts)

    # ------------------------------------------------------------------
    # Model loading
    # ------------------------------------------------------------------

    def _ensure_loaded(self) -> bool:
        if self._loaded:
            return True
        if self._load_error:
            return False
        if self._async_load:
            self.warmup()
            return False
        return self._load_once()

    def warmup(self) -> None:
        """Shared runtime recall uses lexical results while the model loads."""
        if self._loaded or self._load_error or self._loader is not None:
            return
        with self._load_lock:
            if self._loaded or self._load_error or self._loader is not None:
                return
            self._loader = threading.Thread(target=self._load_once, name="ErisEmbeddingWarmup", daemon=True)
            loader = self._loader
        loader.start()

    def _load_once(self) -> bool:
        # Never make a foreground lookup wait for a background model import.
        if not self._load_lock.acquire(blocking=False):
            return False
        try:
            if self._loaded:
                return True
            if self._load_error:
                return False
            self._load_model()
            self._loaded = True
            return True
        except Exception as exc:
            self._load_error = f"{type(exc).__name__}: {exc}"
            # Fail-soft: recall vẫn chạy lexical, vector rỗng, KHÔNG raise. Nhưng lỗi
            # không được im lặng: phát một health signal rõ ràng ĐÚNG MỘT LẦN
            # (logger.error) rồi set cờ để các lượt sau không spam log.
            if not self._load_failed_signaled:
                self._load_failed_signaled = True
                logger.error(
                    "EmbeddingIndex model load failed (%s): %s — recall rơi về lexical only, "
                    "dense=0.0. Lỗi này chỉ báo một lần; theo dõi debug_status().load_failed_signaled.",
                    self.model_name,
                    exc,
                )
            else:
                logger.debug(
                    "EmbeddingIndex model load vẫn fail (%s): %s", self.model_name, exc
                )
            return False
        finally:
            self._load_lock.release()

    def _load_model(self) -> None:
        if self._device == "auto":
            self._device = "cuda" if self._cuda_available() else "cpu"
        from transformers import AutoModel, AutoTokenizer

        t0 = time.time()
        os.makedirs(self.cache_dir, exist_ok=True)
        local_model_path = os.path.join(self.cache_dir, "config.json")
        load_path = self.cache_dir if os.path.isfile(local_model_path) else self.model_name
        if not os.path.isdir(load_path) and "/" not in load_path:
            load_path = f"sentence-transformers/{load_path}"
        cache_arg = None if os.path.isfile(local_model_path) else self.cache_dir
        trust_remote_code = os.getenv("ERIS_EMBEDDING_TRUST_REMOTE_CODE", "0").strip().lower() in {
            "1", "true", "yes", "on"
        }

        logger.info(
            "[EmbeddingIndex] Loading model from %s (family=%s, device=%s, pooling=%s)...",
            load_path,
            self.model_family,
            self._device,
            self._resolved_pooling(),
        )
        self._tokenizer = AutoTokenizer.from_pretrained(
            load_path,
            cache_dir=cache_arg,
            trust_remote_code=trust_remote_code,
        )
        # Last-token pooling cần padding trái để token cuối luôn là token thật.
        if self._resolved_pooling() == "last_token":
            self._tokenizer.padding_side = "left"
        self._model = AutoModel.from_pretrained(
            load_path,
            cache_dir=cache_arg,
            trust_remote_code=trust_remote_code,
        )
        self._model.eval()
        if self._device != "cpu":
            try:
                self._model = self._model.to(self._device)
            except Exception:
                self._device = "cpu"
        logger.info("[EmbeddingIndex] Model loaded in %.1fs.", time.time() - t0)

    # ------------------------------------------------------------------
    # Encoding
    # ------------------------------------------------------------------

    def _embed_many(self, texts: list[str], *, role: EmbeddingRole) -> list[tuple[float, ...]]:
        if not texts:
            return []
        if not self._ensure_loaded():
            return [() for _ in texts]
        try:
            matrix = self._encode_with_cache(texts, role=role)
            if matrix is None:
                return [() for _ in texts]
            return [tuple(float(x) for x in row) for row in matrix]
        except Exception as exc:
            logger.warning("EmbeddingIndex.%s encode failed: %s", role, exc)
            return [() for _ in texts]

    def _encode(self, texts: list[str], *, role: EmbeddingRole) -> np.ndarray:
        """Encode texts và L2-normalize output."""

        import torch

        prepared = [self._prepare_text(text, role=role) for text in texts]
        batch_size = max(1, int(os.getenv("ERIS_EMBEDDING_BATCH_SIZE", "16")))
        all_vecs: list[np.ndarray] = []

        for i in range(0, len(prepared), batch_size):
            batch = prepared[i : i + batch_size]
            encoded = self._tokenizer(
                batch,
                padding=True,
                truncation=True,
                max_length=self.max_seq_length,
                return_tensors="pt",
            )
            if self._device != "cpu":
                encoded = {key: value.to(self._device) for key, value in encoded.items()}
            with torch.no_grad():
                output = self._model(**encoded)
            token_embeddings = output.last_hidden_state
            attention_mask = encoded["attention_mask"]
            pooled = self._pool(token_embeddings, attention_mask)
            pooled = torch.nn.functional.normalize(pooled, p=2, dim=1)
            all_vecs.append(pooled.detach().cpu().float().numpy())

        return np.vstack(all_vecs)

    def _pool(self, token_embeddings: Any, attention_mask: Any) -> Any:
        strategy = self._resolved_pooling()
        if strategy == "last_token":
            # Hoạt động cho cả padding trái và phải. Với padding trái, token cuối
            # luôn thật; với padding phải, lấy vị trí length-1 của từng dòng.
            left_padding = bool(attention_mask[:, -1].sum().item() == attention_mask.shape[0])
            if left_padding:
                return token_embeddings[:, -1]
            lengths = attention_mask.sum(dim=1) - 1
            batch_indices = lengths.new_tensor(range(token_embeddings.shape[0]))
            return token_embeddings[batch_indices, lengths]

        mask = attention_mask.unsqueeze(-1).to(token_embeddings.dtype)
        summed = (token_embeddings * mask).sum(dim=1)
        counts = mask.sum(dim=1).clamp(min=1e-9)
        return summed / counts

    def _prepare_text(self, text: str, *, role: EmbeddingRole) -> str:
        clean = " ".join(str(text or "").split())
        if role == "query":
            if self.model_family == "qwen3":
                instruction = self.query_instruction or "Retrieve relevant text."
                return f"Instruct: {instruction}\nQuery: {clean}"
            if self.model_family == "e5":
                return f"query: {clean}"
        elif self.model_family == "e5":
            return f"passage: {clean}"
        return clean

    def _encode_with_cache(self, texts: list[str], *, role: EmbeddingRole) -> np.ndarray | None:
        if not texts:
            return None
        keys = [self._cache_key(text, role) for text in texts]
        uncached_indices = [idx for idx, key in enumerate(keys) if key not in self._vector_cache]
        if uncached_indices:
            uncached_texts = [texts[idx] for idx in uncached_indices]
            try:
                new_vecs = self._encode(uncached_texts, role=role)
                for pos, idx in enumerate(uncached_indices):
                    self._vector_cache[keys[idx]] = new_vecs[pos]
            except Exception as exc:
                logger.warning("EmbeddingIndex encode batch failed: %s", exc)
                return None
        try:
            return np.stack([self._vector_cache[key] for key in keys])
        except KeyError:
            return None

    def _cache_key(self, text: str, role: EmbeddingRole) -> str:
        namespace = (
            f"{self.model_name}|{self.model_family}|{self._resolved_pooling()}|"
            f"{self.max_seq_length}|{role}|{self.query_instruction if role == 'query' else ''}"
        )
        return _short_hash(namespace + "\n" + str(text or ""))

    def _resolved_pooling(self) -> str:
        if self.pooling_strategy in {"mean", "last_token"}:
            return self.pooling_strategy
        return "last_token" if self.model_family == "qwen3" else "mean"

    @staticmethod
    def _detect_model_family(model_name: str) -> str:
        name = str(model_name or "").lower()
        if "qwen3" in name and "embedding" in name:
            return "qwen3"
        if "e5" in name:
            return "e5"
        if "bge-m3" in name or "bge_m3" in name:
            # BGE-M3 dense mode dùng CLS trong FlagEmbedding; AutoModel mean pooling
            # không phải adapter đầy đủ. Giữ family riêng để debug không giả vờ.
            return "bge_m3_generic"
        if "minilm" in name:
            return "minilm"
        return "generic"

    @staticmethod
    def _cuda_available() -> bool:
        try:
            import torch

            return bool(torch.cuda.is_available())
        except Exception:
            return False


def build_embedding_index(*, model_name: str | None = None, enabled: bool = True) -> EmbeddingIndex | None:
    """Build index nếu dependency có sẵn; model vẫn lazy-load."""

    env_enabled = os.getenv("ERIS_EMBEDDING_ENABLED", "1").strip().lower()
    if env_enabled in {"0", "false", "no", "off"} or not enabled:
        return None
    try:
        import torch  # noqa: F401
        from transformers import AutoTokenizer  # noqa: F401
    except ImportError:
        logger.warning("EmbeddingIndex: thiếu torch/transformers. Recall dùng lexical only.")
        return None
    return EmbeddingIndex(model_name=model_name)


_SHARED_INDEX: EmbeddingIndex | None = None
_SHARED_BUILT = False


def get_shared_embedding_index() -> EmbeddingIndex | None:
    """Một provider dùng chung toàn tiến trình."""

    global _SHARED_INDEX, _SHARED_BUILT
    if _SHARED_BUILT:
        return _SHARED_INDEX
    _SHARED_BUILT = True
    env_enabled = os.getenv("ERIS_EMBEDDING_ENABLED", "1").strip().lower()
    if env_enabled in {"0", "false", "no", "off"}:
        return None
    try:
        # Chỉ ưu tiên MiniLM local khi user chưa chỉ định model khác. Nếu đã đặt
        # ERIS_EMBEDDING_MODEL=Qwen/Qwen3-Embedding-0.6B thì không được lén load
        # thư mục MiniLM chỉ vì nó nằm gần source.
        explicit_model = os.getenv("ERIS_EMBEDDING_MODEL", "").strip()
        cache_dir = None
        if not explicit_model and _LOCAL_MODEL_DIR.is_dir():
            cache_dir = str(_LOCAL_MODEL_DIR)
        _SHARED_INDEX = EmbeddingIndex(cache_dir=cache_dir)
        _SHARED_INDEX._async_load = True
        _SHARED_INDEX.warmup()
    except Exception:
        _SHARED_INDEX = None
    return _SHARED_INDEX


def reset_shared_embedding_index() -> None:
    global _SHARED_INDEX, _SHARED_BUILT
    _SHARED_INDEX = None
    _SHARED_BUILT = False


__all__ = [
    "EmbeddingIndex",
    "build_embedding_index",
    "get_shared_embedding_index",
    "reset_shared_embedding_index",
]
