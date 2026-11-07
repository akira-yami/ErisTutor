from __future__ import annotations

"""
memory_activation.py
====================

Biến scored candidates thành MemoryActivation.

Activation không phải "hãy nói ký ức này ra".
Nó là áp lực có route:
- attention
- appraisal
- wording
- silence
- context

Surface policy quyết định có được đưa ra lời nói không.
Affective residue thường chỉ đi vào appraisal/silence, không được biến thành
claim công khai kiểu "tôi buồn". Cảm ơn, cuối cùng cũng có chút phẩm giá.
"""

from dataclasses import dataclass, field
from typing import Any

try:
    from .memory_record import compact_text, clamp01
    from .memory_candidate import MemoryCandidate
    from .memory_scoring import MemoryScore, LayerProfile, DEFAULT_LAYER_PROFILES
except Exception:  # pragma: no cover
    from memory_record import compact_text, clamp01
    from memory_candidate import MemoryCandidate
    from memory_scoring import MemoryScore, LayerProfile, DEFAULT_LAYER_PROFILES


@dataclass(slots=True)
class MemoryActivation:
    record_id: str
    layer: str
    text: str

    score: float
    relevance: float
    resonance: float

    surface_policy: str
    bias_routes: tuple[str, ...]
    matched_terms: tuple[str, ...] = ()
    matched_anchors: tuple[str, ...] = ()

    evidence_ids: tuple[str, ...] = ()
    score_breakdown: dict[str, Any] = field(default_factory=dict)

    may_surface: bool = False
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "record_id": self.record_id,
            "layer": self.layer,
            "text": self.text,
            "score": round(float(self.score), 3),
            "relevance": round(float(self.relevance), 3),
            "resonance": round(float(self.resonance), 3),
            "surface_policy": self.surface_policy,
            "bias_routes": list(self.bias_routes),
            "matched_terms": list(self.matched_terms),
            "matched_anchors": list(self.matched_anchors),
            "evidence_ids": list(self.evidence_ids),
            "score_breakdown": dict(self.score_breakdown),
            "may_surface": bool(self.may_surface),
            "reason": self.reason,
        }

    def to_attention_line(self) -> str:
        return compact_text(
            f"active_memory_resonance/{self.layer} score={self.score:.2f} "
            f"policy={self.surface_policy}: {self.text}",
            limit=280,
        )


@dataclass(slots=True)
class ScoredMemoryCandidate:
    candidate: MemoryCandidate
    score: MemoryScore


@dataclass(slots=True)
class MemoryRetrievalResult:
    # Records được chọn sau WorkingMemoryGate. Không recall lần hai theo text.
    source_records: tuple[Any, ...] = ()
    activations: tuple[MemoryActivation, ...] = ()
    scored_candidates: tuple[ScoredMemoryCandidate, ...] = ()
    attention_bias: float = 0.0
    appraisal_bias: float = 0.0
    wording_bias: float = 0.0
    silence_bias: float = 0.0
    dominant_layer: str = ""
    score: float = 0.0
    debug_notes: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class WorkingMemoryPolicy:
    """Capacity policy for the active memory workspace, not long-term storage.

    Retrieval may discover many memories; cognition should not broadcast all of
    them.  Keeping a small, route-aware active set prevents episodic memory from
    becoming a second prompt history and mirrors the useful neuroscience motif:
    large long-term stores, sharply limited working access.
    """

    low_capacity: int = 3
    normal_capacity: int = 5
    high_capacity: int = 7
    exact_recall_capacity: int = 8
    background_episodic_quota: int = 1
    exact_episodic_quota: int = 4


class WorkingMemoryGate:
    """Project retrieval results into a bounded active workspace.

    This is intentionally a *gate*, not another scorer. MemoryScorer determines
    relevance. The gate only enforces capacity and layer permissions supplied by
    the current MemoryCue.
    """

    def __init__(self, policy: WorkingMemoryPolicy | None = None) -> None:
        self.policy = policy or WorkingMemoryPolicy()

    def _capacity(self, cue: Any, requested: int) -> int:
        budget = str(getattr(cue, "budget_mode", "normal") or "normal").lower()
        exact = bool(getattr(cue, "needs_exact_recall", False))
        if exact:
            base = self.policy.exact_recall_capacity
        elif budget in {"low", "tight", "minimal", "degraded"}:
            base = self.policy.low_capacity
        elif budget in {"high", "deep", "expanded"}:
            base = self.policy.high_capacity
        else:
            base = self.policy.normal_capacity
        return max(1, min(int(requested), int(base)))

    def apply(
        self,
        result: "MemoryRetrievalResult",
        *,
        cue: Any,
        requested: int,
    ) -> "MemoryRetrievalResult":
        capacity = self._capacity(cue, requested)
        exact = bool(getattr(cue, "needs_exact_recall", False))
        current_evidence = bool(getattr(cue, "needs_current_evidence", False))
        query_text = str(getattr(cue, "query_text", "") or "").strip()
        episodic_quota = (
            self.policy.exact_episodic_quota if exact
            else (self.policy.background_episodic_quota if query_text and not current_evidence else 0)
        )
        quotas = {
            "repair": 1,
            "procedural": 1,
            "relational": 1,
            "affective_residue": 1,
            "semantic": 3 if exact else 2,
            "episodic": episodic_quota,
        }
        selected: list[MemoryActivation] = []
        counts: dict[str, int] = {}
        for activation in result.activations:
            if len(selected) >= capacity:
                break
            layer = str(activation.layer or "")
            quota = quotas.get(layer, 1)
            if quota <= 0 or counts.get(layer, 0) >= quota:
                continue
            # Episodic recollection is expensive and context-heavy. Outside an
            # explicit recall request it must win on relevance, not mere salience.
            if layer == "episodic" and not exact and activation.relevance < 0.34:
                continue
            selected.append(activation)
            counts[layer] = counts.get(layer, 0) + 1

        def route_bias(route: str) -> float:
            values = [item.score for item in selected if route in set(item.bias_routes)]
            return clamp01(max(values) if values else 0.0)

        dominant = selected[0].layer if selected else ""
        total = clamp01(
            sum(item.score for item in selected[:3]) / max(1, min(3, len(selected)))
            if selected else 0.0
        )
        dropped = max(0, len(result.activations) - len(selected))
        return MemoryRetrievalResult(
            activations=tuple(selected),
            scored_candidates=result.scored_candidates,
            attention_bias=route_bias("attention"),
            appraisal_bias=route_bias("appraisal"),
            wording_bias=route_bias("wording"),
            silence_bias=clamp01(route_bias("silence") * 0.75),
            dominant_layer=dominant,
            score=total,
            debug_notes=tuple((*result.debug_notes, f"working_memory_gate:kept={len(selected)}:dropped={dropped}:capacity={capacity}")),
        )


class MemoryActivationBuilder:
    def __init__(self, profiles: dict[str, LayerProfile] | None = None):
        self.profiles = profiles or DEFAULT_LAYER_PROFILES

    def build(self, scored: list[ScoredMemoryCandidate], *, max_activations: int = 10) -> MemoryRetrievalResult:
        usable = [item for item in scored if item.score.total > 0.0]
        usable.sort(key=lambda item: item.score.total, reverse=True)

        activations: list[MemoryActivation] = []
        debug_notes: list[str] = []

        for item in usable[:max_activations]:
            record = item.candidate.record
            score = item.score
            profile = self.profiles.get(record.layer)
            routes = profile.bias_routes if profile else ("context",)
            surface_policy = record.surface_policy or (profile.default_surface_policy if profile else "silent_bias")
            may_surface = bool(profile.can_surface if profile else False) and score.surfaceability >= 0.18 and score.permission >= 0.5

            activation = MemoryActivation(
                record_id=record.id,
                layer=record.layer,
                text=record.recall_line(limit=260),
                score=score.total,
                relevance=score.relevance,
                resonance=score.resonance,
                surface_policy=surface_policy,
                bias_routes=routes,
                matched_terms=score.matched_terms,
                matched_anchors=score.matched_anchors,
                evidence_ids=record.evidence_ids,
                score_breakdown=score.to_dict(),
                may_surface=may_surface,
                reason=score.reason,
            )
            activations.append(activation)

        attention_bias = self._route_bias(activations, "attention")
        appraisal_bias = self._route_bias(activations, "appraisal")
        wording_bias = self._route_bias(activations, "wording")
        silence_bias = clamp01(self._route_bias(activations, "silence") * 0.75)
        dominant_layer = activations[0].layer if activations else ""
        total_score = clamp01(sum(item.score for item in activations[:3]) / max(1, min(3, len(activations))) if activations else 0.0)

        return MemoryRetrievalResult(
            activations=tuple(activations),
            scored_candidates=tuple(usable[:max_activations]),
            attention_bias=attention_bias,
            appraisal_bias=appraisal_bias,
            wording_bias=wording_bias,
            silence_bias=silence_bias,
            dominant_layer=dominant_layer,
            score=total_score,
            debug_notes=tuple(debug_notes),
        )

    def _route_bias(self, activations: list[MemoryActivation], route: str) -> float:
        if not activations:
            return 0.0
        values = [item.score for item in activations if route in set(item.bias_routes)]
        return clamp01(max(values) if values else 0.0)
