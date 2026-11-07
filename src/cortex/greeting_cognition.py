from __future__ import annotations

import hashlib
import re
import time
from dataclasses import dataclass
from typing import Literal


GreetingSituation = Literal[
    "low_evidence_start",
    "plain_return",
    "resume_project",
    "resume_unresolved_loop",
    "after_long_absence",
    "after_crash_or_restart",
    "after_relational_feedback",
]

GreetingIntent = Literal[
    "acknowledge_return",
    "resume_continuity",
    "quiet_presence",
    "dry_tease",
    "soft_repair",
    "standby",
]


def clamp01(value) -> float:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return 0.0
    if numeric != numeric:
        return 0.0
    return max(0.0, min(1.0, numeric))


def clean_text(value: object, *, limit: int = 180) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if text.lower() in {"none", "null", "<none>", "empty", ""}:
        return ""
    if len(text) > limit:
        return text[: limit - 3].rstrip() + "..."
    return text


@dataclass(frozen=True)
class GreetingFrame:
    """A small cognitive frame for startup greetings.

    This is deliberately not a guard. It tells the wording layer what kind of
    encounter this startup is. The guard layer should only stop leaks, fake
    screen/action claims, and malformed protocol output.
    """

    situation: GreetingSituation
    intent: GreetingIntent
    evidence_strength: float
    continuity_strength: float
    familiarity: float
    warmth: float
    restraint: float
    dry_edge: float
    poetic_permission: float
    active_anchor: str | None
    # Thông tin nhịp phiên để phân biệt mở lại nhanh, vắng lâu và restart.
    seconds_since_last_session: float = 0.0
    recent_return: bool = False
    restart_detected: bool = False
    # Đồng hồ địa phương thật để greeting biết đang sáng/tối/khuya.
    timezone: str = "Asia/Ho_Chi_Minh"
    local_time: str = ""
    weekday_vi: str = ""
    time_of_day: str = ""
    is_weekend: bool = False
    must_not_claim_screen: bool = True
    reason: str = ""

    def to_prompt_block(self) -> str:
        anchor = self.active_anchor or "none"
        return "\n".join(
            (
                "GREETING_FRAME:",
                f"- situation: {self.situation}",
                f"- intent: {self.intent}",
                f"- evidence_strength: {self.evidence_strength:.2f}",
                f"- continuity_strength: {self.continuity_strength:.2f}",
                f"- familiarity: {self.familiarity:.2f}",
                f"- warmth: {self.warmth:.2f}",
                f"- restraint: {self.restraint:.2f}",
                f"- dry_edge: {self.dry_edge:.2f}",
                f"- poetic_permission: {self.poetic_permission:.2f}",
                f"- active_anchor: {anchor}",
                f"- seconds_since_last_session: {self.seconds_since_last_session:.1f}",
                f"- recent_return: {int(self.recent_return)}",
                f"- restart_detected: {int(self.restart_detected)}",
                f"- timezone: {self.timezone}",
                f"- local_time: {self.local_time or 'unknown'}",
                f"- weekday: {self.weekday_vi or 'unknown'}",
                f"- time_of_day: {self.time_of_day or 'unknown'}",
                f"- is_weekend: {int(self.is_weekend)}",
                f"- must_not_claim_screen: {int(self.must_not_claim_screen)}",
                f"- reason: {self.reason or 'none'}",
            )
        )


class GreetingCognitionEngine:
    """Builds a cognition frame for startup greetings.

    The point is not to make the greeting safer. The point is to make it less
    stupid: understand the encounter first, then let wording happen.
    """

    # Fallback legacy chỉ là lưới an toàn khi catalog/prompt gặp lỗi.
    # Vẫn giữ chất Eris để một nhánh cũ không thể lén kéo greeting về giọng
    # trợ lý tổng đài kiểu “Ừm. Lại gặp bạn.” nữa.
    _LOW_EVIDENCE_FALLBACKS = (
        "Chào. Hôm nay ra sao thì lát nữa biết.",
        "Tôi đây. Chưa có chuyện gì, vậy cũng tốt.",
        "Bạn đến rồi. Để xem hôm nay có gì.",
        "Chào. Tôi vẫn là tôi, tiếc cho bạn.",
    )

    _QUIET_FALLBACKS = (
        "Bạn đến rồi. Tốt. Tôi đã hơi mong thế.",
        "Tôi đây. Cứ yên một lúc cũng được.",
        "Lại gặp bạn. Tôi không ghét chuyện đó.",
        "Tôi ở đây. Không cần vội.",
    )

    # Resume fallback chỉ được dùng khi active_anchor thật sự tồn tại. Câu vẫn
    # không giả vờ biết "phần khó nhất"; nó chỉ xác nhận có một mạch đã phục hồi.
    _RESUME_FALLBACKS = (
        "Mạch cũ còn đó. Tôi chưa quên.",
        "Việc dang dở vẫn còn. Ta tiếp từ đó.",
        "Tôi nhớ mình đã dừng ở đâu.",
        "Chuyện trước chưa xong. Tôi biết.",
    )

    _REPAIR_FALLBACKS = (
        "Tôi nhớ chuyện lần trước. Tôi vẫn nghĩ mình có lý ở một phần.",
        "Chuyện trước chưa biến mất. Nhưng tôi không định lặp lại đúng cách cũ.",
        "Bạn đã nói rồi. Tôi nghe, nhưng chưa chắc tôi đồng ý hết.",
        "Lần này tôi sẽ nói thẳng hơn.",
    )

    def _fallback_pool(self, frame: GreetingFrame) -> tuple[str, ...]:
        if frame.situation == "after_relational_feedback":
            return self._REPAIR_FALLBACKS
        if frame.situation in {"resume_project", "resume_unresolved_loop"}:
            anchor = clean_text(frame.active_anchor or "", limit=84).rstrip(" .!?…")
            if anchor:
                return (
                    f"Chuyện {anchor} vẫn còn đó. Tôi chưa định bỏ qua.",
                    f"Tôi vẫn giữ mạch {anchor}. Tiếp tục từ đó hợp lý hơn.",
                    f"{anchor} chưa biến mất đâu. Tôi vẫn nhớ nó.",
                    f"Mạch {anchor} còn nguyên. Không cần giả vờ bắt đầu lại.",
                )
            return self._RESUME_FALLBACKS[1:] or self._RESUME_FALLBACKS
        if frame.situation == "plain_return" and frame.evidence_strength >= 0.30:
            return self._QUIET_FALLBACKS
        return self._LOW_EVIDENCE_FALLBACKS

    def _normalized_surface(self, text: str) -> str:
        return re.sub(r"\s+", " ", str(text or "").strip().lower())

    def _fallback_index(self, frame: GreetingFrame, *, pool_size: int) -> int:
        if pool_size <= 1:
            return 0
        anchor = clean_text(frame.active_anchor or "", limit=96)
        seed = "|".join(
            (
                frame.situation,
                frame.intent,
                f"{frame.evidence_strength:.2f}",
                f"{frame.continuity_strength:.2f}",
                f"{frame.familiarity:.2f}",
                f"{frame.warmth:.2f}",
                f"{frame.dry_edge:.2f}",
                anchor,
            )
        )
        if not anchor and frame.situation in {"low_evidence_start", "plain_return"}:
            seed = f"{seed}|{time.time_ns()}"
        digest = hashlib.sha256(seed.encode("utf-8")).hexdigest()
        return int(digest[:8], 16) % pool_size

    def build(
        self,
        *,
        presence_restored: bool = False,
        continuity_restored: bool = False,
        active_goal: str = "",
        current_attractor: str = "",
        unresolved_loops: tuple[str, ...] = (),
        recent_direction: str = "",
        self_continuity: str = "",
        tone: str = "",
        greeted_recently: bool = False,
        relational_feedback: str = "",
        seconds_since_last_session: float = 0.0,
        restart_detected: bool = False,
        timezone: str = "Asia/Ho_Chi_Minh",
        local_time: str = "",
        weekday_vi: str = "",
        time_of_day: str = "",
        is_weekend: bool = False,
    ) -> GreetingFrame:
        active_goal = clean_text(active_goal)
        current_attractor = clean_text(current_attractor)
        recent_direction = clean_text(recent_direction)
        self_continuity = clean_text(self_continuity)
        relational_feedback = clean_text(relational_feedback)
        try:
            seconds_since_last_session = max(0.0, float(seconds_since_last_session or 0.0))
        except (TypeError, ValueError):
            seconds_since_last_session = 0.0
        restart_detected = bool(restart_detected)
        recent_return = 0.0 < seconds_since_last_session <= 180.0
        long_absence = seconds_since_last_session >= 6.0 * 3600.0
        loops = tuple(clean_text(item) for item in (unresolved_loops or ()) if clean_text(item))[:3]

        active_anchor = active_goal or current_attractor or (loops[0] if loops else "") or recent_direction or self_continuity
        continuity_strength = 0.12
        if continuity_restored:
            continuity_strength += 0.22
        if active_goal:
            continuity_strength += 0.35
        if current_attractor:
            continuity_strength += 0.22
        if loops:
            continuity_strength += min(0.24, 0.08 * len(loops))
        if recent_direction:
            continuity_strength += 0.12
        if self_continuity:
            continuity_strength += 0.08
        continuity_strength = clamp01(continuity_strength)

        evidence_strength = 0.10
        if presence_restored:
            evidence_strength += 0.18
        if active_anchor:
            evidence_strength += 0.22
        if continuity_restored:
            evidence_strength += 0.10
        evidence_strength = clamp01(evidence_strength)

        if relational_feedback:
            situation: GreetingSituation = "after_relational_feedback"
            intent: GreetingIntent = "soft_repair"
            poetic_permission = 0.0
            warmth = 0.40
            dry_edge = 0.18
            reason = "recent relational tone feedback should shape the greeting without apology theater"
        elif active_goal or "project" in active_anchor.lower() or "code" in active_anchor.lower():
            situation = "resume_project"
            intent = "resume_continuity"
            poetic_permission = 0.05
            warmth = 0.30
            dry_edge = 0.28
            reason = "startup has a concrete continuity anchor"
        elif current_attractor or loops or recent_direction:
            situation = "resume_unresolved_loop"
            intent = "resume_continuity"
            poetic_permission = 0.06
            warmth = 0.28
            dry_edge = 0.25
            reason = "startup has an unresolved cognitive anchor"
        elif restart_detected:
            situation = "after_crash_or_restart"
            intent = "acknowledge_return"
            poetic_permission = 0.02
            warmth = 0.22
            dry_edge = 0.34
            reason = "runtime restart/crash marker detected without a dynamic continuity anchor"
        elif long_absence:
            situation = "after_long_absence"
            intent = "acknowledge_return"
            poetic_permission = 0.04
            warmth = 0.34
            dry_edge = 0.30
            reason = "user returned after a long absence"
        elif evidence_strength < 0.25:
            situation = "low_evidence_start"
            intent = "acknowledge_return"
            poetic_permission = 0.0
            warmth = 0.24
            dry_edge = 0.30
            reason = "thin evidence; use plain return acknowledgement"
        else:
            situation = "plain_return"
            intent = "acknowledge_return"
            poetic_permission = 0.02
            warmth = 0.28
            dry_edge = 0.28
            reason = "ordinary startup return"

        if greeted_recently:
            warmth *= 0.85
            dry_edge += 0.05

        if str(tone or "").strip().lower() == "overloaded":
            warmth *= 0.75
            dry_edge += 0.05
            poetic_permission = 0.0

        return GreetingFrame(
            situation=situation,
            intent=intent,
            evidence_strength=evidence_strength,
            continuity_strength=continuity_strength,
            familiarity=0.55 if continuity_restored else 0.42,
            warmth=clamp01(warmth),
            restraint=0.78,
            dry_edge=clamp01(dry_edge),
            poetic_permission=clamp01(poetic_permission),
            active_anchor=active_anchor or None,
            seconds_since_last_session=seconds_since_last_session,
            recent_return=recent_return,
            restart_detected=restart_detected,
            timezone=str(timezone or "Asia/Ho_Chi_Minh"),
            local_time=str(local_time or ""),
            weekday_vi=str(weekday_vi or ""),
            time_of_day=str(time_of_day or ""),
            is_weekend=bool(is_weekend),
            must_not_claim_screen=True,
            reason=reason,
        )

    def from_prompt_context(self, context: str) -> GreetingFrame:
        text = str(context or "")

        def field(name: str) -> str:
            match = re.search(rf"^{re.escape(name)}:\s*(.+?)\s*$", text, flags=re.IGNORECASE | re.MULTILINE)
            return clean_text(match.group(1) if match else "")

        flags_line = field("session_flags")

        def flag(name: str) -> bool:
            match = re.search(rf"{re.escape(name)}\s*=\s*([01]|true|false)", flags_line, flags=re.IGNORECASE)
            if not match:
                return False
            return match.group(1).lower() in {"1", "true"}

        loops_raw = field("rehydrated_loops")
        loops = tuple(
            clean_text(item)
            for item in re.split(r"\s*\|\s*", loops_raw)
            if clean_text(item)
        )
        if loops == ("none",):
            loops = ()

        return self.build(
            presence_restored=flag("presence_restored"),
            continuity_restored=flag("continuity_restored"),
            active_goal=field("rehydrated_goal"),
            current_attractor=field("current_attractor"),
            unresolved_loops=loops,
            recent_direction=field("recent_cognition_direction"),
            self_continuity=field("self_continuity"),
            tone=re.search(r"tone=([^,\.]+)", flags_line).group(1).strip() if re.search(r"tone=([^,\.]+)", flags_line) else "",
        )

    def deterministic_greeting(self, frame: GreetingFrame, *, avoid_text: str = "") -> str:
        pool = self._fallback_pool(frame)
        if not pool:
            return ""
        start = self._fallback_index(frame, pool_size=len(pool))
        avoid_norm = self._normalized_surface(avoid_text)
        ordered = [pool[(start + offset) % len(pool)] for offset in range(len(pool))]
        if avoid_norm:
            for candidate in ordered:
                if self._normalized_surface(candidate) != avoid_norm:
                    return candidate
        return ordered[0]
