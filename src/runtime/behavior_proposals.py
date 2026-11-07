# -*- coding: utf-8 -*-
"""ProposalBus ERIS-03: hộp thư *ý tưởng*, không phải một speaker/governor thứ hai.

Các worker nền chỉ được nộp ý tưởng với identity/provenance và TTL. Bus không
chọn ý tưởng, không tạo NextConversationMove và không gọi TTS. RootAgent chỉ đọc
một projection nhỏ, còn ConversationKernel quyết định có phát ngôn hay không.
"""
from __future__ import annotations

import math
import threading
import time
import uuid
from collections import deque
from dataclasses import dataclass
from typing import Any, Callable, Mapping


def _brief(value: Any, limit: int) -> str:
    return " ".join(str(value or "").split())[:limit]


def _confidence(value: Any) -> float:
    try:
        parsed = float(value)
    except (ValueError, TypeError):
        parsed = 0.5
    return min(1.0, max(0.0, parsed)) if math.isfinite(parsed) else 0.5


@dataclass(frozen=True, slots=True)
class BehaviorProposal:
    proposal_id: str
    created_at: float
    source: str
    kind: str
    subject: str
    payload: Mapping[str, Any]
    confidence: float = 0.5
    public: bool = False
    # V03: identity từ producer, không từ nội dung câu thoại để tránh gộp nhầm.
    proposal_key: str = ""
    expires_at: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "proposal_id": self.proposal_id,
            "created_at": self.created_at,
            "source": self.source,
            "kind": self.kind,
            "subject": self.subject,
            "payload": dict(self.payload),
            "confidence": round(self.confidence, 3),
            "public": False,
            "proposal_key": self.proposal_key,
            "expires_at": self.expires_at,
        }


class ProposalBus:
    """Một inbox bounded dùng chung; không có quyền public ở bất kỳ source nào.

    Thời gian tồn tại của proposal không được gia hạn vì producer retry cùng id.
    Không dùng hàng đợi tiêu thụ (pop) vì Root của nhiều event chỉ cần snapshot
    tại thời điểm của nó, không sở hữu/xóa state nguồn.
    """

    ROOT_IDEA_KINDS = frozenset({"mental_material", "subject_affordance", "idea"})

    def __init__(self, *, clock: Callable[[], float] = time.time, max_items: int = 256) -> None:
        self.clock = clock
        self._items: deque[BehaviorProposal] = deque(maxlen=max(64, min(1024, int(max_items))))
        self._lock = threading.RLock()

    def submit(
        self,
        *,
        source: str,
        kind: str,
        subject: str = "",
        payload: Mapping[str, Any] | None = None,
        confidence: float = 0.5,
        public: bool = False,
        proposal_key: str = "",
        ttl_seconds: float = 180.0,
    ) -> BehaviorProposal:
        normalized_source = _brief(source or "runtime", 96)
        normalized_kind = _brief(kind or "observe", 96).lower()
        # Không tồn tại 'speaker qua bus' kể cả khi caller mạo danh executive.
        if public or normalized_kind in {"speak", "public_speech", "reply", "send_audio"}:
            raise PermissionError("proposal_bus_is_private_only")
        now = float(self.clock())
        ttl = max(1.0, min(1800.0, float(ttl_seconds)))
        key = _brief(proposal_key, 180)
        # Giới hạn payload để background worker không chiếm hết context/ram.
        values = payload if isinstance(payload, Mapping) else {}
        sanitized: dict[str, Any] = {}
        for name, item in list(values.items())[:16]:
            label = _brief(name, 64)
            if isinstance(item, (str, int, float, bool)) or item is None:
                sanitized[label] = _brief(item, 900) if isinstance(item, str) else item
            elif isinstance(item, (list, tuple)):
                sanitized[label] = tuple(_brief(x, 180) for x in item[:12])
            # Không serialize object/frame/worker internals vào shared inbox.
        with self._lock:
            if key:
                for old in reversed(self._items):
                    if (old.source, old.proposal_key) == (normalized_source, key) and old.expires_at > now:
                        return old
            proposal = BehaviorProposal(
                proposal_id=f"proposal:{uuid.uuid4().hex[:16]}",
                created_at=now,
                source=normalized_source,
                kind=normalized_kind,
                subject=_brief(subject, 320),
                payload=sanitized,
                confidence=_confidence(confidence),
                public=False,
                proposal_key=key,
                expires_at=now + ttl,
            )
            self._items.append(proposal)
            return proposal

    def tail(self, limit: int = 32) -> tuple[BehaviorProposal, ...]:
        with self._lock:
            now = float(self.clock())
            return tuple(x for x in self._items if x.expires_at > now)[-max(1, min(256, int(limit))):]

    def revoke(self, *, source: str, proposal_key: str) -> int:
        """Thu hồi idea đã mất quyền công khai (user đã được trả lời/topic đóng).

        Chỉ được thu hồi *đúng* identity nguồn; không xóa proposal của lane khác.
        """
        key, owner = _brief(proposal_key, 180), _brief(source, 96)
        if not key:
            return 0
        with self._lock:
            before = len(self._items)
            self._items = deque(
                (x for x in self._items if (x.source, x.proposal_key) != (owner, key)),
                maxlen=self._items.maxlen,
            )
            return before - len(self._items)

    def root_snapshot(self, limit: int = 6) -> tuple[dict[str, Any], ...]:
        """Projection cho Root: ý tưởng *chưa kiểm chứng*, không phải evidence.

        Root có toàn quyền bỏ qua; semantic của câu nói phải do Root quyết định.
        Chỉ xuất các trường allowlist, không xuất payload tùy ý từ worker.
        """
        with self._lock:
            now = float(self.clock())
            candidates = [x for x in self._items if x.expires_at > now and x.kind in self.ROOT_IDEA_KINDS]
            selected = candidates[-max(1, min(12, int(limit))):]
        return tuple({
            "proposal_id": x.proposal_id,
            "source": x.source,
            "kind": x.kind,
            "topic_hint": x.subject[:320],
            "confidence": round(x.confidence, 3),
            "source_refs": [str(r)[:180] for r in (
                x.payload.get("source_refs") if isinstance(x.payload.get("source_refs"), (list, tuple)) else ()
            )[:8]],
            "epistemic_status": "unverified_private_idea",
        } for x in selected)


def ensure_proposal_bus(host: Any) -> ProposalBus:
    existing = getattr(host, "behavior_proposal_bus", None)
    if isinstance(existing, ProposalBus):
        return existing
    bus = ProposalBus()
    try:
        setattr(host, "behavior_proposal_bus", bus)
    except Exception:
        pass
    return bus


__all__ = ["BehaviorProposal", "ProposalBus", "ensure_proposal_bus"]
