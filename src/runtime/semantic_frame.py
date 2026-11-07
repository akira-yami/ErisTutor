# -*- coding: utf-8 -*-
"""Contract typed duy nhất cho semantic frame và assistant act.

Module này là ranh giới parse JSON từ model. Sau khi đi qua đây, runtime dùng
object typed thay vì đọc lại những ``dict[str, Any]`` rải rác. Các lớp vẫn triển
khai ``Mapping`` để code tương thích cũ có thể gọi ``dict(frame)`` trong giai
đoạn chuyển đổi, nhưng nguồn chân lý chỉ còn một bộ enum và một validator.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from enum import Enum
from typing import Any


class _TextEnum(str, Enum):
    """Enum chuỗi có output JSON ổn định."""

    def __str__(self) -> str:
        return self.value


class ReferenceKind(_TextEnum):
    NONE = "none"
    RECENT_USER_TURN = "recent_user_turn"
    RECENT_ASSISTANT_TURN = "recent_assistant_turn"
    ASSISTANT_PROPOSITION = "assistant_proposition"
    OPEN_QUESTION = "open_question"
    LONG_TERM_MEMORY = "long_term_memory"
    CURRENT_MESSAGE = "current_message"


class ReferenceSpeaker(_TextEnum):
    NONE = "none"
    USER = "user"
    ASSISTANT = "assistant"


class ReferenceOperation(_TextEnum):
    NONE = "none"
    REPEAT = "repeat"
    RECALL = "recall"
    CLARIFY = "clarify"
    CONFIRM = "confirm"
    REPAIR = "repair"
    ANSWER = "answer"


class MemoryQueryIntent(_TextEnum):
    NONE = "none"
    RECALL_FACT = "recall_fact"
    RECALL_SUMMARY = "recall_summary"
    VERIFY_MEMORY = "verify_memory"
    EXPLAIN_MEMORY = "explain_memory"


class MemoryAnswerMode(_TextEnum):
    EXACT = "exact"
    GIST = "gist"
    SUMMARY = "summary"
    ADMIT_UNCERTAIN = "admit_uncertain"


class AssistantActKind(_TextEnum):
    SILENCE = "silence"
    ACKNOWLEDGEMENT = "acknowledgement"
    SOCIAL = "social"
    ASSERTION = "assertion"
    EXPLANATION = "explanation"
    OPINION = "opinion"
    ANSWER = "answer"
    CONFIRMATION = "confirmation"
    REPAIR = "repair"
    QUESTION = "question"
    ACTION_REPORT = "action_report"
    OTHER = "other"


class PropositionMode(_TextEnum):
    NONE = "none"
    NEW = "new"
    CONTINUE = "continue"


_ASSISTANT_ACT_KIND_ALIASES: dict[str, AssistantActKind] = {
    "accept": AssistantActKind.ANSWER,
    "acceptance": AssistantActKind.ANSWER,
    "accept_invitation": AssistantActKind.ANSWER,
    "accept_and_clarify_condition": AssistantActKind.ANSWER,
    "accept_invitation_with_condition": AssistantActKind.ANSWER,
    "acknowledge_and_question": AssistantActKind.QUESTION,
    # Model đôi khi sinh "clarify" / "clarification" không có trong enum.
    # Semantic gần nhất là QUESTION (đang hỏi lại để làm rõ).
    "clarify": AssistantActKind.QUESTION,
    "clarification": AssistantActKind.QUESTION,
    "request_clarification": AssistantActKind.QUESTION,
}


# Mỗi kind tự định nghĩa speaker và operation hợp lệ. Runtime không còn để một
# field ``speaker`` mâu thuẫn lật ngược nghĩa của ``kind``.
_REFERENCE_RULES: dict[ReferenceKind, tuple[frozenset[ReferenceSpeaker], frozenset[ReferenceOperation], ReferenceOperation]] = {
    ReferenceKind.NONE: (
        frozenset({ReferenceSpeaker.NONE}),
        frozenset({ReferenceOperation.NONE}),
        ReferenceOperation.NONE,
    ),
    ReferenceKind.RECENT_USER_TURN: (
        frozenset({ReferenceSpeaker.USER}),
        frozenset({ReferenceOperation.REPEAT, ReferenceOperation.RECALL, ReferenceOperation.CLARIFY}),
        ReferenceOperation.RECALL,
    ),
    ReferenceKind.RECENT_ASSISTANT_TURN: (
        frozenset({ReferenceSpeaker.ASSISTANT}),
        frozenset({
            ReferenceOperation.REPEAT,
            ReferenceOperation.RECALL,
            ReferenceOperation.CLARIFY,
            ReferenceOperation.CONFIRM,
            ReferenceOperation.REPAIR,
        }),
        ReferenceOperation.RECALL,
    ),
    ReferenceKind.ASSISTANT_PROPOSITION: (
        frozenset({ReferenceSpeaker.ASSISTANT}),
        frozenset({
            ReferenceOperation.RECALL,
            ReferenceOperation.CLARIFY,
            ReferenceOperation.CONFIRM,
            ReferenceOperation.REPAIR,
        }),
        ReferenceOperation.CLARIFY,
    ),
    ReferenceKind.OPEN_QUESTION: (
        frozenset({ReferenceSpeaker.USER, ReferenceSpeaker.ASSISTANT}),
        frozenset({ReferenceOperation.ANSWER, ReferenceOperation.CLARIFY, ReferenceOperation.REPAIR}),
        ReferenceOperation.ANSWER,
    ),
    ReferenceKind.LONG_TERM_MEMORY: (
        frozenset({ReferenceSpeaker.NONE}),
        frozenset({ReferenceOperation.RECALL, ReferenceOperation.ANSWER}),
        ReferenceOperation.RECALL,
    ),
    ReferenceKind.CURRENT_MESSAGE: (
        frozenset({ReferenceSpeaker.USER}),
        frozenset({ReferenceOperation.CLARIFY, ReferenceOperation.ANSWER}),
        ReferenceOperation.ANSWER,
    ),
}

_REFERENCE_DEFAULT_SPEAKER: dict[ReferenceKind, ReferenceSpeaker] = {
    ReferenceKind.NONE: ReferenceSpeaker.NONE,
    ReferenceKind.RECENT_USER_TURN: ReferenceSpeaker.USER,
    ReferenceKind.RECENT_ASSISTANT_TURN: ReferenceSpeaker.ASSISTANT,
    ReferenceKind.ASSISTANT_PROPOSITION: ReferenceSpeaker.ASSISTANT,
    ReferenceKind.OPEN_QUESTION: ReferenceSpeaker.USER,
    ReferenceKind.LONG_TERM_MEMORY: ReferenceSpeaker.NONE,
    ReferenceKind.CURRENT_MESSAGE: ReferenceSpeaker.USER,
}

_REFERENCE_KIND_ALIASES: dict[str, ReferenceKind] = {
    "continue": ReferenceKind.RECENT_ASSISTANT_TURN,
    "continuation": ReferenceKind.RECENT_ASSISTANT_TURN,
    "previous_assistant": ReferenceKind.RECENT_ASSISTANT_TURN,
    "previous_assistant_turn": ReferenceKind.RECENT_ASSISTANT_TURN,
    "assistant_turn": ReferenceKind.RECENT_ASSISTANT_TURN,
}


_PROPOSITIONAL_ASSISTANT_ACTS = frozenset(
    {
        AssistantActKind.ASSERTION,
        AssistantActKind.EXPLANATION,
        AssistantActKind.OPINION,
        AssistantActKind.ANSWER,
        AssistantActKind.CONFIRMATION,
        AssistantActKind.REPAIR,
        AssistantActKind.QUESTION,
        AssistantActKind.ACTION_REPORT,
    }
)


@dataclass(frozen=True, slots=True)
class DiscourseReference(Mapping[str, Any]):
    """Tham chiếu đã qua compatibility matrix."""

    kind: ReferenceKind = ReferenceKind.NONE
    speaker: ReferenceSpeaker = ReferenceSpeaker.NONE
    operation: ReferenceOperation = ReferenceOperation.NONE
    turn_index: int = 0
    turn_offset: int = 0
    confidence: float = 0.0

    @property
    def is_session_reference(self) -> bool:
        return self.kind in {
            ReferenceKind.RECENT_USER_TURN,
            ReferenceKind.RECENT_ASSISTANT_TURN,
            ReferenceKind.ASSISTANT_PROPOSITION,
            ReferenceKind.OPEN_QUESTION,
        }

    @property
    def requests_long_term_memory(self) -> bool:
        return (
            self.kind is ReferenceKind.LONG_TERM_MEMORY
            and self.operation in {ReferenceOperation.RECALL, ReferenceOperation.ANSWER}
            and self.confidence >= 0.60
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind.value,
            "speaker": self.speaker.value,
            "operation": self.operation.value,
            "turn_index": self.turn_index,
            "turn_offset": self.turn_offset,
            "confidence": self.confidence,
        }

    def __getitem__(self, key: str) -> Any:
        return self.to_dict()[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self.to_dict())

    def __len__(self) -> int:
        return 6


@dataclass(frozen=True, slots=True)
class ReplyObligation(Mapping[str, bool]):
    must_answer: bool = True
    must_not_broad_clarify: bool = False
    must_not_call_tool: bool = False

    def to_dict(self) -> dict[str, bool]:
        return {
            "must_answer": self.must_answer,
            "must_not_broad_clarify": self.must_not_broad_clarify,
            "must_not_call_tool": self.must_not_call_tool,
        }

    def __getitem__(self, key: str) -> bool:
        return self.to_dict()[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self.to_dict())

    def __len__(self) -> int:
        return 3


@dataclass(frozen=True, slots=True)
class MemoryQueryFrame(Mapping[str, Any]):
    """Query dài hạn do semantic authority tạo; memory không đọc lại raw text."""

    intent: MemoryQueryIntent = MemoryQueryIntent.NONE
    subject: str = ""
    predicate_hint: str = ""
    object_hint: str = ""
    temporal_scope: str = "current"
    answer_mode: MemoryAnswerMode = MemoryAnswerMode.EXACT
    confidence: float = 0.0

    @property
    def usable(self) -> bool:
        if self.confidence < 0.60:
            return False
        if self.intent is MemoryQueryIntent.RECALL_FACT:
            return bool(self.predicate_hint)
        return self.intent is not MemoryQueryIntent.NONE

    def to_dict(self) -> dict[str, Any]:
        return {
            "intent": self.intent.value,
            "subject": self.subject,
            "predicate_hint": self.predicate_hint,
            "object_hint": self.object_hint,
            "temporal_scope": self.temporal_scope,
            "answer_mode": self.answer_mode.value,
            "confidence": self.confidence,
        }

    def __getitem__(self, key: str) -> Any:
        return self.to_dict()[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self.to_dict())

    def __len__(self) -> int:
        return 7


@dataclass(frozen=True, slots=True, eq=False)
class SemanticFrame(Mapping[str, Any]):
    """Object semantic typed dùng xuyên runtime sau boundary model."""

    version: str = "semantic_frame.v1"
    turn_kind: str = ""
    discourse_relation: str = ""
    user_act: str = ""
    target: str = ""
    literal_question: bool = False
    ambiguity_owner: str = "none"
    boundary_action: str = ""
    continuation_key: str = ""
    evidence_scope: str = ""
    tool_intent: str = ""
    missing_evidence: tuple[str, ...] = ()
    reference: DiscourseReference = DiscourseReference()
    reply_obligation: ReplyObligation = ReplyObligation()
    memory_query: MemoryQueryFrame = MemoryQueryFrame()

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "turn_kind": self.turn_kind,
            "discourse_relation": self.discourse_relation,
            "user_act": self.user_act,
            "target": self.target,
            "literal_question": self.literal_question,
            "ambiguity_owner": self.ambiguity_owner,
            "boundary_action": self.boundary_action,
            "continuation_key": self.continuation_key,
            "evidence_scope": self.evidence_scope,
            "tool_intent": self.tool_intent,
            "missing_evidence": list(self.missing_evidence),
            "reference": self.reference.to_dict(),
            "reply_obligation": self.reply_obligation.to_dict(),
            "memory_query": self.memory_query.to_dict(),
        }

    def __getitem__(self, key: str) -> Any:
        return self.to_dict()[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self.to_dict())

    def __len__(self) -> int:
        return 15

    def __eq__(self, other: object) -> bool:
        if isinstance(other, SemanticFrame):
            return self.to_dict() == other.to_dict()
        if isinstance(other, Mapping):
            return self.to_dict() == dict(other)
        return NotImplemented


@dataclass(frozen=True, slots=True)
class AssistantAct(Mapping[str, Any]):
    """Mô tả hành vi của câu assistant, tách khỏi frame của user."""

    kind: AssistantActKind = AssistantActKind.OTHER
    proposition_mode: PropositionMode = PropositionMode.NONE
    confidence: float = 0.0

    @property
    def is_propositional(self) -> bool:
        return self.kind in _PROPOSITIONAL_ASSISTANT_ACTS

    @property
    def opens_question(self) -> bool:
        return self.kind is AssistantActKind.QUESTION

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind.value,
            "proposition_mode": self.proposition_mode.value,
            "confidence": self.confidence,
        }

    def __getitem__(self, key: str) -> Any:
        return self.to_dict()[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self.to_dict())

    def __len__(self) -> int:
        return 3


@dataclass(frozen=True, slots=True)
class SemanticFrameValidation:
    frame: SemanticFrame
    violations: tuple[str, ...]

    @property
    def valid(self) -> bool:
        return not self.violations


@dataclass(frozen=True, slots=True)
class AssistantActValidation:
    act: AssistantAct
    violations: tuple[str, ...]

    @property
    def valid(self) -> bool:
        return not self.violations


def _mapping_or_empty(value: Any, *, field: str, violations: list[str]) -> dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, Mapping):
        return dict(value)
    violations.append(f"{field}_must_be_object:{type(value).__name__}")
    return {}


def _string_list(value: Any, *, field: str, violations: list[str]) -> tuple[str, ...]:
    if value is None or value == "":
        return ()
    if isinstance(value, str):
        item = value.strip()
        return (item,) if item else ()
    if isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
        return tuple(str(item).strip() for item in list(value)[:8] if str(item).strip())
    violations.append(f"{field}_must_be_array:{type(value).__name__}")
    return ()


def _strict_bool(value: Any, *, field: str, default: bool, violations: list[str]) -> bool:
    """Không dùng ``bool('false')``; chuỗi không được xem là boolean."""

    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in (0, 1):
        return bool(value)
    if value is None:
        return default
    violations.append(f"{field}_must_be_boolean:{type(value).__name__}")
    return default


def _bounded_int(value: Any, *, field: str, minimum: int, maximum: int, violations: list[str]) -> int:
    try:
        parsed = int(value or 0)
    except (TypeError, ValueError):
        violations.append(f"{field}_must_be_integer:{type(value).__name__}")
        return minimum
    if parsed < minimum or parsed > maximum:
        violations.append(f"{field}_out_of_range:{parsed}")
    return max(minimum, min(maximum, parsed))


def _bounded_float(value: Any, *, field: str, minimum: float, maximum: float, violations: list[str]) -> float:
    try:
        parsed = float(value or 0.0)
    except (TypeError, ValueError):
        violations.append(f"{field}_must_be_number:{type(value).__name__}")
        return minimum
    if parsed < minimum or parsed > maximum:
        violations.append(f"{field}_out_of_range:{parsed}")
    return max(minimum, min(maximum, parsed))


def _enum(value: Any, enum_type: type[_TextEnum], *, field: str, default: _TextEnum, violations: list[str]):
    text = str(value or default.value).strip().lower()
    try:
        return enum_type(text)
    except ValueError:
        violations.append(f"{field}_invalid:{text or '<empty>'}")
        return default


def _parse_reference(value: Any, violations: list[str]) -> DiscourseReference:
    payload = _mapping_or_empty(value, field="reference", violations=violations)
    raw_kind = str(payload.get("kind", "") or "").strip().lower()
    if raw_kind in _REFERENCE_KIND_ALIASES:
        kind = _REFERENCE_KIND_ALIASES[raw_kind]
        violations.append(f"reference.kind_alias:{raw_kind}:{kind.value}")
    else:
        kind = _enum(payload.get("kind"), ReferenceKind, field="reference.kind", default=ReferenceKind.NONE, violations=violations)
    speaker = _enum(payload.get("speaker"), ReferenceSpeaker, field="reference.speaker", default=ReferenceSpeaker.NONE, violations=violations)
    operation = _enum(payload.get("operation"), ReferenceOperation, field="reference.operation", default=ReferenceOperation.NONE, violations=violations)
    turn_index = _bounded_int(payload.get("turn_index", 0), field="reference.turn_index", minimum=0, maximum=1_000_000, violations=violations)
    turn_offset = _bounded_int(payload.get("turn_offset", 0), field="reference.turn_offset", minimum=0, maximum=16, violations=violations)
    confidence = _bounded_float(payload.get("confidence", 0.0), field="reference.confidence", minimum=0.0, maximum=1.0, violations=violations)

    allowed_speakers, allowed_operations, _default_operation = _REFERENCE_RULES[kind]
    semantic_invalid = False
    if speaker not in allowed_speakers:
        violations.append(f"reference.speaker_incompatible:{kind.value}:{speaker.value}")
        semantic_invalid = True
    if operation not in allowed_operations:
        violations.append(f"reference.operation_incompatible:{kind.value}:{operation.value}")
        semantic_invalid = True

    if kind not in {
        ReferenceKind.RECENT_USER_TURN,
        ReferenceKind.RECENT_ASSISTANT_TURN,
        ReferenceKind.ASSISTANT_PROPOSITION,
        ReferenceKind.OPEN_QUESTION,
    }:
        if turn_index or turn_offset:
            violations.append(f"reference.locator_not_allowed:{kind.value}")
        # A locator attached to a non-session kind is syntactic debris. Dropping
        # the locator does not invent a new semantic target.
        turn_index = 0
        turn_offset = 0
    elif turn_index and turn_offset:
        violations.append("reference.locator_ambiguous:turn_index_and_turn_offset")
        semantic_invalid = True

    if semantic_invalid:
        # Fail closed at the semantic boundary. Previous code silently rewrote an
        # incompatible speaker/operation into a plausible target, which could turn
        # malformed model JSON into a confident reference to the wrong turn. A bad
        # reference now becomes *no reference*; downstream may answer the current
        # message, but cannot hallucinate a conversation identity on the model's behalf.
        return DiscourseReference()

    return DiscourseReference(
        kind=kind,
        speaker=speaker,
        operation=operation,
        turn_index=turn_index,
        turn_offset=turn_offset,
        confidence=confidence,
    )


def _parse_obligation(value: Any, violations: list[str]) -> ReplyObligation:
    payload = _mapping_or_empty(value, field="reply_obligation", violations=violations)
    return ReplyObligation(
        must_answer=_strict_bool(payload.get("must_answer"), field="reply_obligation.must_answer", default=True, violations=violations),
        must_not_broad_clarify=_strict_bool(payload.get("must_not_broad_clarify"), field="reply_obligation.must_not_broad_clarify", default=False, violations=violations),
        must_not_call_tool=_strict_bool(payload.get("must_not_call_tool"), field="reply_obligation.must_not_call_tool", default=False, violations=violations),
    )


def _parse_memory_query(value: Any, *, reference: DiscourseReference, violations: list[str]) -> MemoryQueryFrame:
    payload = _mapping_or_empty(value, field="memory_query", violations=violations)
    intent = _enum(payload.get("intent"), MemoryQueryIntent, field="memory_query.intent", default=MemoryQueryIntent.NONE, violations=violations)
    answer_mode = _enum(payload.get("answer_mode"), MemoryAnswerMode, field="memory_query.answer_mode", default=MemoryAnswerMode.EXACT, violations=violations)
    confidence = _bounded_float(payload.get("confidence", 0.0), field="memory_query.confidence", minimum=0.0, maximum=1.0, violations=violations)
    query = MemoryQueryFrame(
        intent=intent,
        subject=str(payload.get("subject", "") or "").strip(),
        predicate_hint=str(payload.get("predicate_hint", "") or "").strip(),
        object_hint=str(payload.get("object_hint", "") or "").strip(),
        temporal_scope=str(payload.get("temporal_scope", "current") or "current").strip().lower(),
        answer_mode=answer_mode,
        confidence=confidence,
    )
    if reference.requests_long_term_memory and query.intent is MemoryQueryIntent.NONE:
        violations.append("long_term_memory_requires_memory_query")
    if reference.kind is not ReferenceKind.LONG_TERM_MEMORY and query.intent is not MemoryQueryIntent.NONE:
        violations.append("memory_query_without_long_term_reference")
        return MemoryQueryFrame()
    return query


def validate_semantic_frame(value: Mapping[str, Any] | SemanticFrame | Any | None) -> SemanticFrameValidation:
    """Parse provider JSON thành ``SemanticFrame`` typed và không ném lỗi."""

    if isinstance(value, SemanticFrame):
        return SemanticFrameValidation(frame=value, violations=())

    violations: list[str] = []
    payload = _mapping_or_empty(value, field="semantic_frame", violations=violations)
    for wrapper in ("v1", "semantic_frame.v1"):
        nested = payload.get(wrapper)
        if isinstance(nested, Mapping):
            payload = dict(nested)
            violations.append(f"semantic_frame.wrapper_alias:{wrapper}")
            break
    reference = _parse_reference(payload.get("reference"), violations)
    obligation = _parse_obligation(payload.get("reply_obligation"), violations)

    ambiguity_owner = str(payload.get("ambiguity_owner", "none") or "none").strip().lower()
    if ambiguity_owner not in {"user", "assistant", "shared", "none"}:
        violations.append(f"ambiguity_owner_invalid:{ambiguity_owner}")
        ambiguity_owner = "none"

    continuation_key = str(payload.get("continuation_key", "") or "").strip()
    discourse_relation = str(payload.get("discourse_relation", "") or "").strip()
    evidence_scope = str(payload.get("evidence_scope", "") or "").strip()
    missing_evidence = _string_list(payload.get("missing_evidence"), field="missing_evidence", violations=violations)
    tool_intent = str(payload.get("tool_intent", "") or "").strip()
    target = str(payload.get("target", "") or "").strip()
    user_act = str(payload.get("user_act", "") or "").strip()
    boundary_action = str(payload.get("boundary_action", "") or "").strip()

    if ambiguity_owner == "assistant" and not obligation.must_not_broad_clarify:
        violations.append("assistant_owned_ambiguity_requires_no_broad_clarification")
        obligation = ReplyObligation(obligation.must_answer, True, obligation.must_not_call_tool)
    if discourse_relation.startswith("continuation_") and not continuation_key:
        violations.append("continuation_requires_key")
        # Missing identity is not a formatting defect. Do not invent
        # ``recent_assistant_thread`` and accidentally revive an old thread.
        # Neutralizing the relation preserves current-message answering while
        # denying the malformed frame authority to continue anything.
        discourse_relation = ""
    if boundary_action and boundary_action != "none" and not obligation.must_answer:
        obligation = ReplyObligation(True, obligation.must_not_broad_clarify, obligation.must_not_call_tool)
    if missing_evidence and not evidence_scope:
        violations.append("missing_evidence_requires_scope")
        # Keep the absence explicit. Grounding policy, not the parser, owns the
        # choice of evidence scope.
    if target == "assistant" and user_act == "relational_check_in":
        if tool_intent not in {"", "none"}:
            violations.append("assistant_check_in_must_not_call_tool")
        tool_intent = "none"
        obligation = ReplyObligation(True, True, True)
    if reference.kind is not ReferenceKind.NONE:
        must_not_broad = obligation.must_not_broad_clarify
        must_not_tool = obligation.must_not_call_tool
        if reference.is_session_reference:
            must_not_broad = True
            must_not_tool = True
            tool_intent = "none"
        obligation = ReplyObligation(True, must_not_broad, must_not_tool)

    memory_query = _parse_memory_query(payload.get("memory_query"), reference=reference, violations=violations)
    frame = SemanticFrame(
        version="semantic_frame.v1",
        turn_kind=str(payload.get("turn_kind", "") or "").strip(),
        discourse_relation=discourse_relation,
        user_act=user_act,
        target=target,
        literal_question=_strict_bool(payload.get("literal_question"), field="literal_question", default=False, violations=violations),
        ambiguity_owner=ambiguity_owner,
        boundary_action=boundary_action,
        continuation_key=continuation_key,
        evidence_scope=evidence_scope,
        tool_intent=tool_intent,
        missing_evidence=missing_evidence,
        reference=reference,
        reply_obligation=obligation,
        memory_query=memory_query,
    )
    return SemanticFrameValidation(frame=frame, violations=tuple(dict.fromkeys(violations)))


def validate_assistant_act(value: Mapping[str, Any] | AssistantAct | Any | None) -> AssistantActValidation:
    """Parse act của assistant và kiểm tra proposition mode theo loại act."""

    if isinstance(value, AssistantAct):
        return AssistantActValidation(act=value, violations=())
    violations: list[str] = []
    payload = _mapping_or_empty(value, field="assistant_act", violations=violations)
    raw_kind = str(payload.get("kind", "") or "").strip().lower().replace("-", "_").replace(" ", "_")
    kind = _ASSISTANT_ACT_KIND_ALIASES.get(raw_kind)
    if kind is not None:
        violations.append(f"assistant_act.kind_alias:{raw_kind}:{kind.value}")
    else:
        kind = _enum(payload.get("kind"), AssistantActKind, field="assistant_act.kind", default=AssistantActKind.OTHER, violations=violations)
    mode = _enum(payload.get("proposition_mode"), PropositionMode, field="assistant_act.proposition_mode", default=PropositionMode.NONE, violations=violations)
    confidence = _bounded_float(payload.get("confidence", 0.0), field="assistant_act.confidence", minimum=0.0, maximum=1.0, violations=violations)

    if kind not in _PROPOSITIONAL_ASSISTANT_ACTS and mode is not PropositionMode.NONE:
        # Shape repair is safe here: a non-propositional act cannot own a
        # proposition trajectory, so the extra mode is simply discarded.
        violations.append(f"assistant_act.non_propositional_mode:{kind.value}:{mode.value}")
        mode = PropositionMode.NONE
    elif kind in _PROPOSITIONAL_ASSISTANT_ACTS and mode is PropositionMode.NONE:
        # V29 semantic-authority invariant: never invent NEW/CONTINUE on behalf
        # of a stochastic worker.  That choice changes discourse identity and
        # therefore belongs to the model output itself.  Degrade to UNKNOWN
        # semantics instead of silently creating or reviving a proposition root.
        violations.append(f"assistant_act.proposition_mode_required:{kind.value}")
        kind = AssistantActKind.OTHER
        mode = PropositionMode.NONE
        confidence = min(confidence, 0.25)

    return AssistantActValidation(
        act=AssistantAct(kind=kind, proposition_mode=mode, confidence=confidence),
        violations=tuple(dict.fromkeys(violations)),
    )


__all__ = [
    "AssistantAct",
    "AssistantActKind",
    "AssistantActValidation",
    "DiscourseReference",
    "MemoryAnswerMode",
    "MemoryQueryFrame",
    "MemoryQueryIntent",
    "PropositionMode",
    "ReferenceKind",
    "ReferenceOperation",
    "ReferenceSpeaker",
    "ReplyObligation",
    "SemanticFrame",
    "SemanticFrameValidation",
    "validate_assistant_act",
    "validate_semantic_frame",
]
