# -*- coding: utf-8 -*-
"""V93 - provenance công khai cho mỗi câu ERIS thực sự phát ra.

Module này cố ý KHÔNG lưu chain-of-thought. Nó chỉ giữ metadata causal ngắn gọn,
đủ để lượt sau trả lời trung thực các câu kiểu "sao em lại nói vậy?" mà không phải
đoán ngược lịch sử từ prose.
"""
from __future__ import annotations

from dataclasses import dataclass
import time
from typing import Any, Mapping, Sequence


_VALID_OWNERS = {"user", "eris", "shared", "environment", "external", "unknown"}


def _compact(value: Any, limit: int = 320) -> str:
    return " ".join(str(value or "").split())[: max(16, int(limit))]


def _clamp01(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = default
    if number != number:
        number = default
    return max(0.0, min(1.0, number))


def normalize_semantic_owner(value: Any, *, default: str = "unknown") -> str:
    """Chuẩn hóa owner theo fail-closed: không chắc thì ``unknown``, không đoán user."""
    raw = _compact(value, 48).lower()
    aliases = {
        "assistant": "eris",
        "self": "eris",
        "system": "eris",
        "human": "user",
        "conversation": "shared",
        "joint": "shared",
        "world": "environment",
        "screen": "environment",
    }
    raw = aliases.get(raw, raw)
    if raw in _VALID_OWNERS:
        return raw
    fallback = aliases.get(_compact(default, 48).lower(), _compact(default, 48).lower())
    return fallback if fallback in _VALID_OWNERS else "unknown"


def infer_open_loop_owner(*, event_kind: Any, explicit_owner: Any = "") -> str:
    """Owner của open-loop observer.

    Autonomous observation mặc định thuộc ERIS. Đây là invariant V93 ngăn thought
    nội sinh bị state reducer nâng thành món nợ chung của user.
    """
    explicit = normalize_semantic_owner(explicit_owner)
    if explicit != "unknown":
        return explicit
    kind = _compact(event_kind, 48).lower()
    if kind == "autonomous":
        return "eris"
    if kind in {"chat", "user", "direct_chat"}:
        return "shared"
    if kind in {"environment", "screen", "telemetry"}:
        return "environment"
    return "unknown"


def infer_candidate_semantic_owner(candidate: Any) -> str:
    """Suy ra owner từ typed candidate, chỉ dùng signal cấu trúc đã có sẵn."""
    kind = _compact(getattr(candidate, "kind", ""), 64).lower()
    source = _compact(getattr(candidate, "source", ""), 80).lower()
    source_kind = _compact(getattr(candidate, "source_kind", ""), 80).lower()
    anchor = _compact(getattr(candidate, "anchor_type", ""), 48).lower()
    speech_act = _compact(getattr(candidate, "speech_act", ""), 80).lower()

    if source in {"expression_urge", "mental_material", "private_mind", "subject_initiative_committed"}:
        return "eris"
    if source_kind in {"eris_self_memory", "private_life_workspace", "weak_association", "self_context"}:
        return "eris"
    if kind in {"inner_thought"} or speech_act in {"share_self_originated_thought", "self_clarification"}:
        return "eris"
    if kind in {"screen_comment", "semantic_scene", "discovery_share"} or source_kind == "current_environment":
        return "environment"
    if kind in {"continuation", "followup_reminder", "task_followup"}:
        return "shared"
    if kind == "force_speak":
        return "shared"
    if anchor == "internal":
        return "eris"
    if anchor in {"conversation", "task"}:
        return "shared"
    return "unknown"


def infer_candidate_trigger_owner(candidate: Any) -> str:
    refs = tuple(str(item or "").lower() for item in (getattr(candidate, "evidence_refs", ()) or ()))
    kind = _compact(getattr(candidate, "kind", ""), 64).lower()
    anchor = _compact(getattr(candidate, "anchor_type", ""), 48).lower()
    source_kind = _compact(getattr(candidate, "source_kind", ""), 80).lower()
    if kind == "force_speak" or any(ref.startswith(("user:", "user-event:", "user_event:")) for ref in refs):
        return "user"
    if kind in {"screen_comment", "semantic_scene", "discovery_share"} or source_kind == "current_environment":
        return "environment"
    if anchor in {"conversation", "task"}:
        return "shared"
    if anchor == "internal":
        return "eris"
    return "unknown"


@dataclass(frozen=True, slots=True)
class PublicCausalReceipt:
    channel: str
    semantic_owner: str
    trigger_owner: str
    source_kind: str = ""
    source_ref: str = ""
    motive: str = ""
    why_now: str = ""
    user_triggered: bool = False
    confidence: float = 0.0
    speech_act: str = ""
    evidence_refs: tuple[str, ...] = ()
    created_at: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": "eris.public_causal_receipt.v93",
            "channel": _compact(self.channel, 40).lower(),
            "semantic_owner": normalize_semantic_owner(self.semantic_owner),
            "trigger_owner": normalize_semantic_owner(self.trigger_owner),
            "source_kind": _compact(self.source_kind, 100),
            "source_ref": _compact(self.source_ref, 180),
            "motive": _compact(self.motive, 120),
            "why_now": _compact(self.why_now, 360),
            "user_triggered": bool(self.user_triggered),
            "confidence": round(_clamp01(self.confidence), 4),
            "speech_act": _compact(self.speech_act, 100).lower(),
            "evidence_refs": [_compact(item, 180) for item in self.evidence_refs[:8] if _compact(item, 180)],
            "created_at": float(self.created_at or 0.0),
        }

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any] | None) -> "PublicCausalReceipt":
        data = dict(payload or {})
        refs = data.get("evidence_refs", ())
        if isinstance(refs, str):
            refs = (refs,)
        elif not isinstance(refs, Sequence):
            refs = ()
        return cls(
            channel=_compact(data.get("channel", ""), 40).lower(),
            semantic_owner=normalize_semantic_owner(data.get("semantic_owner", "unknown")),
            trigger_owner=normalize_semantic_owner(data.get("trigger_owner", "unknown")),
            source_kind=_compact(data.get("source_kind", ""), 100),
            source_ref=_compact(data.get("source_ref", ""), 180),
            motive=_compact(data.get("motive", ""), 120),
            why_now=_compact(data.get("why_now", ""), 360),
            user_triggered=bool(data.get("user_triggered", False)),
            confidence=_clamp01(data.get("confidence", 0.0)),
            speech_act=_compact(data.get("speech_act", ""), 100).lower(),
            evidence_refs=tuple(_compact(item, 180) for item in tuple(refs)[:8] if _compact(item, 180)),
            created_at=float(data.get("created_at", 0.0) or 0.0),
        )


def build_public_causal_receipt(
    *,
    channel: Any,
    speech_act: Any = "",
    semantic_owner: Any = "unknown",
    trigger_owner: Any = "unknown",
    source_kind: Any = "",
    source_ref: Any = "",
    motive: Any = "",
    why_now: Any = "",
    user_triggered: bool = False,
    confidence: Any = 0.0,
    evidence_refs: Sequence[Any] = (),
    now: float | None = None,
) -> PublicCausalReceipt:
    semantic = normalize_semantic_owner(semantic_owner)
    trigger = normalize_semantic_owner(trigger_owner)
    return PublicCausalReceipt(
        channel=_compact(channel, 40).lower(),
        semantic_owner=semantic,
        trigger_owner=trigger,
        source_kind=_compact(source_kind, 100),
        source_ref=_compact(source_ref, 180),
        motive=_compact(motive, 120),
        why_now=_compact(why_now, 360),
        user_triggered=bool(user_triggered or trigger == "user"),
        confidence=_clamp01(confidence),
        speech_act=_compact(speech_act, 100).lower(),
        evidence_refs=tuple(_compact(item, 180) for item in tuple(evidence_refs)[:8] if _compact(item, 180)),
        created_at=float(now if now is not None else time.time()),
    )


def build_receipt_from_plan(plan: Any, *, channel: str, speech_act: str = "", now: float | None = None) -> PublicCausalReceipt:
    """Tạo receipt từ IntentionPlan/plan-like object đã commit, không đọc prose public."""
    semantic_owner = normalize_semantic_owner(getattr(plan, "semantic_owner", "unknown"))
    trigger_owner = normalize_semantic_owner(getattr(plan, "trigger_owner", "unknown"))
    if semantic_owner == "unknown" and str(channel).lower() == "chat":
        semantic_owner = "shared"
    if trigger_owner == "unknown" and str(channel).lower() == "chat":
        trigger_owner = "user"
    return build_public_causal_receipt(
        channel=channel,
        speech_act=speech_act or getattr(getattr(plan, "semantics", None), "speech_act", ""),
        semantic_owner=semantic_owner,
        trigger_owner=trigger_owner,
        source_kind=getattr(plan, "source_kind", "") or getattr(plan, "owner", ""),
        source_ref=getattr(plan, "source_ref", "") or getattr(getattr(plan, "semantics", None), "subject_key", ""),
        motive=getattr(plan, "motive", ""),
        why_now=getattr(plan, "why_now", "") or getattr(plan, "notes", ""),
        user_triggered=bool(getattr(plan, "user_triggered", False) or trigger_owner == "user"),
        confidence=getattr(plan, "confidence", 0.0),
        evidence_refs=tuple(getattr(plan, "evidence_refs", ()) or ()),
        now=now,
    )


__all__ = [
    "PublicCausalReceipt",
    "build_public_causal_receipt",
    "build_receipt_from_plan",
    "infer_candidate_semantic_owner",
    "infer_candidate_trigger_owner",
    "infer_open_loop_owner",
    "normalize_semantic_owner",
]
