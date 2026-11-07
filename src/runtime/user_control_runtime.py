# -*- coding: utf-8 -*-
"""Extracted coordinator runtime boundary.

The methods remain owner-oriented for backward compatibility while the main
coordinator becomes a small composition surface. New behavior should prefer
typed services over adding more methods to these compatibility mixins.
"""
from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import random
import re
import threading
import time
import traceback
import unicodedata
from collections.abc import Mapping
from types import SimpleNamespace

from domain import Decision
from cortex.contracts import TiPolicyReport
from runtime.chat_cycle import ChatCycleMixin
from runtime.stage_protocol_adapter import stage_state_event


class UserControlRuntimeMixin:
    def _apply_position_update(self, decision: Decision) -> None:
        intent = decision.resolve_motion_intent()
        tx = decision.resolve_motion_target_x()
        facing = decision.resolve_motion_facing()

        if intent in {"walk_to", "climb_to_top"} and 0.0 <= tx <= 1.0:
            self.inferred_pos_x = tx
        elif intent == "walk" and facing == -1:
            self.inferred_pos_x = max(0.0, self.inferred_pos_x - 0.4)
        elif intent == "walk" and facing == 1:
            self.inferred_pos_x = min(1.0, self.inferred_pos_x + 0.4)

    def _build_silent_motion_decision(self, ti_report: TiPolicyReport) -> Decision | None:
        motion_intent = str(getattr(ti_report, "motion_intent_hint", "") or "").strip()
        motion_target_x = float(getattr(ti_report, "motion_target_x_hint", -1.0) or -1.0)
        motion_facing = int(getattr(ti_report, "motion_facing_hint", 0) or 0)
        if not motion_intent:
            return None
        if motion_target_x < 0.0 and motion_facing == 0:
            return None
        return Decision(
            text="",
            animation=str(getattr(ti_report, "animation_hint", "") or "stand"),
            should_speak=False,
            confidence=0.54,
            parse_mode="motion_only",
            debug_trace=f"silent_motion; ti_mode={getattr(ti_report, 'mode', '')}",
            motion_intent=motion_intent,
            motion_target_x=motion_target_x,
            motion_facing=motion_facing,
        )

    def _normalize_user_message(self, user_text: str) -> str:
        lowered = (user_text or "").strip().lower()
        try:
            remojibake = lowered.encode("latin1").decode("utf-8")
            if remojibake:
                lowered = remojibake
        except (UnicodeEncodeError, UnicodeDecodeError):
            pass
        lowered = unicodedata.normalize("NFD", lowered)
        lowered = "".join(ch for ch in lowered if unicodedata.category(ch) != "Mn")
        lowered = re.sub(r"[^\w\s]", " ", lowered)
        lowered = " ".join(lowered.split())

        filler_suffixes = {
            "nhe",
            "nha",
            "nhi",
            "a",
            "ah",
            "ha",
            "huh",
            "vay",
            "co",
            "khong",
            "nao",
            "di",
            "voi",
            "giup",
            "dum",
        }
        if False:
            filler_suffixes = set(
            self._normalize_user_message(item)
            for item in (
                "nhỉ",
                "à",
                "ah",
                "ha",
                "huh",
                "vậy",
                "có",
                "không",
                "nào",
                "đi",
                "với",
                "giúp",
                "đùm",
                "đi",
            )
        )
        tokens = lowered.split()
        while tokens and tokens[-1] in filler_suffixes:
            tokens.pop()
        return " ".join(tokens)

    def _looks_like_screen_vision_request(
        self,
        user_text: str,
        *,
        previous_assistant_text: str = "",
    ) -> bool:
        """Dùng duy nhất smart vision gate của ChatCycleMixin.

        Bản cũ ở coordinator coi mọi lượt chat là lý do chụp màn hình và vô tình
        đè method mới trong mixin. Delegate tường minh để MRO không thể hồi sinh
        logic cũ trong một lần merge sau này.
        """

        return ChatCycleMixin._looks_like_screen_vision_request(
            self,
            user_text,
            previous_assistant_text=previous_assistant_text,
        )

    def _capture_screen_frame_for_chat(self, user_text: str, *, previous_public_turn=None) -> str:
        return ChatCycleMixin._capture_screen_frame_for_chat(
            self,
            user_text,
            previous_public_turn=previous_public_turn,
        )

    def _build_user_message_signature(self, user_text: str) -> str:
        normalized = self._normalize_user_message(user_text)
        if not normalized:
            return ""

        # Repetition identity is textual only. A regex action router must not
        # reinterpret the user's meaning before the model-owned semantic planner.
        return normalized

    def _track_repeated_user_message(self, user_text: str) -> int:
        signature = self._build_user_message_signature(user_text)
        if not signature:
            self.last_user_signature = ""
            self.repeated_user_count = 0
            return 0

        if signature == self.last_user_signature:
            self.repeated_user_count += 1
        else:
            self.last_user_signature = signature
            self.repeated_user_count = 1
        return self.repeated_user_count

    def _handle_force_speak_request(self) -> None:
        """Biến force-speak thành ConversationEvent, không dựng speech pipeline riêng."""

        if not bool(getattr(self, "ai_runtime_enabled", False)):
            self.logger.error("[conversation-v12] force_speak unavailable: runtime bootstrap failed")
            return
        orchestrator = getattr(self, "ai_autonomous_orchestrator", None)
        run_force = getattr(orchestrator, "run_force_speak", None)
        if not callable(run_force):
            self.logger.error("[conversation-v12] force_speak unavailable: autonomous event ingress missing")
            return
        run_force()

    def _handle_stage_inbound_event(self, msg: dict) -> None:
        """Nhận event ngược từ frontend/sân khấu.

        Đây là lớp protocol, không thay chat payload cũ. Frontend có thể báo
        `interrupt` ngay khi mic phát hiện user bắt đầu nói, trước khi STT có text.
        """

        bus = getattr(self, "stage_event_bus", None)
        event = None
        try:
            event = bus.remember_inbound(msg) if bus is not None else None
        except Exception as exc:
            self.logger.debug("[stage-protocol] inbound parse failed: %s", exc)
        if event is None:
            return
        self.last_stage_inbound_event = event.to_meta()

        if event.type == "interrupt":
            self._handle_stage_interrupt_event(event)
            return

        if event.type in {"vad_start", "partial_asr", "final_asr", "laugh", "backchannel"}:
            self._handle_voice_observation_event(event)
            return

        if event.type == "avatar_ready":
            self.avatar_ready = True
            self._emit_stage_protocol_state("avatar_ready_ack", data=event.to_meta())
            return

        if event.type == "audio_playback_aborted":
            self._handle_audio_playback_aborted(event)
            return

        if event.type in {"audio_playback_started", "audio_playback_done"}:
            self._track_frontend_audio_event(event)
            clock = getattr(self, "interaction_clock", None)
            if clock is not None:
                timestamp = float(getattr(event, "timestamp", time.time()) or time.time())
                if event.type == "audio_playback_started":
                    clock.mark_audio_started(timestamp, turn_id=str(getattr(event, "turn_id", "") or ""))
                else:
                    # Internal TTS lifecycle đã biết segment_count và là authority
                    # cho floor. Frontend done của segment đầu không được nhả turn
                    # khi segment sau vẫn pending. Chỉ dùng frontend edge làm fallback
                    # khi không có bằng chứng đây là một segment trung gian.
                    last_tts = getattr(self, "last_tts_event", {})
                    intermediate = False
                    if isinstance(last_tts, dict):
                        same_turn = str(last_tts.get("turn_id", "") or "") == str(getattr(event, "turn_id", "") or "")
                        try:
                            intermediate = bool(
                                same_turn
                                and int(last_tts.get("segment_count", 1) or 1) > 1
                                and int(last_tts.get("segment_id", 0) or 0) < int(last_tts.get("segment_count", 1) or 1) - 1
                            )
                        except (TypeError, ValueError):
                            intermediate = False
                    if not intermediate:
                        clock.mark_audio_finished(timestamp, turn_id=str(getattr(event, "turn_id", "") or ""))
            self._emit_stage_protocol_state(f"{event.type}_ack", data=event.to_meta())
            return

        if event.type == "stage_ack":
            self._emit_stage_protocol_state("stage_ack_received", data=event.to_meta())

    def _handle_voice_observation_event(self, event) -> None:
        """Route VAD/ASR/laughter/backchannel vào voice mechanics V12.

        VAD chỉ mở một *tentative interrupt*. Ta không được gọi
        ``interrupt_signal()`` cho mọi ``vad_start/partial/final`` vì method đó
        hard-cancel toàn bộ TTS và khiến backchannel như "ừm" làm mất audio.

        Quy tắc mới:
        - ``vad_start``: ghi nhận user bắt đầu nói, chưa hard-cancel;
        - ``partial_asr``: chỉ commit khi đã có correction marker hoặc đủ nội dung;
        - ``final_asr``: backchannel giữ audio, steering/stop mới hard-cancel;
        - ``backchannel``/``laugh``: không đụng generation hay continuation.

        ConversationKernel vẫn là nơi duy nhất tăng generation khi user turn thật
        đi vào chat ingress. Tầng voice này chỉ xử lý playback/floor cơ học.
        """

        signal = str(getattr(event, "type", "") or "").strip().lower()
        text = str(getattr(event, "message", "") or "").strip()
        observed_at = float(getattr(event, "timestamp", time.time()) or time.time())
        data = getattr(event, "data", {})
        if not isinstance(data, Mapping):
            data = {}
        try:
            confidence = float(data.get("confidence", 0.0) or 0.0)
        except (TypeError, ValueError):
            confidence = 0.0

        decision = None
        manager = self._ensure_voice_manager()
        try:
            if signal == "vad_start":
                decision = manager.on_vad_start(timestamp=observed_at)
            elif signal == "partial_asr":
                decision = manager.on_asr_partial(
                    text,
                    confidence=confidence,
                    timestamp=observed_at,
                )
            elif signal == "final_asr":
                decision = manager.on_asr_final(
                    text,
                    confidence=confidence or 1.0,
                    timestamp=observed_at,
                )
            elif signal in {"backchannel", "laugh"}:
                # Frontend đã phân loại đây là tín hiệu nghe/biểu cảm. Không biến
                # nó thành barge-in; chỉ đóng tentative VAD nếu còn mở.
                manager.on_vad_end()
        except Exception as exc:
            self.logger.debug("[voice-event] tentative interrupt handling failed: %s", exc)

        if decision is not None and bool(getattr(decision, "committed", False)):
            clock = getattr(self, "interaction_clock", None)
            if clock is not None:
                try:
                    clock.mark_interrupt(observed_at)
                except Exception:
                    pass

        try:
            from runtime.observability import GLOBAL_OBSERVABILITY
            GLOBAL_OBSERVABILITY.increment(
                "eris_voice_floor_signals_total",
                tags={
                    "signal": signal,
                    "strategy": str(getattr(decision, "strategy", "") or "none"),
                },
                fields={
                    "has_text": bool(text),
                    "committed": bool(getattr(decision, "committed", False)),
                    "cancelled_audio": bool(getattr(decision, "cancelled_audio", False)),
                    "turn_id": str(getattr(event, "turn_id", "") or ""),
                },
            )
        except Exception:
            pass
        self._emit_stage_protocol_state(f"{signal}_ack", data=event.to_meta())

    def _handle_audio_playback_aborted(self, event) -> None:
        aborted_at = float(getattr(event, "timestamp", time.time()) or time.time())
        reason = str(getattr(event, "reason", "") or "frontend_playback_aborted")
        clock = getattr(self, "interaction_clock", None)
        if clock is not None:
            try:
                clock.mark_interrupt(aborted_at)
            except Exception:
                pass
        self._emit_stage_protocol_state("audio_playback_aborted_ack", reason=reason, data=event.to_meta())

    def _handle_stage_interrupt_event(self, event) -> None:
        clock = getattr(self, "interaction_clock", None)
        if clock is not None:
            clock.mark_interrupt(float(getattr(event, "timestamp", time.time()) or time.time()))
        text = str(getattr(event, "message", "") or "").strip()
        interrupt_at = float(getattr(event, "timestamp", time.time()) or time.time())
        if text:
            self._capture_speech_interrupt_for_user(text, timestamp=float(getattr(event, "timestamp", time.time()) or time.time()))
        else:
            # Voice-start interrupt chưa có transcript: stage layer chỉ dừng/duck
            # playback để nhường user. Khi final text đi vào inbound chat,
            # ConversationKernel mới tăng generation và hủy continuation lease.
            try:
                self._ensure_voice_manager().interrupt_signal(
                    reason=str(getattr(event, "reason", "") or "frontend_interrupt")
                )
            except Exception as exc:
                self.logger.debug("[stage-protocol] interrupt cancel failed: %s", exc)
        self._emit_stage_protocol_state("interrupt_ack", reason=str(getattr(event, "reason", "") or ""), data=event.to_meta())

    def _track_frontend_audio_event(self, event) -> None:
        """Cho frontend playback event cập nhật ledger nếu frontend là nguồn phát."""

        legacy_type = "audio_start" if event.type == "audio_playback_started" else "audio_done"
        try:
            self._ensure_voice_manager().track_tts_event(
                SimpleNamespace(
                    type=legacy_type,
                    turn_id=str(getattr(event, "turn_id", "") or ""),
                    segment_id=int(getattr(event, "seq", 0) or 0),
                    text=str(getattr(event, "message", "") or ""),
                    turn_segments=(),
                ),
                stage_frame=None,
                timestamp=float(getattr(event, "timestamp", time.time()) or time.time()),
            )
        except Exception as exc:
            self.logger.debug("[stage-protocol] frontend audio track failed: %s", exc)

    def _emit_stage_protocol_state(self, event_type: str, *, reason: str = "", data: dict | None = None) -> None:
        try:
            bus = getattr(self, "stage_event_bus", None)
            if bus is not None:
                bus.emit(stage_state_event(event_type=event_type, reason=reason, data=data or {}))
        except Exception as exc:
            self.logger.debug("[stage-protocol] state emit failed: %s", exc)

    def _handle_debug_command(self, msg: dict) -> None:
        """Chạy lệnh debug (scenario preset) từ panel DEBUG của Godot.

        Panel gửi ``{"type":"debug_command","command":"reset all; stage rooted; ..."}``.
        Lệnh đi qua :class:`DebugConsole`, ép thẳng vào state sống của coordinator
        nên cycle kế tiếp khởi đầu từ trạng thái mới (engine vẫn decay sau đó).
        """
        command = str(msg.get("command") or "").strip()
        if not command:
            self.logger.debug("[debug_command] lệnh rỗng, bỏ qua")
            return
        console = getattr(self, "_debug_console", None)
        if console is None:
            from runtime.debug_console import DebugConsole

            console = DebugConsole(self)
            self._debug_console = console
        ok = True
        result = ""
        try:
            result = console.execute(command)
            self.logger.info("[debug_command] %s -> %s", command, result)
        except Exception as exc:  # pragma: no cover - debug surface không được crash runtime
            ok = False
            result = str(exc)
            self.logger.warning("[debug_command] '%s' lỗi: %s", command, exc)
        try:
            # Gửi ack im lặng để panel DEBUG biết lệnh vừa chạy xong.
            self.transport.send_json(
                {
                    "parse_mode": "debug_command_ack",
                    "should_speak": False,
                    "animation": "",
                    "debug_command_ack": {
                        "command": command[:240],
                        "ok": ok,
                        "result": str(result or "")[:500],
                    },
                }
            )
        except Exception as exc:  # pragma: no cover - ack debug không được làm rơi runtime
            self.logger.debug("[debug_command] không gửi được ack: %s", exc)

    def _handle_identity_control(self, msg: dict) -> None:
        """Typed administration surface for the Identity panel.

        This replaces the old habit of routing normal panel buttons through the
        free-form debug console.  The Emotion panel may keep its debug terminal,
        but Identity controls now have a small, explicit protocol and an ACK.
        """
        action = str(msg.get("action") or "").strip().lower()
        request_id = str(msg.get("request_id") or "")[:120]
        ok = True
        result = ""
        data: dict[str, object] = {}
        try:
            reducer = getattr(self, "ai_state_reducer", None)
            if action == "set_learning":
                if reducer is None:
                    raise RuntimeError("identity reducer chưa sẵn sàng")
                from runtime.ai_orchestrated.contracts import StateEvent

                enabled = bool(msg.get("enabled", True))
                snapshot = reducer.snapshot()
                event = StateEvent(
                    kind="admin_control",
                    turn_seq=snapshot.last_applied_turn_seq,
                    turn_id=f"identity-control-learning-{int(time.time() * 1000)}",
                    payload={"action": "set_learning", "enabled": enabled},
                    base_version=snapshot.version,
                    source="identity_panel",
                )
                reducer.apply_now(event)
                result = "learning=normal" if enabled else "learning=observe_only"
                data = {"enabled": enabled}
            elif action == "snapshot":
                if reducer is None:
                    raise RuntimeError("identity reducer chưa sẵn sàng")
                consolidator = getattr(self, "ai_identity_consolidator", None)
                if consolidator is None:
                    raise RuntimeError("identity consolidator chưa sẵn sàng")
                write_result = consolidator.write(reducer.snapshot(), reason="identity_panel")
                ok = bool(write_result.get("ok"))
                result = str(write_result.get("path") or write_result.get("error") or "snapshot failed")
                data = {"path": str(write_result.get("path") or "")}
            elif action == "reset_discovery":
                runtime = getattr(self, "ai_discovery_runtime", None)
                if runtime is None:
                    raise RuntimeError("discovery runtime chưa sẵn sàng")
                runtime.reset()
                result = "discovery archive đã reset"
            elif action in {"reset_mood", "reset_relationship"}:
                if reducer is None:
                    raise RuntimeError("identity reducer chưa sẵn sàng")
                from runtime.ai_orchestrated.contracts import StateEvent

                snapshot = reducer.snapshot()
                event = StateEvent(
                    kind="admin_control",
                    turn_seq=snapshot.last_applied_turn_seq,
                    turn_id=f"identity-control-{action}-{int(time.time() * 1000)}",
                    payload={"action": action},
                    base_version=snapshot.version,
                    source="identity_panel",
                )
                reducer.apply_now(event)
                result = action
            else:
                raise ValueError(f"identity_control action không hợp lệ: {action or '(rỗng)'}")
        except Exception as exc:
            ok = False
            result = str(exc)
            self.logger.warning("[identity_control] action=%s failed: %s", action, exc)

        try:
            self.transport.send_json(
                {
                    "type": "identity_control_ack",
                    "parse_mode": "identity_control_ack",
                    "should_speak": False,
                    "identity_control_ack": {
                        "request_id": request_id,
                        "action": action,
                        "ok": ok,
                        "result": str(result or "")[:500],
                        "data": data,
                    },
                }
            )
        except Exception as exc:  # pragma: no cover - ACK không được làm rơi runtime
            self.logger.debug("[identity_control] không gửi được ack: %s", exc)

    def _handle_anchor_control(self, msg: dict) -> None:
        """Tác_Giả bật/tắt trục tối từ panel Godot (R5.5, R10.4).

        Gọi thẳng AuthorControl trên anchor_system của private mind loop. Đổi xong
        thì digest kế tiếp tự phản ánh trạng thái mới (panel đọc switches live).
        """
        loop = getattr(self, "private_mind_loop", None)
        sysm = getattr(loop, "anchor_system", None)
        author = getattr(sysm, "author", None) if sysm is not None else None
        if author is None:
            self.logger.debug("[anchor_control] no anchor_system available")
            return
        action = str(msg.get("action") or "").strip()
        value = bool(msg.get("value", True))
        try:
            if action == "shadow":
                author.enable_shadow(value)
            elif action == "deception":
                author.enable_deception(value)
            elif action == "strict_grounding":
                author.set_strict_grounding(value)
            elif action == "safe_mode":
                if value:
                    author.disable_dark()
                else:
                    author.set_strict_grounding(False)
            elif action == "reset_all":
                author.reset_all()
            else:
                self.logger.debug("[anchor_control] unknown action=%s", action)
                return
            self.logger.info("[anchor_control] %s -> %s (safe_mode=%s)", action, value, author.is_safe_mode)
        except Exception as exc:
            self.logger.warning("[anchor_control] %s failed: %s", action, exc)

    def _handle_depth_control(self, msg: dict) -> None:
        """Bật/tắt cờ chiều-sâu (N1–N3) từ tab Panel trong context menu Godot.

        Ghi control-file ``memory_core/depth_flags.json`` (qua cortex.depth_flags);
        các helper cờ đọc file đó mỗi lần gọi nên bật/tắt ăn ngay, không restart.
        """
        flag = str(msg.get("flag") or "").strip()
        value = bool(msg.get("value", True))
        try:
            from cortex.depth_flags import KNOWN_FLAGS, set_flags

            if flag not in KNOWN_FLAGS:
                self.logger.debug("[depth_control] unknown flag=%s", flag)
                return
            set_flags({flag: value})
            self.logger.info("[depth_control] %s -> %s", flag, value)
        except Exception as exc:
            self.logger.warning("[depth_control] %s failed: %s", flag, exc)

    def _build_depth_v2_block(self, *, private_mind_digest: dict[str, object] | None = None) -> dict[str, object]:
        """Compact v2 contract for the replacement depth panel.

        Built from private_mind as the primary source, with lightweight live
        supplements for expression, semantic-frame reading, flags, and a tiny affect history.
        """
        try:
            from cortex.depth_flags import all_states
        except Exception:
            flags = {}
        else:
            flags = all_states()

        try:
            rich = self._cached_panel_state()
        except Exception:
            rich = {}
        identity = rich.get("identity") if isinstance(rich, dict) and isinstance(rich.get("identity"), dict) else {}

        try:
            from runtime.depth_v2_state import build_depth_v2_state

            from runtime.conversation.projection import legacy_semantic_projection
            semantic_frame = legacy_semantic_projection(self)
            if semantic_frame:
                cognitive_read = (
                    f"act={semantic_frame.get('user_act', 'unknown')}; "
                    f"relation={semantic_frame.get('discourse_relation', 'unknown')}; "
                    f"target={semantic_frame.get('target', 'unknown')}; "
                    f"owner={semantic_frame.get('ambiguity_owner', 'unknown')}"
                )
            else:
                cognitive_read = ""
            block = build_depth_v2_state(
                private_mind_digest=private_mind_digest if isinstance(private_mind_digest, dict) else {},
                flags=flags,
                expression_gate=getattr(self, "_last_expression_gate", None),
                tom_read=cognitive_read,
                identity=identity,
                affect_history=getattr(self, "_depth_v2_affect_history", None),
            )
        except Exception:
            block = {
                "version": 2,
                "ts": round(time.time(), 3),
                "identity": identity,
                "status": {"headline": "listening", "subtext": "depth_v2 unavailable", "focus_mode": "attentive"},
                "affect": {"headline": "steady", "subtext": "fallback", "orbit": [], "metrics": {}},
                "tom": {"live": False, "text": "", "confidence": 0.0},
                "expression": {"want": 0.0, "suppress": 0.0, "allowed": 0.0, "stance": ""},
                "priorities": [],
                "interiority": {"sections": []},
                "n2": {"live": False, "axes": [], "sentence": "", "attachment": {"live": False, "value": 0.0}},
                "journal": [],
                "signal_log": [],
                "context": {"tags": [], "focus_mode": "attentive"},
                "flags": flags,
                "affect_history": list(getattr(self, "_depth_v2_affect_history", []) or []),
            }
        self._depth_v2_affect_history = list(block.get("affect_history", []) or [])
        return block

    def _cached_panel_state(self, *, ttl_seconds: float = 1.0) -> dict[str, object]:
        """build_panel_state() có cache ngắn — payload UDP gọi rất dày."""
        now = time.time()
        cache = getattr(self, "_panel_state_cache", None)
        if isinstance(cache, tuple) and (now - cache[0]) < ttl_seconds:
            return cache[1]
        try:
            from runtime.panel_state import build_panel_state

            state = build_panel_state()
        except Exception:
            state = {}
        self._panel_state_cache = (now, state)
        return state
