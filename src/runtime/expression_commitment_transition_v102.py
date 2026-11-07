# -*- coding: utf-8 -*-
"""ERIS V102 - Expression Commitment Transition.

V101 đã tách "nghĩ ra một ý" khỏi "hình thành ý định muốn biểu đạt". Tuy nhiên
V44/V67 vẫn còn một shortcut production:

    ExpressionUrge -> commitment_payload(...) ngay trong cùng lần shape

Nghĩa là chỉ cần urge tồn tại là semantic commitment public xuất hiện tức thời.
V102 lấy quyền đó ra khỏi ExpressionUrgeRuntime và biến commitment thành một
transition có state theo thời gian:

    ExpressionUrge
        -> commitment charge + dwell + semantic stability
        -> ExpressionCommitment
        -> Subject / Neuro / Surface

Các invariant:
- không có global winner; mỗi urge có một transition state riêng;
- một observation không đủ để commit, kể cả pressure rất cao;
- hard boundary thật chỉ DEFER projection, không xóa desire/commitment private;
- commitment dùng hysteresis: ngưỡng mở cao hơn ngưỡng release để state không
  rung ON/OFF khi pressure dao động nhẹ;
- semantic target đổi mạnh phải tích maturity lại;
- public receipt làm state cooling/released; public text không feed ngược vào
  cognition.

Module không thread, không LLM/TTS, không tự SPEAK và không lưu chain-of-thought.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
import re
import time
from typing import Any, Mapping

from cortex.subject.expression_commitment import commitment_payload, hard_expression_boundary


COMMITMENT_TRANSITION_SCHEMA = "eris.expression_commitment_transition.v102"
COMMITMENT_TRANSITION_OWNER = "expression_commitment_transition_v102"


def _clamp01(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = default
    if not math.isfinite(number):
        number = default
    return max(0.0, min(1.0, number))


def _compact(value: Any, limit: int = 900) -> str:
    text = " ".join(str(value or "").split()).strip()
    return text if len(text) <= limit else text[: max(0, limit - 1)].rstrip() + "…"


def _words(text: str) -> set[str]:
    return {
        word
        for word in re.findall(r"[\w\u00c0-\u024f\u1e00-\u1eff]+", str(text or "").casefold())
        if len(word) >= 3
    }


def _semantic_overlap(left: str, right: str) -> float:
    a, b = _words(left), _words(right)
    if not a or not b:
        return 0.0
    return len(a & b) / max(1, len(a | b))


@dataclass(slots=True)
class CommitmentTransitionStateV102:
    """State riêng của một urge trên đường trở thành semantic commitment.

    ``charge`` không phải một score bầu winner. Nó là biến tích phân cục bộ của
    đúng urge này: sustained pressure làm charge tăng, stale/low-drive làm giảm.
    ``open`` chỉ xảy ra sau dwell + observations + semantic stability.
    """

    urge_id: str
    semantic_target: str
    motive: str
    charge: float = 0.0
    observations: int = 0
    created_at: float = 0.0
    updated_at: float = 0.0
    last_seen_at: float = 0.0
    last_signal: float = 0.0
    state: str = "forming"
    committed_since: float = 0.0
    last_committed_at: float = 0.0
    last_boundary_reason: str = ""
    boundary_count: int = 0
    semantic_shifts: int = 0
    last_semantic_shift_at: float = 0.0
    release_count: int = 0
    satisfaction: float = 0.0

    def to_dict(self, *, now: float) -> dict[str, Any]:
        return {
            "schema": COMMITMENT_TRANSITION_SCHEMA,
            "urge_id": self.urge_id,
            "semantic_target": self.semantic_target,
            "motive": self.motive,
            "charge": round(_clamp01(self.charge), 3),
            "signal": round(_clamp01(self.last_signal), 3),
            "observations": self.observations,
            "state": self.state,
            "age_seconds": round(max(0.0, now - self.created_at), 2),
            "committed_age_seconds": round(max(0.0, now - self.committed_since), 2) if self.committed_since else 0.0,
            "last_boundary_reason": self.last_boundary_reason,
            "boundary_count": self.boundary_count,
            "semantic_shifts": self.semantic_shifts,
            "seconds_since_semantic_shift": (
                round(max(0.0, now - self.last_semantic_shift_at), 2)
                if self.last_semantic_shift_at else None
            ),
            "release_count": self.release_count,
            "satisfaction": round(_clamp01(self.satisfaction), 3),
        }


class ExpressionCommitmentTransitionV102:
    """Authority production cho transition ExpressionUrge -> Commitment.

    V102 cố ý không biết cách viết câu và không sở hữu public lane. Kết quả duy
    nhất của ``consider_urge`` là typed commitment payload hoặc ``None``.
    """

    # Hysteresis: mở cao, release thấp. Các con số không phải speech permission
    # độc lập; chúng chỉ là biên của state integrator sau dwell/stability checks.
    OPEN_CHARGE = 0.60
    RELEASE_CHARGE = 0.28
    MIN_OBSERVATIONS = 2
    MIN_DWELL_SECONDS = 1.5
    SEMANTIC_SETTLE_SECONDS = 1.5

    def __init__(self, host: Any, *, logger: Any | None = None) -> None:
        self.host = host
        self.logger = logger or getattr(host, "logger", None)
        self._states: dict[str, CommitmentTransitionStateV102] = {}
        self._last_log_signature = ""

    def _log(self, level: str, message: str, *args: Any) -> None:
        fn = getattr(self.logger, level, None) if self.logger is not None else None
        if callable(fn):
            try:
                fn(message, *args)
            except Exception:
                pass

    @staticmethod
    def _signal(urge: Any, *, pressure: float, freshness: float) -> float:
        satisfaction = _clamp01(getattr(urge, "satisfaction", 0.0))
        confidence = _clamp01(getattr(urge, "confidence", 0.72), 0.72)
        relevance = _clamp01(getattr(urge, "relevance", 0.60), 0.60)
        persistence = _clamp01(1.0 - satisfaction)
        # Pressure là current desire; freshness/persistence giữ continuity;
        # confidence/relevance chỉ modulation nhẹ, không tự cấp commitment.
        return _clamp01(
            0.42 * _clamp01(pressure)
            + 0.22 * _clamp01(freshness)
            + 0.18 * persistence
            + 0.10 * confidence
            + 0.08 * relevance
        )

    @staticmethod
    def _hard_boundary(runtime_context: Mapping[str, Any] | None) -> tuple[bool, str]:
        return hard_expression_boundary(runtime_context or {})

    def consider_urge(
        self,
        urge: Any,
        *,
        pressure: float,
        freshness: float,
        runtime_context: Mapping[str, Any] | None = None,
        now: float | None = None,
    ) -> dict[str, Any] | None:
        """Tích hợp một urge và chỉ trả commitment khi transition đã mở.

        Hard boundary không tiêu charge và không xóa committed state. Nó chỉ làm
        projection trả ``None`` trong tick hiện tại. Khi boundary hết, cùng state
        có thể tiếp tục mà không cần user "nạp quyền nói" trở lại.
        """

        current = float(now if now is not None else time.time())
        urge_id = _compact(getattr(urge, "urge_id", ""), 220)
        target = _compact(getattr(urge, "content", ""), 1200)
        motive = _compact(getattr(urge, "motive", "express"), 80).lower() or "express"
        if not urge_id or not target:
            return None

        signal = self._signal(urge, pressure=pressure, freshness=freshness)
        satisfaction = _clamp01(getattr(urge, "satisfaction", 0.0))
        state = self._states.get(urge_id)
        if state is None:
            state = CommitmentTransitionStateV102(
                urge_id=urge_id,
                semantic_target=target,
                motive=motive,
                # Lần đầu chỉ tạo một charge nhỏ. Pressure cao không được phép
                # biến observation đầu tiên thành public commitment tức thời.
                charge=_clamp01(0.12 + 0.28 * signal),
                observations=1,
                created_at=current,
                updated_at=current,
                last_seen_at=current,
                last_signal=signal,
                state="forming",
                satisfaction=satisfaction,
            )
            self._states[urge_id] = state
        else:
            elapsed = max(0.0, current - state.last_seen_at)
            overlap = _semantic_overlap(state.semantic_target, target)
            semantic_shift = bool(target != state.semantic_target and overlap < 0.34)
            if semantic_shift:
                state.semantic_shifts += 1
                state.last_semantic_shift_at = current
                state.semantic_target = target
                state.motive = motive
                state.charge = min(0.30, state.charge * 0.45)
                state.observations = 1
                state.committed_since = 0.0
                state.state = "forming"
            else:
                state.observations += 1
                state.semantic_target = target
                state.motive = motive
                # Integrator cục bộ. Cùng một urge cần sống qua thời gian mới mở;
                # low signal làm charge rơi thay vì chỉ so một threshold tức thời.
                dt_factor = max(0.30, min(1.35, (elapsed if elapsed > 0.0 else 1.0) / 4.0))
                natural_decay = 0.5 ** (elapsed / 240.0) if elapsed > 0.0 else 1.0
                state.charge = _clamp01(state.charge * natural_decay)
                if signal >= 0.42:
                    state.charge = _clamp01(
                        state.charge + (0.12 + 0.22 * signal) * dt_factor
                    )
                else:
                    state.charge = _clamp01(
                        state.charge - (0.07 + 0.18 * (0.42 - signal)) * dt_factor
                    )
            state.updated_at = current
            state.last_seen_at = current
            state.last_signal = signal
            state.satisfaction = satisfaction

        boundary, boundary_reason = self._hard_boundary(runtime_context)
        if boundary:
            state.boundary_count += 1
            state.last_boundary_reason = boundary_reason
            # Nếu đã committed thì semantic decision vẫn còn; chỉ projection bị
            # defer. Nếu chưa commit, giữ state holding để tiếp tục sau boundary.
            state.state = "deferred_committed" if state.committed_since > 0.0 else "holding"
            self._log_state(state, current)
            return None
        state.last_boundary_reason = ""

        age = max(0.0, current - state.created_at)
        stable_for = (
            max(0.0, current - state.last_semantic_shift_at)
            if state.last_semantic_shift_at > 0.0 else age
        )

        # Public receipt hoặc desire đã consummate phải đóng transition.
        if satisfaction >= 0.88:
            if state.committed_since > 0.0:
                state.release_count += 1
            state.state = "cooling"
            state.committed_since = 0.0
            state.charge = min(state.charge, 0.18)
            self._log_state(state, current)
            return None

        already_committed = state.committed_since > 0.0
        if already_committed:
            # Hysteresis: fluctuation nhỏ không làm semantic commitment rung.
            if state.charge < self.RELEASE_CHARGE:
                state.release_count += 1
                state.committed_since = 0.0
                state.state = "holding"
                self._log_state(state, current)
                return None
            state.state = "committed"
        else:
            can_open = (
                state.observations >= self.MIN_OBSERVATIONS
                and age >= self.MIN_DWELL_SECONDS
                and stable_for >= self.SEMANTIC_SETTLE_SECONDS
                and state.charge >= self.OPEN_CHARGE
            )
            if not can_open:
                state.state = "holding" if state.charge >= 0.34 else "forming"
                self._log_state(state, current)
                return None
            state.committed_since = current
            state.last_committed_at = current
            state.state = "committed"

        self._log_state(state, current)
        return commitment_payload(
            # Giữ origin/source identity V67 để downstream/replay không phải đổi
            # contract; authority tạo payload đã chuyển sang V102.
            origin="expression_urge",
            source_id=urge_id,
            semantic_target=target,
            motive=motive,
            pressure=_clamp01(pressure),
            freshness=_clamp01(freshness),
            persistence=_clamp01(1.0 - satisfaction),
        )

    def note_expressed(self, urge_id: str, *, now: float | None = None) -> bool:
        state = self._states.get(str(urge_id or "").strip())
        if state is None:
            return False
        current = float(now if now is not None else time.time())
        state.satisfaction = 1.0
        state.charge = min(state.charge, 0.12)
        state.committed_since = 0.0
        state.state = "cooling"
        state.updated_at = current
        state.last_seen_at = current
        self._log_state(state, current)
        return True

    def snapshot(self, *, now: float | None = None, limit: int = 12) -> tuple[dict[str, Any], ...]:
        current = float(now if now is not None else time.time())
        self._prune(current)
        rows = sorted(
            self._states.values(),
            key=lambda item: (item.committed_since > 0.0, item.charge, item.updated_at),
            reverse=True,
        )
        return tuple(item.to_dict(now=current) for item in rows[: max(0, int(limit))])

    def _prune(self, now: float) -> None:
        dead = []
        for urge_id, state in self._states.items():
            stale = max(0.0, now - state.last_seen_at)
            if stale > 1200.0 or (state.state == "cooling" and stale > 240.0):
                dead.append(urge_id)
        for urge_id in dead:
            self._states.pop(urge_id, None)

    def _log_state(self, state: CommitmentTransitionStateV102, now: float) -> None:
        signature = (
            f"{state.urge_id}:{state.state}:{round(state.charge, 1)}:"
            f"{state.observations}:{state.last_boundary_reason}"
        )
        if signature == self._last_log_signature:
            return
        self._last_log_signature = signature
        self._log(
            "info",
            "[commitment-transition-v102] urge=%s state=%s charge=%.2f obs=%s boundary=%s target=%s",
            state.urge_id,
            state.state,
            state.charge,
            state.observations,
            state.last_boundary_reason or "none",
            _compact(state.semantic_target, 180),
        )


__all__ = [
    "COMMITMENT_TRANSITION_SCHEMA",
    "COMMITMENT_TRANSITION_OWNER",
    "CommitmentTransitionStateV102",
    "ExpressionCommitmentTransitionV102",
]
