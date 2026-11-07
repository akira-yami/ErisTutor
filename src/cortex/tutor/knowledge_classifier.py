# -*- coding: utf-8 -*-
"""
cortex/tutor/knowledge_classifier.py
====================================

Phân loại câu trả lời gia sư thành KnowledgeKind để định tuyến:
- durable : kiến thức thật, tái dùng → lưu (embedding) + làm giàu dự đoán.
- context : đoán ngữ cảnh user đang làm gì → cập nhật hiểu biết, không lưu dài hạn.
- banter  : chuyện vui/khịa → đẩy xung nói cho vui, không lưu.

Dùng local LLM (inject `generate_fn`); heuristic fallback khi model lỗi.
"""

from __future__ import annotations

from typing import Any, Callable, Optional

GenerateFn = Callable[[str], str]

_VALID = {"durable", "context", "banter"}

_PROMPT = (
    "Phân loại điều ERIS vừa học được vào ĐÚNG MỘT nhãn:\n"
    "- durable: kiến thức/sự thật chung, tái dùng được về sau.\n"
    "- context: phỏng đoán về việc người dùng đang làm/đang xem trên màn hình.\n"
    "- banter: chuyện vui, vô thưởng vô phạt, để trêu/nói cho vui.\n"
    "Câu hỏi: {question}\n"
    "Câu trả lời: {answer}\n"
    "Chỉ trả về JSON {{\"kind\": \"durable|context|banter\"}}."
)


class KnowledgeClassifier:
    def __init__(self, *, generate_fn: Optional[GenerateFn] = None) -> None:
        self._generate_fn = generate_fn

    def classify(
        self,
        question: str,
        answer: str,
        *,
        gap_source: str = "",
        kind_hint: str = "",
    ) -> str:
        # Heuristic ưu tiên: observer_unknown ⇒ context; có kind_hint hợp lệ ⇒ dùng.
        hint = str(kind_hint or "").strip().lower()
        if gap_source == "observer_unknown":
            base = "context"
        elif hint in _VALID:
            base = hint
        else:
            base = "durable"

        fn = self._generate_fn
        if fn is None:
            return base
        try:
            out = fn(_PROMPT.format(question=question, answer=answer))
        except Exception:
            return base
        text = str(out or "").lower()
        for kind in _VALID:
            if f'"{kind}"' in text or f"kind: {kind}" in text or text.strip() == kind:
                return kind
        return base


__all__ = ["KnowledgeClassifier"]
