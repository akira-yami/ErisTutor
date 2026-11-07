# -*- coding: utf-8 -*-
"""Atomic persistence cho state cơ học của ConversationKernel V12."""
from __future__ import annotations

from dataclasses import replace
import threading
import time
from typing import Any, Callable

from .contracts import ConversationSessionState, RunPhase


class ConversationSessionStore:
    def __init__(self, *, atomic_store: Any | None = None, logger: Any = None) -> None:
        self.atomic_store = atomic_store
        self.logger = logger
        self._lock = threading.RLock()
        self._memory: dict[str, ConversationSessionState] = {}
        # Phân biệt "load đầu tiên sau process restart" với load bình thường
        # trong cùng process. Chỉ first-load mới được phép abandoned transient run.
        self._known_sessions: set[str] = set()

    @staticmethod
    def _sid(session_id: str) -> str:
        return str(session_id or "default").strip()[:180] or "default"

    @classmethod
    def _key(cls, session_id: str) -> str:
        return f"eris.conversation_session.v12:{cls._sid(session_id)}"

    def load(self, session_id: str) -> ConversationSessionState:
        sid = self._sid(session_id)
        if self.atomic_store is None:
            with self._lock:
                return self._memory.get(sid, ConversationSessionState(session_id=sid))
        try:
            record = self.atomic_store.ensure(self._key(sid), ConversationSessionState(session_id=sid).to_dict())
            state = ConversationSessionState.from_mapping(record.payload, session_id=sid)
            with self._lock:
                first_load_in_process = sid not in self._known_sessions
                self._known_sessions.add(sid)

            # Sau crash, active root/writer/audio run cũ không được "sống lại".
            # Ta chỉ recover state cơ học; tuyệt đối không replay text hay tự gọi
            # RootAgent. CONTINUATION_PENDING không nằm trong danh sách transient
            # vì lease riêng sẽ tự kiểm generation + expiry trước khi wake.
            if first_load_in_process and state.phase in {
                RunPhase.THINKING,
                RunPhase.WORKING,
                RunPhase.WRITING,
                RunPhase.DELIVERING,
            }:
                state = self.mutate(
                    sid,
                    lambda prior, now: replace(
                        prior,
                        active_run_id="",
                        active_move_id="",
                        active_delivery_id="",
                        phase=RunPhase.WAITING_USER,
                    ),
                )
            with self._lock:
                self._memory[sid] = state
                self._known_sessions.add(sid)
            return state
        except Exception as exc:  # noqa: BLE001
            self._log("warning", "[conversation-v12] durable load degraded: %s", exc)
            with self._lock:
                return self._memory.get(sid, ConversationSessionState(session_id=sid))

    def mutate(self, session_id: str, fn: Callable[[ConversationSessionState, float], ConversationSessionState]) -> ConversationSessionState:
        sid = self._sid(session_id)
        now = time.time()
        if self.atomic_store is None:
            with self._lock:
                prior = self._memory.get(sid, ConversationSessionState(session_id=sid))
                state = fn(prior, now)
                state = replace(state, version=prior.version + 1, updated_at=now)
                self._memory[sid] = state
                self._known_sessions.add(sid)
                return state
        try:
            with self.atomic_store.transaction(self._key(sid), ConversationSessionState(session_id=sid).to_dict()) as tx:
                prior = ConversationSessionState.from_mapping(tx.payload, session_id=sid)
                state = fn(prior, now)
                state = replace(state, version=prior.version + 1, updated_at=now)
                tx.stage(state.to_dict())
            with self._lock:
                self._memory[sid] = state
                self._known_sessions.add(sid)
            return state
        except Exception as exc:  # noqa: BLE001
            self._log("warning", "[conversation-v12] durable mutation degraded: %s", exc)
            with self._lock:
                prior = self._memory.get(sid, ConversationSessionState(session_id=sid))
                state = fn(prior, now)
                state = replace(state, version=prior.version + 1, updated_at=now)
                self._memory[sid] = state
                self._known_sessions.add(sid)
                return state

    def set_note(self, session_id: str, note: str) -> ConversationSessionState:
        raw = str(note or "")[:12000]
        return self.mutate(session_id, lambda prior, now: replace(prior, note=raw, note_updated_at=now))

    def clear_note(self, session_id: str) -> ConversationSessionState:
        return self.set_note(session_id, "")

    def _log(self, level: str, message: str, *args: Any) -> None:
        if self.logger is None:
            return
        try:
            getattr(self.logger, level)(message, *args)
        except Exception:
            pass


__all__ = ["ConversationSessionStore"]
