# -*- coding: utf-8 -*-
"""Proposal sources chuyển cơ hội runtime thành ActionProposal input có provenance.

Affordance, silence và body-only đều đi qua cùng một adapter. Ingress không còn
biết cách chấm từng nguồn, còn Subject Core vẫn là nơi enrich/rank theo history,
need, value và competence.
"""
from __future__ import annotations

from typing import Any, Mapping, Sequence

from cortex.subject.service import SubjectService
from cortex.subject.expression_commitment import read_commitment
from cortex.active_concern import match_concern


def clamp01(value: Any, default: float = 0.5) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = default
    if number != number:
        number = default
    return max(0.0, min(1.0, number))


class AutonomousProposalSource:
    """Hợp nhất các affordance runtime thành proposal schema của Subject Core."""

    def __init__(self, subject: SubjectService) -> None:
        self._subject = subject

    @staticmethod
    def from_affordance(
        affordance: Any,
        *,
        turn_id: str,
        active_concerns: Sequence[Mapping[str, Any]] = (),
        policy_signal: Mapping[str, Any] | None = None,
        affective_bias: Mapping[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        affordance_id = str(getattr(affordance, "affordance_id", "") or "").strip()
        candidate = getattr(affordance, "candidate", None)
        if not affordance_id or candidate is None:
            return None
        kind = str(getattr(candidate, "kind", "") or "autonomous_speech").strip()
        topic = str(getattr(candidate, "topic", "") or "").strip()
        owner = str(getattr(affordance, "owner", "") or getattr(candidate, "source", "") or "runtime")
        confidence = clamp01(getattr(candidate, "confidence", 0.5))
        relevance = clamp01(getattr(candidate, "relevance", confidence))
        urgency = clamp01(getattr(candidate, "urgency", 0.2), 0.2)
        requires_grounding = bool(getattr(candidate, "requires_grounding", False))
        seed_intrusion = clamp01(getattr(candidate, "seed_intrusion_risk", 0.0), 0.0)
        intrusion = max(
            seed_intrusion,
            clamp01(getattr(candidate, "intrusion_risk", 1.0 - relevance), 1.0 - relevance),
        )
        seed_quality = clamp01(getattr(candidate, "seed_quality_score", 0.0), 0.0)
        seed_specificity = clamp01(getattr(candidate, "seed_specificity", 0.0), 0.0)
        seed_motive_strength = clamp01(getattr(candidate, "seed_motive_strength", 0.0), 0.0)
        seed_semantic_completeness = clamp01(getattr(candidate, "seed_semantic_completeness", 0.0), 0.0)
        seed_id = str(getattr(candidate, "seed_id", "") or "").strip()
        seed_epistemic_mode = str(getattr(candidate, "seed_epistemic_mode", "") or "").strip().lower()
        seed_temporal_context = getattr(candidate, "seed_temporal_context", {})
        seed_temporal_context = (
            dict(seed_temporal_context) if hasattr(seed_temporal_context, "items") else {}
        )
        metadata = getattr(affordance, "metadata", {})
        metadata = metadata if isinstance(metadata, dict) else dict(metadata) if hasattr(metadata, "items") else {}
        intention_id = str(metadata.get("intention_id") or "").strip()
        motive_kind = str(metadata.get("motive_kind") or "").strip().lower()
        raw_expression_commitment = getattr(candidate, "expression_commitment", {})
        expression_commitment = (
            dict(raw_expression_commitment)
            if isinstance(raw_expression_commitment, Mapping)
            and read_commitment({"expression_commitment": raw_expression_commitment}) is not None
            else None
        )
        # V67: đây là public proposal boundary. Opportunity/thought chưa có typed
        # commitment vẫn được sống ở owner của nó, nhưng không được lẻn vào Neuro
        # dưới dạng một speech proposal rồi nhờ ID legacy chứng minh hộ.
        if expression_commitment is None:
            return None
        speech_act = str(getattr(candidate, "speech_act", "") or "").strip().lower()
        tone = str(getattr(candidate, "tone", "") or "").strip().lower()
        expression_urge_id = str(getattr(candidate, "expression_urge_id", "") or "").strip()
        expression_pressure = clamp01(getattr(candidate, "expression_pressure", 0.0), 0.0)
        expression_freshness = clamp01(getattr(candidate, "expression_freshness", 0.0), 0.0)
        expression_satisfaction = clamp01(getattr(candidate, "expression_satisfaction", 0.0), 0.0)
        expression_motive = str(getattr(candidate, "expression_motive", "") or "").strip().lower()
        affect_bias = dict(affective_bias or {})

        need_ids: list[str] = []
        lowered = f"{owner} {kind}".casefold()
        if any(token in lowered for token in ("followup", "continuation", "arc", "memory")):
            need_ids.append("continuity")
        if any(token in lowered for token in ("social", "chat", "seed", "conversation", "relationship", "initiative", "probe", "understand_user")):
            need_ids.append("connection")
        if any(token in lowered for token in ("probe", "understand_user", "curiosity")):
            need_ids.append("coherence")
        if any(token in lowered for token in ("project", "task", "practice", "competence")):
            need_ids.append("competence")
        if requires_grounding or any(token in lowered for token in ("observe", "ground", "inspect")):
            need_ids.append("coherence")
        if not need_ids:
            need_ids.append("connection")

        evidence_refs = tuple(
            str(item)
            for item in (getattr(candidate, "evidence_refs", ()) or ())
            if str(item or "").strip()
        ) or (turn_id, affordance_id)
        policy = dict(policy_signal or {})
        policy_cost = clamp01(policy.get("total_cost", 0.0), 0.0)

        proposal = {
            "proposal_id": f"proposal:{affordance_id}",
            "action_kind": "speak",
            "arguments": {
                "affordance_id": affordance_id,
                "semantic_target": topic,
                "speech_act": speech_act,
                "tone": tone,
                "source_owner": owner,
                "candidate_kind": kind,
                "requires_grounding": requires_grounding,
                "intrusion_risk": intrusion,
                **({
                    "expression_urge_id": expression_urge_id,
                    "expression_pressure": expression_pressure,
                    "expression_freshness": expression_freshness,
                    "expression_satisfaction": expression_satisfaction,
                    "expression_motive": expression_motive,
                } if expression_urge_id else {}),
                **({
                    "seed_id": seed_id,
                    "seed_quality": seed_quality,
                    "seed_specificity": seed_specificity,
                    "seed_motive_strength": seed_motive_strength,
                    "seed_semantic_completeness": seed_semantic_completeness,
                    "seed_epistemic_mode": seed_epistemic_mode,
                    "seed_temporal_context": seed_temporal_context,
                    "claim_contract": {
                        "current_environment_requires_direct_evidence": True,
                        "surface_may_not_add_new_current_world_premises": True,
                        "runtime_clock_scope": "clock_calendar_only",
                    },
                } if seed_id else {}),
                # Giữ binding của Subject intention xuyên qua adapter runtime.
                # V26 từng làm rơi hai field này, khiến public act không thể đóng
                # đúng lifecycle và frame sau dễ quay lại proposal seed cũ.
                **({"intention_id": intention_id} if intention_id else {}),
                **({"initiative_motive": motive_kind} if motive_kind else {}),
                **({"expression_commitment": expression_commitment} if expression_commitment else {}),
                "context": "autonomous",
                "skill_id": "dialogue",
                # V67: policy signal chỉ còn telemetry self-density/repetition.
                # Nó không được đổi quyền biểu đạt của một typed commitment.
                "initiative_policy_cost": 0.0,
                "initiative_policy_signal": policy,
                "affective_selection_bias": {
                    key: round(clamp01(value, 0.0), 3)
                    for key, value in affect_bias.items()
                    if key in {"exploration_pull", "playful_pull", "social_withdrawal", "friction_salience"}
                },
            },
            "supporting_need_ids": tuple(dict.fromkeys(need_ids)),
            "expected_need_relief": max(
                0.05,
                relevance * (0.76 + 0.24 * seed_quality) if seed_id else relevance,
            ),
            "expected_action_efficacy": (
                confidence * (0.78 + 0.22 * seed_semantic_completeness) if seed_id else confidence
            ),
            "expected_epistemic_value": (
                0.62 if requires_grounding else (0.12 + 0.16 * seed_specificity if seed_id else 0.16)
            ),
            "value_congruence": (0.42 + 0.18 * seed_motive_strength if seed_id else 0.50),
            "substrate_cost": min(1.0, 0.12 + 0.45 * intrusion),
            "uncertainty": max(
                1.0 - confidence,
                clamp01(getattr(candidate, "uncertainty", 0.0), 0.0),
                (0.34 * (1.0 - seed_quality) if seed_id else 0.0),
            ),
            "project_contribution": 0.55 if "competence" in need_ids else 0.0,
            "historical_success": 0.50,
            "commitment_conflict": max(0.0, intrusion - urgency * 0.25),
            "evidence_refs": evidence_refs,
            "need_effects": {
                **({"connection": 0.72, "sovereignty": -min(0.30, intrusion * 0.35)} if "connection" in need_ids else {}),
                **({"coherence": 0.62} if "coherence" in need_ids else {}),
                **({"competence": 0.54} if "competence" in need_ids else {}),
                **({"continuity": 0.58} if "continuity" in need_ids else {}),
            },
        }

        # V66: commitment đã quyết định action family. Pressure/freshness chỉ
        # xếp hạng giữa NHIỀU điều Eris cùng muốn nói, không cạnh tranh quyền
        # được nói với SILENCE.
        if expression_commitment:
            commitment_pressure = clamp01(expression_commitment.get("pressure", expression_pressure), expression_pressure)
            commitment_freshness = clamp01(expression_commitment.get("freshness", expression_freshness), expression_freshness)
            commitment_persistence = clamp01(expression_commitment.get("persistence", 0.5), 0.5)
            proposal["expected_need_relief"] = clamp01(
                float(proposal["expected_need_relief"]) + 0.22 * commitment_pressure
            )
            proposal["value_congruence"] = clamp01(
                float(proposal["value_congruence"]) + 0.14 * commitment_persistence
            )
            proposal["expected_action_efficacy"] = clamp01(
                float(proposal["expected_action_efficacy"]) + 0.07 * commitment_freshness
            )


        # V43: mood chỉ bias selection nhẹ trước Neuro. Không action veto, không
        # hạ grounding threshold, không cho playful mood tự cấp quyền nói.
        exploration_pull = clamp01(affect_bias.get("exploration_pull", 0.0), 0.0)
        playful_pull = clamp01(affect_bias.get("playful_pull", 0.0), 0.0)
        social_withdrawal = clamp01(affect_bias.get("social_withdrawal", 0.0), 0.0)
        friction_salience = clamp01(affect_bias.get("friction_salience", 0.0), 0.0)
        semantic_shape = f"{owner} {kind} {speech_act} {tone}".casefold()
        if any(token in semantic_shape for token in ("curiosity", "probe", "investigat", "question", "association", "explore")):
            proposal["expected_epistemic_value"] = clamp01(float(proposal["expected_epistemic_value"]) + 0.08 * exploration_pull)
            proposal["expected_action_efficacy"] = clamp01(float(proposal["expected_action_efficacy"]) + 0.03 * exploration_pull)
        if any(token in semantic_shape for token in ("play", "tease", "joke", "banter", "humor")):
            proposal["expected_need_relief"] = clamp01(float(proposal["expected_need_relief"]) + 0.055 * playful_pull)
        if any(token in semantic_shape for token in ("repair", "correct", "contradict", "clarif")):
            proposal["expected_epistemic_value"] = clamp01(float(proposal["expected_epistemic_value"]) + 0.055 * friction_salience)
        if social_withdrawal > 0.0:
            proposal["substrate_cost"] = clamp01(float(proposal["substrate_cost"]) + 0.085 * social_withdrawal)
            proposal["commitment_conflict"] = clamp01(float(proposal["commitment_conflict"]) + 0.055 * social_withdrawal)

        # V40: proposal được gắn với concern đang sống nếu semantic thực sự trùng.
        # Concern không override action; nó chỉ làm Neuro thấy continuity, novelty
        # và habituation trước commit, thay vì downstream mới phát hiện "lại nói nữa".
        matched, match_score = match_concern(topic, active_concerns)
        if matched is not None:
            activation = clamp01(matched.get("activation", 0.0), 0.0)
            novelty = clamp01(matched.get("novelty", 0.0), 0.0)
            habituation = clamp01(matched.get("habituation", 0.0), 0.0)
            speech_pressure = clamp01(matched.get("speech_pressure", 0.0), 0.0)
            age_seconds = max(0.0, float(matched.get("age_seconds", 0.0) or 0.0))
            mutation_count = max(0, int(matched.get("mutation_count", 0) or 0))
            concern_id = str(matched.get("concern_id") or "").strip()
            proposal["arguments"].update({
                "active_concern_id": concern_id,
                "concern_match": round(match_score, 3),
                "concern_state": str(matched.get("state") or "simmering"),
                "concern_trajectory": str(matched.get("trajectory") or "stable"),
                "concern_activation": activation,
                "concern_novelty": novelty,
                "concern_habituation": habituation,
                "concern_speech_pressure": speech_pressure,
                "concern_mutation_count": mutation_count,
            })
            if "continuity" not in proposal["supporting_need_ids"]:
                proposal["supporting_need_ids"] = tuple((*proposal["supporting_need_ids"], "continuity"))
            # Fresh seed mới nhập concern nên được "ủ" một chút nếu không gấp. Đây
            # là bias mềm, không hard-block: urgency cao vẫn có thể thắng.
            incubation = 0.10 if age_seconds < 20.0 and urgency < 0.55 and mutation_count == 0 else 0.0
            proposal["expected_need_relief"] = clamp01(
                float(proposal["expected_need_relief"]) * (0.76 + 0.34 * speech_pressure)
            )
            proposal["expected_action_efficacy"] = clamp01(
                float(proposal["expected_action_efficacy"]) * (0.84 + 0.20 * novelty)
            )
            proposal["substrate_cost"] = clamp01(
                float(proposal["substrate_cost"]) + 0.20 * habituation + incubation
            )
            proposal["commitment_conflict"] = clamp01(
                float(proposal["commitment_conflict"]) + 0.32 * habituation + incubation
            )
            proposal["need_effects"]["continuity"] = max(
                float(proposal["need_effects"].get("continuity", 0.0) or 0.0),
                0.30 + 0.34 * activation,
            )
        return proposal

    def collect(
        self,
        blackboard: Any,
        affordances: Sequence[Any],
        *,
        turn_id: str,
        planning_context: Mapping[str, Any] | None = None,
    ) -> tuple[tuple[dict[str, Any], ...], dict[str, Any]]:
        context = dict(planning_context or {})
        raw_concerns = context.get("active_concerns")
        if not isinstance(raw_concerns, (list, tuple)):
            self_context = context.get("self_context") if isinstance(context.get("self_context"), Mapping) else {}
            raw_concerns = self_context.get("active_concerns", ()) if isinstance(self_context, Mapping) else ()
        active_concerns = tuple(item for item in tuple(raw_concerns or ()) if isinstance(item, Mapping))
        raw_policy_signals = context.get("initiative_policy_signals")
        policy_signals = raw_policy_signals if isinstance(raw_policy_signals, Mapping) else {}
        raw_affective_bias = context.get("affective_selection_bias")
        affective_bias = raw_affective_bias if isinstance(raw_affective_bias, Mapping) else {}
        proposals = [
            item
            for item in (
                self.from_affordance(
                    affordance,
                    turn_id=turn_id,
                    active_concerns=active_concerns,
                    policy_signal=(
                        policy_signals.get(str(getattr(affordance, "affordance_id", "") or ""), {})
                        if isinstance(policy_signals, Mapping)
                        else {}
                    ),
                    affective_bias=affective_bias,
                )
                for affordance in affordances
            )
            if item is not None
        ]
        public_view = blackboard.public_view() if callable(getattr(blackboard, "public_view", None)) else {}
        reply_expected = bool(public_view.get("reply_expected", getattr(blackboard, "reply_expected", False)))
        user_busy = bool(getattr(blackboard, "user_busy", False))
        # V67: reply_expected/user_busy chỉ còn metadata cho discourse/wording.
        # Chúng không thay đổi score của SILENCE/BODY_ONLY hay speech commitment.
        engagement_active = bool(public_view.get("engagement_active", getattr(blackboard, "engagement_active", False)))

        # V67: capability speech không còn là một motive độc lập. Nó chỉ là
        # realization path cho một ExpressionCommitment đã tồn tại và thật sự
        # cần grounding. Không commitment => không có public capability speech.
        grounding_source = next(
            (
                proposal for proposal in proposals
                if proposal.get("action_kind") == "speak"
                and bool(dict(proposal.get("arguments", {}) or {}).get("requires_grounding", False))
                and read_commitment(dict(proposal.get("arguments", {}) or {})) is not None
            ),
            None,
        )
        if grounding_source is not None:
            source_args = dict(grounding_source.get("arguments", {}) or {})
            proposals.append({
                "proposal_id": f"proposal:capability-grounded-speech:{turn_id}",
                "action_kind": "speak",
                "arguments": {
                    "context": "autonomous",
                    "capability_route": True,
                    "reason_family": "ground_committed_public_claim",
                    "skill_id": "investigation",
                    "semantic_target": source_args.get("semantic_target", ""),
                    "speech_act": source_args.get("speech_act", ""),
                    "tone": source_args.get("tone", ""),
                    "expression_commitment": dict(source_args.get("expression_commitment", {}) or {}),
                },
                "supporting_need_ids": ("coherence", "competence"),
                "expected_need_relief": 0.58,
                "expected_action_efficacy": clamp01(self._subject.substrate.action_efficacy, 0.5),
                "expected_epistemic_value": 0.78,
                "value_congruence": 0.50,
                "substrate_cost": max(0.08, 1.0 - self._subject.substrate.processing_capacity),
                "uncertainty": 0.38,
                "project_contribution": self._subject.substrate.project_viability * 0.35,
                "historical_success": 0.50,
                "commitment_conflict": 0.08,
                "evidence_refs": tuple(grounding_source.get("evidence_refs", ())) or (turn_id,),
                "need_effects": {"coherence": 0.70, "competence": 0.48},
            })
        proposals.extend((
            {
                "proposal_id": f"proposal:silence:{turn_id}",
                "action_kind": "silence",
                "arguments": {"context": "autonomous", "reason_family": "low_pressure_wait"},
                "supporting_need_ids": ("sovereignty", "coherence"),
                "expected_need_relief": 0.44,
                "expected_action_efficacy": 0.96,
                "expected_epistemic_value": 0.24,
                "value_congruence": 0.50,
                "substrate_cost": 0.01,
                "uncertainty": 0.10,
                "historical_success": 0.50,
                "commitment_conflict": 0.04,
                "evidence_refs": (turn_id,),
                "need_effects": {
                    "sovereignty": 0.42,
                    "coherence": 0.22,
                    "connection": -0.72,
                },
            },
            {
                "proposal_id": f"proposal:body-only:{turn_id}",
                "action_kind": "body_only",
                "arguments": {"context": "autonomous", "reason_family": "nonverbal_presence"},
                "supporting_need_ids": ("connection", "sovereignty"),
                "expected_need_relief": 0.32 if not engagement_active else 0.16,
                "expected_action_efficacy": 0.80,
                "expected_epistemic_value": 0.08,
                "value_congruence": 0.50,
                "substrate_cost": 0.04,
                "uncertainty": 0.18,
                "historical_success": 0.50,
                "commitment_conflict": 0.02,
                "evidence_refs": (turn_id,),
                "need_effects": {"connection": 0.24, "sovereignty": 0.30},
            },
        ))
        return tuple(proposals), {
            "reply_expected": reply_expected,
            "user_busy": user_busy,
            "engagement_active": engagement_active,
            "novelty": clamp01(public_view.get("novelty", 0.30), 0.30),
        }


__all__ = ["AutonomousProposalSource", "clamp01"]
