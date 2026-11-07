from __future__ import annotations

import time
from typing import Any, Mapping

from cortex.epistemic import (
    ActionCandidate,
    CognitiveEpisode,
    EpisodeFeedback,
    EpistemicKernel,
    Hypothesis,
)
from cortex.epistemic.contracts import BeliefHorizon, clamp01, compact_text


def prepare_authoritative_action(kernel: EpistemicKernel, frame: Any) -> ActionCandidate | None:
    observations, hypothesis_set = _build_epistemic_context(kernel, frame)
    actual_action = _action_from_frame(frame)
    has_user_message = any(getattr(item, "kind", "") == "user_message" for item in observations)
    candidates = _action_alternatives(actual_action) if str(getattr(frame, "channel", "")) == "chat" and has_user_message else (actual_action,)
    recommended = kernel.select_action(candidates, hypothesis_set=hypothesis_set)
    prepared = {
        "observations": observations,
        "hypothesis_set": hypothesis_set,
        "actual_action_id": actual_action.id,
        "recommended_action_id": recommended.id if recommended else "",
        "action_diff": bool(recommended and recommended.id != actual_action.id),
    }
    frame.epistemic_episode = prepared
    if kernel.authoritative and recommended is not None and recommended.id != actual_action.id:
        _apply_recommendation(frame, recommended, hypothesis_set.uncertainty)
    return recommended


def start_episode(kernel: EpistemicKernel, frame: Any) -> CognitiveEpisode:
    prepared = getattr(frame, "epistemic_episode", None)
    if not isinstance(prepared, Mapping) or "hypothesis_set" not in prepared:
        prepare_authoritative_action(kernel, frame)
        prepared = getattr(frame, "epistemic_episode", {})
    observations = tuple(prepared.get("observations", ()))
    hypothesis_set = prepared.get("hypothesis_set")
    actual_action = _action_from_frame(frame)
    metadata = {
        "mode": kernel.store.mode,
        "actual_action_id": actual_action.id,
        "recommended_action_id": str(prepared.get("recommended_action_id", "")),
        "action_diff": bool(prepared.get("action_diff", False)),
    }
    frame_id = str(getattr(frame, "cycle_id", "") or "")
    channel = str(getattr(frame, "channel", "unknown") or "unknown")

    episode = kernel.start_episode(
        channel=channel,
        observations=observations,
        hypothesis_set=hypothesis_set,
        action=actual_action,
        frame_id=frame_id,
        expected_outcome="helpful_without_intrusion",
        metadata=metadata,
    )
    try:
        frame.epistemic_episode = episode
        frame.debug_notes.append(
            f"epistemic_v2={kernel.store.mode} uncertainty={hypothesis_set.uncertainty:.2f} action_diff={int(metadata['action_diff'])}"
        )
    except Exception:
        pass
    # User tự khai trực tiếp -> ghi belief ngay khi mở episode (không chờ acceptance),
    # để bản sắc về user tích lũy qua phiên (#4). Không bao giờ làm gãy mở episode.
    try:
        kernel.record_explicit_user_facts(hypothesis_set)
    except Exception:
        pass
    return episode


def start_shadow_episode(kernel: EpistemicKernel, frame: Any) -> CognitiveEpisode:
    """Compatibility alias for callers written during the shadow rollout."""
    return start_episode(kernel, frame)


def _build_epistemic_context(kernel: EpistemicKernel, frame: Any) -> tuple[tuple[Any, ...], Any]:
    frame_id = str(getattr(frame, "cycle_id", "") or "")
    observations = []
    for index, percept in enumerate(tuple(getattr(frame, "percepts", ()) or ())):
        kind = str(getattr(percept, "kind", "runtime_event") or "runtime_event")
        content = str(getattr(percept, "content", "") or "")
        source = str(getattr(percept, "source", "runtime") or "runtime")
        confidence = clamp01(getattr(percept, "confidence", 0.5), 0.5)
        is_user = kind == "user_message"
        observations.append(kernel.observe(
            kind=kind,
            content=content,
            source=source,
            timestamp=_timestamp(getattr(percept, "timestamp", None), getattr(frame, "started_at", None)),
            directness=1.0 if is_user else 0.75 if source in {"telemetry", "vision", "runtime"} else 0.45,
            reliability=confidence,
            independence_key=f"{frame_id}:{kind}:{index}",
            source_kind="user_direct" if is_user else "runtime",
            episode_id=frame_id,
            metadata={"privacy_level": str(getattr(percept, "privacy_level", "internal") or "internal")},
        ))

    proposals = []
    meaning = getattr(frame, "meaning_layer", None)
    meaning_text = str(getattr(meaning, "meaning", "") or "").strip()
    if meaning_text:
        refs = tuple(ref for observation in observations for ref in observation.evidence_refs)
        proposals.append(Hypothesis(
            id="",
            claim=meaning_text,
            subject="situation",
            facet=str(getattr(meaning, "semantic_family", "general") or "general"),
            confidence=min(0.9, clamp01(getattr(meaning, "confidence", 0.0))),
            uncertainty=max(0.20, 1.0 - clamp01(getattr(meaning, "confidence", 0.0))),
            overread_risk=_meaning_overread_risk(frame),
            evidence_refs=refs,
            origin="meaning_layer",
            horizon_hint=BeliefHorizon.TURN_STATE,
        ))

    return tuple(observations), kernel.infer(observations, proposals=proposals)


def _apply_recommendation(frame: Any, recommendation: ActionCandidate, uncertainty: float) -> None:
    plan = getattr(frame, "action_plan", None)
    if plan is None or recommendation.action_type not in {"clarify", "neutral_reply"}:
        return
    notes = list(getattr(plan, "notes", ()) or ())
    notes.append(f"epistemic_v2_authority={recommendation.action_type}")
    notes.append(f"epistemic_uncertainty={uncertainty:.2f}")
    plan.should_speak = True
    plan.speech_intent = "user_requested" if recommendation.action_type == "clarify" else (plan.speech_intent or "user_requested")
    plan.speech_act = "clarifying_question" if recommendation.action_type == "clarify" else "answer"
    plan.reason_to_speak = (
        "Epistemic V2 selected clarification because a consequential interpretation is uncertain."
        if recommendation.action_type == "clarify"
        else "Epistemic V2 selected a neutral literal reply to avoid overreading."
    )
    plan.reason = plan.reason_to_speak
    plan.notes = tuple(dict.fromkeys(notes))


def update_relationship_projection(kernel: EpistemicKernel, engine: Any, event: Any, *, fallback_state: Any = None) -> Any:
    from cortex.relationship_dynamics import RelationshipDynamicsState

    saved = kernel.get_projection("relationship")
    previous = RelationshipDynamicsState.from_dict(saved.get("state") if saved else None)
    if not saved and fallback_state is not None:
        previous = RelationshipDynamicsState.from_dict(fallback_state.to_dict())
    report = engine.update(previous, event)
    evidence = tuple(getattr(report, "evidence", ()) or ())
    kernel.set_projection(
        "relationship",
        {"state": report.state.to_dict(), "evidence": list(evidence)},
        reason=str(getattr(report.state, "last_event", "relationship_turn")),
    )
    return report


def feedback_from_outcome(outcome: Any) -> EpisodeFeedback:
    tags = tuple(str(item) for item in (getattr(outcome, "tags", ()) or ()))
    text = str(getattr(outcome, "feedback_text", "") or "")
    normalized = text.lower()
    tone_feedback = any(marker in normalized for marker in (
        "lạnh", "lanh", "robot", "máy móc", "may moc", "vô hồn", "vo hon",
        "formal", "cứng", "cung", "không tự nhiên", "khong tu nhien",
    ))
    corrected = bool(getattr(outcome, "user_corrected", False))
    confused = bool(getattr(outcome, "user_confused", False))
    accepted = getattr(outcome, "user_accepted", None)
    kind = "tone_feedback" if tone_feedback else "correction" if corrected else "acceptance" if accepted is True else "unknown"
    return EpisodeFeedback(
        kind=kind,
        accepted=accepted,
        corrected=corrected,
        confused=confused,
        felt_intruded=getattr(outcome, "user_felt_intruded", None),
        relation_improved=getattr(outcome, "relation_improved", None),
        relation_ruptured=getattr(outcome, "relation_ruptured", None),
        tone_feedback=tone_feedback,
        text=compact_text(text, 300),
        evidence=tuple(getattr(outcome, "evidence", ()) or ()) + tags,
        confidence=clamp01(getattr(outcome, "confidence", 0.5), 0.5),
    )


def _action_from_frame(frame: Any) -> ActionCandidate:
    plan = getattr(frame, "action_plan", None)
    final = getattr(frame, "final_decision", {}) or {}
    action_type = str(getattr(plan, "speech_act", "") or getattr(plan, "speech_intent", "") or "silence")
    risk_flags = tuple(getattr(plan, "risk_flags", ()) or ())
    selected_utility = clamp01(getattr(plan, "selected_utility", 0.5), 0.5)
    meaning_risk = _meaning_overread_risk(frame)
    flagged_risk = any("overread" in str(flag) or "intrusion" in str(flag) for flag in risk_flags)
    return ActionCandidate(
        id=f"actual:{getattr(frame, 'cycle_id', '')}",
        action_type=action_type,
        content=str(final.get("text", "") if isinstance(final, Mapping) else ""),
        task_fit=selected_utility,
        relational_fit=0.55 if getattr(plan, "should_speak", False) else 0.35,
        expected_helpfulness=0.60 if action_type not in {"no_speech", "silence"} else 0.30,
        epistemic_value=0.15,
        intrusion_risk=max(0.65 if flagged_risk else 0.18, meaning_risk),
        repetition=0.15 if any("repeat" in str(flag) for flag in risk_flags) else 0.0,
        resource_cost=0.10,
        high_consequence=meaning_risk >= 0.55 or any("risk" in str(flag) or "overread" in str(flag) for flag in risk_flags),
        metadata={"risk_flags": list(risk_flags)},
    )


def _action_alternatives(actual: ActionCandidate) -> tuple[ActionCandidate, ...]:
    clarify = ActionCandidate(
        id="shadow:clarify",
        action_type="clarify",
        task_fit=0.58,
        relational_fit=0.62,
        expected_helpfulness=0.58,
        epistemic_value=0.90,
        intrusion_risk=0.08,
        repetition=0.05,
        resource_cost=0.14,
        high_consequence=False,
    )
    neutral = ActionCandidate(
        id="shadow:neutral_reply",
        action_type="neutral_reply",
        task_fit=0.62,
        relational_fit=0.60,
        expected_helpfulness=0.55,
        epistemic_value=0.35,
        intrusion_risk=0.06,
        repetition=0.05,
        resource_cost=0.08,
        high_consequence=False,
    )
    return actual, clarify, neutral


def _meaning_overread_risk(frame: Any) -> float:
    appraisal = getattr(frame, "appraisal", None)
    stance = str(getattr(appraisal, "stance", "") or "")
    notes = " ".join(str(item) for item in (getattr(getattr(frame, "action_plan", None), "risk_flags", ()) or ())).lower()
    risk = 0.25
    if "ungrounded" in stance:
        risk += 0.30
    if "overread" in notes or "mindread" in notes:
        risk += 0.35
    return clamp01(risk)


def _timestamp(value: Any, fallback: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        try:
            return float(fallback)
        except (TypeError, ValueError):
            return time.time()
