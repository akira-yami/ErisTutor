from __future__ import annotations

"""
semantic_claim.py
=================

Claim chuẩn hóa cho bộ nhớ semantic của ERIS.

Lý do tồn tại:
- Evidence chỉ nói "user vừa nói gì".
- SemanticClaim nói "từ evidence đó rút ra mệnh đề logic nào".
- Memory item mới quyết định claim đó có đáng lưu lâu hay không.

Tức là file này KHÔNG ghi file JSON, KHÔNG recall, KHÔNG tự trả lời user.
Nó chỉ là xương sống dữ liệu để những tầng sau không phải đoán mò bằng token overlap.
Một việc nhỏ, nhưng nó ngăn được cả bi kịch "thích Hà Tĩnh" bị hiểu thành "quê ở Hà Tĩnh".
"""

from dataclasses import dataclass, field
from typing import Any, Literal, Mapping
import hashlib
import time


Assertion = Literal[
    "asserted",       # User khẳng định trực tiếp.
    "denied",         # User phủ định.
    "hypothetical",   # Giả định / nếu như.
    "uncertain",      # Không chắc / có lẽ.
    "quoted",         # Chỉ trích dẫn lời/chuỗi, không claim là đúng.
    "corrected",      # Claim này là bản sửa một claim trước đó.
]

Modality = Literal[
    "fact",           # Fact tự khai báo hoặc fact có evidence đủ mạnh.
    "preference",     # Sở thích / ghét / muốn style.
    "desire",         # Mong muốn chưa chắc là fact ổn định.
    "belief",         # Niềm tin/quan điểm của user.
    "memory",         # Claim về việc nhớ/lịch sử.
    "observation",    # Quan sát hiện tại, thường ngắn hạn.
    "instruction",    # Quy ước / luật xử lý.
    "feedback",       # Feedback về ERIS/response.
]

Temporal = Literal[
    "current",        # Đúng ở hiện tại, dễ hết hạn.
    "session",        # Chỉ trong phiên này.
    "stable",         # Tương đối bền.
    "past",           # Mốc quá khứ.
    "future",         # Mốc tương lai / dự định.
    "habitual",       # Thói quen.
    "timeless",       # Định nghĩa/quy ước không phụ thuộc thời gian.
    "unknown",        # Chưa xác định.
]

Scope = Literal[
    "self",            # Claim về user nếu subject=user.
    "other",           # Claim về người khác.
    "project",         # Claim về project ERIS hoặc project khác.
    "current_session", # Claim chỉ có nghĩa trong phiên hiện tại.
    "world",           # Claim thế giới chung.
    "relationship",    # Claim về quan hệ user-ERIS.
]

SourceKind = Literal[
    "user_text",
    "assistant_text",
    "vision",
    "memory",
    "file",
    "system",
    "inference",
]


@dataclass(frozen=True, slots=True)
class SemanticClaim:
    """Một mệnh đề semantic đã chuẩn hóa.

    Cấu trúc quan trọng nhất là:
        subject + predicate + object + modality + temporal + assertion

    Ví dụ:
        subject="user"
        predicate="preference_location"
        object_text="Hà Tĩnh"
        modality="preference"

    Claim đó KHÔNG entail "user.hometown_is(Hà Tĩnh)".
    Ontology/entailment mới có quyền quyết định chuyện đó.
    """

    claim_id: str

    subject: str
    predicate: str

    object_text: str
    object_value: Any
    object_type: str

    assertion: Assertion
    modality: Modality
    temporal: Temporal
    scope: Scope
    source: SourceKind

    confidence: float

    evidence_text: str
    evidence_ids: tuple[str, ...]

    observed_at: float = 0.0
    valid_from: float | None = None
    valid_until: float | None = None
    stability: Literal["transient", "session", "medium", "stable", "unknown"] = "unknown"

    # tuple bất biến hơn dict trong frozen dataclass. Dict trong frozen vẫn mutate được,
    # loại giả băng đó nhìn thì cứng nhưng chạm vào vẫn chảy.
    qualifiers: tuple[tuple[str, Any], ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        return {
            "claim_id": self.claim_id,
            "subject": self.subject,
            "predicate": self.predicate,
            "object_text": self.object_text,
            "object_value": self.object_value,
            "object_type": self.object_type,
            "assertion": self.assertion,
            "modality": self.modality,
            "temporal": self.temporal,
            "scope": self.scope,
            "source": self.source,
            "confidence": float(self.confidence),
            "evidence_text": self.evidence_text,
            "evidence_ids": list(self.evidence_ids),
            "observed_at": float(self.observed_at),
            "valid_from": self.valid_from,
            "valid_until": self.valid_until,
            "stability": self.stability,
            "qualifiers": {str(k): v for k, v in self.qualifiers},
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "SemanticClaim":
        qualifiers_raw = data.get("qualifiers") or {}
        if isinstance(qualifiers_raw, Mapping):
            qualifiers = tuple((str(k), v) for k, v in qualifiers_raw.items())
        else:
            qualifiers = tuple(tuple(x) for x in qualifiers_raw if isinstance(x, (list, tuple)) and len(x) == 2)
        return cls(
            claim_id=str(data.get("claim_id") or make_claim_id(data)),
            subject=str(data.get("subject") or "unknown"),
            predicate=str(data.get("predicate") or "unknown"),
            object_text=str(data.get("object_text") or ""),
            object_value=data.get("object_value"),
            object_type=str(data.get("object_type") or "unknown"),
            assertion=_coerce_literal(str(data.get("assertion") or "asserted"), {"asserted", "denied", "hypothetical", "uncertain", "quoted", "corrected"}, "asserted"),  # type: ignore[arg-type]
            modality=_coerce_literal(str(data.get("modality") or "fact"), {"fact", "preference", "desire", "belief", "memory", "observation", "instruction", "feedback"}, "fact"),  # type: ignore[arg-type]
            temporal=_coerce_literal(str(data.get("temporal") or "unknown"), {"current", "session", "stable", "past", "future", "habitual", "timeless", "unknown"}, "unknown"),  # type: ignore[arg-type]
            scope=_coerce_literal(str(data.get("scope") or "self"), {"self", "other", "project", "current_session", "world", "relationship"}, "self"),  # type: ignore[arg-type]
            source=_coerce_literal(str(data.get("source") or "user_text"), {"user_text", "assistant_text", "vision", "memory", "file", "system", "inference"}, "user_text"),  # type: ignore[arg-type]
            confidence=_clamp01(data.get("confidence", 0.0)),
            evidence_text=str(data.get("evidence_text") or ""),
            evidence_ids=tuple(str(x) for x in data.get("evidence_ids") or () if str(x).strip()),
            observed_at=float(data.get("observed_at") or 0.0),
            valid_from=data.get("valid_from"),
            valid_until=data.get("valid_until"),
            stability=_coerce_literal(str(data.get("stability") or "unknown"), {"transient", "session", "medium", "stable", "unknown"}, "unknown"),  # type: ignore[arg-type]
            qualifiers=qualifiers,
        )

    def qualifier_dict(self) -> dict[str, Any]:
        return {str(k): v for k, v in self.qualifiers}

    def with_confidence(self, confidence: float) -> "SemanticClaim":
        """Trả bản sao với confidence mới.

        Dataclass frozen nên không mutate trực tiếp. Ký ức mà tự sửa mình âm thầm
        thì debug sẽ vui như mò kim trong đống traceback.
        """

        data = self.to_dict()
        data["confidence"] = _clamp01(confidence)
        return SemanticClaim.from_dict(data)


@dataclass(frozen=True, slots=True)
class ExtractedClaim:
    """Kết quả extractor trả ra trước khi commit.

    `commit_hint` chỉ là gợi ý. Worthiness/Retention policy mới là nơi quyết định thật.
    """

    claim: SemanticClaim
    extraction_confidence: float
    extraction_method: str = "deterministic_rule"
    commit_hint: str = "unknown"
    warnings: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "claim": self.claim.to_dict(),
            "extraction_confidence": float(self.extraction_confidence),
            "extraction_method": self.extraction_method,
            "commit_hint": self.commit_hint,
            "warnings": list(self.warnings),
        }


def make_claim_id(seed: Any) -> str:
    """Sinh id ổn định theo nội dung claim.

    Id ổn định giúp test và merge dễ chịu hơn một chút. Không dùng làm bảo mật.
    """

    text = repr(seed).encode("utf-8", errors="ignore")
    return "claim_" + hashlib.sha256(text).hexdigest()[:16]


def build_claim(
    *,
    subject: str,
    predicate: str,
    object_text: str,
    object_value: Any = None,
    object_type: str = "unknown",
    assertion: Assertion = "asserted",
    modality: Modality = "fact",
    temporal: Temporal = "unknown",
    scope: Scope = "self",
    source: SourceKind = "user_text",
    confidence: float = 0.0,
    evidence_text: str = "",
    evidence_ids: tuple[str, ...] = (),
    observed_at: float | None = None,
    valid_from: float | None = None,
    valid_until: float | None = None,
    stability: Literal["transient", "session", "medium", "stable", "unknown"] = "unknown",
    qualifiers: Mapping[str, Any] | None = None,
) -> SemanticClaim:
    payload = {
        "subject": subject,
        "predicate": predicate,
        "object_text": object_text,
        "object_value": object_value if object_value is not None else object_text,
        "object_type": object_type,
        "assertion": assertion,
        "modality": modality,
        "temporal": temporal,
        "scope": scope,
        "source": source,
        "confidence": _clamp01(confidence),
        "evidence_text": evidence_text,
        "evidence_ids": tuple(evidence_ids),
        "observed_at": float(observed_at if observed_at is not None else time.time()),
        "valid_from": valid_from,
        "valid_until": valid_until,
        "stability": stability,
        "qualifiers": tuple((str(k), v) for k, v in dict(qualifiers or {}).items()),
    }
    return SemanticClaim(claim_id=make_claim_id(payload), **payload)


def _clamp01(value: Any) -> float:
    try:
        number = float(value)
    except Exception:
        number = 0.0
    if number != number or number in (float("inf"), float("-inf")):
        number = 0.0
    return max(0.0, min(1.0, number))


def _coerce_literal(value: str, allowed: set[str], default: str) -> str:
    return value if value in allowed else default


__all__ = [
    "Assertion",
    "Modality",
    "Temporal",
    "Scope",
    "SourceKind",
    "SemanticClaim",
    "ExtractedClaim",
    "build_claim",
    "make_claim_id",
]
