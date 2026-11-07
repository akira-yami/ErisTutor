from __future__ import annotations

"""
memory.understanding.meaning_frame
==================================

Contract trung gian giữa câu user và SemanticClaim.

Ý tưởng chính:
- Không extract thẳng `text -> predicate` nữa.
- Trước hết đọc câu thành `MeaningFrame`: user đang tự khai báo fact, nêu sở thích,
  sửa lỗi, đưa quy ước, hay chỉ phản hồi xã giao?
- `ClaimBuilder` mới biến frame đã được kiểm tra thành SemanticClaim.

File này chỉ chứa dữ liệu. Nó không gọi model, không ghi memory, không quyết định
lưu lâu. Tách như vậy để tránh kiểu một file vừa đọc chữ, vừa làm ontology, vừa
làm quan tòa memory. Nhân loại gọi đó là "tiện"; debugger gọi đó là địa ngục.
"""

from dataclasses import dataclass, field
from typing import Any, Literal, Mapping


FrameType = Literal[
    "identity",            # tên, tuổi, năm sinh...
    "location",            # quê, nơi ở, vị trí hiện tại...
    "preference",          # thích/ghét/muốn kiểu gì...
    "instruction",         # từ giờ nếu X thì hiểu là Y...
    "feedback",            # feedback về ERIS/câu trả lời...
    "project",             # claim liên quan project/code...
    "memory_request",      # user yêu cầu nhớ nhưng chưa rõ claim sạch
    "session_event",       # greeting/question/turn event, không long-term semantic
    "unknown",
]

Polarity = Literal["asserted", "denied", "uncertain", "hypothetical", "quoted", "corrected"]
TimeScope = Literal["current", "session", "stable", "past", "future", "habitual", "timeless", "unknown"]


@dataclass(frozen=True, slots=True)
class EntityRef:
    """Entity đã link nhẹ từ surface text.

    `kind` là nhãn loại entity: location/person_name/style_rule/topic/...
    `canonical` là dạng ổn định để merge/search. Không được dùng canonical bỏ dấu
    làm surface public nếu user cần tên riêng có dấu.
    """

    text: str
    kind: str = "unknown"
    canonical: str = ""
    confidence: float = 0.0
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "kind": self.kind,
            "canonical": self.canonical,
            "confidence": float(self.confidence),
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "EntityRef":
        return cls(
            text=str(data.get("text") or ""),
            kind=str(data.get("kind") or "unknown"),
            canonical=str(data.get("canonical") or ""),
            confidence=float(data.get("confidence") or 0.0),
            metadata=dict(data.get("metadata") or {}),
        )


@dataclass(frozen=True, slots=True)
class MeaningFrame:
    """Khung nghĩa đã đọc từ một phát ngôn.

    Field quan trọng:
    - frame_type: loại hành vi/nghĩa ở mức cao.
    - relation: quan hệ ngữ nghĩa, chưa phải predicate memory cuối.
    - target: entity/value đang được nói tới.
    - polarity/time_scope/certainty: dùng để chống lưu sai loại claim.

    Ví dụ:
        "tôi chỉ thích Hà Tĩnh, không sống ở đó"
        -> Preference frame: relation=likes, target=Hà Tĩnh, polarity=asserted
        -> Location frame: relation=living_location, target=đó/Hà Tĩnh, polarity=denied
    """

    frame_type: FrameType
    relation: str
    subject: str = "user"
    target: EntityRef | None = None
    polarity: Polarity = "asserted"
    modality: str = "fact"
    time_scope: TimeScope = "unknown"
    scope: str = "self"
    certainty: float = 0.0
    stability: str = "unknown"
    evidence_span: str = ""
    source: str = "user_text"
    slots: Mapping[str, Any] = field(default_factory=dict)
    warnings: tuple[str, ...] = field(default_factory=tuple)

    @property
    def is_assertive(self) -> bool:
        return self.polarity not in {"hypothetical", "quoted"}

    def to_dict(self) -> dict[str, Any]:
        return {
            "frame_type": self.frame_type,
            "relation": self.relation,
            "subject": self.subject,
            "target": self.target.to_dict() if self.target else None,
            "polarity": self.polarity,
            "modality": self.modality,
            "time_scope": self.time_scope,
            "scope": self.scope,
            "certainty": float(self.certainty),
            "stability": self.stability,
            "evidence_span": self.evidence_span,
            "source": self.source,
            "slots": dict(self.slots),
            "warnings": list(self.warnings),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "MeaningFrame":
        target_raw = data.get("target")
        target = EntityRef.from_dict(target_raw) if isinstance(target_raw, Mapping) else None
        return cls(
            frame_type=_coerce(str(data.get("frame_type") or "unknown"), _FRAME_TYPES, "unknown"),  # type: ignore[arg-type]
            relation=str(data.get("relation") or "unknown"),
            subject=str(data.get("subject") or "user"),
            target=target,
            polarity=_coerce(str(data.get("polarity") or "asserted"), _POLARITIES, "asserted"),  # type: ignore[arg-type]
            modality=str(data.get("modality") or "fact"),
            time_scope=_coerce(str(data.get("time_scope") or data.get("temporal") or "unknown"), _TIME_SCOPES, "unknown"),  # type: ignore[arg-type]
            scope=str(data.get("scope") or "self"),
            certainty=_clamp01(data.get("certainty", data.get("confidence", 0.0))),
            stability=str(data.get("stability") or "unknown"),
            evidence_span=str(data.get("evidence_span") or data.get("evidence_text") or ""),
            source=str(data.get("source") or "user_text"),
            slots=dict(data.get("slots") or {}),
            warnings=tuple(str(x) for x in data.get("warnings") or () if str(x).strip()),
        )


@dataclass(frozen=True, slots=True)
class MeaningParseResult:
    """Output của SemanticParser.

    Parser có thể là model.gguf, parser rule-based, hoặc hybrid. Nhưng dù đến từ
    đâu, downstream chỉ nhận dạng này. Model không được phép commit memory trực tiếp.
    """

    frames: tuple[MeaningFrame, ...] = ()
    source: str = "unknown"
    confidence: float = 0.0
    diagnostics: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "frames": [frame.to_dict() for frame in self.frames],
            "source": self.source,
            "confidence": float(self.confidence),
            "diagnostics": list(self.diagnostics),
        }


_FRAME_TYPES = {"identity", "location", "preference", "instruction", "feedback", "project", "memory_request", "session_event", "unknown"}
_POLARITIES = {"asserted", "denied", "uncertain", "hypothetical", "quoted", "corrected"}
_TIME_SCOPES = {"current", "session", "stable", "past", "future", "habitual", "timeless", "unknown"}


def _coerce(value: str, allowed: set[str], default: str) -> str:
    return value if value in allowed else default


def _clamp01(value: Any) -> float:
    try:
        number = float(value)
    except Exception:
        number = 0.0
    if number != number or number in (float("inf"), float("-inf")):
        number = 0.0
    return max(0.0, min(1.0, number))


__all__ = ["EntityRef", "MeaningFrame", "MeaningParseResult", "FrameType", "Polarity", "TimeScope"]
