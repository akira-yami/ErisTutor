# -*- coding: utf-8 -*-
"""Contract V12 cho hội thoại Eris.

Nguyên tắc cốt lõi:
- Agent quyết định *ý nghĩa* cần nói qua :class:`NextConversationMove`.
- Runtime chỉ giữ state cơ học: generation, run, delivery, timer, receipt.
- Không biểu diễn nhịp nói bằng floor/beat/projection hay quota số câu.

File này cố ý nhỏ. Nếu một field mới không cần thiết để runtime vận hành an toàn,
đừng thêm nó vào contract hội thoại.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping, TypeAlias
import time
import uuid


def _text(value: Any, limit: int) -> str:
    return " ".join(str(value or "").strip().split())[:limit]


def _parse_presentation_blocks(raw: Any) -> tuple[dict[str, Any], ...]:
    from ..workspace.presentation_p2 import normalize_presentation_blocks
    return normalize_presentation_blocks(raw)


class ConversationAfter(str, Enum):
    """Lifecycle duy nhất sau khi lời hiện tại đã được phát thành công."""

    WAIT_USER = "wait_user"
    MAY_CONTINUE = "may_continue"
    CONTINUE_SELF = "continue_self"


class RunPhase(str, Enum):
    """Phase vận hành public-safe; UI có thể hiển thị trực tiếp."""

    IDLE = "idle"
    THINKING = "thinking"
    WORKING = "working"
    WRITING = "writing"
    DELIVERING = "delivering"
    WAITING_USER = "waiting_user"
    CONTINUATION_PENDING = "continuation_pending"
    CANCELLED = "cancelled"
    FAILED = "failed"


class InterruptKind(str, Enum):
    BACKCHANNEL = "backchannel"
    STOP = "stop"
    STEERING = "steering"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class NextConversationMove:
    """Quyết định semantic duy nhất mà root agent phải finalize.

    ``say`` thường là chỉ dẫn nội dung cho SurfaceWriter. Riêng prose cứu từ
    response lỗi có ``literal_dialogue`` để giữ nguyên lời đã sinh, không viết
    lại. ``delivery`` chỉ mô tả cách thể hiện gần đúng. Runtime tuyệt đối
    không suy thêm thread, floor hay teaching move từ hai field này.
    """

    intent: str
    say: str
    delivery: str = ""
    after: ConversationAfter = ConversationAfter.WAIT_USER
    move_id: str = field(default_factory=lambda: f"move:{uuid.uuid4().hex}")
    literal_dialogue: bool = False
    # Tài liệu do Root quyết định, KHÔNG dùng làm nguồn TTS.
    document_source: str = ""
    # P2: Root-owned structured presentation, separate from the spoken surface.
    presentation_blocks: tuple[dict[str, Any], ...] = ()
    # Internal executor receipts only. Never sourced from model-provided IDs.
    tool_artifact_bundles: tuple[str, ...] = ()
    canvas_segments: tuple[dict[str, str], ...] = ()
    # Decision summary used only for attribution after successful delivery.
    interaction_choice: Mapping[str, str] = field(default_factory=dict)

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any] | None) -> "NextConversationMove":
        data = raw if isinstance(raw, Mapping) else {}
        from .interaction_choice import normalize_interaction_choice
        try:
            after = ConversationAfter(str(data.get("after", "wait_user") or "wait_user").strip().lower())
        except ValueError:
            after = ConversationAfter.WAIT_USER
        intent = _text(data.get("intent"), 1200)
        say = _text(data.get("say"), 4000)
        if not intent:
            intent = "Phản hồi trực tiếp và hữu ích cho lượt hiện tại."
        if not say:
            say = "Trả lời trực tiếp điều user vừa nói, không bịa thêm ngữ cảnh."
        return cls(
            intent=intent,
            say=say,
            delivery=_text(data.get("delivery"), 500),
            after=after,
            move_id=_text(data.get("move_id"), 180) or f"move:{uuid.uuid4().hex}",
            literal_dialogue=data.get("_runtime_literal_dialogue") is True,
            interaction_choice=normalize_interaction_choice(data.get("interaction_choice")),
            document_source=str(data.get("document_source", "") or "")[:12000],
            presentation_blocks=_parse_presentation_blocks(data.get("presentation_blocks")),
            canvas_segments=tuple({"block_id": str(p.get("block_id", ""))[:120], "say": str(p.get("say", ""))[:1200]}
                for p in (data.get("canvas_segments") or ())[:6] if isinstance(p, Mapping))
                if isinstance(data.get("canvas_segments", ()), (tuple, list)) else (),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": "eris.next_conversation_move.v12",
            "kind": "move",
            "move_id": self.move_id,
            "intent": self.intent,
            "say": self.say,
            "delivery": self.delivery,
            "after": self.after.value,
            **({"interaction_choice": dict(self.interaction_choice)} if self.interaction_choice else {}),
            **({"document_source": self.document_source} if self.document_source else {}),
            **({"presentation_blocks": list(self.presentation_blocks)} if self.presentation_blocks else {}),
            **({"canvas_segments": list(self.canvas_segments)} if self.canvas_segments else {}),
        }


@dataclass(frozen=True, slots=True)
class NoReply:
    """Root-agent decision to finish an event without public speech."""

    reason: str
    decision_id: str = field(default_factory=lambda: f"decision:{uuid.uuid4().hex}")

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any] | None) -> "NoReply":
        data = raw if isinstance(raw, Mapping) else {}
        return cls(
            reason=_text(data.get("reason"), 500) or "no_reply",
            decision_id=_text(data.get("decision_id"), 180) or f"decision:{uuid.uuid4().hex}",
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": "eris.conversation_no_reply.v12",
            "kind": "no_reply",
            "decision_id": self.decision_id,
            "reason": self.reason,
        }


ConversationDecision: TypeAlias = NoReply | NextConversationMove


@dataclass(frozen=True, slots=True)
class ConversationEvent:
    event_id: str
    session_id: str
    kind: str
    text: str = ""
    generation: int = 0
    created_at: float = field(default_factory=time.time)
    metadata: Mapping[str, Any] = field(default_factory=dict)

    @classmethod
    def user_turn(cls, *, session_id: str, text: str, generation: int = 0) -> "ConversationEvent":
        return cls(
            event_id=f"user:{uuid.uuid4().hex}",
            session_id=_text(session_id, 180) or "default",
            kind="user_turn",
            text=str(text or "")[:16000],
            generation=max(0, int(generation)),
        )

    @classmethod
    def continuation_due(
        cls,
        *,
        session_id: str,
        generation: int,
        lease_id: str,
    ) -> "ConversationEvent":
        return cls(
            event_id=f"continuation:{_text(lease_id, 180) or uuid.uuid4().hex}",
            session_id=_text(session_id, 180) or "default",
            kind="continuation_due",
            generation=max(0, int(generation)),
            metadata={"lease_id": _text(lease_id, 180)},
        )


@dataclass(frozen=True, slots=True)
class ConversationSessionState:
    """State cơ học duy nhất của conversation runtime.

    ``note`` là plain text do agent sở hữu; runtime chỉ lưu nguyên văn. Không có
    next-thread, desired-shape hay bất kỳ semantic scheduler field nào ở đây.
    """

    session_id: str
    generation: int = 0
    active_run_id: str = ""
    active_move_id: str = ""
    active_delivery_id: str = ""
    phase: RunPhase = RunPhase.IDLE
    note: str = ""
    note_updated_at: float = 0.0
    last_delivered_move_id: str = ""
    last_delivery_finished_at: float = 0.0
    updated_at: float = 0.0
    version: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": "eris.conversation_session.v12",
            "session_id": self.session_id,
            "generation": self.generation,
            "active_run_id": self.active_run_id,
            "active_move_id": self.active_move_id,
            "active_delivery_id": self.active_delivery_id,
            "phase": self.phase.value,
            "note": self.note,
            "note_updated_at": self.note_updated_at,
            "last_delivered_move_id": self.last_delivered_move_id,
            "last_delivery_finished_at": self.last_delivery_finished_at,
            "updated_at": self.updated_at,
            "version": self.version,
        }

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any] | None, *, session_id: str) -> "ConversationSessionState":
        data = raw if isinstance(raw, Mapping) else {}
        try:
            phase = RunPhase(str(data.get("phase", "idle") or "idle"))
        except ValueError:
            phase = RunPhase.IDLE
        return cls(
            session_id=_text(data.get("session_id", session_id), 180) or session_id,
            generation=max(0, int(data.get("generation", 0) or 0)),
            active_run_id=_text(data.get("active_run_id"), 180),
            active_move_id=_text(data.get("active_move_id"), 180),
            active_delivery_id=_text(data.get("active_delivery_id"), 180),
            phase=phase,
            note=str(data.get("note", "") or "")[:12000],
            note_updated_at=max(0.0, float(data.get("note_updated_at", 0.0) or 0.0)),
            last_delivered_move_id=_text(data.get("last_delivered_move_id"), 180),
            last_delivery_finished_at=max(0.0, float(data.get("last_delivery_finished_at", 0.0) or 0.0)),
            updated_at=max(0.0, float(data.get("updated_at", 0.0) or 0.0)),
            version=max(0, int(data.get("version", 0) or 0)),
        )


@dataclass(frozen=True, slots=True)
class ContinuationLease:
    """Lease cơ học cho một cơ hội continuation.

    Các field ``expires_at`` / ``chain_started_at`` / ``consecutive_self_wakes``
    chỉ phục vụ chống wake ma và runaway loop. Chúng không mang topic, câu cần
    nói hay bất kỳ semantic objective nào.
    """

    lease_id: str
    session_id: str
    generation: int
    source_move_id: str
    due_at: float
    mode: ConversationAfter
    expires_at: float = 0.0
    chain_started_at: float = 0.0
    consecutive_self_wakes: int = 0
    status: str = "pending"
    claim_token: str = ""
    updated_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": "eris.continuation_lease.v12",
            "lease_id": self.lease_id,
            "session_id": self.session_id,
            "generation": self.generation,
            "source_move_id": self.source_move_id,
            "due_at": self.due_at,
            "expires_at": self.expires_at,
            "chain_started_at": self.chain_started_at,
            "consecutive_self_wakes": self.consecutive_self_wakes,
            "mode": self.mode.value,
            "status": self.status,
            "claim_token": self.claim_token,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any] | None) -> "ContinuationLease | None":
        data = raw if isinstance(raw, Mapping) else {}
        lease_id = _text(data.get("lease_id"), 180)
        session_id = _text(data.get("session_id"), 180)
        if not lease_id or not session_id:
            return None
        try:
            mode = ConversationAfter(str(data.get("mode", "wait_user") or "wait_user"))
        except ValueError:
            return None
        return cls(
            lease_id=lease_id,
            session_id=session_id,
            generation=max(0, int(data.get("generation", 0) or 0)),
            source_move_id=_text(data.get("source_move_id"), 180),
            due_at=max(0.0, float(data.get("due_at", 0.0) or 0.0)),
            expires_at=max(0.0, float(data.get("expires_at", 0.0) or 0.0)),
            chain_started_at=max(0.0, float(data.get("chain_started_at", 0.0) or 0.0)),
            consecutive_self_wakes=max(0, int(data.get("consecutive_self_wakes", 0) or 0)),
            mode=mode,
            status=_text(data.get("status"), 40) or "pending",
            claim_token=_text(data.get("claim_token"), 180),
            updated_at=max(0.0, float(data.get("updated_at", 0.0) or 0.0)),
        )


@dataclass(frozen=True, slots=True)
class DeliveryReceipt:
    delivery_id: str
    session_id: str
    generation: int
    move_id: str
    successful: bool
    finished_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": "eris.delivery_receipt.v12",
            "delivery_id": self.delivery_id,
            "session_id": self.session_id,
            "generation": self.generation,
            "move_id": self.move_id,
            "successful": self.successful,
            "finished_at": self.finished_at,
        }


__all__ = [
    "ConversationAfter", "ConversationDecision", "ConversationEvent", "ConversationSessionState",
    "ContinuationLease", "DeliveryReceipt", "InterruptKind", "NextConversationMove",
    "NoReply", "RunPhase",
]
