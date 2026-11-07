from __future__ import annotations

"""
memory_query_executor.py

Executor chạy MemoryQueryIR trên TurnGraph / memory_core.

Quy tắc:
- Executor không hiểu tiếng Việt.
- Executor không parse user text.
- Executor không quyết định style public.
- Executor chỉ lấy dữ liệu theo IR.

Nếu thiếu dữ liệu hoặc query mơ hồ, trả RawRecallResult.missing().
"""

from typing import Any, Optional

from dataclasses import dataclass, field

from .memory_query_ir import (
    ActRef,
    MemoryQueryIR,
    MemorySourceKind,
    QueryIntent,
    RawRecallResult,
    RelationRef,
    SurfaceMode,
)
from .memory_turn_graph import TurnGraph, TurnNode
from .name_validation import is_plausible_user_name
from .predicate_registry import PredicateRegistry, get_default_registry


@dataclass(slots=True)
class MemoryQueryExecutor:
    """Thực thi IR trên graph và memory_core."""

    registry: PredicateRegistry = field(default_factory=get_default_registry)

    def execute(
        self,
        ir: MemoryQueryIR,
        *,
        turn_graph: Optional[TurnGraph] = None,
        memory_core: Any = None,
    ) -> RawRecallResult:
        if ir.intent == QueryIntent.RECALL_TURN:
            if not turn_graph:
                return RawRecallResult.missing(ir, "missing_turn_graph")
            return self._execute_turn_recall(ir, turn_graph)

        if ir.intent == QueryIntent.RECALL_COUNT:
            if not turn_graph:
                return RawRecallResult.missing(ir, "missing_turn_graph")
            count = turn_graph.count(ir.target)
            return RawRecallResult(
                found=True,
                source=MemorySourceKind.SESSION_TURN_GRAPH,
                ir=ir,
                count=count,
                confidence=min(0.95, ir.confidence + 0.05),
                evidence=("turn_graph:count",),
            )

        if ir.intent == QueryIntent.RECALL_FACT:
            return self._execute_fact_recall(ir, memory_core=memory_core, turn_graph=turn_graph)

        if ir.intent == QueryIntent.RECALL_SUMMARY:
            if not turn_graph:
                return RawRecallResult.missing(ir, "missing_turn_graph")
            return self._execute_summary(ir, turn_graph)

        return RawRecallResult.missing(ir, "unsupported_query_intent")

    # ------------------------------------------------------------------
    # Turn recall
    # ------------------------------------------------------------------

    def _execute_turn_recall(self, ir: MemoryQueryIR, graph: TurnGraph) -> RawRecallResult:
        # relation: ANSWER_TO(anchor=user question N)
        if ir.relation.relation == RelationRef.ANSWER_TO:
            if not ir.relation.anchor:
                return RawRecallResult.missing(ir, "answer_to_missing_anchor", MemorySourceKind.SESSION_TURN_GRAPH)

            anchors = graph.select(ir.relation.anchor)
            if not anchors:
                return RawRecallResult.missing(ir, "anchor_not_found", MemorySourceKind.SESSION_TURN_GRAPH)

            # Nếu anchor có quantity, trả list answer cho từng anchor.
            answers: list[TurnNode] = []
            for anchor in anchors:
                answer = graph.first_assistant_answer_to(anchor.id)
                if answer:
                    answers.append(answer)

            if not answers:
                return RawRecallResult.missing(ir, "answer_node_not_found", MemorySourceKind.SESSION_TURN_GRAPH)

            if len(answers) == 1:
                node = answers[0]
                return RawRecallResult(
                    found=True,
                    source=MemorySourceKind.SESSION_TURN_GRAPH,
                    ir=ir,
                    items=(node,),
                    exact_text=node.text,
                    confidence=min(ir.confidence, node.confidence, 0.95),
                    evidence=(f"turn:{node.id}", "edge:answers"),
                )

            return RawRecallResult(
                found=True,
                source=MemorySourceKind.SESSION_TURN_GRAPH,
                ir=ir,
                items=tuple(answers),
                exact_text="\n".join(f"{i+1}. {n.text}" for i, n in enumerate(answers)),
                confidence=min(ir.confidence, min(n.confidence for n in answers), 0.92),
                evidence=tuple(f"turn:{n.id}" for n in answers),
            )

        # relation: AFTER / BEFORE anchor.
        if ir.relation.relation in {RelationRef.AFTER, RelationRef.BEFORE}:
            if not ir.relation.anchor:
                return RawRecallResult.missing(ir, "relative_query_missing_anchor", MemorySourceKind.SESSION_TURN_GRAPH)
            anchors = graph.select(ir.relation.anchor)
            if not anchors:
                return RawRecallResult.missing(ir, "relative_anchor_not_found", MemorySourceKind.SESSION_TURN_GRAPH)
            anchor = anchors[0]
            node = (
                graph.neighbor_after(anchor.id, ir.target)
                if ir.relation.relation == RelationRef.AFTER
                else graph.neighbor_before(anchor.id, ir.target)
            )
            if not node:
                return RawRecallResult.missing(ir, "relative_node_not_found", MemorySourceKind.SESSION_TURN_GRAPH)
            return RawRecallResult(
                found=True,
                source=MemorySourceKind.SESSION_TURN_GRAPH,
                ir=ir,
                items=(node,),
                exact_text=node.text,
                confidence=min(ir.confidence, node.confidence, 0.90),
                evidence=(f"turn:{node.id}", f"relation:{ir.relation.relation.value}"),
            )

        # relation SELF: direct select.
        nodes = graph.select(ir.target)
        if not nodes:
            return RawRecallResult.missing(ir, "target_node_not_found", MemorySourceKind.SESSION_TURN_GRAPH)

        if ir.target.quantity is not None:
            text = "\n".join(f"{i+1}. {node.text}" for i, node in enumerate(nodes))
            return RawRecallResult(
                found=True,
                source=MemorySourceKind.SESSION_TURN_GRAPH,
                ir=ir,
                items=tuple(nodes),
                exact_text=text,
                confidence=min(ir.confidence, min(n.confidence for n in nodes), 0.92),
                evidence=tuple(f"turn:{n.id}" for n in nodes),
            )

        node = nodes[0]
        return RawRecallResult(
            found=True,
            source=MemorySourceKind.SESSION_TURN_GRAPH,
            ir=ir,
            items=(node,),
            exact_text=node.text,
            confidence=min(ir.confidence, node.confidence, 0.95),
            evidence=(f"turn:{node.id}",),
        )

    # ------------------------------------------------------------------
    # Fact recall
    # ------------------------------------------------------------------

    def _execute_fact_recall(
        self,
        ir: MemoryQueryIR,
        *,
        memory_core: Any,
        turn_graph: Optional[TurnGraph],
    ) -> RawRecallResult:
        fact_key = ir.target.fact_key
        if not fact_key:
            return RawRecallResult.missing(ir, "fact_key_missing")
        if fact_key == "user.profile":
            return self._execute_user_profile_recall(ir, memory_core=memory_core)

        # Ưu tiên semantic memory ổn định.
        value = self._lookup_fact(memory_core, fact_key)
        if value is not None:
            if fact_key == "user.name" and not is_plausible_user_name(value):
                value = None
            else:
                text = self._format_fact(fact_key, value)
                return RawRecallResult(
                    found=True,
                    source=MemorySourceKind.SEMANTIC_MEMORY,
                    ir=ir,
                    exact_text=text,
                    gist=text,
                    confidence=min(0.92, max(ir.confidence, 0.78)),
                    evidence=(f"semantic_fact:{fact_key}",),
                )


        # Fallback có kiểm soát: nếu fact vừa được nói trong phiên nhưng chưa commit xong,
        # lấy từ TurnGraph fact_mentions. Đây không phải long-term memory.
        if turn_graph:
            nodes = turn_graph.select(ir.target)
            for node in reversed(nodes):
                for fact in node.fact_mentions:
                    if fact.key == fact_key:
                        if fact_key == "user.name" and not is_plausible_user_name(fact.value):
                            continue
                        text = self._format_fact(fact_key, fact.value)
                        return RawRecallResult(
                            found=True,
                            source=MemorySourceKind.SESSION_TURN_GRAPH,
                            ir=ir,
                            items=(node,),
                            exact_text=text,
                            gist=text,
                            confidence=min(ir.confidence, fact.confidence, 0.88),
                            evidence=(f"turn_fact:{node.id}:{fact_key}",),
                            warnings=("fact_from_session_not_long_term",),
                        )

        return RawRecallResult.missing(ir, f"fact_not_found:{fact_key}", MemorySourceKind.SEMANTIC_MEMORY)

    def _execute_user_profile_recall(self, ir: MemoryQueryIR, *, memory_core: Any) -> RawRecallResult:
        name = self._lookup_fact(memory_core, "user.name")
        if name is not None and not is_plausible_user_name(name):
            name = None

        items = self._semantic_user_items(memory_core)
        profile_lines: list[str] = []
        evidence: list[str] = []

        if name:
            profile_lines.append(f"tên bạn là {name}")
            evidence.append("semantic_fact:user.name")

        for item in items:
            fact_key = str(getattr(item, "fact_key", "") or "")
            if fact_key == "user.name":
                continue
            try:
                confidence = float(getattr(item, "confidence", 0.0) or 0.0)
                importance = float(getattr(item, "importance", 0.0) or 0.0)
            except (TypeError, ValueError):
                confidence = 0.0
                importance = 0.0
            if confidence < 0.74 or importance < 0.70:
                continue
            rendered = self._format_user_profile_fact(item)
            if not rendered:
                continue
            profile_lines.append(rendered)
            item_id = str(getattr(item, "id", "") or fact_key or "user_fact")
            evidence.append(f"semantic_fact:{item_id}")
            if len(profile_lines) >= 4:
                break

        if not profile_lines:
            return RawRecallResult.missing(ir, "fact_not_found:user.profile", MemorySourceKind.SEMANTIC_MEMORY)

        if len(profile_lines) == 1 and name:
            text = (
                f"Mình biết chắc nhất hiện tại: {profile_lines[0]}. "
                "Phần còn lại về bạn tôi chưa có đủ ký ức ổn định để nói chắc."
            )
        else:
            head = profile_lines[0]
            tail = "; ".join(profile_lines[1:])
            text = (
                f"Mình biết chắc nhất hiện tại: {head}. "
                f"Ngoài ra tôi có vài dấu vết: {tail}. "
                "Tôi chưa có đủ ký ức ổn định để gán một hồ sơ tính cách cụ thể nếu bạn chưa nói rõ."
            )

        return RawRecallResult(
            found=True,
            source=MemorySourceKind.SEMANTIC_MEMORY,
            ir=ir,
            exact_text=text,
            gist=text,
            confidence=min(0.88, max(ir.confidence, 0.76)),
            evidence=tuple(dict.fromkeys(evidence)),
        )

    def _semantic_user_items(self, memory_core: Any) -> list[Any]:
        candidates = []
        semantic = getattr(memory_core, "semantic", None)
        if semantic is not None:
            candidates.append(semantic)
        core = getattr(memory_core, "core", None)
        semantic = getattr(core, "semantic", None)
        if semantic is not None:
            candidates.append(semantic)
        for store in candidates:
            load = getattr(store, "load", None)
            if not callable(load):
                continue
            try:
                items = list(load() or [])
            except Exception:
                continue
            user_items = [
                item
                for item in items
                if str(getattr(item, "status", "") or "") in {"active", "tentative"}
                and (
                    str(getattr(item, "domain", "") or "") == "user"
                    or str(getattr(item, "fact_key", "") or "").startswith("user.")
                )
            ]
            return sorted(
                user_items,
                key=lambda item: (
                    float(getattr(item, "confidence", 0.0) or 0.0),
                    float(getattr(item, "importance", 0.0) or 0.0),
                    str(getattr(item, "updated_at", "") or ""),
                ),
                reverse=True,
            )
        return []

    def _format_user_profile_fact(self, item: Any) -> str:
        fact_key = str(getattr(item, "fact_key", "") or "")
        value = getattr(item, "semantic_value", None)
        if value is None:
            value = getattr(item, "value", None)
        value_text = str(value or "").strip()
        claim = str(getattr(item, "claim", "") or "").strip()
        if fact_key == "user.response_style" and value_text:
            return "bạn từng góp ý về cách tôi trả lời"
        if fact_key == "user.study_field" and value_text:
            return f"bạn từng nhắc việc học/lĩnh vực học: {value_text}"
        if value_text:
            spec = self.registry.by_fact_key(fact_key)
            label = spec.display_name if spec is not None else "thông tin"
            return f"{label}: {value_text}" if fact_key else value_text
        return claim if claim and len(claim) <= 160 else ""

    def _lookup_fact(self, memory_core: Any, fact_key: str) -> Any:
        if memory_core is None:
            return None

        # Adapter mềm cho nhiều shape memory_core khác nhau.
        for method in ("lookup_fact", "get_fact", "semantic_lookup"):
            fn = getattr(memory_core, method, None)
            if callable(fn):
                try:
                    value = fn(fact_key)
                    if value is not None:
                        unwrapped = _unwrap_fact_value(value)
                        if unwrapped is not None:
                            return unwrapped
                except Exception:
                    pass

        # Dict memory state.
        if isinstance(memory_core, dict):
            value = _deep_get(memory_core, fact_key)
            if value is not None:
                return _unwrap_fact_value(value)

        # memory_core.recall(query) nếu có.
        recall = getattr(memory_core, "recall", None)
        if callable(recall):
            try:
                result = recall(fact_key)
                return _unwrap_fact_value(result)
            except Exception:
                return None

        return None

    def _format_fact(self, fact_key: str, value: Any) -> str:
        """Surface fact qua registry duy nhất.

        Executor không còn phải biết tên, tháng sinh, giới tính hay thuộc tính mới
        nào. Thêm predicate mới ở registry là đủ để query và trả lời tự nhiên.
        """

        return self.registry.render_fact(fact_key, value)

    # ------------------------------------------------------------------
    # Summary
    # ------------------------------------------------------------------

    def _execute_summary(self, ir: MemoryQueryIR, graph: TurnGraph) -> RawRecallResult:
        nodes = graph.select(ir.target)
        if not nodes:
            nodes = graph.nodes[-6:]
        if not nodes:
            return RawRecallResult.missing(ir, "no_turns_to_summarize", MemorySourceKind.SESSION_TURN_GRAPH)

        parts = []
        for node in nodes[-6:]:
            short = node.text.strip()
            if len(short) > 100:
                short = short[:97] + "..."
            parts.append(f"{node.role.value}: {short}")

        gist = "Gần đây mạch hội thoại gồm: " + " / ".join(parts)
        return RawRecallResult(
            found=True,
            source=MemorySourceKind.SESSION_TURN_GRAPH,
            ir=ir,
            items=tuple(nodes[-6:]),
            gist=gist,
            confidence=min(0.75, ir.confidence),
            evidence=tuple(f"turn:{n.id}" for n in nodes[-6:]),
        )


# ---------------------------------------------------------------------------
# Generic adapters
# ---------------------------------------------------------------------------


def _unwrap_fact_value(value: Any) -> Any:
    if value is None:
        return None
    if _looks_like_internal_memory_object(value):
        return None
    if isinstance(value, dict):
        for key in ("value", "content", "text", "claim"):
            if key in value:
                return _unwrap_fact_value(value[key])
        return None
    if isinstance(value, (list, tuple, set)):
        if len(value) == 1:
            return _unwrap_fact_value(next(iter(value)))
        return None
    if isinstance(value, str) and _looks_like_internal_memory_surface(value):
        return None
    if isinstance(value, (str, int, float, bool)):
        return value
    for attr in ("value", "content", "text", "claim"):
        if hasattr(value, attr):
            return _unwrap_fact_value(getattr(value, attr))
    return None


def _looks_like_internal_memory_object(value: Any) -> bool:
    type_name = type(value).__name__
    if type_name in {"MemoryPacket", "RawRecallResult", "RecallAnswer"}:
        return True
    packet_fields = {
        "episodic",
        "semantic",
        "relational",
        "affective_residue",
        "procedural",
        "repair",
        "token_budget",
        "source_counts",
    }
    return sum(1 for field in packet_fields if hasattr(value, field)) >= 3


def _looks_like_internal_memory_surface(text: str) -> bool:
    one_line = " ".join(str(text or "").split())
    return (
        "MemoryPacket(" in one_line
        or "RawRecallResult(" in one_line
        or "RecallAnswer(" in one_line
        or "QueryHypothesis(" in one_line
        or "MemoryQueryIR(" in one_line
    )


def _deep_get(data: dict, fact_key: str) -> Any:
    # Hỗ trợ cả {"user.birth_month": 8} và {"user": {"birth_month": 8}}
    if fact_key in data:
        return data[fact_key]
    cur: Any = data
    for part in fact_key.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return None
        cur = cur[part]
    return cur
