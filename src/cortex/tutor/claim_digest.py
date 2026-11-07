# -*- coding: utf-8 -*-
"""
cortex/tutor/claim_digest.py
============================

Chưng cất Tutor_Answer thành các Knowledge_Claim NGẮN, ATOMIC rồi commit vào trí nhớ
ngữ nghĩa (có embedding) qua `ingest_fn`, gắn provenance `source="tutor"`.

Khác bản cũ (chỉ ném nguyên văn câu trả lời thành 1 blob):
  1. Chưng cất: tách câu trả lời thành vài fact ngắn độc lập (LLM `distill_fn` nếu có,
     fallback tách câu). Mỗi fact commit riêng → embedding sạch hơn, recall trúng hơn.
  2. Cổng kiểm chứng: KHÔNG commit nếu câu trả lời chỉ là "tôi không chắc / không biết".
     Confidence theo việc có citations + có hedge hay không (không còn cứng 0.66).

KHÔNG tạo store mới. KHÔNG phát answer ra kênh nói — module này chỉ ghi trí nhớ.
"""

from __future__ import annotations
from core.di import resolve_dependency

import inspect
import re
from typing import Any, Callable, List, Optional

from .config import TutorConfig, scrub_identity
from .contracts import TutorEpisode


# ingest_fn(text, *, source, gap_id, citations[, confidence]) -> số claim đã commit.
IngestFn = Callable[..., int]
# distill_fn(answer_text) -> list[str] (các fact ngắn, atomic). Tùy chọn.
DistillFn = Callable[[str], List[str]]


# Câu trả lời "không biết" thì KHÔNG học — tránh đóng đinh rác vào trí nhớ.
_STRONG_UNCERTAIN = (
    "tôi không biết", "minh khong biet", "mình không biết", "tôi không rõ",
    "không có thông tin", "khong co thong tin", "tôi không chắc", "mình không chắc",
    "không chắc chắn", "xin lỗi, tôi không", "xin loi, toi khong", "không tìm thấy",
    "i don't know", "i do not know", "i'm not sure", "i am not sure",
    "no information", "cannot find", "unable to find",
)
# Hedge: vẫn học nhưng hạ confidence (phỏng đoán, không phải sự thật chắc).
_HEDGE = (
    "có thể", "co the", "có lẽ", "co le", "dường như", "hình như", "tôi nghĩ",
    "mình nghĩ", "perhaps", "maybe", "probably", "i think", "không chắc",
)

_MAX_FACTS = 5
_MAX_FACT_LEN = 300
_MIN_FACT_LEN = 3


def _accepts_confidence(fn: Any) -> bool:
    """True nếu ingest_fn nhận tham số confidence (hoặc có **kwargs)."""
    try:
        sig = inspect.signature(fn)
    except (TypeError, ValueError):
        return False
    for param in sig.parameters.values():
        if param.kind == param.VAR_KEYWORD:
            return True
        if param.name == "confidence":
            return True
    return False


def _is_too_uncertain(text: str) -> bool:
    head = str(text or "").strip().lower()[:160]
    return any(marker in head for marker in _STRONG_UNCERTAIN)


def _is_hedged(text: str) -> bool:
    low = str(text or "").lower()
    return any(h in low for h in _HEDGE)


def _heuristic_split(text: str) -> List[str]:
    """Tách câu trả lời thành các câu ngắn khi không có distill_fn."""
    raw = str(text or "").strip()
    if not raw:
        return []
    # Tách theo xuống dòng / dấu câu kết thúc / bullet.
    parts = re.split(r"(?:(?<=[.!?;])\s+)|\n+|(?:^|\s)[-•*]\s+", raw)
    facts = [re.sub(r"\s+", " ", p).strip(" -•*\t") for p in parts if p and p.strip()]
    return facts or [re.sub(r"\s+", " ", raw).strip()]


class ClaimDigest:
    """Chưng cất câu trả lời gia sư thành fact atomic và ghi vào trí nhớ ngữ nghĩa."""

    def __init__(
        self,
        config: Optional[TutorConfig] = None,
        *,
        ingest_fn: Optional[IngestFn] = None,
        distill_fn: Optional[DistillFn] = None,
    ) -> None:
        self.config = resolve_dependency(config, TutorConfig).normalized()
        self._ingest_fn = ingest_fn
        self._distill_fn = distill_fn
        self._ingest_accepts_confidence = _accepts_confidence(ingest_fn) if ingest_fn is not None else False

    def digest(self, episode: TutorEpisode) -> int:
        """Trả tổng số Knowledge_Claim đã commit. 0 nếu rỗng/không chắc/không commit được."""

        answer = episode.answer
        if answer is None or not answer.text:
            return 0
        if self._ingest_fn is None:
            # Không có đường commit ⇒ không học được gì (an toàn: không tạo store mới).
            return 0

        text = scrub_identity(answer.text).strip()
        if not text:
            return 0

        # Cổng kiểm chứng: câu trả lời "không biết / không chắc" ⇒ không commit.
        if _is_too_uncertain(text):
            return 0

        gap_id = episode.gap.gap_id
        citations = tuple(answer.citations or ())

        facts = self._distill(text)
        if not facts:
            # Fallback cuối: vẫn lưu nguyên văn (đã cắt) như một claim, giữ hành vi cũ.
            facts = [text[:_MAX_FACT_LEN]]

        committed = 0
        seen: set[str] = set()
        for fact in facts:
            fact = str(fact or "").strip()
            if len(fact) < _MIN_FACT_LEN:
                continue
            key = fact.lower()
            if key in seen:
                continue
            seen.add(key)
            confidence = self._confidence(fact, citations)
            committed += self._commit(fact[:_MAX_FACT_LEN], gap_id=gap_id, citations=citations, confidence=confidence)
        return committed

    # ------------------------------------------------------------------

    def _distill(self, text: str) -> List[str]:
        facts: List[str] = []
        if self._distill_fn is not None:
            try:
                raw = self._distill_fn(text) or []
                facts = [str(f).strip() for f in raw if str(f or "").strip()]
            except Exception:
                facts = []
        if not facts:
            facts = _heuristic_split(text)
        # Bỏ fact thuần hedge quá ngắn (phỏng đoán rỗng), khử trùng, cắt số lượng.
        cleaned: List[str] = []
        for fact in facts:
            if len(fact) < _MIN_FACT_LEN:
                continue
            if _is_hedged(fact) and len(fact) < 16:
                continue
            cleaned.append(fact)
            if len(cleaned) >= _MAX_FACTS:
                break
        return cleaned

    def _confidence(self, fact: str, citations: tuple) -> float:
        base = 0.6
        if citations:
            base += 0.12
        if _is_hedged(fact):
            base -= 0.12
        return max(0.5, min(0.8, round(base, 3)))

    def _commit(self, text: str, *, gap_id: str, citations: tuple, confidence: float) -> int:
        fn = self._ingest_fn
        if fn is None:
            return 0
        try:
            if self._ingest_accepts_confidence:
                count = fn(text, source="tutor", gap_id=gap_id, citations=citations, confidence=confidence)
            else:
                count = fn(text, source="tutor", gap_id=gap_id, citations=citations)
        except TypeError:
            # ingest_fn chữ ký hẹp hơn dự kiến ⇒ thử lại không kèm confidence.
            try:
                count = fn(text, source="tutor", gap_id=gap_id, citations=citations)
            except Exception:
                return 0
        except Exception:
            return 0
        try:
            return max(0, int(count))
        except (TypeError, ValueError):
            return 0


__all__ = ["ClaimDigest"]
