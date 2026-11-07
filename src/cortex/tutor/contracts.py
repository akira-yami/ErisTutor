                                                                    # -*- coding: utf-8 -*-
"""
cortex/tutor/contracts.py
=========================

Data models + literal contracts cho Curiosity Tutor.

Không phụ thuộc nặng vào phần còn lại của ERIS: chỉ tái dùng `clamp01` (import mềm,
có fallback) để các test/đơn vị không kéo theo cả pipeline.
"""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass, field
from typing import Any, Literal, Optional


# ---------------------------------------------------------------------------
# Import mềm clamp01 — fallback nội bộ nếu utility dùng chung không có sẵn.
# ---------------------------------------------------------------------------
try:  # pragma: no cover - đường import thường
    from cortex.value_utils import clamp01  # type: ignore
except Exception:  # pragma: no cover
    try:
        from .._vendored import clamp01  # type: ignore
    except Exception:
        def clamp01(value: Any, default: float = 0.0) -> float:  # type: ignore
            try:
                number = float(value)
            except (TypeError, ValueError):
                number = default
            if number != number:  # NaN
                number = default
            return max(0.0, min(1.0, number))


# =============================================================================
# Literal contracts
# =============================================================================

TutorStage = Literal[
    "forming",    # đang hình thành câu hỏi
    "opening",    # đang mở trình duyệt / điều hướng
    "asking",     # đang gõ câu hỏi
    "reading",    # đang đọc câu trả lời
    "digesting",  # đang chưng cất thành claim
    "done",       # xong
    "failed",     # thất bại
]

FailReason = Literal[
    "timeout",
    "captcha_or_expired",
    "selector_miss",
    "network_error",
    "empty_answer",
    "cancelled",             # discovery nền nhường User/chat, không phải failure
    "formulation_failed",
    "gate_rejected",
    "gate_rejected_known",    # router: đã biết rồi (trí nhớ trúng)
    "gate_rejected_trivial",  # router: vụn vặt, không đáng hỏi
]

GapSource = Literal["expectation_violation", "observer_unknown", "epistemic_uncertainty"]

# Phân loại tri thức rút ra từ một lượt hỏi gia sư:
#   durable  = kiến thức thật, tái dùng được → lưu (embedding) + làm giàu dự đoán
#   context  = đoán ngữ cảnh user đang làm gì → cập nhật hiểu biết, KHÔNG lưu dài hạn
#   banter   = chuyện vui/khịa → đẩy xung nói cho vui, KHÔNG lưu
KnowledgeKind = Literal["durable", "context", "banter"]


_MAX_QUESTION_LEN = 400
_MAX_HIGHLIGHT_LEN = 200


def _short_id(prefix: str, seed: str) -> str:
    digest = hashlib.sha1(seed.encode("utf-8", errors="ignore")).hexdigest()[:8]
    return f"{prefix}_{digest}"


# =============================================================================
# Data models
# =============================================================================

@dataclass(slots=True)
class KnowledgeGap:
    """Một lỗ hiểu biết cụ thể, dẫn xuất từ vi phạm kỳ vọng hoặc observer 'unknown'."""

    description: str = ""
    context: str = ""
    source: GapSource = "expectation_violation"
    stake_relevance: float = 0.0
    prediction_error: float = 0.0
    created_ts: float = 0.0
    updated_ts: float = 0.0
    resolved: bool = False
    embedding: tuple[float, ...] = ()
    gap_id: str = ""
    force: bool = False        # lệnh trực tiếp / "không biết" ⇒ bỏ qua idle + rate-limit
    kind_hint: str = ""        # gợi ý phân loại: durable | context | banter

    def __post_init__(self) -> None:
        self.description = str(self.description or "").strip()
        self.context = str(self.context or "").strip()
        self.stake_relevance = clamp01(self.stake_relevance)
        self.prediction_error = clamp01(self.prediction_error)
        self.embedding = tuple(self.embedding or ())
        if not self.gap_id:
            self.gap_id = _short_id("gap", self.description or self.context)

    def to_dict(self) -> dict[str, Any]:
        return {
            "gap_id": self.gap_id,
            "description": self.description,
            "source": self.source,
            "stake_relevance": round(self.stake_relevance, 4),
            "prediction_error": round(self.prediction_error, 4),
            "resolved": self.resolved,
            "updated_ts": self.updated_ts,
        }


@dataclass(slots=True)
class TutorQuestion:
    """Câu hỏi ERIS hình thành để hỏi gia sư. 1..400 ký tự khi hợp lệ."""

    text: str = ""
    gap_id: str = ""

    def __post_init__(self) -> None:
        text = str(self.text or "").strip()
        if len(text) > _MAX_QUESTION_LEN:
            text = text[:_MAX_QUESTION_LEN].rstrip()
        self.text = text

    @property
    def is_valid(self) -> bool:
        return 1 <= len(self.text) <= _MAX_QUESTION_LEN


@dataclass(slots=True)
class TutorAnswer:
    """Câu trả lời đọc được từ gia sư."""

    text: str = ""
    citations: tuple[str, ...] = ()
    ok: bool = False
    fail_reason: Optional[FailReason] = None

    def __post_init__(self) -> None:
        self.text = str(self.text or "").strip()
        self.citations = tuple(self.citations or ())


@dataclass(slots=True)
class TutorEpisode:
    """Một lượt hoàn chỉnh từ chọn gap đến commit claim (hoặc kết thúc)."""

    gap: KnowledgeGap
    episode_id: str = ""
    question: Optional[TutorQuestion] = None
    answer: Optional[TutorAnswer] = None
    stage: TutorStage = "forming"
    claims_committed: int = 0
    shared: bool = False
    started_ts: float = 0.0
    ended_ts: float = 0.0
    fail_reason: Optional[FailReason] = None

    def __post_init__(self) -> None:
        if not self.episode_id:
            seed = f"{self.gap.gap_id}:{self.started_ts or time.time()}"
            self.episode_id = _short_id("ep", seed)

    def to_dict(self) -> dict[str, Any]:
        return {
            "episode_id": self.episode_id,
            "gap_id": self.gap.gap_id,
            "stage": self.stage,
            "claims_committed": self.claims_committed,
            "shared": self.shared,
            "fail_reason": self.fail_reason,
            "duration_s": round(max(0.0, self.ended_ts - self.started_ts), 3) if self.ended_ts else 0.0,
        }


__all__ = [
    "TutorStage",
    "FailReason",
    "GapSource",
    "KnowledgeKind",
    "KnowledgeGap",
    "TutorQuestion",
    "TutorAnswer",
    "TutorEpisode",
]
