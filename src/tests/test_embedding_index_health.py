# -*- coding: utf-8 -*-
"""Task 1: lỗi load embedding không được im lặng, nhưng vẫn fail-soft.

Env test thiếu torch/transformers nên EmbeddingIndex thật luôn rơi vào nhánh
load-fail. Đây chính là trạng thái cần kiểm: recall vẫn chạy (vector rỗng),
debug_status phản ánh đúng, và health signal (logger.error) chỉ phát MỘT LẦN.
"""
from __future__ import annotations

import logging

from memory.embedding_index import EmbeddingIndex


def _fresh_index() -> EmbeddingIndex:
    # Patch _load_model để fail TẤT ĐỊNH, không chạm model registry/network. Nếu
    # để identifier rác đi tới from_pretrained khi có transformers thì vừa phụ
    # thuộc môi trường vừa có thể resolve model từ xa (vi phạm "không tải model").
    index = EmbeddingIndex(model_name="eris-nonexistent-model-xyz", cache_dir="/eris/nonexistent-cache")

    def _boom() -> None:
        raise RuntimeError("deliberate load failure for test")

    index._load_model = _boom  # type: ignore[method-assign]
    return index


def test_load_fail_returns_empty_vector_without_raising():
    index = _fresh_index()
    # KHÔNG raise: trả tuple rỗng, vẫn cho caller chạy lexical.
    assert index.embed_document("xin chào") == ()
    assert index.embed_query("xin chào") == ()
    assert index.embed_documents(["a", "b"]) == [(), ()]


def test_debug_status_reflects_load_failure():
    index = _fresh_index()
    index.embed_document("x")
    status = index.debug_status()
    assert status["loaded"] is False
    assert status["load_error"]  # khác rỗng
    assert status["load_failed_signaled"] is True


def test_embed_documents_cache_hit_cross_turn():
    """Finding 4: document cũ cache-hit, chỉ encode text CHƯA cache.

    Không cần model thật: stub _encode (đếm text thực sự encode) và ép _loaded=True
    để đi qua _encode_with_cache/_vector_cache như production. Chứng minh batch chỉ
    encode phần mới và cache_size tăng đúng theo số text duy nhất.
    """
    import numpy as np

    index = EmbeddingIndex(model_name="eris-stub", cache_dir="/eris/nonexistent-cache")
    encoded_texts: list[str] = []

    def _fake_encode(texts, *, role):
        encoded_texts.extend(texts)
        return np.array([[float(len(t)), 1.0, 0.0] for t in texts], dtype=float)

    index._encode = _fake_encode  # type: ignore[method-assign]
    index._loaded = True  # bỏ qua load model thật

    # Lượt 1: hai document mới -> encode cả hai.
    first = index.embed_documents(["alpha", "beta"])
    assert len(first) == 2
    assert first[0] != () and first[1] != ()
    assert encoded_texts == ["alpha", "beta"]
    assert index.debug_status()["cache_size"] == 2

    # Lượt 2: "alpha" đã cache (cross-turn hit), chỉ "gamma" cần encode.
    second = index.embed_documents(["alpha", "gamma"])
    assert len(second) == 2
    # "alpha" trả cùng vector cache, không encode lại.
    assert second[0] == first[0]
    assert encoded_texts == ["alpha", "beta", "gamma"]  # chỉ thêm "gamma"
    assert index.debug_status()["cache_size"] == 3


def test_health_signal_logged_exactly_once(caplog):
    index = _fresh_index()
    with caplog.at_level(logging.ERROR, logger="memory.embedding_index"):
        index.embed_document("x")
        index.embed_document("y")
        index.embed_documents(["z", "w"])
    load_fail_errors = [
        rec for rec in caplog.records
        if rec.levelno == logging.ERROR and "model load failed" in rec.getMessage()
    ]
    assert len(load_fail_errors) == 1
    assert index.debug_status()["load_failed_signaled"] is True


def test_shared_index_does_not_block_recall_while_model_loads():
    import threading
    import numpy as np

    index = EmbeddingIndex(device="cpu")
    entered = threading.Event()
    release = threading.Event()
    loads = []
    def load():
        loads.append(1)
        entered.set()
        assert release.wait(2)
    index._load_model = load
    index._encode = lambda texts, role: np.array([[1., 0.] for text in texts])
    index._async_load = True
    try:
        index.warmup()
        assert entered.wait(1)
        assert index.embed_query("xin chào") == ()
        assert index.embed_documents(["a", "b"]) == [(), ()]
        assert index._vector_cache == {}  # warming is not cached as a permanent empty result
        assert index.debug_status()["loading"] is True
        release.set()
        index._loader.join(1)
        assert index.embed_query("xin chào") == (1., 0.)
        assert loads == [1]
    finally:
        release.set()
