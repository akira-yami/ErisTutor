# -*- coding: utf-8 -*-
"""Table-driven inbound protocol dispatcher.

The dispatcher owns protocol mechanics only. Domain handlers remain on the
coordinator-facing mixins, preserving the public runtime API while preventing
``RuntimeCoordinator`` from becoming a 7,000-line switch statement with moods.

Nâng cấp Incremental Dialogue (Text-first):
- Sử dụng ``InteractionDynamicsController`` để phân loại từ ngắn ("ừ", "khoan"...)
  dựa trên ngữ cảnh câu nói trước đó của Eris (ví dụ: "ừ" sau câu hỏi của Eris là
  ``YIELD_TO_USER`` phải tạo lượt trả lời, còn "ừ" khi Eris đang giảng bài là
  ``KEEP_SPEAKING`` giữ nguyên audio).
- Tích hợp ``MicroActRouter`` để phát trực tiếp các micro-act không mang nội dung
  mới (``RECEIPT``, ``PROMPT_CONTINUE``, ``STOP_ACK``) và ghi log bằng
  ``InteractionActReceipt`` mà không phải chờ toàn bộ semantic pipeline (Root -> Writer).
"""
from __future__ import annotations
from core.di import resolve_dependency

import time
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Callable

from runtime.interaction_dynamics import (
    DynamicsAction,
    MicroActKind,
    ensure_interaction_dynamics_controller,
    ensure_micro_act_router,
)
from runtime.open_loop_store import ensure_open_loop_store
from runtime.policy_settings import ProtocolSettings
from runtime.stage_protocol import is_stage_inbound_type
from runtime.subsystem_health import FailureKind


@dataclass(frozen=True, slots=True)
class MessageDispatchResult:
    message_type: str
    handled: bool
    reason: str = ""


class IncomingMessageDispatcher:
    def __init__(
        self,
        owner: Any,
        *,
        settings: ProtocolSettings | None = None,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self.owner = owner
        self.settings = resolve_dependency(settings, ProtocolSettings.load)
        self.clock = clock or time.time
        self._handled = 0
        self._unknown = 0
        self._invalid = 0

    def dispatch(self, message: Mapping[str, Any] | Any) -> MessageDispatchResult:
        if not isinstance(message, Mapping):
            self._invalid += 1
            error = TypeError("incoming message must be a mapping")
            self._record_failure(error, reason="invalid_message_envelope")
            if self.settings.reject_non_mapping:
                return MessageDispatchResult("", False, "invalid_message_envelope")
            message = {}

        msg = dict(message)
        raw_type = str(msg.get("type", "") or "").strip()
        msg_type = raw_type[: self.settings.max_message_type_chars]
        if raw_type != msg_type:
            self._invalid += 1
            self._record_failure(ValueError("incoming message type too long"), reason="message_type_too_long")
            return MessageDispatchResult(msg_type, False, "message_type_too_long")
        if not msg_type:
            self._invalid += 1
            return MessageDispatchResult("", False, "missing_message_type")

        if self._maintenance_blocks(msg_type):
            self.owner._send_runtime_json(
                {
                    "type": "maintenance_busy",
                    "parse_mode": "maintenance_busy",
                    "should_speak": False,
                    "maintenance_busy": {"message": "Brain đang bảo trì; lượt mới chưa được nhận."},
                },
                channel="maintenance",
            )
            return self._success(msg_type, reason="maintenance_busy")

        if is_stage_inbound_type(msg_type):
            self.owner._handle_stage_inbound_event(msg)
            return self._success(msg_type, reason="stage")

        handler = self._routes().get(msg_type)
        if handler is None:
            self._unknown += 1
            logger = getattr(self.owner, "logger", None)
            method = getattr(logger, "debug", None)
            if callable(method):
                method("[incoming] ignored unknown msg_type=%s", msg_type)
            return MessageDispatchResult(msg_type, False, "unknown_message_type")

        try:
            handler(msg)
        except Exception as exc:
            self._record_failure(exc, reason=f"handler_failed:{msg_type}")
            raise
        return self._success(msg_type)

    def snapshot(self) -> dict[str, int]:
        return {"handled": self._handled, "unknown": self._unknown, "invalid": self._invalid}

    def _routes(self) -> dict[str, Callable[[dict[str, Any]], None]]:
        owner = self.owner
        return {
            "panel_state_ack": owner._handle_panel_state_ack,
            "memory_panel_request": getattr(owner, "_handle_memory_panel_request", lambda _msg: None),
            "capability_panel_request": getattr(owner, "_handle_capability_panel_request", lambda _msg: None),
            "learning_canvas_request": getattr(owner, "_handle_learning_canvas_request", lambda _msg: None),
            "agent_control": getattr(owner, "_handle_agent_control", lambda _msg: None),
            "phase7_validation_control": owner._handle_phase7_validation_control,
            "chat_start": lambda _msg: owner._enter_chat_cycle(),
            "chat": self._handle_chat,
            "force_speak": self._handle_force_speak,
            "anchor_control": owner._handle_anchor_control,
            "identity_control": getattr(owner, "_handle_identity_control", lambda _msg: None),
            "depth_control": owner._handle_depth_control,
            "settings_update": owner._handle_settings_update,
            "settings_health": lambda _msg: owner._handle_settings_health(),
            "settings_connection_test": owner._handle_settings_connection_test,
            "maintenance_catalog": owner._handle_maintenance_catalog,
            "maintenance_request": owner._handle_maintenance_request,
            "maintenance_execute": owner._handle_maintenance_request,
            "debug_command": owner._handle_debug_command,
            "chat_end": lambda _msg: owner._end_chat_cycle(),
        }

    def _handle_chat(self, msg: dict[str, Any]) -> None:
        owner = self.owner
        user_text = str(msg.get("message", "") or "").strip()
        try:
            timestamp = float(msg.get("timestamp") or self.clock())
        except (TypeError, ValueError):
            timestamp = float(self.clock())
        # P0: Đến đây mới có nội dung cuối của user. Không huỷ TTS ở
        # chat_start, vì khi ấy không thể phân biệt "ừ" với "dừng lại".
        # Dù client bỏ quên chat_start, message chat vẫn tự mở chat mode.
        if not bool(getattr(owner, "chat_mode", False)):
            owner._enter_chat_cycle()
        cancel_audio = True  # Fail-safe: chưa biết nghĩa thì user được quyền chen ngang.
        cancel_generation = True
        dynamics_decision = None
        try:
            clock = getattr(owner, "interaction_clock", None)
            speech_busy = bool(getattr(clock, "speech_floor_busy", False))
            thinking_busy = bool(getattr(owner, "_chat_processing", False))

            # Lấy ngữ cảnh câu nói gần nhất hoặc đang phát của Eris để phân biệt
            # "ừ" khi đang giảng bài (KEEP_SPEAKING) với "ừ" sau câu hỏi (YIELD_TO_USER).
            spoken_so_far = ""
            ledger = getattr(owner, "interrupt_ledger", None)
            if ledger is not None and callable(getattr(ledger, "active", None)):
                active_span = ledger.active()
                if active_span is not None:
                    spoken_so_far = str(getattr(active_span, "text", "") or "")
            last_assistant_text = str(getattr(owner, "last_text", "") or "")
            if not last_assistant_text:
                chat_hist = getattr(owner, "chat_history", None) or ()
                for item in reversed(tuple(chat_hist)):
                    if isinstance(item, Mapping) and item.get("role") == "assistant":
                        last_assistant_text = str(item.get("content", "") or "")
                        break
            last_turn = getattr(owner, "last_agent_turn", None)
            last_act = str(last_turn.get("intent", "") if isinstance(last_turn, Mapping) else "")

            sid = str(
                getattr(owner, "conversation_id", "")
                or getattr(owner, "thread_id", "")
                or "foreground:primary"
            ).strip()[:180]
            has_open_loop = bool(ensure_open_loop_store(owner).active_loops(sid, now=timestamp))

            controller = ensure_interaction_dynamics_controller(owner)
            dynamics_decision = controller.evaluate_incoming(
                user_text,
                speech_busy=speech_busy,
                thinking_busy=thinking_busy,
                spoken_text_so_far=spoken_so_far,
                last_assistant_text=last_assistant_text,
                last_assistant_act=last_act,
                has_open_loop=has_open_loop,
            )
            owner.last_dynamics_decision = dynamics_decision

            if dynamics_decision.action == DynamicsAction.KEEP_SPEAKING and speech_busy:
                # Đây thực sự là tiếng đệm lắng nghe khi Eris đang giảng bài;
                # giữ nguyên audio đang phát và không tạo user turn mới.
                owner.logger.debug(
                    "[conversation-v12] KEEP_SPEAKING on context-verified backchannel=%r",
                    user_text[:80],
                )
                finish = getattr(owner, "_end_chat_cycle", None)
                if callable(finish):
                    finish()
                return

            cancel_audio = bool(dynamics_decision.cancel_audio)
            cancel_generation = bool(dynamics_decision.cancel_generation)

            # Bổ sung MicroActRouter phát trực tiếp các act không mang nội dung mới
            # (RECEIPT, PROMPT_CONTINUE, STOP_ACK) và ghi log bằng InteractionActReceipt.
            micro_router = ensure_micro_act_router(owner)
            rev_buf = getattr(owner, "input_revision_buffer", None)
            current_rev = int(getattr(rev_buf, "current_revision", 0) or 0) + 1

            def _emit_micro_act(surface: str, act_kind: MicroActKind) -> bool:
                emit_hook = getattr(owner, "_emit_fast_micro_act", None)
                if callable(emit_hook):
                    try:
                        return bool(emit_hook(surface, act_kind=act_kind.value, timestamp=timestamp, user_text=user_text))
                    except TypeError:
                        return bool(emit_hook(surface, act_kind=act_kind.value, timestamp=timestamp))
                return True

            receipt = micro_router.select_and_emit(
                dynamics_decision,
                user_text=user_text,
                input_revision=current_rev,
                eris_currently_speaking=speech_busy,
                full_reply_imminent=False,
                emit_fn=_emit_micro_act,
                now=timestamp,
            )
            if receipt is not None:
                owner.last_interaction_act_receipt = receipt
        except Exception as exc:
            logger = getattr(owner, "logger", None)
            if callable(getattr(logger, "debug", None)):
                logger.debug("[conversation-v12] dynamics classification failed: %s", exc)
        owner._capture_speech_interrupt_for_user(user_text, timestamp=timestamp)
        preempt = getattr(owner, "_preempt_for_classified_user_turn", None)
        if callable(preempt):
            # Gửi hai cờ riêng, không suy ra cancel_generation từ cancel_audio.
            preempt(cancel_audio=cancel_audio, cancel_generation=cancel_generation)
        elif cancel_audio:
            # Adapter test/host cũ chưa có ChatCycleMixin: vẫn giữ barge-in.
            supersede = getattr(owner, "_supersede_tts_for_new_user_turn", None)
            if callable(supersede):
                supersede("user_chat_supersede")
        owner._run_chat_cycle(user_text, user_timestamp=timestamp)

    def _handle_force_speak(self, _msg: dict[str, Any]) -> None:
        self.owner._supersede_tts_for_new_user_turn("user_force_speak")
        self.owner._handle_force_speak_request()

    def _maintenance_blocks(self, msg_type: str) -> bool:
        return bool(getattr(self.owner, "_maintenance_in_progress", False)) and msg_type in {
            "chat", "chat_start", "force_speak",
        }

    def _success(self, msg_type: str, *, reason: str = "") -> MessageDispatchResult:
        self._handled += 1
        registry = getattr(self.owner, "subsystem_health_registry", None)
        if registry is not None and (self._handled == 1 or self._handled % 64 == 0):
            registry.record_success(
                "runtime.protocol.inbound",
                details={"message_type": msg_type, **self.snapshot()},
            )
        return MessageDispatchResult(msg_type, True, reason)

    def _record_failure(self, error: BaseException, *, reason: str) -> None:
        registry = getattr(self.owner, "subsystem_health_registry", None)
        if registry is not None:
            registry.record_failure(
                "runtime.protocol.inbound",
                error,
                kind=FailureKind.INVALID_EXTERNAL_DATA,
                degraded=True,
                reason=reason,
                details=self.snapshot(),
            )


__all__ = ["IncomingMessageDispatcher", "MessageDispatchResult"]
