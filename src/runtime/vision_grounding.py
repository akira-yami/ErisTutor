# -*- coding: utf-8 -*-
"""Grounded-claim + abstain-first policy cho nhánh vision của ERIS.

Module này nằm giữa ``VisionSemanticCompressor`` và phần tạo câu nói.
Vision model chỉ đưa ra một ``SemanticSceneNote`` có thể đúng, có thể mơ hồ.
Lớp này biến note đó thành các claim nhỏ, gắn bằng chứng/confidence cho từng
claim rồi mới quyết định:

- ``speak``: có claim đủ chắc để dùng;
- ``soften``: user hỏi trực tiếp nhưng bằng chứng chỉ đủ để nói dè dặt;
- ``silence``: lane ambient không đủ chắc, tốt nhất đừng dựng cảnh.

Nó cố ý không tự viết câu public. Con người đã có đủ module thích nói hộ nhau
rồi; vision chỉ nên giao bằng chứng và quyền nói cho policy downstream.
"""
from __future__ import annotations

import os
import re
import time
import unicodedata
from dataclasses import asdict, dataclass, field
from typing import Any, Iterable, Literal, Mapping, Sequence


VisionRecommendation = Literal["speak", "soften", "silence"]
VisionClaimKind = Literal[
    "scene",
    "visible_text",
    "affect",
    "delta",
    "relation",
]


def _clamp01(value: Any, default: float = 0.0) -> float:
    try:
        return max(0.0, min(1.0, float(value)))
    except Exception:
        return max(0.0, min(1.0, float(default)))


def _compact(value: Any, limit: int = 240) -> str:
    text = " ".join(str(value or "").replace("\x00", " ").split()).strip()
    if len(text) <= limit:
        return text
    return text[: max(1, limit - 3)].rstrip() + "..."


def _fold(value: Any) -> str:
    text = unicodedata.normalize("NFD", str(value or "").lower())
    text = "".join(ch for ch in text if unicodedata.category(ch) != "Mn")
    return " ".join(text.replace("đ", "d").split())


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    return _clamp01(raw, default)


def _unique(items: Iterable[str]) -> tuple[str, ...]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        text = str(item or "").strip()
        key = _fold(text)
        if not text or key in seen:
            continue
        seen.add(key)
        out.append(text)
    return tuple(out)


_GENERIC_OR_EMPTY_MARKERS = (
    "visible screen context",
    "screen context",
    "active app | active window",
    "focus=unknown",
    "intent=unknown",
    "there are several windows",
    "co vai cua so",
    "man hinh sang deu",
    "khong co thong bao",
    "nothing notable",
    "no notable content",
)

# Các câu này là suy diễn về người dùng, không phải tri giác màn hình.
_USER_MIND_MARKERS = (
    "nguoi dung dang buon",
    "nguoi dung dang vui",
    "nguoi dung dang chan",
    "nguoi dung dang tuc gian",
    "nguoi dung dang met",
    "user is sad",
    "user is happy",
    "user is bored",
    "user is angry",
    "user is tired",
    "ban dang buon",
    "ban dang vui",
    "ban dang chan",
    "ban dang met",
)

_ERROR_NEGATION_MARKERS = (
    "khong co loi",
    "khong thay loi",
    "no error",
    "no errors",
    "nothing failed",
    "khong co thong bao",
)

_DIRECT_EVIDENCE_PREFIXES = (
    "vision_model:",
    "vision_caption:",
    "screen_hash:",
    "ocr:",
    "ocr_text:",
    "screen_capture:",
)


def _mapping_get(obj: Any, key: str, default: Any = None) -> Any:
    if isinstance(obj, Mapping):
        return obj.get(key, default)
    return getattr(obj, key, default)


@dataclass(slots=True, frozen=True)
class GroundedClaim:
    """Một mệnh đề vision nhỏ có provenance riêng.

    ``grounded=False`` không có nghĩa claim chắc chắn sai. Nó chỉ có nghĩa runtime
    không được dùng nó như fact hiện tại. Đây là khác biệt nhỏ nhưng cứu được khá
    nhiều câu "tôi thấy..." đầy tự tin đến từ một caption đang ngủ gật.
    """

    claim_id: str
    text: str
    kind: VisionClaimKind
    evidence_ids: tuple[str, ...] = ()
    confidence: float = 0.0
    contradictions: tuple[str, ...] = ()
    grounded: bool = False
    source: str = "vision"
    stale: bool = False
    inference_only: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_evidence_item(self) -> dict[str, Any]:
        """Dạng mà ``SourceGroundingEngine`` đọc được."""

        tags = ["vision", "screen", f"vision_claim:{self.kind}"]
        tags.append("grounded" if self.grounded else "ungrounded")
        if self.stale:
            tags.append("stale")
        if self.inference_only:
            tags.append("inference")
        return {
            "id": self.claim_id,
            "evidence_id": self.claim_id,
            "source": "vision_grounded_claim",
            "claim": self.text,
            "confidence": self.confidence,
            "grounded": self.grounded,
            "stale": self.stale,
            "tags": tags,
            "contradictions": list(self.contradictions),
        }


@dataclass(slots=True)
class VisionGroundingReport:
    claims: tuple[GroundedClaim, ...] = ()
    evidence_strength: float = 0.0
    contradictions: tuple[str, ...] = ()
    unknowns: tuple[str, ...] = ()
    recommendation: VisionRecommendation = "silence"
    reason: str = "insufficient_grounding"
    requested: bool = False
    stale: bool = False
    source: str = "unknown"
    observed_at: float = field(default_factory=time.time)
    debug: dict[str, Any] = field(default_factory=dict)
    # Đặt True khi user có vẻ trỏ vào một đối tượng cụ thể ("bài đó", "cái này")
    # nhưng grounding không thể xác nhận đối tượng đó là gì. Downstream có thể
    # dùng cờ này để Eris hỏi lại thay vì im lặng hoàn toàn.
    anaphoric_reference_unresolved: bool = False

    @property
    def grounded_claims(self) -> tuple[GroundedClaim, ...]:
        return tuple(claim for claim in self.claims if claim.grounded and not claim.stale)

    @property
    def weak_claims(self) -> tuple[GroundedClaim, ...]:
        return tuple(claim for claim in self.claims if not claim.grounded)

    @property
    def should_speak(self) -> bool:
        return self.recommendation == "speak" and bool(self.grounded_claims)

    def to_screen_facts(self, *, max_items: int = 3) -> tuple[str, ...]:
        """Chỉ surface claim quan sát trực tiếp; bỏ relation/inference mềm."""

        selected: list[str] = []
        for claim in self.grounded_claims:
            if claim.inference_only or claim.kind == "relation":
                continue
            text = _compact(claim.text, 220)
            if text and _fold(text) not in {_fold(item) for item in selected}:
                selected.append(text)
            if len(selected) >= max(1, int(max_items)):
                break
        return tuple(selected)

    def to_evidence_items(self, *, grounded_only: bool = True) -> tuple[dict[str, Any], ...]:
        claims = self.grounded_claims if grounded_only else self.claims
        return tuple(claim.to_evidence_item() for claim in claims)

    def to_dict(self) -> dict[str, Any]:
        return {
            "claims": [claim.to_dict() for claim in self.claims],
            "evidence_strength": self.evidence_strength,
            "contradictions": list(self.contradictions),
            "unknowns": list(self.unknowns),
            "recommendation": self.recommendation,
            "reason": self.reason,
            "requested": self.requested,
            "stale": self.stale,
            "source": self.source,
            "observed_at": self.observed_at,
            "debug": dict(self.debug),
            "anaphoric_reference_unresolved": self.anaphoric_reference_unresolved,
        }


class VisionGroundingPolicy:
    """Biến ``SemanticSceneNote`` thành claim đã kiểm và quyết định abstain-first."""

    module_name = "vision_grounding_policy"

    def __init__(
        self,
        *,
        min_claim_confidence: float | None = None,
        min_text_confidence: float | None = None,
        min_soften_confidence: float | None = None,
    ) -> None:
        self.min_claim_confidence = _clamp01(
            min_claim_confidence
            if min_claim_confidence is not None
            else _env_float("ERIS_VISION_CLAIM_MIN_CONFIDENCE", 0.72),
            0.72,
        )
        self.min_text_confidence = _clamp01(
            min_text_confidence
            if min_text_confidence is not None
            else _env_float("ERIS_VISION_TEXT_MIN_CONFIDENCE", 0.76),
            0.76,
        )
        self.min_soften_confidence = _clamp01(
            min_soften_confidence
            if min_soften_confidence is not None
            else _env_float("ERIS_VISION_SOFTEN_MIN_CONFIDENCE", 0.42),
            0.42,
        )

    def empty_report(
        self,
        *,
        requested: bool = False,
        stale: bool = False,
        reason: str = "no_scene_note",
        source: str = "unknown",
        unknowns: Sequence[str] = (),
        contradictions: Sequence[str] = (),
        anaphoric_reference_unresolved: bool = False,
    ) -> VisionGroundingReport:
        return VisionGroundingReport(
            claims=(),
            evidence_strength=0.0,
            contradictions=_unique(contradictions),
            unknowns=_unique(unknowns or (reason,)),
            recommendation="soften" if requested else "silence",
            reason=reason,
            requested=requested,
            stale=stale,
            source=source,
            anaphoric_reference_unresolved=anaphoric_reference_unresolved,
        )

    def evaluate(
        self,
        note: Any,
        *,
        requested: bool = False,
        stale: bool = False,
        observed_at: float | None = None,
    ) -> VisionGroundingReport:
        if note is None:
            return self.empty_report(requested=requested, stale=stale)

        source = str(_mapping_get(note, "source", "unknown") or "unknown")
        note_confidence = self._effective_note_confidence(note, source=source)
        evidence_ids = self._evidence_ids(note)
        has_direct_evidence = self._has_direct_evidence(evidence_ids, source=source)
        global_contradictions = list(self._global_contradictions(note, has_direct_evidence=has_direct_evidence))
        unknowns: list[str] = []

        if stale:
            unknowns.append("scene_is_stale")
        if not has_direct_evidence:
            unknowns.append("missing_direct_sensor_evidence")
        if note_confidence < self.min_claim_confidence:
            unknowns.append("scene_confidence_below_grounded_threshold")

        candidates: list[GroundedClaim] = []
        seen: set[str] = set()

        def add_claim(
            text: Any,
            *,
            kind: VisionClaimKind,
            confidence: float,
            inference_only: bool = False,
            extra_contradictions: Sequence[str] = (),
        ) -> None:
            cleaned = _compact(text, 260)
            key = _fold(cleaned)
            if not cleaned or key in seen:
                return
            seen.add(key)
            # Contradiction cấp scene (ví dụ observer báo error mạnh nhưng caption
            # lại khẳng định "không có lỗi") phải chặn mọi claim phụ thuộc scene đó.
            local_contradictions = list(global_contradictions)
            local_contradictions.extend(extra_contradictions)
            local_contradictions.extend(self._text_contradictions(cleaned, kind=kind))
            threshold = self.min_text_confidence if kind == "visible_text" else self.min_claim_confidence
            grounded = bool(
                not stale
                and has_direct_evidence
                and not local_contradictions
                and not inference_only
                and _clamp01(confidence) >= threshold
            )
            candidates.append(
                GroundedClaim(
                    claim_id=f"vision:{kind}:{len(candidates)}",
                    text=cleaned,
                    kind=kind,
                    evidence_ids=evidence_ids,
                    confidence=_clamp01(confidence),
                    contradictions=_unique(local_contradictions),
                    grounded=grounded,
                    source=source,
                    stale=stale,
                    inference_only=inference_only,
                )
            )

        scene_text = _mapping_get(note, "content_summary", "") or _mapping_get(note, "visible_context", "")
        add_claim(scene_text, kind="scene", confidence=note_confidence)

        important_text = _mapping_get(note, "important_text", ()) or ()
        if isinstance(important_text, str):
            important_text = (important_text,)
        for text in list(important_text)[:8]:
            # OCR/text copy dễ sai ký tự hơn caption tổng thể, nên có threshold riêng.
            add_claim(text, kind="visible_text", confidence=min(0.94, note_confidence + 0.04))

        affect = _mapping_get(note, "content_affect", "")
        if affect:
            # Affect của nội dung là inference hẹp. Cho lưu trong report để cognition
            # tham khảo, nhưng không biến nó thành screen fact trực tiếp.
            add_claim(affect, kind="affect", confidence=note_confidence - 0.10, inference_only=True)

        delta = _mapping_get(note, "delta", "")
        prev_scene_id = str(_mapping_get(note, "prev_scene_id", "") or "")
        scene_id = str(_mapping_get(note, "scene_id", "") or "")
        delta_contradictions: list[str] = []
        if delta and (not prev_scene_id or not scene_id or prev_scene_id == scene_id):
            delta_contradictions.append("delta_without_distinct_scene_ids")
        add_claim(
            delta,
            kind="delta",
            confidence=note_confidence - 0.08,
            extra_contradictions=delta_contradictions,
        )

        relation = _mapping_get(note, "relation_to_user", "")
        if relation:
            # Relation-to-user không phải fact nhìn thấy. Để inference_only nhằm ngăn
            # model nói chắc "bạn đang..." chỉ vì một cửa sổ nào đó đang mở.
            add_claim(relation, kind="relation", confidence=note_confidence - 0.16, inference_only=True)

        grounded = [claim for claim in candidates if claim.grounded]
        strongest = max((claim.confidence for claim in candidates), default=0.0)
        grounded_strength = max((claim.confidence for claim in grounded), default=0.0)
        evidence_strength = _clamp01(
            grounded_strength
            if grounded
            else strongest * (0.72 if has_direct_evidence else 0.38)
        )

        if grounded:
            salience = _clamp01(_mapping_get(note, "salience", 0.0))
            if requested or salience >= 0.55:
                recommendation: VisionRecommendation = "speak"
                reason = "grounded_claim_available"
            else:
                recommendation = "silence"
                reason = "grounded_but_not_salient"
        elif requested and strongest >= self.min_soften_confidence and has_direct_evidence:
            recommendation = "soften"
            reason = "weak_evidence_requested"
        elif requested:
            recommendation = "soften"
            reason = "insufficient_grounding_requested"
        else:
            recommendation = "silence"
            reason = "insufficient_grounding_ambient"

        all_contradictions = _unique(
            [*global_contradictions, *(item for claim in candidates for item in claim.contradictions)]
        )
        if all_contradictions and not grounded:
            reason = "contradicted_or_ungrounded"

        if not candidates:
            unknowns.append("no_claim_candidate")
        if not grounded:
            unknowns.append("no_grounded_claim")

        return VisionGroundingReport(
            claims=tuple(candidates),
            evidence_strength=evidence_strength,
            contradictions=all_contradictions,
            unknowns=_unique(unknowns),
            recommendation=recommendation,
            reason=reason,
            requested=requested,
            stale=stale,
            source=source,
            observed_at=float(observed_at if observed_at is not None else time.time()),
            # Đánh dấu khi user có vẻ hỏi về một đối tượng cụ thể (requested=True)
            # nhưng không có claim nào được grounded — tức là màn hình không đủ
            # bằng chứng để Eris trả lời câu trỏ chỉ của user.
            anaphoric_reference_unresolved=(
                requested
                and not grounded
                and "no_grounded_claim" in unknowns
            ),
            debug={
                "note_confidence": round(note_confidence, 4),
                "has_direct_evidence": has_direct_evidence,
                "min_claim_confidence": self.min_claim_confidence,
                "min_text_confidence": self.min_text_confidence,
                "candidate_count": len(candidates),
                "grounded_count": len(grounded),
            },
        )

    def _effective_note_confidence(self, note: Any, *, source: str) -> float:
        confidence = _clamp01(_mapping_get(note, "confidence", 0.0))
        folded_source = _fold(source)
        if "ocr_only" in folded_source:
            confidence = min(confidence, 0.58)
        elif "caption_only" in folded_source:
            confidence = min(confidence, 0.66)
        elif source in {"unknown", ""}:
            confidence = min(confidence, 0.52)
        return confidence

    @staticmethod
    def _evidence_ids(note: Any) -> tuple[str, ...]:
        raw = _mapping_get(note, "evidence", ()) or ()
        if isinstance(raw, str):
            raw = (raw,)
        result: list[str] = []
        for item in raw:
            if isinstance(item, Mapping):
                value = item.get("id") or item.get("evidence_id") or item.get("source") or item.get("claim")
            else:
                value = item
            text = _compact(value, 180)
            if text:
                result.append(text)
        return _unique(result)

    @staticmethod
    def _has_direct_evidence(evidence_ids: Sequence[str], *, source: str) -> bool:
        # ``source=qwen4b_vision`` chỉ là nhãn backend, không chứng minh model đã
        # nhận được ảnh ở lượt này. Phải có provenance trực tiếp do compressor ghi.
        del source
        for item in evidence_ids:
            folded = _fold(item)
            if any(folded.startswith(_fold(prefix)) for prefix in _DIRECT_EVIDENCE_PREFIXES):
                return True
        return False

    def _global_contradictions(self, note: Any, *, has_direct_evidence: bool) -> tuple[str, ...]:
        result: list[str] = []
        scene = _fold(
            f"{_mapping_get(note, 'content_summary', '')} "
            f"{_mapping_get(note, 'visible_context', '')} "
            f"{' '.join(str(x) for x in (_mapping_get(note, 'important_text', ()) or ()))}"
        )
        possible_error = _clamp01(_mapping_get(note, "possible_error", 0.0))
        if possible_error >= 0.70 and any(marker in scene for marker in _ERROR_NEGATION_MARKERS):
            result.append("error_signal_conflicts_with_no_error_claim")
        if not has_direct_evidence:
            result.append("no_direct_sensor_evidence")
        return _unique(result)

    @staticmethod
    def _text_contradictions(text: str, *, kind: VisionClaimKind) -> tuple[str, ...]:
        folded = _fold(text)
        result: list[str] = []
        if not folded:
            result.append("empty_claim")
        if any(marker in folded for marker in _GENERIC_OR_EMPTY_MARKERS):
            result.append("generic_or_content_free_claim")
        if any(marker in folded for marker in _USER_MIND_MARKERS):
            result.append("user_hidden_state_inference")
        if kind == "visible_text" and len(re.sub(r"\W+", "", folded)) < 3:
            result.append("visible_text_too_short")
        return _unique(result)


__all__ = [
    "GroundedClaim",
    "VisionGroundingPolicy",
    "VisionGroundingReport",
    "VisionRecommendation",
    "VisionClaimKind",
]
