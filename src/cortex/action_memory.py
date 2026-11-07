"""Action memory and rhythm state for ERIS.

File này không quyết định câu chữ. Nó chỉ giữ lịch sử hành vi để planner
không cư xử như cá vàng bị reset não sau mỗi frame.

Mục tiêu:
- nhớ action gần đây để chống lặp discrete
- tích social_fatigue / boredom / exposure_recoil
- tạo rhythm_phase cho planner đọc: settled, watching, touch, exposure, recoil,
  repair, execute, withdrawn, dormant
- serialize được để bạn tự nhét vào PersistentInternalState nếu muốn

"""

from __future__ import annotations

from dataclasses import dataclass, field
from time import monotonic
from typing import Any, Iterable, Mapping


# Các hành vi có tính "lộ diện" cao. Dùng liên tục sẽ làm ERIS giống đang diễn.
EXPOSURE_ACTIONS = {
    "guarded_reply",
    "reassure",
    "relational_checkin",
    "inner_whim",
    "light_muse",
    "curious_question",
}

# Các hành vi dễ gây mệt xã hội nếu lặp.
SOCIAL_ACTIONS = {
    "tease",
    "random_chatter",
    "curious_question",
    "relational_checkin",
    "reengagement",
    "reassure",
    "ask",
}

# Các hành vi giữ nhịp yên. Lặp vài lần vẫn ổn hơn lặp tease/chatter.
LOW_PRESSURE_ACTIONS = {
    "no_speech",
    "observe",
    "withdraw",
    "suppress",
    "indirect_presence",
    "micro_presence",
}


@dataclass(slots=True)
class ActionTrace:
    """Một dấu vết hành vi đã xảy ra.

    action:
        speech_act đã chọn.
    family:
        semantic family hoặc intent family. Nếu chưa có thì để rỗng.
    channel:
        autonomous/chat/force_speak.
    timestamp:
        monotonic time khi ghi nhận. Dùng monotonic để tránh lỗi đổi giờ hệ thống.
    spoke:
        True nếu action có tạo lời nói public.
    utility:
        utility đã chọn, nếu scorer có cung cấp.
    outcome:
        optional: positive/negative/neutral/correction/ignored. Có thể update sau.
    """

    action: str
    family: str = ""
    channel: str = ""
    timestamp: float = 0.0
    spoke: bool = False
    utility: float = 0.0
    outcome: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "action": self.action,
            "family": self.family,
            "channel": self.channel,
            "timestamp": self.timestamp,
            "spoke": self.spoke,
            "utility": self.utility,
            "outcome": self.outcome,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ActionTrace":
        return cls(
            action=str(data.get("action") or ""),
            family=str(data.get("family") or ""),
            channel=str(data.get("channel") or ""),
            timestamp=_float(data.get("timestamp"), 0.0),
            spoke=bool(data.get("spoke", False)),
            utility=_float(data.get("utility"), 0.0),
            outcome=str(data.get("outcome") or ""),
        )


@dataclass(slots=True)
class ActionMemoryState:
    """State dài hơi cho action planner.

    Đây là phần bạn cần persist nếu muốn ERIS có lịch sử hành vi thật.
    Nếu không persist, planner vẫn chạy, nhưng sẽ mất cảm giác tích lũy qua session.
    """

    traces: list[ActionTrace] = field(default_factory=list)
    max_traces: int = 48

    # Áp lực mệt xã hội. Cao thì giảm hỏi, check-in, chatter, tease.
    social_fatigue: float = 0.0

    # Áp lực chán/đứng hình. Cao không đồng nghĩa nói nhiều; nó chỉ tăng nhu cầu đổi nhịp.
    boredom_pressure: float = 0.0

    # Sau khi ERIS nói/lộ điều khá gần hoặc symbolic, recoil tăng để lượt sau rút nhẹ.
    exposure_recoil: float = 0.0

    # Áp lực giữ continuity khi im lâu nhưng vẫn có shared space.
    continuity_pressure: float = 0.0

    # Mức quen/lờn với từng action. Action càng bị dùng nhiều càng bị penalty.
    habituation: dict[str, float] = field(default_factory=dict)

    # Pha rhythm public. Planner dùng như một state mềm, không phải rule tuyệt đối.
    rhythm_phase: str = "settled"

    # Timestamp cuối cùng có lời nói public.
    last_spoken_ts: float = 0.0

    # Timestamp cuối cùng có action bất kỳ.
    last_action_ts: float = 0.0

    # Tín hiệu debug ngắn để ghi vào notes.
    last_update_reason: str = ""

    def recent_actions(self, limit: int = 8) -> tuple[str, ...]:
        """Trả action gần đây nhất, mới nhất ở cuối tuple."""
        if limit <= 0:
            return ()
        return tuple(trace.action for trace in self.traces[-limit:])

    def recent_families(self, limit: int = 8) -> tuple[str, ...]:
        """Trả semantic/action family gần đây, bỏ rỗng."""
        if limit <= 0:
            return ()
        return tuple(trace.family for trace in self.traces[-limit:] if trace.family)

    def seconds_since_action(self, now: float | None = None) -> float:
        now = monotonic() if now is None else now
        if self.last_action_ts <= 0:
            return 999999.0
        return max(0.0, now - self.last_action_ts)

    def seconds_since_spoken(self, now: float | None = None) -> float:
        now = monotonic() if now is None else now
        if self.last_spoken_ts <= 0:
            return 999999.0
        return max(0.0, now - self.last_spoken_ts)

    def action_count(self, action: str, window: int = 8) -> int:
        """Đếm action trong cửa sổ gần. Dùng cho repetition penalty."""
        if not action:
            return 0
        return sum(1 for item in self.recent_actions(window) if item == action)

    def family_count(self, family: str, window: int = 8) -> int:
        if not family:
            return 0
        return sum(1 for item in self.recent_families(window) if item == family)

    def record(
        self,
        *,
        action: str,
        family: str = "",
        channel: str = "",
        spoke: bool | None = None,
        utility: float = 0.0,
        outcome: str = "",
        now: float | None = None,
    ) -> None:
        """Ghi nhận action sau khi planner chốt.

        Hàm này nên được gọi ở coordinator sau khi đã có final decision.
        Nếu bạn chỉ muốn planner tự nhớ trong RAM, có thể để ActionPlanner gọi luôn.
        """
        now = monotonic() if now is None else now
        action = str(action or "no_speech")
        spoke = action != "no_speech" if spoke is None else bool(spoke)

        self.traces.append(
            ActionTrace(
                action=action,
                family=str(family or ""),
                channel=str(channel or ""),
                timestamp=now,
                spoke=spoke,
                utility=float(utility or 0.0),
                outcome=str(outcome or ""),
            )
        )
        if len(self.traces) > self.max_traces:
            self.traces = self.traces[-self.max_traces :]

        self.last_action_ts = now
        if spoke:
            self.last_spoken_ts = now

        self._update_pressures_after_action(action=action, family=family, channel=channel, spoke=spoke)
        self.rhythm_phase = self.infer_rhythm_phase(channel=channel)

    def decay(self, *, elapsed_seconds: float) -> None:
        """Decay áp lực theo thời gian.

        Gọi mỗi loop nếu muốn state mềm hơn. Không gọi cũng không chết, chỉ thô hơn.
        """
        elapsed = max(0.0, float(elapsed_seconds or 0.0))
        # Decay theo hàm tuyến tính nhẹ để dễ debug. Đừng biến file này thành luận án vật lý.
        k = min(1.0, elapsed / 240.0)
        self.social_fatigue = _clamp01(self.social_fatigue * (1.0 - 0.45 * k))
        self.exposure_recoil = _clamp01(self.exposure_recoil * (1.0 - 0.55 * k))
        self.boredom_pressure = _clamp01(self.boredom_pressure * (1.0 - 0.20 * k))
        self.continuity_pressure = _clamp01(self.continuity_pressure * (1.0 - 0.18 * k))
        for key in list(self.habituation):
            self.habituation[key] = _clamp01(self.habituation[key] * (1.0 - 0.35 * k))
            if self.habituation[key] <= 0.015:
                del self.habituation[key]

    def absorb_context(
        self,
        *,
        channel: str,
        snapshot: Any = None,
        self_state: Any = None,
        social_presence: Any = None,
        conversation_dynamics: Any = None,
        relationship_dynamics: Any = None,
        goal_state: Any = None,
        now: float | None = None,
    ) -> None:
        """Cập nhật áp lực nền từ context trước khi planner score.

        Hàm này đọc mềm bằng getattr để không phụ thuộc schema cụ thể.
        """
        now = monotonic() if now is None else now
        idle_seconds = _float(getattr(snapshot, "idle_seconds", 0.0), 0.0)
        state_label = str(getattr(self_state, "state_label", "") or "")

        # Idle dài + có shared space thì continuity pressure tăng. Nhưng không ép nói.
        if idle_seconds >= 90:
            self.continuity_pressure = _clamp01(self.continuity_pressure + min(0.20, idle_seconds / 1800.0))
            self.boredom_pressure = _clamp01(self.boredom_pressure + min(0.16, idle_seconds / 2400.0))

        # Overloaded làm giảm social bandwidth, tăng recoil.
        if state_label == "overloaded":
            self.social_fatigue = _clamp01(self.social_fatigue + 0.08)
            self.exposure_recoil = _clamp01(self.exposure_recoil + 0.06)

        mode = str(getattr(conversation_dynamics, "mode", "") or "")
        pressure = _float(getattr(conversation_dynamics, "conversation_pressure", 0.0), 0.0)
        if mode == "repair":
            # Khi đang repair, giảm boredom vì việc hiện tại rõ ràng: sửa cho đúng.
            self.boredom_pressure = _clamp01(self.boredom_pressure * 0.82)
            self.social_fatigue = _clamp01(self.social_fatigue + 0.06 * pressure)
        elif mode == "execute":
            self.boredom_pressure = _clamp01(self.boredom_pressure * 0.88)

        dignity_defense = _float(getattr(relationship_dynamics, "dignity_defense", 0.0), 0.0)
        preferred_distance = _float(getattr(relationship_dynamics, "preferred_distance", 0.0), 0.0)
        if dignity_defense >= 0.45 or preferred_distance >= 0.60:
            self.exposure_recoil = _clamp01(self.exposure_recoil + 0.04)
            self.social_fatigue = _clamp01(self.social_fatigue + 0.04)

        if bool(getattr(goal_state, "active", False)):
            strength = _clamp01(_float(getattr(goal_state, "strength", 0.0), 0.0))
            intervention = _clamp01(_float(getattr(goal_state, "intervention_permission", 0.0), 0.0))
            urgency = _clamp01(_float(getattr(goal_state, "urgency", 0.0), 0.0))
            self.continuity_pressure = _clamp01(self.continuity_pressure + 0.08 * strength)
            if bool(getattr(goal_state, "can_intervene", False)):
                self.boredom_pressure = _clamp01(self.boredom_pressure + 0.04 * max(urgency, intervention))
            else:
                self.exposure_recoil = _clamp01(self.exposure_recoil + 0.04 * (1.0 - intervention))

        self.rhythm_phase = self.infer_rhythm_phase(channel=channel, now=now)
        self.last_update_reason = (
            f"phase={self.rhythm_phase}; fatigue={self.social_fatigue:.2f}; "
            f"boredom={self.boredom_pressure:.2f}; recoil={self.exposure_recoil:.2f}; "
            f"continuity={self.continuity_pressure:.2f}"
        )

    def infer_rhythm_phase(self, *, channel: str = "", now: float | None = None) -> str:
        """Suy rhythm phase từ lịch sử và áp lực.

        Đây là nhịp mềm. Planner dùng nó như bias, không phải định mệnh.
        """
        now = monotonic() if now is None else now
        recent = self.recent_actions(4)
        last = recent[-1] if recent else ""
        channel = str(channel or "")

        if channel == "chat":
            # Chat thường là execute/repair/explain, rhythm autonomous không nên chen quá mạnh.
            if self.exposure_recoil >= 0.55:
                return "recoil"
            return "execute"

        if last in {"guarded_reply", "reassure", "relational_checkin", "inner_whim"}:
            return "exposure"
        if self.exposure_recoil >= 0.48:
            return "recoil"
        if last in {"withdraw", "suppress"}:
            return "withdrawn"
        if self.seconds_since_spoken(now) >= 240 and self.boredom_pressure >= 0.42:
            return "dormant"
        if channel == "force_speak":
            return "touch"
        if self.continuity_pressure >= 0.45:
            return "watching"
        return "settled"

    def _update_pressures_after_action(self, *, action: str, family: str, channel: str, spoke: bool) -> None:
        # Habituation tăng theo action vừa dùng, giảm nhẹ các action khác.
        if action:
            self.habituation[action] = _clamp01(self.habituation.get(action, 0.0) + 0.16)
        for key in list(self.habituation):
            if key != action:
                self.habituation[key] = _clamp01(self.habituation[key] * 0.94)

        if action in SOCIAL_ACTIONS:
            self.social_fatigue = _clamp01(self.social_fatigue + 0.10)
        elif action in LOW_PRESSURE_ACTIONS:
            self.social_fatigue = _clamp01(self.social_fatigue * 0.94)

        if action in EXPOSURE_ACTIONS:
            self.exposure_recoil = _clamp01(self.exposure_recoil + 0.18)
        elif action in {"withdraw", "suppress", "no_speech"}:
            self.exposure_recoil = _clamp01(self.exposure_recoil * 0.86)

        if spoke:
            self.continuity_pressure = _clamp01(self.continuity_pressure * 0.72)
            self.boredom_pressure = _clamp01(self.boredom_pressure * 0.78)
        else:
            self.continuity_pressure = _clamp01(self.continuity_pressure + 0.03)

    def to_dict(self) -> dict[str, Any]:
        return {
            "traces": [trace.to_dict() for trace in self.traces[-self.max_traces :]],
            "max_traces": self.max_traces,
            "social_fatigue": self.social_fatigue,
            "boredom_pressure": self.boredom_pressure,
            "exposure_recoil": self.exposure_recoil,
            "continuity_pressure": self.continuity_pressure,
            "habituation": dict(self.habituation),
            "rhythm_phase": self.rhythm_phase,
            "last_spoken_ts": self.last_spoken_ts,
            "last_action_ts": self.last_action_ts,
            "last_update_reason": self.last_update_reason,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any] | None) -> "ActionMemoryState":
        if not data:
            return cls()
        state = cls(
            traces=[ActionTrace.from_dict(item) for item in data.get("traces", []) if isinstance(item, Mapping)],
            max_traces=int(data.get("max_traces") or 48),
            social_fatigue=_float(data.get("social_fatigue"), 0.0),
            boredom_pressure=_float(data.get("boredom_pressure"), 0.0),
            exposure_recoil=_float(data.get("exposure_recoil"), 0.0),
            continuity_pressure=_float(data.get("continuity_pressure"), 0.0),
            habituation={str(k): _float(v, 0.0) for k, v in dict(data.get("habituation") or {}).items()},
            rhythm_phase=str(data.get("rhythm_phase") or "settled"),
            last_spoken_ts=_float(data.get("last_spoken_ts"), 0.0),
            last_action_ts=_float(data.get("last_action_ts"), 0.0),
            last_update_reason=str(data.get("last_update_reason") or ""),
        )
        if len(state.traces) > state.max_traces:
            state.traces = state.traces[-state.max_traces :]
        current = monotonic()
        if state.last_action_ts > current + 60.0:
            state.last_action_ts = 0.0
        if state.last_spoken_ts > current + 60.0:
            state.last_spoken_ts = 0.0
        return state

    def to_prompt_context(self) -> str:
        """Context ngắn để nhét vào prompt nếu cần.

        Không mô tả như cảm xúc người thật. Chỉ là áp lực nhịp hành vi.
        """
        return (
            "action_dynamics: "
            f"phase={self.rhythm_phase}; "
            f"recent_actions={','.join(self.recent_actions(5)) or 'none'}; "
            f"fatigue={self.social_fatigue:.2f}; "
            f"boredom={self.boredom_pressure:.2f}; "
            f"recoil={self.exposure_recoil:.2f}; "
            f"continuity={self.continuity_pressure:.2f}."
        )


def build_action_family(*, proposed_intent: str = "", speech_act: str = "", notes: Iterable[str] = ()) -> str:
    """Suy family ngắn để memory/repetition dùng.

    Ưu tiên intent vì intent thường gần semantic hơn speech_act.
    """
    intent = str(proposed_intent or "").strip().lower()
    if intent:
        return intent
    for note in notes or ():
        text = str(note or "")
        if text.startswith("meaning_family="):
            return text.split("=", 1)[1].strip().lower()
        if text.startswith("semantic_family="):
            return text.split("=", 1)[1].strip().lower()
    return str(speech_act or "").strip().lower()


def _float(value: Any, default: float = 0.0) -> float:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return default
    if numeric != numeric:  # NaN
        return default
    return numeric


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, float(value or 0.0)))
