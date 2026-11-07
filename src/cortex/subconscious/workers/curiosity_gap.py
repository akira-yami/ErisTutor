# -*- coding: utf-8 -*-
"""Phase 2 - Epistemic Void / Active Curiosity Worker.

CuriosityGapAgent tìm lỗ hổng tri thức rồi găm seed, không hỏi ngay. Curiosity mà
cứ bắn ra thành câu hỏi tức thì thì không phải trí tò mò, đó là popup quảng cáo có
bằng triết học.
"""

from __future__ import annotations

from typing import Iterable

from ..thought_fragment import ThoughtFragment, clamp01
from ..worker_base import RuleBasedWorker, WorkerContext
from ._utils import CURIOSITY_TERMS, TECHNICAL_TERMS, context_evidence_ids, contains_any, score_terms, user_text_from_context


class CuriosityGapAgent(RuleBasedWorker):
    """Sinh knowledge_gap / curiosity_seed / wait_for_evidence_plan / ask_later_candidate."""

    name = "curiosity_gap_agent"

    def build_fragments(self, context: WorkerContext) -> Iterable[ThoughtFragment]:
        text = user_text_from_context(context)
        evidence_ids = context_evidence_ids(context, fallback_prefix="curiosity")
        if not text and not evidence_ids:
            return ()

        now = float(context.now or 0.0) or None
        curiosity = score_terms(text, CURIOSITY_TERMS)
        technical = score_terms(text, TECHNICAL_TERMS)
        has_phase_scope = any(token in text.lower() for token in ("phase", "trục", "worker", "module", "pipeline"))
        gap_strength = clamp01(0.24 + curiosity * 0.36 + technical * 0.22 + (0.18 if has_phase_scope else 0.0))

        # Không phải mọi task đều cần curiosity. Nếu gap thấp, chỉ im.
        if gap_strength < 0.32:
            return ()

        if has_phase_scope:
            gap = "Scope kiến trúc còn mở: cần biết worker nào được cắm runtime ngay và worker nào chỉ dừng ở blackboard."
            seed = "Theo dõi xem fragment Phase mới có đủ evidence/route hay chỉ tạo nội tâm sân khấu."
            ask_later = "Nếu integration sâu hơn, hỏi user muốn cắm vào coordinator hay giữ ở adapter test trước."
        elif contains_any(text, TECHNICAL_TERMS):
            gap = "Thiếu xác nhận về trạng thái repo/test hiện tại; cần quan sát output trước khi kết luận sâu."
            seed = "Chờ kết quả test hoặc log để phân biệt bug contract với bug integration."
            ask_later = "Nếu thiếu dữ kiện, hỏi xin log/test failure thay vì đoán tiếp."
        else:
            gap = "Có khoảng trống ngữ cảnh; nên infer quietly và chờ evidence tự lấp trước khi hỏi."
            seed = "Giữ curiosity ở dạng private seed, chưa surface."
            ask_later = "Chỉ hỏi khi câu trả lời trực tiếp cần dữ kiện đó."

        return (
            ThoughtFragment.create(
                source_agent=self.name,
                kind="knowledge_gap",
                content=gap,
                evidence_ids=evidence_ids,
                confidence=0.46 + gap_strength * 0.28,
                urgency=gap_strength * 0.70,
                affect_charge=0.18 + gap_strength * 0.20,
                privacy_level="normal",
                surface_allowed=False,
                ttl_seconds=360,
                tags=("curiosity", "knowledge_gap"),
                now=now,
            ),
            ThoughtFragment.create(
                source_agent=self.name,
                kind="curiosity_seed",
                content=seed,
                evidence_ids=evidence_ids,
                confidence=0.48 + gap_strength * 0.26,
                urgency=gap_strength * 0.52,
                affect_charge=0.24 + gap_strength * 0.22,
                privacy_level="private",
                surface_allowed=False,
                ttl_seconds=540,
                tags=("curiosity", "curiosity_seed"),
                now=now,
            ),
            ThoughtFragment.create(
                source_agent=self.name,
                kind="wait_for_evidence_plan",
                content="Không hỏi ngay; ưu tiên chờ thêm evidence từ runtime/test/log hoặc từ phản hồi tiếp theo của user.",
                evidence_ids=evidence_ids,
                confidence=0.56,
                urgency=0.34 + gap_strength * 0.20,
                affect_charge=0.16,
                privacy_level="normal",
                surface_allowed=False,
                ttl_seconds=420,
                tags=("curiosity", "wait_for_evidence"),
                now=now,
            ),
            ThoughtFragment.create(
                source_agent=self.name,
                kind="ask_later_candidate",
                content=ask_later,
                evidence_ids=evidence_ids,
                confidence=0.44 + gap_strength * 0.18,
                urgency=0.22 + gap_strength * 0.20,
                affect_charge=0.14,
                privacy_level="normal",
                surface_allowed=False,
                ttl_seconds=420,
                tags=("curiosity", "ask_later"),
                now=now,
            ),
        )
