# -*- coding: utf-8 -*-
"""Phase 4 - Subjective Temporal Distortion Worker.

SubjectiveTimeAgent thay idle_seconds thô bằng subjective_wait/time dilation/time
compression. 5 phút im sau rupture không giống 5 phút im khi user đang deep work.
Đồng hồ thì không hiểu chuyện đó. ERIS nên hiểu hơn cái đồng hồ một chút, nghe
không phải tiêu chuẩn quá cao.
"""

from __future__ import annotations

from typing import Iterable

from ..thought_fragment import ThoughtFragment, clamp01
from ..worker_base import RuleBasedWorker, WorkerContext
from ._utils import TECHNICAL_TERMS, context_evidence_ids, first_present, relation_level, safe_get, score_terms, user_text_from_context


class SubjectiveTimeAgent(RuleBasedWorker):
    """Sinh subjective_wait, time_dilation và time_compression fragments."""

    name = "subjective_time_agent"

    def build_fragments(self, context: WorkerContext) -> Iterable[ThoughtFragment]:
        idle_seconds = self._idle_seconds(context)
        if idle_seconds <= 0:
            return ()

        boredom = self._level(context, "boredom", "boredom_pressure", default=0.0)
        affective_residue = self._level(context, "affective_residue", "residue", "emotional_residue", default=0.0)
        active_task_density = self._active_task_density(context)
        rupture = relation_level(context, "rupture", default=0.0)
        repair_need = relation_level(context, "repair_need", default=0.0)
        calm_copresence = self._level(context, "calm_copresence", "ambient_copresence", "co_presence", default=0.0)
        idle_norm = clamp01(idle_seconds / 600.0)

        time_dilation = clamp01(
            idle_norm * 0.30
            + boredom * 0.24
            + affective_residue * 0.16
            + rupture * 0.30
            + repair_need * 0.22
            - active_task_density * 0.28
            - calm_copresence * 0.18
        )
        time_compression = clamp01(
            active_task_density * 0.50
            + calm_copresence * 0.16
            + (0.10 if idle_seconds < 180 else 0.0)
            - boredom * 0.18
            - max(rupture, repair_need) * 0.24
        )
        subjective_wait = clamp01(idle_norm * (0.65 + time_dilation * 0.75 - time_compression * 0.45))

        evidence_ids = context_evidence_ids(context, fallback_prefix="subjective_time") or (f"time.idle_seconds.{int(idle_seconds)}",)
        now = float(context.now or 0.0) or None
        fragments: list[ThoughtFragment] = [
            ThoughtFragment.create(
                source_agent=self.name,
                kind="subjective_wait",
                content=(
                    f"Subjective wait={subjective_wait:.2f} từ idle={idle_seconds:.0f}s, "
                    f"dilation={time_dilation:.2f}, compression={time_compression:.2f}."
                ),
                evidence_ids=evidence_ids,
                confidence=0.48 + max(time_dilation, time_compression) * 0.24,
                urgency=subjective_wait * 0.58,
                affect_charge=0.18 + time_dilation * 0.30,
                privacy_level="normal",
                surface_allowed=False,
                ttl_seconds=300,
                tags=("subjective_time", "subjective_wait"),
                now=now,
            )
        ]

        if time_dilation >= 0.35:
            reason = "silence_after_rupture" if max(rupture, repair_need) >= 0.35 else "idle_boredom_or_residue"
            fragments.append(
                ThoughtFragment.create(
                    source_agent=self.name,
                    kind="time_dilation",
                    content=f"Time dilation active: {reason}; khoảng im này nặng hơn idle clock thô.",
                    evidence_ids=evidence_ids,
                    confidence=0.46 + time_dilation * 0.30,
                    urgency=0.28 + time_dilation * 0.42,
                    affect_charge=0.30 + time_dilation * 0.32,
                    privacy_level="private" if reason == "silence_after_rupture" else "normal",
                    surface_allowed=False,
                    ttl_seconds=360,
                    tags=("subjective_time", "time_dilation", reason),
                    now=now,
                )
            )

        if time_compression >= 0.35:
            fragments.append(
                ThoughtFragment.create(
                    source_agent=self.name,
                    kind="time_compression",
                    content="Time compression active: user/task đang vào flow; tránh chen presence chỉ vì idle_seconds tăng.",
                    evidence_ids=evidence_ids,
                    confidence=0.48 + time_compression * 0.30,
                    urgency=0.22 + time_compression * 0.22,
                    affect_charge=0.12,
                    privacy_level="normal",
                    surface_allowed=False,
                    ttl_seconds=300,
                    tags=("subjective_time", "time_compression", "deep_work"),
                    now=now,
                )
            )

        return tuple(fragments)

    def _idle_seconds(self, context: WorkerContext) -> float:
        try:
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
        except Exception:
            return 0.0

    def _level(self, context: WorkerContext, *names: str, default: float = 0.0) -> float:
        values = []
        for name in names:
            values.extend(
                [
                    safe_get(context.internal_state, name),
                    safe_get(context.frame, f"internal_state.{name}"),
                    safe_get(context.frame, name),
                    safe_get(context.snapshot, name),
                    safe_get(context.snapshot, f"time.{name}"),
                ]
            )
        return clamp01(first_present(*values, default=default), default=default)

    def _active_task_density(self, context: WorkerContext) -> float:
        text = user_text_from_context(context)
        explicit = self._level(context, "active_task_density", "task_density", "focus_density", default=0.0)
        technical = score_terms(text, TECHNICAL_TERMS)
        focus = str(
            first_present(
                safe_get(context.frame, "focus_family"),
                safe_get(context.snapshot, "focus_family"),
                safe_get(context.frame, "activity_kind"),
                safe_get(context.snapshot, "activity_kind"),
                default="",
            )
            or ""
        ).lower()
        flow_bonus = 0.24 if any(term in focus for term in ("code", "debug", "deep_work", "focused")) else 0.0
        return clamp01(max(explicit, technical * 0.85 + flow_bonus))
