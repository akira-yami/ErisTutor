# -*- coding: utf-8 -*-
"""Conversation Trajectory V76 cho ERIS.

Module này giữ *quỹ đạo chủ đề* giữa chat công khai, seed worker và autonomous
cognition. Nó cố ý tách ba thứ vốn rất dễ bị trộn lẫn:

1. pending_question: ERIS đang chờ user trả lời một câu hỏi cụ thể hay không;
2. topic trajectory: semantic spine đang sống, kể cả sau khi câu hỏi đã được trả lời;
3. development policy: autonomous nên HOLD/DEEPEN/CONNECT/BRANCH hay thật sự SHIFT.

Điểm chính của V75: một user turn mới sẽ giải quyết pending question cũ nhưng KHÔNG
xóa chủ đề. Nhờ vậy state không còn giữ kiểu open-loop "user may respond..." sau
khi user đã trả lời, trong khi seed/autonomous vẫn có một anchor để tiếp tục đào sâu.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
import hashlib
import math
import re
import time
from typing import Any, Mapping, Sequence


TRAJECTORY_SCHEMA = "eris.conversation_trajectory.v76"
_TOPIC_STATES = {"active", "developing", "simmering", "satiated", "dormant", "reopened"}
_ALLOWED_DEVELOPMENT = {"hold", "deepen", "connect", "branch", "shift"}


def _compact(value: Any, limit: int = 520) -> str:
    text = " ".join(str(value or "").split()).strip()
    return text if len(text) <= limit else text[: max(0, limit - 1)].rstrip() + "…"


def _clamp01(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = default
    if not math.isfinite(number):
        number = default
    return max(0.0, min(1.0, number))


def _tokens(text: Any) -> set[str]:
    return {
        token
        for token in re.findall(r"[\w\u00c0-\u024f\u1e00-\u1eff]+", str(text or "").casefold())
        if len(token) >= 3
    }


def _similarity(left: Any, right: Any) -> float:
    a, b = _tokens(left), _tokens(right)
    if not a or not b:
        return 0.0
    return len(a & b) / max(1, len(a | b))


def _topic_id(text: str, *, salt: Any = "") -> str:
    raw = f"{_compact(text, 1200)}|{salt}|{time.time_ns()}"
    return "topic_" + hashlib.sha256(raw.encode("utf-8", errors="replace")).hexdigest()[:16]


_EXPLICIT_SHIFT_RE = re.compile(
    r"(?:\b(?:đổi|chuyển)\s+(?:chủ đề|đề tài|chuyện|cái khác)\b"
    r"|\b(?:nói|bàn)\s+(?:chuyện|cái|chủ đề)\s+khác\b"
    r"|\b(?:change|switch)\s+(?:the\s+)?topic\b|\bmove\s+on\b)",
    re.IGNORECASE,
)

_EXPLICIT_CLOSE_RE = re.compile(
    r"(?:\b(?:bỏ|cho qua|khép|dừng|thôi)\s+(?:qua\s+)?(?:vụ|chuyện|chủ đề|cái)?\s*(?:này|đó|ấy)?\b"
    r"|\bđừng nhắc(?: lại)? nữa\b|\bkhông bàn(?: nữa)?\b|\blet(?:'s| us)\s+(?:leave|drop)\s+(?:that|it)\b)",
    re.IGNORECASE,
)

_CONTINUATION_CUES = (
    "câu đó", "câu này", "lúc nãy", "vừa nãy", "ý đó", "ý này", "vậy", "thế",
    "sao em", "sao bạn", "còn em", "còn bạn", "nếu vậy", "như vậy", "ý em",
    "that question", "what you said", "you said", "then what", "in that case",
)

_QUESTION_ACTS = {
    "user_probe", "ask_genuine_question", "casual_curiosity", "followup_reminder",
    "relationship_continuity_bid", "contextual_question", "question",
}

_SHIFT_ACTS = {"independent_topic_shift", "topic_shift", "shift_topic"}


@dataclass(frozen=True, slots=True)
class ConversationTrajectory:
    revision: int = 0
    updated_at: float = 0.0
    topic_id: str = ""
    root_focus: str = ""
    current_focus: str = ""
    parent_focuses: tuple[str, ...] = ()
    last_user_text: str = ""
    last_assistant_text: str = ""
    last_user_turn_ref: str = ""
    last_assistant_turn_ref: str = ""
    last_user_turn_index: int = 0
    last_assistant_turn_index: int = 0
    depth: int = 0
    momentum: float = 0.0
    user_engagement: float = 0.0
    # V76: vitality hữu hạn. Chỉ user/evidence mới recharge mạnh; lời ERIS tự nói
    # không được tự bơm lại nhiên liệu cho topic.
    energy: float = 0.0
    lifecycle_state: str = "dormant"
    semantic_gain_history: tuple[float, ...] = ()
    private_budget: int = 0
    public_autonomous_budget: int = 0
    autonomous_public_count: int = 0
    private_development_count: int = 0
    last_external_input_at: float = 0.0
    active: bool = False
    closed: bool = False
    development_mode: str = "hold"
    pending_question_id: str = ""
    pending_question_text: str = ""
    pending_question_turn_index: int = 0
    awaiting_user_answer: bool = False
    last_resolved_question_id: str = ""
    source_refs: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": TRAJECTORY_SCHEMA,
            "revision": int(self.revision),
            "updated_at": float(self.updated_at),
            "topic_id": self.topic_id,
            "root_focus": _compact(self.root_focus, 520),
            "current_focus": _compact(self.current_focus, 720),
            "parent_focuses": [_compact(item, 360) for item in self.parent_focuses[-6:]],
            "last_user_text": _compact(self.last_user_text, 720),
            "last_assistant_text": _compact(self.last_assistant_text, 900),
            "last_user_turn_ref": self.last_user_turn_ref,
            "last_assistant_turn_ref": self.last_assistant_turn_ref,
            "last_user_turn_index": int(self.last_user_turn_index),
            "last_assistant_turn_index": int(self.last_assistant_turn_index),
            "depth": int(self.depth),
            "momentum": round(_clamp01(self.momentum), 4),
            "user_engagement": round(_clamp01(self.user_engagement), 4),
            "energy": round(max(0.0, min(1.5, float(self.energy))), 4),
            "lifecycle_state": self.lifecycle_state if self.lifecycle_state in _TOPIC_STATES else "dormant",
            "semantic_gain_history": [round(_clamp01(x), 4) for x in self.semantic_gain_history[-8:]],
            "private_budget": max(0, int(self.private_budget)),
            "public_autonomous_budget": max(0, int(self.public_autonomous_budget)),
            "autonomous_public_count": max(0, int(self.autonomous_public_count)),
            "private_development_count": max(0, int(self.private_development_count)),
            "last_external_input_at": float(self.last_external_input_at),
            "active": bool(self.active),
            "closed": bool(self.closed),
            "development_mode": self.development_mode if self.development_mode in _ALLOWED_DEVELOPMENT else "hold",
            "pending_question_id": self.pending_question_id,
            "pending_question_text": _compact(self.pending_question_text, 520),
            "pending_question_turn_index": int(self.pending_question_turn_index),
            "awaiting_user_answer": bool(self.awaiting_user_answer),
            "last_resolved_question_id": self.last_resolved_question_id,
            "source_refs": list(self.source_refs[-12:]),
        }

    def seed_context(self) -> dict[str, Any]:
        """Projection nhỏ cho associative worker, không phải prompt public."""
        if (
            not self.active or self.closed or not self.topic_id or not self.current_focus
            or self.lifecycle_state in {"satiated", "dormant"}
            or self.private_budget <= 0
            or self.energy <= 0.04
        ):
            return {}
        return {
            "schema": TRAJECTORY_SCHEMA,
            "revision": int(self.revision),
            "active": bool(self.active),
            "closed": bool(self.closed),
            "topic_id": self.topic_id,
            "root_focus": _compact(self.root_focus, 420),
            "current_focus": _compact(self.current_focus, 620),
            "parent_focuses": [_compact(item, 280) for item in self.parent_focuses[-4:]],
            "last_assistant_extension": _compact(self.last_assistant_text, 520),
            "depth": int(self.depth),
            "momentum": round(_clamp01(self.momentum), 4),
            "user_engagement": round(_clamp01(self.user_engagement), 4),
            "energy": round(max(0.0, min(1.5, float(self.energy))), 4),
            "lifecycle_state": self.lifecycle_state,
            "semantic_gain_history": [round(_clamp01(x), 4) for x in self.semantic_gain_history[-6:]],
            "private_budget": max(0, int(self.private_budget)),
            "public_autonomous_budget": max(0, int(self.public_autonomous_budget)),
            "development_mode": self.development_mode,
            "awaiting_user_answer": bool(self.awaiting_user_answer),
            "pending_question_id": self.pending_question_id,
            "source_refs": list(self.source_refs[-10:]),
            "rule": (
                "Develop this same semantic spine only while it still has energy/private budget. "
                "Prefer deepen/connect/branch over unrelated novelty. Do not treat ERIS public wording as factual evidence. "
                "A private thought is allowed to die without becoming speech."
            ),
        }


class ConversationTrajectoryRuntime:
    """Session-local topic trajectory shared by chat, seed and autonomous lanes."""

    def __init__(self, *, logger: Any = None) -> None:
        self.logger = logger
        self._state = ConversationTrajectory(updated_at=time.time())

    def snapshot(self, *, now: float | None = None) -> ConversationTrajectory:
        current = float(now if now is not None else time.time())
        state = self._state
        if not state.active or state.closed:
            return state
        age = max(0.0, current - state.updated_at)
        # Chủ đề không chết ngay chỉ vì có một khoảng im lặng. Momentum giảm chậm,
        # đủ để autonomous còn tiếp tục suy nghĩ vài phút nhưng không bám cả buổi.
        decay = 0.5 ** (age / 420.0)
        momentum = _clamp01(state.momentum * decay)
        if abs(momentum - state.momentum) > 0.015:
            state = replace(state, momentum=momentum)
            self._state = state
        return state

    def to_dict(self) -> dict[str, Any]:
        return self.snapshot().to_dict()

    def seed_context(self) -> dict[str, Any]:
        return self.snapshot().seed_context()

    def prime_from_history(
        self,
        recent_history: Sequence[Mapping[str, Any]],
        *,
        now: float | None = None,
    ) -> ConversationTrajectory:
        current = float(now if now is not None else time.time())
        user_turns = []
        assistant_turns = []
        for item in recent_history[-10:]:
            if not isinstance(item, Mapping):
                continue
            role = str(item.get("role", "") or "").casefold()
            text = _compact(item.get("content", item.get("text", "")), 900)
            if not text:
                continue
            if role in {"user", "human"}:
                user_turns.append(text)
            elif role == "assistant":
                assistant_turns.append(text)
        if not user_turns:
            return self._state
        latest = user_turns[-1]
        root = user_turns[max(0, len(user_turns) - 3)]
        self._state = ConversationTrajectory(
            revision=self._state.revision + 1,
            updated_at=current,
            topic_id=_topic_id(root, salt="startup"),
            root_focus=root,
            current_focus=latest,
            parent_focuses=tuple(user_turns[-4:-1]),
            last_user_text=latest,
            last_assistant_text=assistant_turns[-1] if assistant_turns else "",
            depth=max(0, len(user_turns[-4:]) - 1),
            momentum=0.56,
            user_engagement=0.58,
            energy=0.72,
            lifecycle_state="reopened",
            private_budget=4,
            public_autonomous_budget=1,
            last_external_input_at=current,
            active=True,
            closed=False,
            development_mode="connect" if len(user_turns) > 1 else "deepen",
            source_refs=("startup:trajectory_prime",),
        )
        self._log("startup_prime")
        return self._state

    def observe_user_turn(
        self,
        user_text: Any,
        *,
        turn_ref: str = "",
        turn_index: int = 0,
        previous_assistant_text: Any = "",
        now: float | None = None,
    ) -> ConversationTrajectory:
        current = float(now if now is not None else time.time())
        text = _compact(user_text, 1000)
        if not text:
            return self._state
        prior = self.snapshot(now=current)
        previous = _compact(previous_assistant_text or prior.last_assistant_text, 1000)
        explicit_close = bool(_EXPLICIT_CLOSE_RE.search(text))
        explicit_shift = bool(_EXPLICIT_SHIFT_RE.search(text))

        resolved_id = prior.pending_question_id if prior.awaiting_user_answer else prior.last_resolved_question_id
        refs = list(prior.source_refs)
        if turn_ref:
            refs.append(str(turn_ref))

        if explicit_close:
            self._state = replace(
                prior,
                revision=prior.revision + 1,
                updated_at=current,
                last_user_text=text,
                last_user_turn_ref=str(turn_ref or ""),
                last_user_turn_index=max(0, int(turn_index or 0)),
                current_focus=text,
                momentum=0.08,
                energy=0.0,
                lifecycle_state="dormant",
                private_budget=0,
                public_autonomous_budget=0,
                active=False,
                closed=True,
                development_mode="hold",
                awaiting_user_answer=False,
                pending_question_id="",
                pending_question_text="",
                pending_question_turn_index=0,
                last_resolved_question_id=resolved_id,
                source_refs=tuple(dict.fromkeys(refs))[-12:],
            )
            self._log("user_close")
            return self._state

        if explicit_shift or not prior.active or prior.closed or not prior.topic_id:
            topic_id = _topic_id(text, salt=turn_ref or turn_index)
            root = text
            parents: tuple[str, ...] = ()
            depth = 0
            mode = "deepen"
            momentum = 0.70
        else:
            topic_id = prior.topic_id
            root = prior.root_focus or text
            continuity = max(
                _similarity(text, prior.current_focus),
                _similarity(text, previous),
                0.62 if any(cue in text.casefold() for cue in _CONTINUATION_CUES) else 0.0,
            )
            # Low lexical overlap is often a legitimate semantic branch. Keep one
            # topic_id unless user explicitly shifts; branch is precisely what lets
            # "habit -> self -> continuity of identity" remain one conversation arc.
            mode = "deepen" if continuity >= 0.22 else "branch"
            parents_list = [*prior.parent_focuses]
            if prior.current_focus and prior.current_focus != text:
                parents_list.append(prior.current_focus)
            parents = tuple(dict.fromkeys(item for item in parents_list if item))[-6:]
            depth = min(12, prior.depth + (1 if text != prior.current_focus else 0))
            momentum = _clamp01(max(0.58, prior.momentum * 0.90) + 0.10 + 0.10 * continuity)

        engagement = _clamp01(
            0.48
            + min(0.24, len(_tokens(text)) / 70.0)
            + (0.10 if "?" in text else 0.0)
            + (0.08 if prior.awaiting_user_answer else 0.0)
        )
        # V76: chỉ external/user input recharge vitality. User tiếp tục cùng topic
        # cấp thêm energy; topic mới có pool mới. Không có "assistant nói -> energy tăng".
        if explicit_shift or not prior.active or prior.closed or not prior.topic_id:
            energy = 0.92
            lifecycle_state = "active"
        else:
            energy = min(1.50, max(0.18, prior.energy) + 0.35)
            lifecycle_state = "reopened" if prior.lifecycle_state in {"satiated", "dormant"} else "developing"
        self._state = ConversationTrajectory(
            revision=prior.revision + 1,
            updated_at=current,
            topic_id=topic_id,
            root_focus=root,
            current_focus=text,
            parent_focuses=parents,
            last_user_text=text,
            last_assistant_text=previous or prior.last_assistant_text,
            last_user_turn_ref=str(turn_ref or ""),
            last_assistant_turn_ref=prior.last_assistant_turn_ref,
            last_user_turn_index=max(0, int(turn_index or 0)),
            last_assistant_turn_index=prior.last_assistant_turn_index,
            depth=depth,
            momentum=momentum,
            user_engagement=engagement,
            energy=energy,
            lifecycle_state=lifecycle_state,
            semantic_gain_history=prior.semantic_gain_history[-4:] if topic_id == prior.topic_id else (),
            private_budget=6,
            public_autonomous_budget=2,
            autonomous_public_count=0 if topic_id != prior.topic_id else prior.autonomous_public_count,
            private_development_count=0 if topic_id != prior.topic_id else prior.private_development_count,
            last_external_input_at=current,
            active=True,
            closed=False,
            development_mode=mode,
            pending_question_id="",
            pending_question_text="",
            pending_question_turn_index=0,
            awaiting_user_answer=False,
            last_resolved_question_id=resolved_id,
            source_refs=tuple(dict.fromkeys(refs))[-12:],
        )
        self._log("user_turn")
        return self._state

    def observe_assistant_commit(
        self,
        assistant_text: Any,
        *,
        turn_ref: str = "",
        turn_index: int = 0,
        speech_act: str = "",
        response_expectation: str = "",
        autonomous: bool = False,
        now: float | None = None,
    ) -> ConversationTrajectory:
        current = float(now if now is not None else time.time())
        text = _compact(assistant_text, 1200)
        if not text:
            return self._state
        prior = self.snapshot(now=current)
        act = str(speech_act or "").strip().casefold()
        expectation = str(response_expectation or "").strip().casefold()
        asks = bool(expectation in {"required", "invited"}) if expectation else bool("?" in text or act in _QUESTION_ACTS)
        shifts = bool(act in _SHIFT_ACTS or _EXPLICIT_SHIFT_RE.search(text))
        refs = list(prior.source_refs)
        if turn_ref:
            refs.append(str(turn_ref))

        if shifts and prior.active:
            parents = tuple(dict.fromkeys((*prior.parent_focuses, prior.current_focus)))[-6:]
            next_mode = "branch"
            next_momentum = max(0.20, prior.momentum * 0.72)
        else:
            parents = prior.parent_focuses
            next_mode = "connect" if prior.active else "hold"
            # V76: assistant commit KHÔNG self-recharge momentum. Đây là sửa lỗi
            # self-sustaining loop của V75. Public output chỉ giữ/tiêu topic.
            next_momentum = _clamp01(prior.momentum * (0.94 if autonomous else 0.98))

        rough_gain = _clamp01(1.0 - max(_similarity(text, prior.last_assistant_text), _similarity(text, prior.current_focus)) * 0.72)
        gains = (*prior.semantic_gain_history, rough_gain)[-8:]
        energy_cost = 0.22 if autonomous else 0.10
        next_energy = max(0.0, prior.energy - energy_cost)
        next_public_budget = max(0, prior.public_autonomous_budget - (1 if autonomous else 0))
        avg_gain = sum(gains[-4:]) / max(1, len(gains[-4:]))
        if next_energy <= 0.06 or (len(gains) >= 4 and avg_gain < 0.22):
            lifecycle_state = "satiated"
        elif next_energy <= 0.22 or next_public_budget <= 0:
            lifecycle_state = "simmering"
        else:
            lifecycle_state = "developing"

        qid = ""
        if asks:
            qid = f"question:{turn_ref or turn_index or prior.revision + 1}"
        self._state = replace(
            prior,
            revision=prior.revision + 1,
            updated_at=current,
            last_assistant_text=text,
            last_assistant_turn_ref=str(turn_ref or ""),
            last_assistant_turn_index=max(0, int(turn_index or 0)),
            parent_focuses=parents,
            momentum=next_momentum,
            energy=next_energy,
            lifecycle_state=lifecycle_state,
            semantic_gain_history=gains,
            public_autonomous_budget=next_public_budget,
            autonomous_public_count=prior.autonomous_public_count + (1 if autonomous else 0),
            development_mode=next_mode,
            pending_question_id=qid,
            pending_question_text=text if asks else "",
            pending_question_turn_index=max(0, int(turn_index or 0)) if asks else 0,
            awaiting_user_answer=asks,
            source_refs=tuple(dict.fromkeys(refs))[-12:],
        )
        self._log("assistant_commit")
        return self._state

    def observe_private_development(
        self,
        *,
        semantic_gain: float,
        now: float | None = None,
    ) -> ConversationTrajectory:
        """Ghi một bước phát triển private mà không giả thành public speech.

        Private thought tiêu ít energy hơn public speech. Nếu gain thấp lặp lại,
        topic tự chuyển SIMMERING/SATIATED. Không có budget reset theo revision.
        """
        current = float(now if now is not None else time.time())
        prior = self.snapshot(now=current)
        if not prior.active or prior.closed or prior.private_budget <= 0:
            return prior
        gain = _clamp01(semantic_gain)
        gains = (*prior.semantic_gain_history, gain)[-8:]
        energy = max(0.0, prior.energy - 0.06)
        private_budget = max(0, prior.private_budget - 1)
        avg_gain = sum(gains[-4:]) / max(1, len(gains[-4:]))
        if energy <= 0.06 or (len(gains) >= 4 and avg_gain < 0.22):
            lifecycle = "satiated"
        elif private_budget <= 0 or energy <= 0.22:
            lifecycle = "simmering"
        else:
            lifecycle = "developing"
        self._state = replace(
            prior,
            revision=prior.revision + 1,
            updated_at=current,
            energy=energy,
            lifecycle_state=lifecycle,
            semantic_gain_history=gains,
            private_budget=private_budget,
            private_development_count=prior.private_development_count + 1,
        )
        self._log("private_development")
        return self._state

    def _log(self, reason: str) -> None:
        fn = getattr(self.logger, "info", None) if self.logger is not None else None
        if not callable(fn):
            return
        state = self._state
        try:
            fn(
                "[conversation trajectory v76] reason=%s rev=%s topic=%s depth=%s momentum=%.2f energy=%.2f life=%s pub=%s priv=%s mode=%s awaiting=%s focus=%s",
                reason,
                state.revision,
                state.topic_id or "none",
                state.depth,
                state.momentum,
                state.energy,
                state.lifecycle_state,
                state.public_autonomous_budget,
                state.private_budget,
                state.development_mode,
                int(state.awaiting_user_answer),
                _compact(state.current_focus, 120) or "none",
            )
        except Exception:
            pass


def ensure_conversation_trajectory_runtime(host: Any) -> ConversationTrajectoryRuntime:
    runtime = getattr(host, "conversation_trajectory_runtime", None)
    if isinstance(runtime, ConversationTrajectoryRuntime):
        return runtime
    runtime = ConversationTrajectoryRuntime(logger=getattr(host, "logger", None))
    setattr(host, "conversation_trajectory_runtime", runtime)
    return runtime


__all__ = [
    "TRAJECTORY_SCHEMA",
    "ConversationTrajectory",
    "ConversationTrajectoryRuntime",
    "ensure_conversation_trajectory_runtime",
]
