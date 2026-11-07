from __future__ import annotations

"""
memory_scoring.py
=================

Unified MemoryScorer.

Nguyên tắc:
- Relevance và prior tách riêng.
- Prior không được cứu ký ức không liên quan.
- Repair/procedural/relational/affective/semantic/episodic có policy riêng.
- Query rỗng chỉ tạo background bias yếu, không tạo active resonance giả.

Đây là chỗ ERIS "biết hậu quả":
ký ức từng gây repair, residue, trust, rupture... tạo áp lực.
Nhưng nó không phải cấm đoán. Nó chỉ là causal memory.
"""

from dataclasses import dataclass, field
from typing import Any

try:
    from .memory_record import clamp01, compact_text
    from .memory_cue import MemoryCue
    from .memory_candidate import MemoryCandidate
except Exception:  # pragma: no cover
    from memory_record import clamp01, compact_text
    from memory_cue import MemoryCue
    from memory_candidate import MemoryCandidate


@dataclass(frozen=True, slots=True)
class LayerProfile:
    layer: str
    weight: float
    min_relevance: float
    bias_routes: tuple[str, ...]
    default_surface_policy: str
    can_surface: bool = False
    background_allowed: bool = False


DEFAULT_LAYER_PROFILES: dict[str, LayerProfile] = {
    "repair": LayerProfile(
        layer="repair",
        weight=1.12,
        min_relevance=0.05,
        bias_routes=("wording", "attention"),
        default_surface_policy="surface_if_needed_for_fix",
        can_surface=True,
        background_allowed=False,
    ),
    "procedural": LayerProfile(
        layer="procedural",
        weight=1.02,
        min_relevance=0.07,
        bias_routes=("wording",),
        default_surface_policy="silent_bias",
        can_surface=False,
        background_allowed=True,
    ),
    "relational": LayerProfile(
        layer="relational",
        weight=0.92,
        min_relevance=0.12,
        bias_routes=("attention", "appraisal", "silence"),
        default_surface_policy="silent_bias",
        can_surface=False,
        background_allowed=True,
    ),
    "affective_residue": LayerProfile(
        layer="affective_residue",
        weight=0.86,
        min_relevance=0.16,
        bias_routes=("attention", "appraisal", "silence"),
        default_surface_policy="never_direct_claim",
        can_surface=False,
        background_allowed=True,
    ),
    "semantic": LayerProfile(
        layer="semantic",
        weight=0.90,
        min_relevance=0.09,
        bias_routes=("attention", "wording"),
        default_surface_policy="surface_if_contextual",
        can_surface=True,
        background_allowed=False,
    ),
    "episodic": LayerProfile(
        layer="episodic",
        weight=0.74,
        min_relevance=0.18,
        bias_routes=("context",),
        default_surface_policy="surface_if_user_asks",
        can_surface=True,
        background_allowed=False,
    ),
}


@dataclass(slots=True)
class MemoryScore:
    total: float

    relevance: float = 0.0
    resonance: float = 0.0
    permission: float = 1.0
    surfaceability: float = 0.0

    lexical: float = 0.0
    phrase: float = 0.0
    bigram: float = 0.0
    anchor: float = 0.0
    semantic: float = 0.0
    graph: float = 0.0
    temporal: float = 0.0

    confidence_prior: float = 0.0
    salience_prior: float = 0.0
    affective_prior: float = 0.0
    relational_prior: float = 0.0
    repair_prior: float = 0.0
    recency_prior: float = 0.0

    risk_penalty: float = 0.0
    privacy_penalty: float = 0.0
    overuse_penalty: float = 0.0

    matched_terms: tuple[str, ...] = ()
    matched_anchors: tuple[str, ...] = ()
    reason: str = ""
    warnings: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "total": round(float(self.total), 4),
            "relevance": round(float(self.relevance), 4),
            "resonance": round(float(self.resonance), 4),
            "permission": round(float(self.permission), 4),
            "surfaceability": round(float(self.surfaceability), 4),
            "lexical": round(float(self.lexical), 4),
            "phrase": round(float(self.phrase), 4),
            "bigram": round(float(self.bigram), 4),
            "anchor": round(float(self.anchor), 4),
            "semantic": round(float(self.semantic), 4),
            "graph": round(float(self.graph), 4),
            "temporal": round(float(self.temporal), 4),
            "confidence_prior": round(float(self.confidence_prior), 4),
            "salience_prior": round(float(self.salience_prior), 4),
            "affective_prior": round(float(self.affective_prior), 4),
            "relational_prior": round(float(self.relational_prior), 4),
            "repair_prior": round(float(self.repair_prior), 4),
            "recency_prior": round(float(self.recency_prior), 4),
            "risk_penalty": round(float(self.risk_penalty), 4),
            "privacy_penalty": round(float(self.privacy_penalty), 4),
            "overuse_penalty": round(float(self.overuse_penalty), 4),
            "matched_terms": list(self.matched_terms),
            "matched_anchors": list(self.matched_anchors),
            "reason": self.reason,
            "warnings": list(self.warnings),
        }


class MemoryScorer:
    def __init__(self, profiles: dict[str, LayerProfile] | None = None):
        self.profiles = profiles or DEFAULT_LAYER_PROFILES

    def score(self, cue: MemoryCue, candidate: MemoryCandidate) -> MemoryScore:
        record = candidate.record
        profile = self.profiles.get(record.layer, LayerProfile(
            layer=record.layer,
            weight=0.8,
            min_relevance=0.12,
            bias_routes=("context",),
            default_surface_policy="silent_bias",
            can_surface=False,
            background_allowed=False,
        ))

        lexical = clamp01(candidate.lexical_score)
        phrase = clamp01(candidate.phrase_score)
        bigram = clamp01(candidate.bigram_score)
        anchor = clamp01(candidate.anchor_score)
        temporal = clamp01(candidate.temporal_score)

        cue_family_match = 1.0 if cue.semantic_family and cue.semantic_family in set(record.anchors) | set(record.tags) else 0.0
        repair_tag_match = len(set(cue.repair_tags) & (set(record.anchors) | set(record.tags))) / max(1, len(set(cue.repair_tags))) if cue.repair_tags else 0.0

        semantic = max(cue_family_match, repair_tag_match)

        # Relevance must be query-match first, but lexical matches should not be
        # starved. The old weights made two direct terms still fall below
        # relational/procedural thresholds, which is how a memory system becomes
        # an expensive empty list.
        relevance = clamp01(
            lexical * 0.42
            + phrase * 0.18
            + anchor * 0.22
            + semantic * 0.22
            + bigram * 0.20
            + temporal * 0.03
            + (0.08 if candidate.matched_terms else 0.0)
            + (0.08 if candidate.matched_anchors else 0.0)
        )

        confidence_prior = clamp01(record.confidence) * 0.08
        salience_prior = clamp01(record.salience) * 0.08
        affective_prior = clamp01(record.emotional_weight) * 0.05
        relational_prior = clamp01(record.relational_weight) * 0.06
        repair_prior = clamp01(max(record.repair_weight, 0.7 if record.layer == "repair" else 0.0)) * 0.10
        recency_prior = temporal * 0.04

        privacy_penalty = self._privacy_penalty(cue, record)
        risk_penalty = self._risk_penalty(cue, candidate)
        overuse_penalty = min(0.10, max(0, int(record.access_count or 0)) * 0.012)

        warnings = list(candidate.warnings)

        # Query rỗng: chỉ background layers có quyền tạo bias yếu.
        if not cue.token_set() and not cue.anchor_set():
            if profile.background_allowed:
                # Background bias is allowed only for non-surface layers, but it
                # must survive adapter rough-score zero. Keep it weak, not dead.
                background = clamp01((0.12 + confidence_prior + salience_prior + affective_prior + relational_prior) * profile.weight)
                return MemoryScore(
                    total=background,
                    relevance=0.0,
                    resonance=background,
                    permission=1.0 - privacy_penalty,
                    surfaceability=0.0,
                    confidence_prior=confidence_prior,
                    salience_prior=salience_prior,
                    affective_prior=affective_prior,
                    relational_prior=relational_prior,
                    repair_prior=repair_prior,
                    recency_prior=recency_prior,
                    privacy_penalty=privacy_penalty,
                    overuse_penalty=overuse_penalty,
                    matched_terms=candidate.matched_terms,
                    matched_anchors=candidate.matched_anchors,
                    reason="background_bias_without_query",
                    warnings=tuple(warnings),
                )
            # Semantic/episodic với fact_key quan trọng vẫn được trả về dưới dạng hot retention
            # khi không có query, thay vì bị block hoàn toàn. Đây là thứ Eris "biết" về user
            # ngay cả khi không đang hỏi gì.
            fact_key = str(getattr(record, "fact_key", "") or getattr(getattr(record, "metadata", {}), "get", lambda k, d="": d)("fact_key", ""))
            importance = clamp01(getattr(record, "salience", 0.0) or 0.0) or clamp01(
                (getattr(getattr(record, "metadata", {}), "get", lambda k, d=0.0: d)("importance", 0.0))
                if isinstance(getattr(record, "metadata", None), dict)
                else 0.0
            )
            if fact_key and importance >= 0.70:
                hot = clamp01((0.08 + confidence_prior + importance * 0.06) * profile.weight)
                return MemoryScore(
                    total=hot,
                    relevance=0.0,
                    resonance=hot,
                    permission=1.0 - privacy_penalty,
                    surfaceability=hot * 0.5 if profile.can_surface else 0.0,
                    confidence_prior=confidence_prior,
                    privacy_penalty=privacy_penalty,
                    matched_terms=candidate.matched_terms,
                    matched_anchors=candidate.matched_anchors,
                    reason="hot_key_fact_no_query",
                    warnings=tuple(warnings),
                )
            return MemoryScore(total=0.0, reason="no_query_no_background_for_layer", warnings=tuple(warnings))

        if relevance < profile.min_relevance:
            return MemoryScore(
                total=0.0,
                relevance=relevance,
                lexical=lexical,
                phrase=phrase,
                bigram=bigram,
                anchor=anchor,
                semantic=semantic,
                temporal=temporal,
                matched_terms=candidate.matched_terms,
                matched_anchors=candidate.matched_anchors,
                reason=f"below_min_relevance:{profile.min_relevance:.2f}",
                warnings=tuple(warnings),
            )

        prior = (
            confidence_prior
            + salience_prior
            + affective_prior
            + relational_prior
            + repair_prior
            + recency_prior
        )

        total = clamp01(relevance * profile.weight + prior - privacy_penalty - risk_penalty - overuse_penalty)
        surfaceability = clamp01(total if profile.can_surface else 0.0)
        permission = clamp01(1.0 - privacy_penalty - risk_penalty)

        return MemoryScore(
            total=total,
            relevance=relevance,
            resonance=total,
            permission=permission,
            surfaceability=surfaceability,
            lexical=lexical,
            phrase=phrase,
            bigram=bigram,
            anchor=anchor,
            semantic=semantic,
            temporal=temporal,
            confidence_prior=confidence_prior,
            salience_prior=salience_prior,
            affective_prior=affective_prior,
            relational_prior=relational_prior,
            repair_prior=repair_prior,
            recency_prior=recency_prior,
            risk_penalty=risk_penalty,
            privacy_penalty=privacy_penalty,
            overuse_penalty=overuse_penalty,
            matched_terms=candidate.matched_terms,
            matched_anchors=candidate.matched_anchors,
            reason=f"scored_by_profile:{profile.layer}",
            warnings=tuple(warnings),
        )

    def _privacy_penalty(self, cue: MemoryCue, record: Any) -> float:
        if cue.privacy_sensitivity < 0.5:
            return 0.0
        if getattr(record, "privacy_level", "normal") in {"private", "sensitive"}:
            return 0.35
        return 0.08

    def _risk_penalty(self, cue: MemoryCue, candidate: MemoryCandidate) -> float:
        # Memory không được trả lời thay current screen/file evidence.
        if cue.needs_current_evidence and candidate.record.layer in {"semantic", "episodic"} and not cue.needs_exact_recall:
            return 0.55
        return 0.0
