# -*- coding: utf-8 -*-
"""Temporal Mind: nhịp nhận thức rẻ nằm trước các lượt suy luận nặng.

Module này không phải một bộ não mới và không sinh nội dung. Nó chỉ tích lũy,
quên và ưu tiên các *tín hiệu thời gian* để trả lời một câu hỏi cơ học:
"đã đáng đánh thức deliberation nặng chưa?".

Mục tiêu của V19:
- im lặng là trạng thái mặc định, không cần gọi LLM để phê duyệt;
- tín hiệu yếu có thể tích lũy nhưng sẽ decay nếu thế giới không tiếp tục xác nhận;
- event có revision giống nhau không được cộng áp lực vô hạn qua mỗi tick;
- event có nghĩa vụ rõ (force/follow-up/structured due) đánh thức ngay;
- sau một lượt silence, public act hoặc user turn, nhịp được cập nhật thay vì reset não.
"""
from __future__ import annotations

import math
import time
from dataclasses import dataclass, field, replace
from typing import Any, Callable, Iterable, Mapping

TEMPORAL_MIND_VERSION = "temporal-mind-v2-relational"


@dataclass(frozen=True, slots=True)
class TemporalSignal:
    kind: str
    strength: float
    source: str
    revision: int = 0
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "kind", str(self.kind or "").strip().lower())
        object.__setattr__(self, "source", str(self.source or "runtime").strip() or "runtime")
        object.__setattr__(self, "strength", max(0.0, min(1.0, float(self.strength))))
        object.__setattr__(self, "revision", max(0, int(self.revision or 0)))


@dataclass(frozen=True, slots=True)
class PendingTemporalSignal:
    key: str
    kind: str
    source: str
    revision: int
    pressure: float
    first_seen_at: float
    last_seen_at: float
    seen_count: int


@dataclass(frozen=True, slots=True)
class TemporalMindState:
    revision: int = 0
    activation: float = 0.0
    tension: float = 0.0
    readiness: float = 0.0
    focus_key: str = ""
    last_tick_at: float = 0.0
    last_user_at: float = 0.0
    last_public_at: float = 0.0
    last_heavy_at: float = 0.0
    last_silence_at: float = 0.0
    quiet_until: float = 0.0
    pending: tuple[PendingTemporalSignal, ...] = ()


@dataclass(frozen=True, slots=True)
class TemporalGateDecision:
    wake: bool
    reason: str
    score: float
    activation: float
    dominant_kind: str
    pending_kinds: tuple[str, ...]
    next_check_seconds: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "temporal_version": TEMPORAL_MIND_VERSION,
            "wake": self.wake,
            "reason": self.reason,
            "score": self.score,
            "activation": self.activation,
            "dominant_kind": self.dominant_kind,
            "pending_kinds": list(self.pending_kinds),
            "next_check_seconds": self.next_check_seconds,
        }


@dataclass(frozen=True, slots=True)
class TemporalMindConfig:
    # Half-life ngắn để một thay đổi cửa sổ không ám hệ thống cả buổi tối.
    activation_half_life_seconds: float = 18.0
    pending_half_life_seconds: float = 24.0
    wake_threshold: float = 0.56
    # Khoảng cách tối thiểu giữa hai deliberation nặng không có nghĩa vụ cứng.
    heavy_cooldown_seconds: float = 5.0
    silence_cooldown_seconds: float = 8.0
    public_cooldown_seconds: float = 6.0
    max_pending: int = 12


_EVENT_WEIGHTS: dict[str, float] = {
    "force": 1.00,
    "user_turn": 1.00,
    "structured_due": 0.95,
    "followup_due": 0.90,
    "discovery_ready": 0.86,
    "open_discourse": 0.74,
    "activity_event": 0.62,
    "scene_delta": 0.52,
    "affordance_changed": 0.48,
    "seed_available": 0.42,
    "attention_pressure": 0.34,
    "relational_pressure": 0.58,
    "initiative_pressure": 0.64,
    # V60: không phải raw tab/OCR event. Đây là pattern đã được ghép có
    # provenance (ví dụ fail -> docs cùng task) nên đủ trọng lượng để gọi
    # cognition xem xét; InitiativeArbiter vẫn có quyền chọn WAIT.
    "environment_meaning": 0.88,
}

_HARD_WAKE = frozenset({"force", "user_turn", "structured_due", "followup_due"})


class TemporalMind:
    """State machine rẻ cho nhịp thức/ngủ của cognition.

    Không model call, không memory retrieval, không thay semantic authority.
    Các con số ở đây chỉ quản ngân sách *khi nào suy nghĩ*, không chọn *nghĩ gì*.
    """

    def __init__(
        self,
        *,
        config: TemporalMindConfig | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.config = config or TemporalMindConfig()
        self._clock = clock
        self._state = TemporalMindState()

    @property
    def state(self) -> TemporalMindState:
        return self._state

    @staticmethod
    def _decay(value: float, elapsed: float, half_life: float) -> float:
        if value <= 0.0 or elapsed <= 0.0:
            return max(0.0, min(1.0, value))
        half_life = max(0.001, float(half_life))
        return max(0.0, min(1.0, value * math.pow(2.0, -elapsed / half_life)))

    @staticmethod
    def _signal_key(signal: TemporalSignal) -> str:
        # Revision > 0 là identity tốt nhất. Event không revision dùng source+kind;
        # seen_count vẫn tăng nhưng contribution lặp bị giảm mạnh ở tick kế tiếp.
        return f"{signal.kind}|{signal.source}|{signal.revision}"

    def _decayed_state(self, now: float) -> TemporalMindState:
        state = self._state
        elapsed = max(0.0, now - state.last_tick_at) if state.last_tick_at else 0.0
        activation = self._decay(state.activation, elapsed, self.config.activation_half_life_seconds)
        tension = self._decay(state.tension, elapsed, self.config.activation_half_life_seconds * 1.4)
        pending: list[PendingTemporalSignal] = []
        for item in state.pending:
            decayed = self._decay(
                item.pressure,
                max(0.0, now - item.last_seen_at),
                self.config.pending_half_life_seconds,
            )
            if decayed < 0.035:
                continue
            pending.append(replace(item, pressure=decayed))
        return replace(
            state,
            activation=activation,
            tension=tension,
            readiness=max(0.0, min(1.0, activation * 0.78 + tension * 0.22)),
            last_tick_at=now,
            pending=tuple(pending[: self.config.max_pending]),
        )

    def observe(
        self,
        signals: Iterable[TemporalSignal],
        *,
        now: float | None = None,
        focus_key: str = "",
    ) -> TemporalGateDecision:
        current_time = self._clock() if now is None else float(now)
        state = self._decayed_state(current_time)
        pending_by_key = {item.key: item for item in state.pending}
        dominant_kind = ""
        dominant_contribution = 0.0
        hard_wake_reason = ""
        activation = state.activation
        tension = state.tension

        for signal in tuple(signals):
            key = self._signal_key(signal)
            prior = pending_by_key.get(key)
            weight = _EVENT_WEIGHTS.get(signal.kind, 0.30)
            contribution = max(0.0, min(1.0, signal.strength * weight))

            # Cùng một evidence revision lặp ở mỗi tick chỉ nhắc rằng nó vẫn tồn tại;
            # không được biến một cửa sổ đứng yên thành cấp cứu nhận thức.
            if prior is not None:
                age = max(0.0, current_time - prior.last_seen_at)
                repeat_factor = 0.12 if age < 8.0 else min(0.55, 0.12 + age / 80.0)
                effective = contribution * repeat_factor
                pressure = max(prior.pressure, contribution * 0.72)
                pending_by_key[key] = replace(
                    prior,
                    pressure=max(0.0, min(1.0, pressure)),
                    last_seen_at=current_time,
                    seen_count=prior.seen_count + 1,
                )
            else:
                effective = contribution
                pending_by_key[key] = PendingTemporalSignal(
                    key=key,
                    kind=signal.kind,
                    source=signal.source,
                    revision=signal.revision,
                    pressure=contribution,
                    first_seen_at=current_time,
                    last_seen_at=current_time,
                    seen_count=1,
                )

            # Probabilistic-union style accumulation: không cộng tuyến tính đến vô hạn.
            activation = 1.0 - (1.0 - activation) * (1.0 - effective * 0.82)
            if signal.kind in {"open_discourse", "followup_due", "structured_due", "user_turn", "environment_meaning"}:
                tension = 1.0 - (1.0 - tension) * (1.0 - effective * 0.70)
            if contribution > dominant_contribution:
                dominant_contribution = contribution
                dominant_kind = signal.kind
            if signal.kind in _HARD_WAKE and signal.strength >= 0.70:
                hard_wake_reason = signal.kind

        pending = sorted(
            pending_by_key.values(),
            key=lambda item: (-item.pressure, -item.last_seen_at, item.key),
        )[: self.config.max_pending]
        readiness = max(0.0, min(1.0, activation * 0.78 + tension * 0.22))
        state = replace(
            state,
            revision=state.revision + 1,
            activation=activation,
            tension=tension,
            readiness=readiness,
            focus_key=str(focus_key or state.focus_key or ""),
            pending=tuple(pending),
        )
        self._state = state

        if hard_wake_reason:
            return self._decision(True, f"hard:{hard_wake_reason}", dominant_kind, current_time)

        cooldown_until = max(state.quiet_until, state.last_heavy_at + self.config.heavy_cooldown_seconds)
        if current_time < cooldown_until:
            return self._decision(False, "cooldown", dominant_kind, current_time)
        if readiness >= self.config.wake_threshold:
            return self._decision(True, "pressure_threshold", dominant_kind, current_time)
        return self._decision(False, "quiet_default", dominant_kind, current_time)

    def _decision(self, wake: bool, reason: str, dominant_kind: str, now: float) -> TemporalGateDecision:
        state = self._state
        score = state.readiness
        if wake:
            next_check = 0.0
        elif score >= self.config.wake_threshold * 0.72:
            next_check = 1.5
        elif score >= 0.28:
            next_check = 3.0
        else:
            next_check = 5.5
        if state.quiet_until > now:
            next_check = min(max(0.5, state.quiet_until - now), max(next_check, 0.5))
        return TemporalGateDecision(
            wake=wake,
            reason=f"temporal:{reason}",
            score=score,
            activation=state.activation,
            dominant_kind=dominant_kind,
            pending_kinds=tuple(dict.fromkeys(item.kind for item in state.pending))[:8],
            next_check_seconds=max(0.0, next_check),
        )

    def mark_heavy_deliberation(self, *, now: float | None = None) -> None:
        current_time = self._clock() if now is None else float(now)
        state = self._decayed_state(current_time)
        self._state = replace(state, last_heavy_at=current_time, revision=state.revision + 1)

    def mark_silence(self, *, now: float | None = None) -> None:
        current_time = self._clock() if now is None else float(now)
        state = self._decayed_state(current_time)
        self._state = replace(
            state,
            activation=state.activation * 0.34,
            tension=state.tension * 0.55,
            readiness=state.readiness * 0.38,
            last_silence_at=current_time,
            quiet_until=max(state.quiet_until, current_time + self.config.silence_cooldown_seconds),
            revision=state.revision + 1,
        )

    def note_user_turn(self, *, now: float | None = None, focus_key: str = "direct_chat") -> None:
        current_time = self._clock() if now is None else float(now)
        state = self._decayed_state(current_time)
        self._state = replace(
            state,
            activation=max(state.activation, 0.88),
            tension=max(state.tension, 0.62),
            readiness=max(state.readiness, 0.82),
            focus_key=focus_key or state.focus_key,
            last_user_at=current_time,
            quiet_until=0.0,
            revision=state.revision + 1,
        )

    def note_public_act(self, *, now: float | None = None, focus_key: str = "") -> None:
        current_time = self._clock() if now is None else float(now)
        state = self._decayed_state(current_time)
        self._state = replace(
            state,
            activation=state.activation * 0.28,
            tension=state.tension * 0.42,
            readiness=state.readiness * 0.32,
            focus_key=focus_key or state.focus_key,
            last_public_at=current_time,
            quiet_until=max(state.quiet_until, current_time + self.config.public_cooldown_seconds),
            revision=state.revision + 1,
        )

    def prompt_context(self, *, now: float | None = None) -> str:
        """Projection công khai nhỏ cho planner/surface; không lộ private evidence."""
        current_time = self._clock() if now is None else float(now)
        state = self._decayed_state(current_time)
        self._state = state
        pending = ",".join(tuple(dict.fromkeys(item.kind for item in state.pending))[:5]) if state.pending else "none"
        cadence = "active" if state.readiness >= 0.70 else "holding" if state.readiness >= 0.35 else "quiet"
        return (
            "TEMPORAL MIND (timing only, not semantic authority): "
            f"cadence={cadence}; readiness={state.readiness:.2f}; pending={pending}; "
            "prefer a short self-contained first clause; do not add filler merely to appear responsive; "
            "silence requires no explanation."
        )


__all__ = [
    "TEMPORAL_MIND_VERSION",
    "PendingTemporalSignal",
    "TemporalGateDecision",
    "TemporalMind",
    "TemporalMindConfig",
    "TemporalMindState",
    "TemporalSignal",
]
