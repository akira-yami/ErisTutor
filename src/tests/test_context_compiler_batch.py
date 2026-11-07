# -*- coding: utf-8 -*-
"""Task 2b: context_compiler.compile gom document và gọi embed_documents MỘT LẦN.

Fake embedding index để không cần model thật. Kiểm:
- embed_documents gọi đúng 1 lần cho toàn bộ profile candidate (không embed_document
  từng cái trong vòng scoring);
- thứ tự item chọn hợp lý theo dense/lexical;
- fallback khi index None / không có embed_documents / sai độ dài -> dense=0.0,
  không crash, điểm dồn về trust/domain/lexical/recency.
"""
from __future__ import annotations

from memory.living_user.context_compiler import ProfileContextCompiler
from memory.living_user.episode_store import UserEpisodeStore
from memory.living_user.models import ProfileOperation, UserMemoryScope
from memory.living_user.store import UserProfileStore


def _op(path, value, evidence, **kwargs):
    return ProfileOperation(
        op=kwargs.pop("op", "reinforce"),
        path=path,
        value=value,
        confidence=kwargs.pop("confidence", 0.9),
        explicitness=kwargs.pop("explicitness", "explicit"),
        evidence_ids=(evidence,),
        source=kwargs.pop("source", "test"),
        reason="test",
        **kwargs,
    )


class _FakeBatchIndex:
    def __init__(self) -> None:
        self.embed_documents_calls = 0
        self.embed_document_calls = 0
        self.last_batch: list[str] | None = None

    def embed_query(self, text):
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


def _compiler(tmp_path, index):
    profile_store = UserProfileStore(tmp_path / "profile.json")
    profile_store.apply((
        _op("technical.language", "python", "turn_1"),
        _op("communication.tone", "ngắn gọn", "turn_2"),
    ))
    episode_store = UserEpisodeStore(tmp_path / "episodes.json")
    return ProfileContextCompiler(profile_store, episode_store, embedding_index=index)


def test_compile_batches_embed_documents_once(tmp_path):
    index = _FakeBatchIndex()
    compiler = _compiler(tmp_path, index)
    packet = compiler.compile("python embedding")
    # embed_documents gọi đúng 1 lần cho toàn bộ candidate; không embed_document lẻ.
    assert index.embed_documents_calls == 1
    assert index.embed_document_calls == 0
    assert index.last_batch is not None and len(index.last_batch) == 2
    assert packet.items
    # Record python phải xếp trước nhờ dense + lexical.
    assert packet.items[0].path == "technical.language"


def test_compile_none_index_no_crash(tmp_path):
    compiler = _compiler(tmp_path, None)
    packet = compiler.compile("python embedding")
    assert packet.items  # vẫn chọn được theo trust/domain/lexical


def test_compile_bad_length_index_drops_dense(tmp_path):
    class _BadLenIndex:
        def embed_query(self, text):
            return (1.0, 0.0, 0.0)

        def embed_documents(self, texts):
            return [(1.0, 0.0, 0.0)]  # sai độ dài

    compiler = _compiler(tmp_path, _BadLenIndex())
    packet = compiler.compile("python embedding")
    assert packet.items  # không crash, dense=0.0


def test_compile_empty_vector_batch_falls_back_to_lexical(tmp_path):
    """Finding 3: provider callable TRẢ vector rỗng (độ dài khớp) -> dense=0.0.

    Mô phỏng embedding load-fail thật (embed_documents -> [(), ...]). Recall consumer
    vẫn chạy: không crash, điểm dồn về trust/domain/lexical/recency.
    """
    class _EmptyVecIndex:
        def __init__(self):
            self.embed_documents_calls = 0

        def embed_query(self, text):
            return (1.0, 0.0, 0.0)

        def embed_documents(self, texts):
            self.embed_documents_calls += 1
            return [() for _ in texts]  # độ dài khớp, vector rỗng

    index = _EmptyVecIndex()
    compiler = _compiler(tmp_path, index)
    packet = compiler.compile("python embedding")
    assert index.embed_documents_calls == 1
    assert packet.items  # dense=0.0, vẫn chọn được


def test_compile_malformed_row_is_fail_soft(tmp_path):
    """Finding 1: một row hỏng (None) không được raise, coi như dense=0.0."""
    class _BadRowIndex:
        def embed_query(self, text):
            return (1.0, 0.0, 0.0)

        def embed_documents(self, texts):
            return [None for _ in texts]  # độ dài khớp nhưng row hỏng

    compiler = _compiler(tmp_path, _BadRowIndex())
    packet = compiler.compile("python embedding")
    assert packet.items  # không crash


def test_compile_no_batch_index_uses_zero_dense(tmp_path):
    class _NoBatchIndex:
        def __init__(self):
            self.embed_document_calls = 0

        def embed_query(self, text):
            return (1.0, 0.0, 0.0)

        def embed_document(self, text):
            self.embed_document_calls += 1
            return (1.0, 0.0, 0.0)

    index = _NoBatchIndex()
    compiler = _compiler(tmp_path, index)
    packet = compiler.compile("python embedding")
    # Không có embed_documents callable -> không batch, dense=0.0, không crash.
    assert index.embed_document_calls == 0
    assert packet.items
