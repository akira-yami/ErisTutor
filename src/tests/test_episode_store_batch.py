# -*- coding: utf-8 -*-
"""Task 2a: episode_store.search gom candidate và gọi embed_documents MỘT LẦN.

Dùng fake embedding index để không cần model thật. Kiểm:
- embed_documents gọi đúng 1 lần, embed_document KHÔNG được gọi trong vòng lặp;
- fallback khi index chỉ có embed_query/embed_document (không batch) -> lexical,
  không crash, dense=0.0;
- fallback khi embedding_index=None -> giữ nhánh cũ.
"""
from __future__ import annotations

from memory.living_user.episode_store import UserEpisodeStore
from memory.living_user.models import UserEpisode, UserMemoryScope


class _FakeBatchIndex:
    """Trả vector xác định theo nội dung text, đếm số lần gọi."""

    def __init__(self) -> None:
        self.embed_documents_calls = 0
        self.embed_document_calls = 0
        self.last_batch: list[str] | None = None

    def embed_query(self, text):
        # Query nghiêng về trục "python".
        return (1.0, 0.0, 0.0)

    def embed_documents(self, texts):
        self.embed_documents_calls += 1
        self.last_batch = list(texts)
        rows = []
        for text in texts:
            if "python" in text.lower():
                rows.append((1.0, 0.0, 0.0))
            else:
                rows.append((0.0, 1.0, 0.0))
        return rows

    def embed_document(self, text):
        self.embed_document_calls += 1
        return (0.0, 0.0, 1.0)


class _FakeNoBatchIndex:
    """Chỉ có embed_query + embed_document (không có embed_documents callable)."""

    def __init__(self) -> None:
        self.embed_document_calls = 0

    def embed_query(self, text):
        return (1.0, 0.0, 0.0)

    def embed_document(self, text):
        self.embed_document_calls += 1
        return (1.0, 0.0, 0.0)


def _episode(idx: int, title: str, summary: str, topics=()):
    return UserEpisode(
        id=f"uep_{idx}",
        session_id=f"s{idx}",
        title=title,
        summary=summary,
        topics=tuple(topics),
        importance=0.5,
    )


def _store_with_episodes(tmp_path):
    store = UserEpisodeStore(tmp_path / "episodes.json")
    store.add(_episode(1, "Refactor python memory", "Dọn python embedding", topics=("python",)))
    store.add(_episode(2, "Thiết kế deck", "Chuẩn bị slide pitch", topics=("deck",)))
    return store


def test_search_batches_embed_documents_once(tmp_path):
    store = _store_with_episodes(tmp_path)
    index = _FakeBatchIndex()
    results = store.search("python embedding", limit=5, embedding_index=index)
    # Batch được dùng đúng 1 lần cho toàn bộ candidate; không gọi embed_document.
    assert index.embed_documents_calls == 1
    assert index.embed_document_calls == 0
    assert index.last_batch is not None and len(index.last_batch) == 2
    # Episode python phải xếp trước vì dense + lexical đều cao hơn.
    assert results
    assert results[0].title == "Refactor python memory"


def test_search_no_batch_index_falls_back_to_lexical(tmp_path):
    store = _store_with_episodes(tmp_path)
    index = _FakeNoBatchIndex()
    # Không có embed_documents callable -> không batch, không crash, dense=0.0.
    results = store.search("python embedding", limit=5, embedding_index=index)
    assert index.embed_document_calls == 0
    assert results
    assert results[0].title == "Refactor python memory"


def test_search_none_index_keeps_lexical_fallback(tmp_path):
    store = _store_with_episodes(tmp_path)
    results = store.search("python embedding", limit=5, embedding_index=None)
    assert results
    assert results[0].title == "Refactor python memory"


def test_search_batch_length_mismatch_drops_dense(tmp_path):
    store = _store_with_episodes(tmp_path)

    class _BadLenIndex:
        def embed_query(self, text):
            return (1.0, 0.0, 0.0)

        def embed_documents(self, texts):
            return [(1.0, 0.0, 0.0)]  # sai độ dài

    results = store.search("python embedding", limit=5, embedding_index=_BadLenIndex())
    # Không crash, vẫn recall bằng lexical.
    assert results
    assert results[0].title == "Refactor python memory"


def test_search_empty_vector_batch_falls_back_to_lexical(tmp_path):
    """Finding 3: provider callable TRẢ vector rỗng (độ dài khớp) -> dense=0.0.

    Đây là trạng thái embedding load-fail thật (embed_documents -> [(), ()]). Phải
    đi xuyên tới recall consumer: không crash, vẫn recall bằng lexical.
    """
    store = _store_with_episodes(tmp_path)

    class _EmptyVecIndex:
        def __init__(self):
            self.embed_documents_calls = 0

        def embed_query(self, text):
            return (1.0, 0.0, 0.0)

        def embed_documents(self, texts):
            self.embed_documents_calls += 1
            return [() for _ in texts]  # độ dài khớp, vector rỗng

    index = _EmptyVecIndex()
    results = store.search("python embedding", limit=5, embedding_index=index)
    assert index.embed_documents_calls == 1  # batch vẫn chạy một lần
    assert results  # dense=0.0, lexical vẫn chọn được
    assert results[0].title == "Refactor python memory"


def test_search_malformed_row_is_fail_soft(tmp_path):
    """Finding 1: một row hỏng (None) không được raise, coi như dense=0.0."""
    store = _store_with_episodes(tmp_path)

    class _BadRowIndex:
        def embed_query(self, text):
            return (1.0, 0.0, 0.0)

        def embed_documents(self, texts):
            return [None for _ in texts]  # độ dài khớp nhưng row hỏng

    results = store.search("python embedding", limit=5, embedding_index=_BadRowIndex())
    assert results  # không crash, lexical vẫn recall
    assert results[0].title == "Refactor python memory"
