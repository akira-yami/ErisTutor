# -*- coding: utf-8 -*-
"""Projection nội tâm an toàn cho planner production.

V7 ưu tiên ``SubjectService.inner_view()``: active needs, belief có nguồn, value
đã hình thành, competence calibration, tendency residue và continuity. Module
không chuyển raw chain-of-thought, diary hay evidence bị che sang model nói công
khai. Các trường runtime phụ chỉ còn là telemetry, không phải nguồn nội tâm chính.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Mapping


def _clamp01(value: Any, default: float = 0.0) -> float:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        numeric = default
    if numeric != numeric:
        numeric = default
    return max(0.0, min(1.0, numeric))


def _compact(value: Any, limit: int = 260) -> str:
    text = " ".join(str(value or "").split()).strip()
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + "…"


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _surface_preview(item: Any) -> dict[str, Any]:
    if isinstance(item, Mapping):
        data = dict(item)
    elif callable(getattr(item, "to_context", None)):
        try:
            data = dict(item.to_context() or {})
        except Exception:
            data = {}
    elif callable(getattr(item, "to_dict", None)):
        try:
            data = dict(item.to_dict() or {})
        except Exception:
            data = {}
    else:
        data = {}
    raw_evidence = data.get("evidence_refs", data.get("evidence_ids", ()))
    if isinstance(raw_evidence, str):
        raw_evidence = (raw_evidence,) if raw_evidence.strip() else ()
    elif not isinstance(raw_evidence, (list, tuple)):
        raw_evidence = ()
    evidence_refs = tuple(
        item for item in (_compact(value, 120) for value in raw_evidence[:6]) if item
    )
    return {
        "id": _compact(data.get("id", ""), 80),
        "kind": _compact(data.get("kind", ""), 80),
        "source": _compact(data.get("source", data.get("root_source", "")), 80),
        "topic_seed": _compact(data.get("topic_seed", data.get("seed", "")), 240),
        "confidence": _clamp01(data.get("confidence", 0.0)),
        "pressure": _clamp01(data.get("pressure", data.get("vital_pressure", 0.0))),
        "score": _clamp01(data.get("score", data.get("usefulness", 0.0))),
        "reason": _compact(data.get("reason", ""), 160),
        "evidence_refs": list(evidence_refs),
    }




def _thread_preview(item: Any) -> Mapping[str, Any]:
    if item is None:
        return {}
    to_public = getattr(item, "to_public_dict", None)
    if callable(to_public):
        try:
            data = dict(to_public() or {})
        except Exception:
            data = {}
    elif isinstance(item, Mapping):
        data = dict(item)
    else:
        data = {}
    raw_refs = data.get("evidence_refs", ())
    if isinstance(raw_refs, str):
        raw_refs = (raw_refs,)
    if not isinstance(raw_refs, (list, tuple)):
        raw_refs = ()
    evidence_refs = [
        clean for clean in (_compact(value, 120) for value in raw_refs[:8]) if clean
    ]
    if not evidence_refs:
        return {}
    return {
        "thread_id": _compact(data.get("thread_id", ""), 100),
        "event_kind": _compact(data.get("event_kind", ""), 80),
        "event_ids": [
            clean for clean in (_compact(value, 100) for value in tuple(data.get("event_ids", ()) or ())[:4]) if clean
        ],
        "dominant_appraisal_id": _compact(data.get("dominant_appraisal_id", ""), 100),
        "active_motive_ids": [
            clean for clean in (_compact(value, 100) for value in tuple(data.get("active_motive_ids", ()) or ())[:6]) if clean
        ],
        "conflict": _compact(data.get("conflict", data.get("conflict_summary", "")), 220),
        "epistemic_status": _compact(data.get("epistemic_status", "tentative"), 40),
        "permitted_surface_levels": [
            clean for clean in (_compact(value, 60) for value in tuple(data.get("permitted_surface_levels", ()) or ())[:7]) if clean
        ],
        "prohibited_moves": [
            clean for clean in (_compact(value, 80) for value in tuple(data.get("prohibited_moves", ()) or ())[:8]) if clean
        ],
        "evidence_refs": evidence_refs,
        "salience": round(_clamp01(data.get("salience", 0.0)), 3),
        "uncertainty": round(_clamp01(data.get("uncertainty", 1.0), 1.0), 3),
        "repeated_violation": bool(data.get("repeated_violation", False)),
    }

@dataclass(frozen=True, slots=True)
class AutonomousSelfContext:
    internal_state: Mapping[str, Any] = field(default_factory=dict)
    attention: Mapping[str, Any] = field(default_factory=dict)
    agenda: tuple[Mapping[str, Any], ...] = ()
    private_mind_surface: tuple[Mapping[str, Any], ...] = ()
    private_life_surface: tuple[Mapping[str, Any], ...] = ()
    # Mental workspace là context nội tại cho seed worker, không tự cấp quyền nói.
    private_life_workspace: tuple[Mapping[str, Any], ...] = ()
    active_threads: tuple[Mapping[str, Any], ...] = ()
    # V40: projection concern đang sống, dùng chung cho seed/planner/Neuro.
    active_concerns: tuple[Mapping[str, Any], ...] = ()
    has_public_seed: bool = False
    strongest_seed_score: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "internal_state": dict(self.internal_state),
            "attention": dict(self.attention),
            "agenda": [dict(item) for item in self.agenda],
            "private_mind_surface": [dict(item) for item in self.private_mind_surface],
            "private_life_surface": [dict(item) for item in self.private_life_surface],
            "private_life_workspace": [dict(item) for item in self.private_life_workspace],
            "active_threads": [dict(item) for item in self.active_threads],
            "active_concerns": [dict(item) for item in self.active_concerns],
            "has_public_seed": bool(self.has_public_seed),
            "strongest_seed_score": round(float(self.strongest_seed_score), 3),
        }

    def prompt_context(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False)


def build_autonomous_self_context(host: Any) -> AutonomousSelfContext:
    """Rút public-safe self context từ runtime owner hiện tại."""

    rolling = getattr(host, "rolling_internal_state", None)
    internal_state = {
        key: round(_clamp01(getattr(rolling, key, 0.0)), 3)
        for key in (
            "curiosity",
            "fatigue",
            "silence_pressure",
            "continuity_pressure",
            "attachment_pressure",
            "consolidation_pressure",
            "insight_pressure",
            "agenda_pressure",
            "speech_restraint",
            "resource_caution",
            "ontological_dread",
            "effective_dread",
            "causal_thread_pressure",
            "self_coherence",
        )
    }
    internal_state["internal_drive"] = round(_clamp01(getattr(rolling, "internal_drive", 0.0)), 3)

    # Subject Core là nguồn nội tâm duy nhất. Planner chỉ nhận bounded projection,
    # không nhận raw event ledger hay private reasoning.
    subject_service = getattr(host, "subject_service", None)
    inner_view = {}
    inner_view_fn = getattr(subject_service, "inner_view", None)
    if callable(inner_view_fn):
        try:
            candidate = inner_view_fn()
            if isinstance(candidate, Mapping):
                inner_view = dict(candidate)
        except Exception:
            inner_view = {}
    if inner_view:
        internal_state["subject_core"] = inner_view

    attention_obj = getattr(host, "last_cortex_attention", None)
    attention = {
        "level": _compact(getattr(attention_obj, "level", "quiet"), 48),
        "reason": _compact(getattr(attention_obj, "reason", ""), 160),
        "confidence": round(_clamp01(getattr(attention_obj, "confidence", 1.0)), 3),
        "external_pressure": round(_clamp01(getattr(attention_obj, "external_pressure", 0.0)), 3),
        "internal_drive": round(_clamp01(getattr(attention_obj, "internal_drive", 0.0)), 3),
        "causal_thread_pressure": round(_clamp01(getattr(attention_obj, "causal_thread_pressure", 0.0)), 3),
        "dominant_thread_id": _compact(getattr(attention_obj, "dominant_thread_id", ""), 100),
        "interrupt_cost": round(_clamp01(getattr(attention_obj, "interrupt_cost", 0.0)), 3),
        "resource_caution": round(_clamp01(getattr(attention_obj, "resource_caution", 0.0)), 3),
        "vision_refresh_recommended": bool(getattr(attention_obj, "vision_refresh_recommended", False)),
        "semantic_authority": "none",
    }

    agenda_items: list[Mapping[str, Any]] = []
    agenda_stack = getattr(host, "internal_agenda", None)
    top_items = getattr(agenda_stack, "top_items", None)
    if callable(top_items):
        try:
            candidates = tuple(top_items(limit=12) or ())
        except Exception:
            candidates = ()
        for item in candidates:
            evidence = tuple(str(value) for value in (getattr(item, "evidence_ids", ()) or ()) if str(value).strip())[:5]
            summary = _compact(getattr(item, "summary", ""), 260)
            if not summary or not evidence:
                continue
            agenda_items.append(
                {
                    "id": _compact(getattr(item, "id", ""), 80),
                    "kind": _compact(getattr(item, "kind", "misc"), 80),
                    "summary": summary,
                    "pressure": round(_clamp01(getattr(item, "pressure", 0.0)), 3),
                    "urgency": round(_clamp01(getattr(item, "urgency", 0.0)), 3),
                    "confidence": round(_clamp01(getattr(item, "confidence", 0.0)), 3),
                    "speech_value": round(_clamp01(getattr(item, "speech_value", 0.0)), 3),
                    "score": round(_clamp01(getattr(item, "score", 0.0)), 3),
                    "evidence_refs": list(evidence),
                }
            )

    mind_digest = _mapping(getattr(host, "private_mind_daemon_digest", None))
    daemon = _mapping(mind_digest.get("daemon"))
    mind_source = daemon.get("surface_queue_preview")
    if not isinstance(mind_source, (list, tuple)):
        mind_source = tuple(getattr(host, "private_mind_surface_queue", ()) or ())
    private_mind = tuple(
        preview for preview in (_surface_preview(item) for item in tuple(mind_source or ())[:12])
        if preview.get("topic_seed")
    )

    life_digest = _mapping(getattr(host, "private_life_digest", None))
    life_source = life_digest.get("surface_queue")
    if not isinstance(life_source, (list, tuple)):
        life_source = tuple(getattr(host, "private_life_surface_queue", ()) or ())
    private_life = tuple(
        preview for preview in (_surface_preview(item) for item in tuple(life_source or ())[:12])
        if preview.get("topic_seed")
    )
    life_current = _mapping(life_digest.get("current"))
    private_life_workspace = tuple(
        dict(item) for item in tuple(life_current.get("mental_workspace", ()) or ())[:24]
        if isinstance(item, Mapping) and _compact(item.get("content", ""), 620)
    )

    raw_threads = tuple(
        getattr(host, "active_causal_threads", ())
        or getattr(host, "active_threads", ())
        or ()
    )
    active_threads = tuple(
        preview for preview in (_thread_preview(item) for item in raw_threads[:8])
        if preview.get("thread_id") and preview.get("evidence_refs")
    )

    active_concerns: tuple[Mapping[str, Any], ...] = ()
    concern_runtime = getattr(host, "active_concern_runtime", None)
    snapshot_fn = getattr(concern_runtime, "snapshot", None)
    if callable(snapshot_fn):
        try:
            active_concerns = tuple(dict(item) for item in tuple(snapshot_fn(limit=16) or ()) if isinstance(item, Mapping))
        except Exception:
            active_concerns = ()

    seed_scores: list[float] = []
    for item in agenda_items:
        speech_value = _clamp01(item.get("speech_value", 0.0))
        confidence = _clamp01(item.get("confidence", 0.0))
        score = _clamp01(item.get("score", 0.0))
        seed_scores.append(0.45 * speech_value + 0.30 * confidence + 0.25 * score)
    for item in (*private_mind, *private_life):
        confidence = _clamp01(item.get("confidence", 0.0))
        pressure = _clamp01(item.get("pressure", 0.0))
        score = _clamp01(item.get("score", 0.0))
        seed_scores.append(max(score, 0.48 * confidence + 0.30 * pressure + 0.22 * score))

    for item in active_threads:
        seed_scores.append(_clamp01(item.get("salience", 0.0)) * (1.0 - 0.35 * _clamp01(item.get("uncertainty", 1.0), 1.0)))
    for item in active_concerns:
        # Concern có thể là public seed chỉ khi speech_pressure đủ; activation cao
        # nhưng habituated/đang bị interruption không tự biến thành lý do nói.
        seed_scores.append(_clamp01(item.get("speech_pressure", 0.0)))

    strongest = max(seed_scores, default=0.0)
    # A grounded causal thread may become a public seed. Scalar dread alone may
    # not. This keeps the planner informed without laundering private residue
    # into a claim about the user.
    has_public_seed = bool(strongest >= 0.48 and (agenda_items or private_mind or private_life or active_threads or active_concerns))

    return AutonomousSelfContext(
        internal_state=internal_state,
        attention=attention,
        agenda=tuple(agenda_items[:12]),
        private_mind_surface=private_mind,
        private_life_surface=private_life,
        private_life_workspace=private_life_workspace,
        active_threads=active_threads,
        active_concerns=active_concerns,
        has_public_seed=has_public_seed,
        strongest_seed_score=strongest,
    )


__all__ = ["AutonomousSelfContext", "build_autonomous_self_context"]
