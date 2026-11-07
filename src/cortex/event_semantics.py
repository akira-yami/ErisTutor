from __future__ import annotations
from core.di import resolve_dependency

import math
import re
from dataclasses import dataclass
from typing import Callable, Iterable, Sequence


'''
Module này đóng vai trò như một bộ lọc nhận thức.
 Thay vì nhìn vào các log hệ thống thô (như "alt-tab sang vscode"),
 nó cố gắng hiểu ý nghĩa đằng sau chuỗi hành động đó.
'''

@dataclass(frozen=True, slots=True)
class EventSemanticProfile:
    raw: str
    symbolic_tags: tuple[str, ...]
    embedding: tuple[float, ...] = ()
    cluster_id: str = ""
    confidence: float = 0.0


PRIMITIVE_EVENTS = {
    "mouse_move",
    "mouse_moved",
    "click",
    "scroll",
    "key_press",
    "keypress",
    "window_active",
}


def _normalize_event_key(event_text: str) -> str:
    return re.sub(r"\s+", " ", str(event_text or "").strip().lower())


def should_embed_event(event: str) -> bool:
    compact = _normalize_event_key(event)
    if compact in PRIMITIVE_EVENTS:
        return False
    if len(compact) < 18:
        return False
    return True


def _clean_embedding(embedding: Sequence[float] | None) -> tuple[float, ...]:
    if embedding is None:
        return ()
    values: list[float] = []
    for value in embedding:
        try:
            numeric = float(value)
        except (TypeError, ValueError):
            return ()
        if not math.isfinite(numeric):
            return ()
        values.append(numeric)
    return tuple(values)

#lưu lại các vector này để tránh gọi lại model embedding nhiều lần, tối ưu hiệu năng chạy ngầm
class EventEmbeddingCache:
    def __init__(self):
        self._cache: dict[str, tuple[float, ...]] = {}

    # Lấy embedding đã lưu cho một sự kiện, nếu có. Trả về None nếu không tìm thấy hoặc nếu embedding không hợp lệ.
    def get(self, event_text: str) -> tuple[float, ...] | None:
        key = _normalize_event_key(event_text)
        return self._cache.get(key)

    # Lưu embedding cho một sự kiện. Nếu embedding không hợp lệ (chứa giá trị không phải số hoặc không hữu hạn), nó sẽ không được lưu.
    def put(self, event_text: str, embedding) -> None:
        vector = _clean_embedding(embedding)
        if not vector:
            return
        key = _normalize_event_key(event_text)
        if key:
            self._cache[key] = vector


@dataclass(slots=True)
class _EventCluster:
    cluster_id: str
    centroid: tuple[float, ...]
    count: int = 1

#EventClusterIndex là một hệ thống phân cụm in-memory siêu nhẹ.
# Nó dùng Cosine Similarity (ngưỡng mặc định 0.82) để gom các hành vi có vector tương đồng
#  vào chung một nhóm (cluster_id). Việc này giúp model nhận ra các mẫu thói quen lặp đi lặp lại.
class EventClusterIndex:
    """Tiny in-memory semantic clusterer for enriched event patterns."""

    def __init__(self, *, similarity_threshold: float = 0.82, max_clusters: int = 64):
        self.similarity_threshold = max(0.0, min(1.0, float(similarity_threshold)))
        self.max_clusters = max(1, int(max_clusters))
        self._clusters: list[_EventCluster] = []

    # Cập nhật cluster index với embedding mới. Nếu embedding tương đồng với cluster hiện có (dựa trên cosine similarity), nó sẽ cập nhật centroid của cluster đó. Nếu không, nó sẽ tạo một cluster mới (nếu chưa đạt max_clusters). Trả về cluster_id và confidence của sự phù hợp.
    def update(self, *, embedding: Sequence[float], symbolic_tags: tuple[str, ...]) -> tuple[str, float]:
        vector = _normalize_vector(_clean_embedding(embedding))
        if not vector:
            return "", 0.0

        best_index = -1
        best_similarity = -1.0
        for index, cluster in enumerate(self._clusters):
            similarity = _cosine_similarity(vector, cluster.centroid)
            if similarity > best_similarity:
                best_index = index
                best_similarity = similarity

        if best_index >= 0 and best_similarity >= self.similarity_threshold:
            cluster = self._clusters[best_index]
            updated = _normalize_vector(
                tuple(
                    ((value * cluster.count) + incoming) / (cluster.count + 1)
                    for value, incoming in zip(cluster.centroid, vector)
                )
            )
            cluster.centroid = updated or cluster.centroid
            cluster.count += 1
            return cluster.cluster_id, max(0.0, min(1.0, best_similarity))

        if len(self._clusters) >= self.max_clusters:
            return "", 0.0

        label = _cluster_label(symbolic_tags)
        cluster_id = f"event_cluster_{label}_{len(self._clusters) + 1:02d}"
        self._clusters.append(_EventCluster(cluster_id=cluster_id, centroid=vector))
        return cluster_id, 0.55


_UNSET = object()


def _default_shared_provider():
    """Provider embedding mặc định: EmbeddingIndex singleton dùng chung.

    Soft-import để event_semantics không phụ thuộc cứng vào memory layer. Trả None
    nếu không lấy được (embedding tắt / thiếu dep) — profiler tự fallback symbolic.
    """

    try:
        from memory.embedding_index import get_shared_embedding_index

        return get_shared_embedding_index()
    except Exception:
        return None


class EventSemanticProfiler:
    def __init__(
        self,
        *,
        embedding_cache: EventEmbeddingCache | None = None,
        embedding_provider: Callable[[str], Sequence[float]] | object | None = _UNSET,
        cluster_index: EventClusterIndex | None = None,
    ):
        self.embedding_cache = resolve_dependency(embedding_cache, lambda: EventEmbeddingCache())
        # Không truyền provider -> dùng EmbeddingIndex singleton dùng chung.
        # Truyền tường minh None -> cố ý không dùng provider nào (giữ tương thích cũ).
        if embedding_provider is _UNSET:
            embedding_provider = _default_shared_provider()
        self.embedding_provider = embedding_provider
        self.cluster_index = resolve_dependency(cluster_index, lambda: EventClusterIndex())

    def profile_events(
        self,
        events: Iterable[str],
        *,
        allow_embedding: bool = False,
        use_cached_embedding: bool = True,
    ) -> tuple[EventSemanticProfile, ...]:
        raw_events = tuple(str(item or "").strip() for item in events if str(item or "").strip())
        profiles = [
            EventSemanticProfile(raw=event, symbolic_tags=symbolic_tags_for_event(event))
            for event in raw_events
        ]
        embeddings = self._resolve_embeddings(
            raw_events,
            allow_embedding=allow_embedding,
            use_cached_embedding=use_cached_embedding,
        )

        enriched: list[EventSemanticProfile] = []
        for profile, embedding in zip(profiles, embeddings):
            cluster_id = ""
            confidence = 0.0
            if embedding:
                cluster_id, confidence = self.cluster_index.update(
                    embedding=embedding,
                    symbolic_tags=profile.symbolic_tags,
                )
            enriched.append(
                EventSemanticProfile(
                    raw=profile.raw,
                    symbolic_tags=profile.symbolic_tags,
                    embedding=embedding,
                    cluster_id=cluster_id,
                    confidence=confidence,
                )
            )
        return tuple(enriched)

    def profile_event(
        self,
        event: str,
        *,
        allow_embedding: bool = False,
        use_cached_embedding: bool = True,
    ) -> EventSemanticProfile:
        profiles = self.profile_events(
            (event,),
            allow_embedding=allow_embedding,
            use_cached_embedding=use_cached_embedding,
        )
        return profiles[0] if profiles else EventSemanticProfile(raw="", symbolic_tags=())

    def _resolve_embeddings(
        self,
        events: tuple[str, ...],
        *,
        allow_embedding: bool,
        use_cached_embedding: bool,
    ) -> tuple[tuple[float, ...], ...]:
        embeddings: list[tuple[float, ...]] = []
        missing: list[tuple[int, str]] = []
        for index, event in enumerate(events):
            if not should_embed_event(event):
                embeddings.append(())
                continue
            cached = self.embedding_cache.get(event) if use_cached_embedding else None
            if cached:
                embeddings.append(cached)
                continue
            embeddings.append(())
            if allow_embedding:
                missing.append((index, event))

        if not missing or not self.embedding_provider:
            return tuple(embeddings)

        for index, event, embedding in self._embed_missing(missing):
            vector = _clean_embedding(embedding)
            if not vector:
                continue
            self.embedding_cache.put(event, vector)
            embeddings[index] = vector
        return tuple(embeddings)

    def _embed_missing(
        self,
        missing: list[tuple[int, str]],
    ) -> tuple[tuple[int, str, Sequence[float]], ...]:
        provider = self.embedding_provider
        texts = [event for _, event in missing]
        try:
            embed_events = getattr(provider, "embed_events", None)
            if callable(embed_events):
                vectors = embed_events(texts)
                if vectors is None:
                    return ()
                return tuple(
                    (index, event, vector)
                    for (index, event), vector in zip(missing, vectors)
                )
        except Exception:
            return ()

        results: list[tuple[int, str, Sequence[float]]] = []
        for index, event in missing:
            try:
                embed = getattr(provider, "embed", None)
                vector = embed(event) if callable(embed) else provider(event)  # type: ignore[misc]
            except Exception:
                continue
            results.append((index, event, vector))
        return tuple(results)


def symbolic_tags_for_event(event: str) -> tuple[str, ...]:
    text = _normalize_event_key(event).replace("-", "_")
    words = set(re.findall(r"[a-z0-9_+#]+", text))
    tags: list[str] = []

    def add(tag: str, condition: bool) -> None:
        if condition:
            tags.append(tag)

    # Các tag này là những tín hiệu ngữ nghĩa sơ cấp được trích xuất từ mô tả sự kiện thô, giúp phân loại và hiểu ý nghĩa của sự kiện đó.
    add("mouse", bool(words & {"mouse", "mouse_move", "mouse_moved", "cursor"}))
    add("click", "click" in words or "clicked" in words)
    add("scroll", "scroll" in words or "scrolled" in words)
    add("keyboard", bool(words & {"key", "keys", "key_press", "keypress", "typing", "typed", "input"}))
    add("selection", bool(words & {"select", "selected", "selection", "highlight", "highlighted"}))
    add("hover", "hover" in words or "hovered" in words)
    add("editing", bool(words & {"edit", "editing", "edited", "file_saved", "saved", "rename", "delete", "backspace"}))
    add("coding", _has_any(text, ("code", "coding", "vscode", "visual studio", "godot", "python", "c#", "cs file", ".cs", "build")))
    add("window_active", text.endswith("window_active") or "window_active" in words)
    add("window_switch", _has_any(text, ("alt_tab", "alt-tab", "window switch", "switching", "between")))
    add("restless", _has_any(text, ("rapid", "repeated", "again", "back and forth", "alt_tab", "alt-tab")))
    add("build_error", _has_any(text, ("error", "failed build", "build failure", "traceback", "exception", "compile failed")))
    add("stall", _has_any(text, ("same file", "same code", "staring", "stuck", "waiting", "12 minutes", "long pause")))
    add("distraction", _has_any(text, ("youtube", "entertainment", "doomscroll", "social feed")))
    add("communication", _has_any(text, ("chat", "message", "discord", "slack", "zalo", "meet")))

    return tuple(dict.fromkeys(tags)) # Loại bỏ trùng lặp trong khi giữ nguyên thứ tự


def _has_any(text: str, needles: tuple[str, ...]) -> bool:
    return any(needle in text for needle in needles)


def _cluster_label(symbolic_tags: tuple[str, ...]) -> str:
    for tag in symbolic_tags:
        if tag in {"build_error", "coding", "editing", "window_switch", "distraction", "communication", "stall"}:
            return re.sub(r"[^a-z0-9_]+", "", tag) or "semantic"
    return "semantic"


def _normalize_vector(vector: tuple[float, ...]) -> tuple[float, ...]:
    magnitude = math.sqrt(sum(value * value for value in vector))
    if magnitude <= 0.0:
        return ()
    return tuple(value / magnitude for value in vector)


def _cosine_similarity(left: tuple[float, ...], right: tuple[float, ...]) -> float:
    if not left or not right or len(left) != len(right):
        return -1.0
    return sum(a * b for a, b in zip(left, right))
