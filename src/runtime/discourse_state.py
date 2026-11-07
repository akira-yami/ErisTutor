from __future__ import annotations

"""State và resolver discourse typed, không chứa câu chữ public hay ``Decision``.

Semantic authority mô tả act/reference. Module này chỉ giải target trên ledger và
trả một ``DiscourseAction`` thuần dữ liệu. Renderer và orchestrator chịu trách
nhiệm riêng, nhờ vậy thêm kiểu act không kéo theo việc sửa cả tầng state.
"""

from dataclasses import dataclass
from enum import Enum
import json
from typing import Any

from runtime.semantic_frame import (
    AssistantAct,
    AssistantActKind,
    DiscourseReference,
    MemoryQueryFrame,
    PropositionMode,
    ReferenceKind,
    ReferenceOperation,
    ReferenceSpeaker,
    SemanticFrame,
    validate_assistant_act,
    validate_semantic_frame,
)
from runtime.session_turn_ledger import SessionTurn, SessionTurnLedger


class DiscourseActionKind(str, Enum):
    NONE = "none"
    REPEAT_TURN = "repeat_turn"
    CLARIFY_REFERENCE = "clarify_reference"
    CONFIRM_REFERENCE = "confirm_reference"
    REPAIR_REFERENCE = "repair_reference"
    ANSWER_OPEN_QUESTION = "answer_open_question"
    RECALL_LONG_TERM_MEMORY = "recall_long_term_memory"


@dataclass(frozen=True, slots=True)
class DiscourseAction:
    """Ý định runtime đã resolve, chưa có text và chưa có animation."""

    kind: DiscourseActionKind = DiscourseActionKind.NONE
    reference: DiscourseReference = DiscourseReference()
    target_turn: SessionTurn | None = None
    proposition_root_turn: SessionTurn | None = None
    memory_query: MemoryQueryFrame = MemoryQueryFrame()
    confidence: float = 0.0

    @property
    def deterministic(self) -> bool:
        return self.kind in {
            DiscourseActionKind.REPEAT_TURN,
            DiscourseActionKind.RECALL_LONG_TERM_MEMORY,
        }


@dataclass(frozen=True, slots=True)
class DiscourseResolution:
    frame: SemanticFrame
    action: DiscourseAction

    @property
    def reference(self) -> DiscourseReference:
        return self.frame.reference

    @property
    def target_turn(self) -> SessionTurn | None:
        return self.action.target_turn

    @property
    def proposition_root_turn(self) -> SessionTurn | None:
        return self.action.proposition_root_turn

    @property
    def memory_recall_requested(self) -> bool:
        return self.action.kind is DiscourseActionKind.RECALL_LONG_TERM_MEMORY

    @property
    def memory_query(self) -> MemoryQueryFrame:
        return self.action.memory_query

    def assistant_meta(self, *, user_turn_index: int) -> dict[str, Any]:
        target_index = int(getattr(self.target_turn, "index", 0) or 0)
        root_index = int(getattr(self.proposition_root_turn, "index", 0) or 0)
        return {
            "discourse": {
                "reply_to_user_turn_index": int(user_turn_index),
                "reference_kind": self.reference.kind.value,
                "reference_operation": self.reference.operation.value,
                "reference_target_turn_index": target_index,
                # Root này là proposition đang được lượt user nhắc tới. Commit sẽ
                # quyết định câu assistant mới có tiếp tục root ấy hay không.
                "active_proposition_root_turn_index": root_index,
                "reference_confidence": self.reference.confidence,
            }
        }


class DiscourseStateBuilder:
    """Tạo map index nhỏ, không lặp raw text đã có trong chat history."""

    def __init__(self, ledger: SessionTurnLedger) -> None:
        self.ledger = ledger

    def build(self, *, current_turn_index: int, limit: int = 16) -> dict[str, Any]:
        turns = self.ledger.recent_turns(before_index=current_turn_index, limit=limit)
        turn_map: list[dict[str, Any]] = []
        for history_position, turn in enumerate(turns):
            meta = dict(turn.meta or {})
            discourse = dict(meta.get("discourse", {}) or {})
            assistant_act = validate_assistant_act(meta.get("assistant_act", {})).act
            turn_map.append(
                {
                    "history_position": history_position,
                    "turn_index": turn.index,
                    "role": turn.role,
                    "assistant_act": (
                        assistant_act.kind.value if turn.role == "assistant" else "none"
                    ),
                    "proposition_root_turn_index": int(
                        discourse.get("proposition_root_turn_index", 0) or 0
                    ),
                    "reply_to_user_turn_index": int(
                        discourse.get("reply_to_user_turn_index", 0) or 0
                    ),
                }
            )

        previous_user = next((turn for turn in reversed(turns) if turn.role == "user"), None)
        previous_assistant = next((turn for turn in reversed(turns) if turn.role == "assistant"), None)
        active_root = self._active_proposition(turns)
        open_question = self._open_question(turns)
        return {
            "version": "discourse_state.v2",
            "current_user_turn_index": int(current_turn_index),
            # Mỗi entry map đúng một message trong chat_history theo cùng thứ tự.
            # Không gửi text lần hai, vừa đỡ token vừa tránh hai bản lịch sử lệch.
            "history_turn_map": turn_map,
            "candidate_references": {
                "previous_user_turn_index": int(getattr(previous_user, "index", 0) or 0),
                "previous_assistant_turn_index": int(getattr(previous_assistant, "index", 0) or 0),
                "active_assistant_proposition_turn_index": int(getattr(active_root, "index", 0) or 0),
                "open_question_turn_index": int(getattr(open_question, "index", 0) or 0),
            },
        }

    def prompt_context(self, *, current_turn_index: int, limit: int = 16) -> str:
        return json.dumps(
            self.build(current_turn_index=current_turn_index, limit=limit),
            ensure_ascii=False,
            separators=(",", ":"),
        )

    def _active_proposition(self, turns: list[SessionTurn]) -> SessionTurn | None:
        for turn in reversed(turns):
            if turn.role != "assistant":
                continue
            discourse = dict(turn.meta.get("discourse", {}) or {})
            root_index = int(
                discourse.get("active_proposition_root_turn_index", 0)
                or discourse.get("proposition_root_turn_index", 0)
                or 0
            )
            if root_index:
                root = self.ledger.get_turn(root_index)
                if root is not None and root.role == "assistant":
                    return root
            act = validate_assistant_act(turn.meta.get("assistant_act", {})).act
            if act.is_propositional:
                return turn
        return None

    def _open_question(self, turns: list[SessionTurn]) -> SessionTurn | None:
        answered_user_turns = {
            int(dict(turn.meta.get("discourse", {}) or {}).get("reply_to_user_turn_index", 0) or 0)
            for turn in turns
            if turn.role == "assistant"
        }
        latest_user_index = max(
            (turn.index for turn in turns if turn.role == "user"),
            default=0,
        )
        for turn in reversed(turns):
            if turn.role == "user" and turn.is_question and turn.index not in answered_user_turns:
                return turn
            if turn.role == "assistant":
                act = validate_assistant_act(turn.meta.get("assistant_act", {})).act
                # Một assistant question chỉ còn open nếu chưa có user turn nào sau nó.
                if act.opens_question and latest_user_index < turn.index:
                    return turn
        return None


class DiscourseResolver:
    """Giải object typed thành action; không sinh surface tiếng Việt."""

    def __init__(self, ledger: SessionTurnLedger) -> None:
        self.ledger = ledger

    def resolve(
        self,
        frame: SemanticFrame | dict[str, Any] | None,
        *,
        current_user_turn_index: int,
    ) -> DiscourseResolution:
        typed = validate_semantic_frame(frame).frame
        reference = typed.reference
        target = self._resolve_target(reference, current_user_turn_index=current_user_turn_index)
        root = self._resolve_proposition_root(target)
        action = self._build_action(typed, target=target, root=root)
        return DiscourseResolution(frame=typed, action=action)

    def _resolve_target(
        self,
        reference: DiscourseReference,
        *,
        current_user_turn_index: int,
    ) -> SessionTurn | None:
        if not reference.is_session_reference or reference.confidence < 0.55:
            return None

        if reference.turn_index:
            turn = self.ledger.get_turn(reference.turn_index)
            if turn is None or turn.index >= current_user_turn_index:
                return None
            return turn if self._matches_kind(turn, reference) else None

        candidates = [
            turn
            for turn in self.ledger.recent_turns(before_index=current_user_turn_index, limit=32)
            if self._matches_kind(turn, reference)
        ]
        if not candidates:
            return None
        offset = min(reference.turn_offset, len(candidates) - 1)
        return candidates[-1 - offset]

    def _matches_kind(self, turn: SessionTurn, reference: DiscourseReference) -> bool:
        kind = reference.kind
        if kind is ReferenceKind.RECENT_USER_TURN:
            return turn.role == "user"
        if kind is ReferenceKind.RECENT_ASSISTANT_TURN:
            return turn.role == "assistant"
        if kind is ReferenceKind.ASSISTANT_PROPOSITION:
            if turn.role != "assistant":
                return False
            act = validate_assistant_act(turn.meta.get("assistant_act", {})).act
            discourse = dict(turn.meta.get("discourse", {}) or {})
            return act.is_propositional or bool(
                int(discourse.get("active_proposition_root_turn_index", 0) or 0)
            )
        if kind is ReferenceKind.OPEN_QUESTION:
            if reference.speaker is ReferenceSpeaker.USER:
                return turn.role == "user" and turn.is_question
            if reference.speaker is ReferenceSpeaker.ASSISTANT:
                act = validate_assistant_act(turn.meta.get("assistant_act", {})).act
                return turn.role == "assistant" and act.opens_question
        return False

    def _resolve_proposition_root(self, target: SessionTurn | None) -> SessionTurn | None:
        if target is None or target.role != "assistant":
            return target
        discourse = dict(target.meta.get("discourse", {}) or {})
        root_index = int(
            discourse.get("proposition_root_turn_index", 0)
            or discourse.get("active_proposition_root_turn_index", 0)
            or 0
        )
        if root_index:
            root = self.ledger.get_turn(root_index)
            if root is not None and root.role == "assistant":
                return root
        act = validate_assistant_act(target.meta.get("assistant_act", {})).act
        return target if act.is_propositional else None

    @staticmethod
    def _build_action(
        frame: SemanticFrame,
        *,
        target: SessionTurn | None,
        root: SessionTurn | None,
    ) -> DiscourseAction:
        reference = frame.reference
        action_kind = DiscourseActionKind.NONE
        if reference.requests_long_term_memory and frame.memory_query.usable:
            action_kind = DiscourseActionKind.RECALL_LONG_TERM_MEMORY
        elif target is not None:
            if reference.operation in {ReferenceOperation.REPEAT, ReferenceOperation.RECALL}:
                action_kind = DiscourseActionKind.REPEAT_TURN
            elif reference.operation is ReferenceOperation.CLARIFY:
                action_kind = DiscourseActionKind.CLARIFY_REFERENCE
            elif reference.operation is ReferenceOperation.CONFIRM:
                action_kind = DiscourseActionKind.CONFIRM_REFERENCE
            elif reference.operation is ReferenceOperation.REPAIR:
                action_kind = DiscourseActionKind.REPAIR_REFERENCE
            elif reference.kind is ReferenceKind.OPEN_QUESTION and reference.operation is ReferenceOperation.ANSWER:
                action_kind = DiscourseActionKind.ANSWER_OPEN_QUESTION
        return DiscourseAction(
            kind=action_kind,
            reference=reference,
            target_turn=target,
            proposition_root_turn=root,
            memory_query=frame.memory_query,
            confidence=reference.confidence,
        )


def assistant_proposition_link(
    *,
    assistant_act: AssistantAct,
    assistant_turn_index: int,
    referenced_root_turn_index: int,
    previous_active_root_turn_index: int,
) -> tuple[int, int]:
    """Tính root của câu mới mà không nhìn surface text.

    Trả ``(proposition_root, active_root)``. Acknowledgement/social act không tự
    biến thành proposition; chúng chỉ mang tiếp active root để lượt sau còn lần
    được mạch hội thoại.
    """

    if assistant_act.is_propositional:
        if (
            assistant_act.proposition_mode is PropositionMode.CONTINUE
            and referenced_root_turn_index > 0
        ):
            root = referenced_root_turn_index
        else:
            root = assistant_turn_index
        return root, root
    active = referenced_root_turn_index or previous_active_root_turn_index
    return 0, active


__all__ = [
    "DiscourseAction",
    "DiscourseActionKind",
    "DiscourseResolution",
    "DiscourseResolver",
    "DiscourseStateBuilder",
    "assistant_proposition_link",
]
