# -*- coding: utf-8 -*-
"""Self-model dẫn xuất từ lịch sử có provenance của Subject Core.

Module này KHÔNG tạo personality và không hỏi LLM "Eris là ai". Nó chỉ dựng một
projection có thể kiểm lại từ những thứ Subject Core đã thật sự tích lũy:
value, competence, continuity, derived need, reflective mismatch và narrative
belief có evidence.

Nếu xóa lịch sử causal tương ứng rồi replay, facet phải biến mất hoặc đổi theo.
Đó là điều phân biệt self-model với một đoạn tiểu sử được tác giả viết sẵn.
"""
from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass
from typing import Any, Iterable, Mapping, Sequence

from .contracts import (
    Belief,
    BeliefSourceKind,
    CompetenceEstimate,
    DerivedNeed,
    DerivedNeedStatus,
    ReflectiveMismatch,
    SelfContinuityRecord,
    Value,
)
from .inner_state import ActionMemory

SELF_MODEL_VERSION = "subject-self-v1"


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def _compact(value: Any, limit: int = 420) -> str:
    text = " ".join(str(value or "").split()).strip()
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + "…"


def _stable_id(prefix: str, *parts: Any) -> str:
    raw = "|".join(_compact(part, 800).casefold() for part in parts if str(part or "").strip())
    digest = hashlib.sha1(raw.encode("utf-8", errors="ignore")).hexdigest()[:16]
    return f"{prefix}.{digest}"


@dataclass(frozen=True, slots=True)
class SelfFacet:
    """Một mảnh self-model có nguồn, không phải chân lý bản thể học.

    ``kind`` mô tả nguồn chức năng. ``statement`` là tóm tắt semantic public-safe,
    còn ``evidence_refs`` cho phép forensic truy ngược tại sao facet tồn tại.
    """

    facet_id: str
    kind: str
    statement: str
    confidence: float
    stability: float
    pressure: float
    evidence_refs: tuple[str, ...]
    source_refs: tuple[str, ...]
    contexts: tuple[str, ...] = ()
    polarity: int = 0

    def __post_init__(self) -> None:
        object.__setattr__(self, "confidence", _clamp01(self.confidence))
        object.__setattr__(self, "stability", _clamp01(self.stability))
        object.__setattr__(self, "pressure", _clamp01(self.pressure))
        object.__setattr__(self, "statement", _compact(self.statement, 520))
        object.__setattr__(self, "evidence_refs", tuple(dict.fromkeys(str(x) for x in self.evidence_refs if str(x).strip()))[:24])
        object.__setattr__(self, "source_refs", tuple(dict.fromkeys(str(x) for x in self.source_refs if str(x).strip()))[:24])
        object.__setattr__(self, "contexts", tuple(dict.fromkeys(str(x) for x in self.contexts if str(x).strip()))[:12])
        object.__setattr__(self, "polarity", -1 if self.polarity < 0 else (1 if self.polarity > 0 else 0))

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class SubjectSelfProjection:
    """Ảnh chụp self-model tại một thời điểm.

    Projection này dẫn xuất được, nên ``revision_key`` là hash của causal refs và
    facet content chứ không phải một counter bí mật có thể trôi khỏi event history.
    """

    subject_id: str
    revision_key: str
    facets: tuple[SelfFacet, ...]
    tensions: tuple[str, ...]
    generated_at: float

    def to_dict(self) -> dict[str, Any]:
        ordered = sorted(
            self.facets,
            key=lambda item: (-(item.pressure + item.confidence + item.stability), item.facet_id),
        )
        return {
            "model_version": SELF_MODEL_VERSION,
            "subject_id": self.subject_id,
            "revision_key": self.revision_key,
            "generated_at": self.generated_at,
            "facets": [item.to_dict() for item in ordered[:24]],
            "tensions": list(self.tensions[:12]),
            "dominant_facets": [
                {
                    "facet_id": item.facet_id,
                    "kind": item.kind,
                    "statement": item.statement,
                    "confidence": round(item.confidence, 4),
                    "stability": round(item.stability, 4),
                    "pressure": round(item.pressure, 4),
                    "evidence_refs": list(item.evidence_refs[:8]),
                }
                for item in ordered[:8]
            ],
        }


class SubjectSelfProjector:
    """Dựng self-model từ state đã có, không phát minh state mới."""

    def project(
        self,
        *,
        subject_id: str,
        values: Sequence[Value],
        derived_needs: Sequence[DerivedNeed],
        continuity_records: Sequence[SelfContinuityRecord],
        mismatches: Sequence[ReflectiveMismatch],
        competence: Sequence[CompetenceEstimate],
        beliefs: Sequence[Belief],
        action_memory: Mapping[str, ActionMemory],
        tendencies: Mapping[str, float],
        generated_at: float,
    ) -> SubjectSelfProjection:
        facets: list[SelfFacet] = []
        tensions: list[str] = []

        for value in values:
            if value.status not in {"candidate", "active"}:
                continue
            confidence = _clamp01(value.endorsement * (0.55 + 0.45 * value.behavioral_support))
            stability = _clamp01((value.stability + value.cross_context_support) / 2.0)
            if confidence < 0.34:
                continue
            facets.append(
                SelfFacet(
                    facet_id=f"self.value.{value.value_id}",
                    kind="value",
                    statement=f"Giá trị đang được hành vi hỗ trợ: {value.statement}",
                    confidence=confidence,
                    stability=stability,
                    pressure=_clamp01(confidence * (0.35 + 0.65 * stability)),
                    evidence_refs=tuple((*value.source_decision_ids, *value.source_episode_ids)),
                    source_refs=(value.value_id,),
                    polarity=value.polarity,
                )
            )

        for need in derived_needs:
            if need.status not in {DerivedNeedStatus.CANDIDATE, DerivedNeedStatus.ACTIVE}:
                continue
            confidence = _clamp01(need.strength * (0.45 + 0.55 * need.cross_context_support))
            facets.append(
                SelfFacet(
                    facet_id=f"self.need.{need.need_id}",
                    kind="derived_need",
                    statement=f"Nhu cầu dẫn xuất đang có bằng chứng: {need.label}",
                    confidence=confidence,
                    stability=_clamp01(need.cross_context_support),
                    pressure=_clamp01(need.strength * (1.0 - 0.35 * need.meta_opposition)),
                    evidence_refs=need.created_from_episode_ids,
                    source_refs=(need.need_id,),
                )
            )

        if continuity_records:
            record = continuity_records[-1]
            facets.append(
                SelfFacet(
                    facet_id=f"self.continuity.{record.record_id}",
                    kind="continuity",
                    statement=record.continuity_claim or "Self-continuity đã được đối chiếu với lịch sử gần nhất.",
                    confidence=_clamp01(record.authorship_consistency),
                    stability=_clamp01(1.0 - record.rupture_score),
                    pressure=_clamp01(record.rupture_score),
                    evidence_refs=record.evidence_refs,
                    source_refs=(record.record_id, *record.linked_action_ids),
                )
            )
            if record.rupture_score >= 0.30:
                tensions.append(f"continuity_rupture:{record.record_id}:{record.rupture_score:.3f}")

        for mismatch in mismatches[-8:]:
            if mismatch.status != "open" or mismatch.prediction_error < 0.20:
                continue
            facets.append(
                SelfFacet(
                    facet_id=f"self.mismatch.{mismatch.mismatch_id}",
                    kind="reflective_mismatch",
                    statement="Cách tự giải thích trước đó đang dự đoán hành vi kém và cần được xem xét lại.",
                    confidence=_clamp01(mismatch.prediction_error),
                    stability=0.25,
                    pressure=_clamp01(mismatch.prediction_error),
                    evidence_refs=tuple((*mismatch.narrative_belief_ids, *mismatch.observed_action_ids)),
                    source_refs=(mismatch.mismatch_id,),
                    contexts=mismatch.contexts,
                )
            )
            tensions.append(f"narrative_mismatch:{mismatch.mismatch_id}:{mismatch.prediction_error:.3f}")

        for estimate in competence:
            if estimate.trial_count < 2:
                continue
            if estimate.posterior_mean >= 0.68:
                statement = f"Năng lực {estimate.skill_id} trong {estimate.context_key} đang có lịch sử thành công tương đối ổn."
                pressure = _clamp01(estimate.calibration_error * 0.45)
                polarity = 1
            elif estimate.posterior_mean <= 0.42 or estimate.calibration_error >= 0.30:
                statement = f"Năng lực {estimate.skill_id} trong {estimate.context_key} còn là giới hạn cần hiệu chỉnh bằng kết quả thật."
                pressure = _clamp01(max(1.0 - estimate.posterior_mean, estimate.calibration_error))
                polarity = -1
            else:
                continue
            facets.append(
                SelfFacet(
                    facet_id=f"self.competence.{estimate.estimate_id}",
                    kind="competence",
                    statement=statement,
                    confidence=_clamp01(1.0 - min(0.8, estimate.posterior_variance * 4.0)),
                    stability=_clamp01(min(1.0, estimate.trial_count / 8.0)),
                    pressure=pressure,
                    evidence_refs=estimate.evidence_refs,
                    source_refs=(estimate.estimate_id, estimate.last_result_ref),
                    contexts=(estimate.context_key,),
                    polarity=polarity,
                )
            )

        for belief in beliefs:
            if belief.status != "active" or belief.subject != "self":
                continue
            if belief.source_kind is not BeliefSourceKind.SELF_INTERPRETATION:
                continue
            if belief.accessibility < 0.45 or belief.confidence < 0.45:
                continue
            explanation = ""
            if isinstance(belief.object_json, Mapping):
                explanation = _compact(belief.object_json.get("explanation", ""), 420)
            if not explanation:
                explanation = _compact(f"{belief.predicate}: {belief.object_json}", 420)
            facets.append(
                SelfFacet(
                    facet_id=f"self.narrative.{belief.belief_id}",
                    kind="narrative",
                    statement=explanation,
                    confidence=belief.confidence,
                    stability=_clamp01((belief.salience + belief.retrieval_salience) / 2.0),
                    pressure=_clamp01(belief.salience * 0.55),
                    evidence_refs=belief.evidence_refs,
                    source_refs=(belief.belief_id,),
                )
            )

        # Action memory chỉ tạo facet khi có đủ trải nghiệm. Nó mô tả pattern hành vi,
        # không gắn nhãn tính cách như "kiên trì" hay "rụt rè" từ trên trời rơi xuống.
        for key, memory in sorted(action_memory.items()):
            if memory.attempts < 4:
                continue
            mean = memory.posterior_mean
            if abs(mean - 0.5) < 0.18:
                continue
            action, _, context = key.partition("|")
            facets.append(
                SelfFacet(
                    facet_id=_stable_id("self.behavior", key),
                    kind="behavior_pattern",
                    statement=f"Lịch sử {action} trong {context or 'general'} có success posterior {mean:.2f} sau {memory.attempts} lần.",
                    confidence=_clamp01(min(1.0, memory.attempts / 10.0)),
                    stability=_clamp01(min(1.0, memory.attempts / 12.0)),
                    pressure=_clamp01(abs(mean - 0.5) * 1.2),
                    evidence_refs=(),
                    source_refs=(key,),
                    contexts=(context or "general",),
                    polarity=1 if mean > 0.5 else -1,
                )
            )

        for key, weight in sorted(tendencies.items(), key=lambda item: -abs(float(item[1]))):
            if abs(float(weight)) < 0.16:
                continue
            facets.append(
                SelfFacet(
                    facet_id=_stable_id("self.tendency", key),
                    kind="structural_tendency",
                    statement=f"Tendency đã consolidate: {key} = {float(weight):+.2f}",
                    confidence=_clamp01(0.50 + 0.50 * abs(float(weight))),
                    stability=_clamp01(abs(float(weight))),
                    pressure=_clamp01(abs(float(weight)) * 0.70),
                    evidence_refs=(),
                    source_refs=(key,),
                    polarity=1 if float(weight) > 0 else -1,
                )
            )
            if len([item for item in facets if item.kind == "structural_tendency"]) >= 6:
                break

        # Dedup theo facet_id để projection deterministic khi nhiều nguồn gọi lại.
        dedup = {item.facet_id: item for item in facets}
        ordered = tuple(sorted(dedup.values(), key=lambda item: (item.kind, item.facet_id)))
        revision_material = "|".join(
            f"{item.facet_id}:{item.confidence:.4f}:{item.stability:.4f}:{item.pressure:.4f}:" + ",".join(item.source_refs)
            for item in ordered
        )
        revision_key = hashlib.sha256(revision_material.encode("utf-8", errors="ignore")).hexdigest()[:20]
        return SubjectSelfProjection(
            subject_id=str(subject_id or "eris"),
            revision_key=revision_key,
            facets=ordered,
            tensions=tuple(dict.fromkeys(tensions)),
            generated_at=float(generated_at),
        )


__all__ = ["SELF_MODEL_VERSION", "SelfFacet", "SubjectSelfProjection", "SubjectSelfProjector"]
