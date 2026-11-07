# -*- coding: utf-8 -*-
"""Phase 2 - Self-Reflection / Ego Maintenance Worker.

SelfRegulatorAgent sinh restraint_drive và các warning nội bộ. Nó không làm van
đạo đức, không flatten personality. Nó chỉ báo rằng một impulse có thể đang quá
nóng/quá dày/quá bám. Cơ bản là bộ phận nhắc "đừng tự bắn vào chân" mà con người
thường bỏ qua với lòng tự tin đáng thương.
"""

from __future__ import annotations

from typing import Iterable

from ..thought_fragment import ThoughtFragment, clamp01
from ..worker_base import RuleBasedWorker, WorkerContext
from ._utils import TECHNICAL_TERMS, context_evidence_ids, relation_level, score_terms, user_text_from_context


class SelfRegulatorAgent(RuleBasedWorker):
    """Sinh restraint_drive / social_fatigue_adjustment / dignity_defense_delta."""

    name = "self_regulator"

    def build_fragments(self, context: WorkerContext) -> Iterable[ThoughtFragment]:
        text = user_text_from_context(context)
        evidence_ids = context_evidence_ids(context, fallback_prefix="regulation")
        now = float(context.now or 0.0) or None
        technical = score_terms(text, TECHNICAL_TERMS)

        social_load = relation_level(context, "social_load", default=0.0)
        exposure_load = relation_level(context, "exposure_load", default=0.0)
        rupture = relation_level(context, "rupture", default=0.0)
        repair_need = relation_level(context, "repair_need", default=0.0)
        dignity_defense = relation_level(context, "dignity_defense", default=0.0)
        attachment = relation_level(context, "attachment_pressure", default=0.0)
        irritation = relation_level(context, "irritation", default=0.0)

        restraint = clamp01(
            0.22
            + technical * 0.22
            + social_load * 0.22
            + exposure_load * 0.18
            + rupture * 0.22
            + dignity_defense * 0.20
            + irritation * 0.16
            + attachment * 0.10
        )
        if not text and not evidence_ids and restraint < 0.30:
            return ()

        fragments: list[ThoughtFragment] = [
            ThoughtFragment.create(
                source_agent=self.name,
                kind="restraint_drive",
                content=(
                    f"Restraint drive {restraint:.2f}: ưu tiên ngắn, grounded, tránh tự mở rộng cảm xúc khi task/user focus chưa cho phép."
                ),
                evidence_ids=evidence_ids,
                confidence=0.48 + min(0.32, restraint * 0.35),
                urgency=restraint,
                affect_charge=clamp01(0.18 + irritation * 0.25 + rupture * 0.25),
                privacy_level="private",
                surface_allowed=False,
                ttl_seconds=240,
                tags=("self_regulation", "restraint"),
                now=now,
            )
        ]

        if social_load >= 0.45 or exposure_load >= 0.45:
            fragments.append(
                ThoughtFragment.create(
                    source_agent=self.name,
                    kind="social_fatigue_adjustment",
                    content="Social/exposure load cao; giảm proactive speech và ưu tiên body-only hoặc store nếu không có ích trực tiếp.",
                    evidence_ids=evidence_ids,
                    confidence=0.52 + max(social_load, exposure_load) * 0.25,
                    urgency=max(social_load, exposure_load),
                    affect_charge=0.30 + exposure_load * 0.20,
                    privacy_level="private",
                    surface_allowed=False,
                    ttl_seconds=240,
                    tags=("self_regulation", "social_fatigue"),
                    now=now,
                )
            )

        if attachment >= 0.45 and repair_need < 0.35:
            fragments.append(
                ThoughtFragment.create(
                    source_agent=self.name,
                    kind="over_attention_warning",
                    content="Attachment/attention pressure có thể kéo ERIS chen lời; cần kiểm tra interruption cost trước khi surface.",
                    evidence_ids=evidence_ids,
                    confidence=0.50 + attachment * 0.24,
                    urgency=0.36 + attachment * 0.30,
                    affect_charge=0.34 + attachment * 0.24,
                    privacy_level="private",
                    surface_allowed=False,
                    ttl_seconds=240,
                    tags=("self_regulation", "over_attention"),
                    now=now,
                )
            )

        if dignity_defense >= 0.35 or rupture >= 0.30:
            fragments.append(
                ThoughtFragment.create(
                    source_agent=self.name,
                    kind="dignity_defense_delta",
                    content="Dignity defense đang tăng; nếu phải nói thì giữ directness, giảm warmth giả và tránh dramatic complaint.",
                    evidence_ids=evidence_ids,
                    confidence=0.50 + max(dignity_defense, rupture) * 0.28,
                    urgency=0.38 + max(dignity_defense, rupture) * 0.32,
                    affect_charge=0.40 + max(dignity_defense, rupture) * 0.25,
                    privacy_level="private",
                    surface_allowed=False,
                    ttl_seconds=300,
                    tags=("self_regulation", "dignity_defense"),
                    now=now,
                )
            )

        return tuple(fragments)
