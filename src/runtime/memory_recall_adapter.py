from __future__ import annotations

from dataclasses import dataclass
import json
import os
import re
from typing import Any, Callable, Optional

try:
    from ..domain import Decision
    from ..llm.client import LlamaServerClient
    from ..memory.memory_models import MemoryKind
    from ..memory.memory_query_compiler import MemoryQueryCompiler
    from ..memory.memory_query_executor import MemoryQueryExecutor
    from ..memory.memory_query_ir import (
        ActRef, MemoryQueryIR, MemorySelector, QueryIntent, RecallAnswer, RoleRef, SurfaceMode
    )
    from .semantic_frame import MemoryAnswerMode, MemoryQueryFrame, MemoryQueryIntent
    from ..memory.memory_query_llm_parser import MemoryQueryLLMClient, MemoryQueryLLMParser
    from ..memory.memory_recall_policy import MemoryRecallSurfacePolicy
    from ..memory.memory_query_slot_parser import user_is_declaring_or_updating_fact
    from ..memory.memory_turn_annotator import TurnAnnotator
    from ..memory.memory_turn_graph import TurnGraph
    from .session_turn_ledger import SessionTurnLedger
except ImportError:
    from domain import Decision
    from llm.client import LlamaServerClient
    from memory.memory_models import MemoryKind
    from memory.memory_query_compiler import MemoryQueryCompiler
    from memory.memory_query_executor import MemoryQueryExecutor
    from memory.memory_query_ir import (
        ActRef, MemoryQueryIR, MemorySelector, QueryIntent, RecallAnswer, RoleRef, SurfaceMode
    )
    from runtime.semantic_frame import MemoryAnswerMode, MemoryQueryFrame, MemoryQueryIntent
    from memory.memory_query_llm_parser import MemoryQueryLLMClient, MemoryQueryLLMParser
    from memory.memory_recall_policy import MemoryRecallSurfacePolicy
    from memory.memory_query_slot_parser import user_is_declaring_or_updating_fact
    from memory.memory_turn_annotator import TurnAnnotator
    from memory.memory_turn_graph import TurnGraph
    from runtime.session_turn_ledger import SessionTurnLedger


SemanticLookupFn = Callable[[Any], Optional[Any]]
EpisodicLookupFn = Callable[[str], Optional[Any]]

_FACT_KEY_TO_MEMORY_KIND: dict[str, MemoryKind] = {
    "user.name": MemoryKind.USER_NAME,
    "user.birth_month": MemoryKind.USER_BIRTH_MONTH,
    "user.response_style": MemoryKind.USER_RESPONSE_STYLE,
}


@dataclass(frozen=True, slots=True)
class _MemoryQueryLLMConfig:
    enabled: bool = False
    timeout_seconds: float = 2.0
    max_tokens: int = 320
    min_confidence: float = 0.45

    @classmethod
    def from_env(cls) -> "_MemoryQueryLLMConfig":
        enabled = _env_flag("ERIS_MEMORY_QUERY_LLM_ENABLED", default=False)
        timeout_seconds = _env_float("ERIS_MEMORY_QUERY_LLM_TIMEOUT_MS", default=2000.0) / 1000.0
        max_tokens = _env_int("ERIS_MEMORY_QUERY_LLM_MAX_TOKENS", default=320)
        return cls(
            enabled=enabled,
            timeout_seconds=max(0.1, timeout_seconds),
            max_tokens=max(64, max_tokens),
            min_confidence=0.45,
        )


@dataclass(slots=True)
class MemoryRecallAdapter:
    """
    Runtime adapter for structured memory recall.

    The public contract is intentionally simple: return a ready-to-send Decision
    before chat falls through to the general LLM answer path.
    """

    ledger: SessionTurnLedger
    memory_core: Any = None
    llm_service: Any = None
    semantic_lookup: SemanticLookupFn | None = None
    episodic_lookup: EpisodicLookupFn | None = None
    memory_kernel: Any = None
    compiler: MemoryQueryCompiler | None = None
    executor: MemoryQueryExecutor | None = None
    surface_policy: MemoryRecallSurfacePolicy | None = None
    annotator: TurnAnnotator | None = None

    def try_answer(self, user_text: str) -> Optional[Decision]:
        outcome = self._recall(user_text)
        if outcome is None:
            return None
        return outcome.decision

    def try_answer_query(self, query: MemoryQueryFrame, *, raw_text: str = "") -> Optional[Decision]:
        """Thực thi query typed từ semantic authority, không parse lại câu user.

        Đây là đường production mới cho ``reference.kind=long_term_memory``.
        ``raw_text`` chỉ được giữ trong debug/evidence, không tham gia chọn fact.
        """

        ir = self._ir_from_typed_query(query, raw_text=raw_text)
        if ir is None:
            return None
        executor = self.executor or MemoryQueryExecutor()
        surface_policy = self.surface_policy or MemoryRecallSurfacePolicy()
        annotator = self.annotator or TurnAnnotator()
        turn_graph = TurnGraph.from_session_ledger(self.ledger, annotator=annotator)
        raw = executor.execute(
            ir,
            turn_graph=turn_graph,
            memory_core=self._execution_memory_core(),
        )
        answer = surface_policy.surface(raw)
        if not str(getattr(answer, "text", "") or "").strip():
            return None
        return self._build_decision(answer, compile_result=None)

    @staticmethod
    def _ir_from_typed_query(query: MemoryQueryFrame, *, raw_text: str) -> MemoryQueryIR | None:
        if not query.usable:
            return None
        if query.intent is MemoryQueryIntent.RECALL_FACT:
            intent = QueryIntent.RECALL_FACT
            target = MemorySelector(
                role=RoleRef.USER,
                act=ActRef.FACT_DISCLOSURE,
                fact_key=query.predicate_hint,
                semantic_hint=query.object_hint or None,
                tags=tuple(
                    item
                    for item in (
                        f"subject:{query.subject}" if query.subject else "",
                        f"temporal:{query.temporal_scope}" if query.temporal_scope else "",
                    )
                    if item
                ),
            )
        elif query.intent in {MemoryQueryIntent.RECALL_SUMMARY, MemoryQueryIntent.EXPLAIN_MEMORY}:
            intent = QueryIntent.RECALL_SUMMARY
            target = MemorySelector(
                role=RoleRef.USER,
                act=ActRef.ANY,
                semantic_hint=query.object_hint or query.predicate_hint or query.subject or None,
                tags=tuple(
                    item
                    for item in (
                        f"subject:{query.subject}" if query.subject else "",
                        f"temporal:{query.temporal_scope}" if query.temporal_scope else "",
                    )
                    if item
                ),
            )
        elif query.intent is MemoryQueryIntent.VERIFY_MEMORY:
            intent = QueryIntent.VERIFY_MEMORY
            target = MemorySelector(
                role=RoleRef.USER,
                act=ActRef.FACT_DISCLOSURE,
                fact_key=query.predicate_hint or None,
                semantic_hint=query.object_hint or None,
                tags=tuple(
                    item
                    for item in (
                        f"subject:{query.subject}" if query.subject else "",
                        f"temporal:{query.temporal_scope}" if query.temporal_scope else "",
                    )
                    if item
                ),
            )
        else:
            return None

        surface = {
            MemoryAnswerMode.EXACT: SurfaceMode.EXACT,
            MemoryAnswerMode.GIST: SurfaceMode.GIST,
            MemoryAnswerMode.SUMMARY: SurfaceMode.GIST,
            MemoryAnswerMode.ADMIT_UNCERTAIN: SurfaceMode.ADMIT_UNCERTAIN,
        }[query.answer_mode]
        return MemoryQueryIR(
            intent=intent,
            target=target,
            surface=surface,
            confidence=query.confidence,
            ambiguity=max(0.0, 1.0 - query.confidence),
            intercept_permission=True,
            evidence=("semantic_frame.memory_query",),
            warnings=(),
            raw_text=str(raw_text or ""),
        )

    def try_answer_as_decision_payload(self, user_text: str) -> Optional[dict[str, Any]]:
        outcome = self._recall(user_text)
        if outcome is None:
            return None

        payload = outcome.decision.to_payload()
        payload["memory_source"] = outcome.answer.source.value
        payload["memory_surface"] = outcome.answer.surface.value
        return payload

    def _recall(self, user_text: str) -> Optional["_RecallOutcome"]:
        if user_is_declaring_or_updating_fact(user_text):
            return None

        kernel = getattr(self, "memory_kernel", None)
        if kernel is not None and callable(getattr(kernel, "answer", None)):
            try:
                result = kernel.answer(user_text, session_ledger=self.ledger)
            except Exception:
                result = None
            if result is not None and bool(getattr(result, "intercepted", False)) and getattr(result, "answer", None) is not None:
                compile_result = getattr(result, "compile_result", None)
                # Belt-and-suspenders: kernel đã kiểm intercept_permission, nhưng adapter
                # vẫn kiểm lại để tránh một facade cũ/khác trả intercepted=True quá rộng.
                # Memory recall chỉ được cắt ngang chat khi IR thật sự được cấp quyền.
                best = getattr(compile_result, "best", None)
                if best is not None and not self._should_intercept(best):
                    return None
                answer = result.answer
                decision = self._build_decision(answer, compile_result=compile_result)
                return _RecallOutcome(decision=decision, answer=answer)

        compiler = self._ensure_compiler()
        executor = self.executor or MemoryQueryExecutor()
        surface_policy = self.surface_policy or MemoryRecallSurfacePolicy()
        annotator = self.annotator or TurnAnnotator()

        compile_result = compiler.compile(user_text)
        if not compile_result.has_query or compile_result.best is None:
            return None
        if not self._should_intercept(compile_result.best):
            return None

        turn_graph = TurnGraph.from_session_ledger(
            self.ledger,
            annotator=annotator,
        )
        raw = executor.execute(
            compile_result.best,
            turn_graph=turn_graph,
            memory_core=self._execution_memory_core(),
        )
        answer = surface_policy.surface(raw)
        decision = self._build_decision(answer, compile_result=compile_result)
        return _RecallOutcome(decision=decision, answer=answer)

    def _should_intercept(self, best: Any) -> bool:
        if not bool(getattr(best, "intercept_permission", False)):
            return False
        confidence = float(getattr(best, "confidence", 0.0) or 0.0)
        ambiguity = float(getattr(best, "ambiguity", 1.0) or 1.0)
        if confidence < 0.60:
            return False
        if ambiguity > 0.78:
            return False
        return True

    def _ensure_compiler(self) -> MemoryQueryCompiler:
        if self.compiler is not None:
            return self.compiler

        llm_client = _RuntimeMemoryQueryLLMClient.from_service(self.llm_service)
        llm_parser = MemoryQueryLLMParser(client=llm_client) if llm_client is not None else MemoryQueryLLMParser()
        self.compiler = MemoryQueryCompiler(llm_parser=llm_parser)
        return self.compiler

    def _execution_memory_core(self) -> Any:
        if self.memory_core is None and self.semantic_lookup is None:
            return None
        return _MemoryCoreProxy(
            base=self.memory_core,
            semantic_lookup=self.semantic_lookup,
            episodic_lookup=self.episodic_lookup,
        )

    def _build_decision(self, answer: RecallAnswer, *, compile_result: Any) -> Decision:
        reasoning_tags = (
            "answer_first",
            "memory_query",
            f"memory_surface:{answer.surface.value}",
            f"memory_source:{answer.source.value}",
        )
        return Decision(
            text=answer.text,
            intent="memory_recall",
            action="answer",
            animation="stand",
            reasoning_tags=reasoning_tags,
            should_speak=answer.should_speak,
            confidence=answer.confidence,
            direction=0,
            target_x=-1.0,
            bounce_action="",
            debug_trace=self._debug_trace(answer, compile_result=compile_result),
            next_animation="stand",
            parse_mode="memory_recall",
            system_action="",
            action_input="",
        )

    def _debug_trace(self, answer: RecallAnswer, *, compile_result: Any) -> str:
        pieces = [answer.debug_trace]
        if compile_result is None:
            return "|".join(part for part in pieces if part)

        debug = getattr(compile_result, "debug", None)
        if debug is not None:
            pieces.append(
                "memory_compile"
                f"|slots={int(getattr(debug, 'slot_hypotheses', 0) or 0)}"
                f"|semantic_conf={float(getattr(debug, 'semantic_confidence', 0.0) or 0.0):.2f}"
                f"|semantic_amb={float(getattr(debug, 'semantic_ambiguity', 1.0) or 1.0):.2f}"
                f"|llm={'1' if bool(getattr(debug, 'llm_called', False)) else '0'}"
                f"|llm_reason={str(getattr(debug, 'llm_reason', '') or 'none')}"
            )
            repair_applied = tuple(getattr(debug, "repair_applied", ()) or ())
            if repair_applied:
                pieces.append("repair=" + ",".join(repair_applied))
        return "|".join(part for part in pieces if part)


@dataclass(slots=True)
class _RecallOutcome:
    decision: Decision
    answer: RecallAnswer


class _MemoryCoreProxy:
    def __init__(
        self,
        *,
        base: Any = None,
        semantic_lookup: SemanticLookupFn | None = None,
        episodic_lookup: EpisodicLookupFn | None = None,
    ) -> None:
        self.base = base
        self.semantic_lookup_fn = semantic_lookup
        self.episodic_lookup_fn = episodic_lookup

    def lookup_fact(self, fact_key: str) -> Any:
        if self.base is not None:
            for method_name in ("lookup_fact", "get_fact", "semantic_lookup"):
                fn = getattr(self.base, method_name, None)
                if callable(fn):
                    try:
                        value = fn(fact_key)
                    except Exception:
                        value = None
                    if value is not None:
                        clean_value = _clean_public_fact_value(value)
                        if clean_value is not None:
                            return clean_value

        if self.semantic_lookup_fn is None:
            return None

        kind = _FACT_KEY_TO_MEMORY_KIND.get(str(fact_key or ""))
        lookup_key: Any = kind if kind is not None else str(fact_key or "")
        try:
            return _clean_public_fact_value(self.semantic_lookup_fn(lookup_key))
        except Exception:
            return None

    def get_fact(self, fact_key: str) -> Any:
        return self.lookup_fact(fact_key)

    def semantic_lookup(self, fact_key: str) -> Any:
        return self.lookup_fact(fact_key)

    def recall(self, query: str = "") -> Any:
        if self.base is not None:
            recall = getattr(self.base, "recall", None)
            if callable(recall):
                return recall(query)
        if self.episodic_lookup_fn is None:
            return None
        return self.episodic_lookup_fn(query)

    def __getattr__(self, name: str) -> Any:
        if self.base is None:
            raise AttributeError(name)
        return getattr(self.base, name)


@dataclass(slots=True)
class _RuntimeMemoryQueryLLMClient(MemoryQueryLLMClient):
    service: Any
    timeout_seconds: float = 2.0
    max_tokens: int = 320
    min_confidence: float = 0.45

    @classmethod
    def from_service(cls, service: Any) -> Optional["_RuntimeMemoryQueryLLMClient"]:
        config = _MemoryQueryLLMConfig.from_env()
        if not config.enabled or service is None:
            return None
        client = getattr(service, "client", None)
        if not isinstance(client, LlamaServerClient):
            return None
        if not callable(getattr(client, "create_chat_completion", None)):
            return None
        return cls(
            service=service,
            timeout_seconds=config.timeout_seconds,
            max_tokens=config.max_tokens,
            min_confidence=config.min_confidence,
        )

    def complete_json(self, *, system: str, user: str, schema: dict[str, Any]) -> str:
        client = getattr(self.service, "client", None)
        if not callable(getattr(client, "create_chat_completion", None)):
            return ""

        schema_text = json.dumps(schema, ensure_ascii=False, separators=(",", ":"))
        try:
            response = client.create_chat_completion(
                messages=[
                    {
                        "role": "system",
                        "content": f"{system}\nJSON schema:\n{schema_text}",
                    },
                    {"role": "user", "content": user},
                ],
                temperature=0.0,
                frequency_penalty=0.0,
                presence_penalty=0.0,
                max_tokens=self.max_tokens,
                response_format={"type": "json_object"},
                grammar=None,
                timeout=self.timeout_seconds,
            )
        except Exception:
            return ""

        extractor = getattr(self.service, "_extract_content", None)
        if callable(extractor):
            try:
                raw = str(extractor(response) or "")
            except Exception:
                return ""
        else:
            raw = _extract_response_text(response)

        if not self._passes_confidence_floor(raw):
            return ""
        return raw

    def _passes_confidence_floor(self, raw: str) -> bool:
        if self.min_confidence <= 0.0:
            return True
        try:
            data = json.loads(raw)
        except Exception:
            return True
        if not isinstance(data, dict):
            return True
        try:
            confidence = float(data.get("confidence", 0.0) or 0.0)
        except (TypeError, ValueError):
            return True
        return confidence >= self.min_confidence


def _extract_response_text(response: Any) -> str:
    if isinstance(response, str):
        return response
    if not isinstance(response, dict):
        return ""

    direct = response.get("output_text") or response.get("text") or response.get("content")
    if isinstance(direct, str):
        return direct

    choices = response.get("choices")
    if isinstance(choices, list):
        for choice in choices:
            if not isinstance(choice, dict):
                continue
            message = choice.get("message")
            if isinstance(message, dict):
                content = message.get("content")
                if isinstance(content, str):
                    return content
            content = choice.get("content")
            if isinstance(content, str):
                return content

    candidates = response.get("candidates")
    if isinstance(candidates, list):
        for candidate in candidates:
            content = candidate.get("content") if isinstance(candidate, dict) else None
            if isinstance(content, str):
                return content

    return ""


def _clean_public_fact_value(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, dict):
        for key in ("value", "content", "text", "claim"):
            if key in value:
                return _clean_public_fact_value(value[key])
        return None
    if isinstance(value, (list, tuple, set)):
        if len(value) == 1:
            return _clean_public_fact_value(next(iter(value)))
        return None
    if isinstance(value, (str, int, float, bool)):
        text = str(value).strip()
        if _looks_like_internal_memory_surface(text):
            return None
        return value
    for attr in ("value", "content", "text", "claim"):
        if hasattr(value, attr):
            return _clean_public_fact_value(getattr(value, attr))
    return None


def _looks_like_internal_memory_surface(text: str) -> bool:
    compact = " ".join(str(text or "").split())
    return (
        "MemoryPacket(" in compact
        or "RawRecallResult(" in compact
        or "RecallAnswer(" in compact
        or bool(re.match(r"^[A-Za-z_][A-Za-z0-9_.]*\(.+=.+\)$", compact))
    )

def _env_flag(name: str, *, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, *, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _env_float(name: str, *, default: float) -> float:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default
