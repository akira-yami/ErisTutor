from __future__ import annotations

"""
semantic_encoder.py
===================

Lớp mã hóa ngữ nghĩa chung cho ERIS.

Mục tiêu
--------
File này cố ý chỉ định nghĩa *hợp đồng* và vài implementation an toàn.
Nó không ép project phải dùng một embedding model cụ thể. Làm vậy để sau này
bạn có thể đổi local model, remote API, FAISS, sqlite-vec, hoặc một thứ mới
mà loài người lại đặt tên thật kêu, mà không phải sửa toàn bộ cortex.

Điểm quan trọng
---------------
- Embedding là tín hiệu phụ trợ, không phải chân lý.
- Encoder không được tự quyết định intent, drift, memory hay intervention.
- Encoder chỉ trả vector + metadata + confidence kỹ thuật.
- Nếu không có embedding thật, fallback hashing chỉ dùng cho test/dev, không
  được tự nhận là semantic thật. Vâng, một vector giả vờ hiểu đời thì vẫn là
  túi token được trang điểm.

Cách dùng khuyến nghị
---------------------
    encoder = CachedSemanticEncoder(
        ProviderBackedSemanticEncoder(provider=my_embedding_func)
    )
    vector = encoder.encode("đang xem video hướng dẫn Godot")

Trong đó `my_embedding_func` nhận list[str] và trả list[list[float]].
"""

import hashlib
import math
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Callable, Iterable, Mapping, Protocol, Sequence


# ---------------------------------------------------------------------------
# Helper số học vector
# ---------------------------------------------------------------------------


def clamp01(value: object) -> float:
    """Ép một giá trị về [0, 1].

    Hàm này được copy nhẹ ở đây để file có thể đứng độc lập. Không import
    ngược sang `orientation_context.py`, tránh vòng import kỳ dị kiểu repo tự
    cắn đuôi mình vì muốn "tái sử dụng code" cho có đạo đức.
    """

    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    if number != number:  # NaN
        return 0.0
    return max(0.0, min(1.0, number))


def l2_norm(vector: Sequence[float]) -> float:
    """Tính độ dài L2 của vector."""

    return math.sqrt(sum(float(x) * float(x) for x in vector))


def normalize_vector(vector: Sequence[float]) -> tuple[float, ...]:
    """Chuẩn hóa vector về độ dài 1.

    Nếu vector rỗng hoặc toàn 0 thì trả tuple rỗng để downstream biết tín hiệu
    này không dùng được. Đừng normalize vector 0 thành thần tri thức, cảm ơn.
    """

    if not vector:
        return ()
    norm = l2_norm(vector)
    if norm <= 1e-12:
        return ()
    return tuple(float(x) / norm for x in vector)


def cosine_similarity(left: Sequence[float], right: Sequence[float]) -> float:
    """Tính cosine similarity an toàn.

    Hàm chịu được hai vector khác chiều bằng cách chỉ so phần chiều chung.
    Trong production tốt nhất không để khác chiều, nhưng hệ thống đang phát
    triển mà, nhân loại gọi đó là "linh hoạt", debugger gọi đó là lý do mất ngủ.
    """

    if not left or not right:
        return 0.0
    size = min(len(left), len(right))
    if size <= 0:
        return 0.0
    a = left[:size]
    b = right[:size]
    denom = l2_norm(a) * l2_norm(b)
    if denom <= 1e-12:
        return 0.0
    return max(-1.0, min(1.0, sum(float(x) * float(y) for x, y in zip(a, b)) / denom))


def mean_vectors(vectors: Iterable[Sequence[float]]) -> tuple[float, ...]:
    """Tính centroid đã normalize từ nhiều vector.

    Centroid dùng cho prototype bank: nhiều câu mẫu cùng mô tả một intent sẽ
    được gộp thành một vector đại diện.
    """

    usable = [tuple(float(x) for x in vector) for vector in vectors if vector]
    if not usable:
        return ()
    width = min(len(vector) for vector in usable)
    if width <= 0:
        return ()
    sums = [0.0] * width
    for vector in usable:
        for i in range(width):
            sums[i] += vector[i]
    return normalize_vector(tuple(value / len(usable) for value in sums))


def stable_hash_vector(text: str, *, dimensions: int = 384, salt: str = "eris-semantic-fallback") -> tuple[float, ...]:
    """Tạo vector hashing ổn định từ text.

    Đây KHÔNG phải semantic embedding thật. Nó chỉ là fallback deterministic
    để unit test, dry-run, hoặc chạy khi chưa cắm model. Nó bắt overlap/token
    tốt hơn random một chút, còn "hiểu nghĩa" thì không. Đừng để fallback này
    lái Ni như một nhà tiên tri nghèo dữ liệu.
    """

    dimensions = max(16, int(dimensions or 384))
    vector = [0.0] * dimensions
    normalized = " ".join(str(text or "").lower().split())
    if not normalized:
        return ()

    # Dùng cả word unigram và character trigram để câu ngắn vẫn có dấu vết.
    words = normalized.split()
    features: list[str] = []
    features.extend(words)
    padded = f"  {normalized}  "
    features.extend(padded[i : i + 3] for i in range(max(0, len(padded) - 2)))

    for feature in features:
        digest = hashlib.blake2b(f"{salt}:{feature}".encode("utf-8"), digest_size=8).digest()
        raw = int.from_bytes(digest, "big", signed=False)
        index = raw % dimensions
        sign = 1.0 if ((raw >> 8) & 1) else -1.0
        # Word feature quan trọng hơn char feature một chút.
        weight = 1.0 if " " not in feature and len(feature) > 2 else 0.35
        vector[index] += sign * weight

    return normalize_vector(vector)


# ---------------------------------------------------------------------------
# Dataclass / protocol nền
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SemanticVector:
    """Kết quả encode một đoạn text.

    Fields
    ------
    text:
        Text gốc hoặc text đã được caller chọn để encode.
    vector:
        Vector đã normalize. Rỗng nghĩa là không dùng được.
    model_id:
        Tên model/provider. Dùng cho debug và cache invalidation.
    confidence:
        Độ tin kỹ thuật của encoder, không phải độ đúng về intent.
    is_fallback:
        True nếu vector đến từ hashing/dev fallback.
    created_at:
        Timestamp tạo vector, để cache/debug biết nó mới hay cũ.
    metadata:
        Thông tin phụ, ví dụ provider, dimensions, latency.
    """

    text: str
    vector: tuple[float, ...]
    model_id: str
    confidence: float = 0.0
    is_fallback: bool = False
    created_at: float = field(default_factory=time.time)
    metadata: Mapping[str, object] = field(default_factory=dict)

    @property
    def usable(self) -> bool:
        return bool(self.vector) and self.confidence > 0.0

    @property
    def dimensions(self) -> int:
        return len(self.vector)


@dataclass(frozen=True, slots=True)
class EncoderHealth:
    """Trạng thái hoạt động của encoder.

    `available=False` không phải lỗi chết. Nó chỉ báo downstream phải hạ về
    lexical fallback và cap confidence. Một hệ thống biết mình không có não
    semantic hôm nay vẫn đáng tin hơn hệ thống đoán bừa thật tự tin.
    """

    available: bool
    model_id: str
    is_fallback: bool
    reason: str = ""
    dimensions: int = 0
    last_error: str = ""


class EmbeddingProvider(Protocol):
    """Protocol cho provider embedding thật.

    Một provider hợp lệ nhận list text và trả list vector cùng thứ tự.
    Có thể là local model, remote API, hoặc wrapper quanh llama.cpp embedding.
    """

    def __call__(self, texts: Sequence[str]) -> Sequence[Sequence[float]]:
        ...


class BaseSemanticEncoder:
    """Base class tối thiểu cho semantic encoder."""

    model_id: str = "base"
    is_fallback: bool = False

    def health(self) -> EncoderHealth:
        return EncoderHealth(
            available=True,
            model_id=self.model_id,
            is_fallback=self.is_fallback,
            reason="encoder_ready",
        )

    def encode(self, text: str) -> SemanticVector:
        vectors = self.encode_many([text])
        return vectors[0] if vectors else SemanticVector(str(text or ""), (), self.model_id)

    def encode_many(self, texts: Sequence[str]) -> list[SemanticVector]:
        raise NotImplementedError


# ---------------------------------------------------------------------------
# Implementations
# ---------------------------------------------------------------------------


class NullSemanticEncoder(BaseSemanticEncoder):
    """Encoder rỗng.

    Dùng khi project chưa cấu hình embedding. Downstream vẫn chạy được, nhưng
    phải hiểu là chỉ còn lexical heuristic. Khá buồn, nhưng ít nhất nó thành
    thật với sự bất lực của mình.
    """

    model_id = "null"
    is_fallback = True

    def health(self) -> EncoderHealth:
        return EncoderHealth(
            available=False,
            model_id=self.model_id,
            is_fallback=True,
            reason="semantic_encoder_not_configured",
        )

    def encode_many(self, texts: Sequence[str]) -> list[SemanticVector]:
        now = time.time()
        return [
            SemanticVector(
                text=str(text or ""),
                vector=(),
                model_id=self.model_id,
                confidence=0.0,
                is_fallback=True,
                created_at=now,
                metadata={"reason": "null_encoder"},
            )
            for text in texts
        ]


class LexicalHashingSemanticEncoder(BaseSemanticEncoder):
    """Fallback deterministic dựa trên hashing token/char.

    Tên có chữ `Semantic` vì nó thỏa interface, không phải vì nó thật sự hiểu
    nghĩa. Trong debug report luôn đánh dấu `is_fallback=True` để các tầng như
    Ni/GoalPersistence tự cap confidence.
    """

    def __init__(self, *, dimensions: int = 384, model_id: str = "lexical_hashing_v1") -> None:
        self.dimensions = max(16, int(dimensions or 384))
        self.model_id = model_id
        self.is_fallback = True

    def health(self) -> EncoderHealth:
        return EncoderHealth(
            available=True,
            model_id=self.model_id,
            is_fallback=True,
            reason="lexical_hashing_fallback_only",
            dimensions=self.dimensions,
        )

    def encode_many(self, texts: Sequence[str]) -> list[SemanticVector]:
        now = time.time()
        out: list[SemanticVector] = []
        for text in texts:
            raw = str(text or "")
            vector = stable_hash_vector(raw, dimensions=self.dimensions)
            out.append(
                SemanticVector(
                    text=raw,
                    vector=vector,
                    model_id=self.model_id,
                    confidence=0.42 if vector else 0.0,
                    is_fallback=True,
                    created_at=now,
                    metadata={"dimensions": self.dimensions, "warning": "not_true_semantic_embedding"},
                )
            )
        return out


class ProviderBackedSemanticEncoder(BaseSemanticEncoder):
    """Encoder bọc một embedding provider thật.

    Provider có thể là:
    - hàm gọi local sentence-transformer
    - hàm gọi llama.cpp embedding endpoint
    - hàm gọi remote API
    - bất cứ thứ gì trả vector số

    File này không tự import thư viện embedding để giữ lõi cortex sạch. Một
    module runtime khác có thể inject provider vào đây.
    """

    def __init__(
        self,
        provider: EmbeddingProvider,
        *,
        model_id: str = "provider_embedding",
        confidence: float = 0.82,
        normalize: bool = True,
    ) -> None:
        self.provider = provider
        self.model_id = model_id
        self.provider_confidence = clamp01(confidence)
        self.should_normalize = bool(normalize)
        self.is_fallback = False
        self._last_error = ""
        self._dimensions = 0

    def health(self) -> EncoderHealth:
        return EncoderHealth(
            available=not bool(self._last_error),
            model_id=self.model_id,
            is_fallback=False,
            reason="provider_ready" if not self._last_error else "provider_last_error",
            dimensions=self._dimensions,
            last_error=self._last_error,
        )

    def encode_many(self, texts: Sequence[str]) -> list[SemanticVector]:
        raw_texts = [str(text or "") for text in texts]
        now = time.time()
        started = time.perf_counter()
        try:
            raw_vectors = self.provider(raw_texts)
            self._last_error = ""
        except Exception as exc:  # pragma: no cover - provider là dependency ngoài
            self._last_error = f"{type(exc).__name__}: {exc}"
            return [
                SemanticVector(
                    text=text,
                    vector=(),
                    model_id=self.model_id,
                    confidence=0.0,
                    is_fallback=False,
                    created_at=now,
                    metadata={"error": self._last_error},
                )
                for text in raw_texts
            ]

        latency_ms = (time.perf_counter() - started) * 1000.0
        out: list[SemanticVector] = []
        for index, text in enumerate(raw_texts):
            raw_vector = raw_vectors[index] if index < len(raw_vectors) else ()
            vector = normalize_vector(raw_vector) if self.should_normalize else tuple(float(x) for x in raw_vector)
            if vector:
                self._dimensions = len(vector)
            out.append(
                SemanticVector(
                    text=text,
                    vector=vector,
                    model_id=self.model_id,
                    confidence=self.provider_confidence if vector else 0.0,
                    is_fallback=False,
                    created_at=now,
                    metadata={"latency_ms": round(latency_ms, 3), "batch_size": len(raw_texts)},
                )
            )
        return out


class CachedSemanticEncoder(BaseSemanticEncoder):
    """Cache LRU mỏng cho encoder.

    Embedding prototype và context thường lặp lại nhiều. Cache giúp giảm chi phí
    gọi model/API. Cache key gồm model_id + text nên khi đổi model sẽ không dùng
    nhầm vector cũ, một phép lịch sự tối thiểu với thực tại.
    """

    def __init__(self, inner: BaseSemanticEncoder, *, max_items: int = 4096) -> None:
        self.inner = inner
        self.max_items = max(32, int(max_items or 4096))
        self.model_id = f"cached:{inner.model_id}"
        self.is_fallback = inner.is_fallback
        self._cache: OrderedDict[tuple[str, str], SemanticVector] = OrderedDict()

    def health(self) -> EncoderHealth:
        health = self.inner.health()
        return EncoderHealth(
            available=health.available,
            model_id=self.model_id,
            is_fallback=health.is_fallback,
            reason=f"cached:{health.reason}",
            dimensions=health.dimensions,
            last_error=health.last_error,
        )

    def clear(self) -> None:
        self._cache.clear()

    def encode_many(self, texts: Sequence[str]) -> list[SemanticVector]:
        raw_texts = [str(text or "") for text in texts]
        out: list[SemanticVector | None] = [None] * len(raw_texts)
        missing_indices: list[int] = []
        missing_texts: list[str] = []

        for index, text in enumerate(raw_texts):
            key = (self.inner.model_id, text)
            cached = self._cache.get(key)
            if cached is not None:
                self._cache.move_to_end(key)
                out[index] = cached
            else:
                missing_indices.append(index)
                missing_texts.append(text)

        if missing_texts:
            encoded = self.inner.encode_many(missing_texts)
            for original_index, vector in zip(missing_indices, encoded):
                key = (self.inner.model_id, raw_texts[original_index])
                self._cache[key] = vector
                self._cache.move_to_end(key)
                out[original_index] = vector

            while len(self._cache) > self.max_items:
                self._cache.popitem(last=False)

        # `out` đã được lấp đủ; fallback dưới chỉ phòng thủ cho provider lỗi shape.
        return [item if item is not None else SemanticVector(raw_texts[i], (), self.inner.model_id) for i, item in enumerate(out)]


class SemanticEncoderRouter(BaseSemanticEncoder):
    """Chọn encoder thật nếu có, fallback nếu không.

    Router giúp runtime cấu hình mềm:
    - Có provider thật: dùng provider.
    - Provider lỗi hoặc chưa bật: dùng Null hoặc hashing fallback tùy cấu hình.

    Lưu ý: nếu fallback hashing được bật, downstream vẫn phải biết `is_fallback`.
    Fallback này chỉ giúp hệ thống không chết, không giúp hệ thống thông minh.
    """

    def __init__(
        self,
        primary: BaseSemanticEncoder | None = None,
        *,
        fallback: BaseSemanticEncoder | None = None,
        allow_hashing_fallback: bool = False,
    ) -> None:
        self.primary = primary
        self.fallback = fallback or (LexicalHashingSemanticEncoder() if allow_hashing_fallback else NullSemanticEncoder())
        self.model_id = f"router:{(primary.model_id if primary else 'none')}:{self.fallback.model_id}"
        self.is_fallback = primary is None

    def health(self) -> EncoderHealth:
        if self.primary is None:
            return self.fallback.health()
        primary_health = self.primary.health()
        if primary_health.available:
            return primary_health
        fallback_health = self.fallback.health()
        return EncoderHealth(
            available=fallback_health.available,
            model_id=self.model_id,
            is_fallback=True,
            reason=f"primary_unavailable:{primary_health.reason};fallback:{fallback_health.reason}",
            dimensions=fallback_health.dimensions,
            last_error=primary_health.last_error,
        )

    def _active(self) -> BaseSemanticEncoder:
        if self.primary is not None and self.primary.health().available:
            return self.primary
        return self.fallback

    def encode_many(self, texts: Sequence[str]) -> list[SemanticVector]:
        return self._active().encode_many(texts)


# ---------------------------------------------------------------------------
# Factory tiện dụng
# ---------------------------------------------------------------------------


def build_default_semantic_encoder(
    provider: EmbeddingProvider | None = None,
    *,
    model_id: str = "provider_embedding",
    cache_items: int = 4096,
    allow_hashing_fallback: bool = False,
) -> BaseSemanticEncoder:
    """Tạo encoder mặc định cho runtime.

    Nếu truyền provider, encoder sẽ bọc provider và cache. Nếu không, router sẽ
    trả Null hoặc hashing fallback tùy `allow_hashing_fallback`.
    """

    primary: BaseSemanticEncoder | None = None
    if provider is not None:
        primary = CachedSemanticEncoder(ProviderBackedSemanticEncoder(provider, model_id=model_id), max_items=cache_items)
    return SemanticEncoderRouter(primary, allow_hashing_fallback=allow_hashing_fallback)
