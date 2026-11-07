from __future__ import annotations

"""
memory_turn_graph.py

TurnGraph là graph ký ức phiên hiện tại.

Khác với ledger dạng list:
- Ledger biết "turn thứ mấy".
- Graph biết "turn nào trả lời turn nào", "turn nào sửa turn nào",
  "turn nào lặp lại", "turn nào lưu fact".

Điểm quan trọng:
- Graph không hiểu tiếng Việt. Nó chỉ lưu node/edge đã được annotator gắn nhãn.
- Query executor chạy trên graph, không route bằng keyword user text.
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Iterable, Optional

from .memory_query_ir import ActRef, MemorySelector, RelationRef, RoleRef


class TurnRole(str, Enum):
    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"
    UNKNOWN = "unknown"


class TurnSpeechAct(str, Enum):
    QUESTION = "question"
    ANSWER = "answer"
    STATEMENT = "statement"
    FACT_DISCLOSURE = "fact_disclosure"
    COMMAND = "command"
    CORRECTION = "correction"
    REPAIR = "repair"
    FALLBACK = "fallback"
    UNKNOWN = "unknown"


class TurnEdgeType(str, Enum):
    NEXT = "next"
    ANSWERS = "answers"
    REFERS_TO = "refers_to"
    CORRECTS = "corrects"
    REPEATS = "repeats"
    CLARIFIES = "clarifies"
    STORES_FACT = "stores_fact"


@dataclass(slots=True)
class FactMention:
    """Fact được phát hiện trong turn, ví dụ user.birth_month=8."""

    key: str
    value: Any
    confidence: float = 0.8
    evidence: tuple[str, ...] = ()


@dataclass(slots=True)
class TurnNode:
    """
    Node hội thoại.

    index là thứ tự toàn phiên, bắt đầu 1.
    role_index là thứ tự trong cùng role, ví dụ user turn thứ 3.
    act_index là thứ tự theo role+speech_act, ví dụ user question thứ 2.
    """

    id: str
    index: int
    role: TurnRole
    text: str
    timestamp: float
    speech_act: TurnSpeechAct = TurnSpeechAct.UNKNOWN
    semantic_tags: tuple[str, ...] = ()
    fact_mentions: tuple[FactMention, ...] = ()
    confidence: float = 1.0
    metadata: dict[str, Any] = field(default_factory=dict)
    role_index: Optional[int] = None
    act_index: Optional[int] = None

    @property
    def counts_as_answer(self) -> bool:
        """Fallback/service-error không nên bị tính là câu trả lời nội dung."""
        if self.speech_act == TurnSpeechAct.FALLBACK:
            return False
        if self.metadata.get("is_service_fallback") is True:
            return False
        if self.metadata.get("counts_as_answer") is False:
            return False
        return self.role == TurnRole.ASSISTANT and bool(self.text.strip())

    @property
    def is_question(self) -> bool:
        return self.role == TurnRole.USER and self.speech_act == TurnSpeechAct.QUESTION


@dataclass(slots=True)
class TurnEdge:
    """Cạnh quan hệ giữa hai turn."""

    type: TurnEdgeType
    source_id: str
    target_id: str
    confidence: float = 1.0
    metadata: dict[str, Any] = field(default_factory=dict)


class TurnGraph:
    """
    Graph phiên hiện tại.

    Dùng để thực thi query exact:
    - nth user question
    - assistant answer to nth user question
    - last N user questions
    - count user questions
    """

    def __init__(self) -> None:
        self.nodes: list[TurnNode] = []
        self.edges: list[TurnEdge] = []
        self._by_id: dict[str, TurnNode] = {}
        self._role_counts: dict[TurnRole, int] = {}
        self._act_counts: dict[tuple[TurnRole, TurnSpeechAct], int] = {}

    # ------------------------------------------------------------------
    # Build graph
    # ------------------------------------------------------------------

    def add_turn(self, node: TurnNode) -> TurnNode:
        if node.id in self._by_id:
            raise ValueError(f"Turn id đã tồn tại: {node.id}")

        role_index = self._role_counts.get(node.role, 0) + 1
        self._role_counts[node.role] = role_index

        act_index = self._act_counts.get((node.role, node.speech_act), 0) + 1
        self._act_counts[(node.role, node.speech_act)] = act_index

        stored = TurnNode(
            id=node.id,
            index=node.index,
            role=node.role,
            text=node.text,
            timestamp=node.timestamp,
            speech_act=node.speech_act,
            semantic_tags=node.semantic_tags,
            fact_mentions=node.fact_mentions,
            confidence=node.confidence,
            metadata=dict(node.metadata),
            role_index=role_index,
            act_index=act_index,
        )

        if self.nodes:
            prev = self.nodes[-1]
            self.add_edge(
                TurnEdge(
                    type=TurnEdgeType.NEXT,
                    source_id=prev.id,
                    target_id=stored.id,
                    confidence=1.0,
                )
            )

        self.nodes.append(stored)
        self._by_id[stored.id] = stored
        return stored

    def add_edge(self, edge: TurnEdge) -> None:
        if edge.source_id not in self._by_id and edge.type != TurnEdgeType.NEXT:
            # NEXT có thể được add trong add_turn sau khi source có sẵn.
            pass
        self.edges.append(edge)

    def get(self, node_id: str) -> Optional[TurnNode]:
        return self._by_id.get(node_id)

    @classmethod
    def from_turns(cls, turns: Iterable[TurnNode]) -> "TurnGraph":
        graph = cls()
        for turn in turns:
            graph.add_turn(turn)
        return graph

    @classmethod
    def from_session_ledger(
        cls,
        session_ledger: Any,
        *,
        annotator: Optional[Any] = None,
    ) -> "TurnGraph":
        """
        Adapter mềm từ session_turn_ledger hiện tại.

        Không ép shape cụ thể. Chỉ cần ledger có:
        - turns
        - all_turns()
        hoặc iterable
        với field index/role/text/timestamp/is_question/meta.
        """
        graph = cls()
        raw_turns = getattr(session_ledger, "turns", None)
        if raw_turns is None:
            all_turns = getattr(session_ledger, "all_turns", None)
            raw_turns = all_turns() if callable(all_turns) else session_ledger
        for i, raw in enumerate(raw_turns, start=1):
            role = _role_from_any(getattr(raw, "role", None) or _get(raw, "role"))
            text = str(getattr(raw, "text", None) or _get(raw, "text") or "")
            timestamp = float(getattr(raw, "timestamp", None) or _get(raw, "timestamp") or 0.0)
            metadata = dict(getattr(raw, "meta", None) or getattr(raw, "metadata", None) or _get(raw, "meta") or {})

            if annotator:
                ann = annotator.annotate(role=role, text=text, metadata=metadata)
                speech_act = ann.speech_act
                semantic_tags = ann.semantic_tags
                fact_mentions = ann.fact_mentions
                confidence = ann.confidence
            else:
                is_question = bool(getattr(raw, "is_question", False) or _get(raw, "is_question", False))
                speech_act = TurnSpeechAct.QUESTION if is_question else TurnSpeechAct.UNKNOWN
                semantic_tags = ()
                fact_mentions = ()
                confidence = 0.55

            node = TurnNode(
                id=str(getattr(raw, "id", None) or _get(raw, "id") or f"turn-{i}"),
                index=int(getattr(raw, "index", None) or _get(raw, "index") or i),
                role=role,
                text=text,
                timestamp=timestamp,
                speech_act=speech_act,
                semantic_tags=semantic_tags,
                fact_mentions=fact_mentions,
                confidence=confidence,
                metadata=metadata,
            )
            graph.add_turn(node)

        if annotator:
            annotator.build_edges(graph)
        else:
            graph.infer_basic_answer_edges()

        return graph

    # ------------------------------------------------------------------
    # Edge inference tối thiểu
    # ------------------------------------------------------------------

    def infer_basic_answer_edges(self) -> None:
        """Nối assistant answer với user question gần nhất trước đó."""
        last_question: Optional[TurnNode] = None
        for node in self.nodes:
            if node.is_question:
                last_question = node
                continue
            if node.role == TurnRole.ASSISTANT and node.counts_as_answer and last_question:
                self.add_edge(
                    TurnEdge(
                        type=TurnEdgeType.ANSWERS,
                        source_id=node.id,
                        target_id=last_question.id,
                        confidence=min(node.confidence, last_question.confidence, 0.86),
                        metadata={"rule": "nearest_previous_user_question"},
                    )
                )
                last_question = None

    # ------------------------------------------------------------------
    # Query helpers
    # ------------------------------------------------------------------

    def select(self, selector: MemorySelector) -> list[TurnNode]:
        """Chọn nodes theo MemorySelector. Không hiểu raw text user query."""
        nodes = self.nodes

        role = _role_ref_to_turn(selector.role)
        if role:
            nodes = [n for n in nodes if n.role == role]

        act = _act_ref_to_turn(selector.act)
        if act:
            nodes = [n for n in nodes if n.speech_act == act]

        if selector.fact_key:
            nodes = [
                n
                for n in nodes
                if any(f.key == selector.fact_key for f in n.fact_mentions)
            ]

        if selector.semantic_hint:
            hint = _norm(selector.semantic_hint)
            nodes = sorted(
                nodes,
                key=lambda n: _text_relevance(hint, n.text, n.semantic_tags),
                reverse=True,
            )
            nodes = [n for n in nodes if _text_relevance(hint, n.text, n.semantic_tags) > 0.12]

        if selector.ordinal is not None:
            if selector.ordinal <= 0:
                return []
            return [nodes[selector.ordinal - 1]] if len(nodes) >= selector.ordinal else []

        if selector.quantity is not None:
            q = max(0, selector.quantity)
            if selector.recent:
                return nodes[-q:] if q else []
            return nodes[:q]

        if selector.recent:
            return nodes[-1:] if nodes else []

        return nodes

    def first_assistant_answer_to(self, user_question_id: str) -> Optional[TurnNode]:
        """Lấy assistant node có edge ANSWERS -> user_question_id."""
        candidates: list[tuple[float, TurnNode]] = []
        for edge in self.edges:
            if edge.type != TurnEdgeType.ANSWERS or edge.target_id != user_question_id:
                continue
            node = self.get(edge.source_id)
            if node and node.role == TurnRole.ASSISTANT and node.counts_as_answer:
                candidates.append((edge.confidence, node))
        candidates.sort(key=lambda item: (item[0], -item[1].index), reverse=True)
        return candidates[0][1] if candidates else None

    def neighbor_after(self, node_id: str, selector: MemorySelector) -> Optional[TurnNode]:
        anchor = self.get(node_id)
        if not anchor:
            return None
        candidates = [n for n in self.select(selector) if n.index > anchor.index]
        return candidates[0] if candidates else None

    def neighbor_before(self, node_id: str, selector: MemorySelector) -> Optional[TurnNode]:
        anchor = self.get(node_id)
        if not anchor:
            return None
        candidates = [n for n in self.select(selector) if n.index < anchor.index]
        return candidates[-1] if candidates else None

    def count(self, selector: MemorySelector) -> int:
        return len(self.select(selector))

    def last_n(self, selector: MemorySelector, n: int) -> list[TurnNode]:
        sel = MemorySelector(
            role=selector.role,
            act=selector.act,
            quantity=max(0, n),
            recent=True,
            fact_key=selector.fact_key,
            semantic_hint=selector.semantic_hint,
            tags=selector.tags,
        )
        return self.select(sel)


# ---------------------------------------------------------------------------
# Mapping helpers
# ---------------------------------------------------------------------------


def _get(obj: Any, key: str, default: Any = None) -> Any:
    if isinstance(obj, dict):
        return obj.get(key, default)
    return default


def _role_from_any(value: Any) -> TurnRole:
    if isinstance(value, TurnRole):
        return value
    text = str(value or "").lower()
    if text == "user":
        return TurnRole.USER
    if text == "assistant":
        return TurnRole.ASSISTANT
    if text == "system":
        return TurnRole.SYSTEM
    return TurnRole.UNKNOWN


def _role_ref_to_turn(role: RoleRef) -> Optional[TurnRole]:
    if role == RoleRef.USER:
        return TurnRole.USER
    if role == RoleRef.ASSISTANT:
        return TurnRole.ASSISTANT
    if role == RoleRef.SYSTEM:
        return TurnRole.SYSTEM
    return None


def _act_ref_to_turn(act: ActRef) -> Optional[TurnSpeechAct]:
    mapping = {
        ActRef.QUESTION: TurnSpeechAct.QUESTION,
        ActRef.ANSWER: TurnSpeechAct.ANSWER,
        ActRef.STATEMENT: TurnSpeechAct.STATEMENT,
        ActRef.FACT_DISCLOSURE: TurnSpeechAct.FACT_DISCLOSURE,
        ActRef.COMMAND: TurnSpeechAct.COMMAND,
        ActRef.CORRECTION: TurnSpeechAct.CORRECTION,
        ActRef.REPAIR: TurnSpeechAct.REPAIR,
        ActRef.FALLBACK: TurnSpeechAct.FALLBACK,
    }
    return mapping.get(act)


def _norm(text: str) -> str:
    import re
    import unicodedata

    text = unicodedata.normalize("NFD", text.lower())
    text = "".join(ch for ch in text if unicodedata.category(ch) != "Mn")
    text = re.sub(r"[^a-z0-9\s_.-]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _text_relevance(hint: str, text: str, tags: tuple[str, ...]) -> float:
    """Relevance lexical nhẹ cho semantic_hint. Không dùng cho ordinal."""
    if not hint:
        return 0.0
    h_tokens = set(_norm(hint).split())
    t_tokens = set(_norm(text).split()) | { _norm(t) for t in tags }
    if not h_tokens or not t_tokens:
        return 0.0
    overlap = len(h_tokens & t_tokens) / max(1, len(h_tokens))
    containment = 1.0 if hint in _norm(text) else 0.0
    return min(1.0, overlap * 0.75 + containment * 0.25)
