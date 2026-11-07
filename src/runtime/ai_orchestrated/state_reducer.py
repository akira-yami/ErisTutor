# -*- coding: utf-8 -*-
"""Single-writer reducer cho state AI và personality của ERIS.

Observer, planner, chat và autonomous chỉ nộp event. Reducer là nơi duy nhất
được ghi state. Personality không bị kéo về một ``mature target``: delta được
phép đi cả hai chiều, kể cả các vulnerability như resentment, possessiveness
hay control_seeking. Thứ reducer bảo vệ là lịch sử, confidence, evidence, cap và
thứ tự ghi, không phải một kết luận đạo đức đã chọn trước.
"""

from __future__ import annotations
from core.di import resolve_dependency

import queue
import threading
import time
from dataclasses import replace
from datetime import date, datetime, timedelta
from typing import Any, Callable, Mapping, Sequence

from runtime.diagnostic_logging import safe_log
from runtime.goal_lifecycle import GoalLifecycleRecord, active_goal_records, reconcile_goal_records

from .contracts import (
    AppraisalState,
    CognitiveObservation,
    RelationshipState,
    RuntimeMindState,
    StateEvent,
    clamp01,
    compact,
)
from .identity import IdentityProfile, PendingStrategyUse, PRIMARY_RELATIONSHIP_ID, RelationshipIdentity
from .identity_reducer import IdentityKernelReducer
from .personality import (
    DEFAULT_CORE,
    DEFAULT_MOOD,
    DEFAULT_TEMPERAMENT,
    NarrativeState,
    PersonalityProfile,
    RELATIONSHIP_PROFILE_TRAITS,
    RelationshipPersonalityProfile,
    TraitSignal,
    decay_profile,
)
from .personality_consolidator import consolidate_personality


PersistFn = Callable[[Mapping[str, Any]], None]
Subscriber = Callable[[RuntimeMindState, StateEvent, bool], None]


CONF_THRESHOLDS = {
    "mood": 0.62,
    "temperament": 0.75,
    "core": 0.82,
}
PER_EVENT_MAX = {
    "mood": 0.18,
    "temperament": 0.012,
    "core": 0.003,
}
PER_DAY_CAP = {
    "mood": 0.60,
    "temperament": 0.030,
    "core": 0.008,
}


class StateReducer:
    def __init__(
        self,
        *,
        initial_state: RuntimeMindState | None = None,
        persist: PersistFn | None = None,
        logger: Any | None = None,
    ) -> None:
        self._state = resolve_dependency(initial_state, lambda: RuntimeMindState())
        self._persist = persist
        self._logger = logger
        self._identity_reducer = IdentityKernelReducer(logger=logger)
        self._queue: queue.Queue[StateEvent | None] = queue.Queue()
        self._lock = threading.RLock()
        self._subscribers: list[Subscriber] = []
        self._closed = False
        self._worker = threading.Thread(
            target=self._run,
            name="eris-state-reducer",
            daemon=True,
        )
        self._worker.start()

    def subscribe(self, callback: Subscriber) -> None:
        if callable(callback):
            with self._lock:
                self._subscribers.append(callback)

    def snapshot(self) -> RuntimeMindState:
        with self._lock:
            return RuntimeMindState.from_mapping(self._state.to_dict())

    def submit(self, event: StateEvent) -> bool:
        if self._closed:
            return False
        self._queue.put(event)
        return True

    def apply_now(self, event: StateEvent) -> bool:
        """Dùng cho test/boot migration. Production nên đi qua queue."""
        accepted = self._apply(event)
        self._notify(event, accepted)
        return accepted

    def wait_idle(self, timeout: float = 2.0) -> bool:
        deadline = time.time() + max(0.0, float(timeout or 0.0))
        while time.time() < deadline:
            if self._queue.unfinished_tasks == 0:
                return True
            time.sleep(0.01)
        return self._queue.unfinished_tasks == 0

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._queue.put(None)
        try:
            self._worker.join(timeout=1.5)
        except RuntimeError as exc:
            self._log("debug", "[state reducer] worker join failed: %s", exc)

    def _run(self) -> None:
        while True:
            event = self._queue.get()
            try:
                if event is None:
                    return
                accepted = self._apply(event)
                self._notify(event, accepted)
            except Exception as exc:  # noqa: BLE001
                self._log(
                    "warning",
                    "[state reducer] event failed kind=%s: %s",
                    getattr(event, "kind", "?"),
                    exc,
                )
            finally:
                self._queue.task_done()

    def _apply(self, event: StateEvent) -> bool:
        with self._lock:
            state = self._state
            # Observation cũ không được quay lại viết đè lượt mới. Intention có thể
            # dùng cùng turn seq nên được quản bằng version, không bằng stale guard.
            if event.kind == "cognitive_observation" and event.turn_seq <= state.last_applied_turn_seq:
                self._log(
                    "info",
                    "[state reducer] stale event dropped kind=%s turn=%s latest=%s",
                    event.kind,
                    event.turn_seq,
                    state.last_applied_turn_seq,
                )
                return False
            if event.base_version >= 0 and event.base_version > state.version:
                self._log(
                    "warning",
                    "[state reducer] future base_version dropped base=%s current=%s",
                    event.base_version,
                    state.version,
                )
                return False

            if event.kind == "cognitive_observation":
                new_state = self._reduce_observation(state, event)
            elif event.kind == "intention_plan":
                new_state = replace(
                    state,
                    version=state.version + 1,
                    latest_intention=dict(event.payload),
                    updated_at=time.time(),
                )
            elif event.kind == "mechanical_outcome":
                new_state = self._reduce_mechanical_outcome(state, event)
            elif event.kind == "pending_strategy":
                new_state = self._reduce_pending_strategy(state, event)
            elif event.kind == "strategy_reaction":
                new_state = self._reduce_strategy_reaction(state, event)
            elif event.kind == "admin_control":
                new_state = self._reduce_admin_control(state, event)
            else:
                self._log("debug", "[state reducer] unknown event ignored kind=%s", event.kind)
                return False

            self._state = new_state
            if self._persist is not None:
                try:
                    self._persist(new_state.to_dict())
                except Exception as exc:  # noqa: BLE001
                    self._log("warning", "[state reducer] persist failed: %s", exc)
            return True

    def _reduce_observation(self, state: RuntimeMindState, event: StateEvent) -> RuntimeMindState:
        observation = CognitiveObservation.from_mapping(
            event.payload,
            turn_id=event.turn_id,
            turn_seq=event.turn_seq,
            source=str(event.payload.get("source", event.source) or event.source),
            latency_ms=float(event.payload.get("latency_ms", 0.0) or 0.0),
        )
        now = time.time()

        # Confidence thấp vẫn nằm trong telemetry nhưng không được bẻ state dài hạn.
        weight = observation.confidence if observation.confidence >= 0.55 else 0.0
        # Autonomous/startup không có user turn nên không tự sáng tác biến động quan hệ.
        relation_weight = weight if observation.event_kind in {"chat", "force_speak"} else 0.0
        if not state.identity_learning_enabled:
            relation_weight = 0.0
        relation = state.relationship

        def delta(current: float, proposed: float, cap: float) -> float:
            return clamp01(current + max(-cap, min(cap, proposed * relation_weight * cap)))

        relational_trust = delta(relation.relational_trust, observation.relational_delta, 0.040)
        competence_trust = delta(relation.competence_trust, observation.competence_delta, 0.045)
        factual_reliability = delta(relation.factual_reliability, observation.factual_delta, 0.050)
        boundary_safety = delta(relation.boundary_safety, observation.boundary_delta, 0.040)
        familiarity = delta(relation.familiarity, observation.familiarity_delta, 0.035)
        closeness = delta(relation.closeness, observation.closeness_delta, 0.045)
        playful_safety = delta(relation.playful_safety, observation.playful_safety_delta, 0.065)
        rupture = delta(relation.rupture, observation.rupture_delta, 0.045)
        repair_progress = relation.repair_progress
        if relation_weight > 0.0:
            if observation.repair_needed:
                repair_progress = clamp01(repair_progress - 0.025 * relation_weight)
            elif observation.relationship_meaning in {"acceptance", "repair_accepted"}:
                repair_progress = clamp01(repair_progress + 0.040 * relation_weight)
                rupture = clamp01(rupture - 0.030 * relation_weight)

        updated_relation = RelationshipState(
            relational_trust=relational_trust,
            competence_trust=competence_trust,
            factual_reliability=factual_reliability,
            boundary_safety=boundary_safety,
            familiarity=familiarity,
            closeness=closeness,
            playful_safety=playful_safety,
            rupture=rupture,
            repair_progress=repair_progress,
            last_event=f"observer:{observation.relationship_meaning or observation.user_act}",
            last_turn_seq=max(relation.last_turn_seq, observation.turn_seq),
        )

        appraisal = observation.appraisal
        if weight <= 0.0:
            appraisal = state.appraisal
        else:
            appraisal = AppraisalState(
                pleasantness=appraisal.pleasantness,
                curiosity=appraisal.curiosity,
                tenderness=appraisal.tenderness,
                embarrassment=appraisal.embarrassment,
                irritation=appraisal.irritation,
                pressure=appraisal.pressure,
                novelty=appraisal.novelty,
                last_turn_seq=observation.turn_seq,
            )

        if state.identity_learning_enabled:
            personality = self._reduce_personality(
                state.personality,
                observation,
                now=now,
            )
            identity = self._identity_reducer.reduce_observation(
                state.identity,
                observation,
                relationship_state=updated_relation.to_dict(),
                now=now,
            )
        else:
            personality = state.personality
            identity = state.identity

        # V94: goal user/shared có lifecycle typed. ``open_loops`` không còn là
        # source of truth vì một string không thể biết nó đã stale/suspended hay
        # được user xác nhận lại ở turn nào. V93 ownership vẫn là hard boundary:
        # open-loop của ERIS không được promotion vào goal public.
        loop_owner = str(getattr(observation, "open_loop_owner", "unknown") or "unknown").strip().lower()
        goal_records = tuple(state.goal_records or ())
        if not goal_records and state.open_loops:
            # Migration mềm cho snapshot V93: coi open-loop cũ là shared ACTIVE,
            # sau đó lifecycle mới sẽ tự decay nếu không còn evidence.
            seed_now = state.updated_at if state.updated_at > 0.0 else now
            goal_records = tuple(
                GoalLifecycleRecord.create(
                    text, owner="shared", now=seed_now,
                    turn_seq=state.last_applied_turn_seq,
                    evidence_refs=("legacy-open-loop:v93",),
                )
                for text in state.open_loops[-8:]
                if str(text or "").strip()
            )
        goal_records = reconcile_goal_records(
            goal_records,
            open_loop=observation.open_loop,
            owner=loop_owner,
            event_kind=observation.event_kind,
            relationship_meaning=observation.relationship_meaning,
            confidence=observation.confidence,
            turn_seq=observation.turn_seq,
            evidence_refs=observation.evidence,
            now=now,
        )
        loops = [item.text for item in active_goal_records(goal_records)]

        candidates = list(state.memory_candidates)
        # Không lưu suy đoán thiếu evidence thành ký ức dù confidence cao.
        if observation.memory_worthy and observation.memory_candidate and observation.confidence >= 0.68 and observation.evidence:
            # Cùng một cách diễn giải xuất hiện trong nhiều lượt không tự biến
            # thành nhiều ký ức. Dedupe theo nội dung chuẩn hóa, không dựa vào
            # turn_id mới (turn_id khác chưa chứng minh sự kiện độc lập).
            normalized = " ".join(observation.memory_candidate.casefold().split())
            already_seen = any(
                " ".join(str(item.get("text", "")).casefold().split()) == normalized
                for item in candidates
            )
            if not already_seen:
                candidates.append(
                    {
                        "turn_id": observation.turn_id,
                        "turn_seq": observation.turn_seq,
                        "text": observation.memory_candidate,
                        "confidence": observation.confidence,
                        "evidence": list(observation.evidence),
                        "source": observation.source,
                        "created_at": now,
                    }
                )
                candidates = candidates[-12:]

        return RuntimeMindState(
            version=state.version + 1,
            last_applied_turn_seq=max(state.last_applied_turn_seq, observation.turn_seq),
            relationship=updated_relation,
            appraisal=appraisal,
            personality=personality,
            identity=identity,
            identity_learning_enabled=state.identity_learning_enabled,
            latest_observation=observation.to_dict(),
            latest_intention=state.latest_intention,
            open_loops=tuple(loops[-8:]),
            goal_records=tuple(goal_records[-8:]),
            memory_candidates=tuple(candidates),
            updated_at=now,
        )

    def _reduce_personality(
        self,
        original: PersonalityProfile,
        observation: CognitiveObservation,
        *,
        now: float,
    ) -> PersonalityProfile:
        profile = decay_profile(original, now=now)
        signals = self._resolve_signal_conflicts(observation.trait_signals)

        core = dict(profile.core)
        temperament = dict(profile.temperament)
        mood = dict(profile.mood)
        core_anchor = dict(profile.core_anchor)
        temperament_attractor = dict(profile.temperament_attractor)
        day_usage = self._prune_day_usage(dict(profile.day_usage))
        corroboration = dict(profile.corroboration)
        corroboration_records = {
            str(key): [dict(item) for item in values]
            for key, values in profile.corroboration_records.items()
        }
        streaks = {str(key): dict(value) for key, value in profile.streaks.items()}
        strategy_reinforcement = dict(profile.strategy_reinforcement)
        narrative = profile.narrative
        relationship_profiles = {
            str(key): value
            for key, value in profile.relationship_profiles.items()
        }
        trajectory = [dict(item) for item in profile.trajectory]

        changed = False
        today = date.today().isoformat()
        interpretation = observation.self_interpretation
        consequence = observation.consequence

        for signal in signals:
            category = signal.category
            if category not in CONF_THRESHOLDS:
                continue
            target: dict[str, float]
            defaults: Mapping[str, float]
            if category == "mood":
                target, defaults = mood, DEFAULT_MOOD
            elif category == "temperament":
                target, defaults = temperament, DEFAULT_TEMPERAMENT
            else:
                target, defaults = core, DEFAULT_CORE
            if signal.trait not in defaults:
                self._log(
                    "debug",
                    "[personality reducer] unknown trait skipped category=%s trait=%s",
                    category,
                    signal.trait,
                )
                continue

            threshold = CONF_THRESHOLDS[category]
            if signal.confidence < threshold:
                continue

            evidence = tuple(
                dict.fromkeys(
                    (
                        *signal.evidence,
                        *observation.evidence,
                        *interpretation.evidence,
                        *consequence.evidence,
                    )
                )
            )
            if category in {"temperament", "core"} and not evidence:
                continue

            # Turn không có user vẫn được tự phát triển, nhưng chỉ khi signal đến từ
            # lựa chọn/hậu quả của chính ERIS và observer thực sự có outcome.
            if observation.event_kind in {"autonomous", "startup"} and category != "mood":
                if signal.source not in {"self_choice", "consequence"}:
                    continue
                if consequence.confidence < 0.75 or not consequence.evidence:
                    continue

            direction = 1 if signal.delta > 0 else -1
            streak_key = f"{category}:{signal.trait}"
            streak = dict(streaks.get(streak_key, {}))
            last_ts = float(streak.get("last_ts", 0.0) or 0.0)
            last_direction = int(streak.get("direction", 0) or 0)
            within_window = now - last_ts <= 72 * 3600 if last_ts > 0.0 else False
            count = int(streak.get("count", 0) or 0)
            count = count + 1 if within_window and last_direction == direction else 1
            streaks[streak_key] = {
                "direction": direction,
                "count": count,
                "last_ts": now,
                "last_turn_seq": observation.turn_seq,
                "confidence": signal.confidence,
            }
            # Streak/corroboration cũng là state thật. Trước đây nó vô tình được
            # persist nhờ strategy_reinforcement legacy; khi tước quyền lane cũ,
            # ta phải ghi bookkeeping một cách minh bạch.
            changed = True

            # Core chỉ dịch sau ít nhất ba event mạnh cùng hướng, qua tối thiểu
            # hai runtime session trong 14 ngày. Một buổi chat dồn dập không được
            # tự phong thành identity milestone chỉ vì nó nói cùng một ý ba lần.
            if category == "core":
                corr_key = f"{signal.trait}:{'positive' if direction > 0 else 'negative'}"
                cutoff = now - 14 * 86400.0
                records = [
                    dict(item)
                    for item in corroboration_records.get(corr_key, [])
                    if float(item.get("timestamp", 0.0) or 0.0) >= cutoff
                ]
                records.append(
                    {
                        "timestamp": now,
                        "session_id": observation.session_id or "runtime",
                        "turn_seq": observation.turn_seq,
                        "turn_id": observation.turn_id,
                        "confidence": signal.confidence,
                        "evidence_count": len(evidence),
                    }
                )
                records = records[-16:]
                corroboration_records[corr_key] = records
                corroboration[corr_key] = len(records)
                distinct_sessions = {
                    str(item.get("session_id", "") or "runtime") for item in records
                }
                if len(records) < 3 or len(distinct_sessions) < 2:
                    continue

            confidence_scale = self._confidence_scale(signal.confidence, category)
            evidence_scale = 1.0 if evidence else 0.60
            salience_scale = max(0.20, min(1.0, signal.importance))
            interpretation_scale = (
                0.72 + 0.28 * interpretation.confidence
                if interpretation.summary and interpretation.confidence >= 0.45
                else 0.68
            )
            outcome_scale = 1.0
            if signal.source in {"self_choice", "consequence"} and category != "mood":
                # Reinforcement mang dấu. Outcome xấu có thể làm yếu chiến lược
                # đã biểu hiện, thay vì abs() rồi vô tình củng cố cả thất bại.
                # Chỉ dùng khi self-reflection thừa nhận quan hệ nhân quả đủ chắc.
                if consequence.confidence < 0.55 or abs(consequence.reinforcement) < 0.05:
                    continue
                outcome_scale = consequence.reinforcement * consequence.confidence

            proposed = (
                max(-1.0, min(1.0, signal.delta))
                * PER_EVENT_MAX[category]
                * confidence_scale
                * evidence_scale
                * salience_scale
                * interpretation_scale
                * outcome_scale
            )
            usage_key = f"{today}:{category}"
            used = max(0.0, float(day_usage.get(usage_key, 0.0) or 0.0))
            remaining = max(0.0, PER_DAY_CAP[category] - used)
            applied = max(-remaining, min(remaining, proposed))
            if abs(applied) < 1e-8:
                continue

            before = float(target.get(signal.trait, defaults[signal.trait]) or defaults[signal.trait])
            after = clamp01(before + applied)
            applied = after - before
            if abs(applied) < 1e-8:
                continue
            target[signal.trait] = after
            day_usage[usage_key] = used + abs(applied)
            changed = True

            if (
                category == "temperament"
                and signal.trait in RELATIONSHIP_PROFILE_TRAITS
                and observation.event_kind in {"chat", "force_speak"}
            ):
                relationship_id = observation.relationship_id or PRIMARY_RELATIONSHIP_ID
                relation_profile = relationship_profiles.get(
                    relationship_id,
                    RelationshipPersonalityProfile(relationship_id=relationship_id),
                )
                offsets = dict(relation_profile.trait_offsets)
                old_offset = float(offsets.get(signal.trait, 0.0) or 0.0)
                offsets[signal.trait] = max(
                    -0.35,
                    min(0.35, old_offset + applied * 0.75),
                )
                relationship_profiles[relationship_id] = RelationshipPersonalityProfile.from_mapping(
                    {
                        **relation_profile.to_dict(),
                        "relationship_id": relationship_id,
                        "trait_offsets": offsets,
                        "last_interpretation": interpretation.summary,
                        "event_count": relation_profile.event_count + 1,
                        "last_turn_seq": observation.turn_seq,
                        "updated_at": now,
                    },
                    relationship_id=relationship_id,
                )

            # Hai event cùng hướng bắt đầu dịch attractor. Không có attractor "tốt";
            # attractor chỉ là quỹ đạo đã được lặp và củng cố.
            if category == "temperament" and count >= 2:
                old_attractor = float(
                    temperament_attractor.get(signal.trait, defaults[signal.trait])
                    or defaults[signal.trait]
                )
                temperament_attractor[signal.trait] = clamp01(old_attractor + applied * 0.55)
            elif category == "core":
                # Core anchor chỉ thay cực nhẹ sau corroboration, giữ self-authorship
                # mà không làm identity trôi như bong bóng xà phòng.
                old_anchor = float(core_anchor.get(signal.trait, defaults[signal.trait]) or defaults[signal.trait])
                core_anchor[signal.trait] = clamp01(old_anchor + applied * 0.15)

            trajectory.append(
                {
                    "timestamp": now,
                    "turn_seq": observation.turn_seq,
                    "turn_id": observation.turn_id,
                    "session_id": observation.session_id,
                    "relationship_id": observation.relationship_id,
                    "event_kind": observation.event_kind,
                    "category": category,
                    "trait": signal.trait,
                    "before": round(before, 6),
                    "after": round(after, 6),
                    "applied_delta": round(applied, 6),
                    "signal_delta": signal.delta,
                    "confidence": signal.confidence,
                    "importance": signal.importance,
                    "source": signal.source,
                    "strategy": signal.strategy or consequence.strategy,
                    "self_interpretation": interpretation.summary,
                    "interpretation_frame": interpretation.frame,
                    "interpretation_confidence": interpretation.confidence,
                    "self_authored": interpretation.self_authored,
                    "consequence": consequence.outcome,
                    "reinforcement": consequence.reinforcement,
                    "evidence": list(evidence[:5]),
                }
            )

        # Học chiến lược thuộc IdentityKernelReducer, nơi kết quả chỉ được chấm
        # sau phản ứng thật ở lượt kế tiếp và được tách theo context. Trường
        # strategy_reinforcement cũ được giữ read-only để migrate/rollback,
        # không còn là nguồn học production.

        narrative_text = observation.self_narrative_candidate
        narrative_confidence = observation.self_narrative_confidence
        # Migration an toàn: profile cũ chưa có narrative có thể nhận một
        # self-interpretation đủ chắc làm seed đầu tiên. Sau đó chỉ candidate
        # dài hạn có contract riêng mới được phép viết đè.
        if (
            not narrative_text
            and not narrative.text
            and interpretation.self_authored
            and interpretation.summary
            and interpretation.confidence >= 0.78
        ):
            narrative_text = interpretation.summary
            narrative_confidence = interpretation.confidence

        if narrative_text and narrative_confidence >= 0.72:
            provenance = [
                *narrative.provenance,
                f"turn:{observation.turn_seq}",
                f"frame:{interpretation.frame}",
            ][-8:]
            candidate_narrative = NarrativeState.from_mapping(
                {
                    "text": narrative_text,
                    "frame": interpretation.frame,
                    "confidence": narrative_confidence,
                    "version": narrative.version + 1,
                    "source_turn_seq": observation.turn_seq,
                    "source_turn_id": observation.turn_id,
                    "provenance": provenance,
                    "updated_at": now,
                }
            )
            if candidate_narrative != narrative:
                narrative = candidate_narrative
                changed = True

        if observation.event_kind in {"chat", "force_speak"}:
            relationship_id = observation.relationship_id or PRIMARY_RELATIONSHIP_ID
            relation_profile = relationship_profiles.get(
                relationship_id,
                RelationshipPersonalityProfile(relationship_id=relationship_id),
            )
            carry = list(relation_profile.unresolved_carry)
            if observation.discourse.status == "closed" or observation.relationship_meaning in {
                "closure",
                "repair_accepted",
            }:
                carry = []
            elif observation.open_loop:
                carry = [
                    item for item in carry
                    if item.casefold() != observation.open_loop.casefold()
                ]
                carry.append(observation.open_loop)
                carry = carry[-6:]
            updated_relation_profile = RelationshipPersonalityProfile.from_mapping(
                {
                    **relation_profile.to_dict(),
                    "relationship_id": relationship_id,
                    "unresolved_carry": carry,
                    "last_interpretation": (
                        interpretation.summary
                        if interpretation.self_authored and interpretation.confidence >= 0.60
                        else relation_profile.last_interpretation
                    ),
                    "last_turn_seq": max(relation_profile.last_turn_seq, observation.turn_seq),
                    "updated_at": now,
                },
                relationship_id=relationship_id,
            )
            if updated_relation_profile != relation_profile:
                relationship_profiles[relationship_id] = updated_relation_profile
                changed = True

        # Giữ ledger hữu hạn. 96 entry đủ để debug hướng gần đây mà không biến
        # memory state thành nhật ký đời người không có mục lục.
        trajectory = trajectory[-96:]
        candidate = PersonalityProfile.from_mapping(
            {
                **profile.to_dict(),
                "version": profile.version + (1 if changed else 0),
                "core": core,
                "temperament": temperament,
                "mood": mood,
                "core_anchor": core_anchor,
                "temperament_attractor": temperament_attractor,
                "day_usage": day_usage,
                "corroboration": corroboration,
                "corroboration_records": corroboration_records,
                "streaks": streaks,
                "strategy_reinforcement": strategy_reinforcement,
                "narrative": narrative.to_dict(),
                "relationship_profiles": {
                    key: value.to_dict() for key, value in relationship_profiles.items()
                },
                "trajectory": trajectory,
                "last_decay_at": now,
                "last_updated_at": now if changed else profile.last_updated_at,
            }
        )
        candidate = consolidate_personality(candidate, now=now)
        if (
            not changed
            and profile.last_decay_at == original.last_decay_at
            and candidate.version == profile.version
        ):
            return original
        return candidate

    @staticmethod
    def _confidence_scale(confidence: float, category: str) -> float:
        threshold = CONF_THRESHOLDS[category]
        if confidence < threshold:
            return 0.0
        if confidence >= 0.92:
            return 1.0
        denominator = max(0.01, 0.92 - threshold)
        return max(0.35, min(1.0, (confidence - threshold) / denominator))

    def _resolve_signal_conflicts(
        self,
        signals: Sequence[TraitSignal],
    ) -> tuple[TraitSignal, ...]:
        grouped: dict[tuple[str, str], list[TraitSignal]] = {}
        for signal in signals:
            grouped.setdefault((signal.category, signal.trait), []).append(signal)
        resolved: list[TraitSignal] = []
        for key, group in grouped.items():
            signs = {1 if item.delta > 0 else -1 for item in group if item.delta != 0.0}
            if len(signs) <= 1:
                resolved.extend(group)
                continue
            ordered = sorted(group, key=lambda item: item.confidence, reverse=True)
            if len(ordered) >= 2 and ordered[0].confidence - ordered[1].confidence < 0.08:
                self._log(
                    "debug",
                    "[personality reducer] ambiguous opposite signals skipped category=%s trait=%s",
                    key[0],
                    key[1],
                )
                continue
            resolved.append(ordered[0])
        return tuple(resolved)

    @staticmethod
    def _prune_day_usage(day_usage: dict[str, float]) -> dict[str, float]:
        cutoff = date.today() - timedelta(days=14)
        result: dict[str, float] = {}
        for key, value in day_usage.items():
            day_text = str(key).split(":", 1)[0]
            try:
                day = datetime.strptime(day_text, "%Y-%m-%d").date()
            except ValueError:
                continue
            if day >= cutoff:
                result[str(key)] = max(0.0, float(value or 0.0))
        return result

    def _reduce_pending_strategy(self, state: RuntimeMindState, event: StateEvent) -> RuntimeMindState:
        if not state.identity_learning_enabled:
            return state
        pending = PendingStrategyUse.from_mapping(event.payload)
        identity = self._identity_reducer.add_pending_strategy(
            state.identity,
            pending,
            now=time.time(),
        )
        if identity.version == state.identity.version:
            return state
        return replace(
            state,
            version=state.version + 1,
            identity=identity,
            updated_at=time.time(),
        )

    def _reduce_strategy_reaction(self, state: RuntimeMindState, event: StateEvent) -> RuntimeMindState:
        if not state.identity_learning_enabled:
            return state
        data = event.payload
        sid = str(data.get("session_id", "") or "")
        ref = str(data.get("reaction_ref", "") or "")[:180]
        text = str(data.get("reaction_text", "") or "")[:2000]
        previous = int(data.get("previous_assistant_turn_seq", 0) or 0)
        received = float(data.get("received_at", event.created_at))
        if not sid or not ref or not text or previous <= 0:
            return state
        pending = list(state.identity.pending_strategies)
        for index, item in enumerate(pending):
            if (item.session_id != sid or item.turn_seq != previous or item.reaction_ref
                    or event.turn_seq <= item.turn_seq or not 0 <= received - item.created_at <= 86400):
                continue
            pending[index] = replace(item, reaction_text=text, reaction_ref=ref,
                                     reaction_turn_seq=event.turn_seq)
            identity = replace(state.identity, version=state.identity.version + 1,
                               pending_strategies=tuple(pending), last_updated_at=time.time())
            return replace(state, version=state.version + 1, identity=identity, updated_at=time.time())
        return state

    def _reduce_mechanical_outcome(self, state: RuntimeMindState, event: StateEvent) -> RuntimeMindState:
        payload = dict(event.payload)
        # Tool/TTS failure ảnh hưởng factual/competence, không tự biến thành rupture.
        relation = state.relationship
        success = bool(payload.get("success", False))
        kind = compact(payload.get("kind", "mechanical"), 60)
        shift = 0.010 if success else -0.018
        relation = replace(
            relation,
            competence_trust=clamp01(relation.competence_trust + shift),
            factual_reliability=clamp01(
                relation.factual_reliability + (0.008 if success else -0.012)
            ),
            last_event=f"runtime:{kind}:{'ok' if success else 'failed'}",
        )
        return replace(
            state,
            version=state.version + 1,
            relationship=relation,
            updated_at=time.time(),
        )

    def _reduce_admin_control(self, state: RuntimeMindState, event: StateEvent) -> RuntimeMindState:
        """Lệnh panel/debug đi qua single writer thay vì sửa state từ bên hông."""
        action = compact(event.payload.get("action", ""), 60).lower()
        now = time.time()
        if action == "set_learning":
            enabled = bool(event.payload.get("enabled", True))
            return replace(state, version=state.version + 1, identity_learning_enabled=enabled, updated_at=now)
        if action == "reset_mood":
            personality = PersonalityProfile.from_mapping({
                **state.personality.to_dict(),
                "version": state.personality.version + 1,
                "mood": dict(DEFAULT_MOOD),
                "last_updated_at": now,
            })
            return replace(
                state,
                version=state.version + 1,
                appraisal=AppraisalState(),
                personality=personality,
                updated_at=now,
            )
        if action == "consolidate_personality":
            personality = consolidate_personality(
                state.personality,
                now=now,
                force=True,
            )
            if personality.version == state.personality.version:
                return state
            return replace(
                state,
                version=state.version + 1,
                personality=personality,
                updated_at=now,
            )
        if action == "reset_relationship":
            identity_payload = state.identity.to_dict()
            relationships = dict(identity_payload.get("relationships", {}) or {})
            relationships[PRIMARY_RELATIONSHIP_ID] = RelationshipIdentity().to_dict()
            identity_payload["relationships"] = relationships
            identity_payload["version"] = state.identity.version + 1
            return replace(
                state,
                version=state.version + 1,
                relationship=RelationshipState(),
                identity=IdentityProfile.from_mapping(identity_payload),
                updated_at=now,
            )
        if action == "reset_identity":
            return replace(
                state,
                version=state.version + 1,
                identity=IdentityProfile(),
                updated_at=now,
            )
        if action == "reset_all_ai_state":
            return RuntimeMindState(
                version=state.version + 1,
                last_applied_turn_seq=state.last_applied_turn_seq,
                identity_learning_enabled=state.identity_learning_enabled,
                updated_at=now,
            )
        return state

    def _notify(self, event: StateEvent, accepted: bool) -> None:
        state = self.snapshot()
        with self._lock:
            callbacks = tuple(self._subscribers)
        for callback in callbacks:
            try:
                callback(state, event, accepted)
            except Exception as exc:  # noqa: BLE001
                self._log("debug", "[state reducer] subscriber failed: %s", exc)

    def _log(self, level: str, message: str, *args: Any) -> None:
        logger = self._logger
        if logger is None:
            return
        safe_log(logger, level, message, *args)


__all__ = ["StateReducer"]
