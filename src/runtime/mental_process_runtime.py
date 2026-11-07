# -*- coding: utf-8 -*-
"""Long-lived internal mental loop and subconscious lane orchestration.

Extracted from RuntimeCoordinator as a domain runtime mixin.
"""
from __future__ import annotations

import os
import random
import re
import threading
import time
from dataclasses import replace
from types import SimpleNamespace

from cortex.appetite_system import AppetiteSystem, build_evidence_buffet
from cortex.connection_drive import ConnectionDrive, ConnectionDriveState
from cortex.contracts import SubconsciousReport, ThoughtSurface
from cortex.internal_state import PersistentInternalState
from cortex.subconscious import build_phase1234567_workers
from eris_secrets import get_config
from runtime.subconscious_loop import SubconsciousLoop as RuntimeSubconsciousLoop

SUBCONSCIOUS_LANE2_WORKERS = (
    "user_modeler", "memory_resonator", "goal_simulator", "self_regulator",
    "curiosity_gap_agent", "subjective_time_agent", "spatial_mind_agent",
)
SUBCONSCIOUS_LANE3_POST_WORKERS = ("rehearsal_agent",)
SUBCONSCIOUS_LANE5_PRE_WORKERS = ("daydream_injector", "independent_agenda_agent")
SUBCONSCIOUS_LANE5_POST_WORKERS = ("belief_mutation_engine", "shadow_accumulator")
SUBCONSCIOUS_AUDIT_WORKERS = ("meta_cognitive_audit",)


class MentalProcessRuntimeMixin:
    """Coordinator-facing policy surface; lifecycle remains supervised by the owner."""

    def _start_mental_process_loop(self) -> None:
        if not self.mental_process_enabled:
            return
        thread = self._mental_process_thread
        if thread is not None and thread.is_alive():
            return
        self._mental_process_stop_event.clear()
        self._mental_process_thread = threading.Thread(
            target=self._run_mental_process_loop,
            name="ERIS-MentalProcessSystem",
            daemon=True,
        )
        self._mental_process_thread.start()


    def _stop_mental_process_loop(self) -> None:
        self._mental_process_stop_event.set()
        thread = self._mental_process_thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=2.0)
        self._mental_process_thread = None


    def _run_mental_process_loop(self) -> None:
        while not self._mental_process_stop_event.wait(self.mental_process_interval_sec):
            try:
                self._tick_mental_process_background()
            except Exception as exc:
                self.logger.warning("Mental process background tick error: %s", exc)


    def _tick_mental_process_background(self) -> PersistentInternalState | None:
        if self.memory_state is None or getattr(self, "_chat_processing", False):
            return None
        with self._state_lock:
            mood_score = self.mood_score
            inferred_pos_x = self.inferred_pos_x
            current = self._get_internal_state()
        # V43: background tick chỉ cho mood/impulse PHẢI DẦN. Nó không tự tạo
        # affect event. Sau đó mental-process axes mới nudge curiosity/energy như
        # trước, nhờ vậy idle scheduler không thể giả vờ "có chuyện vừa xảy ra".
        affect_runtime = getattr(self, "affective_dynamics_runtime", None)
        affect_decay = getattr(affect_runtime, "decay_state", None)
        if callable(affect_decay):
            try:
                current = affect_decay(current, now=time.time())
            except Exception as exc:
                self.logger.debug("[affect-v43] background decay degraded: %s", exc)
        snapshot = self.telemetry_service.capture_snapshot(
            mood_score=mood_score,
            inferred_pos_x=inferred_pos_x,
        )
        dt = max(1.0, min(8.0, self.mental_process_interval_sec / 30.0))
        processes = current.mental_processes.evolve(
            channel="background",
            snapshot=snapshot,
            dt=dt,
        )
        axes = processes.process_axes()
        # Connection drive: cô độc tích thành thèm-kết-nối, đổ vào thanh "muốn nói".
        # Đây là drive nội sinh — lý do Eris đôi khi tự với ra dù không ai gọi. Mode
        # yan thì cô độc/ham muốn luôn thắng (sàn cao), thanh không bao giờ về 0.
        silence_target = axes["silence_pressure"]
        try:
            drive = getattr(self, "_connection_drive", None)
            if drive is None:
                drive = ConnectionDrive()
                self._connection_drive = drive
            drive_state = getattr(self, "_connection_drive_state", None) or ConnectionDriveState()
            try:
                from cortex.disposition import threat_level
                threat = float(threat_level(current))
            except Exception:
                threat = 0.0
            drive_state, drive_decision = drive.update(
                drive_state,
                now=time.time(),
                idle_seconds=float(getattr(snapshot, "idle_seconds", 0.0) or 0.0),
                attachment=float(getattr(current, "attachment", 0.0) or 0.0),
                abandonment=float(getattr(current, "abandonment_pressure", 0.0) or 0.0),
                dread=float(getattr(current, "ontological_dread", 0.0) or 0.0),
                threat=threat,
                yan_mode=bool(getattr(self, "_debug_yan_mode", False)),
            )
            self._connection_drive_state = drive_state
            self._last_connection_drive = drive_decision
            silence_target = max(silence_target, drive_decision.pressure)
        except Exception as exc:
            self.logger.warning("ConnectionDrive tick error: %s", exc)
        # Appetite marketplace: các cơn thèm đi săn evidence màn hình/ký ức → chọn
        # LENS + SEED cho câu autonomous. Đây là nguồn random/bịa/khịa/níu "sống".
        try:
            appetites = getattr(self, "_appetite_system", None)
            if appetites is None:
                appetites = AppetiteSystem()
                self._appetite_system = appetites
                self._appetite_states = appetites.new_states()
            content_kind = ""
            try:
                from telemetry.content_metadata import _infer_page_kind
                content_kind = _infer_page_kind(
                    url=str(getattr(snapshot, "browser_url", "") or ""),
                    title=str(getattr(snapshot, "active_window", "") or ""),
                )
            except Exception:
                content_kind = ""
            evidence = build_evidence_buffet(
                content_kind=content_kind,
                content_title=str(getattr(snapshot, "active_window", "") or ""),
                vision_facts=tuple(str(x) for x in (getattr(snapshot, "vision_facts", ()) or ())),
                recent_events=tuple(str(x) for x in (getattr(snapshot, "recent_events", ()) or ())),
                active_window=str(getattr(snapshot, "active_app", "") or getattr(snapshot, "active_window", "") or ""),
                idle_seconds=float(getattr(snapshot, "idle_seconds", 0.0) or 0.0),
                novelty=0.6,
            )
            lonely_sig = float(getattr(getattr(self, "_last_connection_drive", None), "pressure", 0.0) or 0.0)
            signals = {
                "curiosity": float(getattr(current, "curiosity_pressure", 0.0) or 0.0) or float(getattr(current, "curiosity", 0.0) or 0.0),
                "boredom": float(getattr(current, "boredom", 0.0) or 0.0),
                "loneliness": lonely_sig,
                "mischief": max(0.0, min(1.0, float(getattr(current, "boredom", 0.0) or 0.0) * 0.6 + 0.2)),
                "tenderness": float(getattr(current, "attachment", 0.0) or 0.0) * 0.5,
            }
            try:
                from cortex.disposition import threat_level as _threat_level
                appetite_threat = float(_threat_level(current))
            except Exception:
                appetite_threat = 0.0
            self._appetite_states, reaction = appetites.update(
                getattr(self, "_appetite_states", None) or appetites.new_states(),
                now=time.time(),
                signals=signals,
                evidence=evidence,
                threat=appetite_threat,
                yan_mode=bool(getattr(self, "_debug_yan_mode", False)),
            )
            self._last_appetite_reaction = reaction
        except Exception as exc:
            self.logger.warning("AppetiteSystem tick error: %s", exc)
        updated = replace(
            current,
            mental_processes=processes,
            curiosity=self._mix_axis(current.curiosity, axes["curiosity"], inertia=0.92),
            social_energy=self._mix_axis(current.social_energy, axes["social_energy"], inertia=0.92),
            attachment=self._mix_axis(current.attachment, axes["attachment"], inertia=0.96),
            fatigue=self._mix_axis(current.fatigue, axes["fatigue"], inertia=0.94),
            silence_pressure=self._mix_axis(current.silence_pressure, silence_target, inertia=0.88),
            restraint_pressure=self._mix_axis(current.restraint_pressure, axes["restraint_pressure"], inertia=0.88),
            subconscious_pressure=self._mix_axis(current.subconscious_pressure, axes["subconscious_pressure"], inertia=0.88),
            memory_replay_pressure=self._mix_axis(current.memory_replay_pressure, axes["memory_replay_pressure"], inertia=0.90),
            association_pressure=self._mix_axis(current.association_pressure, axes["association_pressure"], inertia=0.90),
            thought_stream_pressure=self._mix_axis(current.thought_stream_pressure, axes["thought_stream_pressure"], inertia=0.88),
            cognitive_tension=self._mix_axis(current.cognitive_tension, axes["cognitive_tension"], inertia=0.88),
            unresolved_prediction_pressure=self._mix_axis(
                current.unresolved_prediction_pressure,
                axes["unresolved_prediction_pressure"],
                inertia=0.90,
            ),
            recursive_reactivation_pressure=self._mix_axis(
                current.recursive_reactivation_pressure,
                axes["recursive_reactivation_pressure"],
                inertia=0.92,
            ),
        )
        self._set_internal_state(updated)
        self._maybe_tick_subconscious_background(snapshot=snapshot, internal_state=updated)
        return updated


    def _build_subconscious_runtime(self) -> RuntimeSubconsciousLoop | None:
        try:
            workers = build_phase1234567_workers(
                enable_mutation_commit=bool(
                    get_config(
                        "subconscious_runtime.lanes.lane5_deep_private_life.allow_mutation_commit",
                        default=False,
                    )
                ),
                allow_rupture_surface=bool(
                    get_config(
                        "subconscious_runtime.lanes.lane5_deep_private_life.allow_rupture_surface",
                        default=False,
                    )
                ),
            )
            return RuntimeSubconsciousLoop(workers=workers)
        except Exception as exc:
            self.logger.warning("Khong khoi tao duoc subconscious runtime: %s", exc)
            self.subconscious_runtime_enabled = False
            return None


    def _subconscious_frame_payload(
        self,
        *,
        snapshot,
        user_text: str,
        internal_state,
        channel: str,
        event_text: str = "",
        vision_facts: tuple[str, ...] = (),
    ) -> SimpleNamespace:
        evidence = tuple(str(item) for item in (*tuple(vision_facts or ()), *tuple(getattr(snapshot, "evidence", ()) or ())) if str(item).strip())[:8]
        disturbance = {"content": event_text} if event_text else None
        return SimpleNamespace(
            user_text=user_text,
            text=user_text,
            current_user_text=user_text,
            evidence=evidence,
            disturbance=disturbance,
            idle_seconds=float(getattr(snapshot, "idle_seconds", 0.0) or 0.0),
            active_goal=str(self.active_conversational_topic or getattr(self, "last_user_intent", "") or ""),
            focus_family=str(getattr(snapshot, "active_window_kind", "") or ""),
            activity_kind=str(getattr(snapshot, "active_window_kind", "") or ""),
            outcome_history=tuple(str(item) for item in tuple(getattr(snapshot, "recent_events", ()) or ())[:6]),
            relation_history=tuple(str(item) for item in tuple(getattr(snapshot, "recent_events", ()) or ())[:6]),
            channel=channel,
        )


    def _subconscious_memory_packet(self, *, limit: int = 12) -> dict[str, tuple[str, ...]]:
        buckets: dict[str, list[str]] = {
            "semantic": [],
            "episodic": [],
            "relational": [],
            "affective_residue": [],
            "procedural": [],
            "open_questions": [],
            "repair": [],
        }

        def add(bucket: str, text: object) -> None:
            value = re.sub(r"\s+", " ", str(text or "")).strip()
            if not value:
                return
            store = buckets.setdefault(bucket, [])
            if value not in store:
                store.append(value[:500])

        for packet in self._daydream_packets_from_runtime(limit=limit):
            kind = str(getattr(packet, "kind", "") or "").strip().lower()
            text = getattr(packet, "text", "")
            if kind == "semantic":
                add("semantic", text)
            elif kind == "episodic":
                add("episodic", text)
            elif kind == "relational":
                add("relational", text)
            elif kind == "repair":
                add("repair", text)
            elif kind == "affective":
                add("affective_residue", text)
            elif kind == "procedural":
                add("procedural", text)
            else:
                add("open_questions", text)

        for text in tuple(getattr(self, "long_term_memory", ()) or ())[:limit]:
            add("semantic", text)

        resonance = getattr(self, "active_memory_resonance", None)
        for activation in tuple(getattr(resonance, "activations", ()) or ())[:6]:
            add("semantic", getattr(activation, "text", ""))

        return {key: tuple(values[:limit]) for key, values in buckets.items() if values}


    def _subconscious_pressure_metrics(self, *, internal_state, snapshot, event_strength: float) -> dict[str, float]:
        idle_seconds = max(0.0, float(getattr(snapshot, "idle_seconds", 0.0) or 0.0))
        idle_norm = max(0.0, min(1.0, idle_seconds / 300.0))
        drift_resonance = max(
            float(getattr(internal_state, "memory_replay_pressure", 0.0) or 0.0),
            float(getattr(internal_state, "association_pressure", 0.0) or 0.0),
            float(getattr(internal_state, "unresolved_prediction_pressure", 0.0) or 0.0),
            float(getattr(internal_state, "recursive_reactivation_pressure", 0.0) or 0.0),
        )
        self_anchor_pressure = max(
            float(getattr(internal_state, "subconscious_pressure", 0.0) or 0.0),
            float(getattr(internal_state, "thought_stream_pressure", 0.0) or 0.0),
            float(getattr(internal_state, "cognitive_tension", 0.0) or 0.0) * 0.85,
        )
        solitude_pressure = max(
            float(getattr(internal_state, "silence_pressure", 0.0) or 0.0),
            idle_norm,
            min(1.0, float(getattr(self, "presence_maintenance_silent_cycles", 0) or 0) / 8.0),
        )
        repair_need = float(getattr(internal_state, "repair_need", 0.0) or 0.0)
        rupture = float(getattr(internal_state, "rupture", 0.0) or 0.0)
        return {
            "event_strength": max(0.0, min(1.0, float(event_strength or 0.0))),
            "drift_resonance": max(0.0, min(1.0, drift_resonance)),
            "self_anchor_pressure": max(0.0, min(1.0, self_anchor_pressure)),
            "solitude_pressure": max(0.0, min(1.0, solitude_pressure)),
            "repair_need": max(0.0, min(1.0, repair_need)),
            "rupture": max(0.0, min(1.0, rupture)),
            "idle_seconds": idle_seconds,
        }


    def _subconscious_blackboard_metrics(self, *, now: float) -> dict[str, float]:
        runtime = getattr(self, "subconscious_runtime", None)
        if runtime is None:
            return {
                "fragment_count": 0.0,
                "coherence": 0.0,
                "pressure": 0.0,
                "recurring_theme_strength": 0.0,
                "unresolved_loop_pressure": 0.0,
                "contradiction_pressure": 0.0,
                "suppressed_recurrence": 0.0,
                "curiosity_gap_pressure": 0.0,
            }
        snapshot = runtime.blackboard.snapshot(now=now)
        fragments = tuple(getattr(snapshot, "fragments", ()) or ())
        if not fragments:
            return {
                "fragment_count": 0.0,
                "coherence": 0.0,
                "pressure": 0.0,
                "recurring_theme_strength": 0.0,
                "unresolved_loop_pressure": 0.0,
                "contradiction_pressure": 0.0,
                "suppressed_recurrence": 0.0,
                "curiosity_gap_pressure": 0.0,
            }

        groups: dict[str, int] = {}
        unique_evidence: set[str] = set()
        unresolved_pressure = 0.0
        contradiction_pressure = 0.0
        curiosity_gap_pressure = 0.0
        suppressed_weight = 0.0
        pressure = 0.0
        for fragment in fragments:
            key = str((fragment.tags[0] if getattr(fragment, "tags", ()) else getattr(fragment, "kind", "")) or getattr(fragment, "kind", "misc"))
            groups[key] = groups.get(key, 0) + 1
            unique_evidence.update(str(item) for item in tuple(getattr(fragment, "evidence_ids", ()) or ()) if str(item).strip())
            local_pressure = max(
                0.0,
                min(
                    1.0,
                    float(getattr(fragment, "urgency", 0.0) or 0.0) * 0.55
                    + float(getattr(fragment, "confidence", 0.0) or 0.0) * 0.20
                    + float(getattr(fragment, "affect_charge", 0.0) or 0.0) * 0.25,
                ),
            )
            pressure = max(pressure, local_pressure)
            tags = tuple(getattr(fragment, "tags", ()) or ())
            kind = str(getattr(fragment, "kind", "") or "")
            if kind in {"unresolved_loop", "suppressed_impulse", "repair_aftershock", "shadow_pressure"} or any(tag in {"shadow", "repair_aftershock", "suppressed_impulse"} for tag in tags):
                unresolved_pressure = max(unresolved_pressure, local_pressure)
            if kind in {"contradiction", "contradiction_warning"} or "contradiction" in tags:
                contradiction_pressure = max(contradiction_pressure, local_pressure)
            if kind in {"knowledge_gap", "curiosity_seed", "ask_later_candidate"} or "curiosity" in tags:
                curiosity_gap_pressure = max(curiosity_gap_pressure, local_pressure)
            if kind in {"suppressed_impulse", "rehearsal_result"} or "suppressed_impulse" in tags:
                suppressed_weight += max(1, len(tuple(getattr(fragment, "evidence_ids", ()) or ())))

        fragment_count = float(len(fragments))
        dominant_count = max(groups.values()) if groups else 0
        dominant_ratio = dominant_count / max(1.0, fragment_count)
        evidence_density = min(1.0, len(unique_evidence) / max(1.0, fragment_count * 2.0))
        coherence = max(0.0, min(1.0, dominant_ratio * 0.55 + evidence_density * 0.20 + pressure * 0.25))
        recurring = max(0.0, min(1.0, dominant_ratio * 0.75 + min(0.25, max(0, dominant_count - 1) * 0.10)))
        return {
            "fragment_count": fragment_count,
            "coherence": coherence,
            "pressure": pressure,
            "recurring_theme_strength": recurring,
            "unresolved_loop_pressure": unresolved_pressure,
            "contradiction_pressure": contradiction_pressure,
            "suppressed_recurrence": max(0.0, min(1.0, suppressed_weight / 6.0)),
            "curiosity_gap_pressure": curiosity_gap_pressure,
        }


    def _schedule_next_subconscious_due(self, lane: str, low: float, high: float, *, now: float) -> None:
        self._subconscious_lane_next_due[lane] = now + random.uniform(low, high)


    def _score_subconscious_surface(
        self,
        *,
        thought,
        snapshot,
        internal_state,
        recurring_theme_strength: float,
    ) -> float:
        if thought is None:
            return 0.0
        topic = str(getattr(thought, "topic_seed", "") or getattr(thought, "dominant_theme", "") or "")
        privacy_level = str(getattr(thought, "privacy_level", "") or "normal").lower()
        route = str(getattr(thought, "recommended_route", "") or "")
        idle_seconds = max(0.0, float(getattr(snapshot, "idle_seconds", 0.0) or 0.0))
        usefulness = 0.68 if route == "surface_candidate" else (0.50 if route in {"rehearse", "store"} else 0.28)
        novelty = max(0.0, min(1.0, float(getattr(thought, "novelty", 0.0) or 0.0)))
        continuity_gain = max(0.0, min(1.0, recurring_theme_strength * 0.55 + float(getattr(thought, "momentum_score", 0.0) or 0.0) * 0.45))
        # V66: focus/typing của user không làm giảm giá trị biểu đạt của thought.
        # Idle chỉ có thể mở thêm không gian wandering, không bao giờ là negative
        # permission signal. Collision thật được giải quyết ở public pipeline.
        privacy_cost = 0.80 if privacy_level in {"private", "sensitive"} else (0.38 if route in {"store", "suppress"} else 0.12)
        repetition_penalty = 0.0
        norm_topic = self._normalize_user_message(topic)
        recent = tuple(getattr(self, "recent_inner_thought_seeds", ()) or ()) + tuple(getattr(self._get_internal_state(), "recent_subconscious_seeds", ()) or ())
        recent_norm = {self._normalize_user_message(str(item)) for item in recent if str(item).strip()}
        if norm_topic and norm_topic in recent_norm:
            repetition_penalty = 0.55
        timing_fit = max(0.0, min(1.0, 0.72 + min(0.18, idle_seconds / 600.0)))
        return max(
            0.0,
            min(
                1.0,
                float(getattr(thought, "surface_pressure", 0.0) or 0.0) * 0.35
                + usefulness * 0.20
                + novelty * 0.15
                + continuity_gain * 0.15
                + timing_fit * 0.15
                - privacy_cost * 0.30
                - repetition_penalty * 0.25,
            ),
        )


    def _surface_from_subconscious_thought(self, thought, *, surface_score: float) -> dict[str, object] | None:
        if thought is None:
            return None
        seed = thought.to_surface_seed()
        if not isinstance(seed, dict):
            return None
        seed = dict(seed)
        seed["surface_score"] = round(float(surface_score), 3)
        seed["root_source"] = str(seed.get("root_source") or "subconscious_synthesizer")
        seed["surface_allowed"] = True
        return seed


    def _maybe_tick_subconscious_runtime(
        self,
        *,
        channel: str,
        snapshot,
        internal_state,
        user_text: str = "",
        event_text: str = "",
        vision_facts: tuple[str, ...] = (),
        event_strength: float = 0.0,
        allow_lane5: bool = True,
        reflection: bool = False,
    ) -> SubconsciousReport | None:
        runtime = getattr(self, "subconscious_runtime", None)
        if runtime is None or not self.subconscious_runtime_enabled:
            self._pending_subconscious_report = None
            self._pending_subconscious_seed = None
            return None

        now = time.time()
        metrics = self._subconscious_pressure_metrics(
            internal_state=internal_state,
            snapshot=snapshot,
            event_strength=event_strength,
        )
        frame_payload = self._subconscious_frame_payload(
            snapshot=snapshot,
            user_text=user_text,
            internal_state=internal_state,
            channel=channel,
            event_text=event_text,
            vision_facts=vision_facts,
        )
        memory_packet = self._subconscious_memory_packet()
        relation = getattr(internal_state, "relationship_dynamics", None)

        lane2_due = now >= float(self._subconscious_lane_next_due.get("lane2", 0.0) or 0.0)
        should_lane2 = (
            metrics["event_strength"] > 0.45
            or metrics["drift_resonance"] > 0.55
            or metrics["self_anchor_pressure"] > 0.50
            or metrics["solitude_pressure"] > 0.60
            or lane2_due
        )
        if should_lane2:
            runtime.tick(
                frame=frame_payload,
                snapshot=snapshot,
                memory_packet=memory_packet,
                relation=relation,
                internal_state=internal_state,
                now=now,
                pre_worker_names=SUBCONSCIOUS_LANE2_WORKERS,
                audit_worker_names=SUBCONSCIOUS_AUDIT_WORKERS,
                run_pre_workers=True,
                run_audit=True,
                run_synthesis=False,
                run_post_workers=False,
                allow_surface_seed=False,
            )
            self._subconscious_lane_last_tick["lane2"] = now
            self._schedule_next_subconscious_due("lane2", 45.0, 120.0, now=now)

        board = self._subconscious_blackboard_metrics(now=now)
        should_lane3 = (
            board["coherence"] > 0.55
            and board["fragment_count"] >= 3.0
            and (
                board["pressure"] > 0.60
                or board["recurring_theme_strength"] > 0.65
                or board["unresolved_loop_pressure"] > 0.60
                or board["contradiction_pressure"] > 0.55
                or board["suppressed_recurrence"] > 0.55
                or board["curiosity_gap_pressure"] > 0.55
                or metrics["event_strength"] > 0.65
            )
        )

        lane3_result = None
        if should_lane3:
            lane3_result = runtime.tick(
                frame=frame_payload,
                snapshot=snapshot,
                memory_packet=memory_packet,
                relation=relation,
                internal_state=internal_state,
                now=now,
                post_worker_names=SUBCONSCIOUS_LANE3_POST_WORKERS,
                audit_worker_names=SUBCONSCIOUS_AUDIT_WORKERS,
                run_pre_workers=False,
                run_audit=True,
                run_synthesis=True,
                run_post_workers=True,
                allow_surface_seed=False,
            )
            self._subconscious_lane_last_tick["lane3"] = now

        lane5_due = now >= float(self._subconscious_lane_next_due.get("lane5", 0.0) or 0.0)
        should_lane5 = allow_lane5 and (
            reflection
            or lane5_due
            or metrics["idle_seconds"] >= 120.0
            or metrics["repair_need"] > 0.45
            or metrics["rupture"] > 0.35
            or board["suppressed_recurrence"] > 0.55
        )
        if should_lane5:
            runtime.tick(
                frame=frame_payload,
                snapshot=snapshot,
                memory_packet=memory_packet,
                relation=relation,
                internal_state=internal_state,
                now=now,
                pre_worker_names=SUBCONSCIOUS_LANE5_PRE_WORKERS,
                post_worker_names=SUBCONSCIOUS_LANE5_POST_WORKERS,
                audit_worker_names=SUBCONSCIOUS_AUDIT_WORKERS,
                run_pre_workers=True,
                run_audit=True,
                run_synthesis=True,
                run_post_workers=True,
                allow_surface_seed=False,
            )
            self._subconscious_lane_last_tick["lane5"] = now
            self._schedule_next_subconscious_due("lane5", 600.0, 1800.0, now=now)

        report = runtime.latest_report()
        seed = None
        if lane3_result is not None and getattr(lane3_result, "thought_object", None) is not None:
            surface_score = self._score_subconscious_surface(
                thought=lane3_result.thought_object,
                snapshot=snapshot,
                internal_state=internal_state,
                recurring_theme_strength=board["recurring_theme_strength"],
            )
            threshold = float(
                get_config(
                    "subconscious_runtime.lanes.lane4_surface_candidate.surface_score_threshold",
                    default=0.68,
                )
                or 0.68
            )
            if surface_score >= threshold:
                seed = self._surface_from_subconscious_thought(
                    lane3_result.thought_object,
                    surface_score=surface_score,
                )
                surface = runtime._surface_from_seed(seed, lane3_result.thought_object) if seed else ThoughtSurface()
                report = replace(
                    report,
                    latest_seed=surface,
                    recent_seeds=(surface,) if surface.mode else (),
                    surface_pressure=max(float(getattr(report, "surface_pressure", 0.0) or 0.0), float(surface.pressure or 0.0)),
                    suppression_reason="",
                )
            else:
                store_range = get_config(
                    "subconscious_runtime.lanes.lane4_surface_candidate.store_private_range",
                    default=(0.45, 0.67),
                )
                if not isinstance(store_range, (list, tuple)) or len(store_range) < 2:
                    store_range = (0.45, 0.67)
                lower = float(store_range[0] or 0.45)
                upper = float(store_range[1] or 0.67)
                if lower <= surface_score <= upper:
                    report = replace(report, suppression_reason=f"store_private(surface_score={surface_score:.2f})")
                else:
                    report = replace(report, suppression_reason=f"suppress(surface_score={surface_score:.2f})")

        self._pending_subconscious_report = report
        self._pending_subconscious_seed = seed
        self._last_subconscious_runtime_tick_at = now
        self._last_subconscious_runtime_reason = channel
        return report


    def _maybe_tick_subconscious_background(self, *, snapshot, internal_state) -> None:
        if not self.subconscious_runtime_enabled or getattr(self, "_chat_processing", False):
            return
        now = time.time()
        ambient_due = now >= float(self._subconscious_lane_next_due.get("lane2", 0.0) or 0.0)
        solitude_due = now >= float(self._subconscious_lane_next_due.get("lane5", 0.0) or 0.0)
        reflection_due = now >= float(self._subconscious_lane_next_due.get("reflection", 0.0) or 0.0)
        if not any((ambient_due, solitude_due, reflection_due)):
            return
        self._maybe_tick_subconscious_runtime(
            channel="background",
            snapshot=snapshot,
            internal_state=internal_state,
            user_text="",
            event_text="ambient_tick" if ambient_due else "",
            event_strength=0.18 if ambient_due else 0.0,
            allow_lane5=solitude_due or reflection_due,
            reflection=reflection_due,
        )
        if reflection_due:
            self._subconscious_lane_last_tick["reflection"] = now
            self._schedule_next_subconscious_due("reflection", 900.0, 1800.0, now=now)


    def _mix_axis(self, previous: float, target: float, *, inertia: float) -> float:
        inertia = max(0.0, min(1.0, float(inertia)))
        value = float(previous or 0.0) * inertia + float(target or 0.0) * (1.0 - inertia)
        return max(0.0, min(1.0, value))


    def _load_mental_process_enabled(self) -> bool:
        flag = os.environ.get("ERIS_MENTAL_PROCESS_LOOP", "1").strip().lower()
        return flag not in {"0", "false", "no", "off"}


    def _load_mental_process_interval(self) -> float:
        raw = os.environ.get("ERIS_MENTAL_PROCESS_INTERVAL_SEC", "30").strip()
        try:
            return max(5.0, float(raw))
        except ValueError:
            return 30.0


    def _load_autonomous_sleep_seconds(self, env_name: str, *, default: tuple[int, ...]) -> tuple[int, ...]:
        raw = os.environ.get(env_name, "").strip()
        if not raw:
            return default

        values: list[int] = []
        for part in re.split(r"[,;\s]+", raw):
            if not part:
                continue
            try:
                value = int(float(part))
            except ValueError:
                continue
            if value >= 5:
                values.append(value)

        return tuple(values) or default


__all__ = ["MentalProcessRuntimeMixin"]
