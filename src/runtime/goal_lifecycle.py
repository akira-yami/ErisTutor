# -*- coding: utf-8 -*-
"""V94 - lifecycle typed cho shared/user goal.

Mục tiêu của module này là chấm dứt kiểu ``open_loops: tuple[str]`` sống mãi chỉ
vì một observer từng ghi ra một câu. Goal public phải có owner, trạng thái và
bằng chứng xác nhận gần nhất. ``open_loops`` cũ vẫn được giữ như compatibility
projection, nhưng chỉ mirror các goal đang ACTIVE.

Không dùng LLM ở đây. Đây là state machine cơ học để tầng initiative không biến
một món nợ cũ thành ``resume_shared_goal`` vô hạn.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from enum import StrEnum
import hashlib
import math
from typing import Any, Mapping, Sequence


def _compact(value: Any, limit: int = 320) -> str:
    return " ".join(str(value or "").split())[: max(16, int(limit))]


def _owner(value: Any) -> str:
    text = str(value or "unknown").strip().lower()
    return text if text in {"user", "eris", "shared", "environment", "external_entity"} else "unknown"


def _goal_key(text: Any, owner: Any) -> str:
    # Identity semantic của goal không phụ thuộc owner. Owner có thể chuyển từ
    # ``user`` sang ``shared`` khi ERIS nhận trách nhiệm, nhưng vẫn là cùng một
    # mục tiêu; hash owner vào key sẽ tạo hai zombie record cho cùng nội dung.
    normalized = _compact(text, 520).casefold()
    raw = normalized.encode("utf-8", errors="ignore")
    return hashlib.sha1(raw).hexdigest()[:16]


class GoalLifecycleState(StrEnum):
    ACTIVE = "active"
    SUSPENDED = "suspended"
    RESOLVED = "resolved"
    ABANDONED = "abandoned"
    EXPIRED = "expired"


_TERMINAL = {
    GoalLifecycleState.RESOLVED,
    GoalLifecycleState.ABANDONED,
    GoalLifecycleState.EXPIRED,
}


@dataclass(frozen=True, slots=True)
class GoalLifecycleRecord:
    goal_id: str
    semantic_key: str
    text: str
    owner: str
    state: GoalLifecycleState = GoalLifecycleState.ACTIVE
    created_at: float = 0.0
    updated_at: float = 0.0
    last_confirmed_at: float = 0.0
    last_confirmed_turn_seq: int = 0
    unconfirmed_user_turns: int = 0
    expires_at: float = 0.0
    reason: str = ""
    evidence_refs: tuple[str, ...] = ()

    @classmethod
    def create(
        cls,
        text: Any,
        *,
        owner: Any,
        now: float,
        turn_seq: int,
        evidence_refs: Sequence[Any] = (),
        ttl_seconds: float = 900.0,
    ) -> "GoalLifecycleRecord":
        cleaned = _compact(text, 520)
        owner_tag = _owner(owner)
        key = _goal_key(cleaned, owner_tag)
        return cls(
            goal_id=f"goal:{key}",
            semantic_key=key,
            text=cleaned,
            owner=owner_tag,
            state=GoalLifecycleState.ACTIVE,
            created_at=float(now),
            updated_at=float(now),
            last_confirmed_at=float(now),
            last_confirmed_turn_seq=max(0, int(turn_seq or 0)),
            unconfirmed_user_turns=0,
            expires_at=float(now) + max(60.0, float(ttl_seconds)),
            reason="confirmed_open_loop",
            evidence_refs=tuple(dict.fromkeys(_compact(item, 160) for item in evidence_refs if _compact(item, 160)))[:12],
        )

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any] | None) -> "GoalLifecycleRecord":
        data = payload or {}
        text = _compact(data.get("text", ""), 520)
        owner_tag = _owner(data.get("owner", "unknown"))
        key = str(data.get("semantic_key") or _goal_key(text, owner_tag))
        state_raw = str(data.get("state") or GoalLifecycleState.ACTIVE.value)
        try:
            state = GoalLifecycleState(state_raw)
        except ValueError:
            state = GoalLifecycleState.SUSPENDED
        refs = data.get("evidence_refs", ())
        refs = refs if isinstance(refs, Sequence) and not isinstance(refs, str) else ()
        return cls(
            goal_id=str(data.get("goal_id") or f"goal:{key}"),
            semantic_key=key,
            text=text,
            owner=owner_tag,
            state=state,
            created_at=max(0.0, float(data.get("created_at", 0.0) or 0.0)),
            updated_at=max(0.0, float(data.get("updated_at", 0.0) or 0.0)),
            last_confirmed_at=max(0.0, float(data.get("last_confirmed_at", 0.0) or 0.0)),
            last_confirmed_turn_seq=max(0, int(data.get("last_confirmed_turn_seq", 0) or 0)),
            unconfirmed_user_turns=max(0, int(data.get("unconfirmed_user_turns", 0) or 0)),
            expires_at=max(0.0, float(data.get("expires_at", 0.0) or 0.0)),
            reason=_compact(data.get("reason", ""), 180),
            evidence_refs=tuple(dict.fromkeys(_compact(item, 160) for item in refs if _compact(item, 160)))[:12],
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": "eris.goal_lifecycle.v94",
            "goal_id": self.goal_id,
            "semantic_key": self.semantic_key,
            "text": self.text,
            "owner": self.owner,
            "state": self.state.value,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "last_confirmed_at": self.last_confirmed_at,
            "last_confirmed_turn_seq": self.last_confirmed_turn_seq,
            "unconfirmed_user_turns": self.unconfirmed_user_turns,
            "expires_at": self.expires_at,
            "reason": self.reason,
            "evidence_refs": list(self.evidence_refs),
        }


def reconcile_goal_records(
    records: Sequence[GoalLifecycleRecord],
    *,
    open_loop: Any,
    owner: Any,
    event_kind: Any,
    relationship_meaning: Any,
    confidence: Any,
    turn_seq: int,
    evidence_refs: Sequence[Any],
    now: float,
    ttl_seconds: float = 900.0,
    suspend_after_user_turns: int = 2,
    expire_after_user_turns: int = 4,
) -> tuple[GoalLifecycleRecord, ...]:
    """Reduce một observation thành lifecycle goal mới.

    Luật V94:
    - chỉ ``user/shared`` được tạo goal public;
    - 2 user turn liên tiếp không còn evidence -> SUSPENDED;
    - 4 turn -> EXPIRED;
    - ``relationship_meaning=closure`` -> RESOLVED;
    - cùng goal được evidence lại -> ACTIVE và reset decay;
    - goal mới sẽ suspend goal ACTIVE cũ để không có hai món nợ foreground cạnh tranh.
    """
    current = float(now)
    event = str(event_kind or "").strip().lower()
    meaning = str(relationship_meaning or "").strip().lower()
    owner_tag = _owner(owner)
    loop = _compact(open_loop, 520)
    try:
        conf = float(confidence or 0.0)
    except (TypeError, ValueError):
        conf = 0.0
    if not math.isfinite(conf):
        conf = 0.0

    items: list[GoalLifecycleRecord] = []
    for item in records:
        record = item if isinstance(item, GoalLifecycleRecord) else GoalLifecycleRecord.from_mapping(item)  # type: ignore[arg-type]
        if record.state not in _TERMINAL and record.expires_at > 0.0 and current >= record.expires_at:
            record = replace(record, state=GoalLifecycleState.EXPIRED, updated_at=current, reason="ttl_expired")
        items.append(record)

    is_user_turn = event == "chat"
    valid_confirmation = bool(loop and owner_tag in {"user", "shared"} and conf >= 0.65)
    confirmed_key = _goal_key(loop, owner_tag) if valid_confirmation else ""

    # Closure là semantic evidence mạnh hơn decay heuristic.
    if is_user_turn and meaning in {"closure", "topic_closed", "resolved"}:
        items = [
            replace(item, state=GoalLifecycleState.RESOLVED, updated_at=current, reason="user_closure")
            if item.state in {GoalLifecycleState.ACTIVE, GoalLifecycleState.SUSPENDED} and item.owner in {"user", "shared"}
            else item
            for item in items
        ]
    elif is_user_turn:
        decayed: list[GoalLifecycleRecord] = []
        for item in items:
            if item.state not in {GoalLifecycleState.ACTIVE, GoalLifecycleState.SUSPENDED}:
                decayed.append(item)
                continue
            if confirmed_key and item.semantic_key == confirmed_key:
                decayed.append(item)
                continue
            count = item.unconfirmed_user_turns + 1
            state = item.state
            reason = "unconfirmed_user_turn"
            if count >= max(2, int(expire_after_user_turns)):
                state = GoalLifecycleState.EXPIRED
                reason = "user_moved_on"
            elif count >= max(1, int(suspend_after_user_turns)):
                state = GoalLifecycleState.SUSPENDED
                reason = "not_reconfirmed"
            decayed.append(replace(
                item,
                state=state,
                unconfirmed_user_turns=count,
                updated_at=current,
                reason=reason,
            ))
        items = decayed

    if valid_confirmation:
        refs = tuple(dict.fromkeys(_compact(item, 160) for item in evidence_refs if _compact(item, 160)))[:12]
        match_index = next((index for index, item in enumerate(items) if item.semantic_key == confirmed_key), None)
        # Goal mới cạnh tranh với goal ACTIVE cũ -> suspend goal cũ thay vì để
        # initiative hiểu rằng user đang nợ hai thread song song.
        for index, item in enumerate(items):
            if index == match_index:
                continue
            if item.state == GoalLifecycleState.ACTIVE and item.owner in {"user", "shared"}:
                items[index] = replace(item, state=GoalLifecycleState.SUSPENDED, updated_at=current, reason="superseded_by_new_goal")
        if match_index is None:
            items.append(GoalLifecycleRecord.create(
                loop,
                owner=owner_tag,
                now=current,
                turn_seq=turn_seq,
                evidence_refs=refs,
                ttl_seconds=ttl_seconds,
            ))
        else:
            existing = items[match_index]
            items[match_index] = replace(
                existing,
                text=loop,
                owner=owner_tag,
                state=GoalLifecycleState.ACTIVE,
                updated_at=current,
                last_confirmed_at=current,
                last_confirmed_turn_seq=max(0, int(turn_seq or 0)),
                unconfirmed_user_turns=0,
                expires_at=current + max(60.0, float(ttl_seconds)),
                reason="reconfirmed_open_loop",
                evidence_refs=tuple(dict.fromkeys((*existing.evidence_refs, *refs)))[:12],
            )

    # Giữ tombstone gần đây để debug/lifecycle không bị mất lịch sử, nhưng bounded.
    items.sort(key=lambda item: (item.updated_at, item.created_at), reverse=True)
    return tuple(items[:8])


def active_goal_records(records: Sequence[GoalLifecycleRecord]) -> tuple[GoalLifecycleRecord, ...]:
    return tuple(item for item in records if item.state == GoalLifecycleState.ACTIVE and item.owner in {"user", "shared"})


__all__ = [
    "GoalLifecycleRecord",
    "GoalLifecycleState",
    "active_goal_records",
    "reconcile_goal_records",
]
