# -*- coding: utf-8 -*-
"""The single high-level adapter allowed to commit and publish assistant speech."""
from __future__ import annotations

import json
import threading
from typing import Any

from .kernel import KernelTurn
from .writer import RenderedConversationMove
from runtime.json_boundary import public_payload


class RuntimeConversationDeliveryAdapter:
    def __init__(self, host: Any) -> None:
        self.host = host
        self._lock = threading.RLock()
        self._started: set[str] = set()

    def deliver(
        self,
        *,
        turn: KernelTurn,
        rendered: RenderedConversationMove,
        delivery_id: str,
    ) -> None:
        did = str(delivery_id or "")[:180]
        if not did:
            raise ValueError("delivery_id_required")
        with self._lock:
            if did in self._started:
                self._log("warning", "[delivery-adapter] deliver() called twice for same did=%s — idempotency guard fired, skipping body send", did)
                return
            self._started.add(did)

        status_only = bool(rendered.metadata.get("runtime_status_only"))
        prepared = rendered.metadata.get("prepared_public_output")
        if prepared is not None:
            from .public_output_artifact import (
                PreparedPublicOutput, lock_prepared_segments, validate_prepared,
            )
            if not isinstance(prepared, PreparedPublicOutput):
                raise RuntimeError("v03_prepared_wrong_type")
            validate_prepared(prepared)
            if (prepared.session_id != turn.session_id or
                    prepared.generation != turn.generation or
                    prepared.run_id != turn.run_id or
                    prepared.move_id != turn.move.move_id or
                    prepared.delivery_id != did):
                raise RuntimeError('public_artifact_turn_mismatch')
            # State nguồn không được bị thay lén giữa prepare và dispatch.
            registry = rendered.metadata.get("evidence_registry")
            if registry is not None and getattr(registry, "fingerprint", None) != prepared.source_fingerprint:
                raise RuntimeError("v03_source_changed_before_dispatch")
            prepared = lock_prepared_segments(prepared)
            decision = prepared.decision
            channel = prepared.channel
            display_text, memory_text = prepared.display_text, prepared.memory_text
            event_kind = str(rendered.metadata.get("event_kind", "") or "")
            semantic_owner = "shared" if channel == "chat" else "eris"
            trigger_owner = "user" if channel == "chat" else (
                "external" if event_kind in {"work_result", "research_result", "tool_result"} else "eris")
            causal_receipt = None
        else:
            # Writer giữ typed objects (_typed_semantic_frame, SemanticFrame, ...) để
            # cognition nội bộ dùng lại. Parser/body là JSON boundary nên phải hạ domain
            # object về primitive và bỏ cache _typed_* trước json.dumps.
            payload = public_payload(rendered.metadata.get("provider_payload", {}) or {})
            payload["text"] = rendered.surface.display_text
            if rendered.surface.speech_text:
                payload["speech_text"] = rendered.surface.speech_text
            parser = getattr(self.host, "parser", None)
            parse = getattr(parser, "parse", None)
            if not callable(parse):
                self._log("warning", "[delivery-adapter] parser unavailable, cannot deliver did=%s", did)
                raise RuntimeError("public_decision_parser_unavailable")
            raw = json.dumps(payload, ensure_ascii=False)
            try:
                decision = parse(raw, user_hint=str(rendered.metadata.get("user_text", "") or ""))
            except TypeError:
                decision = parse(raw)

            decision.text = rendered.surface.display_text
            decision.speech_text = rendered.surface.speech_text
            decision.display_language = rendered.surface.language
            decision.should_speak = bool(rendered.surface.display_text.strip())
            decision.utterance_turn_id = did
            if not str(getattr(decision, "delivery_hint", "") or "").strip():
                decision.delivery_hint = turn.move.delivery
            decision.performance_context = dict(getattr(decision, "performance_context", {}) or {})
            channel = self._channel_for(rendered)
            event_kind = str(rendered.metadata.get("event_kind", "") or "")
            semantic_owner = "shared" if channel == "chat" else "eris"
            trigger_owner = "user" if channel == "chat" else (
                "external" if event_kind in {"work_result", "research_result", "tool_result"} else "eris"
            )
            decision.performance_context.update({
                "conversation_session_id": turn.session_id,
                "conversation_generation": turn.generation,
                "conversation_run_id": turn.run_id,
                "conversation_move_id": turn.move.move_id,
                # Cùng một id phải đi xuyên public commit -> TTS -> outcome để trace
                # không còn kết thúc bằng semantic/trigger=unknown.
                "event_trace_id": did,
                "public_intent_owner": "runtime_status" if status_only else "conversation_root_agent_v12",
                "semantic_owner": semantic_owner,
                "trigger_owner": trigger_owner,
            })

            causal_receipt = None
            try:
                from runtime.public_causal_receipt import build_public_causal_receipt

                causal_receipt = build_public_causal_receipt(
                    channel=channel,
                    semantic_owner=semantic_owner,
                    trigger_owner=trigger_owner,
                    source_kind="conversation_kernel_v12",
                    source_ref=turn.move.move_id,
                    motive=turn.move.intent,
                    why_now=event_kind or "conversation_event",
                    user_triggered=(channel == "chat"),
                    confidence=1.0,
                )
                decision.performance_context["public_causal_receipt"] = causal_receipt.to_dict()
            except Exception as exc:
                self._log("debug", "[conversation-v12] causal receipt degraded: %s", exc)

            stage = getattr(self.host, "ai_stage_planner", None)
            apply_stage = getattr(stage, "apply", None)
            if callable(apply_stage):
                try:
                    state_runtime = getattr(self.host, "ai_state_reducer", None)
                    snapshot = getattr(state_runtime, "snapshot", None)
                    state = snapshot() if callable(snapshot) else None
                    apply_stage(decision, turn.move.delivery or "neutral", state=state, channel=channel)
                except Exception as exc:
                    self._log("debug", "[conversation-v12] stage projection degraded: %s", exc)

            display_text = str(rendered.surface.display_text or "").strip()
            if not display_text:
                raise RuntimeError("public_delivery_empty")
            memory_text = display_text
            try:
                from runtime.public_surface_commit import GLOBAL_PUBLIC_SURFACE_COMMITTER

                surfaces = GLOBAL_PUBLIC_SURFACE_COMMITTER.build(display_text)
                display_text = surfaces.display_text
                memory_text = surfaces.memory_text
                decision.text = display_text
            except Exception as exc:
                self._log("debug", "[conversation-v12] surface commit degraded: %s", exc)

            # Không đưa semantic grounding classifier lên critical path.
            # Báo cáo nguồn vẫn giữ trong metadata để chạy test và sửa core.

        # RÀO CUỐI sau Stage/localize: writer hoặc adapter khác không được
        # phát lời tự nhận đã hoàn tất action mà không có receipt của đúng run.
        # Không dựa vào claim_manifest, không gọi LLM phụ kiểm duyệt cá tính.
        from .action_claim_integrity import enforce_completed_action_claims
        from .public_output_artifact import tts_clean
        spoken_surfaces = ((prepared.display_text, prepared.memory_text,
                            *(tts_clean(seg.tts_text) for seg in prepared.segments))
                           if prepared is not None else
                           (display_text, memory_text, str(getattr(decision, 'speech_text', '') or '')))
        enforce_completed_action_claims(
            surfaces=spoken_surfaces,
            receipts=rendered.metadata.get('action_claim_receipts', ()),
            event_id=str(rendered.metadata.get('event_id', '') or ''),
            session_id=turn.session_id, run_id=turn.run_id,
            generation=turn.generation,
        )

        kernel = getattr(self.host, "conversation_kernel", None) or getattr(self.host, "_conversation_kernel", None)
        store = getattr(kernel, "store", None)
        if store is not None:
            state = store.load(turn.session_id)
            if (state.generation != turn.generation or state.active_run_id != turn.run_id
                    or state.active_delivery_id != did):
                raise RuntimeError("v03_preempted_before_dispatch" if prepared is not None
                                   else "public_preempted_before_dispatch")
        send = getattr(self.host, "_send_decision_to_body", None)
        if not callable(send):
            self._log("warning", "[delivery-adapter] _send_decision_to_body not available on host, cannot deliver did=%s", did)
            raise RuntimeError("public_transport_unavailable")
        if prepared is not None:
            from runtime.speech_contract import configured_speech_language, normalize_language
            language = configured_speech_language()
            if any(
                normalize_language(str(frame.speech_contract.get("language", language))) != language
                for frame in prepared.segments
            ):
                raise RuntimeError("v03_speech_configuration_changed_before_dispatch")
            kernel = getattr(self.host, "conversation_kernel", None) or getattr(self.host, "_conversation_kernel", None)
            store = getattr(kernel, "store", None)
            if store is not None:
                state = store.load(turn.session_id)
                if (state.generation != turn.generation or state.active_run_id != turn.run_id
                        or state.active_delivery_id != did):
                    raise RuntimeError("v03_preempted_before_dispatch")
            # Gửi artifact đã niêm phong; không có semantic verifier receipt.
            canvas = getattr(self.host, "learning_canvas_service", None)
            if canvas is not None and turn.canvas_scope:
                packet = canvas.playback.register(did, turn.generation, turn.canvas_scope,
                    {f.segment_id: f.delivery.get("learning_canvas", {}).get("block_id", "") for f in prepared.segments},
                    session_id=turn.session_id)
                if packet:
                    self.host._send_runtime_json(packet, channel="learning_canvas")
            accepted = send(decision, channel=channel, prepared=prepared)
        else:
            accepted = send(decision, channel=channel)
        # Runtime production trả bool; test/adapter cũ trả None và được coi là
        # accepted để giữ compatibility. False nghĩa stage ownership đã suppress.
        if accepted is False:
            raise RuntimeError("public_transport_suppressed")
        # Side-effect choreography chỉ sau khi transport đã nhận hợp lệ,
        # không khi prepare/rewrite/preempt/fail/unknown.
        apply_position = getattr(self.host, "_apply_position_update", None)
        if callable(apply_position):
            apply_position(decision)

        # Status kỹ thuật không phải ký ức, belief hoặc speech-act của Eris.
        # Chỉ đăng ký receipt để TTS settle; không ghi history/Self.
        if status_only:
            from .speech_outcome import register_public_surface
            register_public_surface(self.host, delivery_id=did, on_delivered=lambda: None)
            return

        # Chỉ từ đây trở xuống mới là PUBLIC COMMIT thật. Trước điểm này tuyệt
        # đối không append history/ledger hay update self-state.
        assistant_turn = None
        if memory_text:
            append_history = getattr(self.host, "_append_chat_history", None)
            if callable(append_history):
                append_history("assistant", memory_text, invalid=False, pending_delivery_id=did)
        ledger_factory = getattr(self.host, "_ensure_session_turn_ledger", None)
        if callable(ledger_factory):
            assistant_turn = ledger_factory().record_assistant(
                display_text,
                meta={
                    "schema": "eris.conversation_delivery.v12",
                    "channel": channel,
                    "delivery_id": did,
                    "generation": turn.generation,
                    "run_id": turn.run_id,
                    "next_conversation_move": turn.move.to_dict(),
                    "delivery_status": "pending",  # Chưa có terminal TTS receipt.
                },
            )

        # Đăng ký receipt chỉ SAU khi commit history/ledger. Nếu TTS no_tts
        # callback hoàn tất đồng bộ trong send(), helper sẽ áp receipt đã đến sớm.
        # Side effect Observation / living-memory chỉ được ghi khi thực sự
        # phát xong (hoặc no_tts), không phải khi audio mới bắt đầu buffer.
        # Chỉ giữ primitive cho callback bất đồng bộ; không bắt closure mang
        # toàn bộ rendered/turn/registry (có thể rất lớn) trong lúc TTS buffer.
        delivered_user_text = str(rendered.metadata.get("user_text", "") or "")[:2000]
        delivered_speech_act = str(getattr(decision, "speech_act", "") or turn.move.intent or "public_commit")[:240]
        delivered_turn_index = int(getattr(assistant_turn, "index", 0) or 0)
        delivered_choice = dict(turn.move.interaction_choice)
        delivered_session_id = str(turn.session_id)
        delivered_goal = str(turn.move.intent)

        def _on_delivered() -> None:
            journal = getattr(self.host, "_journal_conversation_turn", None)
            if callable(journal) and memory_text:
                try:
                    journal("assistant", memory_text)
                except Exception as exc:
                    self._log("debug", "[delivery-adapter] memory journal degraded: %s", exc)
            try:
                from runtime.conversation.observation_bridge import ensure_conversation_observation_bridge
                history = tuple(getattr(self.host, "chat_history", ()) or ())[-12:]
                ensure_conversation_observation_bridge(self.host).observe_assistant_commit(
                    memory_text or display_text,
                    turn_ref=did,
                    turn_index=delivered_turn_index,
                    speech_act=delivered_speech_act,
                    event_kind=event_kind or channel,
                    causal_receipt=causal_receipt,
                    recent_history=tuple(item for item in history if isinstance(item, dict)),
                    user_text=delivered_user_text,
                    interaction_choice=delivered_choice,
                    session_id=delivered_session_id,
                    choice_goal=delivered_goal,
                )
            except Exception as exc:
                self._log("debug", "[conversation-v12] delivered observation degraded: %s", exc)

        from .speech_outcome import register_public_surface
        register_public_surface(self.host, delivery_id=did, on_delivered=_on_delivered)

        self.host.last_text = display_text
        self.host.last_chat_answer = display_text

        # Gắn capability invocation vào đúng delivery để terminal TTS receipt
        # chỉ commit domain state của capability thực sự được gọi ở lượt này.
        try:
            pending = getattr(self.host, "_v12_delivery_capabilities", None)
            if not isinstance(pending, dict):
                pending = {}
                self.host._v12_delivery_capabilities = pending
            used = tuple(str(x) for x in tuple(rendered.metadata.get("root_capabilities_used", ()) or ()) if str(x))
            pending[did] = {
                "capabilities": used,
                "session_id": turn.session_id,
                "generation": turn.generation,
                "move_id": turn.move.move_id,
            }
            if "tutor" in used:
                tutor = getattr(self.host, "tutor_capability", None)
                binder = getattr(tutor, "bind_pending_delivery", None)
                if callable(binder):
                    binder(
                        event_id=str(rendered.metadata.get("event_id") or ""),
                        session_id=turn.session_id, generation=turn.generation,
                        delivery_id=did, move_id=turn.move.move_id,
                        artifact_bundle_ids=turn.move.tool_artifact_bundles,
                    )
            if len(pending) > 128:
                for stale in list(pending)[:-128]:
                    pending.pop(stale, None)
        except Exception as exc:
            self._log("debug", "[conversation-v12] capability receipt registration degraded: %s", exc)

        try:
            from runtime.ai_orchestrated.autonomous_trace import append_public_turn_trace
            append_public_turn_trace("conversation_move", {
                "trace_id": did,
                "turn_id": did,
                "channel": channel,
                "semantic_owner": semantic_owner,
                "trigger_owner": trigger_owner,
                "source_kind": "conversation_kernel_v12",
                "source_ref": turn.move.move_id,
                "goal_state": turn.move.intent,
                "proposal_source": "root_agent",
                "proposal_kind": "next_conversation_move",
                "authority_action": "commit",
                "authority_reason": "root_agent_finalized_move",
                "authority_owner": "conversation_root_agent_v12",
                "motive": turn.move.intent,
                "public_committed": True,
            }, logger=getattr(self.host, "logger", None))
        except Exception as exc:
            self._log("debug", "[conversation-v12] public trace provenance degraded: %s", exc)

    def prepare_candidate(self, *, turn: KernelTurn, event: Any,
                          payload: dict[str, Any], source_fingerprint: str):
        """Task 4: parse, stage, split CHỈ THUẦN DỮ LIỆU, không phát/commit."""
        from .public_output_artifact import prepare_public_output
        from runtime.json_boundary import public_payload
        clean = public_payload(payload)
        clean["text"] = str(payload.get("text", "") or "")
        clean["speech_text"] = str(payload.get("speech_text", "") or "")
        parse = getattr(getattr(self.host, "parser", None), "parse", None)
        if not callable(parse):
            raise RuntimeError("v03_parser_unavailable")
        raw = json.dumps(clean, ensure_ascii=False)
        try:
            decision = parse(raw, user_hint=str(event.text or ""))
        except TypeError:
            decision = parse(raw)
        decision.text = clean["text"]
        decision.speech_text = clean["speech_text"]
        decision.display_language = str(payload.get("display_language", "vi") or "vi")
        decision.should_speak = bool(decision.text.strip())
        decision.utterance_turn_id = turn.delivery_id
        if not str(getattr(decision, "delivery_hint", "") or "").strip():
            decision.delivery_hint = turn.move.delivery
        decision.performance_context = dict(getattr(decision, "performance_context", {}) or {})
        channel = "chat" if event.kind == "user_turn" else "autonomous"
        decision.performance_context.update({
            "conversation_session_id":turn.session_id,
            "conversation_generation":turn.generation,
            "conversation_run_id":turn.run_id,
            "conversation_move_id":turn.move.move_id,
            "event_trace_id":turn.delivery_id,
            "public_intent_owner":"conversation_root_agent_v12",
            "semantic_owner":"shared" if channel == "chat" else "eris",
            "trigger_owner":"user" if channel == "chat" else "eris",
        })
        # apply_stage chỉ được đổi Decision candidate; không gọi dispatch.
        apply_stage = getattr(getattr(self.host,"ai_stage_planner",None),"apply",None)
        if callable(apply_stage):
            reducer = getattr(getattr(self.host,"ai_state_reducer",None),"snapshot",None)
            state = reducer() if callable(reducer) else None
            apply_stage(decision,turn.move.delivery or "neutral",state=state,channel=channel)
        clean["user_text"] = str(getattr(self.host,"current_stage_user_text","") or "") if channel == "chat" else ""
        prepared = prepare_public_output(event_id=event.event_id,turn=turn,channel=channel,
                                     decision=decision,payload=clean,
                                     source_fingerprint=source_fingerprint)
        canvas = getattr(self.host, "learning_canvas_service", None)
        if canvas is not None and turn.canvas_scope:
            from .canvas_narration import bind_prepared
            prepared = bind_prepared(prepared, turn.canvas_scope, canvas.pack)
        return prepared

    @staticmethod
    def _channel_for(rendered: RenderedConversationMove) -> str:
        return "chat" if str(rendered.metadata.get("event_kind", "")) == "user_turn" else "autonomous"

    def _log(self, level: str, message: str, *args: Any) -> None:
        logger = getattr(self.host, "logger", None)
        method = getattr(logger, level, None) if logger is not None else None
        if callable(method):
            try:
                method(message, *args)
            except Exception:
                pass


__all__ = ["RuntimeConversationDeliveryAdapter"]
