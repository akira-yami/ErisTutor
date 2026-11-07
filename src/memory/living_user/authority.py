# -*- coding: utf-8 -*-
"""Nguồn thẩm quyền cho các thao tác người dùng lên memory.

Living-user profile, semantic index và panel chỉ là projection. Khi người dùng
sửa, xác nhận, đổi phạm vi hoặc yêu cầu quên, thao tác đó được ghi append-only
vào ledger này trước. Nhờ vậy một lần semantic sync sau đó không thể lặng lẽ
khôi phục giá trị cũ như thể nút "Quên" chỉ mang tính trang trí.
"""
from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Literal, Mapping

from memory.json_store_io import atomic_write_json, locked_path

from .models import ProfileOperation, UserMemoryScope, normalize_path, value_fingerprint

AuthorityAction = Literal["correct", "confirm", "forget", "move_scope", "restore"]


@dataclass(frozen=True, slots=True)
class UserMemoryAuthorityEvent:
    """Một quyết định trực tiếp của người dùng về memory.

    Event không bị sửa tại chỗ. Trạng thái hiện tại được suy ra từ chuỗi event,
    giống một sổ cái tử tế thay vì nhiều file JSON tự nhận mình là bản gốc.
    """

    id: str
    action: AuthorityAction
    path: str
    scope: UserMemoryScope = field(default_factory=UserMemoryScope)
    value: Any = None
    target_scope: UserMemoryScope | None = None
    hard: bool = False
    evidence_ids: tuple[str, ...] = ()
    source: str = "user_panel"
    created_at: float = field(default_factory=time.time)
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "path", normalize_path(self.path))
        object.__setattr__(self, "scope", UserMemoryScope.from_value(self.scope))
        if self.target_scope is not None:
            object.__setattr__(self, "target_scope", UserMemoryScope.from_value(self.target_scope))
        object.__setattr__(self, "evidence_ids", tuple(dict.fromkeys(str(x) for x in self.evidence_ids if str(x).strip()))[:96])
        object.__setattr__(self, "metadata", dict(self.metadata or {}))

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "action": self.action,
            "path": self.path,
            "scope": self.scope.to_dict(),
            "value": self.value,
            "target_scope": self.target_scope.to_dict() if self.target_scope else None,
            "hard": bool(self.hard),
            "evidence_ids": list(self.evidence_ids),
            "source": self.source,
            "created_at": float(self.created_at),
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "UserMemoryAuthorityEvent":
        action = str(data.get("action") or "correct")
        if action not in {"correct", "confirm", "forget", "move_scope", "restore"}:
            action = "correct"
        target = data.get("target_scope")
        return cls(
            id=str(data.get("id") or ""),
            action=action,  # type: ignore[arg-type]
            path=str(data.get("path") or ""),
            scope=UserMemoryScope.from_value(data.get("scope")),
            value=data.get("value"),
            target_scope=UserMemoryScope.from_value(target) if target else None,
            hard=bool(data.get("hard", False)),
            evidence_ids=tuple(data.get("evidence_ids") or ()),
            source=str(data.get("source") or "user_panel"),
            created_at=float(data.get("created_at") or time.time()),
            metadata=dict(data.get("metadata") or {}),
        )


@dataclass(frozen=True, slots=True)
class AuthorityDecision:
    allowed: bool
    operation: ProfileOperation | None = None
    reason: str = ""
    authority_event_id: str = ""


class UserMemoryAuthorityLedger:
    """Append-only ledger và policy gate cho mọi projection memory."""

    def __init__(self, path: str | os.PathLike[str], *, max_events: int = 1600) -> None:
        self.path = Path(path)
        self.max_events = max(100, int(max_events))
        self._lock = threading.RLock()
        self._events: list[UserMemoryAuthorityEvent] = []
        self.load()

    def all(self) -> tuple[UserMemoryAuthorityEvent, ...]:
        with self._lock:
            return tuple(self._events)

    def append(
        self,
        action: AuthorityAction,
        path: str,
        *,
        scope: UserMemoryScope | None = None,
        value: Any = None,
        target_scope: UserMemoryScope | None = None,
        hard: bool = False,
        evidence_ids: Iterable[str] = (),
        source: str = "user_panel",
        metadata: Mapping[str, Any] | None = None,
    ) -> UserMemoryAuthorityEvent:
        clean_path = normalize_path(path)
        if not clean_path:
            raise ValueError("empty_authority_path")
        active_scope = scope or UserMemoryScope()
        created_at = time.time()
        fingerprint = value_fingerprint(value) if value is not None else ""
        seed = f"{action}|{clean_path}|{active_scope.identity}|{fingerprint}|{created_at:.9f}"
        event = UserMemoryAuthorityEvent(
            id=f"uma_{hashlib.sha256(seed.encode('utf-8')).hexdigest()[:20]}",
            action=action,
            path=clean_path,
            scope=active_scope,
            value=value,
            target_scope=target_scope,
            hard=hard,
            evidence_ids=tuple(evidence_ids),
            source=source,
            created_at=created_at,
            metadata=dict(metadata or {}),
        )
        with self._lock:
            if action == "forget" and hard:
                # Hard-delete phải scrub cả correction history trong authority
                # ledger. Chỉ giữ tombstone path/scope để ngăn nguồn cũ tái nhập;
                # value và evidence cũ không được nằm lại dưới danh nghĩa audit.
                self._events = [
                    old for old in self._events
                    if not self._event_is_covered_by_hard_forget(old, clean_path, active_scope)
                ]
            self._events.append(event)
            self._events = self._events[-self.max_events :]
            self.save()
        return event

    @staticmethod
    def _event_is_covered_by_hard_forget(
        event: UserMemoryAuthorityEvent,
        path_prefix: str,
        scope: UserMemoryScope,
    ) -> bool:
        path_match = event.path == path_prefix or event.path.startswith(path_prefix + ".")
        source_match = event.scope.identity == scope.identity
        target_match = event.target_scope is not None and event.target_scope.identity == scope.identity
        return path_match and (source_match or target_match)

    def latest_rule(self, path: str, scope: UserMemoryScope | None = None) -> UserMemoryAuthorityEvent | None:
        """Lấy quyết định mới nhất áp lên path/scope.

        Forget có thể dùng prefix nên được xét cả ``user`` cho ``user.name``.
        Các action còn lại chỉ áp exact path để một sửa đổi không lan sang họ hàng.
        """
        clean_path = normalize_path(path)
        active_scope = scope or UserMemoryScope()
        with self._lock:
            for event in reversed(self._events):
                same_scope = event.scope.identity == active_scope.identity
                if not same_scope:
                    continue
                exact = event.path == clean_path
                prefix_forget = event.action == "forget" and (
                    clean_path == event.path or clean_path.startswith(event.path + ".")
                )
                if exact or prefix_forget:
                    return event
        return None

    def decide(self, operation: ProfileOperation) -> AuthorityDecision:
        """Lọc/remap operation từ semantic/dreaming trước khi chạm projection."""
        event = self.latest_rule(operation.path, operation.scope)
        if event is None or event.action == "restore":
            return AuthorityDecision(True, operation)
        if event.action == "forget":
            return AuthorityDecision(False, None, "blocked_by_user_forget", event.id)
        if event.action in {"correct", "confirm"}:
            if value_fingerprint(operation.value) == value_fingerprint(event.value):
                # Cho phép evidence cùng giá trị củng cố correction, nhưng luôn giữ
                # explicitness và authority metadata mạnh hơn nguồn tự động.
                guarded = ProfileOperation.from_dict({
                    **operation.to_dict(),
                    "value": event.value,
                    "explicitness": "explicit",
                    "confidence": max(0.99, operation.confidence),
                    "source": "user_authority_reinforcement",
                    "metadata": {**operation.metadata, "authority_event_id": event.id},
                })
                return AuthorityDecision(True, guarded, authority_event_id=event.id)
            return AuthorityDecision(False, None, "conflicts_with_user_correction", event.id)
        if event.action == "move_scope" and event.target_scope is not None:
            guarded = ProfileOperation.from_dict({
                **operation.to_dict(),
                "scope": event.target_scope.to_dict(),
                "metadata": {**operation.metadata, "authority_event_id": event.id, "remapped_from_scope": operation.scope.identity},
            })
            return AuthorityDecision(True, guarded, authority_event_id=event.id)
        return AuthorityDecision(True, operation)

    def guard_operations(self, operations: Iterable[ProfileOperation]) -> tuple[tuple[ProfileOperation, ...], tuple[str, ...]]:
        allowed: list[ProfileOperation] = []
        rejected: list[str] = []
        for operation in operations:
            decision = self.decide(operation)
            if decision.allowed and decision.operation is not None:
                allowed.append(decision.operation)
            else:
                rejected.append(f"{decision.reason}:{operation.path}")
        return tuple(allowed), tuple(rejected)

    def projection_operations(self) -> tuple[ProfileOperation, ...]:
        """Sinh patch authority để replay lên profile projection.

        Chỉ correction/confirm/move cần patch. Forget được service áp bằng method
        chuyên biệt để hard-delete xóa thật record và history của projection.
        """
        operations: list[ProfileOperation] = []
        for event in self._events:
            evidence = (f"authority:{event.id}", *event.evidence_ids)
            if event.action in {"correct", "confirm"}:
                operations.append(ProfileOperation(
                    op="update",
                    path=event.path,
                    value=event.value,
                    confidence=1.0,
                    explicitness="explicit",
                    scope=event.scope,
                    evidence_ids=evidence,
                    reason="Projection từ quyết định trực tiếp của người dùng.",
                    source="user_authority",
                    metadata={"authority_event_id": event.id},
                ))
            elif event.action == "move_scope" and event.target_scope is not None:
                operations.append(ProfileOperation(
                    op="update",
                    path=event.path,
                    value=event.value,
                    confidence=1.0,
                    explicitness="explicit",
                    scope=event.target_scope,
                    evidence_ids=evidence,
                    reason="Projection phạm vi mới do người dùng chọn.",
                    source="user_authority_scope",
                    metadata={"authority_event_id": event.id, "moved_from": event.scope.identity},
                ))
        return tuple(operations)

    def load(self) -> None:
        with self._lock:
            if not self.path.is_file() or self.path.stat().st_size <= 0:
                self._events = []
                return
            try:
                with locked_path(self.path):
                    raw = json.loads(self.path.read_text(encoding="utf-8"))
                rows = raw.get("events") if isinstance(raw, Mapping) else raw
                self._events = [
                    UserMemoryAuthorityEvent.from_dict(row)
                    for row in rows or []
                    if isinstance(row, Mapping)
                ][-self.max_events :]
            except Exception:
                self._events = []

    def save(self) -> None:
        atomic_write_json(self.path, {
            "schema_version": 1,
            "kind": "user_memory_authority_ledger",
            "events": [event.to_dict() for event in self._events[-self.max_events :]],
        })


__all__ = [
    "AuthorityAction",
    "AuthorityDecision",
    "UserMemoryAuthorityEvent",
    "UserMemoryAuthorityLedger",
]
