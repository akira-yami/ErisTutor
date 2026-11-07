# -*- coding: utf-8 -*-
"""Canonical reflection/dream scheduling and consolidation for ERIS V3.

Dream is delayed pattern consolidation, not a decorative callback after every
turn. The scheduler therefore emits no long-term requests for ordinary low-value
turns. Humanity could use the same restraint with meeting notes.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import time
from typing import Any

from core.evidence import StateCommitRequest


def _get(obj: Any, name: str, default: Any = None) -> Any:
    if obj is None:
        return default
    return obj.get(name, default) if isinstance(obj, dict) else getattr(obj, name, default)


def _plain(obj: Any) -> Any:
    if obj is None or isinstance(obj, (str, int, float, bool)):
        return obj
    if isinstance(obj, dict):
        return {str(k): _plain(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple, set)):
        return [_plain(v) for v in obj]
    if hasattr(obj, "to_dict"):
        try:
            return _plain(obj.to_dict())
        except Exception:
            pass
    return repr(obj)


def _evidence_ids(frame: Any) -> list[str]:
    out = []
    for item in list(_get(frame, "evidence", []) or []):
        value = str(_get(item, "id", _get(item, "evidence_id", "")) or "")
        if value:
            out.append(value)
    return list(dict.fromkeys(out))[:24]


@dataclass(slots=True)
class DreamScheduleDecision:
    scheduled: bool
    reason: str
    pressure: float
    run_after: float = 0.0
    tags: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "scheduled": self.scheduled,
            "reason": self.reason,
            "pressure": round(float(self.pressure), 4),
            "run_after": self.run_after,
            "tags": list(self.tags),
        }


class DreamConsolidationEngine:
    """Builds commit requests. It never writes stores directly."""

    def build_requests(self, frame: Any, decision: DreamScheduleDecision) -> list[StateCommitRequest]:
        if not decision.scheduled:
            return []
        ids = _evidence_ids(frame)
        if not ids:
            return []
        permission = _get(frame, "permission", None)
        storage = bool(_get(permission, "memory_storable", False))
        if not storage:
            return []
        temporal = _get(frame, "temporal", None)
        spatial = _get(frame, "spatial", None)
        homeostasis = _get(frame, "homeostasis", None)
        tom = _get(frame, "tom", None)
        sociocultural = _get(frame, "sociocultural", None)
        self_obs = _get(frame, "self_observation", None)
        outcome = _get(frame, "outcome", None)
        relation = _get(frame, "relation", None)
        knowledge = dict(_get(frame, "metadata", {}) or {}).get("knowledge_acquisition")
        requests: list[StateCommitRequest] = []

        def add(target: str, content: Any, confidence: float, *, decay: str = "normal", tags: list[str] | None = None, reason: str = "") -> None:
            requests.append(StateCommitRequest(
                target=target,
                content=content,
                evidence_ids=ids,
                confidence=max(0.0, min(1.0, confidence)),
                permission_required=True,
                storage_permission=storage,
                decay_policy=decay,
                reason=reason or f"Canonical dream consolidation: {target}.",
                source_module="dream_consolidation",
                tags=["v3", "dream_consolidation", *(tags or [])],
                metadata={"schedule": decision.to_dict(), "frame_id": str(_get(frame, "frame_id", "") or "")},
            ))

        add("temporal_rhythm_memory", _plain(temporal), 0.68, decay="slow", tags=["rhythm"])
        if spatial is not None and (_get(spatial, "gaze_target", None) or float(_get(spatial, "obstruction_risk", 0.0) or 0.0) > 0.2):
            add("spatial_memory", _plain(spatial), 0.62, decay="normal", tags=["spatial"])
        body = _get(frame, "body_plan", None)
        if body is not None and str(_get(body, "action", "none")) != "none":
            add("body_rhythm_memory", {"body_plan": _plain(body), "outcome": _plain(outcome)}, 0.64, decay="normal", tags=["body"])
        if float(_get(tom, "global_confidence", 0.0) or 0.0) >= 0.50:
            add("tom_history", {"tom": _plain(tom), "outcome": _plain(outcome), "self_observation": _plain(self_obs)}, 0.58, decay="fast", tags=["tentative_tom"])
        if sociocultural:
            add("sociocultural_memory", {"annotations": _plain(sociocultural), "relation": _plain(relation)}, 0.60, decay="slow", tags=["culture"])
        if str(_get(frame, "budget_mode", "")) == "reflection":
            add("homeostatic_baseline", _plain(homeostasis), 0.70, decay="slow", tags=["baseline"])
            add("self_continuity", {
                "winning_value": str(_get(_get(frame, "value_resolution", None), "winning_value", "truthfulness")),
                "agency": _plain(_get(frame, "agency", None)),
                "self_observation": _plain(self_obs),
            }, 0.66, decay="slow", tags=["identity_continuity"])
        if knowledge is not None and bool(_get(knowledge, "succeeded", False)):
            for fact in list(_get(knowledge, "candidate_beliefs", []) or [])[:4]:
                confidence = float(_get(fact, "confidence", 0.0) or 0.0)
                if confidence >= 0.74:
                    add("worldview_memory", _plain(fact), min(0.82, confidence), decay="slow", tags=["tool_grounded", "tentative_worldview"])
        if decision.pressure >= 0.70 or bool(_get(outcome, "user_corrected", False)):
            add("episodic_memory", {
                "scene": [str(_get(frame, "user_text", "") or "")],
                "eris_action": str(_get(_get(frame, "wording", None), "parsed_text", "") or ""),
                "outcome": _plain(outcome),
                "self_observation": _plain(self_obs),
            }, 0.72, decay="normal", tags=["high_value_episode"])
        return requests


__all__ = ["DreamScheduleDecision", "DreamConsolidationEngine"]
