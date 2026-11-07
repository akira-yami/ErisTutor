# -*- coding: utf-8 -*-
"""
src/cortex/user_context_stream.py
=================================

Tầng 1 - Sensory / User Context Stream.

Trách nhiệm:
- Đọc diff màn hình, app đang active, OCR/vision hint, key/mouse/recent events.
- Gọi/nhận kết quả từ observer 0.6B nếu runtime đã bật.
- Xuất `RollingUserContext` nhỏ, ổn định, có thể đưa cho CortexRuntime và main planner.

Không làm:
- Không nói với user.
- Không commit memory.
- Không gọi model lớn.
- Không tự quyết định đánh thức main model.

Tầng này là kẻ gác cổng. Nó nhìn cửa. Nó không viết thơ về cái cửa.
"""

from __future__ import annotations

import math
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Mapping, Optional

try:
    from cortex.observer_contracts import ObserverFrame, SignalDiff, SignalSnapshot, clamp01, compact_text
except Exception:  # pragma: no cover
    from .observer_contracts import ObserverFrame, SignalDiff, SignalSnapshot, clamp01, compact_text  # type: ignore


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        n = float(value)
    except Exception:
        n = default
    if math.isnan(n) or math.isinf(n):
        n = default
    return n


def _ema(previous: float, new: float, alpha: float = 0.35) -> float:
    return clamp01(previous * (1.0 - alpha) + new * alpha)


@dataclass(slots=True)
class RollingUserContext:
    """Bản tóm tắt ngoại sinh đang lăn theo thời gian.

    external_pressure:
        Áp lực từ thế giới ngoài: lỗi, diff lớn, user gọi, scene lạ.
    interrupt_cost:
        Chi phí chen vào. Code/game/compile + idle thấp thì rất đắt.
    consolidation_pressure:
        Áp lực gom memory/trace sau nhiều diff hoặc sự kiện lặp.
    insight_pressure:
        Áp lực cần nhìn sâu hơn để tạo insight, không đồng nghĩa được nói.
    busy_score:
        Độ bận của bạn theo signal, không phải phán xét đạo đức. May quá.
    """

    timestamp: float = field(default_factory=time.time)
    focus_kind: str = "unknown"
    activity_intent: str = "unknown"
    active_app: str = ""
    active_window_title: str = ""
    idle_seconds: float = 0.0

    external_pressure: float = 0.0
    interrupt_cost: float = 0.0
    consolidation_pressure: float = 0.0
    insight_pressure: float = 0.0
    busy_score: float = 0.0

    novelty: float = 0.0
    repetition: float = 0.0
    possible_error: float = 0.0
    user_presence: float = 0.0
    resource_pressure: float = 0.0

    recommended_action: str = "observe_only"
    recommended_budget: str = "fast"
    evidence: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()
    debug: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def prompt_hint(self) -> str:
        """Render cực ngắn nếu cần đưa vào debug/prompt dev mode."""

        return (
            f"focus={self.focus_kind}; intent={self.activity_intent}; idle={self.idle_seconds:.0f}s; "
            f"external={self.external_pressure:.2f}; interrupt_cost={self.interrupt_cost:.2f}; "
            f"consolidation={self.consolidation_pressure:.2f}; insight={self.insight_pressure:.2f}"
        )


class UserContextStream:
    """Bộ sở hữu RollingUserContext.

    Có thể nhận `ObserverFrame` từ BackgroundObserver hiện tại. Nếu chưa có observer,
    nó vẫn chạy rule nhẹ từ SignalSnapshot/SignalDiff. Thiết kế mềm để không bắt repo
    cúi đầu trước một class duy nhất như nghi thức phong kiến.
    """

    def __init__(self, *, alpha: float = 0.36) -> None:
        self.alpha = max(0.05, min(0.85, float(alpha)))
        self.current = RollingUserContext()
        self._event_accumulator = 0.0
        self._last_update_at = 0.0

    def update(
        self,
        *,
        signal_snapshot: SignalSnapshot | Mapping[str, Any] | None = None,
        signal_diff: SignalDiff | None = None,
        observer_frame: ObserverFrame | None = None,
        user_text: str = "",
        channel: str = "autonomous",
        now: float | None = None,
    ) -> RollingUserContext:
        """Cập nhật context từ signal mới.

        `observer_frame` là nguồn tốt nhất vì nó đã qua Qwen 0.6B/rule observer.
        Nếu thiếu, ta dùng diff/snapshot. Tức là hệ vẫn sống, không nằm ăn vạ vì model bé ngủ quên.
        """

        ts = float(now or time.time())
        snapshot = signal_snapshot if isinstance(signal_snapshot, SignalSnapshot) else SignalSnapshot.from_mapping(signal_snapshot or {})
        diff = signal_diff or SignalDiff()
        frame = observer_frame

        focus_kind = str(getattr(frame, "focus_kind", "") or snapshot.focus_kind_hint or "unknown")
        activity_intent = str(getattr(frame, "activity_intent", "") or "unknown")
        salience = clamp01(getattr(frame, "salience", diff.diff_score if diff else 0.0))
        novelty = clamp01(getattr(frame, "novelty", getattr(diff, "novelty", 0.0)))
        repetition = clamp01(getattr(frame, "repetition", getattr(diff, "repetition", 0.0)))
        possible_error = clamp01(getattr(frame, "possible_error", 0.0))
        user_presence = clamp01(getattr(frame, "user_presence", 0.0))
        recommended_action = str(getattr(frame, "recommended_action", "observe_only") or "observe_only")
        recommended_budget = str(getattr(frame, "recommended_budget", "fast") or "fast")

        direct_user_pull = 1.0 if user_text and channel in {"chat", "force_speak"} else 0.0
        resource_pressure = clamp01(max(snapshot.cpu_percent / 100.0, snapshot.ram_percent / 100.0))
        busy_score = self._busy_score(snapshot=snapshot, focus_kind=focus_kind, activity_intent=activity_intent, resource_pressure=resource_pressure)
        interrupt_cost = self._interrupt_cost(snapshot=snapshot, focus_kind=focus_kind, busy_score=busy_score, direct_user_pull=direct_user_pull)

        external_pressure_raw = clamp01(
            0.42 * salience
            + 0.22 * novelty
            + 0.22 * possible_error
            + 0.10 * direct_user_pull
            + 0.04 * user_presence
        )
        if recommended_action == "wake_main_cognition":
            external_pressure_raw = max(external_pressure_raw, 0.82)
        elif recommended_action == "call_vision_compressor":
            external_pressure_raw = max(external_pressure_raw, 0.62)

        # Diff lặp nhiều nhưng không mới -> nên gom/tiêu hóa, không nên liên tục gọi main.
        repeated_pattern_pressure = clamp01(repetition * 0.55 + (1.0 if diff.reason else 0.0) * 0.12)
        consolidation_raw = clamp01(
            0.36 * repeated_pattern_pressure
            + 0.28 * possible_error
            + 0.18 * novelty
            + 0.18 * max(0.0, salience - 0.35)
        )
        insight_raw = clamp01(
            0.34 * possible_error
            + 0.28 * novelty
            + 0.22 * salience
            + 0.16 * (1.0 - repetition)
        )

        prev = self.current
        if ts - self._last_update_at > 90:
            # Nghỉ lâu thì giảm tích lũy. Không thì context cũ ám mãi như cache hỏng.
            self._event_accumulator *= 0.55
        self._event_accumulator = clamp01(self._event_accumulator * 0.86 + external_pressure_raw * 0.20)

        evidence = []
        if frame is not None:
            evidence.extend(str(x) for x in getattr(frame, "evidence", ()) or ())
            evidence.extend(str(x) for x in getattr(frame, "notes", ()) or ())
        evidence.extend(str(x) for x in getattr(diff, "reason", ()) or ())
        if user_text:
            evidence.append("direct_user_input")

        self.current = RollingUserContext(
            timestamp=ts,
            focus_kind=focus_kind,
            activity_intent=activity_intent,
            active_app=compact_text(snapshot.active_app, 120),
            active_window_title=compact_text(snapshot.active_window_title, 180),
            idle_seconds=max(0.0, _safe_float(snapshot.idle_seconds)),
            external_pressure=_ema(prev.external_pressure, max(external_pressure_raw, self._event_accumulator), self.alpha),
            interrupt_cost=_ema(prev.interrupt_cost, interrupt_cost, self.alpha),
            consolidation_pressure=_ema(prev.consolidation_pressure, consolidation_raw, self.alpha),
            insight_pressure=_ema(prev.insight_pressure, insight_raw, self.alpha),
            busy_score=_ema(prev.busy_score, busy_score, self.alpha),
            novelty=_ema(prev.novelty, novelty, self.alpha),
            repetition=_ema(prev.repetition, repetition, self.alpha),
            possible_error=_ema(prev.possible_error, possible_error, self.alpha),
            user_presence=_ema(prev.user_presence, user_presence, self.alpha),
            resource_pressure=_ema(prev.resource_pressure, resource_pressure, self.alpha),
            recommended_action=recommended_action,
            recommended_budget=recommended_budget,
            evidence=tuple(dict.fromkeys(x for x in evidence if str(x).strip()))[:20],
            notes=("rolling_user_context",),
            debug={
                "salience": salience,
                "diff_score": getattr(diff, "diff_score", 0.0),
                "direct_user_pull": direct_user_pull,
                "raw_external_pressure": external_pressure_raw,
                "raw_interrupt_cost": interrupt_cost,
            },
        )
        self._last_update_at = ts
        return self.current

    def _busy_score(
        self,
        *,
        snapshot: SignalSnapshot,
        focus_kind: str,
        activity_intent: str,
        resource_pressure: float,
    ) -> float:
        idle = max(0.0, _safe_float(snapshot.idle_seconds))
        focus_busy = 0.0
        if focus_kind in {"code", "docs"}:
            focus_busy += 0.42
        if focus_kind in {"game"}:
            focus_busy += 0.70
        if activity_intent in {"debugging", "designing", "reading", "searching"}:
            focus_busy += 0.22
        if activity_intent == "fragmented":
            focus_busy += 0.12
        if idle < 20:
            focus_busy += 0.22
        elif idle > 300:
            focus_busy -= 0.18
        return clamp01(focus_busy + 0.34 * resource_pressure)

    def _interrupt_cost(
        self,
        *,
        snapshot: SignalSnapshot,
        focus_kind: str,
        busy_score: float,
        direct_user_pull: float,
    ) -> float:
        if direct_user_pull >= 0.9:
            return 0.05
        idle = max(0.0, _safe_float(snapshot.idle_seconds))
        cost = 0.22 + 0.58 * busy_score
        if focus_kind in {"game"}:
            cost += 0.18
        if idle > 300:
            cost -= 0.32
        elif idle > 120:
            cost -= 0.16
        return clamp01(cost)
