# -*- coding: utf-8 -*-
"""RootAgent V12: semantic authority duy nhất cho public conversation.

RootAgent hoạt động như một agent loop nhỏ:

``context -> model -> optional capability/note tool -> model -> final decision``

Điểm quan trọng là loop này không tái sinh giant planning form. Mỗi bước chỉ có
một action rất nhỏ, và public boundary cuối vẫn luôn là đúng một
``ConversationDecision = NoReply | NextConversationMove``.
"""
from __future__ import annotations

from typing import Any, Mapping, Protocol, Sequence
from dataclasses import replace
from copy import deepcopy
import json
import os
import uuid

from .root_prompt_contract import ROOT_SYSTEM_CORE, SOCIAL_JUDGMENT_PROMPT, ELEGANT_SOCIAL_PROMPT
from .tool_guides import TOOL_GUIDES, read_tool_guide
from .root_session import RootConversationSession
from .interaction_choice import INTERACTION_CHOICE_PROMPT, INTERACTION_CHOICE_SCHEMA
from llm.provider_errors import provider_failure

from .context import ConversationNoteTools, RootCapabilityRegistry, RootMemoryRecallTools
from .contracts import (
    ConversationAfter,
    ConversationDecision,
    ConversationEvent,
    NextConversationMove,
    NoReply,
)


NEXT_MOVE_SCHEMA: dict[str, Any] = {
    "oneOf": [
        {
            "type": "object",
            "additionalProperties": False,
            "required": ["kind", "intent", "say", "delivery", "after"],
            "properties": {
                "kind": {"type": "string", "enum": ["move"]},
                "interaction_choice": INTERACTION_CHOICE_SCHEMA,
                "intent": {"type": "string"},
                "say": {"type": "string"},
                "delivery": {"type": "string"},
                "after": {"type": "string", "enum": ["wait_user", "may_continue", "continue_self"]},
                "document_source": {"type": "string"},
                "canvas_segments": {"type": "array", "maxItems": 6, "items": {
                    "type": "object", "additionalProperties": False, "required": ["block_id", "say"],
                    "properties": {"block_id": {"type": "string"}, "say": {"type": "string"}}}},
                "presentation_blocks": {"type": "array", "maxItems": 6, "items": {
                    "type": "object", "additionalProperties": False,
                    "required": ["kind", "content"],
                    "properties": {
                        "kind": {"type": "string", "enum": ["math", "table", "code", "chart", "document"]},
                        "title": {"type": "string"},
                        "content": {"type": "object", "additionalProperties": True},
                    },
                }},
            },
        },
        {
            "type": "object",
            "additionalProperties": False,
            "required": ["kind", "reason"],
            "properties": {
                "kind": {"type": "string", "enum": ["no_reply"]},
                "reason": {"type": "string"},
            },
        },
    ],
}


# Schema một *iteration* của root loop. Các action tool chỉ tồn tại ở cognition
# boundary; writer/runtime public không bao giờ nhìn thấy chúng.
ROOT_STEP_SCHEMA: dict[str, Any] = {
    "oneOf": [
        *NEXT_MOVE_SCHEMA["oneOf"],
        {
            "type": "object", "additionalProperties": False,
            "required": ["kind", "guide"],
            "properties": {
                "kind": {"type": "string", "enum": ["read_tool_guide"]},
                "guide": {"type": "string", "enum": list(TOOL_GUIDES)},
            },
        },
        {
            "type": "object",
            "additionalProperties": False,
            "required": ["kind", "capability", "arguments"],
            "properties": {
                "kind": {"type": "string", "enum": ["use_capability"]},
                "capability": {"type": "string", "enum": ["tutor", "work", "research", "action", "canvas"]},
                "arguments": {"type": "object", "additionalProperties": True},
            },
        },
        {
            "type": "object",
            "additionalProperties": False,
            "required": ["kind", "query"],
            "properties": {
                "kind": {"type": "string", "enum": ["recall_memory"]},
                "query": {"type": "string"},
            },
        },
        {
            "type": "object",
            "additionalProperties": False,
            "required": ["kind", "focus"],
            "properties": {
                "kind": {"type": "string", "enum": ["continue_thinking"]},
                "focus": {"type": "string"},
            },
        },
        {
            "type": "object",
            "additionalProperties": False,
            "required": ["kind", "text"],
            "properties": {
                "kind": {"type": "string", "enum": ["set_note"]},
                "text": {"type": "string"},
            },
        },
        {
            "type": "object",
            "additionalProperties": False,
            "required": ["kind"],
            "properties": {"kind": {"type": "string", "enum": ["clear_note"]}},
        },
    ],
}


# Các entry non-finalize của ROOT_STEP_SCHEMA (bỏ move/no_reply). Dùng để dựng
# nhánh batch: mỗi phần tử trong actions[] phải là MỘT trong các action nội bộ
# độc lập này, finalize không bao giờ được phép nằm trong batch.
_NON_FINALIZE_STEP_SCHEMAS: list[dict[str, Any]] = [
    schema for schema in ROOT_STEP_SCHEMA["oneOf"]
    if not set(schema["properties"]["kind"].get("enum", [])) & {"move", "no_reply"}
]


# Warm-up/session and one-shot both use this same batch-capable wire schema.
ROOT_BATCH_STEP_SCHEMA: dict[str, Any] = {
    "oneOf": [
        *ROOT_STEP_SCHEMA["oneOf"],
        {
            "type": "object",
            "additionalProperties": False,
            "required": ["kind", "actions"],
            "properties": {
                "kind": {"type": "string", "enum": ["batch"]},
                "actions": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": 4,
                    "items": {"oneOf": _NON_FINALIZE_STEP_SCHEMAS},
                },
            },
        },
    ],
}


def interaction_choices_enabled() -> bool:
    return os.getenv("ERIS_ROOT_INTERACTION_CHOICES", "0").strip().lower() not in {"0", "false", "off", "no"}


def root_step_schema() -> dict[str, Any]:
    schema = deepcopy(ROOT_BATCH_STEP_SCHEMA)
    for branch in schema["oneOf"]:
        if branch.get("properties", {}).get("kind", {}).get("enum") == ["move"]:
            if interaction_choices_enabled():
                branch["required"].append("interaction_choice")
            else:
                branch["properties"].pop("interaction_choice", None)
    return schema


class RootAgent(Protocol):
    def decide(
        self,
        *,
        event: ConversationEvent,
        recent_history: Sequence[Mapping[str, Any]],
        note: str,
        runtime_context: Mapping[str, Any],
    ) -> ConversationDecision:
        ...


class DeterministicRootAgent:
    """Fail-soft contract; không giả làm reasoning engine hoặc social planner."""

    def decide(
        self,
        *,
        event: ConversationEvent,
        recent_history: Sequence[Mapping[str, Any]],
        note: str,
        runtime_context: Mapping[str, Any],
    ) -> ConversationDecision:
        del recent_history, note, runtime_context
        if event.kind != "user_turn":
            return NoReply(reason="root_model_unavailable")
        return NextConversationMove(
            intent="Phản hồi trực tiếp lượt user hiện tại.",
            say=("Trả lời trực tiếp và hữu ích cho nội dung này: " + str(event.text or "")[:1800]),
            delivery="tự nhiên",
            after=ConversationAfter.WAIT_USER,
        )


class RuntimeModelRootAgent:
    """DeepSeek-backed root loop với capability invocation theo nhu cầu.

    ``max_root_steps`` là circuit breaker tài nguyên, không phải giới hạn văn
    phong. Nếu model loop sai, runtime dừng tool loop và dùng degraded root
    decision; không fallback về planner/social runtime cũ.
    """

    def __init__(self, router: Any, *, logger: Any = None, max_root_steps: int = 8) -> None:
        self.router = router
        self.logger = logger
        # max_root_steps là trần CỨNG TUYỆT ĐỐI: clamp 2..12, không bao giờ bỏ.
        # Default nâng 6->8 cho đủ đất khi đang thật sự tiến triển, nhưng vòng
        # còn một cơ chế cắt sớm riêng khi phát hiện không tiến triển (bên dưới).
        self.max_root_steps = max(2, min(12, int(max_root_steps or 8)))
        # Một dòng hội thoại của Root, không chia sẻ với Surface/Observer.
        # Self gốc vẫn nằm trong reducer/SubjectService; đây chỉ là remote cache.
        self._root_session = RootConversationSession(logger=logger)

    def preload_self(self, host: Any) -> None:
        """Đăng ký warm-up lúc startup, không chờ DeepSeek trên main thread."""
        self._root_session.start(
            host=host, router=self.router,
            system=self._system_prompt(RootCapabilityRegistry({})),
            schema=root_step_schema(),
        )

    def decide(
        self,
        *,
        event: ConversationEvent,
        recent_history: Sequence[Mapping[str, Any]],
        note: str,
        runtime_context: Mapping[str, Any],
    ) -> ConversationDecision:
        client = (
            getattr(self.router, "executive_client", None)
            or getattr(self.router, "deep_client", None)
            or getattr(self.router, "seed_client", None)
            or getattr(self.router, "surface_client", None)
            or getattr(getattr(self.router, "llm_service", None), "client", None)
        )
        invoke = getattr(self.router, "invoke_structured_worker", None)
        if client is None or not callable(invoke):
            return self._degraded(event, recent_history, note, runtime_context)

        note_tools = runtime_context.get("__conversation_note_tools")
        if not isinstance(note_tools, ConversationNoteTools):
            note_tools = None
        capabilities = runtime_context.get("__root_capabilities")
        if not isinstance(capabilities, RootCapabilityRegistry):
            capabilities = RootCapabilityRegistry({})
        memory_tools = runtime_context.get("__root_memory_recall_tools")
        if not isinstance(memory_tools, RootMemoryRecallTools):
            memory_tools = None
        # Kernel cấp callback này theo (session, generation, run_id). Root chỉ
        # kiểm tra tính hợp lệ cơ học, không để background kết thúc sau khi user
        # đã chen vào rồi tiếp tục gọi công cụ tốn thời gian.
        still_active = runtime_context.get("__root_run_active")
        still_active = still_active if callable(still_active) else lambda: True

        tool_results: list[dict[str, Any]] = []
        prior_actions: set[str] = set()
        # reflection_state là object mutable để continue_thinking tăng đếm dùng
        # chung giữa single-step và batch (và xuyên nhiều batch trong một event).
        reflection_state: dict[str, int] = {"count": 0}
        current_note = str(note or "")[:12000]
        system = self._system_prompt(capabilities)
        schema = root_step_schema()
        # Phát hiện 'không tiến triển': đếm số bước LIÊN TIẾP mà toàn bộ
        # tool_result sinh ra đều vô nghĩa (duplicate/empty/failed/limit_reached/
        # unavailable/invalid hoặc trùng result cùng loại ở bước trước). Trần
        # cứng vẫn giữ nguyên; đây chỉ cắt sớm khi loop quay vòng vô ích.
        no_progress_streak = 0
        last_meaningful_fingerprint: set[str] = set()

        for step_index in range(self.max_root_steps):
            if not still_active():
                return NoReply(reason="root_turn_preempted")
            collect_batch = runtime_context.get("__root_input_batch")
            if event.kind == "user_turn" and callable(collect_batch):
                batch = collect_batch(claim=True)
                metadata = batch.to_dict()
                metadata.pop("canonical_text", None)
                event = replace(event, text=batch.canonical_text,
                                metadata={**event.metadata, "user_input_batch": metadata})
                self._log("info", "[root-input] messages=%d wait_seconds=%.1f revision=%d",
                          len(batch.entries), metadata["wait_seconds"], batch.revision)
            payload = self._payload(
                event=event,
                recent_history=recent_history,
                note=current_note,
                runtime_context=runtime_context,
                tool_results=tool_results,
                step_index=step_index,
            )
            try:
                raw = self._root_session.ask(
                    router=self.router, client=client, system=system,
                    schema=schema, payload=payload,
                    runtime_context=runtime_context,
                    recent_history=recent_history,
                )
                if raw is None:
                    # Không chờ warm-up đang chạy / không hỗ trợ session /
                    # reset do lỗi. Luồng cũ trả lời ngay với context đầy đủ.
                    # Same contract as session; batch is valid in either path.
                    raw = invoke(
                        client,
                        system=system,
                        payload=payload,
                        schema=schema,
                        name="eris_root_conversation_step_v12",
                        max_tokens=4096,
                        reasoning_effort="medium",
                        inference_tier="subconscious_4b",
                        cognitive_lane="root_conversation",
                        priority=70,
                        temperature=0.3,
                    )
            except Exception as exc:  # noqa: BLE001
                failure = provider_failure(exc, role="root")
                if failure is not None:
                    # Kernel chịu trách nhiệm status-only. Không ngụy tạo
                    # quyết định của Root và không gọi lại Writer đang lỗi.
                    raise failure from exc
                self._log("debug", "[conversation-v12] root model degraded: %s", type(exc).__name__)
                return self._degraded(event, recent_history, current_note, runtime_context)

            if not still_active():
                return NoReply(reason="root_turn_preempted")
            if not isinstance(raw, Mapping):
                return self._degraded(event, recent_history, current_note, runtime_context)

            kind = str(raw.get("kind", "") or "").strip().lower()
            # Chỉ ghi LOẠI bước, không ghi focus/query/lời nghĩ riêng tư.
            # Truy vết được agent loop thật mà log không phình theo prompt.
            self._log("info", "[root-agent] step=%d event_kind=%s action=%s",
                      step_index + 1, event.kind[:48], kind[:48])
            if kind == "no_reply":
                return NoReply.from_mapping(raw)
            if kind == "move" or (event.kind == "user_turn" and (raw.get("intent") or raw.get("say"))):
                # Quan sát cadence: chỉ ghi LOẠI `after` (wait_user|may_continue|
                # continue_self), KHÔNG serialize nội dung say/intent. Dùng để thấy
                # Root có rải nhịp qua may_continue khi chạy live hay không.
                self._log("info", "[root-agent] finalize move after=%s",
                          str(raw.get("after", "wait_user")).strip().lower()[:24])
                # Finalize TRỰC TIẾP move Root đã chọn — không chạy một vòng model
                # thứ hai (self-verify) vốn làm phẳng personality. Anti-fabrication
                # vẫn do claim_manifest + grounding gate + enforce_completed_action_claims
                # ở runtime_writer lo, độc lập với self-verify. Vẫn tôn trọng preempt:
                # user chen vào giữa chừng thì trả NoReply('root_turn_preempted').
                if not still_active():
                    return NoReply(reason="root_turn_preempted")
                from ..workspace.presentation_p2 import verified_tool_bundles
                return replace(NextConversationMove.from_mapping(raw),
                               tool_artifact_bundles=verified_tool_bundles(tool_results))

            if kind == "batch":
                # Nhiều action nội bộ ĐỘC LẬP trong một bước. Bỏ phần tử finalize
                # (phòng thủ dù schema đã cấm), giới hạn 4 phần tử đầu.
                raw_actions = raw.get("actions")
                actions = [
                    a for a in (raw_actions if isinstance(raw_actions, Sequence) else [])
                    if isinstance(a, Mapping)
                    and str(a.get("kind", "") or "").strip().lower() not in {"move", "no_reply"}
                ][:4]
                step_results: list[dict[str, Any]] = []
                for action in actions:
                    # Preempt giữa chừng: user đã chen vào thì dừng ngay, không
                    # chạy nốt các action còn lại trong batch.
                    if not still_active():
                        return NoReply(reason="root_turn_preempted")
                    result = self._run_single_action(
                        action,
                        step_index=step_index,
                        event=event,
                        recent_history=recent_history,
                        runtime_context=runtime_context,
                        capabilities=capabilities,
                        memory_tools=memory_tools,
                        note_tools=note_tools,
                        prior_actions=prior_actions,
                        reflection_state=reflection_state,
                    )
                    if result is not None:
                        tool_results.append(result)
                        step_results.append(result)
                        if result.get("kind") == "note_result":
                            # Note là state dùng lại ở payload lượt sau; đồng bộ
                            # bản cache cục bộ theo operation.
                            if result.get("operation") == "set" and result.get("ok"):
                                current_note = str(action.get("text", "") or "").strip()[:12000]
                            elif result.get("operation") == "clear" and result.get("ok"):
                                current_note = ""
                # Cập nhật progress-streak cho cả batch rồi mới quyết cắt sớm.
                early = self._advance_progress_and_maybe_finalize(
                    step_results=step_results,
                    last_meaningful_fingerprint=last_meaningful_fingerprint,
                    streak_ref=no_progress_streak,
                )
                no_progress_streak = early["streak"]
                if early["stop"]:
                    return self._no_progress_decision(
                        event, recent_history, current_note, runtime_context)
                # batch KHÔNG tự finalize: quay lại vòng model kế với toàn bộ
                # tool_results mới.
                continue

            result = self._run_single_action(
                raw,
                step_index=step_index,
                event=event,
                recent_history=recent_history,
                runtime_context=runtime_context,
                capabilities=capabilities,
                memory_tools=memory_tools,
                note_tools=note_tools,
                prior_actions=prior_actions,
                reflection_state=reflection_state,
            )
            if result is None:
                self._log("debug", "[conversation-v12] unknown root step kind=%r", kind)
                break
            tool_results.append(result)
            if result.get("kind") == "note_result":
                if result.get("operation") == "set" and result.get("ok"):
                    current_note = str(raw.get("text", "") or "").strip()[:12000]
                elif result.get("operation") == "clear" and result.get("ok"):
                    current_note = ""
            early = self._advance_progress_and_maybe_finalize(
                step_results=[result],
                last_meaningful_fingerprint=last_meaningful_fingerprint,
                streak_ref=no_progress_streak,
            )
            no_progress_streak = early["streak"]
            if early["stop"]:
                return self._no_progress_decision(
                    event, recent_history, current_note, runtime_context)
            continue

        self._log("warning", "[conversation-v12] root step circuit breaker reached")
        return self._degraded(event, recent_history, current_note, runtime_context)

    def _run_single_action(
        self,
        raw: Mapping[str, Any],
        *,
        step_index: int,
        event: ConversationEvent,
        recent_history: Sequence[Mapping[str, Any]],
        runtime_context: Mapping[str, Any],
        capabilities: RootCapabilityRegistry,
        memory_tools: Any,
        note_tools: Any,
        prior_actions: set[str],
        reflection_state: dict[str, int],
    ) -> dict[str, Any] | None:
        """Thực thi MỘT action nội bộ non-finalize, trả về tool_result dict.

        Dùng chung cho single-step và batch nên dedup (``prior_actions``) và
        giới hạn ``continue_thinking`` (``reflection_state['count']``) áp dụng
        xuyên suốt. Trả None nếu kind là finalize (move/no_reply) hoặc không
        nhận diện — caller tự quyết finalize/break. Luôn fail-soft: lỗi tool
        được gói thành result status 'failed', không ném exception.
        """
        kind = str(raw.get("kind", "") or "").strip().lower()

        if kind == "read_tool_guide":
            name = str(raw.get("guide", "") or "").strip()
            fingerprint = "guide:" + name
            if fingerprint in prior_actions:
                return {"kind": "tool_guide_result", "guide": name, "status": "duplicate"}
            prior_actions.add(fingerprint)
            return read_tool_guide(name)

        if kind == "continue_thinking":
            # Vòng Root→Root nội bộ, KHÔNG sinh lời public hay gọi Writer/TTS.
            # Giới hạn riêng nhằm tránh model tự lặp vô hạn.
            reflection_state["count"] = reflection_state.get("count", 0) + 1
            if reflection_state["count"] > 2:
                return {"kind": "reflection_result", "status": "limit_reached", "next": "finalize"}
            focus = str(raw.get("focus", "") or "").strip()[:900]
            return {"kind": "reflection_result", "status": "continued", "focus": focus,
                    "next": "reassess_and_finalize_or_use_evidence"}

        if kind == "recall_memory":
            query = str(raw.get("query", "") or "").strip()[:600]
            fingerprint = "memory:" + query.casefold()
            if fingerprint in prior_actions:
                outcome: Mapping[str, Any] = {"status": "duplicate", "reason": "same_query_already_attempted"}
            else:
                prior_actions.add(fingerprint)
                try:
                    outcome = (memory_tools.query(query) if memory_tools is not None else
                               {"status": "unavailable", "reason": "memory_recall_not_registered"})
                except Exception as exc:  # noqa: BLE001
                    outcome = {"status": "failed", "reason": type(exc).__name__}
            self._log("info", "[root-agent] memory_result step=%d status=%s",
                      step_index + 1, str(outcome.get("status", "unknown"))[:40])
            return {"kind": "memory_result", "query": query, "result": _bounded_value(outcome, depth=0)}

        if kind == "set_note":
            text = str(raw.get("text", "") or "")[:12000]
            if note_tools is not None:
                note_tools.set(text)
            return {"kind": "note_result", "operation": "set", "ok": note_tools is not None}

        if kind == "clear_note":
            if note_tools is not None:
                note_tools.clear()
            return {"kind": "note_result", "operation": "clear", "ok": note_tools is not None}

        if kind == "use_capability":
            capability_name = str(raw.get("capability", "") or "").strip().lower()
            arguments = raw.get("arguments") if isinstance(raw.get("arguments"), Mapping) else {}
            # Không tự gọi lại cùng một công cụ cùng args trong một event.
            # Model sẽ nhận receipt duplicate và chọn hướng khác.
            fingerprint = "cap:" + capability_name + ":" + json.dumps(
                _bounded_value(arguments, depth=0), ensure_ascii=False, sort_keys=True, default=str)
            if fingerprint in prior_actions:
                return {"kind": "capability_result", "capability": capability_name,
                        "result": {"status": "duplicate", "reason": "same_call_already_attempted"}}
            prior_actions.add(fingerprint)
            result = self._invoke_capability(
                registry=capabilities,
                name=capability_name,
                arguments=arguments,
                event=event,
                recent_history=recent_history,
                run_id=str(runtime_context.get("__conversation_run_id", "") or ""),
            )
            receipt = capabilities.result_receipts_for(
                event.event_id, run_id=str(runtime_context.get("__conversation_run_id", "") or ""),
                generation=event.generation,
            )
            self._log("info", "[root-agent] capability_result step=%d capability=%s status=%s",
                      step_index + 1, capability_name[:32], str(result.get("status", "unknown"))[:40])
            return {
                "receipt_ref": f"capability:{receipt[-1].invocation_id}" if receipt else "",
                "kind": "capability_result",
                "capability": capability_name,
                "result": _bounded_value(result, depth=0),
            }

        # Finalize (move/no_reply) hoặc kind không nhận diện: caller xử lý.
        return None

    # Các status được coi là KHÔNG tiến triển. Một tool_result mang một trong
    # các status này (hoặc rỗng) không đóng góp bằng chứng mới cho model.
    _NON_PROGRESS_STATUSES = frozenset(
        {"duplicate", "empty", "failed", "limit_reached", "unavailable", "invalid"}
    )

    @staticmethod
    def _result_fingerprint(result: Mapping[str, Any]) -> str:
        """Chữ ký ổn định của MỘT tool_result theo (loại, định danh, nội dung).

        Dùng để nhận ra model quay vòng trả lại cùng một kết quả cho cùng một
        loại action ở các bước liên tiếp (tiến triển giả).
        """
        kind = str(result.get("kind", "") or "")
        if kind == "memory_result":
            inner = result.get("result")
            inner = inner if isinstance(inner, Mapping) else {}
            payload = {k: inner.get(k) for k in ("status", "text") if k in inner}
            return "memory_result:" + str(result.get("query", "")) + ":" + json.dumps(
                payload, ensure_ascii=False, sort_keys=True, default=str)
        if kind == "capability_result":
            inner = result.get("result")
            inner = inner if isinstance(inner, Mapping) else {}
            return "capability_result:" + str(result.get("capability", "")) + ":" + json.dumps(
                inner, ensure_ascii=False, sort_keys=True, default=str)
        if kind == "reflection_result":
            return "reflection_result:" + str(result.get("status", "")) + ":" + str(result.get("focus", ""))
        if kind == "note_result":
            return "note_result:" + str(result.get("operation", "")) + ":" + str(result.get("ok", ""))
        return kind + ":" + json.dumps(
            {k: v for k, v in result.items() if k != "receipt_ref"},
            ensure_ascii=False, sort_keys=True, default=str)

    @classmethod
    def _is_meaningful_result(
        cls, result: Mapping[str, Any], *, prior_fingerprints: set[str]
    ) -> bool:
        """True nếu result mang bằng chứng MỚI có nghĩa cho model.

        False nếu result rỗng, status thuộc tập không-tiến-triển, HOẶC trùng
        chữ ký của một result cùng loại ở bước trước. reflection_result chỉ coi
        là tiến triển khi còn trong hạn (status 'continued'); limit_reached thì
        không. note_result 'set/clear ok' luôn là một thay đổi state có nghĩa.
        """
        if not isinstance(result, Mapping) or not result:
            return False
        kind = str(result.get("kind", "") or "")
        # Lấy status ở cả cấp ngoài lẫn trong 'result' (memory/capability lồng).
        status = str(result.get("status", "") or "").strip().lower()
        inner = result.get("result")
        if not status and isinstance(inner, Mapping):
            status = str(inner.get("status", "") or "").strip().lower()
        if status in cls._NON_PROGRESS_STATUSES:
            return False
        if kind == "note_result" and not result.get("ok"):
            return False
        fingerprint = cls._result_fingerprint(result)
        if fingerprint in prior_fingerprints:
            return False
        return True

    def _advance_progress_and_maybe_finalize(
        self,
        *,
        step_results: Sequence[Mapping[str, Any]],
        last_meaningful_fingerprint: set[str],
        streak_ref: int,
    ) -> dict[str, Any]:
        """Cập nhật no_progress_streak sau MỘT bước (single hoặc batch).

        Nếu không có result nào trong bước là meaningful -> streak += 1; ngược
        lại streak = 0 và ghi lại chữ ký các result có nghĩa để bước sau so
        trùng. Trả {'streak': int, 'stop': bool}; stop=True khi streak >= 2
        (caller sẽ finalize sớm). Nếu bước không sinh result nào (vd batch rỗng)
        coi như không tiến triển.
        """
        meaningful_fps: set[str] = set()
        for result in step_results:
            if self._is_meaningful_result(result, prior_fingerprints=last_meaningful_fingerprint):
                meaningful_fps.add(self._result_fingerprint(result))
        if meaningful_fps:
            streak = 0
            last_meaningful_fingerprint.clear()
            last_meaningful_fingerprint.update(meaningful_fps)
        else:
            streak = streak_ref + 1
        stop = streak >= 2
        if stop:
            self._log("info", "[root-agent] no_progress streak=%d forcing early finalize", streak)
        return {"streak": streak, "stop": stop}

    def _no_progress_decision(
        self,
        event: ConversationEvent,
        recent_history: Sequence[Mapping[str, Any]],
        note: str,
        runtime_context: Mapping[str, Any],
    ) -> ConversationDecision:
        """Finalize sớm khi loop trì trệ: degraded cho user_turn, NoReply khác."""
        if event.kind == "user_turn":
            return self._degraded(event, recent_history, note, runtime_context)
        return NoReply(reason="no_progress")

    @staticmethod
    def _system_prompt(capabilities: RootCapabilityRegistry) -> str:
        del capabilities  # A stable menu; registry still owns execution permission.
        return ROOT_SYSTEM_CORE + SOCIAL_JUDGMENT_PROMPT + ELEGANT_SOCIAL_PROMPT + (INTERACTION_CHOICE_PROMPT if interaction_choices_enabled() else "")

    @staticmethod
    def _payload(
        *,
        event: ConversationEvent,
        recent_history: Sequence[Mapping[str, Any]],
        note: str,
        runtime_context: Mapping[str, Any],
        tool_results: Sequence[Mapping[str, Any]],
        step_index: int,
    ) -> dict[str, Any]:
        return {
            "step_index": step_index,
            "event": {
                "event_id": event.event_id,
                "kind": event.kind,
                "text": str(event.text or "")[:32000],
                "generation": event.generation,
                "metadata": _bounded_value(dict(event.metadata), depth=0),
            },
            "recent_public_history": [
                {
                    "role": str(x.get("role", ""))[:24],
                    # Khi history TTS đổi trạng thái, nội dung Root nhận vẫn
                    # là lời gốc, không chèn lại nhãn âm thanh vào đầu câu.
                    "content": str(x.get("raw_content", x.get("content", "")))[:12000],
                    **{key: str(x[key])[:180] for key in
                       ("history_id", "delivery_id", "delivery_status") if x.get(key)},
                }
                for x in recent_history[-32:]
                if isinstance(x, Mapping)
            ],
            "conversation_note": str(note or "")[:8000],
            "runtime_context": _bounded_context(runtime_context),
            "tool_results": [dict(item) for item in tool_results
                             if item.get("kind") == "tool_guide_result" and item.get("status") == "found"]
                            + [dict(item) for item in tool_results
                               if item.get("kind") != "tool_guide_result"][-6:],
        }

    def _invoke_capability(
        self,
        *,
        registry: RootCapabilityRegistry,
        name: str,
        arguments: Mapping[str, Any],
        event: ConversationEvent,
        recent_history: Sequence[Mapping[str, Any]],
        run_id: str = "",
    ) -> Mapping[str, Any]:
        invocation_id = f"cap:{uuid.uuid4().hex}"
        capability = registry.get(name)
        invoke = getattr(capability, "invoke_for_root", None) if capability is not None else None
        if not callable(invoke):
            reason = "capability_not_registered" if capability is None else "capability_has_no_root_entry"
            response: Mapping[str, Any] = {"status": "unavailable", "reason": f"{reason}:{name}"}
        else:
            try:
                registry.record_invocation(name, str(event.event_id or ""))
                raw = invoke(arguments=dict(arguments), event=event, recent_history=recent_history)
                response = dict(raw) if isinstance(raw, Mapping) else {"status": "unknown", "reason": "untyped_executor_result"}
            except Exception as exc:  # noqa: BLE001
                self._log("debug", "[conversation-v12] capability %s degraded: %s", name, exc)
                response = {"status": "failed", "error": type(exc).__name__, "message": str(exc)[:1200]}
        # Receipt chỉ phản ánh kết quả đã trả về; không biến 'started' thành success.
        if run_id:
            registry.record_result(name=name, invocation_id=invocation_id,
                                   event=event, run_id=run_id, result=response)
        return response

    @staticmethod
    def _degraded(
        event: ConversationEvent,
        recent_history: Sequence[Mapping[str, Any]],
        note: str,
        runtime_context: Mapping[str, Any],
    ) -> ConversationDecision:
        return DeterministicRootAgent().decide(
            event=event,
            recent_history=recent_history,
            note=note,
            runtime_context=runtime_context,
        )

    def _log(self, level: str, message: str, *args: Any) -> None:
        if self.logger is None:
            return
        try:
            getattr(self.logger, level)(message, *args)
        except Exception:
            pass


def _clean_empty_and_zeroes(val: Any) -> Any:
    """Loại bỏ các key/giá trị rỗng, vô nghĩa hoặc các khối toàn số 0.0 để giữ prompt tinh gọn."""
    if val in (None, "", {}, [], ()):
        return None
    if isinstance(val, Mapping):
        cleaned = {}
        for k, v in val.items():
            if str(k).startswith("__"):
                continue
            # Bỏ existential_anchor nếu chưa có beat nào và toàn bộ các trọng số đều là 0.0
            if k == "existential_anchor" and isinstance(v, Mapping):
                if not any(isinstance(x, (int, float)) and x > 0 for x in v.values()):
                    continue
            # Bỏ environment_awareness nếu không có app/process nào đang mở và activity là unknown
            if k == "environment_awareness" and isinstance(v, Mapping):
                if not v.get("focused_app") and not v.get("focused_process") and v.get("activity") in (None, "unknown"):
                    continue
            sub = _clean_empty_and_zeroes(v)
            if sub is not None:
                cleaned[k] = sub
        return cleaned if cleaned else None
    if isinstance(val, (list, tuple)):
        cleaned = [_clean_empty_and_zeroes(x) for x in val]
        cleaned = [x for x in cleaned if x is not None]
        return cleaned if cleaned else None
    return val


def _bounded_context(context: Mapping[str, Any]) -> dict[str, Any]:
    # Hai key ``__*`` là dependency runtime nội bộ và tuyệt đối không serialize
    # vào model payload. Model chỉ thấy evidence/context public-safe đã chọn.
    allowed = (
        "agent_execution_route",
        "teaching_active",
        "explicit_tool_route",
        "conversation_status",
        "conversation_move_receipts",
        "work_lane_progress",
        "private_cognition_context",
        "background_proposals",
        "autonomous_self_context",
        "user_addressing_context",
        "affective_voice_state",
        "engagement_context",
        "learner_continuity",
        "learning_brief",
        "confusion_signal",
        "social_situation_evidence",
        "discourse_context",
        "relationship_summary",
        "relationship_context",
        "previous_public_excerpt",
        "interrupt_context",
        "open_loop_hints",
        "recent_interaction_act_receipts",
        "memory_context",
        "grounding_context",
        "language_hint",
        "speech_language_hint",
        "continuation_lease",
    )
    out: dict[str, Any] = {}
    for key in allowed:
        if key in context:
            val = _clean_empty_and_zeroes(_bounded_value(context[key], depth=0))
            if val is not None:
                out[key] = val
    # Passive evidence read on each Root decision; no extra tick/worker/LLM.
    try:
        from ..workspace.presentation_feedback import recent_render_feedback
        receipts = recent_render_feedback(limit=4)
        if receipts:
            out['renderer_feedback'] = receipts
    except (OSError, ValueError, ImportError):
        pass
    try:
        from ..workspace.presentation_health import recent_presentation_health
        state = recent_presentation_health(limit=4)
        if state:
            out['presentation_health'] = state
    except (OSError, ValueError, ImportError):
        pass
    return out


def _bounded_value(value: Any, *, depth: int) -> Any:
    """Bound arbitrary evidence trước khi đưa vào root-model payload."""

    if depth >= 5:
        return str(value or "")[:4000]
    if isinstance(value, Mapping):
        return {
            str(key)[:120]: _bounded_value(item, depth=depth + 1)
            for key, item in list(value.items())[:96]
            if not str(key).startswith("__")
        }
    if isinstance(value, (list, tuple)):
        return [_bounded_value(item, depth=depth + 1) for item in list(value)[:96]]
    if isinstance(value, str):
        return value[:32000]
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return str(value)[:1200]


__all__ = [
    "DeterministicRootAgent",
    "NEXT_MOVE_SCHEMA",
    "ROOT_BATCH_STEP_SCHEMA",
    "ROOT_STEP_SCHEMA",
    "RootAgent",
    "RuntimeModelRootAgent",
]
