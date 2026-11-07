# -*- coding: utf-8 -*-
"""Discourse baton dùng chung cho chat và self-initiated turn.

Post-turn observer là semantic authority duy nhất. Runtime không nhìn dấu hỏi,
không dò "thôi/ok", và không cộng điểm bằng vài keyword rồi tự phong rằng một
thread còn mở. Timer chỉ mô tả độ tươi cơ học của kết luận semantic đã có.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from .semantic_contracts import DiscourseFrame, compact


@dataclass(frozen=True, slots=True)
class DiscourseState:
    mode: str = "ambient"
    last_role: str = ""
    last_public_excerpt: str = ""
    seconds_since_user: float = 0.0
    seconds_since_assistant: float = 0.0
    thread_fresh: bool = False
    topic_closed: bool = False
    reply_expected: bool = False
    assistant_may_continue: bool = False
    # Follow-up appraisal là trạng thái tạm thời, không phải relationship lâu dài.
    # Nó cho phép nhắc đúng một lần rồi rút khỏi thread nếu user vẫn im.
    followup_state: str = "none"
    followup_reminder_due: bool = False
    followup_reminder_count: int = 0
    followup_abandoned: bool = False
    suppress_same_topic: bool = False
    may_start_new_topic: bool = False
    social_distance: float = 0.0
    unresolved_point: str = ""
    thread_key: str = "unknown"
    open_loops: tuple[str, ...] = ()
    confidence: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "last_role": self.last_role,
            "last_public_excerpt": self.last_public_excerpt,
            "seconds_since_user": round(self.seconds_since_user, 2),
            "seconds_since_assistant": round(self.seconds_since_assistant, 2),
            "thread_fresh": self.thread_fresh,
            "topic_closed": self.topic_closed,
            "reply_expected": self.reply_expected,
            "assistant_may_continue": self.assistant_may_continue,
            "followup_state": self.followup_state,
            "followup_reminder_due": self.followup_reminder_due,
            "followup_reminder_count": self.followup_reminder_count,
            "followup_abandoned": self.followup_abandoned,
            "suppress_same_topic": self.suppress_same_topic,
            "may_start_new_topic": self.may_start_new_topic,
            "social_distance": round(self.social_distance, 3),
            "unresolved_point": self.unresolved_point,
            "thread_key": self.thread_key,
            "open_loops": list(self.open_loops),
            "confidence": round(self.confidence, 3),
            "instruction": (
                "This frame came from the post-turn semantic observer. Do not reclassify it from punctuation, "
                "keywords, or recency. waiting_user/reply_expected means ERIS must not nag or repeat the same question; it does not block an independent self-owned expression after rhythm/collision checks. "
                "followup_reminder_due permits one face-saving reminder; followup_abandoned closes the old topic "
                "and permits one slightly reserved topic shift without blaming the user."
            ),
        }


class DiscourseStateBuilder:
    """Chuyển observer frame thành baton cơ học, không tự phân loại ngôn ngữ."""

    def build(
        self,
        history: Sequence[Mapping[str, Any]],
        *,
        open_loops: Sequence[Any] = (),
        latest_observation: Mapping[str, Any] | None = None,
        seconds_since_user: float = 0.0,
        seconds_since_assistant: float = 0.0,
        freshness_seconds: float = 420.0,
    ) -> DiscourseState:
        turns = [item for item in history if isinstance(item, Mapping) and str(item.get("content", "")).strip()]
        last = turns[-1] if turns else {}
        last_role = str(last.get("role", "") or "")
        last_text = compact(last.get("content", ""), 360)

        observation = latest_observation if isinstance(latest_observation, Mapping) else {}
        frame = DiscourseFrame.from_mapping(
            observation.get("discourse") if isinstance(observation.get("discourse"), Mapping) else {}
        )
        explicit_loops = tuple(compact(item, 180) for item in open_loops if compact(item, 180))[-5:]
        age = min(max(0.0, seconds_since_user), max(0.0, seconds_since_assistant))
        fresh = bool(turns and age <= max(30.0, float(freshness_seconds)))
        closed = frame.status == "closed"
        waiting = frame.status == "waiting_user" or frame.reply_expected
        may_continue = bool(
            frame.assistant_may_continue
            and frame.status == "open"
            and not waiting
            and not closed
            and frame.confidence > 0.0
        )
        mode = "continuation" if may_continue and fresh else "ambient"
        return DiscourseState(
            mode=mode,
            last_role=last_role,
            last_public_excerpt=last_text,
            seconds_since_user=max(0.0, seconds_since_user),
            seconds_since_assistant=max(0.0, seconds_since_assistant),
            thread_fresh=fresh,
            topic_closed=closed,
            reply_expected=waiting,
            assistant_may_continue=may_continue,
            unresolved_point=frame.unresolved_point,
            thread_key=frame.thread_key,
            open_loops=explicit_loops,
            confidence=frame.confidence,
        )


__all__ = ["DiscourseState", "DiscourseStateBuilder"]
