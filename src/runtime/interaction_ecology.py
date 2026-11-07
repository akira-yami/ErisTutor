# -*- coding: utf-8 -*-
"""Interaction Ecology V76 cho ERIS.

Module này trả lời một câu hỏi khác với ConversationTrajectory:

    "Nếu ERIS *có thể* nói, thì nên hiện diện theo kiểu nào?"

Trajectory quản lý *đang nói về gì*. Ecology quản lý *đang cư xử ra sao*.
Mục tiêu là chặn bệnh "mọi tò mò đều biến thành câu hỏi" và tạo chỗ cho những
hành vi giống company hơn: COMMENT, SCREEN_REACT, SHARE, STORY, MUTTER, TEASE,
SUPPORT và quan trọng nhất là SILENCE.

Các nguyên tắc V76:
- Câu hỏi là một hành vi có chi phí xã hội, không phải default.
- Mọi lời nói có ``response_expectation`` rõ: required / invited / none.
- Lặp cùng mode làm habituation tăng; mode đó tự mất điểm trong vài lượt sau.
- Public pacing là state có lịch sử. ERIS vừa tự nói mà user chưa phản hồi thì
  lượt autonomous kế tiếp phải khó hơn đáng kể, trừ khi có evidence mới thật.
- COMMENT/MUTTER/SCREEN_REACT có thể không tạo "nợ trả lời" cho user.
- Không tự suy rằng user im = ghét ERIS. Im lặng chỉ tăng interruption cost và
  giảm public budget; ý nghĩa quan hệ vẫn phải dựa trên evidence khác.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
import math
import time
from typing import Any, Mapping, Sequence


ECOLOGY_SCHEMA = "eris.interaction_ecology.v76"

RESPONSE_REQUIRED = "required"
RESPONSE_INVITED = "invited"
RESPONSE_NONE = "none"
RESPONSE_EXPECTATIONS = {RESPONSE_REQUIRED, RESPONSE_INVITED, RESPONSE_NONE}

MODE_ASK = "ask"
MODE_RECONFIRM = "reconfirm"
MODE_COMMENT = "comment"
MODE_SCREEN_REACT = "screen_react"
MODE_SHARE = "share"
MODE_STORY = "story"
MODE_CALLBACK = "callback"
MODE_MUTTER = "mutter"
MODE_TEASE = "tease"
MODE_SUPPORT = "support"
MODE_TASK = "task"
MODE_REPAIR = "repair"
MODE_SILENCE = "silence"
MODE_BODY_ONLY = "body_only"

ALL_MODES = {
    MODE_ASK, MODE_RECONFIRM, MODE_COMMENT, MODE_SCREEN_REACT, MODE_SHARE,
    MODE_STORY, MODE_CALLBACK, MODE_MUTTER, MODE_TEASE, MODE_SUPPORT,
    MODE_TASK, MODE_REPAIR, MODE_SILENCE, MODE_BODY_ONLY,
}

# Speech act đã có trong codebase. Mapping này cố ý bảo thủ: act lạ -> SHARE/COMMENT,
# không mặc định biến act lạ thành ASK chỉ vì có một dấu hỏi trong câu wording.
_ASK_ACTS = {
    "user_probe", "ask_genuine_question", "question", "clarifying_question",
    "clarification_question", "casual_curiosity", "followup_question",
    "relationship_continuity_bid", "mutual_understanding_bid", "social_check_in",
    "curiosity_bid", "contextual_question",
}
_RECONFIRM_ACTS = {"memory_reconfirm", "reconfirm", "soft_reconfirm", "temporal_reconfirm"}
_SCREEN_ACTS = {"comment_on_grounded_screen", "environment_help_offer", "screen_comment", "respond_to_semantic_scene"}
_COMMENT_ACTS = {"companion_commentary", "ambient_comment", "contextual_comment", "presence"}
_SHARE_ACTS = {
    "grounded_self_disclosure", "share_self_originated_thought", "self_clarification",
    "share_reaction", "share_opinion", "share_observation", "share_intention", "share_preference",
}
_STORY_ACTS = {"story", "story_fragment", "narrative_fragment", "anecdote"}
_CALLBACK_ACTS = {"memory_callback", "shared_history_callback", "callback"}
_MUTTER_ACTS = {"mutter", "ambient_mutter", "small_mutter", "micro_presence", "contextual_small_contact"}
_TEASE_ACTS = {"tease", "playful_prompt", "dry_tease", "soft_tease", "playful_observation"}
_SUPPORT_ACTS = {"support", "reassure", "comfort", "encourage", "care_comment"}
_TASK_ACTS = {"resume_shared_goal", "offer_result_for_recognition", "capability_result", "task_update"}
_REPAIR_ACTS = {"causal_repair", "repair", "clarify", "clarification", "self_clarification"}


def _clamp01(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = default
    if not math.isfinite(number):
        number = default
    return max(0.0, min(1.0, number))


def _compact(value: Any, limit: int = 240) -> str:
    text = " ".join(str(value or "").split()).strip()
    return text if len(text) <= limit else text[: max(0, limit - 1)].rstrip() + "…"


def classify_interaction_mode(
    *,
    speech_act: Any = "",
    candidate_kind: Any = "",
    action_kind: Any = "speak",
    text: Any = "",
) -> str:
    """Phân loại một candidate thành mode hành vi ổn định.

    Không dùng punctuation làm nguồn chân lý chính. Surface có thể viết câu hỏi tu từ;
    semantic speech_act mới là tín hiệu mạnh hơn.
    """
    action = str(action_kind or "").strip().lower()
    if action == "silence":
        return MODE_SILENCE
    if action == "body_only":
        return MODE_BODY_ONLY

    act = str(speech_act or "").strip().lower()
    kind = str(candidate_kind or "").strip().lower()
    if act in _REPAIR_ACTS or kind == "conversation_repair":
        return MODE_REPAIR
    if act in _TASK_ACTS or kind in {"followup_reminder", "capability_result", "task"}:
        return MODE_TASK
    if act in _RECONFIRM_ACTS or kind == "memory_reconfirm":
        return MODE_RECONFIRM
    if act in _SCREEN_ACTS or kind in {"screen_comment", "semantic_scene", "environment"}:
        return MODE_SCREEN_REACT
    if act in _STORY_ACTS or kind in {"story", "story_fragment"}:
        return MODE_STORY
    if act in _CALLBACK_ACTS or kind in {"memory_callback", "callback"}:
        return MODE_CALLBACK
    if act in _MUTTER_ACTS or kind in {"mutter", "ambient_mutter", "micro_presence"}:
        return MODE_MUTTER
    if act in _TEASE_ACTS or kind in {"tease", "playful_prompt"}:
        return MODE_TEASE
    if act in _SUPPORT_ACTS or kind in {"support", "reassure", "comfort"}:
        return MODE_SUPPORT
    if act in _ASK_ACTS or kind in {"user_probe", "social_check_in", "casual_curiosity"}:
        return MODE_ASK
    if act in _SHARE_ACTS or kind in {"inner_thought", "self_expression", "random_chat"}:
        return MODE_SHARE
    if act in _COMMENT_ACTS or kind in {"comment", "commentary", "presence"}:
        return MODE_COMMENT

    # Fallback rất nhẹ. Chỉ dùng dấu hỏi nếu semantic layer không cho gì cả.
    clean = _compact(text, 1000)
    if clean.rstrip().endswith("?"):
        return MODE_ASK
    return MODE_COMMENT


def response_expectation_for_mode(mode: str, *, speech_act: str = "") -> str:
    """Gán mức nợ phản hồi mặc định cho mode.

    REQUIRED dành cho câu hỏi thực sự cần dữ liệu để tiếp tục task/clarification.
    ASK social bình thường chỉ INVITED: user được quyền không trả lời mà autonomous
    không coi đó là một lỗi giao thức.
    """
    mode = str(mode or "").strip().lower()
    act = str(speech_act or "").strip().lower()
    if mode == MODE_REPAIR and act in {"clarifying_question", "clarification_question"}:
        return RESPONSE_REQUIRED
    if mode == MODE_TASK and act in {"clarifying_question", "ask_required_input", "request_required_input"}:
        return RESPONSE_REQUIRED
    if mode in {MODE_ASK, MODE_RECONFIRM}:
        return RESPONSE_INVITED
    return RESPONSE_NONE


@dataclass(slots=True)
class InteractionEcologyState:
    revision: int = 0
    updated_at: float = 0.0
    last_user_at: float = 0.0
    last_public_at: float = 0.0
    recent_modes: tuple[str, ...] = ()
    recent_expectations: tuple[str, ...] = ()
    question_fatigue: float = 0.0
    public_fatigue: float = 0.0
    ambient_appetite: float = 0.25
    interruption_pressure: float = 0.0
    unsolicited_streak: int = 0
    questions_since_user_contribution: int = 0
    public_since_user_contribution: int = 0
    mode_habituation: dict[str, float] = field(default_factory=dict)
    last_mode: str = ""
    last_expectation: str = RESPONSE_NONE
    last_outcome: str = ""

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["schema"] = ECOLOGY_SCHEMA
        payload["question_fatigue"] = round(_clamp01(self.question_fatigue), 4)
        payload["public_fatigue"] = round(_clamp01(self.public_fatigue), 4)
        payload["ambient_appetite"] = round(_clamp01(self.ambient_appetite), 4)
        payload["interruption_pressure"] = round(_clamp01(self.interruption_pressure), 4)
        payload["mode_habituation"] = {
            str(k): round(_clamp01(v), 4) for k, v in self.mode_habituation.items()
        }
        return payload

    @classmethod
    def from_dict(cls, data: Mapping[str, Any] | None) -> "InteractionEcologyState":
        if not isinstance(data, Mapping):
            return cls(updated_at=time.time())
        return cls(
            revision=max(0, int(data.get("revision", 0) or 0)),
            updated_at=float(data.get("updated_at", 0.0) or 0.0),
            last_user_at=float(data.get("last_user_at", 0.0) or 0.0),
            last_public_at=float(data.get("last_public_at", 0.0) or 0.0),
            recent_modes=tuple(str(x) for x in (data.get("recent_modes", ()) or ()) if str(x))[-16:],
            recent_expectations=tuple(str(x) for x in (data.get("recent_expectations", ()) or ()) if str(x))[-16:],
            question_fatigue=_clamp01(data.get("question_fatigue", 0.0)),
            public_fatigue=_clamp01(data.get("public_fatigue", 0.0)),
            ambient_appetite=_clamp01(data.get("ambient_appetite", 0.25), 0.25),
            interruption_pressure=_clamp01(data.get("interruption_pressure", 0.0)),
            unsolicited_streak=max(0, int(data.get("unsolicited_streak", 0) or 0)),
            questions_since_user_contribution=max(0, int(data.get("questions_since_user_contribution", 0) or 0)),
            public_since_user_contribution=max(0, int(data.get("public_since_user_contribution", 0) or 0)),
            mode_habituation={
                str(k): _clamp01(v) for k, v in dict(data.get("mode_habituation", {}) or {}).items()
                if str(k) in ALL_MODES
            },
            last_mode=str(data.get("last_mode", "") or ""),
            last_expectation=str(data.get("last_expectation", RESPONSE_NONE) or RESPONSE_NONE),
            last_outcome=str(data.get("last_outcome", "") or ""),
        )


@dataclass(frozen=True, slots=True)
class EcologyCandidateBias:
    mode: str
    response_expectation: str
    multiplier: float = 1.0
    hard_suppress: bool = False
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "response_expectation": self.response_expectation,
            "multiplier": round(float(self.multiplier), 4),
            "hard_suppress": bool(self.hard_suppress),
            "reason": self.reason,
        }


class InteractionEcologyRuntime:
    """State machine ngắn hạn cho nhịp hiện diện công khai."""

    def __init__(self, state: InteractionEcologyState | None = None, *, logger: Any = None) -> None:
        self.state = state or InteractionEcologyState(updated_at=time.time())
        self.logger = logger

    def decay(self, *, now: float | None = None) -> InteractionEcologyState:
        current = float(now or time.time())
        state = self.state
        if state.updated_at <= 0.0:
            state.updated_at = current
            return state
        dt = max(0.0, current - state.updated_at)
        if dt <= 0.0:
            return state

        # Fatigue xã hội nguội theo phút, không reset theo frame.
        q_decay = 0.5 ** (dt / 420.0)
        p_decay = 0.5 ** (dt / 300.0)
        h_decay = 0.5 ** (dt / 600.0)
        state.question_fatigue = _clamp01(state.question_fatigue * q_decay)
        state.public_fatigue = _clamp01(state.public_fatigue * p_decay)
        state.interruption_pressure = _clamp01(state.interruption_pressure * p_decay)
        for key in list(state.mode_habituation):
            value = _clamp01(state.mode_habituation[key] * h_decay)
            if value < 0.015:
                state.mode_habituation.pop(key, None)
            else:
                state.mode_habituation[key] = value

        # Im lâu làm appetite cho một micro-presence tăng rất chậm. Appetite chỉ
        # là bias cho COMMENT/MUTTER, tuyệt đối không phải giấy phép tự nói.
        since_public = current - state.last_public_at if state.last_public_at > 0 else 999999.0
        if since_public > 180.0:
            state.ambient_appetite = _clamp01(state.ambient_appetite + min(0.10, dt / 3600.0))
        state.updated_at = current
        return state

    def observe_user_turn(self, *, now: float | None = None, outcome: str = "engaged") -> InteractionEcologyState:
        current = float(now or time.time())
        state = self.decay(now=current)
        state.revision += 1
        state.last_user_at = current
        state.questions_since_user_contribution = 0
        state.public_since_user_contribution = 0
        state.unsolicited_streak = 0
        state.interruption_pressure *= 0.35
        state.public_fatigue *= 0.68
        state.question_fatigue *= 0.74
        state.last_outcome = str(outcome or "engaged")
        state.updated_at = current
        return state

    def observe_public_action(
        self,
        *,
        mode: str,
        response_expectation: str = RESPONSE_NONE,
        autonomous: bool = False,
        now: float | None = None,
    ) -> InteractionEcologyState:
        current = float(now or time.time())
        state = self.decay(now=current)
        mode = mode if mode in ALL_MODES else MODE_COMMENT
        expectation = response_expectation if response_expectation in RESPONSE_EXPECTATIONS else RESPONSE_NONE
        state.revision += 1
        state.last_public_at = current
        state.last_mode = mode
        state.last_expectation = expectation
        state.recent_modes = (*state.recent_modes, mode)[-16:]
        state.recent_expectations = (*state.recent_expectations, expectation)[-16:]
        state.mode_habituation[mode] = _clamp01(state.mode_habituation.get(mode, 0.0) + 0.22)
        state.public_since_user_contribution += 1
        if autonomous:
            state.unsolicited_streak += 1
        else:
            state.unsolicited_streak = max(0, state.unsolicited_streak - 1)

        if mode in {MODE_ASK, MODE_RECONFIRM}:
            state.questions_since_user_contribution += 1
            state.question_fatigue = _clamp01(state.question_fatigue + (0.28 if mode == MODE_ASK else 0.16))
        elif mode in {MODE_COMMENT, MODE_MUTTER, MODE_SCREEN_REACT, MODE_SILENCE, MODE_BODY_ONLY}:
            state.question_fatigue = _clamp01(state.question_fatigue * 0.92)

        if mode not in {MODE_SILENCE, MODE_BODY_ONLY}:
            state.public_fatigue = _clamp01(state.public_fatigue + (0.16 if autonomous else 0.08))
            state.ambient_appetite = _clamp01(state.ambient_appetite * 0.62)
        if expectation != RESPONSE_NONE:
            state.interruption_pressure = _clamp01(state.interruption_pressure + 0.18)
        state.updated_at = current
        return state

    def bias_candidate(
        self,
        *,
        speech_act: str = "",
        candidate_kind: str = "",
        action_kind: str = "speak",
        text: str = "",
        autonomous: bool = True,
        seconds_since_user: float = 999999.0,
        grounded_environment_novelty: float = 0.0,
        plan_preferred_modes: Sequence[str] = (),
        plan_avoid_modes: Sequence[str] = (),
        response_expectation: str = "",
    ) -> EcologyCandidateBias:
        state = self.decay()
        mode = classify_interaction_mode(
            speech_act=speech_act,
            candidate_kind=candidate_kind,
            action_kind=action_kind,
            text=text,
        )
        expectation = response_expectation or response_expectation_for_mode(mode, speech_act=speech_act)
        if expectation not in RESPONSE_EXPECTATIONS:
            expectation = RESPONSE_NONE

        if mode in {MODE_SILENCE, MODE_BODY_ONLY}:
            # Silence được thưởng khi public fatigue/question fatigue cao. Không hard
            # force vì task/repair vẫn có thể cần nói.
            boost = 1.0 + 0.45 * max(state.public_fatigue, state.question_fatigue)
            return EcologyCandidateBias(mode, RESPONSE_NONE, min(1.55, boost), False, "silence_absorbs_public_fatigue")

        multiplier = 1.0
        reasons: list[str] = []
        habituation = _clamp01(state.mode_habituation.get(mode, 0.0))
        if habituation > 0.0:
            multiplier *= max(0.28, 1.0 - 0.62 * habituation)
            reasons.append(f"habituation={habituation:.2f}")

        # Question fatigue: sau hai câu hỏi mà user chưa đóng góp gì mới, hỏi tiếp
        # gần như mất quyền thắng trừ task/repair rõ ràng.
        if mode == MODE_ASK:
            multiplier *= max(0.12, 1.0 - 0.82 * state.question_fatigue)
            if state.questions_since_user_contribution >= 2:
                multiplier *= 0.22
                reasons.append("consecutive_questions")
        elif mode == MODE_RECONFIRM:
            multiplier *= max(0.34, 1.0 - 0.58 * state.question_fatigue)

        # Public pacing: autonomous liên tục mà user chưa có turn mới thì càng nói
        # càng đắt. SCREEN_REACT có evidence mới được nới một chút.
        if autonomous:
            streak_penalty = max(0.18, 1.0 - 0.28 * state.unsolicited_streak)
            if mode == MODE_SCREEN_REACT and _clamp01(grounded_environment_novelty) >= 0.65:
                streak_penalty = max(streak_penalty, 0.72)
            multiplier *= streak_penalty
            if state.unsolicited_streak:
                reasons.append(f"unsolicited_streak={state.unsolicited_streak}")

        # Nếu vừa user nói, đáp trả trực tiếp không bị policy ambient chặn. Nếu user
        # im lâu, micro-presence được tăng nhẹ nhưng câu hỏi không được tăng theo.
        since_user = max(0.0, float(seconds_since_user or 0.0))
        if autonomous and since_user > 120.0 and mode in {MODE_COMMENT, MODE_MUTTER, MODE_SCREEN_REACT}:
            multiplier *= 1.0 + 0.24 * state.ambient_appetite
            reasons.append("ambient_opportunity")

        if mode in set(str(x) for x in plan_preferred_modes):
            multiplier *= 1.28
            reasons.append("relational_plan_prefers")
        if mode in set(str(x) for x in plan_avoid_modes):
            multiplier *= 0.28
            reasons.append("relational_plan_avoids")

        # Một lời nói đòi phản hồi là đắt hơn lời nói không tạo debt. Đây chỉ là
        # cost, không phủ quyết required input của task.
        if expectation == RESPONSE_INVITED:
            multiplier *= 0.86
        elif expectation == RESPONSE_REQUIRED and mode not in {MODE_TASK, MODE_REPAIR}:
            multiplier *= 0.58

        # Public fatigue cực cao + không task/repair + không evidence mới: hard
        # suppress autonomous speech. Tầng Neuro vẫn có silence/body candidate.
        hard = bool(
            autonomous
            and state.public_fatigue >= 0.88
            and mode not in {MODE_TASK, MODE_REPAIR}
            and not (mode == MODE_SCREEN_REACT and _clamp01(grounded_environment_novelty) >= 0.72)
        )
        if hard:
            multiplier = min(multiplier, 0.08)
            reasons.append("public_fatigue_hard_cap")

        return EcologyCandidateBias(
            mode=mode,
            response_expectation=expectation,
            multiplier=max(0.04, min(1.65, multiplier)),
            hard_suppress=hard,
            reason=",".join(reasons) or "ecology_neutral",
        )

    def projection(self, *, now: float | None = None) -> dict[str, Any]:
        state = self.decay(now=now)
        recent = state.recent_modes[-8:]
        ask_ratio = sum(1 for x in recent if x in {MODE_ASK, MODE_RECONFIRM}) / max(1, len(recent))
        recommended: list[str] = []
        if state.question_fatigue >= 0.45 or ask_ratio >= 0.40:
            recommended.extend([MODE_COMMENT, MODE_MUTTER, MODE_SCREEN_REACT, MODE_SILENCE])
        if state.public_fatigue >= 0.65:
            recommended.extend([MODE_SILENCE, MODE_BODY_ONLY])
        if not recommended:
            recommended.extend([MODE_COMMENT, MODE_SHARE, MODE_SCREEN_REACT, MODE_SILENCE])
        return {
            **state.to_dict(),
            "ask_ratio_recent": round(ask_ratio, 4),
            "recommended_modes": list(dict.fromkeys(recommended))[:6],
            "rule": (
                "Question is not the default interaction. Prefer a mode that fits the current plan, "
                "recent behavior and grounded opportunity. response_expectation=none creates no user reply debt."
            ),
        }


__all__ = [
    "ECOLOGY_SCHEMA", "RESPONSE_REQUIRED", "RESPONSE_INVITED", "RESPONSE_NONE",
    "MODE_ASK", "MODE_RECONFIRM", "MODE_COMMENT", "MODE_SCREEN_REACT", "MODE_SHARE",
    "MODE_STORY", "MODE_CALLBACK", "MODE_MUTTER", "MODE_TEASE", "MODE_SUPPORT",
    "MODE_TASK", "MODE_REPAIR", "MODE_SILENCE", "MODE_BODY_ONLY", "ALL_MODES",
    "InteractionEcologyState", "EcologyCandidateBias", "InteractionEcologyRuntime",
    "classify_interaction_mode", "response_expectation_for_mode",
]
