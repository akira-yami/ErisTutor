# -*- coding: utf-8 -*-
"""Cognitive disturbances, percept collection, memory resonance and micro-policy evidence.

Extracted from RuntimeCoordinator as a domain runtime mixin.
"""
from __future__ import annotations

import re
import time

from cortex.contracts import (
    CognitiveDisturbance, PerceptEvent, SocialPresenceEvent,
    SubconsciousReport, ThoughtStreamReport,
)
from cortex.internal_state import PersistentInternalState
from cortex.observer_contracts import ObserverFrame


class CognitiveEventRuntimeMixin:
    """Coordinator-facing policy surface; lifecycle remains supervised by the owner."""

    def _make_cognitive_disturbance(
        self,
        *,
        channel: str,
        kind: str,
        content: str,
        evidence: tuple[str, ...] = (),
        intensity: float = 1.0,
    ) -> CognitiveDisturbance | None:
        text = re.sub(r"\s+", " ", str(content or "")).strip()
        if not text:
            return None
        return CognitiveDisturbance(
            channel=str(channel or ""),
            kind=str(kind or ""),
            content=text[:240],
            evidence=tuple(str(item) for item in (evidence or ()) if str(item or "").strip())[:6],
            intensity=self._bounded_float(intensity),
            timestamp=time.time(),
            closes_loop=self._looks_like_closure_disturbance(text),
        )


    def _looks_like_closure_disturbance(self, content: str) -> bool:
        normalized = self._normalize_user_message(content)
        return any(
            token in normalized
            for token in (
                "done",
                "fixed",
                "resolved",
                "solved",
                "complete",
                "completed",
                "xong",
                "đã xong",
                "giải quyết",
            )
        )


    def _build_event_disturbances(
        self,
        *,
        channel: str,
        snapshot=None,
        user_text: str = "",
        force_speak: bool = False,
        vision_facts: tuple[str, ...] = (),
    ) -> tuple[CognitiveDisturbance, ...]:
        disturbances: list[CognitiveDisturbance] = []
        if user_text:
            disturbance = self._make_cognitive_disturbance(
                channel=channel,
                kind="user_message",
                content=user_text,
                evidence=("direct_user_input",),
                intensity=1.0,
            )
            if disturbance is not None:
                disturbances.append(disturbance)

        if force_speak:
            disturbance = self._make_cognitive_disturbance(
                channel=channel,
                kind="user_force_speak",
                content="force_speak requested",
                evidence=tuple(vision_facts or ())[:3] or ("ui_request",),
                intensity=0.96,
            )
            if disturbance is not None:
                disturbances.append(disturbance)

        if channel == "autonomous":
            recent_events = tuple(str(item) for item in (getattr(snapshot, "recent_events", ()) or ())[:3])
            evidence = tuple(str(item) for item in (getattr(snapshot, "evidence", ()) or ())[:3])
            active = str(getattr(snapshot, "active_window_kind", "") or getattr(snapshot, "active_window", "") or "ambient")
            idle = float(getattr(snapshot, "idle_seconds", 0.0) or 0.0)
            content = f"autonomous tick: focus={active}; idle={int(idle)}s"
            if recent_events:
                content = f"{content}; events={', '.join(recent_events[:2])}"
            intensity = 0.34 + min(0.18, idle / 1800.0)
            if recent_events or evidence or vision_facts:
                intensity += 0.16
            disturbance = self._make_cognitive_disturbance(
                channel=channel,
                kind="autonomous_tick",
                content=content,
                evidence=tuple(dict.fromkeys((*recent_events, *evidence, *tuple(vision_facts or ()))))[:6],
                intensity=intensity,
            )
            if disturbance is not None:
                disturbances.append(disturbance)

        return tuple(disturbances)


    def _disturb_cognitive_substrate(
        self,
        *,
        channel: str,
        snapshot=None,
        user_text: str = "",
        force_speak: bool = False,
        vision_facts: tuple[str, ...] = (),
        consume_surface: bool = False,
    ) -> ThoughtStreamReport:
        disturbances = self._build_event_disturbances(
            channel=channel,
            snapshot=snapshot,
            user_text=user_text,
            force_speak=force_speak,
            vision_facts=vision_facts,
        )
        substrate = getattr(self, "cognitive_substrate", self.thought_stream)
        report = substrate.latest_report() if callable(getattr(substrate, "latest_report", None)) else ThoughtStreamReport()
        injector = getattr(substrate, "inject_disturbances", None)
        if disturbances and callable(injector):
            report = injector(disturbances, immediate=True, queue_surface=False)
        if consume_surface and not getattr(getattr(report, "latest_seed", None), "mode", ""):
            pop_surface = getattr(substrate, "pop_surface", None)
            if callable(pop_surface):
                report = self._with_thought_stream_surface(report, pop_surface())
        event_strength = max(
            (max(0.0, min(1.0, float(getattr(item, "intensity", 0.0) or 0.0))) for item in tuple(disturbances or ())),
            default=0.0,
        )
        try:
            internal_state = self._get_internal_state()
        except Exception:
            internal_state = None
        if internal_state is not None and snapshot is not None:
            self._maybe_tick_subconscious_runtime(
                channel=channel,
                snapshot=snapshot,
                internal_state=internal_state,
                user_text=user_text,
                event_text=user_text,
                vision_facts=vision_facts,
                event_strength=event_strength,
                allow_lane5=True,
                reflection=False,
            )
        return report


    def _with_thought_stream_surface(self, report: ThoughtStreamReport, surface) -> ThoughtStreamReport:
        if surface is None or not getattr(surface, "mode", ""):
            return report
        return ThoughtStreamReport(
            latest_seed=surface,
            recent_seeds=getattr(report, "recent_seeds", ()),
            surface_pressure=max(float(getattr(report, "surface_pressure", 0.0) or 0.0), float(getattr(surface, "pressure", 0.0) or 0.0)),
            suppression_reason="",
            latest_thought=getattr(report, "latest_thought", None) or ThoughtStreamReport().latest_thought,
            recent_thoughts=getattr(report, "recent_thoughts", ()),
            momentum=getattr(report, "momentum", None) or ThoughtStreamReport().momentum,
            recent_momentum=getattr(report, "recent_momentum", ()),
            active_attractor=getattr(report, "active_attractor", None) or ThoughtStreamReport().active_attractor,
            unresolved_loops=getattr(report, "unresolved_loops", ()),
            stream_tension=float(getattr(report, "stream_tension", 0.0) or 0.0),
            unresolved_count=int(getattr(report, "unresolved_count", 0) or 0),
            continuity_notes=tuple(getattr(report, "continuity_notes", ()) or ()),
            disturbances=tuple(getattr(report, "disturbances", ()) or ()),
        )


    def _pop_thought_stream_report_for_autonomous(self) -> ThoughtStreamReport:
        surface = self.thought_stream.pop_surface()
        latest = self.thought_stream.latest_report()
        if not surface.mode:
            return ThoughtStreamReport(
                recent_seeds=latest.recent_seeds,
                surface_pressure=latest.surface_pressure,
                suppression_reason=latest.suppression_reason or "no_surface_seed",
                latest_thought=latest.latest_thought,
                recent_thoughts=latest.recent_thoughts,
                momentum=latest.momentum,
                recent_momentum=latest.recent_momentum,
                active_attractor=latest.active_attractor,
                unresolved_loops=latest.unresolved_loops,
                stream_tension=latest.stream_tension,
                unresolved_count=latest.unresolved_count,
                continuity_notes=latest.continuity_notes,
                disturbances=latest.disturbances,
            )
        return ThoughtStreamReport(
            latest_seed=surface,
            recent_seeds=latest.recent_seeds,
            surface_pressure=surface.pressure,
            suppression_reason="",
            latest_thought=latest.latest_thought,
            recent_thoughts=latest.recent_thoughts,
            momentum=latest.momentum,
            recent_momentum=latest.recent_momentum,
            active_attractor=latest.active_attractor,
            unresolved_loops=latest.unresolved_loops,
            stream_tension=latest.stream_tension,
            unresolved_count=latest.unresolved_count,
            continuity_notes=latest.continuity_notes,
            disturbances=latest.disturbances,
        )


    def _pop_subconscious_report_for_autonomous(self) -> SubconsciousReport:
        runtime = getattr(self, "subconscious_runtime", None)
        if runtime is not None and self.subconscious_runtime_enabled:
            return runtime.latest_report()
        return self._pop_thought_stream_report_for_autonomous()


    def _save_memory_state(self) -> None:
        if self.memory_state is None:
            return
        saver = getattr(self.memory_core, "save", None)
        if not callable(saver):
            return
        try:
            saver(self.memory_state)
        except Exception as exc:
            self.logger.warning("Memory state save error: %s", exc)


    def _memory_recall_texts(self, *, snapshot=None) -> list[str]:
        recall = getattr(self.memory_core, "recall", None)
        if callable(recall):
            try:
                query = self._memory_query_from_snapshot(snapshot)
                packet = recall(query)
                texts: list[str] = []
                for name in ("repair", "procedural", "semantic", "relational", "affective_residue", "episodic"):
                    texts.extend(str(item) for item in getattr(packet, name, ()) or ())
                # Tri thức đã học từ gia sư (tag 'tutor') được dán nhãn rõ và đưa lên
                # đầu để LLM thấy điều ERIS đã học — đường học -> hành vi THẬT, không
                # chỉ nằm trong shadow prediction.
                learned = self._recall_learned_knowledge(snapshot)
                if learned:
                    texts = learned + texts
                return texts
            except Exception as exc:
                self.logger.warning("Memory recall error: %s", exc)
        return list(getattr(self, "long_term_memory", []) or []) #nếu không có bộ nhớ cấu trúc hoặc truy vấn lỗi, thì fallback về long_term_memory đã lưu ở coordinator, vốn được cập nhật sau mỗi chu kỳ


    def _recall_learned_knowledge(self, snapshot=None) -> list[str]:
        """Lấy claim đã học từ gia sư liên quan ngữ cảnh hiện tại, dán nhãn [đã học]."""
        provider = getattr(self, "tutor_knowledge_provider", None)
        if provider is None or not callable(getattr(provider, "learned_claims", None)):
            return []
        try:
            query = self._memory_query_from_snapshot(snapshot)
            claims = provider.learned_claims(query, limit=2)
        except Exception:
            return []
        return [f"[đã học] {c}" for c in claims if str(c or "").strip()]


    def _activate_memory_resonance(
        self,
        *,
        snapshot=None,
        user_text: str = "",
        channel: str = "",
        fragments: tuple[str, ...] = (),
    ):
        recall = getattr(self.memory_core, "recall", None)
        if not callable(recall):
            self.active_memory_resonance = None
            return None
        try:
            query = " ".join(
                str(item)
                for item in (channel, user_text, *tuple(fragments or ()), self._memory_query_from_snapshot(snapshot))
                if str(item or "").strip()
            )
            report = recall(query)
        except Exception as exc:
            self.logger.warning("Memory resonance activation error: %s", exc)
            self.active_memory_resonance = None
            return None
        self.active_memory_resonance = report
        return report


    def _thought_stream_resonance_fragments(self) -> tuple[str, ...]:
        report = self.thought_stream.latest_report()
        thought = getattr(report, "latest_thought", None)
        momentum = getattr(report, "momentum", None)
        attractor = getattr(report, "active_attractor", None)
        fragments: list[str] = []
        if thought is not None:
            fragments.extend(
                str(item)
                for item in (
                    getattr(thought, "topic_seed", ""),
                    getattr(thought, "partial_text", ""),
                    getattr(thought, "unresolved_reason", ""),
                    getattr(thought, "source_memory", ""),
                    *tuple(getattr(thought, "associations", ()) or ())[:3],
                )
                if str(item or "").strip()
            )
        if momentum is not None and getattr(momentum, "unresolved", False):
            fragments.append(
                " ".join(
                    str(item)
                    for item in (
                        getattr(momentum, "topic_seed", ""),
                        getattr(momentum, "affordance", ""),
                        getattr(momentum, "closure_state", ""),
                    )
                    if str(item or "").strip()
                )
            )
        if attractor is not None and getattr(attractor, "attractor", False):
            fragments.append(
                " ".join(
                    str(item)
                    for item in (
                        getattr(attractor, "topic_seed", ""),
                        "unresolved_tension_loop",
                    )
                    if str(item or "").strip()
                )
            )
        return tuple(item for item in fragments[-8:] if item)


    def _memory_attention_lines(self) -> tuple[str, ...]:
        report = getattr(self, "active_memory_resonance", None)
        to_attention_lines = getattr(report, "to_attention_lines", None)
        if not callable(to_attention_lines):
            return ()
        try:
            return tuple(to_attention_lines())
        except Exception as exc:
            self.logger.warning("Memory resonance attention render error: %s", exc)
            return ()


    def _norm_deviation_texts(self, snapshot) -> tuple[str, ...]:
        return ()


    def _note_norm_observation(self, snapshot) -> None:
        return


    def _bounded_float(value: object, *, minimum: float = 0.0, maximum: float = 1.0) -> float:
        try:
            number = float(value)
        except (TypeError, ValueError):
            number = minimum
        if number != number:
            number = minimum
        return max(minimum, min(maximum, number))


    def _event_timestamp(self, snapshot, *, started_at: float | None = None) -> str:
        captured_at = str(getattr(snapshot, "captured_at", "") or getattr(snapshot, "time", "") or "").strip()
        if captured_at:
            return captured_at
        return time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime(started_at or time.time()))


    def _normalize_event_evidence(self, evidence, *, limit: int = 4) -> tuple[str, ...]:
        items: list[str] = []
        seen: set[str] = set()
        for item in evidence or ():
            text = str(item or "").strip()
            if not text:
                continue
            key = text.lower()
            if key in seen:
                continue
            items.append(text[:160])
            seen.add(key)
            if len(items) >= limit:
                break
        return tuple(items)


    def _make_percept_event(
        self,
        *,
        #Nhận các field
        kind: str,
        content: str,
        timestamp: str,
        evidence=(),
        confidence: float = 1.0,
        source: str = "runtime",
        privacy_level: str = "internal",
        novelty_hint: str = "routine",
    ) -> PerceptEvent | None:
        text = str(content or "").strip()
        if not text:
            return None
        return PerceptEvent(
            kind=str(kind or "unknown").strip() or "unknown",
            content=text[:260],
            evidence=self._normalize_event_evidence(evidence),
            confidence=self._bounded_float(confidence),
            source=str(source or "runtime").strip() or "runtime",
            timestamp=timestamp,
            privacy_level=str(privacy_level or "internal").strip() or "internal",
            novelty_hint=str(novelty_hint or "routine").strip() or "routine",
        )


    def _telemetry_event_privacy_level(self, kind: str) -> str:
        if kind == "resource_pressure":
            return "system_metric" #chỉ số hệ thống kiểu CPU/RAM
        if kind == "idle_state": #trạng thái hoạt động/nhàn rỗi
            return "activity_state"
        return "local_context" #ngữ cảnh cục bộ như cửa sổ đang active, focus summary, recent events, evidence


    def _telemetry_event_novelty_hint(self, kind: str, content: str) -> str:
        if kind == "resource_pressure": #threshold_crossed vượt ngưỡng
            return "threshold_crossed"
        if kind == "idle_state":
            return "duration_threshold"
        if kind in {"recent_event", "evidence"}:
            if kind == "recent_event" and self._is_generic_screen_event(content):
                return "routine_telemetry" #telemetry lặp/nhạt, như "đã mở Chrome" hay "đang ở desktop"
            if kind == "evidence" and self._is_generic_screen_evidence(content):
                return "routine_telemetry" # telemetry lặp/nhạt
            return "concrete_telemetry" #telemetry cụ thể, đáng chú ý hơn
        if kind in {"active_window", "focus_context"}:
            return "ambient_context"
        return "routine"


    def _collect_percept_events(
        self,
        *,
        channel: str,
        snapshot,
        user_text: str = "",
        vision_facts: tuple[str, ...] = (),
        force_speak: bool = False,
        internal_state: PersistentInternalState | None = None,
        thought_stream_report=None,
        social_presence_event: SocialPresenceEvent | None = None,
        observer_frame: ObserverFrame | None = None,
        started_at: float | None = None,
    ) -> tuple[PerceptEvent, ...]:
        percepts: list[PerceptEvent] = []
        timestamp = self._event_timestamp(snapshot, started_at=started_at)

        def add(
            kind: str,
            content: str,
            *,
            source: str = "runtime",
            confidence: float = 1.0,
            evidence=(),
            privacy_level: str = "internal",
            novelty_hint: str = "routine",
        ) -> None:
            event = self._make_percept_event(
                kind=kind,
                content=content,
                evidence=evidence,
                confidence=confidence,
                source=source,
                timestamp=timestamp,
                privacy_level=privacy_level,
                novelty_hint=novelty_hint,
            )
            if event is not None:
                percepts.append(event)

        if user_text:
            add(
                "user_message",
                user_text,
                source="chat",
                confidence=1.0,
                privacy_level="user_private",
                novelty_hint="direct_user_input",
            )
        if force_speak:
            add(
                "user_force_speak",
                "force_speak requested",
                source="ui",
                confidence=1.0,
                privacy_level="internal",
                novelty_hint="explicit_request",
            )

        if observer_frame is not None:
            observer_content = self._observer_percept_content(observer_frame)
            if observer_content:
                add(
                    "background_observer",
                    observer_content,
                    source="observer",
                    confidence=max(0.35, self._bounded_float(getattr(observer_frame, "confidence", 0.0))),
                    evidence=self._observer_attention_tags(observer_frame, channel=channel),
                    privacy_level=str(getattr(observer_frame, "privacy_level", "normal") or "normal"),
                    novelty_hint=str(getattr(observer_frame, "recommended_action", "observe_only") or "observe_only"),
                )

        if social_presence_event is not None and bool(getattr(social_presence_event, "present", False)):
            kind = str(getattr(social_presence_event, "kind", "") or "presence")
            reason = str(getattr(social_presence_event, "reason", "") or "")
            content = f"kind={kind}; speech_allowed={bool(getattr(social_presence_event, 'speech_allowed', False))}"
            if reason:
                content += f"; reason={reason}"
            add(
                "social_presence_event",
                content,
                source="internal_state",
                confidence=max(0.45, self._bounded_float(getattr(social_presence_event, "confidence", 0.0))),
                evidence=tuple(getattr(social_presence_event, "evidence", ()) or ())[:6],
                privacy_level="internal",
                novelty_hint="social_presence",
            )

        active_window = getattr(snapshot, "active_window", "") or getattr(snapshot, "active_app", "")
        add(
            "active_window",
            active_window,
            source="telemetry",
            confidence=0.72,
            privacy_level=self._telemetry_event_privacy_level("active_window"),
            novelty_hint=self._telemetry_event_novelty_hint("active_window", str(active_window)),
        )
        focus_summary = getattr(snapshot, "focus_summary", "")
        add(
            "focus_context",
            focus_summary,
            source="telemetry",
            confidence=0.68,
            privacy_level=self._telemetry_event_privacy_level("focus_context"),
            novelty_hint=self._telemetry_event_novelty_hint("focus_context", str(focus_summary)),
        )

        for deviation_text in self._norm_deviation_texts(snapshot):
            add(
                "norm_deviation",
                deviation_text,
                source="memory",
                confidence=0.66,
                evidence=("norm_memory",),
                privacy_level="internal",
                novelty_hint="baseline_deviation",
            )

        idle_seconds = float(getattr(snapshot, "idle_seconds", 0.0) or 0.0)
        if idle_seconds >= 180:
            idle_content = f"idle={int(idle_seconds)}s ({getattr(snapshot, 'idle_label', '') or 'idle'})"
            add(
                "idle_state",
                idle_content,
                source="telemetry",
                confidence=0.64,
                privacy_level=self._telemetry_event_privacy_level("idle_state"),
                novelty_hint=self._telemetry_event_novelty_hint("idle_state", idle_content),
            )

        vision_fact_keys = {str(item or "").strip().lower() for item in tuple(vision_facts or ())}
        for event in tuple(getattr(snapshot, "recent_events", ()) or ())[:6]:
            event_text = str(event)
            if event_text.strip().lower() in vision_fact_keys:
                continue
            add(
                "recent_event",
                event_text,
                source="telemetry",
                confidence=0.76,
                privacy_level=self._telemetry_event_privacy_level("recent_event"),
                novelty_hint=self._telemetry_event_novelty_hint("recent_event", event_text),
            )
        for item in tuple(getattr(snapshot, "evidence", ()) or ())[:6]:
            evidence_text = str(item)
            add(
                "evidence",
                evidence_text,
                source="telemetry",
                confidence=0.72,
                privacy_level=self._telemetry_event_privacy_level("evidence"),
                novelty_hint=self._telemetry_event_novelty_hint("evidence", evidence_text),
            )
        for fact in tuple(vision_facts or ())[: self.vision_fact_max_items]:
            add(
                "vision_fact",
                str(fact),
                source="vision",
                confidence=0.86,
                evidence=(self.vision_last_trace or "vision_facts",),
                privacy_level="screen_context",
                novelty_hint=getattr(self, "vision_facts_novelty_hint", "") or "screen_fact",
            )

        for disturbance in tuple(getattr(thought_stream_report, "disturbances", ()) or ())[:3]:
            disturbance_channel = str(getattr(disturbance, "channel", "") or "")
            disturbance_kind = str(getattr(disturbance, "kind", "") or "")
            disturbance_content = str(getattr(disturbance, "content", "") or "")
            if not disturbance_content:
                continue
            add(
                "substrate_disturbance",
                f"{disturbance_channel}/{disturbance_kind}: {disturbance_content}",
                source="cognitive_substrate",
                confidence=max(0.45, self._bounded_float(getattr(disturbance, "intensity", 0.0))),
                evidence=tuple(getattr(disturbance, "evidence", ()) or ())[:6],
                privacy_level="internal" if disturbance_kind != "user_message" else "user_private",
                novelty_hint="input_disturbance",
            )

        momentum = getattr(thought_stream_report, "momentum", None)
        active_attractor = getattr(thought_stream_report, "active_attractor", None)
        if (
            active_attractor is not None
            and bool(getattr(active_attractor, "attractor", False))
            and float(getattr(active_attractor, "score", 0.0) or 0.0) >= 0.64
        ):
            loop_id = str(getattr(active_attractor, "loop_id", "") or "")
            topic_seed = str(getattr(active_attractor, "topic_seed", "") or "")
            add(
                "cognitive_attractor",
                f"loop={loop_id}; topic={topic_seed}",
                source="thought_stream",
                confidence=max(0.76, self._bounded_float(getattr(active_attractor, "score", 0.0))),
                evidence=tuple(getattr(active_attractor, "evidence", ()) or ())[:6],
                privacy_level="internal",
                novelty_hint="cognitive_attractor",
            )

        if (
            momentum is not None
            and str(getattr(momentum, "affordance", "") or "") == "attention"
            and float(getattr(momentum, "score", 0.0) or 0.0) >= 0.58
        ):
            topic_seed = str(getattr(momentum, "topic_seed", "") or "")
            mode = str(getattr(momentum, "mode", "") or "")
            thought_id = str(getattr(momentum, "thought_id", "") or "")
            add(
                "cognitive_momentum",
                f"thought={thought_id}; mode={mode}; topic={topic_seed}",
                source="thought_stream",
                confidence=max(0.72, self._bounded_float(getattr(momentum, "score", 0.0))),
                evidence=tuple(getattr(momentum, "evidence", ()) or ())[:6],
                privacy_level="internal",
                novelty_hint="cognitive_momentum",
            )

        goal_state = getattr(internal_state, "goal_persistence", None) if internal_state is not None else None
        if goal_state is not None and bool(getattr(goal_state, "active", False)):
            summary = str(getattr(goal_state, "summary", "") or "")
            strength = float(getattr(goal_state, "strength", 0.0) or 0.0)
            urgency = float(getattr(goal_state, "urgency", 0.0) or 0.0)
            if summary:
                add(
                    "persistent_goal",
                    f"goal={summary}; strength={strength:.2f}; urgency={urgency:.2f}",
                    source="internal_state",
                    confidence=max(0.70, self._bounded_float(strength)),
                    evidence=tuple(getattr(goal_state, "evidence", ()) or ())[:6],
                    privacy_level="internal",
                    novelty_hint="persistent_goal",
                )

        add(
            "cycle_channel",
            channel,
            source="runtime",
            confidence=0.50,
            privacy_level="internal",
            novelty_hint="cycle_marker",
        )
        return tuple(percepts[:18])


    def _micro_policy_anchor_hints(self, *, broadcast=None, snapshot=None) -> tuple[str, str]:
        user_action = ""
        session_context = ""
        if broadcast is not None:
            for item in (broadcast, *tuple(getattr(broadcast, "items", ()) or ())):
                label = str(getattr(item, "label", "") or "").strip().lower()
                content = str(getattr(item, "content", "") or "").strip()
                if not content:
                    continue
                if not user_action and label in {"user_action", "action_context", "recent_event"}:
                    user_action = content
                if not session_context and label in {"focus_context", "session_context", "active_window", "focus_summary"}:
                    session_context = content
        if snapshot is not None:
            if not user_action:
                recent_events = tuple(
                    str(item or "").strip().replace("_", " ")
                    for item in (getattr(snapshot, "recent_events", ()) or ())
                    if str(item or "").strip()
                )
                user_action = next((item for item in recent_events if item), "")
            if not session_context:
                session_context = str(
                    getattr(snapshot, "focus_summary", "")
                    or getattr(snapshot, "active_window", "")
                    or getattr(snapshot, "active_app", "")
                    or getattr(snapshot, "active_window_kind", "")
                    or ""
                ).strip()
        return user_action, session_context


__all__ = ["CognitiveEventRuntimeMixin"]
