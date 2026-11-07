# -*- coding: utf-8 -*-
"""
cortex/tutor/knowledge_provider.py
==================================

KnowledgeProvider — đọc claim ĐÃ HỌC (đã commit, có embedding) từ trí nhớ ngữ nghĩa để
cung cấp claim Tutor đã học cho memory recall và các consumer có contract rõ.

Chỉ ĐỌC state đã commit (không gọi backend/LLM) ⇒ giữ tính tất định của lõi dự đoán.
"""

from __future__ import annotations

from typing import Any, Callable, Optional

_MAX_PRED_LEN = 280


class KnowledgeProvider:
    """Làm giàu dự đoán bằng tri thức gia sư đã học.

    `recall_fn(query) -> list[item]`: mỗi item có `.claim` (str) và `.tags` (iterable).
    Mặc định chỉ dùng item có tag "tutor" để không lấn át recall thường.
    """

    def __init__(
        self,
        recall_fn: Optional[Callable[[str], list]] = None,
        *,
        only_tutor: bool = True,
    ) -> None:
        self._recall_fn = recall_fn
        self._only_tutor = only_tutor

    def enrich(self, *, content_thread: str = "", percept_text: str = "") -> str:
        if self._recall_fn is None:
            return ""
        query = " ".join(x for x in (str(percept_text or ""), str(content_thread or "")) if x.strip()).strip()
        if not query:
            return ""
        try:
            hits = self._recall_fn(query) or []
        except Exception:
            return ""
        for item in hits:
            tags = tuple(getattr(item, "tags", ()) or ())
            if self._only_tutor and "tutor" not in tags:
                continue
            claim = str(getattr(item, "claim", "") or "").strip()
            if not claim:
                continue
            activity = (str(percept_text or "") or str(content_thread or "")).strip()
            base = f"bạn tiếp tục: {activity}" if activity else "Tiếp tục như hiện tại"
            enriched = f"{base} — dựa trên điều đã học: {claim}"
            return enriched[:_MAX_PRED_LEN]
        return ""

    def learned_claims(self, query: str, *, limit: int = 3) -> list:
        """Trả các claim đã học (tag 'tutor') liên quan query, dạng text thô.

        Dùng cho đường ĐƯA TRI THỨC VÀO PROMPT THẬT (không phải shadow prediction):
        runtime gọi hàm này khi dựng memory_context để LLM thấy điều ERIS đã học.
        """
        if self._recall_fn is None:
            return []
        q = str(query or "").strip()
        if not q:
            return []
        try:
            hits = self._recall_fn(q) or []
        except Exception:
            return []
        out: list = []
        for item in hits:
            tags = tuple(getattr(item, "tags", ()) or ())
            if self._only_tutor and "tutor" not in tags:
                continue
            claim = str(getattr(item, "claim", "") or "").strip()
            if not claim:
                continue
            out.append(claim)
            if len(out) >= max(1, int(limit)):
                break
        return out


__all__ = ["KnowledgeProvider"]
