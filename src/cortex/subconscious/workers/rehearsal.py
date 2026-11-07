# -*- coding: utf-8 -*-
"""Phase 2 - Phantom Rehearsal Worker.

RehearsalAgent chạy sau Synthesizer. Input chính là ThoughtObject đã thắng cổ chai.
Nó mô phỏng vài route: say_now, delay, body_only, store_for_later, suppress. Nó
không sinh câu public, vì diễn nháp mà hét ra ngoài thì hơi thất bại về mặt khái niệm.
"""

from __future__ import annotations

from typing import Iterable

from ..thought_fragment import ThoughtFragment, ThoughtObject, clamp01, clean_text
from ..worker_base import RuleBasedWorker, WorkerContext
from ._utils import context_evidence_ids, first_present, safe_get


class RehearsalAgent(RuleBasedWorker):
    """Sinh rehearsal_result / suppressed_impulse / rupture_seed."""

    name = "rehearsal_agent"
    run_after_synthesis = True

    def build_fragments(self, context: WorkerContext) -> Iterable[ThoughtFragment]:
        thought = self._extract_thought_object(context)
        if thought is None:
            return ()

        now = float(context.now or 0.0) or None
        evidence_ids = thought.evidence_ids or context_evidence_ids(context, fallback_prefix="rehearsal")
        surface = clamp01(getattr(thought, "surface_pressure", 0.0))
        suppressed = clamp01(getattr(thought, "suppressed_pressure", 0.0))
        confidence = clamp01(getattr(thought, "confidence", 0.0))
        route = clean_text(getattr(thought, "recommended_route", "suppress"), limit=60)
        theme = clean_text(getattr(thought, "dominant_theme", "thought"), limit=120)

        wandering = "wandering" in tuple(getattr(thought, "tags", ()) or ()) or str(getattr(thought, "action_opportunity", "") or "") == "wandering_surface_candidate"
        # V66: rehearsal đánh giá chính thought (surface/suppression/confidence),
        # không đọc user focus/interruption để biến nó thành suppress/delay.
        # Public turn collision thuộc expression runtime, không thuộc nội tâm.
        say_now_score = clamp01(surface * 0.52 + confidence * 0.32 - suppressed * 0.24)
        delay_score = clamp01(0.24 + suppressed * 0.28 + (0.20 if route == "rehearse" else 0.0))
        body_only_score = clamp01(0.18 + suppressed * 0.24 + (0.18 if surface < 0.55 else 0.0))
        store_score = clamp01(0.18 + confidence * 0.34 + suppressed * 0.16 + (0.08 if wandering else 0.0))
        suppress_score = clamp01(0.12 + suppressed * 0.42 + (0.16 if confidence < 0.40 else 0.0))
        scores = {
            "say_now": say_now_score,
            "delay": delay_score,
            "body_only": body_only_score,
            "store_for_later": store_score,
            "suppress": suppress_score,
        }
        winner = max(scores.items(), key=lambda item: item[1])[0]
        score_text = ", ".join(f"{name}={value:.2f}" for name, value in scores.items())

        fragments: list[ThoughtFragment] = [
            ThoughtFragment.create(
                source_agent=self.name,
                kind="rehearsal_result",
                content=f"Rehearsal cho '{theme}': winner={winner}; {score_text}.",
                evidence_ids=evidence_ids,
                confidence=clamp01(0.46 + confidence * 0.34),
                urgency=max(surface, suppressed) * 0.72,
                affect_charge=clamp01(0.12 + suppressed * 0.34),
                privacy_level="normal",
                surface_allowed=False,
                ttl_seconds=240,
                tags=("rehearsal", theme.replace(" ", "_"), winner),
                now=now,
            )
        ]

        if suppressed >= 0.55 or route in {"rehearse", "store", "suppress"}:
            fragments.append(
                ThoughtFragment.create(
                    source_agent=self.name,
                    kind="suppressed_impulse",
                    content=f"Impulse về '{theme}' bị giữ lại; route={route}, suppressed_pressure={suppressed:.2f}.",
                    evidence_ids=evidence_ids,
                    confidence=clamp01(0.44 + suppressed * 0.35),
                    urgency=suppressed,
                    affect_charge=clamp01(0.30 + suppressed * 0.45),
                    privacy_level="private",
                    surface_allowed=False,
                    ttl_seconds=420,
                    tags=("rehearsal", "suppressed_impulse", theme.replace(" ", "_")),
                    now=now,
                )
            )

        # Rupture seed rất thận trọng. Chỉ sinh khi suppression cao + confidence cao,
        # để tránh shadow rác từ mỗi lần im bình thường.
        if suppressed >= 0.78 and confidence >= 0.58:
            fragments.append(
                ThoughtFragment.create(
                    source_agent=self.name,
                    kind="rupture_seed",
                    content=f"Rupture seed thấp về '{theme}': áp lực bị kìm cao, cần MetaAudit/Repair kiểm tra trước mọi biểu hiện công khai.",
                    evidence_ids=evidence_ids,
                    confidence=clamp01(0.40 + suppressed * 0.32),
                    urgency=clamp01(0.46 + suppressed * 0.32),
                    affect_charge=clamp01(0.55 + suppressed * 0.30),
                    privacy_level="private",
                    surface_allowed=False,
                    ttl_seconds=600,
                    tags=("rehearsal", "rupture_seed"),
                    now=now,
                )
            )

        return tuple(fragments)

    def _extract_thought_object(self, context: WorkerContext) -> ThoughtObject | None:
        direct = safe_get(context, "thought_object")
        if isinstance(direct, ThoughtObject):
            return direct
        if direct is not None and hasattr(direct, "recommended_route"):
            return direct  # type: ignore[return-value]

        report_object = first_present(
            safe_get(context.frame, "subconscious_report.object"),
            safe_get(context.frame, "thought_object"),
            default=None,
        )
        if isinstance(report_object, ThoughtObject):
            return report_object
        if isinstance(report_object, dict) and report_object.get("id"):
            try:
                return ThoughtObject(
                    id=str(report_object.get("id")),
                    dominant_theme=str(report_object.get("dominant_theme") or report_object.get("topic_seed") or "thought"),
                    fragments=tuple(report_object.get("fragments") or ("report.fragment",)),
                    summary=str(report_object.get("summary") or report_object.get("partial_text") or "thought"),
                    confidence=float(report_object.get("confidence", 0.0)),
                    surface_pressure=float(report_object.get("surface_pressure", report_object.get("pressure", 0.0))),
                    suppressed_pressure=float(report_object.get("suppressed_pressure", 0.0)),
                    evidence_ids=tuple(report_object.get("evidence_ids") or report_object.get("evidence") or ()),
                    recommended_route=str(report_object.get("recommended_route") or "suppress"),
                    privacy_level=str(report_object.get("privacy_level") or "normal"),
                    tags=tuple(report_object.get("tags") or ()),
                    created_at=float(report_object.get("created_at", 0.0) or 0.0),
                )
            except Exception:
                return None
        return None
