# -*- coding: utf-8 -*-
"""Phase 3 - Stochastic Resonance / Daydream Injector.

DaydreamInjector không gọi main LLM và không bịa thơ từ không khí. Nó chỉ nối
2 memory node đã có với 1 current concern khi runtime đủ rảnh, đủ bored, và rủi
ro chen lời thấp. Nói ngắn gọn: mơ cũng phải có hóa đơn nguồn. Loài người bịa
rồi gọi là trực giác đủ nhiều rồi, ERIS không cần học thói đó quá sớm.
"""

from __future__ import annotations

from typing import Iterable

from ..thought_fragment import ThoughtFragment, clamp01, clean_text
from ..worker_base import RuleBasedWorker, WorkerContext
from ._utils import (
    context_evidence_ids,
    first_present,
    memory_texts_from_context,
    relation_level,
    safe_get,
    user_text_from_context,
)


class DaydreamInjector(RuleBasedWorker):
    """Sinh poetic_association_fragment và weird_but_grounded_link.

    Điều kiện bật mặc định khá dè dặt:
    - idle/boredom đủ cao.
    - rupture/repair risk thấp.
    - có ít nhất 2 memory/current concern để ghép.

    Output mặc định không surface public. Phase 3 chỉ tạo private material cho
    các tầng sau, không biến ERIS thành NPC cứ thông báo "tôi vừa nghĩ ra".
    """

    name = "daydream_injector"

    def __init__(self, *, allow_surface: bool = False) -> None:
        self.allow_surface = bool(allow_surface)

    def build_fragments(self, context: WorkerContext) -> Iterable[ThoughtFragment]:
        idle_seconds = self._idle_seconds(context)
        boredom = self._level(context, "boredom", "boredom_pressure", default=0.0)
        fatigue = self._level(context, "fatigue", "social_fatigue", default=0.0)
        rupture = relation_level(context, "rupture", default=0.0)
        repair_need = relation_level(context, "repair_need", default=0.0)
        social_load = self._level(context, "social_load", "overload", default=0.0)
        risk = clamp01(max(rupture, repair_need, social_load * 0.72))

        idle_score = clamp01(idle_seconds / 420.0)
        trigger = clamp01(idle_score * 0.40 + boredom * 0.38 + fatigue * 0.12 - risk * 0.34)
        if trigger < 0.36:
            return ()

        memory_nodes = memory_texts_from_context(context, limit=6)
        current_concern = self._current_concern(context)
        # Cần ít nhất 2 node có rễ. Nếu chỉ có 1 câu hiện tại thì đây không phải
        # daydream, chỉ là paraphrase có mùi nhang prompt.
        grounded_nodes = tuple(item for item in (*memory_nodes, current_concern) if item)
        if len(grounded_nodes) < 2:
            return ()

        node_a = clean_text(grounded_nodes[0], limit=170)
        node_b = clean_text(grounded_nodes[1], limit=170)
        concern = clean_text(current_concern or node_b, limit=150)
        evidence_ids = context_evidence_ids(context, fallback_prefix="daydream")
        if memory_nodes:
            evidence_ids = tuple(dict.fromkeys((*evidence_ids, *(f"memory_node.{idx}" for idx, _ in enumerate(memory_nodes[:2])))))
        now = float(context.now or 0.0) or None

        association = (
            f"Daydream ghép '{node_a}' với '{node_b}' quanh concern '{concern}'; "
            "liên kết này chỉ là giả thuyết thẩm mỹ có rễ, chưa phải truth."
        )
        link = (
            f"Weird-but-grounded link: pattern '{node_a}' có thể soi lại '{concern}' "
            f"qua residue của '{node_b}'."
        )

        return (
            ThoughtFragment.create(
                source_agent=self.name,
                kind="poetic_association_fragment",
                content=association,
                evidence_ids=evidence_ids,
                confidence=0.42 + trigger * 0.30,
                urgency=0.22 + trigger * 0.28,
                affect_charge=0.28 + boredom * 0.24 + fatigue * 0.08,
                privacy_level="private",
                surface_allowed=self.allow_surface and risk < 0.20,
                ttl_seconds=900,
                tags=("daydream", "poetic_association", "grounded_drift"),
                now=now,
            ),
            ThoughtFragment.create(
                source_agent=self.name,
                kind="weird_but_grounded_link",
                content=link,
                evidence_ids=evidence_ids,
                confidence=0.40 + trigger * 0.28,
                urgency=0.18 + trigger * 0.24,
                affect_charge=0.24 + boredom * 0.20,
                privacy_level="private",
                surface_allowed=False,
                ttl_seconds=900,
                tags=("daydream", "weird_but_grounded_link", "private_thread_seed"),
                now=now,
            ),
        )

    def _idle_seconds(self, context: WorkerContext) -> float:
        return max(
            0.0,
            float(
                first_present(
                    safe_get(context.frame, "idle_seconds"),
                    safe_get(context.snapshot, "idle_seconds"),
                    safe_get(context.snapshot, "time.idle_seconds"),
                    safe_get(context.internal_state, "idle_seconds"),
                    default=0.0,
                )
                or 0.0
            ),
        )

    def _level(self, context: WorkerContext, *names: str, default: float = 0.0) -> float:
        values = []
        for name in names:
            values.extend(
                [
                    safe_get(context.internal_state, name),
                    safe_get(context.frame, f"internal_state.{name}"),
                    safe_get(context.frame, name),
                    safe_get(context.snapshot, name),
                ]
            )
        return clamp01(first_present(*values, default=default), default=default)

    def _current_concern(self, context: WorkerContext) -> str:
        return clean_text(
            first_present(
                user_text_from_context(context),
                safe_get(context.frame, "dominant_focus"),
                safe_get(context.frame, "workspace.dominant_thread"),
                safe_get(context.snapshot, "dominant_focus"),
                safe_get(context.internal_state, "current_concern"),
                default="",
            ),
            limit=220,
        )
