from __future__ import annotations

"""
memory_query_ir.py

Ngôn ngữ trung gian (IR / AST) cho truy vấn ký ức của ERIS.

Mục tiêu:
- Không để câu hỏi nhớ-lại đi thẳng vào keyword route.
- Dịch câu user thành một cấu trúc truy vấn rõ nghĩa.
- Tách ba việc:
  1. Hiểu user đang hỏi ký ức nào.
  2. Thực thi truy vấn trên TurnGraph / memory_core.
  3. Quyết định surface ra public bao nhiêu.

IR này cố tình độc lập với runtime hiện tại để bạn có thể nối dần.
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional


# ---------------------------------------------------------------------------
# Enum nền
# ---------------------------------------------------------------------------


class QueryIntent(str, Enum):
    """Loại truy vấn ký ức ở cấp cao."""

    RECALL_TURN = "recall_turn"
    RECALL_FACT = "recall_fact"
    RECALL_COUNT = "recall_count"
    RECALL_SUMMARY = "recall_summary"
    VERIFY_MEMORY = "verify_memory"
    EXPLAIN_MEMORY_FAILURE = "explain_memory_failure"
    UNKNOWN = "unknown"


class RoleRef(str, Enum):
    """Role mà query đang trỏ tới."""

    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"
    ANY = "any"


class ActRef(str, Enum):
    """Hành vi hội thoại mà query cần lấy."""

    QUESTION = "question"
    ANSWER = "answer"
    STATEMENT = "statement"
    FACT_DISCLOSURE = "fact_disclosure"
    COMMAND = "command"
    CORRECTION = "correction"
    REPAIR = "repair"
    FALLBACK = "fallback"
    ANY = "any"


class RelationRef(str, Enum):
    """
    Quan hệ giữa target và anchor.

    Ví dụ:
    - SELF: lấy chính target.
    - ANSWER_TO: lấy câu assistant trả lời cho một câu hỏi user.
    - BEFORE/AFTER: lấy turn trước/sau anchor.
    """

    SELF = "self"
    BEFORE = "before"
    AFTER = "after"
    ANSWER_TO = "answer_to"
    REFERS_TO = "refers_to"
    CORRECTION_OF = "correction_of"
    RECENT_PREVIOUS = "recent_previous"
    UNKNOWN = "unknown"


class SurfaceMode(str, Enum):
    """Cách được phép surface câu trả lời ra public."""

    EXACT = "exact"
    GIST = "gist"
    COUNT = "count"
    EXPLAIN = "explain"
    ADMIT_UNCERTAIN = "admit_uncertain"


class MemorySourceKind(str, Enum):
    """Nguồn ký ức mà executor dùng để trả."""

    SESSION_TURN_GRAPH = "session_turn_graph"
    SESSION_LEDGER = "session_ledger"
    SEMANTIC_MEMORY = "semantic_memory"
    EPISODIC_MEMORY = "episodic_memory"
    REPAIR_MEMORY = "repair_memory"
    MIXED = "mixed"
    NONE = "none"


# ---------------------------------------------------------------------------
# IR dataclass
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class MemorySelector:
    """
    Bộ chọn node/fact trong memory.

    Lưu ý:
    - ordinal dùng cho exact ordinal như "câu hỏi thứ 3".
    - quantity dùng cho recent batch như "3 câu hỏi trước".
    - semantic_hint dùng cho anchor mơ hồ như "câu hỏi về ưu tiên".
    - fact_key dùng cho semantic fact như "user.birth_month".
    """

    role: RoleRef = RoleRef.ANY
    act: ActRef = ActRef.ANY
    ordinal: Optional[int] = None
    quantity: Optional[int] = None
    recent: bool = False
    fact_key: Optional[str] = None
    semantic_hint: Optional[str] = None
    tags: tuple[str, ...] = ()

    def is_empty(self) -> bool:
        return (
            self.role == RoleRef.ANY
            and self.act == ActRef.ANY
            and self.ordinal is None
            and self.quantity is None
            and not self.recent
            and not self.fact_key
            and not self.semantic_hint
            and not self.tags
        )

    def compact(self) -> str:
        parts: list[str] = []
        if self.role != RoleRef.ANY:
            parts.append(f"role={self.role.value}")
        if self.act != ActRef.ANY:
            parts.append(f"act={self.act.value}")
        if self.ordinal is not None:
            parts.append(f"ordinal={self.ordinal}")
        if self.quantity is not None:
            parts.append(f"quantity={self.quantity}")
        if self.recent:
            parts.append("recent=true")
        if self.fact_key:
            parts.append(f"fact={self.fact_key}")
        if self.semantic_hint:
            parts.append(f"semantic={self.semantic_hint}")
        if self.tags:
            parts.append("tags=" + ",".join(self.tags))
        return "Selector(" + "; ".join(parts) + ")"


@dataclass(slots=True)
class MemoryRelation:
    """Quan hệ query giữa target và anchor."""

    relation: RelationRef = RelationRef.SELF
    anchor: Optional[MemorySelector] = None

    def compact(self) -> str:
        if self.anchor:
            return f"{self.relation.value} -> {self.anchor.compact()}"
        return self.relation.value


@dataclass(slots=True)
class MemoryQueryIR:
    """
    AST truy vấn ký ức.

    Đây là output của compiler, input của executor.
    Không nên chứa text answer. IR chỉ nói "cần lấy cái gì".
    """

    intent: QueryIntent
    target: MemorySelector
    relation: MemoryRelation = field(default_factory=MemoryRelation)
    surface: SurfaceMode = SurfaceMode.EXACT
    confidence: float = 0.0
    ambiguity: float = 1.0
    intercept_permission: bool = False
    evidence: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    raw_text: str = ""

    def normalized(self) -> "MemoryQueryIR":
        """Trả bản sao với confidence/ambiguity được kẹp vào [0, 1]."""
        return MemoryQueryIR(
            intent=self.intent,
            target=self.target,
            relation=self.relation,
            surface=self.surface,
            confidence=clamp01(self.confidence),
            ambiguity=clamp01(self.ambiguity),
            intercept_permission=bool(self.intercept_permission),
            evidence=self.evidence,
            warnings=self.warnings,
            raw_text=self.raw_text,
        )

    def signature(self) -> str:
        """
        Chữ ký cấu trúc để repair prior so khớp.

        Không dùng raw_text, vì mục tiêu là học từ cấu trúc query chứ không học
        vẹt một câu cụ thể.
        """
        anchor_sig = self.relation.anchor.compact() if self.relation.anchor else "none"
        return (
            f"{self.intent.value}|{self.target.compact()}|"
            f"{self.relation.relation.value}|{anchor_sig}|{self.surface.value}"
        )

    def compact(self) -> str:
        return (
            f"MemoryQueryIR(intent={self.intent.value}, "
            f"target={self.target.compact()}, "
            f"relation={self.relation.compact()}, "
            f"surface={self.surface.value}, "
            f"conf={self.confidence:.2f}, amb={self.ambiguity:.2f})"
        )


@dataclass(slots=True)
class QueryHypothesis:
    """
    Một giả thuyết query trong quá trình compile.

    Compiler nên sinh nhiều hypothesis, xếp hạng, rồi chọn best.
    Đây là chống "chọn nghĩa quá sớm".
    """

    ir: MemoryQueryIR
    score: float
    source: str
    reasons: tuple[str, ...] = ()
    penalties: tuple[str, ...] = ()

    def normalized(self) -> "QueryHypothesis":
        return QueryHypothesis(
            ir=self.ir.normalized(),
            score=clamp01(self.score),
            source=self.source,
            reasons=self.reasons,
            penalties=self.penalties,
        )


# ---------------------------------------------------------------------------
# Kết quả executor / policy
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class RawRecallResult:
    """
    Kết quả thô sau khi executor chạy query.

    Đây chưa phải câu public cuối cùng. Policy còn phải quyết định quote/gist/
    uncertain.
    """

    found: bool
    source: MemorySourceKind
    ir: MemoryQueryIR
    items: tuple[Any, ...] = ()
    exact_text: Optional[str] = None
    gist: Optional[str] = None
    count: Optional[int] = None
    confidence: float = 0.0
    evidence: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()

    @classmethod
    def missing(
        cls,
        ir: MemoryQueryIR,
        warning: str,
        source: MemorySourceKind = MemorySourceKind.NONE,
    ) -> "RawRecallResult":
        return cls(
            found=False,
            source=source,
            ir=ir,
            confidence=0.0,
            warnings=(warning,),
        )


@dataclass(slots=True)
class RecallAnswer:
    """
    Câu trả lời đã được policy surface.

    Adapter có thể đổi object này thành Decision payload.
    """

    text: str
    confidence: float
    source: MemorySourceKind
    surface: SurfaceMode
    ir: Optional[MemoryQueryIR] = None
    debug_trace: str = ""
    should_speak: bool = True
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def uncertain(
        cls,
        text: str = "Tôi chưa đủ chắc để lần lại ký ức đó.",
        *,
        ir: Optional[MemoryQueryIR] = None,
        debug_trace: str = "recall_uncertain",
    ) -> "RecallAnswer":
        return cls(
            text=text,
            confidence=0.0,
            source=MemorySourceKind.NONE,
            surface=SurfaceMode.ADMIT_UNCERTAIN,
            ir=ir,
            debug_trace=debug_trace,
        )


# ---------------------------------------------------------------------------
# Helper nhỏ, không chứa policy
# ---------------------------------------------------------------------------


def clamp01(value: float) -> float:
    try:
        return max(0.0, min(1.0, float(value)))
    except Exception:
        return 0.0


def blend_confidence(*values: float, default: float = 0.0) -> float:
    """Trung bình mềm cho các confidence hợp lệ."""
    valid = [clamp01(v) for v in values if v is not None]
    if not valid:
        return default
    return clamp01(sum(valid) / len(valid))


def add_warning(ir: MemoryQueryIR, warning: str) -> MemoryQueryIR:
    return MemoryQueryIR(
        intent=ir.intent,
        target=ir.target,
        relation=ir.relation,
        surface=ir.surface,
        confidence=ir.confidence,
        ambiguity=ir.ambiguity,
        evidence=ir.evidence,
        warnings=tuple((*ir.warnings, warning)),
        raw_text=ir.raw_text,
    )
