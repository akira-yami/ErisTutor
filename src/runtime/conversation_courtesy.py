# -*- coding: utf-8 -*-
"""Conversation Courtesy Contract V76 cho ERIS.

Nếu ERIS vừa đặt câu hỏi cần câu trả lời, autonomous speech phải nhường lượt cho
user. Đây là quyền giữ lượt hội thoại, không phải một heuristic "user có vẻ bận".
State được clear ngay khi có user turn mới hoặc khi timeout cấu hình hết hạn.
"""
from __future__ import annotations

import os
import threading
import time
from dataclasses import asdict, dataclass
from typing import Any


_QUESTION_ACTS = {
    "user_probe", "ask_genuine_question", "question", "clarifying_question",
    "clarification_question", "casual_curiosity", "mutual_understanding_bid",
    "followup_question", "relationship_continuity_bid",
}


def _timeout_default() -> float:
    try:
        return max(15.0, float(os.getenv("ERIS_COURTESY_WAIT_TIMEOUT_SECONDS", "180") or 180.0))
    except (TypeError, ValueError):
        return 180.0


def _compact(value: Any, limit: int = 360) -> str:
    return " ".join(str(value or "").split()).strip()[: max(16, int(limit))]


@dataclass(frozen=True, slots=True)
class CourtesyState:
    awaiting_user_answer: bool = False
    pending_question_id: str = ""
    pending_speech_act: str = ""
    response_expectation: str = "none"
    question_text: str = ""
    asked_at: float = 0.0
    timeout_seconds: float = 180.0
    revision: int = 0
    last_release_reason: str = ""

    def timed_out(self, *, now: float | None = None) -> bool:
        if not self.awaiting_user_answer or self.asked_at <= 0.0:
            return False
        return float(now or time.time()) - self.asked_at >= max(15.0, self.timeout_seconds)

    def effective_wait(self, *, now: float | None = None) -> bool:
        return bool(self.awaiting_user_answer and not self.timed_out(now=now))

    def to_dict(self, *, now: float | None = None) -> dict[str, Any]:
        payload = asdict(self)
        payload["schema"] = "eris.conversation_courtesy.v74"
        payload["effective_wait"] = self.effective_wait(now=now)
        payload["timed_out"] = self.timed_out(now=now)
        return payload


class ConversationCourtesyContract:
    def __init__(self, *, timeout_seconds: float | None = None, logger: Any | None = None) -> None:
        self.logger = logger
        self._lock = threading.RLock()
        self._state = CourtesyState(timeout_seconds=float(timeout_seconds or _timeout_default()))

    def snapshot(self, *, now: float | None = None) -> CourtesyState:
        current = float(now or time.time())
        with self._lock:
            state = self._state
            if state.awaiting_user_answer and state.timed_out(now=current):
                self._state = CourtesyState(
                    awaiting_user_answer=False,
                    timeout_seconds=state.timeout_seconds,
                    revision=state.revision + 1,
                    last_release_reason="courtesy_timeout",
                )
            return self._state

    def observe_user_turn(self, *, turn_ref: str = "", now: float | None = None) -> CourtesyState:
        del turn_ref
        current = float(now or time.time())
        with self._lock:
            prior = self._state
            self._state = CourtesyState(
                awaiting_user_answer=False,
                timeout_seconds=prior.timeout_seconds,
                revision=prior.revision + 1,
                last_release_reason="user_response",
            )
        self._log("debug", "[courtesy] release vì user đã có turn mới at=%.3f", current)
        return self._state

    def observe_assistant_commit(
        self,
        text: str,
        *,
        speech_act: str = "",
        response_expectation: str = "",
        turn_ref: str = "",
        now: float | None = None,
    ) -> CourtesyState:
        current = float(now or time.time())
        clean = _compact(text, 1200)
        act = _compact(speech_act, 120).lower()
        expectation = _compact(response_expectation, 24).lower()
        if expectation not in {"required", "invited", "none"}:
            expectation = "required" if bool(act in _QUESTION_ACTS or (clean and clean.rstrip().endswith("?"))) else "none"
        # V76: punctuation không còn tự tạo reply debt nếu upstream đã gán NONE.
        asks = expectation in {"required", "invited"}
        with self._lock:
            prior = self._state
            if asks:
                self._state = CourtesyState(
                    awaiting_user_answer=True,
                    pending_question_id=_compact(turn_ref or f"assistant-question:{prior.revision + 1}", 220),
                    pending_speech_act=act or "question",
                    response_expectation=expectation,
                    question_text=clean,
                    asked_at=current,
                    timeout_seconds=prior.timeout_seconds,
                    revision=prior.revision + 1,
                    last_release_reason="",
                )
            elif prior.awaiting_user_answer:
                # Một assistant commit khác không tự xóa trạng thái chờ user. Nếu
                # boundary upstream bị bỏ qua, giữ state để tick sau tiếp tục
                # fail-closed cho đến khi có user turn hoặc timeout hợp lệ.
                self._state = prior
            else:
                self._state = CourtesyState(
                    awaiting_user_answer=False,
                    timeout_seconds=prior.timeout_seconds,
                    revision=prior.revision + 1,
                    last_release_reason=prior.last_release_reason,
                )
        return self._state

    def runtime_context(self, *, now: float | None = None) -> dict[str, Any]:
        state = self.snapshot(now=now)
        return {
            "awaiting_user_answer": state.effective_wait(now=now),
            "courtesy_hold": state.effective_wait(now=now),
            "pending_question_id": state.pending_question_id,
            "response_expectation": state.response_expectation,
            "courtesy_revision": state.revision,
        }

    def _log(self, level: str, message: str, *args: Any) -> None:
        method = getattr(self.logger, level, None)
        if callable(method):
            try:
                method(message, *args)
            except Exception:
                pass


def ensure_conversation_courtesy(owner: Any) -> ConversationCourtesyContract:
    runtime = getattr(owner, "conversation_courtesy_contract", None)
    if isinstance(runtime, ConversationCourtesyContract):
        return runtime
    runtime = ConversationCourtesyContract(logger=getattr(owner, "logger", None))
    try:
        setattr(owner, "conversation_courtesy_contract", runtime)
    except Exception:
        pass
    return runtime


__all__ = ["CourtesyState", "ConversationCourtesyContract", "ensure_conversation_courtesy"]
