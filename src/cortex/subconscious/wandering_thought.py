# -*- coding: utf-8 -*-
"""WanderingThoughtEngine: spark -> drift -> association -> fragment.

Engine này chạy nhẹ theo tick. Nó không gọi SurfaceArbiter, không gọi LLM, không
phát public speech. Nó chỉ tạo ThoughtSpark, nuôi DriftPool, rồi đôi khi kết tinh
ThoughtFragment để Blackboard/Synthesizer/Rehearsal xử tiếp.
"""

from __future__ import annotations
from core.di import resolve_dependency

from typing import Any

from .blackboard import SubconsciousBlackboard
from .drift_pool import DriftPool
from .free_association import FreeAssociationEngine
from .thought_fragment import ThoughtFragment
from .thought_spark import ThoughtSpark, clamp01, clean_text, now_timestamp
from .topic_seed_pool import TopicSeedPool
from .wandering_contracts import AssociationPath, WanderingThoughtResult
from .wandering_topic_memory import WanderingTopicMemory


def _get(obj: Any, path: str, default: Any = None) -> Any:
    current = obj
    for part in str(path or "").split("."):
        if current is None:
            return default
        if isinstance(current, dict):
            current = current.get(part, default)
        else:
            current = getattr(current, part, default)
    return current if current is not None else default


class WanderingThoughtEngine:
    """Tầng ý nghĩ vẩn vơ có continuity."""

    STATUS_PING_PATTERNS = (
        "tôi đang nghĩ",
        "i am thinking",
        "i'm thinking",
        "vẫn ở đây",
        "i am here",
        "presence ping",
        "status ping",
        "idle_seconds=",
        "silent_cycles=",
    )

    def __init__(
        self,
        *,
        seed_pool: TopicSeedPool | None = None,
        drift_pool: DriftPool | None = None,
        association_engine: FreeAssociationEngine | None = None,
        topic_memory: WanderingTopicMemory | None = None,
        min_idle_seconds: float = 25.0,
        min_resonance_for_fragment: float = 0.30,
    ) -> None:
        self.seed_pool = resolve_dependency(seed_pool, lambda: TopicSeedPool())
        self.drift_pool = resolve_dependency(drift_pool, lambda: DriftPool())
        self.association_engine = resolve_dependency(association_engine, lambda: FreeAssociationEngine())
        self.topic_memory = resolve_dependency(topic_memory, lambda: WanderingTopicMemory())
        self.min_idle_seconds = max(0.0, float(min_idle_seconds or 25.0))
        self.min_resonance_for_fragment = clamp01(min_resonance_for_fragment)
        self.last_result = WanderingThoughtResult([], [], [], 0.0, "not_started")

    def tick(
        self,
        *,
        frame: Any = None,
        snapshot: Any = None,
        memory_packet: Any = None,
        internal_state: Any = None,
        blackboard: SubconsciousBlackboard | None = None,
        now: float | None = None,
        allow_fragments: bool = True,
    ) -> WanderingThoughtResult:
        current = now_timestamp() if now is None else float(now)
        idle = self._idle_seconds(snapshot)
        focus = self._focus_family(snapshot=snapshot, frame=frame)
        trigger, trigger_reason = self._trigger_reason(
            frame=frame,
            snapshot=snapshot,
            internal_state=internal_state,
            idle=idle,
        )

        seeds = self.seed_pool.generate(frame=frame, snapshot=snapshot, memory_packet=memory_packet, internal_state=internal_state, now=current)
        sparks = list(self.seed_pool.to_sparks(seeds, now=current))
        self.drift_pool.submit_many(sparks, now=current)
        self.drift_pool.decay(now=current)
        resonance_sparks = list(self.drift_pool.resonate(now=current, limit=3))
        if resonance_sparks:
            sparks.extend(resonance_sparks)
        self.topic_memory.touch_sparks(sparks, now=current)
        self.topic_memory.decay(now=current)

        fragments: list[ThoughtFragment] = []
        topic_candidates: list[str] = []
        associations = self.association_engine.associate(self.drift_pool, now=current, limit=3, allow_weak_random=True)
        if allow_fragments and trigger:
            for association in associations:
                if association.score < self.min_resonance_for_fragment:
                    continue
                topic = self.topic_memory.touch_association(association, now=current)
                topic_candidates.append(topic.theme)
                surface_allowed = self._surface_allowed(association)
                kind = "wandering_surface_candidate" if surface_allowed else "wandering_fragment"
                fragment = association.to_fragment(
                    surface_allowed=surface_allowed,
                    now=current,
                    kind=kind,
                    privacy_level="normal",
                )
                if not self._is_status_ping(fragment.content):
                    fragments.append(fragment)
                    if blackboard is not None:
                        blackboard.submit(fragment)

        mood_delta = self._mood_delta(sparks, fragments, internal_state=internal_state)
        if not topic_candidates:
            topic_candidates = [topic.theme for topic in self.topic_memory.active_topics(limit=5)]
        debug_bits = [trigger_reason, f"idle={idle:.1f}", f"focus={focus}", f"sparks={len(sparks)}", f"fragments={len(fragments)}"]
        result = WanderingThoughtResult(
            sparks=sparks,
            fragments=fragments,
            topic_candidates=list(dict.fromkeys(topic_candidates))[:8],
            mood_delta=mood_delta,
            debug_reason="; ".join(bit for bit in debug_bits if bit),
        )
        self.last_result = result
        return result

    def _trigger_reason(self, *, frame: Any, snapshot: Any, internal_state: Any, idle: float) -> tuple[bool, str]:
        boredom = clamp01(_get(internal_state, "boredom", 0.0))
        solitude = clamp01(_get(internal_state, "solitude", 0.0))
        curiosity = max(clamp01(_get(internal_state, "curiosity", 0.0)), clamp01(_get(internal_state, "mental_processes.curiosity.level", 0.0)))
        repeated = self._has_repeated_runtime_event(frame=frame, snapshot=snapshot)
        drift_ready = bool(self.drift_pool.related_groups(now=now_timestamp(), min_size=2))
        after_debug = self._after_debug(frame=frame, snapshot=snapshot)
        # V66: deep focus của user không được tắt đời sống nội tâm của Eris.
        # Nó có thể đi vào scene/wording ở downstream, không phải cognition veto.
        if idle >= self.min_idle_seconds:
            return True, "idle_light"
        if boredom >= 0.38 or solitude >= 0.32:
            return True, "mood_wandering"
        if repeated:
            return True, "runtime_event_repeated"
        if after_debug:
            return True, "after_debug_long"
        if drift_ready and curiosity >= 0.20:
            return True, "drift_resonance"
        # Vẫn tạo spark riêng, nhưng chưa kết tinh fragment. Nội tâm có thể âm ỉ;
        # không phải tick nào cũng phải ném câu thơ vào blackboard như máy rải hoa.
        return False, "private_simmer_only"

    def _surface_allowed(self, association: AssociationPath) -> bool:
        if self._is_status_ping(association.statement):
            return False
        # Surface ở đây chỉ nghĩa "đủ rõ để trở thành fragment có thể biểu đạt",
        # chưa phải phát public speech. User focus/typing không thuộc contract này.
        grounded_enough = association.groundedness >= 0.34 and association.score >= self.min_resonance_for_fragment
        weird_not_noise = 0.18 <= association.weirdness <= 0.86
        return bool(grounded_enough and weird_not_noise)

    def _idle_seconds(self, snapshot: Any) -> float:
        try:
            return max(0.0, float(_get(snapshot, "idle_seconds", 0.0) or 0.0))
        except Exception:
            return 0.0

    def _focus_family(self, *, snapshot: Any, frame: Any) -> str:
        raw = clean_text(
            _get(snapshot, "focus_kind_hint")
            or _get(snapshot, "focus_kind")
            or _get(snapshot, "active_window_kind")
            or _get(frame, "focus_family")
            or _get(frame, "dominant_focus")
            or "",
            limit=80,
        ).lower()
        if raw in {"coding", "ide", "editor", "code"} or any(token in raw for token in ("vscode", "pycharm", "terminal", "debug")):
            return "code"
        if raw in {"documentation", "docs", "paper"}:
            return "docs"
        if raw in {"focus_aid_media", "youtube", "browser_media", "media", "entertainment"}:
            return "media"
        return raw or "idle"

    def _has_repeated_runtime_event(self, *, frame: Any, snapshot: Any) -> bool:
        for source in (_get(frame, "recent_events", ()), _get(snapshot, "recent_events", ()), _get(frame, "runtime_events", ())):
            try:
                items = tuple(source or ())
            except TypeError:
                items = (source,)
            text = " ".join(clean_text(item, limit=160).lower() for item in items)
            if any(token in text for token in ("repeated", "again", "lặp", "loop", "body_only", "reject", "timeout")):
                return True
        return False

    def _after_debug(self, *, frame: Any, snapshot: Any) -> bool:
        hay = clean_text(
            _get(frame, "session_arc") or _get(snapshot, "session_arc") or _get(frame, "dominant_focus") or "",
            limit=160,
        ).lower()
        return any(token in hay for token in ("debug", "fix", "pytest", "bug", "architecture"))

    def _mood_delta(self, sparks: list[ThoughtSpark], fragments: list[ThoughtFragment], *, internal_state: Any) -> float:
        if not sparks:
            return 0.0
        base = sum(s.affect_tint for s in sparks) / max(1, len(sparks))
        clarity = sum(s.clarity for s in sparks) / max(1, len(sparks))
        fragment_release = 0.04 if fragments else -0.01
        fatigue = clamp01(_get(internal_state, "fatigue", 0.0))
        return max(-1.0, min(1.0, base * 0.22 + clarity * 0.04 + fragment_release - fatigue * 0.03))

    def _is_status_ping(self, text: str) -> bool:
        lowered = clean_text(text, limit=220).lower()
        return any(pattern in lowered for pattern in self.STATUS_PING_PATTERNS)
