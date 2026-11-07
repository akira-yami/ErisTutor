# -*- coding: utf-8 -*-
"""Phase 1 - Memory Resonance Worker.

MemoryResonatorAgent nối hiện tại với memory/unresolved residue. Nó không biến ký
ức thành lời thoại ngay; nó chỉ tạo mảnh cộng hưởng để Synthesizer cân cùng các
worker khác. Ký ức không phải loa phường, dù nhiều chatbot rất thích giả vờ vậy.
"""

from __future__ import annotations

from typing import Iterable

from ..thought_fragment import ThoughtFragment, clamp01, clean_text
from ..worker_base import RuleBasedWorker, WorkerContext
from ._utils import (
    context_evidence_ids,
    memory_texts_from_context,
    safe_get,
    score_terms,
    TECHNICAL_TERMS,
    user_text_from_context,
)


class MemoryResonatorAgent(RuleBasedWorker):
    """Sinh pattern_match / unresolved_loop / memory_echo_fragment."""

    name = "memory_resonator"

    def __init__(self, *, allow_surface_echo: bool = False) -> None:
        # Mặc định không surface trực tiếp. Có thể bật ở integration sau khi guard
        # đã đủ tốt. Một dòng memory echo sai còn khó chịu hơn một traceback thật thà.
        self.allow_surface_echo = bool(allow_surface_echo)

    def build_fragments(self, context: WorkerContext) -> Iterable[ThoughtFragment]:
        text = user_text_from_context(context)
        memories = memory_texts_from_context(context, limit=6)
        evidence_ids = context_evidence_ids(context, fallback_prefix="memory")
        now = float(context.now or 0.0) or None
        fragments: list[ThoughtFragment] = []

        # Unresolved loop có thể nằm trong frame/thought_stream/internal_state tùy runtime revision.
        unresolved = safe_get(context.frame, "unresolved_loop") or safe_get(context.frame, "thought_surface.unresolved_loop")
        unresolved_texts = tuple(clean_text(item, limit=220) for item in (unresolved if isinstance(unresolved, (list, tuple)) else (unresolved,)) if clean_text(item, limit=220))
        if unresolved_texts:
            content = "Unresolved loop còn mở: " + "; ".join(unresolved_texts[:2])
            fragments.append(
                ThoughtFragment.create(
                    source_agent=self.name,
                    kind="unresolved_loop",
                    content=content,
                    evidence_ids=evidence_ids or tuple(f"unresolved.{idx}" for idx in range(len(unresolved_texts[:2]))),
                    confidence=0.62,
                    urgency=0.56,
                    affect_charge=0.32,
                    privacy_level="normal",
                    surface_allowed=False,
                    ttl_seconds=420,
                    tags=("memory", "unresolved_loop"),
                    now=now,
                )
            )

        if memories:
            # Chọn memory đầu tiên vì memory_packet thường đã được retrieval engine xếp điểm.
            top_memory = memories[0]
            overlap = score_terms(f"{text} {top_memory}", TECHNICAL_TERMS)
            confidence = clamp01(0.46 + overlap * 0.32 + (0.12 if text else 0.0))
            fragments.append(
                ThoughtFragment.create(
                    source_agent=self.name,
                    kind="memory_echo_fragment",
                    content=f"Memory echo liên quan hiện tại: {top_memory}",
                    evidence_ids=evidence_ids or ("memory.packet.0",),
                    confidence=confidence,
                    urgency=0.36 + overlap * 0.20,
                    affect_charge=0.18 + min(0.24, overlap * 0.20),
                    privacy_level="normal",
                    surface_allowed=self.allow_surface_echo and confidence >= 0.70,
                    ttl_seconds=360,
                    tags=("memory", "memory_echo"),
                    now=now,
                )
            )

            if len(memories) >= 2:
                fragments.append(
                    ThoughtFragment.create(
                        source_agent=self.name,
                        kind="pattern_match",
                        content="Có nhiều memory node cùng hướng: " + "; ".join(memories[:3]),
                        evidence_ids=evidence_ids or tuple(f"memory.packet.{idx}" for idx in range(min(3, len(memories)))),
                        confidence=0.52 + min(0.20, len(memories) * 0.04),
                        urgency=0.34,
                        affect_charge=0.20,
                        privacy_level="normal",
                        surface_allowed=False,
                        ttl_seconds=360,
                        tags=("memory", "pattern_match"),
                        now=now,
                    )
                )

        affective = safe_get(context.memory_packet, "affective_residue") or safe_get(context.frame, "memory_packet.affective_residue")
        affective_texts = tuple(clean_text(item, limit=200) for item in (affective if isinstance(affective, (list, tuple)) else (affective,)) if clean_text(item, limit=200))
        if affective_texts:
            fragments.append(
                ThoughtFragment.create(
                    source_agent=self.name,
                    kind="affective_residue",
                    content="Affective residue còn tác động nhẹ: " + "; ".join(affective_texts[:2]),
                    evidence_ids=evidence_ids or ("memory.affective_residue",),
                    confidence=0.50,
                    urgency=0.28,
                    affect_charge=0.48,
                    privacy_level="private",
                    surface_allowed=False,
                    ttl_seconds=420,
                    tags=("memory", "affective_residue"),
                    now=now,
                )
            )

        return tuple(fragments)
