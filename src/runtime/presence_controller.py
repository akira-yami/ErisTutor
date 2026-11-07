from __future__ import annotations

import time


from typing import Any, Mapping


class PresenceControllerMixin:
    """Bookkeeping tối thiểu cho nhịp autonomous của Architecture C.

    Cortex V2 không còn một pipeline micro-presence riêng chen trước public
    writer. Initiative và wording nằm trong ``AIAutonomousOrchestrator``; mixin
    này chỉ ghi lại việc Eris đã nói hay đã giữ im lặng để các read-model khác
    có thể quan sát mà không giành quyền quyết định.
    """

    def _note_autonomous_silence(self) -> int:
        self.presence_maintenance_silent_cycles = (
            int(getattr(self, "presence_maintenance_silent_cycles", 0) or 0) + 1
        )
        self.reengagement_quiet_cycles = (
            int(getattr(self, "reengagement_quiet_cycles", 0) or 0) + 1
        )
        return self.presence_maintenance_silent_cycles

    def _maybe_emit_ambient_micro_reflex(self, *, now: float | None = None) -> bool:
        """Đánh giá và phát xạ phản xạ sinh học tự nhiên (thở dài, ngâm nga, đổi dáng) khi rảnh."""
        clock = getattr(self, "interaction_clock", None)
        if clock is not None and getattr(clock, "speech_floor_busy", False):
            return False

        ts = float(now if now is not None else time.time())
        idle_seconds = 0.0
        if clock is not None and getattr(clock, "last_public_activity_at", 0.0) > 0.0:
            idle_seconds = ts - clock.last_public_activity_at

        # Nếu user mới tương tác gần đây (< 45s), không phát ambient
        if idle_seconds < 45.0:
            return False

        # Kiểm tra xem có đang dở pending input của user không
        if bool(getattr(self, "_pending_user_input", None)):
            return False

        try:
            from runtime.micro_reflex_system import get_micro_reflex_engine
            engine = get_micro_reflex_engine(logger=getattr(self, "logger", None))

            current_mood: Mapping[str, Any] = {}
            affect_bridge = getattr(self, "current_affect", None)
            if isinstance(affect_bridge, Mapping):
                current_mood = affect_bridge
            elif hasattr(self, "ai_state_reducer"):
                try:
                    snapshot = self.ai_state_reducer.snapshot()
                    current_mood = getattr(snapshot, "mood", {}) or {}
                except Exception:
                    pass

            ambient_kind = engine.evaluate_ambient(idle_seconds, current_mood=current_mood, now=ts)
            if ambient_kind is None:
                return False

            emit_fn = getattr(self, "_emit_fast_micro_act", None)
            if callable(emit_fn):
                return bool(emit_fn(surface="", act_kind=ambient_kind, timestamp=ts))
        except Exception as exc:
            logger = getattr(self, "logger", None)
            if logger:
                logger.debug("[ambient-reflex] eval error: %s", exc)
        return False

    def _note_autonomous_spoken(
        self,
        *,
        presence_maintenance: bool = False,
        reengagement: bool = False,
        now: float | None = None,
    ) -> None:
        current_time = time.time() if now is None else float(now)

        # V66: self-rhythm của Eris. Streak chỉ mô tả mật độ lời của CHÍNH Eris;
        # user reply không cấp lại "quyền nói". Khoảng cách đủ dài tự làm streak
        # decay, nhờ vậy pacing là self-regulation chứ không phải obedience.
        previous = float(getattr(self, "_last_expression_rhythm_spoken_at", 0.0) or 0.0)
        streak = max(0, int(getattr(self, "autonomous_expression_streak", 0) or 0))
        if previous <= 0.0 or current_time - previous >= 90.0:
            streak = 1
        else:
            streak += 1
        self.autonomous_expression_streak = streak
        self._last_expression_rhythm_spoken_at = current_time

        if presence_maintenance:
            self.last_presence_maintenance_at = current_time
            self.presence_maintenance_silent_cycles = 0
        if reengagement:
            self.last_reengagement_at = current_time
        self.reengagement_quiet_cycles = 0

        snub_tracker = getattr(self, "snub_tracker", None)
        spoken_text = str(getattr(self, "last_text", "") or "").strip()
        if snub_tracker is not None and spoken_text:
            try:
                snub_tracker.on_eris_spoke(spoken_text, channel="autonomous")
            except Exception as exc:  # External tracker boundary.
                self.last_snub_tracker_error = type(exc).__name__
