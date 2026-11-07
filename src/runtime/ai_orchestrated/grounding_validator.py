# -*- coding: utf-8 -*-
"""Grounding boundary riêng cho autonomous speech.

Validator chạy hai lần:

1. preflight: trước khi gọi Surface Worker, kiểm tra SpeechIntent có quyền dùng
   anchor/evidence đó hay không;
2. postflight: sau khi có text, dùng grounding gate chung để chặn claim vượt
   quá evidence.

Surface Worker có quyền viết câu. Nó không có quyền tự cấp giác quan cho mình.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from runtime.grounding_surface_gate import evaluate_grounded_surface

from .cognitive_blackboard import TypedCognitiveBlackboard
from .initiative import SpeechIntent


@dataclass(frozen=True, slots=True)
class GroundingDecision:
    allowed: bool
    reason: str
    violation_code: str = ""


class AutonomousGroundingValidator:
    """Kiểm tra provenance trước và sau model call."""

    def __init__(self, *, logger: Any | None = None) -> None:
        self.logger = logger

    def validate_intent(
        self,
        intent: SpeechIntent,
        blackboard: TypedCognitiveBlackboard,
    ) -> GroundingDecision:
        scope = str(intent.plan.semantics.evidence_scope or "").strip().lower()
        if blackboard.privacy_blocked and scope.startswith("screen"):
            return GroundingDecision(False, "screen_privacy_blocked", "screen_privacy_blocked")
        if intent.anchor_type in {"screen", "activity"}:
            # Raw window title/OCR delta chỉ đủ cho body gesture. Public speech
            # bắt buộc có grounded claim và evidence id từ VisionGroundingPolicy.
            if blackboard.vision_grounding_recommendation != "speak":
                return GroundingDecision(False, "vision_grounding_recommends_silence", "ungrounded_screen_anchor")
            available = set(blackboard.grounded_screen_evidence_refs)
            if not blackboard.grounded_screen_claims or not available:
                return GroundingDecision(False, "screen_without_grounded_claim", "missing_grounded_screen_claim")
            if not intent.evidence_refs:
                return GroundingDecision(False, "screen_without_evidence_ref", "missing_screen_evidence")
            unknown = [ref for ref in intent.evidence_refs if ref not in available]
            if unknown:
                return GroundingDecision(False, "unknown_screen_evidence_ref", "unknown_evidence_ref")
        if intent.anchor_type == "semantic_scene":
            # Packet semantic có provenance riêng: mỗi fact được ánh xạ thành một
            # evidence ref ổn định theo scene. Surface chỉ được dùng refs thuộc
            # packet hiện hành, không được mượn packet cũ sau lúc đổi tab.
            if not blackboard.semantic_context_ready:
                return GroundingDecision(False, "semantic_context_not_ready", "missing_semantic_context")
            available = set(blackboard.semantic_evidence_refs)
            if intent.evidence_refs and not set(intent.evidence_refs).issubset(available):
                return GroundingDecision(False, "unknown_semantic_evidence_ref", "unknown_evidence_ref")
        # V42: reply baton là social/timing feature của Neuro, không phải
        # grounding rule. Validator sau commit chỉ kiểm provenance/safety.
        if intent.anchor_type == "self" and not blackboard.public_seed_ready:
            return GroundingDecision(False, "private_seed_not_public", "private_cognition_boundary")
        if intent.anchor_type == "memory":
            if not blackboard.memory_available:
                return GroundingDecision(False, "memory_not_available", "missing_memory_provenance")
            if not intent.evidence_refs:
                return GroundingDecision(False, "memory_without_evidence", "missing_memory_provenance")
        if intent.anchor_type == "discovery" and not blackboard.discovery_ready:
            return GroundingDecision(False, "discovery_not_ready", "missing_discovery_artifact")
        if intent.anchor_type == "task" and not blackboard.engagement_active:
            return GroundingDecision(False, "engagement_not_active", "missing_engagement")
        return GroundingDecision(True, "intent_grounded")

    def validate_surface(
        self,
        text: str,
        *,
        report: Any,
        engine: Any,
        channel: str = "",
    ) -> GroundingDecision:
        verdict = evaluate_grounded_surface(engine, str(text or ""), report=report, channel=channel)
        if bool(getattr(verdict, "allowed", False)):
            return GroundingDecision(True, "surface_grounded")
        code = str(getattr(verdict, "violation_code", "") or "grounding_blocked")
        return GroundingDecision(False, code, code)


__all__ = ["AutonomousGroundingValidator", "GroundingDecision"]
