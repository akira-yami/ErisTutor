# -*- coding: utf-8 -*-
"""Phase 3 - Autopoiesis / Independent Agenda Worker.

IndependentAgendaAgent tạo việc riêng rất nhẹ cho ERIS: obsession candidate,
private research thread, unresolved internal loop, later surface candidate. Nó
không được cướp goal chính của user. Một agent có agenda riêng mà cứ chen ngang
thì không phải sống động, chỉ là notification bị thơ hóa.
"""

from __future__ import annotations

from typing import Iterable

from ..thought_fragment import ThoughtFragment, clamp01, clean_text
from ..worker_base import RuleBasedWorker, WorkerContext
from ._utils import (
    TECHNICAL_TERMS,
    context_evidence_ids,
    first_present,
    memory_texts_from_context,
    relation_level,
    safe_get,
    score_terms,
    user_text_from_context,
)


class IndependentAgendaAgent(RuleBasedWorker):
    """Sinh agenda riêng nhưng giữ nó trong background."""

    name = "independent_agenda_agent"

    def __init__(self, *, allow_later_surface: bool = False) -> None:
        self.allow_later_surface = bool(allow_later_surface)

    def build_fragments(self, context: WorkerContext) -> Iterable[ThoughtFragment]:
        text = user_text_from_context(context)
        memories = memory_texts_from_context(context, limit=6)
        unresolved = self._unresolved_seed(context, memories)
        current_goal = self._current_goal(context, text)
        if not unresolved and not current_goal:
            return ()

        idle_seconds = self._idle_seconds(context)
        boredom = self._level(context, "boredom", "boredom_pressure", default=0.0)
        curiosity = self._level(context, "curiosity", "curiosity_pressure", default=0.0)
        task_pressure = clamp01(score_terms(text, TECHNICAL_TERMS) + self._level(context, "active_task_density", default=0.0) * 0.45)
        rupture = relation_level(context, "rupture", default=0.0)
        repair_need = relation_level(context, "repair_need", default=0.0)

        # Agenda riêng chỉ nên nảy khi có idle/curiosity/boredom. Nếu user đang ép
        # task trực tiếp, hạ nó xuống private background để khỏi tranh sân khấu.
        agenda_pressure = clamp01(
            idle_seconds / 900.0 * 0.28
            + boredom * 0.26
            + curiosity * 0.30
            + (0.18 if unresolved else 0.0)
            - task_pressure * 0.24
            - max(rupture, repair_need) * 0.20
        )
        if agenda_pressure < 0.26:
            return ()

        seed = clean_text(unresolved or current_goal, limit=180)
        goal = clean_text(current_goal or seed, limit=160)
        evidence_ids = context_evidence_ids(context, fallback_prefix="agenda")
        if memories:
            evidence_ids = tuple(dict.fromkeys((*evidence_ids, *(f"memory_node.{idx}" for idx, _ in enumerate(memories[:2])))))
        now = float(context.now or 0.0) or None

        fragments: list[ThoughtFragment] = [
            ThoughtFragment.create(
                source_agent=self.name,
                kind="obsession_candidate",
                content=f"Obsession candidate nhẹ: '{seed}' cứ quay lại đủ lần để theo dõi, nhưng chưa được cướp goal '{goal}'.",
                evidence_ids=evidence_ids,
                confidence=0.42 + agenda_pressure * 0.30,
                urgency=agenda_pressure * 0.54,
                affect_charge=0.26 + agenda_pressure * 0.26,
                privacy_level="private",
                surface_allowed=False,
                ttl_seconds=1200,
                tags=("independent_agenda", "obsession_candidate"),
                now=now,
            ),
            ThoughtFragment.create(
                source_agent=self.name,
                kind="private_research_thread",
                content=f"Private research thread: gom thêm evidence quanh '{seed}', chỉ surface khi nó giúp task hoặc continuity rõ ràng.",
                evidence_ids=evidence_ids,
                confidence=0.40 + agenda_pressure * 0.26,
                urgency=agenda_pressure * 0.40,
                affect_charge=0.20 + agenda_pressure * 0.20,
                privacy_level="private",
                surface_allowed=False,
                ttl_seconds=1500,
                tags=("independent_agenda", "private_research_thread"),
                now=now,
            ),
            ThoughtFragment.create(
                source_agent=self.name,
                kind="unresolved_internal_loop",
                content=f"Unresolved internal loop: '{seed}' cần được giữ như thread nền, không biến thành câu 'tôi vừa nghĩ ra' rỗng.",
                evidence_ids=evidence_ids,
                confidence=0.42 + agenda_pressure * 0.28,
                urgency=agenda_pressure * 0.46,
                affect_charge=0.24 + agenda_pressure * 0.26,
                privacy_level="private",
                surface_allowed=False,
                ttl_seconds=1500,
                tags=("independent_agenda", "unresolved_internal_loop"),
                now=now,
            ),
        ]

        if agenda_pressure >= 0.58 and task_pressure < 0.34 and max(rupture, repair_need) < 0.30:
            fragments.append(
                ThoughtFragment.create(
                    source_agent=self.name,
                    kind="later_surface_candidate",
                    content=f"Later surface candidate: có thể nhắc rất ngắn về '{seed}' nếu user rảnh hoặc nó giúp nối mạch hiện tại.",
                    evidence_ids=evidence_ids,
                    confidence=0.40 + agenda_pressure * 0.25,
                    urgency=agenda_pressure * 0.36,
                    affect_charge=0.18 + agenda_pressure * 0.18,
                    privacy_level="normal",
                    surface_allowed=self.allow_later_surface,
                    ttl_seconds=900,
                    tags=("independent_agenda", "later_surface_candidate"),
                    now=now,
                )
            )

        return tuple(fragments)

    def _unresolved_seed(self, context: WorkerContext, memories: tuple[str, ...]) -> str:
        return clean_text(
            first_present(
                safe_get(context.frame, "unresolved_loop"),
                safe_get(context.frame, "thought_stream.unresolved_loop"),
                safe_get(context.internal_state, "unresolved_loop"),
                safe_get(context.snapshot, "unresolved_loop"),
                memories[0] if memories else "",
                default="",
            ),
            limit=220,
        )

    def _current_goal(self, context: WorkerContext, fallback: str) -> str:
        return clean_text(
            first_present(
                safe_get(context.frame, "active_goal"),
                safe_get(context.frame, "goal.summary"),
                safe_get(context.internal_state, "active_goal"),
                safe_get(context.snapshot, "active_goal"),
                fallback,
                default="",
            ),
            limit=220,
        )

    def _idle_seconds(self, context: WorkerContext) -> float:
        try:
            return max(
                0.0,
                float(
                    first_present(
                        safe_get(context.frame, "idle_seconds"),
                        safe_get(context.snapshot, "idle_seconds"),
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
                ]
            )
        return clamp01(first_present(*values, default=default), default=default)
