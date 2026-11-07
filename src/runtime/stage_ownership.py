# -*- coding: utf-8 -*-
"""stage_ownership.py — quyền sở hữu độc quyền bề mặt phát ngôn.

Một cuộc gọi hoặc tác vụ hệ thống hữu hạn có thể cần giữ sân khấu trong một
khoảng ngắn.  Nếu không có lớp quyền sở hữu riêng, autonomous/chat/TTS đều có
thể cùng gửi payload và tranh cùng một bubble, một queue audio.  Kết quả thường
được con người mô tả rất chính xác bằng cụm "loạn hết cả lên".

Lớp này chỉ quản lý quyền, không phát audio và không biết Godot.  Producer hỏi
``blocks(channel)`` trước khi surface; owner giữ lease chịu trách nhiệm release.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Any, Iterable


@dataclass(frozen=True, slots=True)
class StageOwnershipSnapshot:
    owner_id: str = ""
    kind: str = ""
    blocks: tuple[str, ...] = ()
    acquired_at: float = 0.0

    @property
    def active(self) -> bool:
        return bool(self.owner_id)

    def to_payload(self) -> dict[str, object]:
        return {
            "active": self.active,
            "owner_id": self.owner_id,
            "kind": self.kind,
            "blocks": list(self.blocks),
            "acquired_at": self.acquired_at,
        }


class StageOwnership:
    """Thread-safe lease cho bề mặt output công khai."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._snapshot = StageOwnershipSnapshot()

    def acquire(
        self,
        *,
        owner_id: str,
        kind: str,
        blocks: Iterable[str] = ("autonomous",),
        replace: bool = False,
    ) -> bool:
        owner = str(owner_id or "").strip()
        if not owner:
            raise ValueError("Stage ownership cần owner_id không rỗng.")
        normalized_blocks = tuple(
            dict.fromkeys(str(item or "").strip().lower() for item in blocks if str(item or "").strip())
        )
        with self._lock:
            current = self._snapshot
            if current.active and current.owner_id != owner and not replace:
                return False
            self._snapshot = StageOwnershipSnapshot(
                owner_id=owner,
                kind=str(kind or "exclusive_stage").strip() or "exclusive_stage",
                blocks=normalized_blocks,
                acquired_at=time.time(),
            )
            return True

    def release(self, owner_id: str) -> bool:
        owner = str(owner_id or "").strip()
        with self._lock:
            if not self._snapshot.active or self._snapshot.owner_id != owner:
                return False
            self._snapshot = StageOwnershipSnapshot()
            return True

    def snapshot(self) -> StageOwnershipSnapshot:
        with self._lock:
            return self._snapshot

    def blocks(self, channel: str, *, requester_id: str = "") -> bool:
        normalized = str(channel or "").strip().lower()
        requester = str(requester_id or "").strip()
        with self._lock:
            current = self._snapshot
            if not current.active or (requester and requester == current.owner_id):
                return False
            return normalized in current.blocks


def stage_ownership_for(runtime_owner: Any) -> StageOwnership:
    ownership = getattr(runtime_owner, "stage_ownership", None)
    if isinstance(ownership, StageOwnership):
        return ownership
    ownership = StageOwnership()
    setattr(runtime_owner, "stage_ownership", ownership)
    return ownership


def stage_channel_blocked(runtime_owner: Any, channel: str, *, requester_id: str = "") -> bool:
    return stage_ownership_for(runtime_owner).blocks(channel, requester_id=requester_id)


__all__ = [
    "StageOwnership",
    "StageOwnershipSnapshot",
    "stage_channel_blocked",
    "stage_ownership_for",
]
