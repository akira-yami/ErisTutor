# -*- coding: utf-8 -*-
"""Typed semantic contracts cho self-initiated turn.

Chỉ model/observer được quyền gán nghĩa. Runtime dùng các key đã khai báo để
cooldown, nối mạch và chống lặp; không đoán lại bằng keyword, dấu hỏi hoặc Jaccard.
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Any, Mapping, Sequence


_KEY_RE = re.compile(r"[^a-z0-9_.:-]+")


def compact(value: Any, limit: int = 180) -> str:
    text = " ".join(str(value or "").split()).strip()
    return text if len(text) <= limit else text[: max(0, limit - 3)].rstrip() + "..."


def semantic_key(value: Any, *, fallback: str = "unknown", limit: int = 96) -> str:
    """Chuẩn hóa *identifier model đã chọn*, không suy nghĩa từ câu tự nhiên."""
    raw = str(value or "").strip().lower()
    normalized = _KEY_RE.sub("_", raw).strip("_.:-")
    return (normalized or fallback)[:limit]


_SPEECH_ACT_ALIASES = {
    "random chat": "random_chat",
    "small talk": "small_talk",
    "user probe": "user_probe",
    "direct answer": "direct_answer",
    "topic shift": "independent_topic_shift",
}


def speech_act_key(value: Any, *, fallback: str = "unspecified", limit: int = 64) -> str:
    """Chỉ nhận speech_act có hình dạng identifier; prose phải fail closed.

    Trước đây một câu mô tả tiếng Việt bị ``semantic_key`` thay từng ký tự có
    dấu thành ``_`` rồi trở thành ID rác trong cooldown/log. Speech act là API
    identity, không phải chỗ chứa câu văn tự do.
    """

    raw = str(value or "").strip().lower()
    if not raw:
        return fallback
    if raw in _SPEECH_ACT_ALIASES:
        return _SPEECH_ACT_ALIASES[raw][:limit]
    # Identifier hợp lệ không chứa khoảng trắng và chỉ dùng ASCII key chars.
    if re.fullmatch(r"[a-z0-9][a-z0-9_.:-]{0,95}", raw):
        return raw[:limit]
    return fallback


@dataclass(frozen=True, slots=True)
class DiscourseFrame:
    """Kết luận discourse do post-turn observer tạo.

    ``status`` chỉ nhận trạng thái rõ. Timer/history ở runtime không được tự đổi
    closed thành open hay biến một dấu hỏi thành nghĩa vụ tiếp lời.
    """

    status: str = "unknown"
    reply_expected: bool = False
    assistant_may_continue: bool = False
    unresolved_point: str = ""
    thread_key: str = "unknown"
    confidence: float = 0.0
    evidence: tuple[str, ...] = ()

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any] | None) -> "DiscourseFrame":
        data = payload or {}
        status = semantic_key(data.get("status", "unknown"), fallback="unknown", limit=40)
        if status not in {"open", "closed", "waiting_user", "neutral", "unknown"}:
            status = "unknown"
        raw_evidence = data.get("evidence", ())
        if isinstance(raw_evidence, str):
            evidence = (compact(raw_evidence, 140),) if raw_evidence.strip() else ()
        elif isinstance(raw_evidence, Sequence):
            evidence = tuple(compact(item, 140) for item in raw_evidence[:4] if compact(item, 140))
        else:
            evidence = ()
        try:
            confidence = float(data.get("confidence", 0.0) or 0.0)
        except (TypeError, ValueError):
            confidence = 0.0
        return cls(
            status=status,
            reply_expected=bool(data.get("reply_expected", False)),
            assistant_may_continue=bool(data.get("assistant_may_continue", False)),
            unresolved_point=compact(data.get("unresolved_point", ""), 220),
            thread_key=semantic_key(data.get("thread_key", "unknown"), fallback="unknown", limit=96),
            confidence=max(0.0, min(1.0, confidence)),
            evidence=evidence,
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class TurnSemantics:
    """Danh tính semantic ổn định của một public turn do planner gán."""

    turn_kind: str = "other"
    speech_act: str = "unspecified"
    subject_key: str = "unknown"
    evidence_scope: str = "none"
    continuation_key: str = "none"

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any] | None) -> "TurnSemantics":
        data = payload or {}
        kind = semantic_key(data.get("turn_kind", "other"), fallback="other", limit=40)
        if kind not in {
            "chat_response", "continuation", "environment", "activity_event",
            "discovery", "internal", "presence", "startup", "force_speak", "other",
        }:
            kind = "other"
        return cls(
            turn_kind=kind,
            speech_act=speech_act_key(data.get("speech_act", "unspecified"), fallback="unspecified", limit=64),
            subject_key=semantic_key(data.get("subject_key", "unknown"), fallback="unknown", limit=96),
            evidence_scope=semantic_key(data.get("evidence_scope", "none"), fallback="none", limit=96),
            continuation_key=semantic_key(data.get("continuation_key", "none"), fallback="none", limit=96),
        )

    @property
    def signature(self) -> str:
        return f"{self.turn_kind}:{self.speech_act}:{self.subject_key}:{self.evidence_scope}"

    @property
    def complete(self) -> bool:
        return self.speech_act != "unspecified" and self.subject_key != "unknown"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class ActivitySemantics:
    """Annotation semantic của phiên app/game do main planner tạo.

    Tracker chỉ giữ annotation này cho tick sau; nó không đọc chữ ``victory`` hay
    ``error`` rồi tự tuyên bố user vừa thắng hoặc build vừa hỏng.
    """

    activity_kind: str = "unknown"
    phase: str = "unknown"
    event: str = ""
    event_key: str = "none"
    confidence: float = 0.0
    evidence_refs: tuple[str, ...] = ()

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any] | None) -> "ActivitySemantics":
        data = payload or {}
        raw_refs = data.get("evidence_refs", ())
        if isinstance(raw_refs, str):
            refs = (compact(raw_refs, 120),) if raw_refs.strip() else ()
        elif isinstance(raw_refs, Sequence):
            refs = tuple(compact(item, 120) for item in raw_refs[:5] if compact(item, 120))
        else:
            refs = ()
        try:
            confidence = float(data.get("confidence", 0.0) or 0.0)
        except (TypeError, ValueError):
            confidence = 0.0
        return cls(
            activity_kind=semantic_key(data.get("activity_kind", "unknown"), fallback="unknown", limit=64),
            phase=semantic_key(data.get("phase", "unknown"), fallback="unknown", limit=64),
            event=compact(data.get("event", ""), 180),
            event_key=semantic_key(data.get("event_key", "none"), fallback="none", limit=96),
            confidence=max(0.0, min(1.0, confidence)),
            evidence_refs=refs,
        )

    @property
    def has_event(self) -> bool:
        return self.event_key != "none" and bool(self.event)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


__all__ = [
    "ActivitySemantics",
    "DiscourseFrame",
    "TurnSemantics",
    "compact",
    "semantic_key",
    "speech_act_key",
]
