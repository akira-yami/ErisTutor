# -*- coding: utf-8 -*-
"""
relational_feedback.py
======================

Tầng này đọc feedback về *cách ERIS hiện diện* trong hội thoại:
- lạnh quá
- xa cách quá
- vô hồn / robot / máy móc
- nói như assistant/support/corporate
- cứng / khô / không tự nhiên

Nó không mutate relationship state, không tự viết câu trả lời, và không biến mọi
chữ "lạnh" thành drama quan hệ. Nó chỉ trả một signal có score để các tầng sau
SocialProjection / RelationshipEventBuilder / StyleProjection quyết định.

Lý do tách riêng
----------------
AffectSemantics đọc áp lực cảm xúc/ngữ nghĩa chung. Nhưng "lạnh lùng vl luôn"
không chỉ là cảm xúc của user; đó là feedback về tone và khoảng cách của ERIS.
Nếu để nó rơi vào ordinary_dialogue thì ERIS tiếp tục đứng đó như hóa đơn điện tử
biết nói. Không sang lắm.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import re
import unicodedata
from typing import Any, Literal, Mapping


RelationalFeedbackKind = Literal[
    "coldness_feedback",
    "distance_feedback",
    "robotic_feedback",
    "lifeless_feedback",
    "too_formal_feedback",
    "tone_acceptance",
    "tone_rejection",
    "unknown",
]


def clamp01(value: Any, default: float = 0.0) -> float:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return default
    if numeric != numeric:
        return default
    return max(0.0, min(1.0, numeric))


def normalize_vi(text: str) -> str:
    lowered = str(text or "").strip().lower()
    # Cứu một số log/comment bị mojibake do đọc nhầm encoding. Nếu không cần thì bỏ qua.
    try:
        fixed = lowered.encode("latin1").decode("utf-8")
        if fixed:
            lowered = fixed
    except (UnicodeEncodeError, UnicodeDecodeError):
        pass
    lowered = unicodedata.normalize("NFD", lowered)
    lowered = "".join(ch for ch in lowered if unicodedata.category(ch) != "Mn")
    lowered = lowered.replace("đ", "d").replace("Đ", "d")
    lowered = re.sub(r"[^a-z0-9_./#:+\-\s]", " ", lowered)
    return " ".join(lowered.split())


def _contains_phrase(text: str, phrase: str) -> bool:
    return bool(re.search(rf"\b{re.escape(phrase)}\b", text))


def _has_any(text: str, phrases: tuple[str, ...]) -> bool:
    return any(_contains_phrase(text, phrase) or phrase in text for phrase in phrases)


@dataclass(frozen=True, slots=True)
class RelationalFeedbackSignal:
    detected: bool = False
    kind: RelationalFeedbackKind = "unknown"
    intensity: float = 0.0
    repair_needed: float = 0.0
    warmth_request: float = 0.0
    defensive_risk: float = 0.0
    evidence: tuple[str, ...] = field(default_factory=tuple)
    surface_text: str = ""
    previous_assistant_defensive: bool = False

    @classmethod
    def empty(cls) -> "RelationalFeedbackSignal":
        return cls()

    def to_dict(self) -> dict[str, Any]:
        return {
            "detected": bool(self.detected),
            "kind": self.kind,
            "intensity": round(clamp01(self.intensity), 4),
            "repair_needed": round(clamp01(self.repair_needed), 4),
            "warmth_request": round(clamp01(self.warmth_request), 4),
            "defensive_risk": round(clamp01(self.defensive_risk), 4),
            "evidence": list(self.evidence),
            "surface_text": self.surface_text,
            "previous_assistant_defensive": bool(self.previous_assistant_defensive),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any] | None) -> "RelationalFeedbackSignal":
        if not data:
            return cls.empty()
        evidence = data.get("evidence", ())
        if isinstance(evidence, str):
            evidence_tuple = (evidence,)
        else:
            try:
                evidence_tuple = tuple(str(item) for item in evidence if str(item or "").strip())
            except TypeError:
                evidence_tuple = ()
        kind = str(data.get("kind", "unknown") or "unknown")
        if kind not in {
            "coldness_feedback",
            "distance_feedback",
            "robotic_feedback",
            "lifeless_feedback",
            "too_formal_feedback",
            "tone_acceptance",
            "tone_rejection",
            "unknown",
        }:
            kind = "unknown"
        return cls(
            detected=bool(data.get("detected", False)),
            kind=kind,  # type: ignore[arg-type]
            intensity=clamp01(data.get("intensity", 0.0)),
            repair_needed=clamp01(data.get("repair_needed", 0.0)),
            warmth_request=clamp01(data.get("warmth_request", 0.0)),
            defensive_risk=clamp01(data.get("defensive_risk", 0.0)),
            evidence=evidence_tuple,
            surface_text=str(data.get("surface_text", "") or "")[:180],
            previous_assistant_defensive=bool(data.get("previous_assistant_defensive", False)),
        )

    def to_prompt_context(self) -> str:
        if not self.detected:
            return "relational_feedback: none."
        return (
            "relational_feedback: "
            f"kind={self.kind}; intensity={self.intensity:.2f}; repair_needed={self.repair_needed:.2f}; "
            f"warmth_request={self.warmth_request:.2f}; defensive_risk={self.defensive_risk:.2f}; "
            f"previous_defensive={int(self.previous_assistant_defensive)}; "
            f"evidence={','.join(self.evidence) if self.evidence else 'none'}. "
            "rule: acknowledge the tone/distance feedback directly; do not defend the prior wording; "
            "do not ask whether the user wants adjustment; keep ERIS restrained, not sweet or submissive."
        )


def merge_affect_with_relational_feedback(affect: Any, feedback: RelationalFeedbackSignal) -> dict[str, Any]:
    """Tạo dict affect cho StyleProjection mà không cần mutate AffectSemanticSignal.

    StyleProjection nhận dict, nên đây là adapter sạch: giữ score cũ, rồi boost
    perceived_rejection / relational_pressure / attachment vừa đủ để kích
    `relational_repair` mà không bắt user phải "farm attachment". Cuối cùng cũng
    có một chỗ không biến cảm xúc thành thanh EXP, may thay.
    """

    def get(name: str, default: float = 0.0) -> float:
        return clamp01(getattr(affect, name, default))

    evidence = []
    try:
        evidence.extend(str(item) for item in (getattr(affect, "evidence", ()) or ()) if str(item or "").strip())
    except TypeError:
        pass
    if feedback.detected:
        evidence.extend(f"relational_feedback:{item}" for item in feedback.evidence)

    relational_pressure = max(get("relational_pressure"), feedback.repair_needed)
    perceived_rejection = max(get("perceived_rejection"), feedback.intensity)
    # Fe hiện có rule relational_repair cần attachment >= .45. Feedback tone không phải tình cảm,
    # nhưng cần mở khe attachment_visibility vừa đủ để hạ khoảng cách. Đây là bridge, không phải romance.
    attachment = max(get("attachment"), 0.46 if feedback.detected else get("attachment"))
    intimacy = max(get("intimacy"), min(0.42, feedback.warmth_request + 0.10) if feedback.detected else get("intimacy"))
    conflict = max(get("conflict"), min(0.45, feedback.defensive_risk * 0.45) if feedback.detected else get("conflict"))

    return {
        "attachment": attachment,
        "distress": get("distress"),
        "intimacy": intimacy,
        "technicality": get("technicality"),
        "conflict": conflict,
        "ambiguity": get("ambiguity"),
        "playfulness": get("playfulness"),
        "relational_pressure": relational_pressure,
        "perceived_rejection": perceived_rejection,
        "attachment_bid": max(get("attachment_bid"), feedback.warmth_request * 0.35 if feedback.detected else get("attachment_bid")),
        "playful_distress": get("playful_distress"),
        "romantic_boundary_probe": get("romantic_boundary_probe"),
        "evidence": tuple(dict.fromkeys(evidence)),
    }


__all__ = [
    "RelationalFeedbackSignal",
    "merge_affect_with_relational_feedback",
    "normalize_vi",
    "clamp01",
]
