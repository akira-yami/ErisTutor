# -*- coding: utf-8 -*-
"""body_choreography.py — timeline thân thể cho model 2D không có mặt.

ERIS hiện không phải Live2D có mặt/miệng/mắt chi tiết.  Vì vậy các cue kiểu
``look_away``, ``blink_slow`` hay ``audible_laugh_soft`` không nên bị hiểu là
"đổi mặt"; chúng cần được dịch sang động tác thân hiện có: hop, stand, read,
sad, sit...

Module này đứng sau PerformanceAct:

    performance_act + motion/actions + tts_text
    -> body_timeline
    -> Godot phát chuỗi MotionCommand theo mốc thời gian

Timeline cố ý JSON rất đơn giản để Godot body cũ nhận được mà không cần biết
gì về cognition hay TTS nội bộ.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


_VALID_BODY_ANIMS = {
    "stand",
    "sit",
    "read",
    "stand_walk",
    "hop",
    "jump",
    "sleep",
    "sad",
    "food",
    "affection",
    "clone",
    "divine",
    "idle",
}


@dataclass(frozen=True, slots=True)
class BodyTimelineEvent:
    at_ms: int
    name: str
    duration_ms: int = 0
    kind: str = "motion"
    intent: str = "pose"
    facing: int = 0
    target_x: float | None = None

    def to_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "at_ms": max(0, int(self.at_ms)),
            "kind": self.kind or "motion",
            "name": _safe_anim(self.name),
            "duration_ms": max(0, int(self.duration_ms)),
            "intent": self.intent or "pose",
            "facing": -1 if self.facing < 0 else 1 if self.facing > 0 else 0,
        }
        if self.target_x is not None:
            payload["target_x"] = max(0.0, min(1.0, float(self.target_x)))
        return payload


@dataclass(frozen=True, slots=True)
class BodyTimeline:
    turn_id: str
    duration_ms: int
    source: str
    events: tuple[BodyTimelineEvent, ...]

    def to_payload(self) -> dict[str, Any]:
        return {
            "turn_id": self.turn_id,
            "duration_ms": max(0, int(self.duration_ms)),
            "source": self.source,
            "events": [event.to_payload() for event in self.events],
        }


def _safe_anim(name: str) -> str:
    value = str(name or "").strip()
    return value if value in _VALID_BODY_ANIMS else "stand"


def _estimate_duration_ms(text: str) -> int:
    """Ước lượng cửa sổ diễn theo độ dài câu.

    Đây không phải timing TTS chính xác.  Nó chỉ giữ pose đủ lâu để câu nói có
    nhịp, đồng thời tự kết thúc để body không bị kẹt ở pose "sad/read".
    """

    length = len(str(text or "").strip())
    return max(900, min(4200, 520 + length * 42))


def _act_kind(delivery: dict[str, Any]) -> str:
    act = delivery.get("performance_act") if isinstance(delivery, dict) else None
    if not isinstance(act, dict):
        return ""
    return str(act.get("kind", "") or "").strip()


def _has_any(values: tuple[str, ...], *needles: str) -> bool:
    joined = " ".join(str(v or "") for v in values).lower()
    return any(needle in joined for needle in needles)


class BodyChoreographyPlanner:
    """Map stage/performance cue sang chuỗi animation thân thể.

    Quy tắc quan trọng: chỉ tạo timeline khi có tín hiệu đủ mạnh.  Câu trả lời
    thường không nên làm body giật liên tục; nếu không có performance act hoặc
    cue rõ, để autonomy local tự chạy sẽ tự nhiên hơn.
    """

    def plan(
        self,
        *,
        turn_id: str,
        text: str,
        motion: str = "",
        actions: tuple[str, ...] = (),
        delivery: dict[str, Any] | None = None,
    ) -> BodyTimeline | None:
        delivery = delivery or {}
        kind = _act_kind(delivery)
        duration = _estimate_duration_ms(text)
        motion_key = str(motion or "").strip().lower()
        action_values = tuple(str(action or "").strip().lower() for action in actions)

        events = self._events_for_performance(kind, duration)
        source = f"performance_act:{kind}" if kind else ""

        if not events:
            events = self._events_for_surface_cue(motion_key, action_values, duration)
            if events:
                source = "stage_cue"

        if not events:
            return None

        return BodyTimeline(
            turn_id=str(turn_id),
            duration_ms=duration,
            source=source,
            events=tuple(events),
        )

    def _events_for_performance(self, kind: str, duration: int) -> list[BodyTimelineEvent]:
        """Các màn nhỏ đã biết.

        Vì body không có mặt, "cười khẽ/châm chọc" = hop rất ngắn rồi quay đi;
        "dỗi/khóc giả" = sad/read; "thở dài" = sad/sit.  Tất cả đều quay về
        stand trước khi timeline hết để tránh kẹt pose.
        """

        end = max(700, duration - 220)
        match kind:
            case "playful_laugh":
                return [
                    BodyTimelineEvent(0, "hop", 260, facing=0),
                    BodyTimelineEvent(320, "stand", 520, intent="face", facing=-1),
                    BodyTimelineEvent(end, "stand", 220, intent="face", facing=0),
                ]
            case "requested_tease":
                return [
                    BodyTimelineEvent(0, "hop", 220, facing=0),
                    BodyTimelineEvent(260, "stand", 520, intent="face", facing=-1),
                    BodyTimelineEvent(900, "stand", 420, intent="face", facing=0),
                ]
            case "fake_annoyed_deadpan":
                return [
                    BodyTimelineEvent(0, "stand", 420, intent="face", facing=-1),
                    BodyTimelineEvent(560, "read", 680, intent="pose", facing=-1),
                    BodyTimelineEvent(end, "stand", 220, intent="face", facing=0),
                ]
            case "fake_sulk":
                return [
                    BodyTimelineEvent(0, "sad", 760, intent="pose", facing=-1),
                    BodyTimelineEvent(900, "read", 650, intent="pose", facing=-1),
                    BodyTimelineEvent(end, "stand", 220, intent="face", facing=0),
                ]
            case "soft_sigh":
                return [
                    BodyTimelineEvent(0, "sad", 820, intent="pose", facing=0),
                    BodyTimelineEvent(980, "sit", 900, intent="pose", facing=0),
                    BodyTimelineEvent(end, "stand", 220, intent="pose", facing=0),
                ]
            case "fake_cry_refusal":
                return [
                    BodyTimelineEvent(0, "sad", 920, intent="pose", facing=0),
                    BodyTimelineEvent(1100, "stand", 620, intent="face", facing=-1),
                    BodyTimelineEvent(end, "stand", 220, intent="face", facing=0),
                ]
            case "curious_hm":
                return [
                    BodyTimelineEvent(0, "stand", 280, intent="face", facing=1),
                    BodyTimelineEvent(380, "read", 560, intent="pose", facing=1),
                    BodyTimelineEvent(end, "stand", 220, intent="pose", facing=0),
                ]
            case _:
                return []

    def _events_for_surface_cue(
        self,
        motion: str,
        actions: tuple[str, ...],
        duration: int,
    ) -> list[BodyTimelineEvent]:
        end = max(650, duration - 180)
        values = (motion, *actions)
        # Cười thành tiếng mới đáng hop. ``small_smile`` là một dư âm nhẹ; map
        # nó sang hop khiến Eris nhảy lên vì vừa hiểu một fact, rất mascot và
        # hoàn toàn phá chất điềm của nhân vật.
        if _has_any(values, "audible_laugh", "laugh_soft", "playful_laugh"):
            return [
                BodyTimelineEvent(0, "hop", 220, intent="pose"),
                BodyTimelineEvent(360, "stand", 360, intent="face", facing=-1),
                BodyTimelineEvent(end, "stand", 180, intent="face", facing=0),
            ]
        if _has_any(values, "recognition", "noticed", "realization"):
            return [
                BodyTimelineEvent(0, "stand", 300, intent="face", facing=1),
                BodyTimelineEvent(360, "read", 520, intent="pose", facing=1),
                BodyTimelineEvent(end, "stand", 180, intent="face", facing=0),
            ]
        if _has_any(values, "belief_update", "contrast", "old_assumption"):
            return [
                BodyTimelineEvent(0, "stand", 420, intent="face", facing=-1),
                BodyTimelineEvent(480, "read", 460, intent="pose", facing=-1),
                BodyTimelineEvent(end, "stand", 180, intent="face", facing=0),
            ]
        if _has_any(values, "assimilate", "reframe", "curious_explain", "read"):
            return [
                BodyTimelineEvent(0, "read", 680, intent="pose", facing=1),
                BodyTimelineEvent(max(760, end - 380), "stand", 320, intent="face", facing=1),
                BodyTimelineEvent(end, "stand", 180, intent="face", facing=0),
            ]
        if _has_any(values, "small_smile", "afterthought", "soft_near", "lean_in_small", "settle"):
            return [
                BodyTimelineEvent(0, "stand", 520, intent="face", facing=1),
                BodyTimelineEvent(end, "stand", 180, intent="face", facing=0),
            ]
        if _has_any(values, "look_away", "scoff", "blink_slow"):
            return [
                BodyTimelineEvent(0, "stand", 450, intent="face", facing=-1),
                BodyTimelineEvent(end, "stand", 180, intent="face", facing=0),
            ]
        if _has_any(values, "look_down", "sigh"):
            return [
                BodyTimelineEvent(0, "sad", 720, intent="pose"),
                BodyTimelineEvent(end, "stand", 180, intent="pose"),
            ]
        if _has_any(values, "tilt_head", "curious"):
            return [
                BodyTimelineEvent(0, "read", 520, intent="pose", facing=1),
                BodyTimelineEvent(end, "stand", 180, intent="pose"),
            ]
        return []


__all__ = [
    "BodyChoreographyPlanner",
    "BodyTimeline",
    "BodyTimelineEvent",
]
