# -*- coding: utf-8 -*-
"""Persistent Individual Core V77.

Đây là lớp hợp nhất state dài hạn cho *cách Eris tồn tại cạnh user*, không thay thế
các subsystem giàu có đã có (PersistentInternalState, ActiveConcern, Life-Horizon,
ConversationTrajectory, LivingUserMemory). Nó làm ba việc mà các lớp cũ chưa có
một authority chung:

1. Giữ Interaction Ecology xuyên restart: gần đây Eris hỏi/nói/im theo nhịp nào.
2. Giữ Relational Plan nhiều lượt: "làm tôi vui lên", "ở cạnh tôi", "đừng hỏi nhiều".
3. Học rất chậm operational self-model + habit từ outcome có provenance, có journal
   để rollback. Không sửa personality prompt trực tiếp.

LLM vẫn chỉ là một cơ quan tạo/appraise candidate. State này không tự viết lời nói.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
import json
import math
import os
import re
import threading
import time
from pathlib import Path
from typing import Any, Mapping, Sequence

from memory.json_store_io import atomic_write_json
from runtime.workspace_manager import default_eris_home
from runtime.interaction_ecology import (
    InteractionEcologyRuntime, InteractionEcologyState, EcologyCandidateBias,
    MODE_ASK, MODE_COMMENT, MODE_MUTTER, MODE_SCREEN_REACT, MODE_SHARE, MODE_SILENCE,
    MODE_STORY, MODE_TEASE, MODE_SUPPORT, MODE_CALLBACK, MODE_TASK, MODE_REPAIR,
    RESPONSE_NONE, classify_interaction_mode, response_expectation_for_mode,
)
from runtime.relational_plan import RelationalPlanManager
from runtime.temporal_memory_policy import project_records
from runtime.continuous_self_dynamics import ContinuousSelfDynamics, ContinuousSelfState


INDIVIDUAL_SCHEMA = "eris.persistent_individual.v77"
STATE_VERSION = 2


def _env_flag(name: str, default: bool = False) -> bool:
    """Đọc feature flag an toàn; dùng cho các cơ chế học có drift dài hạn."""
    raw = str(os.environ.get(name, "1" if default else "0") or "").strip().casefold()
    return raw in {"1", "true", "yes", "on", "enabled"}


def _clamp01(value: Any, default: float = 0.0) -> float:
    try:
        n = float(value)
    except (TypeError, ValueError):
        n = default
    if not math.isfinite(n):
        n = default
    return max(0.0, min(1.0, n))


def _compact(value: Any, limit: int = 420) -> str:
    text = " ".join(str(value or "").split()).strip()
    return text if len(text) <= limit else text[: max(0, limit - 1)].rstrip() + "…"


@dataclass(slots=True)
class OperationalSelfModel:
    """Self-model *vận hành*, không phải lời tự sự public.

    Score là tendency được học chậm từ trace thật. Nó không được Surface phát ngôn
    như một fact về "tâm hồn" của Eris.
    """

    over_questioning: float = 0.25
    topic_drift_tendency: float = 0.20
    over_talking: float = 0.18
    silence_tolerance: float = 0.58
    ambient_comment_comfort: float = 0.46
    correction_sensitivity: float = 0.55
    evidence_count: dict[str, int] = field(default_factory=dict)
    revision: int = 0
    updated_at: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["schema"] = "eris.operational_self_model.v76"
        for key in (
            "over_questioning", "topic_drift_tendency", "over_talking",
            "silence_tolerance", "ambient_comment_comfort", "correction_sensitivity",
        ):
            payload[key] = round(_clamp01(payload[key]), 4)
        return payload

    @classmethod
    def from_dict(cls, data: Mapping[str, Any] | None) -> "OperationalSelfModel":
        if not isinstance(data, Mapping):
            return cls(updated_at=time.time())
        return cls(
            over_questioning=_clamp01(data.get("over_questioning", 0.25), 0.25),
            topic_drift_tendency=_clamp01(data.get("topic_drift_tendency", 0.20), 0.20),
            over_talking=_clamp01(data.get("over_talking", 0.18), 0.18),
            silence_tolerance=_clamp01(data.get("silence_tolerance", 0.58), 0.58),
            ambient_comment_comfort=_clamp01(data.get("ambient_comment_comfort", 0.46), 0.46),
            correction_sensitivity=_clamp01(data.get("correction_sensitivity", 0.55), 0.55),
            evidence_count={str(k): max(0, int(v or 0)) for k, v in dict(data.get("evidence_count", {}) or {}).items()},
            revision=max(0, int(data.get("revision", 0) or 0)),
            updated_at=float(data.get("updated_at", 0.0) or 0.0),
        )

    def nudge(self, field_name: str, target: float, *, strength: float, evidence_kind: str, now: float) -> None:
        if not hasattr(self, field_name):
            return
        old = _clamp01(getattr(self, field_name, 0.5), 0.5)
        # Identity inertia: feedback một lần không rewrite self-model. Tối đa 6% mỗi
        # observation, feedback trực tiếp mới được strength tương đối cao.
        alpha = max(0.005, min(0.06, float(strength or 0.0)))
        setattr(self, field_name, _clamp01(old * (1.0 - alpha) + _clamp01(target) * alpha))
        self.evidence_count[evidence_kind] = self.evidence_count.get(evidence_kind, 0) + 1
        self.revision += 1
        self.updated_at = now


@dataclass(slots=True)
class HabitPrototype:
    context_key: str
    mode: str
    strength: float = 0.10
    positive_outcomes: int = 0
    negative_outcomes: int = 0
    repetitions: int = 0
    updated_at: float = 0.0
    last_reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["strength"] = round(_clamp01(self.strength), 4)
        return payload

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "HabitPrototype":
        return cls(
            context_key=str(data.get("context_key", "") or ""),
            mode=str(data.get("mode", "") or ""),
            strength=_clamp01(data.get("strength", 0.10), 0.10),
            positive_outcomes=max(0, int(data.get("positive_outcomes", 0) or 0)),
            negative_outcomes=max(0, int(data.get("negative_outcomes", 0) or 0)),
            repetitions=max(0, int(data.get("repetitions", 0) or 0)),
            updated_at=float(data.get("updated_at", 0.0) or 0.0),
            last_reason=str(data.get("last_reason", "") or ""),
        )


@dataclass(slots=True)
class PersistentIndividualState:
    version: int = STATE_VERSION
    revision: int = 0
    created_at: float = 0.0
    updated_at: float = 0.0
    ecology: InteractionEcologyState = field(default_factory=InteractionEcologyState)
    self_model: OperationalSelfModel = field(default_factory=OperationalSelfModel)
    habits: list[HabitPrototype] = field(default_factory=list)
    relational_plans: dict[str, Any] = field(default_factory=dict)
    # V77: self-process liên tục nằm trong cùng persistent authority, không phải
    # một JSON island mới. Nhờ vậy restart giữ causal history của chính ERIS.
    continuous_self: ContinuousSelfState = field(default_factory=ContinuousSelfState)
    last_context_key: str = ""
    last_public_mode: str = ""
    last_public_autonomous: bool = False
    last_public_at: float = 0.0
    last_user_at: float = 0.0
    startup_count: int = 0
    last_startup_at: float = 0.0
    journal: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": INDIVIDUAL_SCHEMA,
            "version": self.version,
            "revision": self.revision,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "ecology": self.ecology.to_dict(),
            "self_model": self.self_model.to_dict(),
            "habits": [item.to_dict() for item in self.habits[-64:]],
            "relational_plans": dict(self.relational_plans or {}),
            "continuous_self": self.continuous_self.to_dict(),
            "last_context_key": self.last_context_key,
            "last_public_mode": self.last_public_mode,
            "last_public_autonomous": self.last_public_autonomous,
            "last_public_at": self.last_public_at,
            "last_user_at": self.last_user_at,
            "startup_count": self.startup_count,
            "last_startup_at": self.last_startup_at,
            "journal": list(self.journal[-120:]),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any] | None) -> "PersistentIndividualState":
        now = time.time()
        if not isinstance(data, Mapping):
            return cls(created_at=now, updated_at=now)
        return cls(
            version=max(1, int(data.get("version", STATE_VERSION) or STATE_VERSION)),
            revision=max(0, int(data.get("revision", 0) or 0)),
            created_at=float(data.get("created_at", now) or now),
            updated_at=float(data.get("updated_at", now) or now),
            ecology=InteractionEcologyState.from_dict(data.get("ecology") if isinstance(data.get("ecology"), Mapping) else {}),
            self_model=OperationalSelfModel.from_dict(data.get("self_model") if isinstance(data.get("self_model"), Mapping) else {}),
            habits=[HabitPrototype.from_dict(row) for row in (data.get("habits", ()) or ()) if isinstance(row, Mapping)][-64:],
            relational_plans=dict(data.get("relational_plans", {}) or {}),
            continuous_self=ContinuousSelfState.from_dict(
                data.get("continuous_self") if isinstance(data.get("continuous_self"), Mapping) else {}
            ),
            last_context_key=str(data.get("last_context_key", "") or ""),
            last_public_mode=str(data.get("last_public_mode", "") or ""),
            last_public_autonomous=bool(data.get("last_public_autonomous", False)),
            last_public_at=float(data.get("last_public_at", 0.0) or 0.0),
            last_user_at=float(data.get("last_user_at", 0.0) or 0.0),
            startup_count=max(0, int(data.get("startup_count", 0) or 0)),
            last_startup_at=float(data.get("last_startup_at", 0.0) or 0.0),
            journal=[dict(row) for row in (data.get("journal", ()) or ()) if isinstance(row, Mapping)][-120:],
        )


class PersistentIndividualRuntime:
    """Authority duy nhất cho state V77, persist JSON atomic và migrate V76."""

    def __init__(self, *, path: Path | None = None, logger: Any = None) -> None:
        self.path = path or (default_eris_home() / "state" / "persistent_individual_v77.json")
        self.legacy_path = (default_eris_home() / "state" / "persistent_individual_v76.json") if path is None else None
        self.logger = logger
        self._lock = threading.RLock()
        self.state = self._load()
        self.ecology = InteractionEcologyRuntime(self.state.ecology, logger=logger)
        self.plans = RelationalPlanManager.from_dict(self.state.relational_plans, logger=logger)
        self.self_dynamics = ContinuousSelfDynamics(self.state.continuous_self, logger=logger)
        self.continuous_self_enabled = _env_flag("ERIS_V77_CONTINUOUS_SELF", True)
        # Short-term ecology/plans chạy mặc định. Hai cơ chế có thể làm ERIS drift
        # qua tuần/tháng được ship cùng patch nhưng mặc định OFF để rollout có thể rollback.
        self.self_model_learning_enabled = _env_flag("ERIS_V76_SELF_MODEL_LEARNING", False)
        self.habit_learning_enabled = _env_flag("ERIS_V76_HABIT_LEARNING", False)
        self._last_continuous_checkpoint_at = 0.0

    def note_startup(self, *, now: float | None = None) -> dict[str, Any]:
        current = float(now or time.time())
        with self._lock:
            previous = self.state.last_startup_at or self.state.updated_at
            gap = max(0.0, current - previous) if previous > 0 else 0.0
            self.state.startup_count += 1
            self.state.last_startup_at = current
            self._touch(current)
            self._journal("startup", {"gap_seconds": round(gap, 3)}, now=current)
            self._save()
        return self.startup_appraisal(now=current, reentry_gap_seconds=gap)

    def startup_appraisal(self, *, now: float | None = None, reentry_gap_seconds: float | None = None) -> dict[str, Any]:
        current = float(now or time.time())
        local = time.localtime(current)
        hour = int(local.tm_hour)
        if reentry_gap_seconds is None:
            reentry_gap_seconds = max(0.0, current - self.state.updated_at) if self.state.updated_at else 0.0
        late_night = hour >= 1 and hour <= 5
        short_return = 0 < reentry_gap_seconds <= 3 * 3600
        return {
            "schema": "eris.startup_appraisal.v77",
            "local_hour": hour,
            "reentry_gap_seconds": round(float(reentry_gap_seconds), 3),
            "late_night": late_night,
            "short_return": short_return,
            "avoid_generic_greeting": bool(short_return or late_night),
            "opening_modes": [MODE_COMMENT, MODE_MUTTER, MODE_SILENCE] if (short_return or late_night) else [MODE_COMMENT, MODE_SHARE, MODE_SILENCE],
            "rule": (
                "Startup is a re-entry appraisal, not a mandatory greeting. A contextual one-liner is optional; silence is valid. "
                "Never invent what the user did while ERIS was offline."
            ),
        }

    def observe_user_turn(
        self,
        text: str,
        *,
        turn_ref: str = "",
        context_key: str = "",
        trajectory: Mapping[str, Any] | None = None,
        now: float | None = None,
    ) -> dict[str, Any]:
        current = float(now or time.time())
        clean = _compact(text, 1600)
        with self._lock:
            # Nếu user quay lại sau một autonomous action, đó chỉ là weak outcome:
            # đủ để habit học "mode này không phá nhịp", không đủ để suy user thích.
            if (
                self.state.last_public_autonomous
                and self.state.last_public_mode
                and self.state.last_public_at > 0
                and current - self.state.last_public_at <= 600.0
            ):
                if self.habit_learning_enabled:
                    self._learn_habit(
                        context_key=self.state.last_context_key or context_key or self._context_key(now=current),
                        mode=self.state.last_public_mode,
                        positive=True,
                        strength=0.018,
                        reason="user_voluntary_return_after_autonomous_action",
                        now=current,
                    )

            self.ecology.observe_user_turn(now=current)
            plan = self.plans.observe_user_turn(clean, turn_ref=turn_ref, now=current)
            if self.continuous_self_enabled:
                # User turn là external support thật. Chỉ đây/evidence typed mới
                # được phép recharge commitment; assistant speech thì không.
                trajectory_view = dict(trajectory or {})
                self.self_dynamics.observe_user_turn(
                    clean, turn_ref=turn_ref, relational_plan=self.plans.projection(now=current),
                    trajectory=trajectory_view, now=current,
                )
            self.state.last_user_at = current
            if self.self_model_learning_enabled or self.habit_learning_enabled:
                self._learn_direct_feedback(clean, now=current)
            self._sync_substates()
            self._touch(current)
            self._journal("user_turn", {"turn_ref": turn_ref, "plan": getattr(plan, "goal_kind", "")}, now=current)
            self._save()
            return self.projection(now=current, context_key=context_key)

    def observe_assistant_commit(
        self,
        text: str,
        *,
        speech_act: str = "",
        candidate_kind: str = "",
        autonomous: bool = False,
        context_key: str = "",
        now: float | None = None,
    ) -> dict[str, Any]:
        current = float(now or time.time())
        mode = classify_interaction_mode(
            speech_act=speech_act,
            candidate_kind=candidate_kind,
            action_kind="speak",
            text=text,
        )
        expectation = response_expectation_for_mode(mode, speech_act=speech_act)
        with self._lock:
            self.ecology.observe_public_action(
                mode=mode,
                response_expectation=expectation,
                autonomous=autonomous,
                now=current,
            )
            self.plans.observe_public_action(mode=mode, autonomous=autonomous, now=current)
            if self.continuous_self_enabled:
                self.self_dynamics.observe_public_action(
                    mode=mode, autonomous=autonomous, response_expectation=expectation, now=current
                )
            self.state.last_public_mode = mode
            self.state.last_public_autonomous = bool(autonomous)
            self.state.last_public_at = current
            self.state.last_context_key = context_key or self._context_key(now=current)

            # Self-model vận hành đọc chính thống kê hành vi. Đây là tự quan sát,
            # không phải LLM tự viết tiểu sử cho chính nó. Học drift dài hạn mặc
            # định OFF và chỉ bật bằng feature flag sau soak test.
            if self.self_model_learning_enabled:
                if mode == MODE_ASK:
                    self.state.self_model.nudge(
                        "over_questioning", min(1.0, self.ecology.state.question_fatigue + 0.15),
                        strength=0.018, evidence_kind="observed_question", now=current,
                    )
                if autonomous and self.ecology.state.unsolicited_streak >= 2:
                    self.state.self_model.nudge(
                        "over_talking", min(1.0, 0.45 + 0.15 * self.ecology.state.unsolicited_streak),
                        strength=0.018, evidence_kind="autonomous_public_streak", now=current,
                    )
                if mode in {MODE_COMMENT, MODE_MUTTER, MODE_SCREEN_REACT}:
                    self.state.self_model.nudge(
                        "ambient_comment_comfort", 0.70, strength=0.008,
                        evidence_kind="ambient_mode_used", now=current,
                    )

            self._sync_substates()
            self._touch(current)
            self._journal(
                "assistant_commit",
                {"mode": mode, "expectation": expectation, "autonomous": bool(autonomous)},
                now=current,
            )
            self._save()
        return {"mode": mode, "response_expectation": expectation}

    def candidate_bias(
        self,
        *,
        speech_act: str = "",
        candidate_kind: str = "",
        action_kind: str = "speak",
        text: str = "",
        autonomous: bool = True,
        seconds_since_user: float = 999999.0,
        grounded_environment_novelty: float = 0.0,
        response_expectation: str = "",
    ) -> EcologyCandidateBias:
        plan = self.plans.projection()
        preferred = tuple(plan.get("effective_preferred_modes", ()) or ()) if plan.get("active") else ()
        avoid = tuple(plan.get("avoid_modes", ()) or ()) if plan.get("active") else ()
        bias = self.ecology.bias_candidate(
            speech_act=speech_act,
            candidate_kind=candidate_kind,
            action_kind=action_kind,
            text=text,
            autonomous=autonomous,
            seconds_since_user=seconds_since_user,
            grounded_environment_novelty=grounded_environment_novelty,
            plan_preferred_modes=preferred,
            plan_avoid_modes=avoid,
            response_expectation=response_expectation,
        )
        # Operational self-model chỉ là bias nhỏ. Nó không được quyền hard-block
        # task/repair. Tự nhận ra mình hay hỏi quá phải dẫn tới inhibition thật.
        multiplier = bias.multiplier
        reason = bias.reason
        if bias.mode == MODE_ASK and self.state.self_model.over_questioning >= 0.45:
            factor = max(0.40, 1.0 - 0.34 * self.state.self_model.over_questioning)
            multiplier *= factor
            reason = f"{reason},self_model_over_questioning={self.state.self_model.over_questioning:.2f}"
        if self.continuous_self_enabled:
            self_bias = self.self_dynamics.action_bias(mode=bias.mode, action_kind=action_kind)
            multiplier *= self_bias.multiplier
            reason = f"{reason},{self_bias.reason}"
        return EcologyCandidateBias(
            bias.mode, bias.response_expectation, multiplier=max(0.03, min(1.80, multiplier)),
            hard_suppress=bias.hard_suppress, reason=reason,
        )

    def observe_private_development(
        self,
        *,
        summary: str,
        material_kind: str = "",
        cause_kind: str = "",
        source_refs: Sequence[str] = (),
        semantic_gain: float = 0.0,
        topic_id: str = "",
        now: float | None = None,
    ) -> dict[str, Any]:
        """Receipt cho private cognition V77.

        Thought có quyền tiếp tục, cạn hoặc chết trong private. Method này không
        tạo ExpressionUrge và không coi việc "nghĩ ra" là lý do bắt buộc phải nói.
        """
        current = float(now or time.time())
        with self._lock:
            if self.continuous_self_enabled:
                self.self_dynamics.observe_private_development(
                    summary=summary, material_kind=material_kind, cause_kind=cause_kind,
                    source_refs=source_refs, semantic_gain=semantic_gain, topic_id=topic_id, now=current,
                )
            self._sync_substates()
            self._touch(current)
            self._journal("private_development", {"kind": material_kind, "cause": cause_kind, "gain": round(float(semantic_gain or 0.0), 3)}, now=current)
            self._save()
            return self.self_dynamics.projection(now=current) if self.continuous_self_enabled else {}

    def curiosity_ask_value(
        self,
        *,
        epistemic_value: float,
        future_usefulness: float,
        relationship_fit: float,
        can_observe_instead: bool = False,
        can_infer_instead: bool = False,
        memory_already_has_answer: bool = False,
        privacy_cost: float = 0.0,
    ) -> float:
        """Giá trị *hỏi user*, không phải giá trị tò mò nói chung.

        Curiosity có thể được thỏa bằng quan sát, suy luận có uncertainty hoặc chờ
        context tốt hơn. Một database còn ô NULL không tạo quyền hỏi.
        """
        positive = _clamp01(epistemic_value) * _clamp01(future_usefulness) * (0.35 + 0.65 * _clamp01(relationship_fit))
        cost = (
            0.45 * self.ecology.state.question_fatigue
            + 0.24 * self.ecology.state.public_fatigue
            + 0.30 * _clamp01(privacy_cost)
            + (0.28 if can_observe_instead else 0.0)
            + (0.22 if can_infer_instead else 0.0)
            + (0.55 if memory_already_has_answer else 0.0)
        )
        return max(-1.0, min(1.0, positive - cost))

    def projection_for_host(
        self,
        host: Any,
        *,
        now: float | None = None,
        context_key: str = "",
    ) -> dict[str, Any]:
        """Projection tiện dụng đọc canonical LivingUser records nếu runtime có.

        Chỉ đọc object record đã curated; không kéo private reasoning hay raw OCR.
        """
        records: Sequence[Any] = ()
        try:
            daemon = getattr(host, "private_mind_daemon", None)
            system = getattr(daemon, "system", None) or getattr(host, "private_mind_system", None)
            store = getattr(system, "user_model_store", None)
            all_fn = getattr(store, "all", None)
            if callable(all_fn):
                records = tuple(all_fn())[:16]
        except Exception:
            records = ()

        # Source hiện tại đã có PersistentInternalState + EndogenousDriveEngine.
        # V77 vẫn không tạo drive store thứ hai, chỉ bridge state sẵn có vào một
        # projection thống nhất để planner không phải nhìn 5 subsystem như 5 vũ trụ.
        existing_internal: dict[str, Any] = {}
        endogenous: dict[str, Any] = {}
        try:
            getter = getattr(host, "_get_internal_state", None)
            internal = getter() if callable(getter) else None
            to_dict = getattr(internal, "to_dict", None)
            if callable(to_dict):
                raw = dict(to_dict() or {})
                keep = (
                    "valence", "arousal", "curiosity", "boredom", "social_energy",
                    "attachment", "irritation", "fatigue", "silence_pressure",
                    "social_pressure", "curiosity_pressure", "restraint_pressure",
                    "cognitive_tension", "goal_persistence", "mental_processes",
                    "conversation_dynamics", "relationship_dynamics", "action_memory",
                )
                existing_internal = {key: raw[key] for key in keep if key in raw}
        except Exception:
            existing_internal = {}
        try:
            rolling = getattr(host, "rolling_internal_state", None)
            to_dict = getattr(rolling, "to_dict", None)
            if callable(to_dict):
                endogenous = dict(to_dict() or {})
        except Exception:
            endogenous = {}

        active_concerns: Sequence[Mapping[str, Any]] = ()
        trajectory: Mapping[str, Any] = {}
        try:
            concern_runtime = getattr(host, "active_concern_runtime", None)
            snapshot_fn = getattr(concern_runtime, "snapshot", None)
            if callable(snapshot_fn):
                active_concerns = tuple(snapshot_fn(now=now, limit=6) or ())
        except Exception:
            active_concerns = ()
        try:
            trajectory_runtime = getattr(host, "conversation_trajectory_runtime", None)
            snapshot_fn = getattr(trajectory_runtime, "snapshot", None)
            if callable(snapshot_fn):
                trajectory_state = snapshot_fn(now=now)
                to_dict = getattr(trajectory_state, "to_dict", None)
                trajectory = dict(to_dict() or {}) if callable(to_dict) else {}
        except Exception:
            trajectory = {}

        return self.projection(
            now=now, context_key=context_key, memory_records=records,
            existing_internal=existing_internal, endogenous=endogenous,
            active_concerns=active_concerns, trajectory=trajectory,
        )

    def projection(
        self,
        *,
        now: float | None = None,
        context_key: str = "",
        memory_records: Sequence[Any] = (),
        existing_internal: Mapping[str, Any] | None = None,
        endogenous: Mapping[str, Any] | None = None,
        active_concerns: Sequence[Mapping[str, Any]] = (),
        trajectory: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        current = float(now or time.time())
        with self._lock:
            ecology = self.ecology.projection(now=current)
            plan = self.plans.projection(now=current)
            habits = self._habit_projection(context_key or self._context_key(now=current), now=current)
            continuous_self = {}
            if self.continuous_self_enabled:
                self.self_dynamics.tick(
                    now=current, persistent_internal=existing_internal, endogenous=endogenous,
                    interaction_ecology=ecology, relational_plan=plan, trajectory=trajectory or {},
                    active_concerns=active_concerns,
                )
                continuous_self = self.self_dynamics.projection(now=current)
                self._sync_substates()
                # Idle self-process phải sống qua restart. Checkpoint tối đa 1 lần/phút
                # để không biến mỗi cheap tick thành một disk write.
                if current - self._last_continuous_checkpoint_at >= 60.0:
                    self._touch(current)
                    self._save()
                    self._last_continuous_checkpoint_at = current
            return {
                "schema": INDIVIDUAL_SCHEMA,
                "revision": self.state.revision,
                "interaction_ecology": ecology,
                "relational_plan": plan,
                "continuous_state_bridge": {
                    "persistent_internal": dict(existing_internal or {}),
                    "endogenous_drives": dict(endogenous or {}),
                    "rule": "Reuse existing InternalState/EndogenousDrive as drives; V77 does not duplicate them.",
                },
                "operational_self_model": self.state.self_model.to_dict(),
                "continuous_self": continuous_self,
                "continuous_self_enabled": self.continuous_self_enabled,
                "learning_flags": {
                    "self_model_learning": self.self_model_learning_enabled,
                    "habit_learning": self.habit_learning_enabled,
                },
                "habit_context": habits,
                "startup_appraisal": self.startup_appraisal(now=current),
                "temporal_memory": project_records(memory_records, now=current, limit=12) if memory_records else {
                    "schema": "eris.temporal_memory_policy.v76", "records": []
                },
                "behavior_contract": (
                    "OBSERVATION->CONTINUOUS_SELF->MOTIVE/COMMITMENT->PLAN->INTERACTION_MODE->CANDIDATES->NEURO->OUTCOME->SELF. "
                    "Private pressure and public pressure are separate. Do not turn curiosity into a question by default; public speech spends pressure and is not the endpoint of private thought."
                ),
            }

    def _learn_direct_feedback(self, text: str, *, now: float) -> None:
        lowered = text.casefold()
        model = self.state.self_model
        if any(x in lowered for x in ("hỏi nhiều", "hỏi liên tục", "spam hỏi", "bớt hỏi", "đừng hỏi")):
            if self.self_model_learning_enabled:
                model.nudge("over_questioning", 0.95, strength=0.06, evidence_kind="direct_user_question_feedback", now=now)
                model.nudge("silence_tolerance", 0.82, strength=0.04, evidence_kind="direct_user_question_feedback", now=now)
            if self.habit_learning_enabled and self.state.last_context_key:
                self._learn_habit(self.state.last_context_key, MODE_ASK, positive=False, strength=0.08, reason="direct_user_question_feedback", now=now)
        if self.self_model_learning_enabled and any(x in lowered for x in ("đổi chủ đề nhanh", "nhảy chủ đề", "lạc chủ đề")):
            model.nudge("topic_drift_tendency", 0.92, strength=0.06, evidence_kind="direct_user_topic_drift_feedback", now=now)
        if self.self_model_learning_enabled and any(x in lowered for x in ("nói nhiều quá", "spam", "lải nhải", "ồn quá")):
            model.nudge("over_talking", 0.92, strength=0.06, evidence_kind="direct_user_public_fatigue_feedback", now=now)
            model.nudge("silence_tolerance", 0.86, strength=0.04, evidence_kind="direct_user_public_fatigue_feedback", now=now)
        if self.self_model_learning_enabled and any(x in lowered for x in ("comment màn hình", "bình luận màn hình", "lèm bèm", "lẩm bẩm")):
            model.nudge("ambient_comment_comfort", 0.82, strength=0.035, evidence_kind="direct_user_ambient_preference", now=now)

    def _learn_habit(
        self,
        context_key: str,
        mode: str,
        *,
        positive: bool,
        strength: float,
        reason: str,
        now: float,
    ) -> None:
        key = str(context_key or "generic")[:160]
        mode = str(mode or MODE_COMMENT)
        habit = next((h for h in self.state.habits if h.context_key == key and h.mode == mode), None)
        if habit is None:
            habit = HabitPrototype(context_key=key, mode=mode, updated_at=now)
            self.state.habits.append(habit)
        habit.repetitions += 1
        if positive:
            habit.positive_outcomes += 1
            target = 0.78
        else:
            habit.negative_outcomes += 1
            target = 0.05
        alpha = max(0.005, min(0.10, float(strength or 0.02)))
        habit.strength = _clamp01(habit.strength * (1.0 - alpha) + target * alpha)
        habit.updated_at = now
        habit.last_reason = reason
        self.state.habits = sorted(self.state.habits, key=lambda h: (h.updated_at, h.strength))[-64:]

    def _habit_projection(self, context_key: str, *, now: float) -> dict[str, Any]:
        relevant = [h for h in self.state.habits if h.context_key == context_key]
        relevant.sort(key=lambda h: h.strength, reverse=True)
        return {
            "context_key": context_key,
            "habits": [h.to_dict() for h in relevant[:8]],
            "preferred_modes": [h.mode for h in relevant if h.strength >= 0.46][:5],
            "rule": "Habits are soft priors learned slowly from outcomes; explicit user boundary always overrides them.",
        }

    @staticmethod
    def _context_key(*, now: float) -> str:
        local = time.localtime(now)
        hour = int(local.tm_hour)
        if 0 <= hour < 6:
            daypart = "late_night"
        elif hour < 12:
            daypart = "morning"
        elif hour < 18:
            daypart = "afternoon"
        else:
            daypart = "evening"
        return f"daypart:{daypart}"

    def _sync_substates(self) -> None:
        self.state.ecology = self.ecology.state
        self.state.relational_plans = self.plans.to_dict()
        self.state.continuous_self = self.self_dynamics.state

    def _touch(self, now: float) -> None:
        self.state.revision += 1
        self.state.updated_at = now

    def _journal(self, kind: str, payload: Mapping[str, Any], *, now: float) -> None:
        self.state.journal.append({"at": round(now, 3), "kind": kind, **dict(payload)})
        self.state.journal = self.state.journal[-120:]

    def _load(self) -> PersistentIndividualState:
        # V77 đổi filename nhưng migrate V76 tự động để không "tái sinh" một ERIS
        # mới chỉ vì upgrade. Khi caller truyền path test/custom thì không đoán path.
        candidates = [self.path]
        if self.legacy_path is not None and self.legacy_path not in candidates:
            candidates.append(self.legacy_path)
        for candidate in candidates:
            try:
                if candidate.is_file() and candidate.stat().st_size > 0:
                    raw = json.loads(candidate.read_text(encoding="utf-8"))
                    state = PersistentIndividualState.from_dict(raw)
                    state.version = STATE_VERSION
                    return state
            except Exception as exc:
                self._log("warning", "[persistent-individual-v77] load degraded path=%s: %s", candidate, exc)
        now = time.time()
        return PersistentIndividualState(created_at=now, updated_at=now)

    def _save(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            atomic_write_json(self.path, self.state.to_dict())
        except Exception as exc:
            self._log("warning", "[persistent-individual-v77] save degraded: %s", exc)

    def _log(self, level: str, message: str, *args: Any) -> None:
        fn = getattr(self.logger, level, None)
        if callable(fn):
            try:
                fn(message, *args)
            except Exception:
                pass


def ensure_persistent_individual_runtime(host: Any) -> PersistentIndividualRuntime:
    runtime = getattr(host, "persistent_individual_runtime", None)
    if isinstance(runtime, PersistentIndividualRuntime):
        return runtime
    runtime = PersistentIndividualRuntime(logger=getattr(host, "logger", None))
    try:
        setattr(host, "persistent_individual_runtime", runtime)
    except Exception:
        pass
    return runtime


__all__ = [
    "INDIVIDUAL_SCHEMA", "OperationalSelfModel", "HabitPrototype",
    "PersistentIndividualState", "PersistentIndividualRuntime",
    "ensure_persistent_individual_runtime",
]
