# -*- coding: utf-8 -*-
"""Model-backed Lane 3 synthesizer.

Đây là lớp làm phần "phiên toà nội tâm": các ThoughtFragment vẫn do worker rẻ
đưa lên Blackboard, nhưng Lane 3 có thể gọi API để nén chúng thành một ThoughtObject
có evidence rõ hơn, có bên công tố/biện hộ/thẩm phán trong log dev.

Quan trọng:
- Không sinh public text.
- Không được tự tin hơn evidence.
- Nếu model bịa evidence id, bỏ evidence đó.
- Nếu model fail, fallback về ConsciousSynthesizer deterministic.
"""

from __future__ import annotations
from core.di import resolve_dependency

from dataclasses import dataclass, field
import json
import os
import re
from typing import Any, Iterable, Mapping

from .blackboard import BlackboardSnapshot, SubconsciousBlackboard
from .synthesizer import ConsciousSynthesizer, SynthesisPolicy, SynthesisReport
from .thought_fragment import (
    PrivacyLevel,
    RecommendedRoute,
    ThoughtFragment,
    ThoughtObject,
    clamp01,
    clean_text,
    make_object_id,
    normalize_privacy_level,
    normalize_route,
    normalize_string_list,
    now_timestamp,
)


def _env_flag(name: str, default: bool) -> bool:
    raw = str(os.getenv(name, "1" if default else "0") or "").strip().lower()
    if not raw:
        return default
    return raw not in {"0", "false", "no", "off", "none"}


def _as_float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


@dataclass(slots=True, frozen=True)
class ModelSynthesisPolicy:
    """Knobs riêng cho Lane 3 API synthesis."""

    enabled: bool = field(default_factory=lambda: _env_flag("ERIS_LANE3_MODEL_SYNTHESIS", True))
    min_fragments: int = field(default_factory=lambda: max(2, int(_as_float(os.getenv("ERIS_LANE3_MIN_FRAGMENTS", "3"), 3))))
    min_board_pressure: float = field(default_factory=lambda: max(0.0, min(1.0, _as_float(os.getenv("ERIS_LANE3_MIN_PRESSURE", "0.34"), 0.34))))
    max_fragments_for_prompt: int = field(default_factory=lambda: max(3, int(_as_float(os.getenv("ERIS_LANE3_PROMPT_FRAGMENTS", "10"), 10))))
    log_api_thoughts: bool = field(default_factory=lambda: _env_flag("ERIS_LANE3_LOG_API_THOUGHTS", True))
    allow_private_summary_log: bool = field(default_factory=lambda: _env_flag("ERIS_LANE3_LOG_PRIVATE_SUMMARY", True))


@dataclass(slots=True, frozen=True)
class InnerCourtReport:
    """Debug report cho phiên tòa Lane 3."""

    ok: bool
    provider: str = ""
    model: str = ""
    verdict: str = ""
    prosecutor: str = ""
    defense: str = ""
    judge: str = ""
    dominant_theme: str = ""
    confidence: float = 0.0
    route: str = ""
    evidence_ids: tuple[str, ...] = ()
    error: str = ""
    fallback_used: bool = False

    def to_dict(self) -> dict[str, object]:
        return {
            "ok": self.ok,
            "provider": self.provider,
            "model": self.model,
            "verdict": self.verdict,
            "prosecutor": self.prosecutor,
            "defense": self.defense,
            "judge": self.judge,
            "dominant_theme": self.dominant_theme,
            "confidence": round(float(self.confidence), 3),
            "route": self.route,
            "evidence_ids": list(self.evidence_ids),
            "error": self.error,
            "fallback_used": self.fallback_used,
        }


class ModelBackedConsciousSynthesizer(ConsciousSynthesizer):
    """ConsciousSynthesizer có thêm Lane 3 API refinement."""

    def __init__(
        self,
        *,
        model_adapter: Any | None = None,
        policy: SynthesisPolicy | None = None,
        model_policy: ModelSynthesisPolicy | None = None,
        logger: Any | None = None,
    ) -> None:
        super().__init__(policy=policy)
        self.model_adapter = model_adapter
        self.model_policy = resolve_dependency(model_policy, lambda: ModelSynthesisPolicy())
        self.logger = logger
        self.last_inner_court_report = InnerCourtReport(ok=False, verdict="not_started")

    def synthesize_from_blackboard(
        self,
        blackboard: SubconsciousBlackboard,
        *,
        now: float | None = None,
    ) -> ThoughtObject | None:
        snapshot = blackboard.snapshot(now=now)
        return self.synthesize(snapshot)

    def synthesize(self, snapshot: BlackboardSnapshot | Iterable[ThoughtFragment]) -> ThoughtObject | None:
        deterministic = super().synthesize(snapshot)
        if deterministic is None:
            self.last_inner_court_report = InnerCourtReport(ok=False, verdict="no_deterministic_object", fallback_used=True)
            return None

        fragments, now = self._extract_live_fragments(snapshot)
        if not self._should_call_model(fragments, now=now):
            self.last_inner_court_report = InnerCourtReport(
                ok=False,
                verdict="below_lane3_threshold",
                dominant_theme=deterministic.dominant_theme,
                confidence=deterministic.confidence,
                route=deterministic.recommended_route,
                evidence_ids=deterministic.evidence_ids,
                fallback_used=True,
            )
            return deterministic

        result = self._call_model(deterministic, fragments, now=now)
        if result is None:
            return deterministic
        return result

    def _extract_live_fragments(self, snapshot: BlackboardSnapshot | Iterable[ThoughtFragment]) -> tuple[tuple[ThoughtFragment, ...], float]:
        if isinstance(snapshot, BlackboardSnapshot):
            now = float(snapshot.created_at or now_timestamp())
            fragments = tuple(snapshot.fragments)
        else:
            fragments = tuple(snapshot)
            now = max((float(fragment.updated_at or fragment.created_at or 0.0) for fragment in fragments), default=now_timestamp())
            if now <= 0.0:
                now = now_timestamp()
        live = tuple(fragment for fragment in fragments if not fragment.is_expired(now) and fragment.priority_score(now) > 0.02)
        return live, now

    def _should_call_model(self, fragments: tuple[ThoughtFragment, ...], *, now: float) -> bool:
        if not self.model_policy.enabled or self.model_adapter is None:
            return False
        if len(fragments) < int(self.model_policy.min_fragments):
            return False
        pressure = max((fragment.priority_score(now) for fragment in fragments), default=0.0)
        if pressure < float(self.model_policy.min_board_pressure):
            return False
        return True

    def _call_model(self, base_object: ThoughtObject, fragments: tuple[ThoughtFragment, ...], *, now: float) -> ThoughtObject | None:
        messages = self._build_messages(base_object, fragments, now=now)
        synthesize = getattr(self.model_adapter, "synthesize", None)
        if not callable(synthesize):
            self.last_inner_court_report = InnerCourtReport(ok=False, verdict="missing_adapter_method", fallback_used=True)
            return None

        result = synthesize(messages)
        provider = str(getattr(result, "provider", "") or "")
        model = str(getattr(result, "model", "") or "")
        if not getattr(result, "ok", False):
            error = str(getattr(result, "error", "unknown") or "unknown")
            self.last_inner_court_report = InnerCourtReport(
                ok=False,
                provider=provider,
                model=model,
                verdict="api_failed",
                error=error,
                fallback_used=True,
            )
            self._log("warning", "[Lane3 API] inner_court failed error=%s fallback=deterministic", error)
            return None

        parsed = dict(getattr(result, "parsed", {}) or {})
        object_ = self._object_from_model_payload(base_object, fragments, parsed, now=now)
        report = self._report_from_payload(parsed, object_, provider=provider, model=model)
        self.last_inner_court_report = report
        self.last_report = SynthesisReport(
            object=object_,
            considered_fragment_ids=tuple(fragment.id for fragment in fragments),
            dominant_group_key="api_inner_court",
            route_reason="api_model_inner_court_with_evidence_gate",
            suppressed_fragment_ids=tuple(fragment.id for fragment in fragments if fragment.id not in set(object_.fragments)),
        )
        self._log_inner_court(report, parsed)
        return object_

    def _build_messages(self, base_object: ThoughtObject, fragments: tuple[ThoughtFragment, ...], *, now: float) -> list[dict[str, str]]:
        selected = tuple(sorted(fragments, key=lambda item: item.priority_score(now), reverse=True))[: self.model_policy.max_fragments_for_prompt]
        fragment_payload = []
        for fragment in selected:
            fragment_payload.append(
                {
                    "id": fragment.id,
                    "source_agent": fragment.source_agent,
                    "kind": fragment.kind,
                    "content": fragment.content,
                    "evidence_ids": list(fragment.evidence_ids),
                    "confidence": round(float(fragment.confidence), 3),
                    "urgency": round(float(fragment.urgency), 3),
                    "affect_charge": round(float(fragment.affect_charge), 3),
                    "privacy_level": fragment.privacy_level,
                    "surface_allowed": bool(fragment.surface_allowed),
                    "tags": list(fragment.tags),
                    "priority_score": round(float(fragment.priority_score(now)), 3),
                }
            )

        allowed_evidence = sorted({evidence for fragment in selected for evidence in fragment.evidence_ids})
        payload = {
            "task": "lane3_private_inner_court_synthesis",
            "rules": [
                "Return one compact JSON object only.",
                "Do not write public dialogue.",
                "Use only provided fragment ids and evidence ids.",
                "If evidence is weak, lower confidence and choose store/rehearse/suppress, not surface_candidate.",
                "Do not invent screen facts, memories, feelings, or user intent.",
                "Treat prosecutor/defense/judge as private audit notes, not speech.",
            ],
            "allowed_routes": ["suppress", "store", "rehearse", "surface_candidate"],
            "allowed_privacy_levels": ["public", "normal", "private", "sensitive"],
            "required_schema": {
                "dominant_theme": "short theme",
                "summary": "private synthesis anchored to evidence",
                "prosecutor_case": "strongest reason this thought matters",
                "defense_case": "strongest reason to restrain/suppress",
                "judge_verdict": "final private verdict",
                "hypotheses": [{"label": "...", "confidence": 0.0, "evidence_ids": ["..."]}],
                "conflict": "main conflict or empty",
                "missing_evidence": ["..."] ,
                "recommended_route": "suppress|store|rehearse|surface_candidate",
                "confidence": 0.0,
                "surface_pressure": 0.0,
                "suppressed_pressure": 0.0,
                "privacy_level": "public|normal|private|sensitive",
                "evidence_ids": ["allowed evidence ids only"],
                "fragment_ids": ["selected fragment ids only"],
                "tags": ["short tags"],
            },
            "deterministic_baseline": base_object.to_dict(),
            "allowed_evidence_ids": allowed_evidence,
            "fragments": fragment_payload,
        }
        system = (
            "You are ERIS Lane 3 private synthesis. You are not a chatbot response generator. "
            "Act as an inner court: prosecutor tests salience, defense tests restraint, judge emits one evidence-grounded ThoughtObject. "
            "Return strict JSON only. No markdown. No public-facing prose."
        )
        return [
            {"role": "system", "content": system},
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False, separators=(",", ":"))},
        ]

    def _object_from_model_payload(
        self,
        base_object: ThoughtObject,
        fragments: tuple[ThoughtFragment, ...],
        payload: Mapping[str, Any],
        *,
        now: float,
    ) -> ThoughtObject:
        allowed_fragment_ids = {fragment.id for fragment in fragments}
        allowed_evidence_ids = {evidence for fragment in fragments for evidence in fragment.evidence_ids}
        fragment_ids = tuple(item for item in normalize_string_list(payload.get("fragment_ids"), limit=32, item_limit=180) if item in allowed_fragment_ids)
        if not fragment_ids:
            fragment_ids = base_object.fragments

        model_evidence = tuple(item for item in normalize_string_list(payload.get("evidence_ids"), limit=32, item_limit=140) if item in allowed_evidence_ids)
        evidence_ids = model_evidence or base_object.evidence_ids
        theme = clean_text(payload.get("dominant_theme") or base_object.dominant_theme, limit=160)
        summary = clean_text(payload.get("summary") or payload.get("judge_verdict") or base_object.summary, limit=700)
        if not summary:
            summary = base_object.summary
        confidence = clamp01(payload.get("confidence", base_object.confidence), base_object.confidence)
        surface_pressure = clamp01(payload.get("surface_pressure", base_object.surface_pressure), base_object.surface_pressure)
        suppressed_pressure = clamp01(payload.get("suppressed_pressure", base_object.suppressed_pressure), base_object.suppressed_pressure)
        privacy_level = self._merged_privacy(base_object.privacy_level, payload.get("privacy_level", base_object.privacy_level))
        tags = tuple(dict.fromkeys((*base_object.tags, *normalize_string_list(payload.get("tags"), limit=12, item_limit=80), "lane3_api")))[:24]
        route = self._safe_route(
            payload.get("recommended_route", base_object.recommended_route),
            confidence=confidence,
            surface_pressure=surface_pressure,
            suppressed_pressure=suppressed_pressure,
            evidence_ids=evidence_ids,
            privacy_level=privacy_level,
        )
        return ThoughtObject(
            id=make_object_id(fragment_ids, theme, now),
            dominant_theme=theme or base_object.dominant_theme,
            fragments=fragment_ids,
            summary=summary,
            confidence=confidence,
            surface_pressure=surface_pressure,
            suppressed_pressure=suppressed_pressure,
            evidence_ids=evidence_ids,
            recommended_route=route,
            privacy_level=privacy_level,
            tags=tags,
            created_at=now,
        )

    def _safe_route(
        self,
        raw_route: Any,
        *,
        confidence: float,
        surface_pressure: float,
        suppressed_pressure: float,
        evidence_ids: tuple[str, ...],
        privacy_level: PrivacyLevel,
    ) -> RecommendedRoute:
        try:
            route = normalize_route(raw_route)
        except Exception:
            route = "store"

        if privacy_level in {"private", "sensitive"}:
            return "rehearse" if suppressed_pressure >= self.policy.rehearsal_band else "store"
        if self.policy.require_evidence_for_surface and not evidence_ids:
            return "store" if confidence >= self.policy.min_confidence_for_store else "suppress"
        if route == "surface_candidate":
            if confidence < self.policy.min_confidence_for_surface or surface_pressure < self.policy.min_pressure_for_surface:
                return "rehearse" if suppressed_pressure >= self.policy.rehearsal_band else "store"
        return route

    def _merged_privacy(self, base: Any, model: Any) -> PrivacyLevel:
        order = {"public": 0, "normal": 1, "private": 2, "sensitive": 3}
        try:
            base_level = normalize_privacy_level(base)
        except Exception:
            base_level = "normal"
        try:
            model_level = normalize_privacy_level(model)
        except Exception:
            model_level = base_level
        return model_level if order.get(model_level, 1) >= order.get(base_level, 1) else base_level

    def _report_from_payload(self, payload: Mapping[str, Any], object_: ThoughtObject, *, provider: str, model: str) -> InnerCourtReport:
        return InnerCourtReport(
            ok=True,
            provider=provider,
            model=model,
            verdict=clean_text(payload.get("judge_verdict") or object_.summary, limit=260),
            prosecutor=clean_text(payload.get("prosecutor_case"), limit=260),
            defense=clean_text(payload.get("defense_case"), limit=260),
            judge=clean_text(payload.get("judge_verdict"), limit=260),
            dominant_theme=object_.dominant_theme,
            confidence=object_.confidence,
            route=object_.recommended_route,
            evidence_ids=object_.evidence_ids,
            fallback_used=False,
        )

    def _log_inner_court(self, report: InnerCourtReport, payload: Mapping[str, Any]) -> None:
        if not self.model_policy.log_api_thoughts:
            return
        evidence_count = len(tuple(report.evidence_ids or ()))
        self._log(
            "info",
            "[Lane3 API] inner_court verdict route=%s conf=%.2f theme=%s evidence=%d provider=%s model=%s",
            report.route,
            report.confidence,
            report.dominant_theme,
            evidence_count,
            report.provider,
            report.model,
        )
        if self.model_policy.allow_private_summary_log:
            if report.prosecutor:
                self._log("info", "[Lane3 API] prosecutor_piece: %s", report.prosecutor)
            if report.defense:
                self._log("info", "[Lane3 API] defense_piece: %s", report.defense)
            if report.judge:
                self._log("info", "[Lane3 API] judge_piece: %s", report.judge)
        hypotheses = payload.get("hypotheses")
        if isinstance(hypotheses, list):
            for index, item in enumerate(hypotheses[:3], start=1):
                if not isinstance(item, Mapping):
                    continue
                label = clean_text(item.get("label"), limit=120)
                confidence = clamp01(item.get("confidence", 0.0))
                evidence = normalize_string_list(item.get("evidence_ids"), limit=4, item_limit=80)
                if label:
                    self._log("info", "[Lane3 API] thought_piece#%d conf=%.2f label=%s evidence=%s", index, confidence, label, list(evidence))

    def _log(self, level: str, msg: str, *args: Any) -> None:
        fn = getattr(self.logger, level, None) if self.logger is not None else None
        if callable(fn):
            fn(msg, *args)
