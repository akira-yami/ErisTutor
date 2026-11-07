# -*- coding: utf-8 -*-
"""ERIS V98 - Shared Activation Nervous System (shadow mode).

V98 KHÔNG thay public governor / V97 trace và cũng KHÔNG biến activation thành
một bảng score mới để chọn candidate. Mục tiêu của file này hẹp nhưng nền tảng:

    nguồn cảm giác / self-state
            |
            v
      ModulationEvent                 (không subsystem nào set field trực tiếp)
            |
            v
      SharedActivationBus             (9 field dùng chung, state liên tục)
            |
            v
      ContinuousDynamicsRuntime       (quán tính + decay + recurrent coupling)
            |
            v
      ExpressionMotorV98              (body / micro-expression / vocalization)

Trong V98 motor mặc định chạy SHADOW: nó chỉ tạo MotorIntent chẩn đoán, tuyệt đối
không tự phát TTS/câu public. Semantic sentence dài vẫn đi đường V97. Ở các version
sau mới có thể dần biến candidate/seed cũ thành modulation/content material.

Thiết kế cố tình local + deterministic. RNG không nằm trong dynamics. Nếu sau này
cần micro-noise để tránh chuyển động quá cơ khí, noise phải có seed cố định trong
replay và chỉ được dùng cho timing/wording nhỏ, không được roulette chọn "ý định".
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
import math
import os
import threading
import time
from typing import Any, Callable, Iterable, Mapping, Sequence


ACTIVATION_SCHEMA = "eris.shared_activation.v98"
MOTOR_SCHEMA = "eris.expression_motor.v98"
MODULATION_SCHEMA = "eris.modulation_event.v98"

ACTIVATION_FIELDS: tuple[str, ...] = (
    "curiosity",
    "boredom",
    "social_need",
    "task_focus",
    "concern_salience",
    "uncertainty",
    "expressive_pressure",
    "inhibition",
    "fatigue",
)

# Đây là homeostatic set-point, KHÔNG phải personality score. Khi không có tín
# hiệu, dynamics từ từ quay về vùng này thay vì reset ngay sau mỗi cycle.
_DEFAULT_BASELINES: dict[str, float] = {
    "curiosity": 0.34,
    "boredom": 0.12,
    "social_need": 0.24,
    "task_focus": 0.30,
    "concern_salience": 0.14,
    "uncertainty": 0.16,
    "expressive_pressure": 0.08,
    "inhibition": 0.30,
    "fatigue": 0.10,
}

# Field có timescale khác nhau. Expressive pressure đổi nhanh hơn fatigue/social.
_HOMEOSTATIC_TAU_SEC: dict[str, float] = {
    "curiosity": 42.0,
    "boredom": 55.0,
    "social_need": 85.0,
    "task_focus": 20.0,
    "concern_salience": 26.0,
    "uncertainty": 24.0,
    "expressive_pressure": 12.0,
    "inhibition": 18.0,
    "fatigue": 120.0,
}


def _clamp01(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = default
    if not math.isfinite(number):
        number = default
    return max(0.0, min(1.0, number))


def _clamp_signed(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = default
    if not math.isfinite(number):
        number = default
    return max(-1.0, min(1.0, number))


def _env_float(name: str, default: float, *, minimum: float, maximum: float) -> float:
    try:
        raw = float(os.getenv(name, str(default)) or default)
    except (TypeError, ValueError):
        raw = default
    return max(minimum, min(maximum, raw))


def _compact(value: Any, limit: int = 120) -> str:
    text = " ".join(str(value or "").split()).strip()
    return text if len(text) <= limit else text[: max(0, limit - 1)].rstrip() + "…"


def _mapping_value(mapping: Mapping[str, Any], *names: str, default: float = 0.0) -> float:
    for name in names:
        if name in mapping:
            return _clamp01(mapping.get(name), default)
    return _clamp01(default)


@dataclass(frozen=True, slots=True)
class ModulationEvent:
    """Input duy nhất được phép tác động SharedActivationBus.

    ``targets`` nói "nguồn này đang kéo field về vùng nào"; dynamics sẽ tiến dần
    tới target theo strength/half-life, không set thẳng. ``impulses`` là xung có
    dấu cho event ngắn (ví dụ user vừa quay lại -> giảm boredom, tăng social).
    """

    source: str
    targets: Mapping[str, float] = field(default_factory=dict)
    impulses: Mapping[str, float] = field(default_factory=dict)
    strength: float = 0.55
    half_life_seconds: float = 1.5
    ttl_seconds: float = 3.0
    created_at: float = 0.0
    event_id: str = ""
    tags: tuple[str, ...] = ()

    def normalized(self, *, now: float) -> "ModulationEvent":
        clean_targets = {
            key: _clamp01(value)
            for key, value in dict(self.targets or {}).items()
            if key in ACTIVATION_FIELDS
        }
        clean_impulses = {
            key: _clamp_signed(value)
            for key, value in dict(self.impulses or {}).items()
            if key in ACTIVATION_FIELDS
        }
        source = _compact(self.source, 96) or "unknown"
        event_id = _compact(self.event_id, 128) or f"{source}:{int(now * 1000)}"
        return ModulationEvent(
            source=source,
            targets=clean_targets,
            impulses=clean_impulses,
            strength=_clamp01(self.strength, 0.55),
            half_life_seconds=max(0.15, min(30.0, float(self.half_life_seconds or 1.5))),
            ttl_seconds=max(0.20, min(120.0, float(self.ttl_seconds or 3.0))),
            created_at=float(self.created_at or now),
            event_id=event_id,
            tags=tuple(dict.fromkeys(_compact(x, 60) for x in self.tags if _compact(x, 60)))[:12],
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": MODULATION_SCHEMA,
            "event_id": self.event_id,
            "source": self.source,
            "targets": dict(self.targets),
            "impulses": dict(self.impulses),
            "strength": round(_clamp01(self.strength), 4),
            "half_life_seconds": round(float(self.half_life_seconds), 4),
            "ttl_seconds": round(float(self.ttl_seconds), 4),
            "created_at": float(self.created_at),
            "tags": list(self.tags),
        }


@dataclass(frozen=True, slots=True)
class ActivationSnapshot:
    revision: int
    timestamp: float
    values: Mapping[str, float]
    tick_count: int = 0
    active_modulators: int = 0

    def value(self, name: str) -> float:
        return _clamp01(dict(self.values).get(name, 0.0))

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": ACTIVATION_SCHEMA,
            "revision": int(self.revision),
            "timestamp": float(self.timestamp),
            "tick_count": int(self.tick_count),
            "active_modulators": int(self.active_modulators),
            "values": {key: round(self.value(key), 4) for key in ACTIVATION_FIELDS},
        }


@dataclass(frozen=True, slots=True)
class MotorIntent:
    """Motor intent không chứa câu thoại; nó chỉ nói actuator nào đang muốn chạy."""

    channel: str
    activation: float
    action_hint: str
    emitted_at: float
    source_revision: int
    shadow: bool = True
    reasons: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": MOTOR_SCHEMA,
            "channel": self.channel,
            "activation": round(_clamp01(self.activation), 4),
            "action_hint": self.action_hint,
            "emitted_at": float(self.emitted_at),
            "source_revision": int(self.source_revision),
            "shadow": bool(self.shadow),
            "reasons": list(self.reasons),
        }


class SharedActivationBus:
    """State 9 chiều dùng chung.

    Public API cố tình KHÔNG có ``set_field``. Subsystem chỉ ``submit`` event.
    Chỉ ContinuousDynamicsRuntime dùng ``_commit_from_dynamics`` để cập nhật state.
    """

    def __init__(
        self,
        *,
        initial_values: Mapping[str, float] | None = None,
        baselines: Mapping[str, float] | None = None,
        history_limit: int = 160,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.clock = clock
        self._lock = threading.RLock()
        self._baselines = dict(_DEFAULT_BASELINES)
        for key, value in dict(baselines or {}).items():
            if key in ACTIVATION_FIELDS:
                self._baselines[key] = _clamp01(value, self._baselines[key])
        self._values = dict(self._baselines)
        for key, value in dict(initial_values or {}).items():
            if key in ACTIVATION_FIELDS:
                self._values[key] = _clamp01(value, self._values[key])
        self._revision = 0
        self._tick_count = 0
        self._timestamp = float(self.clock())
        self._pending: deque[ModulationEvent] = deque()
        self._event_history: deque[dict[str, Any]] = deque(maxlen=max(16, int(history_limit)))
        self._active_modulator_count = 0

    @property
    def baselines(self) -> dict[str, float]:
        with self._lock:
            return dict(self._baselines)

    def submit(self, event: ModulationEvent) -> bool:
        if not isinstance(event, ModulationEvent):
            raise TypeError("SharedActivationBus chỉ nhận ModulationEvent")
        now = float(self.clock())
        clean = event.normalized(now=now)
        if not clean.targets and not clean.impulses:
            return False
        with self._lock:
            self._pending.append(clean)
            self._event_history.append(clean.to_dict())
        return True

    def snapshot(self) -> ActivationSnapshot:
        with self._lock:
            return ActivationSnapshot(
                revision=self._revision,
                timestamp=self._timestamp,
                values=dict(self._values),
                tick_count=self._tick_count,
                active_modulators=self._active_modulator_count,
            )

    def recent_events(self, limit: int = 20) -> tuple[dict[str, Any], ...]:
        with self._lock:
            count = max(1, min(160, int(limit)))
            return tuple(dict(item) for item in list(self._event_history)[-count:])

    # Hai method dưới là internal contract giữa bus và dynamics. Không export một
    # setter công khai vì như vậy các module cũ sẽ lại "chọc điện cực" thẳng vào field.
    def _drain_pending(self) -> tuple[ModulationEvent, ...]:
        with self._lock:
            items = tuple(self._pending)
            self._pending.clear()
            return items

    def _commit_from_dynamics(
        self,
        values: Mapping[str, float],
        *,
        now: float,
        active_modulators: int,
    ) -> ActivationSnapshot:
        clean = {
            key: _clamp01(dict(values).get(key, self._values.get(key, 0.0)))
            for key in ACTIVATION_FIELDS
        }
        with self._lock:
            self._values = clean
            self._revision += 1
            self._tick_count += 1
            self._timestamp = float(now)
            self._active_modulator_count = max(0, int(active_modulators))
            return self.snapshot()


class ExpressionMotorV98:
    """Motor layer pre-semantic.

    Ba actuator được xét ĐỘC LẬP, không có "candidate winner". Vì V98 shadow nên
    ``vocalization`` chỉ có thể tạo ``speech_impulse_without_semantic_content``;
    nó chưa được quyền viết câu, gọi LLM hay mở TTS.
    """

    _THRESHOLDS = {
        "body": 0.47,
        "micro_expression": 0.44,
        "vocalization": 0.62,
    }
    _COOLDOWNS = {
        "body": 7.0,
        "micro_expression": 5.0,
        "vocalization": 16.0,
    }

    def __init__(
        self,
        *,
        shadow: bool = True,
        callback: Callable[[MotorIntent], None] | None = None,
        clock: Callable[[], float] = time.time,
        logger: Any | None = None,
    ) -> None:
        self.shadow = bool(shadow)
        self.callback = callback
        self.clock = clock
        self.logger = logger
        self._lock = threading.RLock()
        self._last_emitted: dict[str, float] = {key: -1e12 for key in self._THRESHOLDS}
        self._armed: dict[str, bool] = {key: True for key in self._THRESHOLDS}
        self._intents: deque[MotorIntent] = deque(maxlen=96)

    def evaluate(self, snapshot: ActivationSnapshot, *, now: float | None = None) -> tuple[MotorIntent, ...]:
        current = float(now if now is not None else self.clock())
        v = {key: snapshot.value(key) for key in ACTIVATION_FIELDS}

        # Không dùng max() để chọn một winner. Mỗi channel có hệ động lực riêng.
        drives = {
            "body": _clamp01(
                0.26 * v["curiosity"]
                + 0.24 * v["boredom"]
                + 0.22 * v["social_need"]
                + 0.18 * v["concern_salience"]
                + 0.10 * (1.0 - v["fatigue"])
            ),
            "micro_expression": _clamp01(
                0.31 * v["concern_salience"]
                + 0.24 * v["curiosity"]
                + 0.18 * v["social_need"]
                + 0.13 * v["uncertainty"]
                + 0.08 * v["expressive_pressure"]
                - 0.12 * v["inhibition"]
            ),
            "vocalization": _clamp01(
                0.52 * v["expressive_pressure"]
                + 0.15 * v["social_need"]
                + 0.11 * v["concern_salience"]
                + 0.08 * v["boredom"]
                + 0.06 * v["curiosity"]
                - 0.27 * v["inhibition"]
                - 0.15 * v["task_focus"]
                - 0.13 * v["fatigue"]
            ),
        }

        emitted: list[MotorIntent] = []
        with self._lock:
            for channel in ("body", "micro_expression", "vocalization"):
                drive = drives[channel]
                threshold = self._THRESHOLDS[channel]
                # Hysteresis: sau khi fire phải hạ thật xuống mới arm lại. Không
                # spam intent chỉ vì activation rung quanh ngưỡng 60-70 ms.
                if drive <= threshold - 0.10:
                    self._armed[channel] = True
                if not self._armed[channel]:
                    continue
                if drive < threshold:
                    continue
                if current - self._last_emitted[channel] < self._COOLDOWNS[channel]:
                    continue

                intent = MotorIntent(
                    channel=channel,
                    activation=drive,
                    action_hint=self._action_hint(channel, v),
                    emitted_at=current,
                    source_revision=snapshot.revision,
                    shadow=self.shadow,
                    reasons=self._reasons(channel, v),
                )
                self._last_emitted[channel] = current
                self._armed[channel] = False
                self._intents.append(intent)
                emitted.append(intent)

                # V98 shadow: callback production bị chặn cứng. Không có đường
                # vô tình mở loa chỉ vì ai đó truyền callback vào constructor.
                if not self.shadow and callable(self.callback):
                    self.callback(intent)
                self._log(intent)
        return tuple(emitted)

    def drain_intents(self) -> tuple[MotorIntent, ...]:
        with self._lock:
            items = tuple(self._intents)
            self._intents.clear()
            return items

    def recent_intents(self, limit: int = 16) -> tuple[MotorIntent, ...]:
        with self._lock:
            return tuple(list(self._intents)[-max(1, min(96, int(limit))):])

    @staticmethod
    def _action_hint(channel: str, values: Mapping[str, float]) -> str:
        if channel == "vocalization":
            return "speech_impulse_without_semantic_content"
        if channel == "micro_expression":
            if values["concern_salience"] >= 0.58:
                return "concerned_focus"
            if values["curiosity"] >= 0.56:
                return "curious_glance"
            if values["social_need"] >= 0.55:
                return "soft_social_presence"
            return "subtle_state_change"
        if values["task_focus"] >= 0.65:
            return "attend_task"
        if values["boredom"] >= 0.56:
            return "small_fidget"
        if values["social_need"] >= 0.55:
            return "orient_toward_user"
        return "inspect_context"

    @staticmethod
    def _reasons(channel: str, values: Mapping[str, float]) -> tuple[str, ...]:
        ranked = sorted(
            (
                ("curiosity", values["curiosity"]),
                ("boredom", values["boredom"]),
                ("social_need", values["social_need"]),
                ("concern_salience", values["concern_salience"]),
                ("expressive_pressure", values["expressive_pressure"]),
                ("inhibition", values["inhibition"]),
                ("fatigue", values["fatigue"]),
            ),
            key=lambda item: item[1],
            reverse=True,
        )
        return tuple(name for name, value in ranked[:3] if value >= 0.30)

    def _log(self, intent: MotorIntent) -> None:
        if self.logger is None:
            return
        try:
            self.logger.debug(
                "[v98 motor shadow] channel=%s activation=%.3f hint=%s reasons=%s",
                intent.channel,
                intent.activation,
                intent.action_hint,
                ",".join(intent.reasons) or "none",
            )
        except Exception:
            pass


class HostActivationBridgeV98:
    """Bridge local: project state cũ thành MỘT ModulationEvent tonic.

    Đây là migration bridge, không phải authority mới. Nó đọc V77/InternalState/
    Observer rồi map xuống 9 control dimensions. Không semantic generation, không
    candidate, không LLM. Sau V99+ từng subsystem nên tự phát ModulationEvent và
    bridge này có thể mỏng dần rồi bỏ.
    """

    def __init__(self, host: Any, *, clock: Callable[[], float] = time.time) -> None:
        self.host = host
        self.clock = clock
        self._sequence = 0

    def collect(self, *, now: float | None = None) -> tuple[ModulationEvent, ...]:
        current = float(now if now is not None else self.clock())
        internal = self._internal_state()
        rolling = self._rolling_state()
        observer = getattr(self.host, "last_observer_frame", None)

        curiosity = max(
            _mapping_value(internal, "curiosity", "curiosity_pressure", default=0.34),
            _mapping_value(rolling, "curiosity_pressure", "curiosity", default=0.0),
        )
        boredom = _mapping_value(internal, "boredom", default=0.12)
        social_need = max(
            _mapping_value(internal, "social_pressure", "social_energy", "attachment", default=0.24),
            _mapping_value(rolling, "social_pressure", "social_energy", default=0.0),
        )
        fatigue = max(
            _mapping_value(internal, "fatigue", default=0.10),
            _mapping_value(rolling, "fatigue", "resource_caution", default=0.0),
        )
        inhibition = max(
            _mapping_value(internal, "restraint_pressure", default=0.30),
            self._ecology_public_fatigue(),
        )
        concern_salience = self._concern_salience(current)
        uncertainty = _clamp01(getattr(observer, "possible_error", 0.0) if observer is not None else 0.12, 0.12)

        task_focus = 0.30
        if bool(getattr(self.host, "_chat_processing", False)):
            task_focus = max(task_focus, 0.76)
        if observer is not None:
            focus_kind = str(getattr(observer, "focus_kind", "") or "").strip().lower()
            activity = str(getattr(observer, "activity_intent", "") or "").strip().lower()
            salience = _clamp01(getattr(observer, "salience", 0.0))
            if focus_kind not in {"", "unknown", "idle", "ambient"}:
                task_focus = max(task_focus, 0.50 + 0.35 * salience)
            if activity in {"coding", "code", "writing", "reading", "debugging", "studying", "study"}:
                task_focus = max(task_focus, 0.70)

        continuous_public = self._continuous_public_pressure()
        silence_pressure = _mapping_value(internal, "silence_pressure", default=0.0)
        expressive_pressure = max(continuous_public, silence_pressure)

        # Idle chỉ modulation nhẹ; nó không tự sinh topic. Topic/content vẫn phải
        # đến concern/material ở tầng semantic. Đây chỉ là "muốn phát tín hiệu".
        idle_seconds = self._idle_seconds(current)
        if idle_seconds >= 90.0:
            boredom = max(boredom, min(0.66, 0.18 + idle_seconds / 1800.0))
            social_need = max(social_need, min(0.62, 0.20 + idle_seconds / 2400.0))
        if self._speech_floor_busy():
            inhibition = max(inhibition, 0.92)
            expressive_pressure = min(expressive_pressure, 0.12)

        self._sequence += 1
        event = ModulationEvent(
            source="host_projection_v98",
            targets={
                "curiosity": curiosity,
                "boredom": boredom,
                "social_need": social_need,
                "task_focus": task_focus,
                "concern_salience": concern_salience,
                "uncertainty": uncertainty,
                "expressive_pressure": expressive_pressure,
                "inhibition": inhibition,
                "fatigue": fatigue,
            },
            strength=0.62,
            half_life_seconds=1.2,
            ttl_seconds=2.2,
            created_at=current,
            event_id=f"host_projection_v98:{self._sequence}",
            tags=("migration_bridge", "local_only", "tonic"),
        )
        return (event,)

    def user_return_event(self, *, now: float | None = None) -> ModulationEvent:
        """Xung ngắn khi user vừa tương tác; không chứa nội dung câu user."""
        current = float(now if now is not None else self.clock())
        self._sequence += 1
        return ModulationEvent(
            source="user_presence_v98",
            impulses={
                "social_need": 0.22,
                "curiosity": 0.12,
                "boredom": -0.28,
                "expressive_pressure": -0.08,
            },
            strength=0.72,
            half_life_seconds=0.8,
            ttl_seconds=2.0,
            created_at=current,
            event_id=f"user_presence_v98:{self._sequence}",
            tags=("external_event", "user_presence"),
        )

    def public_expression_event(self, *, now: float | None = None) -> ModulationEvent:
        """Refractory sau khi Eris đã commit một biểu đạt public thật.

        Đây là negative feedback quan trọng: nói xong phải làm expressive pressure
        tụt và inhibition tăng tạm thời, nếu không recurrent loop sẽ tự nuôi spam.
        """
        current = float(now if now is not None else self.clock())
        self._sequence += 1
        return ModulationEvent(
            source="public_expression_refractory_v98",
            impulses={
                "expressive_pressure": -0.62,
                "social_need": -0.18,
                "concern_salience": -0.08,
                "inhibition": 0.32,
            },
            strength=0.88,
            half_life_seconds=1.6,
            ttl_seconds=4.5,
            created_at=current,
            event_id=f"public_expression_refractory_v98:{self._sequence}",
            tags=("negative_feedback", "refractory", "public_commit"),
        )

    def _internal_state(self) -> dict[str, Any]:
        try:
            getter = getattr(self.host, "_get_internal_state", None)
            state = getter() if callable(getter) else None
            to_dict = getattr(state, "to_dict", None)
            return dict(to_dict() or {}) if callable(to_dict) else {}
        except Exception:
            return {}

    def _rolling_state(self) -> dict[str, Any]:
        try:
            state = getattr(self.host, "rolling_internal_state", None)
            to_dict = getattr(state, "to_dict", None)
            return dict(to_dict() or {}) if callable(to_dict) else {}
        except Exception:
            return {}

    def _concern_salience(self, now: float) -> float:
        try:
            runtime = getattr(self.host, "active_concern_runtime", None)
            snapshot_fn = getattr(runtime, "snapshot", None)
            rows = tuple(snapshot_fn(now=now, limit=6) or ()) if callable(snapshot_fn) else ()
            values: list[float] = []
            for row in rows:
                if not isinstance(row, Mapping):
                    continue
                values.append(max(
                    _mapping_value(row, "activation", "intensity", "pressure", "salience", default=0.0),
                    _mapping_value(row, "priority", default=0.0),
                ))
            return max(values, default=0.14)
        except Exception:
            return 0.14

    def _continuous_public_pressure(self) -> float:
        try:
            runtime = getattr(self.host, "persistent_individual_runtime", None)
            dynamics = getattr(runtime, "self_dynamics", None)
            state = getattr(dynamics, "state", None)
            return _clamp01(getattr(state, "public_action_pressure", 0.0))
        except Exception:
            return 0.0

    def _ecology_public_fatigue(self) -> float:
        try:
            runtime = getattr(self.host, "persistent_individual_runtime", None)
            ecology = getattr(runtime, "ecology", None)
            state = getattr(ecology, "state", None)
            return _clamp01(getattr(state, "public_fatigue", 0.0))
        except Exception:
            return 0.0

    def _idle_seconds(self, now: float) -> float:
        try:
            clock = getattr(self.host, "interaction_clock", None)
            if clock is not None:
                last = float(getattr(clock, "last_public_activity_at", 0.0) or 0.0)
                if last > 0.0:
                    return max(0.0, now - last)
            last = max(
                float(getattr(self.host, "_temporal_last_user_turn_at", 0.0) or 0.0),
                float(getattr(self.host, "_temporal_last_assistant_turn_at", 0.0) or 0.0),
            )
            return max(0.0, now - last) if last > 0.0 else 0.0
        except Exception:
            return 0.0

    def _speech_floor_busy(self) -> bool:
        try:
            clock = getattr(self.host, "interaction_clock", None)
            return bool(getattr(clock, "busy", False)) if clock is not None else False
        except Exception:
            return False


class ContinuousDynamicsRuntime:
    """Integrate shared activation ở 50-100 ms/tick, local và deterministic."""

    def __init__(
        self,
        bus: SharedActivationBus,
        *,
        motor: ExpressionMotorV98 | None = None,
        source_provider: Callable[..., Iterable[ModulationEvent]] | None = None,
        tick_interval_sec: float | None = None,
        source_sample_interval_sec: float = 0.50,
        clock: Callable[[], float] = time.time,
        logger: Any | None = None,
        health_observer: Any | None = None,
    ) -> None:
        self.bus = bus
        self.motor = motor
        self.source_provider = source_provider
        self.clock = clock
        self.logger = logger
        # V103 FINAL: health observer chỉ đo tick latency + histogram field.
        # Nó không được tác động dynamics hoặc quyết định motor. Gắn sau bootstrap
        # cũng được để tránh vòng phụ thuộc V98 -> V103.
        self.health_observer = health_observer
        # Theo design V98: 50-100ms. Env chỉ chỉnh trong chính biên này.
        configured = tick_interval_sec if tick_interval_sec is not None else _env_float(
            "ERIS_V98_ACTIVATION_TICK_SEC", 0.075, minimum=0.05, maximum=0.10
        )
        self.tick_interval_sec = max(0.05, min(0.10, float(configured)))
        self.source_sample_interval_sec = max(0.10, min(5.0, float(source_sample_interval_sec)))
        self._active: dict[str, ModulationEvent] = {}
        self._last_tick_at: float | None = None
        self._last_source_sample_at = -1e12
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.RLock()

    @property
    def running(self) -> bool:
        thread = self._thread
        return bool(thread is not None and thread.is_alive() and not self._stop_event.is_set())

    def start(self) -> None:
        with self._lock:
            if self.running:
                return
            self._stop_event.clear()
            self._last_tick_at = None
            self._thread = threading.Thread(
                target=self._run,
                name="eris-v98-activation",
                daemon=True,
            )
            self._thread.start()
        self._log("info", "[v98 activation] started tick=%.0fms shadow_motor=%s", self.tick_interval_sec * 1000.0, int(bool(getattr(self.motor, "shadow", True))))

    def stop(self) -> None:
        self._stop_event.set()
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=1.0)
        self._thread = None

    def submit(self, event: ModulationEvent) -> bool:
        return self.bus.submit(event)

    def tick_once(self, *, now: float | None = None, dt: float | None = None) -> ActivationSnapshot:
        _tick_started = time.perf_counter()
        current = float(now if now is not None else self.clock())
        previous = self._last_tick_at
        step = float(dt if dt is not None else (self.tick_interval_sec if previous is None else current - previous))
        step = max(0.001, min(0.25, step))
        self._last_tick_at = current

        self._sample_source(current)
        for event in self.bus._drain_pending():
            # Tonic source thay thế chính nó; không cộng dồn host projection 2 Hz.
            self._active[event.source] = event
        self._prune_active(current)

        before = self.bus.snapshot()
        values = {key: before.value(key) for key in ACTIVATION_FIELDS}
        rates = self._homeostatic_rates(values)
        self._add_recurrent_rates(rates, values)
        self._add_modulation_rates(rates, values, current)

        # Chặn một event lỗi đẩy state từ 0->1 trong một tick. Activation phải có
        # quán tính; motor sẽ nhìn lịch sử state chứ không nhìn spike thô.
        updated = {
            key: _clamp01(values[key] + max(-0.45, min(0.45, rates[key])) * step)
            for key in ACTIVATION_FIELDS
        }
        snapshot = self.bus._commit_from_dynamics(
            updated,
            now=current,
            active_modulators=len(self._active),
        )
        if self.motor is not None:
            self.motor.evaluate(snapshot, now=current)
        # V103 FINAL observability: đo sau khi dynamics+shadow motor hoàn tất để
        # p50/p95/p99 phản ánh đúng chi phí tick production. Observer fail-open.
        try:
            _observe_tick = getattr(self.health_observer, "observe_tick", None)
            if callable(_observe_tick):
                _observe_tick(snapshot, (time.perf_counter() - _tick_started) * 1000.0)
        except Exception:
            pass
        return snapshot

    def snapshot(self) -> dict[str, Any]:
        state = self.bus.snapshot().to_dict()
        state["runtime"] = {
            "running": self.running,
            "tick_interval_sec": self.tick_interval_sec,
            "source_sample_interval_sec": self.source_sample_interval_sec,
            "shadow_motor": bool(getattr(self.motor, "shadow", True)),
            "active_sources": sorted(self._active.keys()),
        }
        if self.motor is not None:
            state["motor_intents"] = [intent.to_dict() for intent in self.motor.recent_intents(limit=12)]
        return state

    def _sample_source(self, now: float) -> None:
        if not callable(self.source_provider):
            return
        if now - self._last_source_sample_at < self.source_sample_interval_sec:
            return
        self._last_source_sample_at = now
        try:
            items = self.source_provider(now=now)
        except TypeError:
            items = self.source_provider()
        except Exception as exc:
            self._log("debug", "[v98 activation] source bridge error: %s", exc)
            return
        for event in tuple(items or ()):
            if isinstance(event, ModulationEvent):
                self.bus.submit(event)

    def _prune_active(self, now: float) -> None:
        expired = [
            source
            for source, event in self._active.items()
            if now - float(event.created_at) > float(event.ttl_seconds)
        ]
        for source in expired:
            self._active.pop(source, None)

    def _homeostatic_rates(self, values: Mapping[str, float]) -> dict[str, float]:
        baseline = self.bus.baselines
        return {
            key: (baseline[key] - values[key]) / _HOMEOSTATIC_TAU_SEC[key]
            for key in ACTIVATION_FIELDS
        }

    @staticmethod
    def _add_recurrent_rates(rates: dict[str, float], v: Mapping[str, float]) -> None:
        """Coupling nhỏ nhưng liên tục: field ảnh hưởng field, không chờ vote cycle."""
        rates["curiosity"] += 0.060 * (v["uncertainty"] - 0.18) + 0.024 * (v["boredom"] - 0.14) - 0.030 * (v["fatigue"] - 0.12)
        rates["boredom"] += 0.030 * (0.42 - v["task_focus"]) - 0.018 * (v["curiosity"] - 0.34)
        rates["social_need"] += 0.018 * (v["boredom"] - 0.15) - 0.015 * (v["task_focus"] - 0.35)
        rates["task_focus"] += 0.020 * (v["concern_salience"] - 0.20) - 0.026 * (v["fatigue"] - 0.14)
        rates["concern_salience"] += 0.024 * (v["uncertainty"] - 0.20) - 0.018 * (v["fatigue"] - 0.16)
        rates["uncertainty"] += 0.016 * (v["concern_salience"] - 0.25) - 0.020 * (v["task_focus"] - 0.45)

        # Expressive pressure là kết quả nổi lên từ nhiều field. Đây là "nguồn
        # động lực muốn phát tín hiệu" mà seed cũ không làm được: không cần random
        # chọn topic để state bắt đầu muốn biểu đạt.
        rates["expressive_pressure"] += (
            0.085 * (v["curiosity"] - 0.34)
            + 0.105 * (v["social_need"] - 0.24)
            + 0.080 * (v["concern_salience"] - 0.14)
            + 0.045 * (v["boredom"] - 0.12)
            + 0.045 * (v["uncertainty"] - 0.16)
            - 0.125 * (v["inhibition"] - 0.30)
            - 0.085 * (v["fatigue"] - 0.10)
            - 0.060 * (v["task_focus"] - 0.30)
        )
        rates["inhibition"] += 0.048 * (v["task_focus"] - 0.35) + 0.055 * (v["fatigue"] - 0.12) - 0.032 * (v["social_need"] - 0.24)
        rates["fatigue"] += 0.012 * (v["task_focus"] - 0.55) + 0.010 * (v["concern_salience"] - 0.50)

    def _add_modulation_rates(self, rates: dict[str, float], values: Mapping[str, float], now: float) -> None:
        for event in self._active.values():
            age = max(0.0, now - float(event.created_at))
            decay = 0.5 ** (age / max(0.15, float(event.half_life_seconds)))
            gain = _clamp01(event.strength) * decay
            if gain <= 0.0005:
                continue
            response_tau = max(0.35, min(3.0, float(event.half_life_seconds)))
            for key, target in dict(event.targets).items():
                if key in rates:
                    rates[key] += gain * (_clamp01(target) - values[key]) / response_tau
            for key, impulse in dict(event.impulses).items():
                if key in rates:
                    rates[key] += gain * _clamp_signed(impulse) / response_tau

    def _run(self) -> None:
        next_at = float(self.clock())
        while not self._stop_event.is_set():
            now = float(self.clock())
            if now >= next_at:
                try:
                    self.tick_once(now=now)
                except Exception as exc:
                    self._log("warning", "[v98 activation] tick error: %s", exc)
                next_at = now + self.tick_interval_sec
            wait_for = max(0.001, min(self.tick_interval_sec, next_at - float(self.clock())))
            self._stop_event.wait(wait_for)

    def _log(self, level: str, message: str, *args: Any) -> None:
        if self.logger is None:
            return
        try:
            getattr(self.logger, level)(message, *args)
        except Exception:
            pass


__all__ = [
    "ACTIVATION_FIELDS",
    "ACTIVATION_SCHEMA",
    "MOTOR_SCHEMA",
    "MODULATION_SCHEMA",
    "ActivationSnapshot",
    "ContinuousDynamicsRuntime",
    "ExpressionMotorV98",
    "HostActivationBridgeV98",
    "ModulationEvent",
    "MotorIntent",
    "SharedActivationBus",
]
