# -*- coding: utf-8 -*-
"""Write-back bridge cho ConversationKernel V12.

Bridge này chỉ ghi nhận những gì *đã xảy ra* ở boundary user/public commit.
Nó không được chọn nội dung, không tạo agenda và không được gọi model để quyết
Eris nên nói gì. RootAgent vẫn là semantic/social authority duy nhất.

Hard-cutover V12 từng giữ các runtime state nhưng làm mất call-site observe.
Kết quả là context có thể đọc state cũ mãi mà relationship/trajectory/memory
không được nuôi bởi các lượt mới. Module này nối lại đúng write-back path đó.
"""
from __future__ import annotations

import time
from typing import Any, Mapping, Sequence


class ConversationObservationBridge:
    def __init__(self, host: Any) -> None:
        self.host = host

    def observe_user_turn(
        self,
        user_text: str,
        *,
        turn_ref: str = "",
        turn_index: int = 0,
        previous_assistant_text: str = "",
        previous_assistant_turn_ref: str = "",
        previous_assistant_turn_index: int = 0,
        now: float | None = None,
    ) -> dict[str, Any]:
        """Fan-out deterministic evidence của một user turn tới các state owner."""
        current = float(now if now is not None else time.time())
        text = str(user_text or "").strip()
        if not text:
            return {}
        result: dict[str, Any] = {}

        # Preserve the literal reaction before optional observer inference can be
        # preempted. No classifier, model call or wait on the user ingress path.
        try:
            from .interaction_choice import record_user_reaction
            sid = str(getattr(self.host, "conversation_id", "") or
                      getattr(self.host, "thread_id", "") or "foreground:primary")
            result["strategy_reaction"] = record_user_reaction(
                self.host, session_id=sid, turn_seq=turn_index, turn_ref=turn_ref,
                user_text=text, now=current,
                previous_assistant_turn_seq=previous_assistant_turn_index,
            )
        except Exception as exc:
            self._debug("strategy_reaction", exc)

        # Xưng hô explicit là shared state deterministic, không phải social planner.
        try:
            from runtime.user_addressing_state import observe_user_addressing
            result["user_addressing_changed"] = bool(observe_user_addressing(text, turn_id=turn_ref))
        except Exception as exc:
            self._debug("user_addressing", exc)

        trajectory_view: Mapping[str, Any] | None = None
        try:
            from runtime.conversation_trajectory import ensure_conversation_trajectory_runtime
            trajectory = ensure_conversation_trajectory_runtime(self.host).observe_user_turn(
                text,
                turn_ref=turn_ref,
                turn_index=turn_index,
                previous_assistant_text=previous_assistant_text,
                now=current,
            )
            to_dict = getattr(trajectory, "to_dict", None)
            trajectory_view = to_dict(now=current) if callable(to_dict) else {}
            result["trajectory"] = dict(trajectory_view or {})
        except TypeError:
            try:
                to_dict = getattr(trajectory, "to_dict", None)  # type: ignore[name-defined]
                trajectory_view = to_dict() if callable(to_dict) else {}
                result["trajectory"] = dict(trajectory_view or {})
            except Exception as exc:
                self._debug("trajectory_user", exc)
        except Exception as exc:
            self._debug("trajectory_user", exc)

        try:
            from runtime.conversation_handoff import ensure_conversation_handoff_runtime
            handoff = ensure_conversation_handoff_runtime(self.host).observe_user_turn(
                text,
                turn_ref=turn_ref,
                turn_index=turn_index,
                previous_assistant_text=previous_assistant_text,
                previous_assistant_turn_ref=previous_assistant_turn_ref,
                previous_assistant_turn_index=previous_assistant_turn_index,
                now=current,
            )
            to_dict = getattr(handoff, "to_dict", None)
            result["handoff"] = dict(to_dict() if callable(to_dict) else {})
        except Exception as exc:
            self._debug("handoff_user", exc)

        try:
            from runtime.persistent_individual import ensure_persistent_individual_runtime
            individual = ensure_persistent_individual_runtime(self.host)
            projection = individual.observe_user_turn(
                text,
                turn_ref=turn_ref,
                trajectory=trajectory_view or {},
                now=current,
            )
            result["persistent_individual"] = dict(projection or {}) if isinstance(projection, Mapping) else {}
        except Exception as exc:
            self._debug("persistent_user", exc)

        try:
            from runtime.life_horizon import ensure_life_horizon_manager
            ensure_life_horizon_manager(self.host).observe_user_turn(turn_ref=turn_ref, now=current)
            result["life_horizon"] = True
        except Exception as exc:
            self._debug("life_horizon_user", exc)

        # Memory hot path là admission/write-back, không phải retrieval và không
        # được chạy ở writer. Method này đã có fallback nếu pipeline nền unavailable.
        ingest = getattr(self.host, "_ingest_user_turn_memory_hot_path", None)
        if callable(ingest):
            try:
                result["memory_ingested"] = bool(ingest(text, turn_id=turn_ref))
            except Exception as exc:
                self._debug("memory_user", exc)

        try:
            from runtime.world_state import ensure_world_state_service
            # ChatCycle đã observe WorldState trước orchestrator. Chỉ fallback nếu
            # caller V12 khác đi thẳng vào bridge và WorldState chưa có đúng text.
            world = ensure_world_state_service(self.host)
            snapshot = world.snapshot(now=current)
            if str(getattr(snapshot.conversation, "last_user_text", "") or "").strip() != text:
                world.observe_user_turn(text, now=current)
        except Exception as exc:
            self._debug("world_user", exc)

        return result

    def observe_assistant_commit(
        self,
        assistant_text: str,
        *,
        turn_ref: str = "",
        turn_index: int = 0,
        speech_act: str = "",
        event_kind: str = "chat",
        causal_receipt: Any = None,
        recent_history: Sequence[Mapping[str, Any]] = (),
        user_text: str = "",
        now: float | None = None,
        claim_provenance: Mapping[str, Any] | None = None,
        interaction_choice: Mapping[str, Any] | None = None,
        session_id: str = "",
        choice_goal: str = "",
    ) -> dict[str, Any]:
        """Ghi hậu quả sau khi transport đã chấp nhận public commit."""
        current = float(now if now is not None else time.time())
        text = str(assistant_text or "").strip()
        if not text:
            return {}
        autonomous = str(event_kind or "").strip().lower() != "user_turn"
        result: dict[str, Any] = {}

        try:
            from runtime.world_state import ensure_world_state_service
            ensure_world_state_service(self.host).observe_assistant_commit(text, now=current)
            result["world_state"] = True
        except Exception as exc:
            self._debug("world_assistant", exc)

        response_expectation = "invited" if "?" in text else "none"
        try:
            from runtime.conversation_trajectory import ensure_conversation_trajectory_runtime
            trajectory = ensure_conversation_trajectory_runtime(self.host).observe_assistant_commit(
                text,
                turn_ref=turn_ref,
                turn_index=turn_index,
                speech_act=speech_act,
                response_expectation=response_expectation,
                autonomous=autonomous,
                now=current,
            )
            to_dict = getattr(trajectory, "to_dict", None)
            try:
                result["trajectory"] = dict(to_dict(now=current) if callable(to_dict) else {})
            except TypeError:
                result["trajectory"] = dict(to_dict() if callable(to_dict) else {})
        except Exception as exc:
            self._debug("trajectory_assistant", exc)

        try:
            from runtime.conversation_handoff import ensure_conversation_handoff_runtime
            handoff = ensure_conversation_handoff_runtime(self.host).observe_assistant_commit(
                text,
                turn_ref=turn_ref,
                turn_index=turn_index,
                speech_act=speech_act,
                causal_receipt=causal_receipt,
                now=current,
            )
            to_dict = getattr(handoff, "to_dict", None)
            result["handoff"] = dict(to_dict() if callable(to_dict) else {})
        except Exception as exc:
            self._debug("handoff_assistant", exc)

        try:
            from runtime.persistent_individual import ensure_persistent_individual_runtime
            projection = ensure_persistent_individual_runtime(self.host).observe_assistant_commit(
                text,
                speech_act=speech_act,
                autonomous=autonomous,
                now=current,
            )
            result["persistent_individual"] = dict(projection or {}) if isinstance(projection, Mapping) else {}
        except Exception as exc:
            self._debug("persistent_assistant", exc)

        try:
            from runtime.life_horizon import ensure_life_horizon_manager
            ensure_life_horizon_manager(self.host).observe_assistant_commit(
                speech_act=speech_act or "public_commit",
                turn_ref=turn_ref,
                now=current,
            )
            result["life_horizon"] = True
        except Exception as exc:
            self._debug("life_horizon_assistant", exc)

        # Completed-turn observer là lane inference nền. Nó cập nhật StateReducer
        # cho *lượt sau*; không được mutate câu vừa commit.
        # Snapshot BEFORE enqueuing this choice: the current utterance cannot
        # be the outcome of itself. Older reacted choices remain retryable.
        state_summary = self._state_summary()
        if session_id:
            state_summary["conversation_session_id"] = session_id
        try:
            from .interaction_choice import record_delivered_choice
            result["pending_strategy"] = record_delivered_choice(
                self.host, choice=interaction_choice or {}, delivery_id=turn_ref,
                session_id=session_id, turn_seq=turn_index, goal=choice_goal,
                assistant_text=text,
                user_text=str(user_text or ""),
            )
        except Exception as exc:
            self._debug("pending_strategy", exc)
        observer = getattr(self.host, "ai_post_turn_observer", None)
        submit = getattr(observer, "submit", None)
        if callable(submit):
            try:
                from memory.claim_provenance import classify_ingress, provenance_of
                assistant_source = provenance_of({"claim_provenance": claim_provenance}) if claim_provenance else classify_ingress(
                    "assistant_utterance", turn_id=turn_ref,
                )
                ticket = submit(
                    turn_id=turn_ref or f"public:{time.time_ns()}",
                    turn_seq=max(0, int(turn_index or 0)),
                    user_text=str(user_text or ""),
                    assistant_text=text,
                    event_kind="chat" if not autonomous else str(event_kind or "autonomous"),
                    recent_history=tuple(recent_history)[-12:],
                    mechanical_outcomes=(),
                    state_summary=state_summary,
                    assistant_meta={"claim_provenance": assistant_source},
                )
                result["post_observer_ticket"] = str(getattr(ticket, "ticket_id", "") or "")
            except Exception as exc:
                self._debug("post_turn_observer", exc)

        return result

    def _state_summary(self) -> dict[str, Any]:
        reducer = getattr(self.host, "ai_state_reducer", None)
        snapshot = getattr(reducer, "snapshot", None)
        if not callable(snapshot):
            return {}
        try:
            state = snapshot()
            to_dict = getattr(state, "to_dict", None)
            if callable(to_dict):
                raw = to_dict()
                return dict(raw) if isinstance(raw, Mapping) else {}
        except Exception as exc:
            self._debug("state_summary", exc)
        return {}

    def _debug(self, operation: str, exc: Exception) -> None:
        logger = getattr(self.host, "logger", None)
        fn = getattr(logger, "debug", None) if logger is not None else None
        if callable(fn):
            try:
                fn("[conversation-observation] %s degraded: %s", operation, exc)
            except Exception:
                pass


def ensure_conversation_observation_bridge(host: Any) -> ConversationObservationBridge:
    existing = getattr(host, "conversation_observation_bridge", None)
    if isinstance(existing, ConversationObservationBridge):
        return existing
    bridge = ConversationObservationBridge(host)
    try:
        host.conversation_observation_bridge = bridge
    except Exception:
        pass
    return bridge


__all__ = ["ConversationObservationBridge", "ensure_conversation_observation_bridge"]
