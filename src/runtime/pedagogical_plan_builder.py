"""Fail-closed boundary for model-generated pedagogical plans."""
from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping

from runtime.pedagogical_contracts import MisconceptionHypothesis, TeachingConcept, TeachingPlan


def _canonical_key(value: Any) -> str:
    return "".join(character for character in str(value or "").casefold() if character.isalnum())


_FORBIDDEN_PLAN_KEYS = frozenset(_canonical_key(value) for value in {
    "assistant_reply", "chain_of_thought", "completion_claim", "cot", "public_reply",
    "public_text", "public_utterance", "reasoning", "speech_text", "utterance",
})
_MAX_CONTEXT_KEYS = 16
_MAX_CONTEXT_ITEMS = 12
_MAX_CONTEXT_TEXT = 1200
_MAX_CONTEXT_DEPTH = 3


def _bounded_value(value: Any, *, depth: int = 0) -> Any:
    if depth >= _MAX_CONTEXT_DEPTH:
        return str(value or "")[:_MAX_CONTEXT_TEXT]
    if isinstance(value, Mapping):
        return {
            str(key)[:120]: _bounded_value(item, depth=depth + 1)
            for key, item in list(value.items())[:_MAX_CONTEXT_KEYS]
        }
    if isinstance(value, (list, tuple)):
        return [_bounded_value(item, depth=depth + 1) for item in value[:_MAX_CONTEXT_ITEMS]]
    if isinstance(value, str):
        return value[:_MAX_CONTEXT_TEXT]
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return str(value)[:_MAX_CONTEXT_TEXT]


def _contains_forbidden_fields(value: Any) -> bool:
    if isinstance(value, Mapping):
        for key, item in value.items():
            if _canonical_key(key) in _FORBIDDEN_PLAN_KEYS or _contains_forbidden_fields(item):
                return True
    elif isinstance(value, (list, tuple)):
        return any(_contains_forbidden_fields(item) for item in value)
    return False


def _clean_text(value: Any, *, limit: int = 420) -> str:
    return value.strip()[:limit] if isinstance(value, str) else ""


def _stable_id(prefix: str, *parts: Any) -> str:
    material = json.dumps(parts, ensure_ascii=True, separators=(",", ":"), default=str)
    digest = hashlib.sha256(material.encode("utf-8")).hexdigest()[:12]
    return f"{prefix}-{digest}"


def _unique_concept_id(raw_id: Any, item: Mapping[str, Any], index: int, used: set[str]) -> str:
    candidate = _clean_text(raw_id, limit=120)
    if not candidate or candidate in used:
        candidate = _stable_id("concept", index, item.get("objective", ""), item.get("description", ""))
    while candidate in used:
        candidate = _stable_id("concept", candidate, index)
    used.add(candidate)
    return candidate


class TeachingPlanBuilder:
    """Turns one explicit structured planning result into a bounded typed plan."""

    def __init__(self, router: Any) -> None:
        self.router = router

    def build(self, request_context: Mapping[str, Any]) -> TeachingPlan | None:
        if not isinstance(request_context, Mapping) or request_context.get("explicit_plan_request") is not True:
            return None
        planner = getattr(self.router, "plan_teaching_episode", None)
        if not callable(planner):
            return None
        bounded_context = _bounded_value(request_context)
        try:
            payload = planner(request_context=bounded_context)
        except Exception:
            return None
        if not isinstance(payload, Mapping) or _contains_forbidden_fields(payload):
            return None
        return self._normalize(payload)

    @staticmethod
    def _normalize(payload: Mapping[str, Any]) -> TeachingPlan | None:
        raw_goal = payload.get("global_goal")
        if not isinstance(raw_goal, str):
            return None
        global_goal = _clean_text(raw_goal)
        raw_evidence = payload.get("success_evidence")
        if not isinstance(raw_evidence, (list, tuple)):
            return None
        bounded_evidence = raw_evidence[:16]
        if any(not isinstance(item, str) or not item.strip() for item in bounded_evidence):
            return None
        success_evidence = tuple(
            text
            for text in (_clean_text(item, limit=240) for item in bounded_evidence)
            if text
        )
        raw_concepts = payload.get("concepts")
        if not global_goal or not success_evidence or not isinstance(raw_concepts, (list, tuple)):
            return None

        prepared: list[tuple[str, str, str, tuple[str, ...]]] = []
        used_ids: set[str] = set()
        for index, raw in enumerate(raw_concepts[:8]):
            if not isinstance(raw, Mapping):
                return None
            raw_objective = raw.get("objective")
            raw_description = raw.get("description")
            if not isinstance(raw_objective, str) or not isinstance(raw_description, str):
                return None
            objective = _clean_text(raw_objective)
            description = _clean_text(raw_description)
            if not objective or not description:
                return None
            raw_id = raw.get("id", raw.get("concept_id", ""))
            if not isinstance(raw_id, str):
                return None
            concept_id = _unique_concept_id(raw_id, raw, index, used_ids)
            raw_prerequisites = raw.get("prerequisite_ids", raw.get("prerequisites", ()))
            if not isinstance(raw_prerequisites, (list, tuple)):
                return None
            bounded_prerequisites = raw_prerequisites[:16]
            if any(not isinstance(value, str) for value in bounded_prerequisites):
                return None
            prerequisites = tuple(
                item
                for item in (_clean_text(value, limit=120) for value in bounded_prerequisites)
                if item
            )
            prepared.append((concept_id, description, objective, prerequisites))
        if len(prepared) < 3:
            return None

        valid_ids = {item[0] for item in prepared}
        concepts_by_id = {
            concept_id: TeachingConcept(
                concept_id=concept_id,
                description=description,
                objective=objective,
                prerequisites=tuple(
                    prerequisite
                    for prerequisite in dict.fromkeys(prerequisites)
                    if prerequisite in valid_ids and prerequisite != concept_id
                ),
            )
            for concept_id, description, objective, prerequisites in prepared
        }
        ordered: list[TeachingConcept] = []
        resolved: set[str] = set()
        remaining = [item[0] for item in prepared]
        while remaining:
            ready = next(
                (concept_id for concept_id in remaining if set(concepts_by_id[concept_id].prerequisites) <= resolved),
                None,
            )
            if ready is None:
                return None
            ordered.append(concepts_by_id[ready])
            resolved.add(ready)
            remaining.remove(ready)

        raw_hypotheses = payload.get("misconception_hypotheses", ())
        if not isinstance(raw_hypotheses, (list, tuple)):
            return None
        hypotheses: list[MisconceptionHypothesis] = []
        for index, raw in enumerate(raw_hypotheses[:16]):
            if not isinstance(raw, Mapping):
                return None
            raw_description = raw.get("description")
            raw_concept_id = raw.get("concept_id", "")
            raw_hypothesis_id = raw.get("id", raw.get("hypothesis_id", ""))
            confidence = raw.get("confidence", 0.0)
            if (
                not isinstance(raw_description, str)
                or not raw_description.strip()
                or not isinstance(raw_concept_id, str)
                or not isinstance(raw_hypothesis_id, str)
                or isinstance(confidence, bool)
                or not isinstance(confidence, (int, float))
                or not 0.0 <= confidence <= 1.0
            ):
                return None
            description = _clean_text(raw_description)
            concept_id = _clean_text(raw_concept_id, limit=120)
            if concept_id not in valid_ids:
                concept_id = ""
            hypothesis_id = _clean_text(raw_hypothesis_id, limit=120)
            hypotheses.append(
                MisconceptionHypothesis(
                    hypothesis_id=hypothesis_id or _stable_id("misconception", index, description),
                    description=description,
                    concept_id=concept_id,
                    confidence=confidence,
                )
            )

        edges = tuple(
            (prerequisite, concept.concept_id)
            for concept in ordered
            for prerequisite in concept.prerequisites
        )
        raw_example_policy = payload.get("example_policy", "allowed")
        if not isinstance(raw_example_policy, str):
            return None
        try:
            return TeachingPlan(
                global_goal=global_goal,
                concepts=tuple(ordered),
                prerequisite_edges=edges,
                success_evidence=success_evidence,
                misconception_hypotheses=tuple(hypotheses),
                example_policy=_clean_text(raw_example_policy, limit=240) or "allowed",
            )
        except (TypeError, ValueError):
            return None


__all__ = ["TeachingPlanBuilder"]
