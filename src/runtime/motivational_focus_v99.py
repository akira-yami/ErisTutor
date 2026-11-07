# -*- coding: utf-8 -*-
"""ERIS V99 - Object-Bound Motivational Focus Field (shadow mode).

V98 đã tạo một ``SharedActivationBus`` liên tục, nhưng activation thuần scalar
chỉ trả lời được câu hỏi "đang có bao nhiêu tò mò / social need / concern". Nó
KHÔNG trả lời "tò mò VỀ CÁI GÌ". Nếu nối thẳng scalar -> speech candidate, ta chỉ
đổi roulette seed cũ thành roulette score mới.

V99 thêm đúng lớp còn thiếu giữa năng lượng và semantic action:

    SharedActivationBus V98              evidence/provenance thật
             |                                   |
             v                                   v
       activation state                    FocusObject(s)
             |                                   |
             +--------------- bind ---------------+
               +---- MotivationalFocusRuntime -+
                              |
                              v
                   BoundFocus / focus field

Nguyên tắc:
- Một activation có thể đồng thời bám nhiều object; KHÔNG chọn một "winner".
- Object bắt buộc có provenance. Runtime không tự bịa weather/event/user fact.
- ``private_activation`` và ``surface_readiness`` tách nhau: Eris có thể quan tâm
  mạnh nhưng vẫn không nên nói lúc user đang bận hoặc inhibition cao.
- V99 vẫn SHADOW. Nó không gọi LLM, không tạo câu, không gọi TTS, không cấp quyền
  SPEAK và cũng không sửa SharedActivationBus ngược lại.
- Randomness không nằm ở đây. Dynamics phải replay deterministic để debug được.

Migration path:
- Bridge legacy chỉ biến concern / active goal / user-model gap / observer frame
  đã tồn tại thành ``FocusObject``.
- Subsystem mới có thể submit FocusObject trực tiếp (ví dụ calendar/weather đã có
  evidence -> motive ``protect_user_future``). Khi migration xong bridge có thể
  mỏng dần mà không đổi API của focus field.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
import hashlib
import math
import os
import threading
import time
from typing import Any, Callable, Iterable, Mapping, Sequence

from runtime.activation_nervous_system_v98 import (
    ACTIVATION_FIELDS,
    ActivationSnapshot,
    SharedActivationBus,
)


FOCUS_OBJECT_SCHEMA = "eris.focus_object.v99"
FOCUS_FIELD_SCHEMA = "eris.motivational_focus.v99"
FOCUS_TRACE_SCHEMA = "eris.bound_focus.v99"


def _clamp01(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = default
    if not math.isfinite(number):
        number = default
    return max(0.0, min(1.0, number))


def _compact(value: Any, limit: int = 320) -> str:
    text = " ".join(str(value or "").split()).strip()
    return text if len(text) <= limit else text[: max(0, limit - 1)].rstrip() + "…"


def _refs(values: Sequence[Any], limit: int = 12) -> tuple[str, ...]:
    output: list[str] = []
    seen: set[str] = set()
    for raw in values or ():
        value = _compact(raw, 180)
        if not value or value in seen:
            continue
        seen.add(value)
        output.append(value)
        if len(output) >= limit:
            break
    return tuple(output)


def _stable_id(kind: str, *parts: Any) -> str:
    raw = "|".join([kind, *(str(x or "") for x in parts)])
    digest = hashlib.sha1(raw.encode("utf-8", "ignore")).hexdigest()[:14]
    return f"{kind}:{digest}"


def _env_float(name: str, default: float, *, minimum: float, maximum: float) -> float:
    try:
        value = float(os.getenv(name, str(default)) or default)
    except (TypeError, ValueError):
        value = default
    return max(minimum, min(maximum, value))


# Đây KHÔNG phải bảng chọn topic. Nó chỉ định activation nào cộng hưởng mạnh với
# một loại motive sau khi object có provenance đã tồn tại.
_DEFAULT_RESONANCE: dict[str, dict[str, float]] = {
    "understand_user": {
        "curiosity": 0.36,
        "social_need": 0.30,
        "uncertainty": 0.24,
        "concern_salience": 0.10,
    },
    "support_active_work": {
        "task_focus": 0.46,
        "concern_salience": 0.22,
        "uncertainty": 0.14,
        "curiosity": 0.12,
        "social_need": 0.06,
    },
    # V100: quỹ đạo hội thoại cũng phải trở thành object trong cùng focus field;
    # nếu không material authority mới sẽ phải quay lại quét context cũ.
    "continue_trajectory": {
        "curiosity": 0.27,
        "task_focus": 0.24,
        "social_need": 0.18,
        "concern_salience": 0.17,
        "uncertainty": 0.14,
    },
    "develop_concern": {
        "concern_salience": 0.38,
        "curiosity": 0.23,
        "uncertainty": 0.18,
        "task_focus": 0.13,
        "expressive_pressure": 0.08,
    },
    "inspect_environment": {
        "curiosity": 0.40,
        "uncertainty": 0.24,
        "task_focus": 0.20,
        "boredom": 0.10,
        "concern_salience": 0.06,
    },
    "protect_user_future": {
        "concern_salience": 0.30,
        "task_focus": 0.22,
        "social_need": 0.20,
        "uncertainty": 0.18,
        "curiosity": 0.10,
    },
    "maintain_connection": {
        "social_need": 0.50,
        "boredom": 0.18,
        "curiosity": 0.14,
        "concern_salience": 0.12,
        "expressive_pressure": 0.06,
    },
    "generic_interest": {
        "curiosity": 0.28,
        "concern_salience": 0.24,
        "task_focus": 0.20,
        "social_need": 0.16,
        "uncertainty": 0.12,
    },
}


@dataclass(frozen=True, slots=True)
class FocusObject:
    """Một object semantic tối thiểu mà activation có thể bám vào.

    ``label`` là mô tả bounded từ evidence hiện có, KHÔNG phải câu sẽ nói ra.
    ``resonance`` chỉ override profile mặc định khi subsystem thật sự biết mình
    muốn field activation nào tác động mạnh hơn.
    """

    object_id: str
    kind: str
    label: str
    motive_kind: str = "generic_interest"
    source: str = "unknown"
    salience: float = 0.5
    novelty: float = 0.3
    uncertainty: float = 0.3
    actionability: float = 0.4
    user_relevance: float = 0.5
    resonance: Mapping[str, float] = field(default_factory=dict)
    evidence_refs: tuple[str, ...] = ()
    ttl_seconds: float = 12.0
    created_at: float = 0.0
    tags: tuple[str, ...] = ()

    def normalized(self, *, now: float) -> "FocusObject":
        object_id = _compact(self.object_id, 160)
        label = _compact(self.label, 520)
        kind = _compact(self.kind, 80) or "unknown"
        motive = _compact(self.motive_kind, 80) or "generic_interest"
        source = _compact(self.source, 96) or "unknown"
        if not object_id:
            object_id = _stable_id("focus", source, kind, label)
        clean_resonance = {
            key: _clamp01(value)
            for key, value in dict(self.resonance or {}).items()
            if key in ACTIVATION_FIELDS and _clamp01(value) > 0.0
        }
        return FocusObject(
            object_id=object_id,
            kind=kind,
            label=label,
            motive_kind=motive,
            source=source,
            salience=_clamp01(self.salience, 0.5),
            novelty=_clamp01(self.novelty, 0.3),
            uncertainty=_clamp01(self.uncertainty, 0.3),
            actionability=_clamp01(self.actionability, 0.4),
            user_relevance=_clamp01(self.user_relevance, 0.5),
            resonance=clean_resonance,
            evidence_refs=_refs(self.evidence_refs),
            ttl_seconds=max(0.5, min(600.0, float(self.ttl_seconds or 12.0))),
            created_at=float(self.created_at or now),
            tags=_refs(self.tags, limit=10),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": FOCUS_OBJECT_SCHEMA,
            "object_id": self.object_id,
            "kind": self.kind,
            "label": self.label,
            "motive_kind": self.motive_kind,
            "source": self.source,
            "salience": round(_clamp01(self.salience), 4),
            "novelty": round(_clamp01(self.novelty), 4),
            "uncertainty": round(_clamp01(self.uncertainty), 4),
            "actionability": round(_clamp01(self.actionability), 4),
            "user_relevance": round(_clamp01(self.user_relevance), 4),
            "resonance": {key: round(_clamp01(value), 4) for key, value in dict(self.resonance).items()},
            "evidence_refs": list(self.evidence_refs),
            "ttl_seconds": round(float(self.ttl_seconds), 3),
            "created_at": float(self.created_at),
            "tags": list(self.tags),
        }


@dataclass(frozen=True, slots=True)
class BoundFocus:
    """Projection public-safe của một focus trace đang sống.

    ``private_activation`` = mức object đang chiếm động lực/chú ý nội bộ.
    ``surface_readiness`` = object *có thể* đáng mang lên public đến đâu sau các
    lực inhibition/expressive pressure. Nó vẫn KHÔNG phải quyền SPEAK.
    """

    object_id: str
    kind: str
    label: str
    motive_kind: str
    source: str
    private_activation: float
    surface_readiness: float
    persistence: float
    first_seen_at: float
    updated_at: float
    last_evidence_at: float
    evidence_refs: tuple[str, ...] = ()
    tags: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": FOCUS_TRACE_SCHEMA,
            "object_id": self.object_id,
            "kind": self.kind,
            "label": self.label,
            "motive_kind": self.motive_kind,
            "source": self.source,
            "private_activation": round(_clamp01(self.private_activation), 4),
            "surface_readiness": round(_clamp01(self.surface_readiness), 4),
            "persistence": round(_clamp01(self.persistence), 4),
            "first_seen_at": float(self.first_seen_at),
            "updated_at": float(self.updated_at),
            "last_evidence_at": float(self.last_evidence_at),
            "evidence_refs": list(self.evidence_refs),
            "tags": list(self.tags),
        }


@dataclass(slots=True)
class _FocusTrace:
    object: FocusObject
    private_activation: float
    surface_readiness: float
    persistence: float
    first_seen_at: float
    updated_at: float
    last_evidence_at: float
    expires_at: float

    def projection(self) -> BoundFocus:
        obj = self.object
        return BoundFocus(
            object_id=obj.object_id,
            kind=obj.kind,
            label=obj.label,
            motive_kind=obj.motive_kind,
            source=obj.source,
            private_activation=self.private_activation,
            surface_readiness=self.surface_readiness,
            persistence=self.persistence,
            first_seen_at=self.first_seen_at,
            updated_at=self.updated_at,
            last_evidence_at=self.last_evidence_at,
            evidence_refs=obj.evidence_refs,
            tags=obj.tags,
        )


class HostFocusObjectBridgeV99:
    """Migration bridge: state legacy có provenance -> FocusObject.

    Bridge này tuyệt đối không gọi LLM/network và không phát sinh "tin hữu ích"
    từ khoảng trống. Ví dụ weather/calendar chỉ có thể vào V99 khi subsystem có
    evidence thật submit FocusObject riêng; bridge không tự tưởng tượng ngày mai
    có mưa để thỏa ``protect_user_future``.
    """

    def __init__(self, host: Any, *, clock: Callable[[], float] = time.time) -> None:
        self.host = host
        self.clock = clock

    def collect(self, *, now: float | None = None) -> tuple[FocusObject, ...]:
        current = float(now if now is not None else self.clock())
        output: list[FocusObject] = []
        output.extend(self._concern_objects(current))
        # V100 migration: semantic spine đang sống cũng phải đi qua FocusObject.
        # Như vậy associative material không cần quét conversation context riêng.
        trajectory = self._conversation_trajectory_object(current)
        if trajectory is not None:
            output.append(trajectory)
        goal = self._active_goal_object(current)
        if goal is not None:
            output.append(goal)
        user_gap = self._user_model_gap_object(current)
        if user_gap is not None:
            output.append(user_gap)
        observer = self._observer_object(current)
        if observer is not None:
            output.append(observer)
        return self._dedupe(output)

    def _concern_objects(self, now: float) -> tuple[FocusObject, ...]:
        runtime = getattr(self.host, "active_concern_runtime", None)
        snapshot = getattr(runtime, "snapshot", None)
        if not callable(snapshot):
            return ()
        try:
            rows = tuple(snapshot(now=now, limit=6) or ())
        except Exception:
            return ()
        output: list[FocusObject] = []
        for row in rows:
            if not isinstance(row, Mapping):
                continue
            concern_id = _compact(row.get("concern_id", ""), 140)
            label = _compact(row.get("subject", ""), 420)
            state = str(row.get("state", "") or "").strip().lower()
            if not concern_id or not label or state == "blocked":
                continue
            activation = _clamp01(row.get("activation", 0.0))
            attractor = _clamp01(row.get("attractor_strength", 0.0))
            novelty = _clamp01(row.get("novelty", 0.25), 0.25)
            tension = _clamp01(row.get("tension", 0.25), 0.25)
            persistence = _clamp01(row.get("persistence", 0.25), 0.25)
            salience = max(activation, 0.90 * attractor, 0.60 * persistence)
            if salience < 0.18:
                continue
            refs = _refs(tuple(row.get("evidence_refs", ()) or ()) + (f"concern:{concern_id}",))
            output.append(FocusObject(
                object_id=f"concern:{concern_id}",
                kind="active_concern",
                label=label,
                motive_kind="develop_concern",
                source="active_concern_v99",
                salience=salience,
                novelty=novelty,
                uncertainty=max(0.16, tension),
                actionability=max(0.30, min(0.82, activation + 0.15 * novelty)),
                user_relevance=0.58,
                evidence_refs=refs,
                ttl_seconds=24.0,
                created_at=now,
                tags=(f"concern_state:{state or 'unknown'}",),
            ))
        return tuple(output)

    def _conversation_trajectory_object(self, now: float) -> FocusObject | None:
        """Project topic trajectory thành focus có provenance cho V100.

        Chỉ external/user input mới recharge trajectory ở runtime V76. Ở đây ta
        giữ cùng rule đó bằng cách gắn ``last_external_input_at`` vào object id,
        nên lời ERIS tự nói không tạo một cause generation mới vô hạn.
        """
        runtime = getattr(self.host, "conversation_trajectory_runtime", None)
        snapshot = getattr(runtime, "snapshot", None)
        if not callable(snapshot):
            return None
        try:
            state = snapshot(now=now)
            row = state.to_dict() if callable(getattr(state, "to_dict", None)) else {}
        except Exception:
            return None
        if not isinstance(row, Mapping):
            return None
        topic_id = _compact(row.get("topic_id", ""), 120)
        focus = _compact(row.get("current_focus", ""), 620)
        if (
            not topic_id or not focus
            or not bool(row.get("active", False))
            or bool(row.get("closed", False))
            or str(row.get("lifecycle_state", "")).lower() in {"satiated", "dormant"}
        ):
            return None
        momentum = _clamp01(row.get("momentum", 0.0))
        energy = _clamp01(min(1.0, float(row.get("energy", 0.0) or 0.0)))
        private_budget = max(0, int(row.get("private_budget", 0) or 0))
        if momentum < 0.20 or energy <= 0.04 or private_budget <= 0:
            return None
        engagement = _clamp01(row.get("user_engagement", 0.0))
        depth = max(0, int(row.get("depth", 0) or 0))
        mode = _compact(row.get("development_mode", "deepen"), 24).lower() or "deepen"
        external_epoch = int(float(row.get("last_external_input_at", 0.0) or 0.0))
        revision = max(0, int(row.get("revision", 0) or 0))
        root = _compact(row.get("root_focus", ""), 420)
        refs = _refs(tuple(row.get("source_refs", ()) or ()) + (f"trajectory:{topic_id}",))
        label = _compact(
            f"topic_id={topic_id}; current_focus={focus}; root_focus={root or focus}; "
            f"trajectory_depth={depth}",
            900,
        )
        return FocusObject(
            object_id=f"trajectory:{topic_id}:e{external_epoch or revision}",
            kind="conversation_trajectory",
            label=label,
            motive_kind="continue_trajectory",
            source="conversation_trajectory_v100",
            salience=_clamp01(0.46 + 0.30 * momentum + 0.12 * engagement + 0.12 * energy),
            novelty=_clamp01(0.22 + 0.18 * min(1.0, depth / 5.0)),
            uncertainty=_clamp01(0.18 + 0.18 * (1.0 - momentum)),
            actionability=_clamp01(0.42 + 0.22 * min(1.0, private_budget / 2.0) + 0.18 * energy),
            user_relevance=_clamp01(0.66 + 0.28 * engagement),
            evidence_refs=refs,
            ttl_seconds=28.0,
            created_at=now,
            tags=(
                f"private_budget:{private_budget}",
                f"development_mode:{mode}",
                f"lifecycle:{str(row.get('lifecycle_state', 'developing') or 'developing')}",
            ),
        )

    def _active_goal_object(self, now: float) -> FocusObject | None:
        reducer = getattr(self.host, "ai_state_reducer", None)
        snapshot = getattr(reducer, "snapshot", None)
        if not callable(snapshot):
            return None
        try:
            state = snapshot()
            records = tuple(getattr(state, "goal_records", ()) or ())
        except Exception:
            return None
        active = next((
            item for item in records
            if str(getattr(item, "state", "") or "") == "active"
            and str(getattr(item, "owner", "") or "") in {"user", "shared"}
            and _compact(getattr(item, "text", ""), 420)
        ), None)
        if active is None:
            return None
        goal_id = _compact(getattr(active, "goal_id", ""), 120) or _stable_id("goal", getattr(active, "text", ""))
        label = _compact(getattr(active, "text", ""), 420)
        refs = _refs(tuple(getattr(active, "evidence_refs", ()) or ()) + (f"goal-lifecycle:{goal_id}",))
        return FocusObject(
            object_id=f"goal:{goal_id}",
            kind="active_goal",
            label=label,
            motive_kind="support_active_work",
            source="goal_lifecycle_v99",
            salience=0.78,
            novelty=0.28,
            uncertainty=0.22,
            actionability=0.90,
            user_relevance=0.96,
            evidence_refs=refs,
            ttl_seconds=18.0,
            created_at=now,
            tags=(f"goal_owner:{str(getattr(active, 'owner', 'shared') or 'shared')}",),
        )

    def _user_model_gap_object(self, now: float) -> FocusObject | None:
        snapshot = self._user_model_snapshot()
        if not snapshot:
            uncertainty = 0.72
            thickness = 0.0
            belief_count = 0
            refs = ("user-model:uninitialized",)
        else:
            thickness = _clamp01(snapshot.get("model_thickness", 0.0))
            belief_count = max(0, int(snapshot.get("belief_count", 0) or 0))
            coverage = min(1.0, belief_count / 18.0)
            uncertainty = _clamp01(1.0 - (0.72 * thickness + 0.28 * coverage))
            refs = (
                f"user-model:beliefs:{belief_count}",
                f"user-model:thickness:{thickness:.3f}",
            )
        # Không giữ object này khi user-model đã đủ dày. Đây là epistemic gap,
        # không phải giấy phép hỏi đời tư cho đủ database.
        if uncertainty < 0.40:
            return None
        return FocusObject(
            object_id="user-model:coverage-gap",
            kind="epistemic_gap",
            label=(
                "Khoảng trống trong mô hình người dùng; ưu tiên lấp bằng quan sát tự nhiên, "
                "ngữ cảnh đang có hoặc câu hỏi thật sự hữu ích thay vì hỏi cho đủ hồ sơ."
            ),
            motive_kind="understand_user",
            source="user_model_gap_v99",
            salience=_clamp01(0.38 + 0.48 * uncertainty),
            novelty=_clamp01(0.20 + 0.30 * uncertainty),
            uncertainty=uncertainty,
            actionability=0.32,
            user_relevance=0.92,
            evidence_refs=refs,
            ttl_seconds=30.0,
            created_at=now,
            tags=("no_private_fact_inference", "observe_before_ask"),
        )

    def _observer_object(self, now: float) -> FocusObject | None:
        frame = getattr(self.host, "last_observer_frame", None)
        if frame is None:
            return None
        focus = str(getattr(frame, "focus_kind", "unknown") or "unknown").strip().lower()
        activity = str(getattr(frame, "activity_intent", "unknown") or "unknown").strip().lower()
        salience = _clamp01(getattr(frame, "salience", 0.0))
        novelty = _clamp01(getattr(frame, "novelty", 0.0))
        uncertainty = max(
            _clamp01(getattr(frame, "ambiguity", 0.0)),
            _clamp01(getattr(frame, "possible_error", 0.0)),
        )
        if focus in {"", "unknown", "idle", "ambient"} or salience < 0.48:
            return None
        evidence = tuple(str(x) for x in (getattr(frame, "evidence", ()) or ()) if str(x or "").strip())
        refs = _refs((*evidence, f"observer-focus:{focus}", f"observer-intent:{activity}"))
        label = f"Môi trường đang có focus={focus}, activity={activity}; chỉ là observer hint, chưa phải fact tuyệt đối."
        return FocusObject(
            object_id=f"observer:{focus}:{activity}",
            kind="environment_focus",
            label=label,
            motive_kind="inspect_environment",
            source="observer_frame_v99",
            salience=salience,
            novelty=novelty,
            uncertainty=uncertainty,
            actionability=_clamp01(0.30 + 0.50 * salience),
            user_relevance=_clamp01(getattr(frame, "user_presence", 0.5), 0.5),
            evidence_refs=refs,
            ttl_seconds=6.0,
            created_at=now,
            tags=("observer_hint_only",),
        )

    def _user_model_snapshot(self) -> Mapping[str, Any]:
        daemon = getattr(self.host, "private_mind_daemon", None)
        system = getattr(daemon, "system", None) or getattr(self.host, "private_mind_system", None)
        store = getattr(system, "user_model_store", None)
        if store is None:
            loop = getattr(self.host, "private_mind_loop", None)
            store = getattr(loop, "user_model_store", None)
        snapshot = getattr(store, "snapshot_for_dossier", None)
        if callable(snapshot):
            try:
                value = snapshot(limit=24)
                if isinstance(value, Mapping):
                    return value
            except Exception:
                pass
        return {}

    @staticmethod
    def _dedupe(items: Iterable[FocusObject]) -> tuple[FocusObject, ...]:
        # Cùng object identity chỉ giữ projection salience cao hơn. Không merge hai
        # object khác id chỉ vì text giống nhau; provenance quan trọng hơn similarity.
        selected: dict[str, FocusObject] = {}
        order: list[str] = []
        for item in items:
            if not isinstance(item, FocusObject):
                continue
            current = selected.get(item.object_id)
            if current is None:
                selected[item.object_id] = item
                order.append(item.object_id)
            elif item.salience > current.salience:
                selected[item.object_id] = item
        return tuple(selected[key] for key in order)


class MotivationalFocusRuntimeV99:
    """Bind activation V98 vào object semantic có provenance.

    Runtime chạy chậm hơn dây thần kinh V98 vì object binding không cần 75 ms.
    ``tick_once`` deterministic và có thể test replay; thread chỉ là wrapper.
    """

    def __init__(
        self,
        activation_bus: SharedActivationBus,
        *,
        source_provider: Callable[..., Iterable[FocusObject]] | None = None,
        tick_interval_sec: float | None = None,
        clock: Callable[[], float] = time.time,
        logger: Any | None = None,
        max_objects: int = 24,
    ) -> None:
        self.activation_bus = activation_bus
        self.source_provider = source_provider
        self.clock = clock
        self.logger = logger
        raw_interval = tick_interval_sec if tick_interval_sec is not None else _env_float(
            "ERIS_V99_FOCUS_TICK_SEC", 0.40, minimum=0.20, maximum=1.00
        )
        self.tick_interval_sec = max(0.20, min(1.00, float(raw_interval)))
        self.max_objects = max(4, min(64, int(max_objects)))
        self._lock = threading.RLock()
        self._pending: deque[FocusObject] = deque(maxlen=96)
        self._traces: dict[str, _FocusTrace] = {}
        self._history: deque[dict[str, Any]] = deque(maxlen=160)
        self._tick_count = 0
        self._revision = 0
        self._last_tick_at = 0.0
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def running(self) -> bool:
        thread = self._thread
        return bool(thread is not None and thread.is_alive() and not self._stop_event.is_set())

    def start(self) -> None:
        if self.running:
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run,
            name="eris-motivational-focus-v99",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        thread = self._thread
        if thread is not None and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=max(0.3, self.tick_interval_sec * 3.0))
        self._thread = None

    def submit(self, focus_object: FocusObject) -> bool:
        """Ingress typed cho subsystem mới (calendar/weather/etc. có evidence thật)."""
        if not isinstance(focus_object, FocusObject):
            raise TypeError("MotivationalFocusRuntimeV99 chỉ nhận FocusObject")
        now = float(self.clock())
        clean = focus_object.normalized(now=now)
        if not clean.label or not clean.evidence_refs:
            # V99 cố tình nghiêm: object semantic không provenance không được làm
            # focus nội sinh. Generic user-model gap vẫn có refs synthetic typed.
            return False
        with self._lock:
            self._pending.append(clean)
            self._history.append({"event": "submit", "at": now, "object": clean.to_dict()})
        return True

    def tick_once(self, *, now: float | None = None, dt: float | None = None) -> tuple[BoundFocus, ...]:
        current = float(now if now is not None else self.clock())
        with self._lock:
            if dt is None:
                if self._last_tick_at > 0.0:
                    dt_value = current - self._last_tick_at
                else:
                    dt_value = self.tick_interval_sec
            else:
                dt_value = float(dt)
            dt_value = max(0.01, min(3.0, dt_value))
            self._last_tick_at = current

        objects: list[FocusObject] = []
        provider = self.source_provider
        if callable(provider):
            try:
                rows = provider(now=current)
            except TypeError:
                rows = provider()
            except Exception as exc:
                rows = ()
                self._log("debug", "[v99 focus] source provider degraded: %s", exc)
            for row in rows or ():
                if isinstance(row, FocusObject):
                    clean = row.normalized(now=current)
                    if clean.label and clean.evidence_refs:
                        objects.append(clean)

        with self._lock:
            while self._pending:
                objects.append(self._pending.popleft())

        # Dedupe identity trước dynamics để cùng một concern không được tính hai lần.
        latest: dict[str, FocusObject] = {}
        for obj in objects:
            previous = latest.get(obj.object_id)
            if previous is None or obj.salience >= previous.salience:
                latest[obj.object_id] = obj

        activation = self.activation_bus.snapshot()
        with self._lock:
            seen: set[str] = set()
            for object_id, obj in latest.items():
                seen.add(object_id)
                target_private, target_surface = self._targets(obj, activation)
                trace = self._traces.get(object_id)
                if trace is None:
                    # New object không teleport lên 100%; nó cần accumulate một
                    # chút để tạo continuity thay vì frame nào cũng giật focus.
                    initial = _clamp01(target_private * 0.38)
                    trace = _FocusTrace(
                        object=obj,
                        private_activation=initial,
                        surface_readiness=_clamp01(target_surface * 0.32),
                        persistence=_clamp01(0.20 + 0.45 * obj.salience),
                        first_seen_at=current,
                        updated_at=current,
                        last_evidence_at=current,
                        expires_at=current + obj.ttl_seconds,
                    )
                    self._traces[object_id] = trace
                else:
                    attack_tau = 1.0 if target_private >= trace.private_activation else 5.5
                    alpha = 1.0 - math.exp(-dt_value / attack_tau)
                    surface_tau = 0.8 if target_surface >= trace.surface_readiness else 3.8
                    surface_alpha = 1.0 - math.exp(-dt_value / surface_tau)
                    trace.private_activation = _clamp01(
                        trace.private_activation + alpha * (target_private - trace.private_activation)
                    )
                    trace.surface_readiness = _clamp01(
                        trace.surface_readiness + surface_alpha * (target_surface - trace.surface_readiness)
                    )
                    # Persistence tích luỹ từ việc evidence lặp lại nhưng có trần;
                    # đây là continuity, không phải recurrence tự cấp quyền nói.
                    persistence_target = _clamp01(0.28 + 0.52 * obj.salience + 0.20 * trace.private_activation)
                    p_alpha = 1.0 - math.exp(-dt_value / 8.0)
                    trace.persistence = _clamp01(
                        trace.persistence + p_alpha * (persistence_target - trace.persistence)
                    )
                    trace.object = obj
                    trace.updated_at = current
                    trace.last_evidence_at = current
                    trace.expires_at = current + obj.ttl_seconds

            # Object biến mất KHÔNG reset ngay. Trace được release chậm để giữ
            # continuity nhưng tuyệt đối không được tự recharge khi thiếu evidence.
            dead: list[str] = []
            for object_id, trace in self._traces.items():
                if object_id in seen:
                    continue
                release_alpha = 1.0 - math.exp(-dt_value / 7.5)
                trace.private_activation = _clamp01(trace.private_activation * (1.0 - 0.85 * release_alpha))
                trace.surface_readiness = _clamp01(trace.surface_readiness * (1.0 - 1.10 * release_alpha))
                trace.persistence = _clamp01(trace.persistence * (1.0 - 0.38 * release_alpha))
                trace.updated_at = current
                if current > trace.expires_at and trace.private_activation < 0.06 and trace.persistence < 0.12:
                    dead.append(object_id)
            for object_id in dead:
                self._traces.pop(object_id, None)

            # Memory bound: prune trace yếu/cũ nhất. Sorting ở đây chỉ để quản lý
            # bộ nhớ; KHÔNG phải semantic winner hay public choice.
            if len(self._traces) > self.max_objects:
                prune = sorted(
                    self._traces.values(),
                    key=lambda t: (t.private_activation + 0.45 * t.persistence, t.last_evidence_at),
                )[: len(self._traces) - self.max_objects]
                for trace in prune:
                    self._traces.pop(trace.object.object_id, None)

            self._tick_count += 1
            self._revision += 1
            projections = tuple(trace.projection() for trace in self._traces.values())
            self._history.append({
                "event": "tick",
                "at": current,
                "revision": self._revision,
                "activation_revision": activation.revision,
                "objects_seen": len(seen),
                "focus_count": len(projections),
            })

        return projections

    def snapshot(self, *, limit: int = 16) -> dict[str, Any]:
        with self._lock:
            rows = [trace.projection() for trace in self._traces.values()]
            # Order chỉ giúp debug nhìn trace mạnh trước; field explicitly nói
            # không có winner để downstream không hiểu lầm index 0 là decision.
            rows.sort(key=lambda x: (x.private_activation, x.persistence), reverse=True)
            return {
                "schema": FOCUS_FIELD_SCHEMA,
                "revision": self._revision,
                "tick_count": self._tick_count,
                "running": self.running,
                "focus_count": len(rows),
                "selection_mode": "parallel_no_winner",
                "selected_object_id": None,
                "focuses": [row.to_dict() for row in rows[: max(1, min(64, int(limit))) ]],
                "contract": (
                    "V99 binds activation to evidence-backed objects. private_activation is not public speech permission; "
                    "surface_readiness is diagnostic only. No LLM/TTS/public writer exists in this runtime."
                ),
            }

    def recent_history(self, limit: int = 24) -> tuple[dict[str, Any], ...]:
        with self._lock:
            return tuple(list(self._history)[-max(1, min(160, int(limit))):])

    @staticmethod
    def _resonance(obj: FocusObject) -> dict[str, float]:
        raw = dict(obj.resonance or {})
        if not raw:
            raw = dict(_DEFAULT_RESONANCE.get(obj.motive_kind, _DEFAULT_RESONANCE["generic_interest"]))
        clean = {key: _clamp01(value) for key, value in raw.items() if key in ACTIVATION_FIELDS}
        total = sum(clean.values())
        if total <= 1e-9:
            return dict(_DEFAULT_RESONANCE["generic_interest"])
        # Normalize để subsystem không tăng drive chỉ bằng cách nhồi nhiều weight.
        return {key: value / total for key, value in clean.items()}

    def _targets(self, obj: FocusObject, activation: ActivationSnapshot) -> tuple[float, float]:
        resonance = self._resonance(obj)
        drive = sum(activation.value(key) * weight for key, weight in resonance.items())

        # Object feature chỉ scale năng lượng đã có; chúng không tạo motive từ 0.
        evidence_quality = _clamp01(
            0.38 * obj.salience
            + 0.18 * obj.novelty
            + 0.15 * obj.uncertainty
            + 0.15 * obj.actionability
            + 0.14 * obj.user_relevance
        )
        fatigue = activation.value("fatigue")
        private_target = _clamp01(
            (0.16 + 0.84 * drive)
            * (0.42 + 0.58 * evidence_quality)
            * (1.0 - 0.22 * fatigue)
        )

        expressive = activation.value("expressive_pressure")
        inhibition = activation.value("inhibition")
        task_focus = activation.value("task_focus")
        # Public readiness bị inhibition mạnh; private focus thì không. Nếu object
        # không phải active work, task focus cao còn giảm willingness chen ngang.
        task_interruption = 0.0 if obj.motive_kind == "support_active_work" else 0.35 * task_focus
        surface_target = _clamp01(
            private_target
            * (0.18 + 0.82 * expressive)
            * (1.0 - 0.78 * inhibition)
            * (1.0 - task_interruption)
            * (0.55 + 0.45 * obj.actionability)
        )
        return private_target, surface_target

    def _run(self) -> None:
        next_at = float(self.clock())
        while not self._stop_event.is_set():
            now = float(self.clock())
            if now >= next_at:
                try:
                    self.tick_once(now=now)
                except Exception as exc:
                    self._log("warning", "[v99 focus] tick error: %s", exc)
                next_at = now + self.tick_interval_sec
            wait_for = max(0.01, min(self.tick_interval_sec, next_at - float(self.clock())))
            self._stop_event.wait(wait_for)

    def _log(self, level: str, message: str, *args: Any) -> None:
        if self.logger is None:
            return
        try:
            getattr(self.logger, level)(message, *args)
        except Exception:
            pass


__all__ = [
    "FOCUS_FIELD_SCHEMA",
    "FOCUS_OBJECT_SCHEMA",
    "FOCUS_TRACE_SCHEMA",
    "BoundFocus",
    "FocusObject",
    "HostFocusObjectBridgeV99",
    "MotivationalFocusRuntimeV99",
]
