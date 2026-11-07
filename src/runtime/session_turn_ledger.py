from __future__ import annotations

from dataclasses import dataclass, field, replace
from time import time
from typing import Any, Iterable, Literal, Optional

try:
    from ..memory.text_normalize_vi import compact_spaces, looks_like_question, visible_len
except ImportError:
    from memory.text_normalize_vi import compact_spaces, looks_like_question, visible_len


Role = Literal["user", "assistant"]


@dataclass(frozen=True)
class SessionTurn:
    """
    Một lượt public trong phiên.

    Đây là raw ledger dùng để đếm/lấy exact trong phiên hiện tại.
    Không được coi nó là long-term memory. Không ai muốn ký ức nhân cách
    của ERIS biến thành biên bản cuộc họp phường.
    """

    index: int
    role: Role
    text: str
    timestamp: float
    is_question: bool = False
    meta: dict[str, Any] = field(default_factory=dict)


class SessionTurnLedger:
    """Ledger deterministic cho câu hỏi về thứ tự, số lượng, câu dài nhất."""

    def __init__(self) -> None:
        self._turns: list[SessionTurn] = []
        self._next_index = 1
        # Monotonic revision of the *public turn stream*. Metadata enrichment does
        # not change it. A cognition cycle can therefore pin this cheap token and
        # discard a result if another public turn arrived while a model was busy.
        self._revision = 0

    @property
    def revision(self) -> int:
        """Current public-conversation generation."""

        return self._revision

    def record_user(self, text: str, *, timestamp: Optional[float] = None, meta: Optional[dict[str, Any]] = None) -> SessionTurn:
        return self._record("user", text, timestamp=timestamp, meta=meta)

    def record_assistant(self, text: str, *, timestamp: Optional[float] = None, meta: Optional[dict[str, Any]] = None) -> SessionTurn:
        return self._record("assistant", text, timestamp=timestamp, meta=meta)

    def update_user_batch(self, index: int, text: str, *, metadata: dict[str, Any]) -> SessionTurn | None:
        """Extend the current, unanswered user turn with its original messages."""
        for position, turn in enumerate(self._turns):
            if turn.index != index or turn.role != "user":
                continue
            if any(t.role == "assistant" for t in self._turns[position + 1:]):
                return None
            cleaned = compact_spaces(text)
            updated = replace(turn, text=cleaned, is_question=looks_like_question(cleaned),
                              meta={**turn.meta, "user_input_batch": dict(metadata)})
            self._turns[position] = updated
            if cleaned != turn.text:
                self._revision += 1
            return updated
        return None

    def _record(self, role: Role, text: str, *, timestamp: Optional[float], meta: Optional[dict[str, Any]]) -> SessionTurn:
        cleaned = compact_spaces(text)
        turn = SessionTurn(
            index=self._next_index,
            role=role,
            text=cleaned,
            timestamp=timestamp if timestamp is not None else time(),
            is_question=looks_like_question(cleaned) if role == "user" else False,
            meta=dict(meta or {}),
        )
        self._next_index += 1
        self._turns.append(turn)
        self._revision += 1
        return turn

    def all_turns(self) -> list[SessionTurn]:
        return list(self._turns)

    def get_turn(self, index: int) -> Optional[SessionTurn]:
        """Lấy một lượt theo chỉ số ổn định trong phiên."""

        for turn in self._turns:
            if turn.index == int(index):
                return turn
        return None

    def recent_turns(self, *, before_index: int | None = None, limit: int = 12) -> list[SessionTurn]:
        """Lấy cửa sổ hội thoại trước một lượt, giữ nguyên thứ tự thời gian."""

        items = self._turns
        if before_index is not None:
            items = [turn for turn in items if turn.index < int(before_index)]
        return list(items[-max(1, int(limit)):])

    def merge_turn_meta(self, index: int, values: dict[str, Any]) -> Optional[SessionTurn]:
        """Gộp metadata sau khi semantic frame được tạo.

        ``SessionTurn`` bất biến để code đọc không sửa ngầm. Khi cần bổ sung frame
        hoặc liên kết discourse, ledger thay nguyên record bằng ``dataclasses.replace``.
        """

        for position, turn in enumerate(self._turns):
            if turn.index != int(index):
                continue
            merged = dict(turn.meta)
            for key, value in dict(values or {}).items():
                if key == "discourse" and isinstance(value, dict):
                    current = dict(merged.get("discourse", {}) or {})
                    current.update(value)
                    merged[key] = current
                else:
                    merged[key] = value
            updated = replace(turn, meta=merged)
            self._turns[position] = updated
            return updated
        return None

    def turns_by_role(self, role: Role) -> list[SessionTurn]:
        return [turn for turn in self._turns if turn.role == role]

    def user_turns(self) -> list[SessionTurn]:
        return self.turns_by_role("user")

    def assistant_turns(self) -> list[SessionTurn]:
        return self.turns_by_role("assistant")

    def user_questions(self) -> list[SessionTurn]:
        return [turn for turn in self.user_turns() if turn.is_question]

    def nth_user_turn(self, ordinal: int) -> Optional[SessionTurn]:
        return self._nth(self.user_turns(), ordinal)

    def nth_assistant_turn(self, ordinal: int) -> Optional[SessionTurn]:
        return self._nth(self.assistant_turns(), ordinal)

    def nth_user_question(self, ordinal: int) -> Optional[SessionTurn]:
        return self._nth(self.user_questions(), ordinal)

    def count_user_questions(self, *, include_current: bool = True) -> int:
        questions = self.user_questions()
        if include_current:
            return len(questions)
        return max(0, len(questions) - 1)

    def longest_user_question(self) -> Optional[SessionTurn]:
        questions = self.user_questions()
        if not questions:
            return None
        return max(questions, key=lambda turn: visible_len(turn.text))

    def gist_recent_user_topics(self, *, limit: int = 8) -> list[str]:
        """
        Sinh gist rất nhẹ từ user turns gần đây.

        Đây không thay MeaningLayer. Nó chỉ dùng khi user hỏi “nhớ gì không”
        và không yêu cầu exact.
        """
        topics: list[str] = []
        for turn in self.user_turns()[-limit:]:
            text = turn.text
            lower = text.lower()
            if "tên" in lower or "ten" in lower:
                topics.append("tên")
            elif "tháng" in lower or "thang" in lower:
                topics.append("tháng sinh/tháng 8")
            elif "câu" in lower or "cau" in lower or "hỏi" in lower or "hoi" in lower:
                topics.append("kiểm tra trí nhớ/thứ tự câu")
            elif "màn hình" in lower or "man hinh" in lower or "icon" in lower:
                topics.append("màn hình/icon")
            elif "thế giới" in lower or "the gioi" in lower or "quan sát" in lower or "quan sat" in lower:
                topics.append("cách ERIS hiểu/quan sát thế giới")
            else:
                topics.append("một đoạn đối thoại gần đây")

        return self._unique_keep_order(topics)

    @staticmethod
    def _nth(items: Iterable[SessionTurn], ordinal: int) -> Optional[SessionTurn]:
        if ordinal <= 0:
            return None
        seq = list(items)
        if ordinal > len(seq):
            return None
        return seq[ordinal - 1]

    @staticmethod
    def _unique_keep_order(items: Iterable[str]) -> list[str]:
        seen: set[str] = set()
        out: list[str] = []
        for item in items:
            if item in seen:
                continue
            seen.add(item)
            out.append(item)
        return out
