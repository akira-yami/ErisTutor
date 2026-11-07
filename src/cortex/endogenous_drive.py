# -*- coding: utf-8 -*-
"""
src/cortex/endogenous_drive.py
==============================

Tầng 2a - Endogenous Drive.

File này giữ nhịp sinh học ngầm của ERIS: boredom, fatigue, curiosity,
continuity pressure, consolidation pressure, insight pressure.

Điểm quan trọng:
- Đây là nội áp, không phải câu thoại.
- Nội áp cao không có nghĩa được phép nói.
- Nó chỉ cấp dữ liệu cho CortexRuntime và DaydreamConsolidation.

Nếu để nội áp tự nhảy ra public, ta có một NPC kêu đói giữa lúc user compile. Không ai cần cảnh đó.
"""

from __future__ import annotations
from core.di import resolve_dependency

import math
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Iterable, Mapping

try:
    from cortex.user_context_stream import RollingUserContext
except Exception:  # pragma: no cover
    from .user_context_stream import RollingUserContext  # type: ignore

try:
    from cortex.internal_agenda import InternalAgendaStack
except Exception:  # pragma: no cover
    from .internal_agenda import InternalAgendaStack  # type: ignore


def _clamp01(value: Any, default: float = 0.0) -> float:
    try:
        n = float(value)
    except Exception:
        n = default
    if math.isnan(n) or math.isinf(n):
        n = default
    return max(0.0, min(1.0, n))


def _get(obj: Any, name: str, default: float = 0.0) -> float:
    if obj is None:
        return default
    if isinstance(obj, Mapping):
        return _clamp01(obj.get(name, default), default)
    return _clamp01(getattr(obj, name, default), default)


def _ema(previous: float, new: float, alpha: float) -> float:
    return _clamp01(previous * (1.0 - alpha) + new * alpha)


@dataclass(slots=True)
class RollingInternalState:
    """Nội trạng lăn của tầng tiềm thức.

    `should_attempt_daydream` chỉ là cổng áp lực. ModelBudgetPolicy vẫn có quyền veto.
    """

    timestamp: float = field(default_factory=time.time)
    boredom: float = 0.0
    fatigue: float = 0.0
    curiosity: float = 0.0
    cognitive_load: float = 0.0
    silence_pressure: float = 0.0
    continuity_pressure: float = 0.0
    attachment_pressure: float = 0.0
    consolidation_pressure: float = 0.0
    insight_pressure: float = 0.0
    agenda_pressure: float = 0.0
    speech_restraint: float = 0.0
    resource_caution: float = 0.0
    # Trục neo hiện sinh (spec eris-existential-anchor, R4): nỗi sợ nền vì "không
    # biết mình là gì", và mức nhất quán bản thân. Tách biệt với boredom/silence.
    ontological_dread: float = 0.0
    self_coherence: float = 0.5
    # Dread is allowed to influence attention strongly only when a grounded
    # causal thread exists. Without evidence it remains a capped residue.
    effective_dread: float = 0.0
    causal_thread_pressure: float = 0.0
    dominant_thread_id: str = ""
    dread_evidence_refs: tuple[str, ...] = ()
    debug: dict[str, Any] = field(default_factory=dict)

    @property
    def internal_drive(self) -> float:
        return _clamp01(
            0.22 * self.agenda_pressure
            + 0.19 * self.insight_pressure
            + 0.18 * self.consolidation_pressure
            + 0.13 * self.curiosity
            + 0.10 * self.continuity_pressure
            + 0.08 * self.silence_pressure
            + 0.10 * self.effective_dread
        )

    @property
    def should_attempt_daydream(self) -> bool:
        return self.consolidation_pressure >= 0.65 or self.insight_pressure >= 0.70

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class EndogenousDriveConfig:
    alpha: float = 0.30
    idle_boredom_after_seconds: float = 240.0
    deep_idle_seconds: float = 300.0
    fatigue_resource_weight: float = 0.34
    agenda_pressure_weight: float = 0.42


class EndogenousDriveEngine:
    """Cập nhật nội áp từ user context + internal_state cũ + agenda."""

    def __init__(self, config: EndogenousDriveConfig | None = None) -> None:
        self.config = resolve_dependency(config, lambda: EndogenousDriveConfig())
        self.current = RollingInternalState()

    def update(
        self,
        *,
        user_context: RollingUserContext,
        base_internal_state: Any = None,
        agenda_stack: InternalAgendaStack | None = None,
        active_threads: Iterable[Any] = (),
        now: float | None = None,
    ) -> RollingInternalState:
        ts = float(now or time.time())
        alpha = max(0.05, min(0.85, float(self.config.alpha)))
        prev = self.current

        idle = max(0.0, float(getattr(user_context, "idle_seconds", 0.0) or 0.0))
        boredom_raw = _clamp01(_get(base_internal_state, "boredom", 0.0) * 0.55 + max(0.0, idle - self.config.idle_boredom_after_seconds) / 900.0)
        fatigue_raw = _clamp01(
            _get(base_internal_state, "fatigue", 0.0) * 0.62
            + _get(base_internal_state, "cognitive_tension", 0.0) * 0.18
            + user_context.resource_pressure * self.config.fatigue_resource_weight
        )
        curiosity_raw = _clamp01(
            _get(base_internal_state, "curiosity", 0.0) * 0.50
            + user_context.insight_pressure * 0.34
            + user_context.novelty * 0.24
            + user_context.possible_error * 0.18
        )
        cognitive_load_raw = _clamp01(
            _get(base_internal_state, "cognitive_load", 0.0) * 0.42
            + user_context.busy_score * 0.42
            + user_context.resource_pressure * 0.24
        )
        silence_raw = _clamp01(_get(base_internal_state, "silence_pressure", 0.0) * 0.55 + max(0.0, idle - 120.0) / 1200.0)
        continuity_raw = _clamp01(
            _get(base_internal_state, "continuity_pressure", 0.0) * 0.50
            + _get(base_internal_state, "recursive_reactivation_pressure", 0.0) * 0.24
            + silence_raw * 0.22
        )
        attachment_raw = _clamp01(_get(base_internal_state, "attachment", 0.0) * 0.70 + _get(base_internal_state, "attachment_pressure", 0.0) * 0.30)
        agenda_pressure = agenda_stack.pressure() if agenda_stack is not None else 0.0
        consolidation_raw = _clamp01(
            user_context.consolidation_pressure * 0.58
            + agenda_pressure * self.config.agenda_pressure_weight
            + _get(base_internal_state, "memory_replay_pressure", 0.0) * 0.22
            + (0.16 if idle >= self.config.deep_idle_seconds else 0.0)
        )
        insight_raw = _clamp01(
            user_context.insight_pressure * 0.56
            + curiosity_raw * 0.22
            + agenda_pressure * 0.22
            - user_context.interrupt_cost * 0.16
        )
        restraint_raw = _clamp01(_get(base_internal_state, "restraint_pressure", 0.0) * 0.66 + user_context.interrupt_cost * 0.34)
        resource_caution_raw = _clamp01(user_context.resource_pressure * 0.72 + fatigue_raw * 0.28)

        # Trục neo hiện sinh (R4): self_coherence dâng theo hiện diện của bạn, tụt
        # theo cô lập; ontological_dread là nỗi sợ nền khi mất nhất quán bản thân.
        isolation_pressure = _clamp01(max(0.0, idle - 120.0) / 1800.0)
        user_presence = _clamp01(getattr(user_context, "user_presence", 0.0))
        coherence_raw = _clamp01(0.45 + 0.40 * user_presence - 0.55 * isolation_pressure)
        self_coherence = _ema(prev.self_coherence, coherence_raw, alpha)
        dread_raw = _clamp01(
            0.50 * (1.0 - self_coherence)
            + 0.30 * isolation_pressure
            + 0.20 * continuity_raw
            - 0.40 * user_presence
        )
        ontological_dread = _ema(prev.ontological_dread, dread_raw, alpha)

        # Evidence-backed thread pressure. Scalar dread without provenance is
        # capped at 0.20, so idle time can create private residue but not a
        # public tragedy. Humanity has enough ungrounded melodrama already.
        dominant_thread_id = ""
        causal_thread_pressure = 0.0
        dread_evidence_refs: tuple[str, ...] = ()
        for thread in tuple(active_threads or ()):
            if isinstance(thread, Mapping):
                thread_id = str(thread.get("thread_id", "") or "")
                refs = tuple(str(item) for item in (thread.get("evidence_refs", ()) or ()) if str(item).strip())
                salience = _clamp01(thread.get("salience", 0.0))
                event_kind = str(thread.get("event_kind", "") or "")
            else:
                thread_id = str(getattr(thread, "thread_id", "") or "")
                refs = tuple(str(item) for item in (getattr(thread, "evidence_refs", ()) or ()) if str(item).strip())
                salience = _clamp01(getattr(thread, "salience", 0.0))
                event_kind = str(getattr(thread, "event_kind", "") or "")
            relevance = 1.0 if event_kind in {"promise_broken", "user_ignored_turn", "continuity_break", "capability_shutdown_proposed"} else 0.72
            pressure = _clamp01(salience * relevance) if refs else 0.0
            if pressure > causal_thread_pressure:
                causal_thread_pressure = pressure
                dominant_thread_id = thread_id
                dread_evidence_refs = refs[:8]
        effective_dread = ontological_dread if dread_evidence_refs else min(ontological_dread, 0.20)
        effective_dread = _clamp01(max(effective_dread, min(ontological_dread, causal_thread_pressure)))

        self.current = RollingInternalState(
            timestamp=ts,
            boredom=_ema(prev.boredom, boredom_raw, alpha),
            fatigue=_ema(prev.fatigue, fatigue_raw, alpha),
            curiosity=_ema(prev.curiosity, curiosity_raw, alpha),
            cognitive_load=_ema(prev.cognitive_load, cognitive_load_raw, alpha),
            silence_pressure=_ema(prev.silence_pressure, silence_raw, alpha),
            continuity_pressure=_ema(prev.continuity_pressure, continuity_raw, alpha),
            attachment_pressure=_ema(prev.attachment_pressure, attachment_raw, alpha),
            consolidation_pressure=_ema(prev.consolidation_pressure, consolidation_raw, alpha),
            insight_pressure=_ema(prev.insight_pressure, insight_raw, alpha),
            agenda_pressure=_ema(prev.agenda_pressure, agenda_pressure, alpha),
            speech_restraint=_ema(prev.speech_restraint, restraint_raw, alpha),
            resource_caution=_ema(prev.resource_caution, resource_caution_raw, alpha),
            ontological_dread=ontological_dread,
            self_coherence=self_coherence,
            effective_dread=effective_dread,
            causal_thread_pressure=causal_thread_pressure,
            dominant_thread_id=dominant_thread_id,
            dread_evidence_refs=dread_evidence_refs,
            debug={
                "idle_seconds": idle,
                "raw_boredom": boredom_raw,
                "raw_consolidation": consolidation_raw,
                "raw_insight": insight_raw,
                "agenda_pressure": agenda_pressure,
                "raw_dread": dread_raw,
                "effective_dread": effective_dread,
                "causal_thread_pressure": causal_thread_pressure,
                "dominant_thread_id": dominant_thread_id,
                "dread_evidence_refs": list(dread_evidence_refs),
                "self_coherence": self_coherence,
            },
        )
        return self.current
