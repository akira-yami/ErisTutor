from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass
from typing import Iterable

from domain import Decision


PUBLIC_TURN_CHANNELS = {
    "chat",
    "session_ritual",
    "presence",
    "reengagement",
    "autonomous",
}


@dataclass(slots=True)
class PublicTurn:
    turn_id: str
    channel: str  # chat, session_ritual, presence, reengagement, autonomous
    text: str
    timestamp: float
    parse_mode: str
    visible_to_user: bool = True
    can_be_replied_to: bool = True


class PublicTurnLedger:
    def __init__(self) -> None:
        self._turns: list[PublicTurn] = []
        self._turns_by_id: dict[str, PublicTurn] = {}
        self._lock = threading.RLock()

    def append(
        self,
        *,
        channel: str,
        text: str,
        parse_mode: str,
        visible_to_user: bool = True,
        can_be_replied_to: bool = True,
        timestamp: float | None = None,
        turn_id: str | None = None,
    ) -> PublicTurn:
        normalized_channel = normalize_public_turn_channel(channel)
        resolved_turn_id = str(turn_id or uuid.uuid4().hex)
        with self._lock:
            existing = self._turns_by_id.get(resolved_turn_id)
            if existing is not None:
                return existing
            turn = PublicTurn(
                turn_id=resolved_turn_id,
                channel=normalized_channel,
                text=str(text or ""),
                timestamp=time.time() if timestamp is None else float(timestamp),
                parse_mode=str(parse_mode or ""),
                visible_to_user=bool(visible_to_user),
                can_be_replied_to=bool(can_be_replied_to),
            )
            self._turns.append(turn)
            self._turns_by_id[resolved_turn_id] = turn
        return turn

    def record_decision(
        self,
        decision: Decision,
        *,
        channel: str,
        visible_to_user: bool = True,
        can_be_replied_to: bool = True,
        timestamp: float | None = None,
    ) -> PublicTurn | None:
        text, should_speak = decision._payload_text()
        if not should_speak or not str(text or "").strip():
            return None
        return self.append(
            channel=channel,
            text=text,
            parse_mode=decision.parse_mode,
            visible_to_user=visible_to_user,
            can_be_replied_to=can_be_replied_to,
            timestamp=timestamp,
        )

    def all(self) -> tuple[PublicTurn, ...]:
        with self._lock:
            return tuple(self._turns)

    def recent(self, limit: int = 20) -> tuple[PublicTurn, ...]:
        if limit <= 0:
            return ()
        with self._lock:
            return tuple(self._turns[-limit:])

    def latest_visible_before(self, timestamp: float) -> PublicTurn | None:
        boundary = float(timestamp)
        with self._lock:
            for turn in reversed(self._turns):
                if turn.visible_to_user and turn.timestamp < boundary:
                    return turn
        return None

    def extend(self, turns: Iterable[PublicTurn]) -> None:
        with self._lock:
            for turn in turns:
                if turn.turn_id in self._turns_by_id:
                    continue
                self._turns.append(turn)
                self._turns_by_id[turn.turn_id] = turn


def normalize_public_turn_channel(channel: str) -> str:
    value = str(channel or "").strip().lower()
    if value == "re_engagement":
        value = "reengagement"
    if value == "force_speak":
        value = "autonomous"
    return value if value in PUBLIC_TURN_CHANNELS else "autonomous"


def infer_public_turn_channel(decision: Decision, *, fallback: str = "autonomous") -> str:
    parse_mode = str(getattr(decision, "parse_mode", "") or "").strip().lower()
    intent = str(getattr(decision, "intent", "") or "").strip().lower()
    tags = {str(tag or "").strip().lower() for tag in tuple(getattr(decision, "reasoning_tags", ()) or ())}

    if parse_mode == "session_ritual" or intent == "session_ritual" or "session_ritual" in tags:
        return "session_ritual"
    if parse_mode in {"re_engagement", "reengagement"} or intent in {"re_engagement", "reengagement"}:
        return "reengagement"
    if (
        parse_mode in {"presence_maintenance", "micro_presence"}
        or intent in {"presence_maintenance", "micro_presence"}
        or "presence_maintenance" in tags
        or "micro_presence" in tags
    ):
        return "presence"
    return normalize_public_turn_channel(fallback)
