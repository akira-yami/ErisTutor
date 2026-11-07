# -*- coding: utf-8 -*-
"""Memory admission policy V41.

Retrieval chỉ nói "record này có vẻ liên quan". Admission mới trả lời câu khó hơn:
"record này có đủ tư cách bước vào cognition/prompt của lượt hiện tại hay không?"

Tách hai việc này ra để audit/runtime/transient noise không được đi ké chỉ vì cosine
cao. Similarity là tín hiệu tìm kiếm, không phải giấy thông hành.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Mapping


_AUDIT_MARKERS = {
    "audit", "runtime", "trace", "debug", "diagnostic", "telemetry",
    "deliberation_frame", "mind_decision", "executive_commit", "need_activation",
    "raw_observation", "idempotency_receipt",
}
_TRANSIENT_MARKERS = {
    "transient", "thought_stream", "private_thought", "hypothesis", "speculation",
    "behavior_observation", "behavior_aftereffect",
}


def _norm(value: Any) -> str:
    return str(value or "").strip().casefold()


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return _norm(value) in {"1", "true", "yes", "on", "y"}


@dataclass(frozen=True, slots=True)
class MemoryAdmissionDecision:
    admitted: bool
    reason: str
    source_class: str
    contamination_risk: float = 0.0


class MemoryAdmissionPolicy:
    """Cổng deterministic trước khi memory được activation/render.

    Chú thích kiến trúc:
    - Không đánh relevance ở đây, MemoryScorer đã làm việc đó.
    - Không sửa/xóa store ở đây, cleanup/curator sở hữu mutation.
    - Chỉ quyết định record có được phép ảnh hưởng cognition lượt này hay không.
    """

    def classify_record(self, record: Any) -> str:
        metadata = getattr(record, "metadata", {})
        metadata = metadata if isinstance(metadata, Mapping) else {}
        tags = {_norm(x) for x in (getattr(record, "tags", ()) or ())}
        source = _norm(metadata.get("source") or metadata.get("source_class") or "")
        origin = _norm(metadata.get("origin") or metadata.get("record_kind") or "")
        blob = " ".join((source, origin, " ".join(tags)))

        if _truthy(metadata.get("audit_only")) or _truthy(metadata.get("runtime_only")):
            return "audit_runtime"
        if any(marker in blob for marker in _AUDIT_MARKERS):
            return "audit_runtime"
        if _truthy(metadata.get("transient")) or _truthy(metadata.get("prompt_eligible")) is False and "prompt_eligible" in metadata:
            return "transient_cognition"
        if any(marker in blob for marker in _TRANSIENT_MARKERS):
            return "transient_cognition"
        layer = _norm(getattr(record, "layer", ""))
        if layer in {"semantic", "episodic", "relational", "procedural", "repair", "affective_residue"}:
            return f"canonical_{layer}"
        return "unknown"

    def decide(self, record: Any, *, cue: Any = None) -> MemoryAdmissionDecision:
        metadata = getattr(record, "metadata", {})
        metadata = metadata if isinstance(metadata, Mapping) else {}
        source_class = self.classify_record(record)
        evidence_ids = tuple(str(x) for x in (getattr(record, "evidence_ids", ()) or ()) if str(x).strip())
        confidence = float(getattr(record, "confidence", 0.0) or 0.0)
        layer = _norm(getattr(record, "layer", ""))

        if source_class == "audit_runtime":
            return MemoryAdmissionDecision(False, "audit_runtime_never_prompt", source_class, 1.0)
        if source_class == "transient_cognition":
            # Transient cognition chỉ được đi qua khi một tầng khác đã promote rõ ràng.
            promoted = _truthy(metadata.get("promoted_to_memory")) or _norm(metadata.get("epistemic_status")) in {"verified", "confirmed"}
            if not promoted:
                return MemoryAdmissionDecision(False, "transient_not_promoted", source_class, 0.85)

        if _truthy(metadata.get("storable")) is False and "storable" in metadata:
            return MemoryAdmissionDecision(False, "record_marked_not_storable", source_class, 0.90)
        if _truthy(metadata.get("prompt_eligible")) is False and "prompt_eligible" in metadata:
            return MemoryAdmissionDecision(False, "record_marked_prompt_ineligible", source_class, 0.90)

        epistemic = _norm(metadata.get("epistemic_status") or metadata.get("status") or "")
        if epistemic in {"rejected", "invalid", "disputed_unresolved", "unverified_hypothesis"}:
            return MemoryAdmissionDecision(False, f"epistemic_status:{epistemic}", source_class, 0.80)

        # Semantic/episodic được phép surface nên yêu cầu provenance chặt hơn các
        # bias layer. Canonical fact_key do user authority tạo vẫn được giữ.
        fact_key = _norm(metadata.get("fact_key") or "")
        explicit = _norm(metadata.get("explicitness") or "")
        source = _norm(metadata.get("source") or "")
        authority_backed = bool(fact_key) or explicit == "explicit" or source in {"user_authority", "direct_user", "panel_correction"}
        if layer in {"semantic", "episodic"} and not evidence_ids and not authority_backed:
            return MemoryAdmissionDecision(False, "surface_memory_missing_provenance", source_class, 0.72)

        if layer == "semantic" and confidence < 0.52 and not authority_backed:
            return MemoryAdmissionDecision(False, "semantic_confidence_too_low", source_class, 0.64)

        # Memory không trả lời current-world query chỉ vì nó được admit. Scorer và
        # grounding vẫn có policy riêng, nhưng ghi risk ở đây để trace dễ đọc.
        current_evidence = bool(getattr(cue, "needs_current_evidence", False)) if cue is not None else False
        risk = 0.25 if current_evidence and layer in {"semantic", "episodic"} else 0.0
        return MemoryAdmissionDecision(True, "admitted_canonical_memory", source_class, risk)

    def decide_profile_record(self, record: Any) -> MemoryAdmissionDecision:
        """Admission cho Living User Profile trước khi compiler chấm relevance."""
        path = _norm(getattr(record, "path", ""))
        source = _norm(getattr(record, "source", ""))
        explicitness = _norm(getattr(record, "explicitness", ""))
        confidence = float(getattr(record, "confidence", 0.0) or 0.0)
        evidence_ids = tuple(str(x) for x in (getattr(record, "evidence_ids", ()) or ()) if str(x).strip())

        if any(path.startswith(prefix) for prefix in ("runtime.", "audit.", "debug.", "telemetry.")):
            return MemoryAdmissionDecision(False, "profile_path_is_runtime_audit", "audit_runtime", 1.0)
        if any(marker in source for marker in _AUDIT_MARKERS):
            return MemoryAdmissionDecision(False, "profile_source_is_runtime_audit", "audit_runtime", 1.0)

        value_raw = getattr(record, "value", None)
        # Diễn giải ẩn dụ/nhập vai/chỉ thị tạm thuộc AI curator. Admission chỉ
        # kiểm ranh giới cấu trúc và provenance, không đoán meaning bằng từ khóa.
        if value_raw is None or (isinstance(value_raw, str) and not value_raw.strip()):
            return MemoryAdmissionDecision(False, "empty_profile_value", "user_memory", 1.0)
        if not evidence_ids:
            return MemoryAdmissionDecision(False, "profile_missing_evidence", "user_memory", 1.0)
        expires_at = getattr(record, "expires_at", None)
        if expires_at is not None and float(expires_at) <= time.time():
            return MemoryAdmissionDecision(False, "record_expired", "user_memory", 0.95)

        if explicitness == "explicit":
            return MemoryAdmissionDecision(True, "explicit_user_memory", "user_memory", 0.0)
        if not evidence_ids:
            return MemoryAdmissionDecision(False, "profile_inference_missing_evidence", "user_memory", 0.78)
        if explicitness == "observed" and confidence >= 0.62:
            return MemoryAdmissionDecision(True, "observed_with_evidence", "user_memory", 0.12)
        if explicitness == "inferred" and confidence >= 0.55:
            # Inferred có evidence vẫn được compiler thấy để ClarificationPolicy quyết
            # định "clarify"/"omit". Admission không tự phong nó thành fact.
            return MemoryAdmissionDecision(True, "inference_with_evidence_needs_clarification", "user_memory", 0.45)
        return MemoryAdmissionDecision(False, "profile_below_admission_threshold", "user_memory", 0.55)


__all__ = ["MemoryAdmissionDecision", "MemoryAdmissionPolicy"]
