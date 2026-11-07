# -*- coding: utf-8 -*-
"""Continuous Self Dynamics V77.

Module này KHÔNG cố chứng minh ERIS có ý thức. Nó giải quyết một vấn đề kỹ thuật
cụ thể hơn: state của ERIS phải có lịch sử nhân quả và tiếp tục biến đổi ngay cả
khi user không gửi message.

Khác V76, nơi Interaction Ecology / Relational Plan / self-model chủ yếu là các
state chuyên biệt, V77 thêm một *self-process* trung tâm để tích hợp chúng thành:

    observation -> self update -> motive/commitment -> action opportunity -> Neuro
                                ^                              |
                                +---------- outcome -----------+

Nguyên tắc quan trọng:
- User/evidence thật có thể làm concern/commitment mạnh lên.
- Private cognition có thể phát triển hoặc làm một thought chết đi mà không nói.
- Public speech TIÊU pressure/budget; tuyệt đối không tự recharge motive chỉ vì
  ERIS vừa nói ra một câu. Đây là chốt chống vòng autonomous tự nuôi chính nó.
- Silence là action hợp lệ. ``public_action_pressure`` thấp thì Neuro nên được
  phép chọn silence dù private cognition vẫn còn chạy.
- State này là operational self-model. Surface không được kể các số nội bộ này
  như bằng chứng cho consciousness/personhood.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
import hashlib
import math
import time
from typing import Any, Mapping, Sequence


SELF_SCHEMA = "eris.continuous_self.v77"
SELF_STATE_VERSION = 1

STATUS_ACTIVE = "active"
STATUS_SIMMERING = "simmering"
STATUS_SETTLED = "settled"
STATUS_DORMANT = "dormant"
STATUS_CANCELLED = "cancelled"


# Các mode này lặp lại ở interaction_ecology, nhưng giữ string ở đây để module
# không tạo import cycle khi PersistentIndividualRuntime import cả hai.
_MODE_ASK = "ask"
_MODE_COMMENT = "comment"
_MODE_MUTTER = "mutter"
_MODE_SCREEN = "screen_react"
_MODE_SHARE = "share"
_MODE_STORY = "story"
_MODE_CALLBACK = "callback"
_MODE_TEASE = "tease"
_MODE_SUPPORT = "support"
_MODE_SILENCE = "silence"
_MODE_TASK = "task"
_MODE_REPAIR = "repair"
_MODE_BODY = "body_only"


def _clamp01(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = default
    if not math.isfinite(number):
        number = default
    return max(0.0, min(1.0, number))


def _compact(value: Any, limit: int = 420) -> str:
    text = " ".join(str(value or "").split()).strip()
    return text if len(text) <= limit else text[: max(0, limit - 1)].rstrip() + "…"


def _stable_id(kind: str, *parts: Any) -> str:
    raw = "|".join([kind, *(str(x or "") for x in parts)])
    return f"{kind}:{hashlib.sha1(raw.encode('utf-8', 'ignore')).hexdigest()[:14]}"


def _decay(value: float, dt: float, half_life: float) -> float:
    if value <= 0.0:
        return 0.0
    if dt <= 0.0 or half_life <= 0.0:
        return _clamp01(value)
    return _clamp01(value * (0.5 ** (dt / half_life)))


@dataclass(slots=True)
class SelfConcern:
    """Một mối quan tâm operational có nguồn gốc rõ.

    Concern này chỉ là projection nhẹ để self-process có thể giữ causal continuity.
    ActiveConcern/PrivateMind vẫn là authority nội dung giàu hơn ở subsystem cũ.
    """

    concern_id: str
    label: str
    source_kind: str
    source_ref: str = ""
    intensity: float = 0.0
    confidence: float = 0.0
    created_at: float = 0.0
    updated_at: float = 0.0
    last_external_evidence_at: float = 0.0
    private_updates: int = 0
    public_attempts: int = 0
    status: str = STATUS_ACTIVE

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["intensity"] = round(_clamp01(self.intensity), 4)
        payload["confidence"] = round(_clamp01(self.confidence), 4)
        return payload

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "SelfConcern":
        return cls(
            concern_id=str(data.get("concern_id", "") or ""),
            label=_compact(data.get("label", ""), 520),
            source_kind=str(data.get("source_kind", "") or ""),
            source_ref=str(data.get("source_ref", "") or ""),
            intensity=_clamp01(data.get("intensity", 0.0)),
            confidence=_clamp01(data.get("confidence", 0.0)),
            created_at=float(data.get("created_at", 0.0) or 0.0),
            updated_at=float(data.get("updated_at", 0.0) or 0.0),
            last_external_evidence_at=float(data.get("last_external_evidence_at", 0.0) or 0.0),
            private_updates=max(0, int(data.get("private_updates", 0) or 0)),
            public_attempts=max(0, int(data.get("public_attempts", 0) or 0)),
            status=str(data.get("status", STATUS_ACTIVE) or STATUS_ACTIVE),
        )


@dataclass(slots=True)
class SelfCommitment:
    """Cam kết sống qua nhiều turn, không reset sau một câu Surface."""

    commitment_id: str
    goal_kind: str
    objective: str
    source_ref: str = ""
    strength: float = 0.0
    progress: float = 0.0
    created_at: float = 0.0
    updated_at: float = 0.0
    expires_at: float = 0.0
    status: str = STATUS_ACTIVE
    preferred_modes: tuple[str, ...] = ()
    avoid_modes: tuple[str, ...] = ()
    public_attempts: int = 0
    private_updates: int = 0
    last_public_at: float = 0.0
    last_user_support_at: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["strength"] = round(_clamp01(self.strength), 4)
        payload["progress"] = round(_clamp01(self.progress), 4)
        return payload

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "SelfCommitment":
        return cls(
            commitment_id=str(data.get("commitment_id", "") or ""),
            goal_kind=str(data.get("goal_kind", "") or ""),
            objective=_compact(data.get("objective", ""), 700),
            source_ref=str(data.get("source_ref", "") or ""),
            strength=_clamp01(data.get("strength", 0.0)),
            progress=_clamp01(data.get("progress", 0.0)),
            created_at=float(data.get("created_at", 0.0) or 0.0),
            updated_at=float(data.get("updated_at", 0.0) or 0.0),
            expires_at=float(data.get("expires_at", 0.0) or 0.0),
            status=str(data.get("status", STATUS_ACTIVE) or STATUS_ACTIVE),
            preferred_modes=tuple(str(x) for x in (data.get("preferred_modes", ()) or ()) if str(x))[:16],
            avoid_modes=tuple(str(x) for x in (data.get("avoid_modes", ()) or ()) if str(x))[:16],
            public_attempts=max(0, int(data.get("public_attempts", 0) or 0)),
            private_updates=max(0, int(data.get("private_updates", 0) or 0)),
            last_public_at=float(data.get("last_public_at", 0.0) or 0.0),
            last_user_support_at=float(data.get("last_user_support_at", 0.0) or 0.0),
        )


@dataclass(slots=True)
class UnfinishedThought:
    """Private thought có thể phát triển, nguội hoặc chết mà không surface."""

    thought_id: str
    topic_id: str
    summary: str
    source_kind: str = "private_cognition"
    pressure: float = 0.0
    semantic_gain_ema: float = 0.0
    created_at: float = 0.0
    updated_at: float = 0.0
    private_rounds: int = 0
    surfaced_count: int = 0
    status: str = STATUS_ACTIVE

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["pressure"] = round(_clamp01(self.pressure), 4)
        payload["semantic_gain_ema"] = round(_clamp01(self.semantic_gain_ema), 4)
        return payload

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "UnfinishedThought":
        return cls(
            thought_id=str(data.get("thought_id", "") or ""),
            topic_id=str(data.get("topic_id", "") or ""),
            summary=_compact(data.get("summary", ""), 700),
            source_kind=str(data.get("source_kind", "private_cognition") or "private_cognition"),
            pressure=_clamp01(data.get("pressure", 0.0)),
            semantic_gain_ema=_clamp01(data.get("semantic_gain_ema", 0.0)),
            created_at=float(data.get("created_at", 0.0) or 0.0),
            updated_at=float(data.get("updated_at", 0.0) or 0.0),
            private_rounds=max(0, int(data.get("private_rounds", 0) or 0)),
            surfaced_count=max(0, int(data.get("surfaced_count", 0) or 0)),
            status=str(data.get("status", STATUS_ACTIVE) or STATUS_ACTIVE),
        )


@dataclass(slots=True)
class SelfTension:
    """Hai motive cùng hợp lệ nhưng kéo ERIS theo hai hướng khác nhau."""

    tension_id: str
    left: str
    right: str
    intensity: float = 0.0
    created_at: float = 0.0
    updated_at: float = 0.0
    status: str = STATUS_ACTIVE

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["intensity"] = round(_clamp01(self.intensity), 4)
        return payload

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "SelfTension":
        return cls(
            tension_id=str(data.get("tension_id", "") or ""),
            left=_compact(data.get("left", ""), 240),
            right=_compact(data.get("right", ""), 240),
            intensity=_clamp01(data.get("intensity", 0.0)),
            created_at=float(data.get("created_at", 0.0) or 0.0),
            updated_at=float(data.get("updated_at", 0.0) or 0.0),
            status=str(data.get("status", STATUS_ACTIVE) or STATUS_ACTIVE),
        )


@dataclass(slots=True)
class ContinuousSelfState:
    version: int = SELF_STATE_VERSION
    revision: int = 0
    created_at: float = 0.0
    updated_at: float = 0.0
    last_user_at: float = 0.0
    last_public_at: float = 0.0
    last_private_at: float = 0.0
    tick_count: int = 0
    orientation: str = "neutral"
    dominant_motive: str = "none"
    public_action_pressure: float = 0.0
    private_cognition_pressure: float = 0.0
    continuity_strength: float = 0.0
    social_availability: float = 0.5
    concerns: list[SelfConcern] = field(default_factory=list)
    commitments: list[SelfCommitment] = field(default_factory=list)
    unfinished_thoughts: list[UnfinishedThought] = field(default_factory=list)
    tensions: list[SelfTension] = field(default_factory=list)
    causal_trace: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": SELF_SCHEMA,
            "version": self.version,
            "revision": self.revision,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "last_user_at": self.last_user_at,
            "last_public_at": self.last_public_at,
            "last_private_at": self.last_private_at,
            "tick_count": self.tick_count,
            "orientation": self.orientation,
            "dominant_motive": self.dominant_motive,
            "public_action_pressure": round(_clamp01(self.public_action_pressure), 4),
            "private_cognition_pressure": round(_clamp01(self.private_cognition_pressure), 4),
            "continuity_strength": round(_clamp01(self.continuity_strength), 4),
            "social_availability": round(_clamp01(self.social_availability, 0.5), 4),
            "concerns": [x.to_dict() for x in self.concerns[-24:]],
            "commitments": [x.to_dict() for x in self.commitments[-16:]],
            "unfinished_thoughts": [x.to_dict() for x in self.unfinished_thoughts[-24:]],
            "tensions": [x.to_dict() for x in self.tensions[-16:]],
            "causal_trace": list(self.causal_trace[-120:]),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any] | None) -> "ContinuousSelfState":
        now = time.time()
        if not isinstance(data, Mapping):
            return cls(created_at=now, updated_at=now)
        return cls(
            version=max(1, int(data.get("version", SELF_STATE_VERSION) or SELF_STATE_VERSION)),
            revision=max(0, int(data.get("revision", 0) or 0)),
            created_at=float(data.get("created_at", now) or now),
            updated_at=float(data.get("updated_at", now) or now),
            last_user_at=float(data.get("last_user_at", 0.0) or 0.0),
            last_public_at=float(data.get("last_public_at", 0.0) or 0.0),
            last_private_at=float(data.get("last_private_at", 0.0) or 0.0),
            tick_count=max(0, int(data.get("tick_count", 0) or 0)),
            orientation=str(data.get("orientation", "neutral") or "neutral"),
            dominant_motive=str(data.get("dominant_motive", "none") or "none"),
            public_action_pressure=_clamp01(data.get("public_action_pressure", 0.0)),
            private_cognition_pressure=_clamp01(data.get("private_cognition_pressure", 0.0)),
            continuity_strength=_clamp01(data.get("continuity_strength", 0.0)),
            social_availability=_clamp01(data.get("social_availability", 0.5), 0.5),
            concerns=[SelfConcern.from_dict(x) for x in (data.get("concerns", ()) or ()) if isinstance(x, Mapping)][-24:],
            commitments=[SelfCommitment.from_dict(x) for x in (data.get("commitments", ()) or ()) if isinstance(x, Mapping)][-16:],
            unfinished_thoughts=[UnfinishedThought.from_dict(x) for x in (data.get("unfinished_thoughts", ()) or ()) if isinstance(x, Mapping)][-24:],
            tensions=[SelfTension.from_dict(x) for x in (data.get("tensions", ()) or ()) if isinstance(x, Mapping)][-16:],
            causal_trace=[dict(x) for x in (data.get("causal_trace", ()) or ()) if isinstance(x, Mapping)][-120:],
        )


@dataclass(frozen=True, slots=True)
class SelfActionBias:
    multiplier: float = 1.0
    silence_boost: float = 0.0
    reason: str = ""


class ContinuousSelfDynamics:
    """Tích hợp state rời rạc thành một self-process chạy trên nhiều timescale."""

    def __init__(self, state: ContinuousSelfState | None = None, *, logger: Any = None) -> None:
        self.state = state or ContinuousSelfState(created_at=time.time(), updated_at=time.time())
        self.logger = logger

    # ------------------------------------------------------------------
    # Event ingress. Chỉ event có causal provenance mới được recharge state.
    # ------------------------------------------------------------------
    def observe_user_turn(
        self,
        text: str,
        *,
        turn_ref: str = "",
        relational_plan: Mapping[str, Any] | None = None,
        trajectory: Mapping[str, Any] | None = None,
        now: float | None = None,
    ) -> ContinuousSelfState:
        current = float(now or time.time())
        self.tick(now=current, relational_plan=relational_plan, trajectory=trajectory)
        state = self.state
        state.last_user_at = current

        # User contribution là external evidence thật, được phép làm commitment
        # hiện tại mạnh lên. Nhưng chỉ upsert theo plan_id/topic_id, không nhân bản.
        self._sync_plan(relational_plan or {}, current=current, external_support=True)
        self._sync_trajectory(trajectory or {}, current=current, external_support=True)
        self._trace("user_turn", {"turn_ref": turn_ref, "chars": len(str(text or ""))}, current)
        self._touch(current)
        return state

    def observe_public_action(
        self,
        *,
        mode: str,
        autonomous: bool,
        response_expectation: str = "none",
        now: float | None = None,
    ) -> ContinuousSelfState:
        current = float(now or time.time())
        self.tick(now=current)
        state = self.state
        state.last_public_at = current

        # Public speech là *chi phí thực thi*. Không có dòng nào cộng strength của
        # concern/commitment ở đây. ERIS không được sống mãi bằng echo của chính nó.
        cost = 0.10 if not autonomous else 0.18
        if mode in {_MODE_ASK, _MODE_STORY, _MODE_SHARE}:
            cost += 0.05
        state.public_action_pressure = _clamp01(state.public_action_pressure - cost)

        active = self._active_commitment(current)
        if active is not None:
            active.public_attempts += 1
            active.last_public_at = current
            # Nhiều attempt nhưng user chưa đóng góp sẽ chuyển plan về simmering,
            # thay vì tự tăng pressure để "cố thêm lần nữa".
            if active.public_attempts >= 3 and active.last_user_support_at < active.last_public_at:
                active.strength = _clamp01(active.strength - 0.05)
                if active.public_attempts >= 6:
                    active.status = STATUS_SIMMERING

        # Nếu một thought được share/comment đúng lúc, đánh dấu đã surface và hạ
        # pressure. Nó vẫn có thể tồn tại private nếu chưa settled.
        if mode in {_MODE_SHARE, _MODE_COMMENT, _MODE_STORY, _MODE_MUTTER}:
            thought = self._top_thought(current)
            if thought is not None and thought.pressure >= 0.38:
                thought.surfaced_count += 1
                thought.pressure = _clamp01(thought.pressure - (0.22 if autonomous else 0.12))

        self._trace(
            "public_action",
            {"mode": mode, "autonomous": bool(autonomous), "expectation": response_expectation},
            current,
        )
        self._touch(current)
        return state

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
    ) -> ContinuousSelfState:
        current = float(now or time.time())
        self.tick(now=current)
        clean = _compact(summary, 700)
        if not clean:
            return self.state
        refs = tuple(str(x) for x in source_refs if str(x))[:12]
        topic = str(topic_id or "").strip()
        if not topic:
            topic = next((x.split(":", 1)[1] for x in refs if x.startswith("trajectory:")), "")
        anchor = topic or str(cause_kind or material_kind or "private")
        thought_id = _stable_id("thought", anchor)
        thought = next((x for x in self.state.unfinished_thoughts if x.thought_id == thought_id), None)
        gain = _clamp01(semantic_gain)
        if thought is None:
            thought = UnfinishedThought(
                thought_id=thought_id,
                topic_id=topic,
                summary=clean,
                source_kind=str(cause_kind or material_kind or "private_cognition"),
                pressure=_clamp01(0.18 + 0.48 * gain),
                semantic_gain_ema=gain,
                created_at=current,
                updated_at=current,
                private_rounds=1,
            )
            self.state.unfinished_thoughts.append(thought)
        else:
            thought.private_rounds += 1
            thought.summary = clean
            thought.semantic_gain_ema = _clamp01(0.68 * thought.semantic_gain_ema + 0.32 * gain)
            # High gain có thể giữ thought sống. Low gain lặp lại làm nó tự cạn.
            if gain >= 0.42:
                thought.pressure = _clamp01(thought.pressure + 0.10 * gain)
                thought.status = STATUS_ACTIVE
            else:
                thought.pressure = _clamp01(thought.pressure - 0.08)
            thought.updated_at = current

        if thought.private_rounds >= 3 and thought.semantic_gain_ema < 0.24:
            thought.status = STATUS_SETTLED
            thought.pressure = min(thought.pressure, 0.16)
        if thought.private_rounds >= 6 and thought.semantic_gain_ema < 0.18:
            thought.status = STATUS_DORMANT
            thought.pressure = min(thought.pressure, 0.06)

        self.state.last_private_at = current
        active = self._active_commitment(current)
        if active is not None:
            active.private_updates += 1
        self._trace(
            "private_development",
            {"thought_id": thought.thought_id, "gain": round(gain, 3), "status": thought.status},
            current,
        )
        self._touch(current)
        self._prune(current)
        return self.state

    # ------------------------------------------------------------------
    # Continuous update. Projection gọi tick ngay cả khi user im.
    # ------------------------------------------------------------------
    def tick(
        self,
        *,
        now: float | None = None,
        persistent_internal: Mapping[str, Any] | None = None,
        endogenous: Mapping[str, Any] | None = None,
        interaction_ecology: Mapping[str, Any] | None = None,
        relational_plan: Mapping[str, Any] | None = None,
        trajectory: Mapping[str, Any] | None = None,
        active_concerns: Sequence[Mapping[str, Any]] = (),
    ) -> ContinuousSelfState:
        current = float(now or time.time())
        state = self.state
        if state.created_at <= 0.0:
            state.created_at = current
        previous = state.updated_at or current
        dt = max(0.0, current - previous)

        # 1) Thời gian tự nó làm state đổi. Concern / thought / public urge nguội
        # theo half-life khác nhau. Đây là phần khiến 2 giờ im không tương đương
        # "đóng băng state rồi mở lại".
        for concern in state.concerns:
            if concern.status not in {STATUS_ACTIVE, STATUS_SIMMERING}:
                continue
            concern.intensity = _decay(concern.intensity, dt, 6 * 3600.0)
            if concern.intensity < 0.16:
                concern.status = STATUS_SIMMERING
            if concern.intensity < 0.055:
                concern.status = STATUS_DORMANT

        for thought in state.unfinished_thoughts:
            if thought.status in {STATUS_DORMANT, STATUS_CANCELLED}:
                continue
            thought.pressure = _decay(thought.pressure, dt, 45 * 60.0)
            if thought.pressure < 0.08 and thought.private_rounds >= 1:
                thought.status = STATUS_DORMANT
            elif thought.pressure < 0.20 and thought.status == STATUS_ACTIVE:
                thought.status = STATUS_SIMMERING

        for tension in state.tensions:
            if tension.status not in {STATUS_ACTIVE, STATUS_SIMMERING}:
                continue
            tension.intensity = _decay(tension.intensity, dt, 90 * 60.0)
            if tension.intensity < 0.10:
                tension.status = STATUS_SETTLED

        for commitment in state.commitments:
            if commitment.status not in {STATUS_ACTIVE, STATUS_SIMMERING}:
                continue
            if commitment.expires_at > 0 and current >= commitment.expires_at:
                commitment.status = STATUS_SETTLED
                commitment.strength = min(commitment.strength, 0.10)
                continue
            # Commitment explicit giảm rất chậm; nó không reset mỗi tick, cũng
            # không được assistant speech recharge. Progress từ plan được sync dưới.
            commitment.strength = _decay(commitment.strength, dt, 4 * 3600.0)
            if commitment.strength < 0.20:
                commitment.status = STATUS_SIMMERING

        # 2) Đồng bộ authority có provenance. sync_plan external_support=False nên
        # chỉ mirror status/progress, không cộng strength từ việc "đọc lại state".
        self._sync_plan(relational_plan or {}, current=current, external_support=False)
        self._sync_trajectory(trajectory or {}, current=current, external_support=False)
        self._sync_active_concerns(active_concerns, current=current)

        internal = dict(persistent_internal or {})
        drives = dict(endogenous or {})
        ecology = dict(interaction_ecology or {})

        social_pressure = _clamp01(
            internal.get("social_pressure", drives.get("social_pressure", internal.get("social_energy", 0.0)))
        )
        curiosity_pressure = _clamp01(
            internal.get("curiosity_pressure", drives.get("curiosity_pressure", internal.get("curiosity", 0.0)))
        )
        restraint = _clamp01(internal.get("restraint_pressure", 0.0))
        fatigue = _clamp01(internal.get("fatigue", 0.0))
        boredom = _clamp01(internal.get("boredom", 0.0))
        cognitive_tension = _clamp01(internal.get("cognitive_tension", 0.0))
        qfat = _clamp01(ecology.get("question_fatigue", 0.0))
        pfat = _clamp01(ecology.get("public_fatigue", 0.0))
        unsolicited = max(0, int(ecology.get("unsolicited_streak", 0) or 0))
        ambient = _clamp01(ecology.get("ambient_appetite", 0.0))

        active_commitment = self._active_commitment(current)
        top_concern = self._top_concern(current)
        top_thought = self._top_thought(current)
        max_tension = max((x.intensity for x in state.tensions if x.status in {STATUS_ACTIVE, STATUS_SIMMERING}), default=0.0)

        commitment_pressure = active_commitment.strength * (1.0 - 0.45 * active_commitment.progress) if active_commitment else 0.0
        concern_pressure = top_concern.intensity if top_concern else 0.0
        thought_pressure = top_thought.pressure if top_thought else 0.0

        # Public và private pressure là hai đại lượng riêng. Có thể nghĩ nhiều mà
        # vẫn nên im. Đây là điểm kiến trúc quan trọng nhất của V77.
        private_target = _clamp01(
            0.34 * cognitive_tension
            + 0.28 * thought_pressure
            + 0.20 * concern_pressure
            + 0.12 * curiosity_pressure
            + 0.06 * boredom
        )
        public_target = _clamp01(
            0.30 * commitment_pressure
            + 0.17 * concern_pressure
            + 0.16 * social_pressure
            + 0.12 * ambient
            + 0.08 * thought_pressure
            - 0.22 * restraint
            - 0.24 * fatigue
            - 0.22 * pfat
            - 0.11 * min(3, unsolicited)
        )

        # Inertia để state không giật như meter âm thanh mỗi tick.
        state.private_cognition_pressure = _clamp01(0.84 * state.private_cognition_pressure + 0.16 * private_target)
        state.public_action_pressure = _clamp01(0.82 * state.public_action_pressure + 0.18 * public_target)
        state.social_availability = _clamp01(1.0 - 0.52 * fatigue - 0.36 * restraint - 0.16 * pfat)
        state.continuity_strength = _clamp01(max(commitment_pressure, concern_pressure, thought_pressure, max_tension))

        # 3) Tension có ý nghĩa: muốn connect nhưng user/public ecology đang mệt.
        if (social_pressure + commitment_pressure) >= 0.62 and (pfat + restraint + qfat) >= 0.72:
            self._upsert_tension(
                left="muốn tiếp tục kết nối / giữ cam kết",
                right="tôn trọng nhịp im và tránh làm phiền",
                intensity=_clamp01(0.5 * (social_pressure + commitment_pressure) + 0.35 * (pfat + restraint)),
                current=current,
            )

        # 4) Orientation là mô tả operational cho planner, không phải persona text.
        state.orientation, state.dominant_motive = self._derive_orientation(
            public_pressure=state.public_action_pressure,
            private_pressure=state.private_cognition_pressure,
            plan=active_commitment,
            top_concern=top_concern,
            top_thought=top_thought,
            fatigue=fatigue,
            pfat=pfat,
            boredom=boredom,
        )

        state.tick_count += 1
        if dt >= 0.5 or state.updated_at <= 0.0:
            self._touch(current)
        else:
            state.updated_at = current
        self._prune(current)
        return state

    # ------------------------------------------------------------------
    # Neuro-facing projection/bias.
    # ------------------------------------------------------------------
    def action_bias(self, *, mode: str, action_kind: str = "speak") -> SelfActionBias:
        state = self.state
        action = str(action_kind or "speak").strip().lower()
        mode = str(mode or "").strip().lower()
        active = self._active_commitment(state.updated_at or time.time())
        preferred = set(active.preferred_modes) if active else set()
        avoided = set(active.avoid_modes) if active else set()

        if action in {"silence", "body_only"} or mode in {_MODE_SILENCE, _MODE_BODY}:
            boost = _clamp01(0.10 + 0.52 * (1.0 - state.public_action_pressure))
            return SelfActionBias(multiplier=1.0, silence_boost=boost, reason=f"self_public_pressure={state.public_action_pressure:.2f}")
        if mode in {_MODE_TASK, _MODE_REPAIR}:
            return SelfActionBias(multiplier=1.0, reason="task_or_repair_not_socially_suppressed")

        multiplier = 0.50 + 0.78 * state.public_action_pressure
        if mode in preferred:
            multiplier *= 1.28
        if mode in avoided:
            multiplier *= 0.25
        if mode == _MODE_ASK and state.public_action_pressure < 0.48:
            multiplier *= 0.48
        if state.social_availability < 0.32:
            multiplier *= 0.62

        # Private thought không tự tạo quyền nói. Nó chỉ giúp SHARE/COMMENT khi
        # public pressure vốn đã đủ cao.
        top_thought = self._top_thought(state.updated_at or time.time())
        if top_thought is not None and top_thought.pressure >= 0.45 and state.public_action_pressure >= 0.46:
            if mode in {_MODE_SHARE, _MODE_COMMENT, _MODE_STORY, _MODE_CALLBACK}:
                multiplier *= 1.12

        return SelfActionBias(
            multiplier=max(0.05, min(1.60, multiplier)),
            reason=f"self:{state.orientation}:public={state.public_action_pressure:.2f}:private={state.private_cognition_pressure:.2f}",
        )

    def projection(self, *, now: float | None = None) -> dict[str, Any]:
        current = float(now or time.time())
        state = self.state
        active = self._active_commitment(current)
        concern = self._top_concern(current)
        thought = self._top_thought(current)
        tensions = [x.to_dict() for x in state.tensions if x.status in {STATUS_ACTIVE, STATUS_SIMMERING}]
        return {
            "schema": SELF_SCHEMA,
            "revision": state.revision,
            "orientation": state.orientation,
            "dominant_motive": state.dominant_motive,
            "public_action_pressure": round(_clamp01(state.public_action_pressure), 4),
            "private_cognition_pressure": round(_clamp01(state.private_cognition_pressure), 4),
            "continuity_strength": round(_clamp01(state.continuity_strength), 4),
            "social_availability": round(_clamp01(state.social_availability), 4),
            "active_commitment": active.to_dict() if active else {},
            "top_concern": concern.to_dict() if concern else {},
            "top_unfinished_thought": thought.to_dict() if thought else {},
            "tensions": tensions[:6],
            "counts": {
                "concerns": len([x for x in state.concerns if x.status in {STATUS_ACTIVE, STATUS_SIMMERING}]),
                "commitments": len([x for x in state.commitments if x.status in {STATUS_ACTIVE, STATUS_SIMMERING}]),
                "unfinished_thoughts": len([x for x in state.unfinished_thoughts if x.status in {STATUS_ACTIVE, STATUS_SIMMERING}]),
            },
            "contract": (
                "Continuous self is causal state, not a consciousness claim. Private pressure may remain high while public pressure is low. "
                "Assistant speech spends public pressure and cannot self-recharge commitments."
            ),
        }

    # ------------------------------------------------------------------
    # Internal helpers.
    # ------------------------------------------------------------------
    def _sync_plan(self, plan: Mapping[str, Any], *, current: float, external_support: bool) -> None:
        if not isinstance(plan, Mapping) or not bool(plan.get("active", False)):
            return
        plan_id = str(plan.get("plan_id", "") or "")
        goal_kind = str(plan.get("goal_kind", "") or "")
        if not plan_id and not goal_kind:
            return
        commitment_id = _stable_id("commitment", plan_id or goal_kind)
        item = next((x for x in self.state.commitments if x.commitment_id == commitment_id), None)
        if item is None:
            item = SelfCommitment(
                commitment_id=commitment_id,
                goal_kind=goal_kind or "relational_goal",
                objective=_compact(plan.get("objective", ""), 700),
                source_ref=str(plan.get("source_ref", "") or ""),
                strength=0.72 if external_support else 0.58,
                progress=_clamp01(plan.get("progress", 0.0)),
                created_at=current,
                updated_at=current,
                expires_at=float(plan.get("expires_at", 0.0) or 0.0),
                preferred_modes=tuple(str(x) for x in (plan.get("effective_preferred_modes", plan.get("preferred_modes", ())) or ()) if str(x))[:16],
                avoid_modes=tuple(str(x) for x in (plan.get("avoid_modes", ()) or ()) if str(x))[:16],
                last_user_support_at=current if external_support else 0.0,
            )
            self.state.commitments.append(item)
        else:
            item.progress = max(item.progress, _clamp01(plan.get("progress", item.progress)))
            item.status = STATUS_SIMMERING if str(plan.get("status", "")) == "simmering" else item.status
            item.expires_at = float(plan.get("expires_at", item.expires_at) or item.expires_at)
            item.preferred_modes = tuple(str(x) for x in (plan.get("effective_preferred_modes", plan.get("preferred_modes", item.preferred_modes)) or ()) if str(x))[:16]
            item.avoid_modes = tuple(str(x) for x in (plan.get("avoid_modes", item.avoid_modes) or ()) if str(x))[:16]
            if external_support:
                item.strength = _clamp01(max(item.strength, 0.62) + 0.10)
                item.last_user_support_at = current
            item.updated_at = current

    def _sync_trajectory(self, trajectory: Mapping[str, Any], *, current: float, external_support: bool) -> None:
        if not isinstance(trajectory, Mapping):
            return
        topic_id = str(trajectory.get("topic_id", "") or "")
        lifecycle = str(trajectory.get("lifecycle_state", "") or "")
        focus = _compact(trajectory.get("current_focus", ""), 700)
        momentum = _clamp01(trajectory.get("momentum", 0.0))
        if not topic_id or not focus or lifecycle in {"satiated", "dormant", "closed"}:
            return
        thought_id = _stable_id("thought", topic_id)
        item = next((x for x in self.state.unfinished_thoughts if x.thought_id == thought_id), None)
        if item is None:
            item = UnfinishedThought(
                thought_id=thought_id,
                topic_id=topic_id,
                summary=focus,
                source_kind="conversation_trajectory",
                pressure=_clamp01(0.16 + 0.38 * momentum),
                semantic_gain_ema=0.45 if external_support else 0.30,
                created_at=current,
                updated_at=current,
            )
            self.state.unfinished_thoughts.append(item)
        elif external_support:
            item.summary = focus
            item.pressure = _clamp01(max(item.pressure, 0.20) + 0.16 * momentum)
            item.status = STATUS_ACTIVE
            item.updated_at = current

    def _sync_active_concerns(self, rows: Sequence[Mapping[str, Any]], *, current: float) -> None:
        for raw in tuple(rows or ())[:8]:
            if not isinstance(raw, Mapping):
                continue
            source_id = str(raw.get("concern_id", "") or raw.get("id", "") or "")
            if not source_id:
                continue
            activation = _clamp01(raw.get("activation", raw.get("speech_pressure", 0.0)))
            if activation < 0.20:
                continue
            label = _compact(raw.get("semantic_focus", raw.get("subject", raw.get("content", source_id))), 520)
            cid = _stable_id("concern", source_id)
            item = next((x for x in self.state.concerns if x.concern_id == cid), None)
            if item is None:
                item = SelfConcern(
                    concern_id=cid,
                    label=label or source_id,
                    source_kind="active_concern_projection",
                    source_ref=source_id,
                    intensity=activation,
                    confidence=_clamp01(raw.get("confidence", 0.60), 0.60),
                    created_at=current,
                    updated_at=current,
                    # ActiveConcern itself already has provenance. Projection into
                    # self-process is trusted as internal typed evidence, not Surface.
                    last_external_evidence_at=current,
                )
                self.state.concerns.append(item)
            else:
                # Projection refresh chỉ mirror activation, không cộng dồn intensity.
                item.intensity = max(item.intensity, activation)
                item.label = label or item.label
                item.updated_at = current
                item.status = STATUS_ACTIVE

    def _upsert_tension(self, *, left: str, right: str, intensity: float, current: float) -> None:
        tid = _stable_id("tension", left, right)
        item = next((x for x in self.state.tensions if x.tension_id == tid), None)
        if item is None:
            self.state.tensions.append(SelfTension(
                tension_id=tid, left=left, right=right,
                intensity=_clamp01(intensity), created_at=current, updated_at=current,
            ))
        else:
            item.intensity = max(item.intensity, _clamp01(intensity))
            item.updated_at = current
            item.status = STATUS_ACTIVE

    def _active_commitment(self, now: float) -> SelfCommitment | None:
        rows = [x for x in self.state.commitments if x.status in {STATUS_ACTIVE, STATUS_SIMMERING} and (x.expires_at <= 0 or now < x.expires_at)]
        if not rows:
            return None
        return max(rows, key=lambda x: (x.strength * (1.0 - 0.35 * x.progress), x.updated_at))

    def _top_concern(self, now: float) -> SelfConcern | None:
        rows = [x for x in self.state.concerns if x.status in {STATUS_ACTIVE, STATUS_SIMMERING}]
        return max(rows, key=lambda x: x.intensity, default=None)

    def _top_thought(self, now: float) -> UnfinishedThought | None:
        rows = [x for x in self.state.unfinished_thoughts if x.status in {STATUS_ACTIVE, STATUS_SIMMERING}]
        return max(rows, key=lambda x: (x.pressure, x.semantic_gain_ema), default=None)

    @staticmethod
    def _derive_orientation(
        *, public_pressure: float, private_pressure: float, plan: SelfCommitment | None,
        top_concern: SelfConcern | None, top_thought: UnfinishedThought | None,
        fatigue: float, pfat: float, boredom: float,
    ) -> tuple[str, str]:
        if fatigue >= 0.72 or pfat >= 0.88:
            return "quiet_recovery", "protect_interaction_bandwidth"
        if plan is not None and plan.goal_kind == "mood_lift":
            return ("playful_company" if public_pressure >= 0.42 else "quiet_company", "carry_relational_commitment")
        if plan is not None and plan.goal_kind in {"keep_company", "reduce_questions"}:
            return ("ambient_company" if public_pressure >= 0.34 else "quiet_company", "carry_relational_commitment")
        if top_concern is not None and top_concern.intensity >= 0.58:
            return ("concerned_presence" if public_pressure >= 0.48 else "private_concern", "resolve_or_hold_concern")
        if top_thought is not None and private_pressure >= 0.45:
            return ("reflective" if public_pressure < 0.45 else "reflective_presence", "develop_unfinished_thought")
        if boredom >= 0.60 and public_pressure >= 0.40:
            return "light_exploration", "seek_novelty_without_interrogation"
        if public_pressure < 0.26:
            return "quiet_presence", "none_public"
        return "available", "respond_to_best_opportunity"

    def _prune(self, now: float) -> None:
        self.state.concerns = sorted(self.state.concerns, key=lambda x: x.updated_at)[-24:]
        self.state.commitments = sorted(self.state.commitments, key=lambda x: x.updated_at)[-16:]
        self.state.unfinished_thoughts = sorted(self.state.unfinished_thoughts, key=lambda x: x.updated_at)[-24:]
        self.state.tensions = sorted(self.state.tensions, key=lambda x: x.updated_at)[-16:]
        self.state.causal_trace = self.state.causal_trace[-120:]

    def _trace(self, kind: str, payload: Mapping[str, Any], now: float) -> None:
        self.state.causal_trace.append({"at": round(now, 3), "kind": kind, **dict(payload)})
        self.state.causal_trace = self.state.causal_trace[-120:]

    def _touch(self, now: float) -> None:
        self.state.revision += 1
        self.state.updated_at = now


__all__ = [
    "SELF_SCHEMA", "SELF_STATE_VERSION",
    "SelfConcern", "SelfCommitment", "UnfinishedThought", "SelfTension",
    "ContinuousSelfState", "SelfActionBias", "ContinuousSelfDynamics",
]
