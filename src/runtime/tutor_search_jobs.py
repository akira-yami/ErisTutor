# -*- coding: utf-8 -*-
"""Research/search job identity helpers.

Thread nền Tutor chạy chậm hơn chat chính. Nếu user đổi chủ đề trong lúc Tutor
đang lục web, kết quả cũ không được nhảy ra như một con zombie lịch sự. Module
này chỉ tạo job_id/topic_hash và check stale deterministic, không dựng queue to đùng.
"""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass
from typing import Literal

try:  # pragma: no cover
    from runtime.tutor_research_policy import normalize_marker_text
except Exception:  # pragma: no cover
    def normalize_marker_text(text: object) -> str:  # type: ignore
        return " ".join(str(text or "").lower().split())

TutorJobMode = Literal["quick_search", "research"]


@dataclass(frozen=True, slots=True)
class TutorSearchJob:
    job_id: str
    topic: str
    topic_hash: str
    mode: TutorJobMode
    user_turn_id: str = ""
    created_ts: float = 0.0

    def to_dict(self) -> dict:
        return {
            "job_id": self.job_id,
            "topic_hash": self.topic_hash,
            "mode": self.mode,
            "user_turn_id": self.user_turn_id,
            "created_ts": self.created_ts,
            "topic_preview": self.topic[:160],
        }


def hash_topic(text: object) -> str:
    normalized = normalize_marker_text(text)
    if not normalized:
        normalized = str(text or "").strip().lower()
    return hashlib.sha1(normalized.encode("utf-8", errors="ignore")).hexdigest()[:16]


def make_tutor_search_job(
    topic: object,
    *,
    mode: str = "research",
    seq: int = 0,
    user_turn_id: object = "",
    now: float | None = None,
) -> TutorSearchJob:
    topic_text = str(topic or "").strip()
    clean_mode: TutorJobMode = "quick_search" if str(mode or "").strip() == "quick_search" else "research"
    created = float(time.time() if now is None else now)
    topic_hash = hash_topic(topic_text)
    turn = str(user_turn_id or "").strip()
    seed = f"{int(created * 1000)}:{seq}:{clean_mode}:{topic_hash}:{turn}"
    job_id = hashlib.sha1(seed.encode("utf-8", errors="ignore")).hexdigest()[:18]
    return TutorSearchJob(
        job_id=job_id,
        topic=topic_text,
        topic_hash=topic_hash,
        mode=clean_mode,
        user_turn_id=turn,
        created_ts=created,
    )


def looks_same_topic(a: object, b: object) -> bool:
    """Loose topic comparison to decide whether an in-flight result went stale."""

    na = normalize_marker_text(a)
    nb = normalize_marker_text(b)
    if not na or not nb:
        return False
    if na == nb or na in nb or nb in na:
        return True
    ta = {tok for tok in na.split() if len(tok) >= 4}
    tb = {tok for tok in nb.split() if len(tok) >= 4}
    if not ta or not tb:
        return False
    overlap = len(ta & tb)
    return overlap >= 2 and overlap / max(1, min(len(ta), len(tb))) >= 0.45


__all__ = ["TutorSearchJob", "hash_topic", "make_tutor_search_job", "looks_same_topic"]
