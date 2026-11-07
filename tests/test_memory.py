"""Unit tests for the memory subsystem."""
from __future__ import annotations

import tempfile
from pathlib import Path

import numpy as np

from memory.memory_record import MemoryRecord
from memory.memory_models import MemoryKind, DecayPolicy
from memory.text_normalize_vi import normalize_vi
from memory.predicate_registry import raw_cosine
from memory.embedding_index import _cosine_similarity


def test_normalize_vietnamese() -> None:
    assert normalize_vi("Học máy và Trí tuệ nhân tạo") == "hoc may va tri tue nhan tao"
    assert normalize_vi("Đà Nẵng") == "da nang"


def test_memory_record_creation() -> None:
    rec = MemoryRecord(
        id="mem-1",
        layer="semantic",
        text="Likes dark mode",
        confidence=0.9,
        salience=0.8,
    )
    assert rec.id == "mem-1"
    assert rec.layer == "semantic"
    assert rec.confidence == 0.9
    assert rec.salience == 0.8


def test_raw_cosine_similarity() -> None:
    vec_a = [1.0, 0.0, 0.0]
    vec_b = [1.0, 0.0, 0.0]
    vec_c = [0.0, 1.0, 0.0]
    assert raw_cosine(vec_a, vec_b) == 1.0
    assert raw_cosine(vec_a, vec_c) == 0.0


def test_numpy_cosine_similarity() -> None:
    a = np.array([1.0, 2.0, 3.0])
    b = np.array([[1.0, 2.0, 3.0], [-1.0, -2.0, -3.0]])
    sims = _cosine_similarity(a, b)
    assert len(sims) == 2
    assert abs(sims[0] - 1.0) < 1e-5
    assert abs(sims[1] - (-1.0)) < 1e-5
