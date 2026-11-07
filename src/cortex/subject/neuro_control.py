# -*- coding: utf-8 -*-
"""Neuro-inspired control layer for Subject Core.

This module deliberately does *less* than the old Executive LLM path.
It turns already-grounded coalition scores into fast action competition. Blocking
deliberation is reserved for materially consequential conflict; direct chat may
separately recommend an advisory parallel review without delaying its fast commit.

The neuroscience analogy is intentionally modest:

* ``WorkspaceBroadcast`` ~= a tiny global-workspace broadcast: only the strongest
  current signals are exposed, never the whole private state dump.
* ``NeuroActionSelector`` ~= basal-ganglia-like gating: candidate action channels
  compete using salience/value/uncertainty and one channel is released.
* ``surprise`` / ``precision`` / ``resource`` are global modulators. They change
  selection gain; they are not new semantic flags or a fake neurotransmitter model.

Important engineering invariant: LLMs never need coalition IDs here. Runtime owns
identity; the model, when invited to deliberate, only receives ephemeral choice
indices local to the current frame.
"""
from __future__ import annotations

import math
import time
import uuid
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from .contracts import CoalitionCandidate, SubjectDeliberationFrame, SubjectExecutiveCommit
from .expression_commitment import committed_indices, hard_expression_boundary, is_committed_speech


def _clamp01(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = default
    if number != number:
        number = default
    return max(0.0, min(1.0, number))


def _compact(value: Any, limit: int = 800) -> str:
    text = " ".join(str(value or "").split()).strip()
    return text if len(text) <= limit else text[: max(0, limit - 1)].rstrip() + "…"


def _runtime_context(frame: SubjectDeliberationFrame) -> dict[str, Any]:
    observation_context = dict(getattr(getattr(frame, "observation", None), "context", {}) or {})
    raw = observation_context.get("runtime_context")
    return dict(raw) if isinstance(raw, Mapping) else {}


def _committed_family(frame: SubjectDeliberationFrame) -> tuple[int, ...]:
    """Trả action family đã commit, trừ khi có hard boundary thật."""
    family = committed_indices(frame.coalitions)
    if not family:
        return ()
    blocked, _reason = hard_expression_boundary(_runtime_context(frame))
    return () if blocked else family


def commit_selection(
    frame: SubjectDeliberationFrame,
    selection: "SelectionResult",
    *,
    reason: str,
    source: str,
    clock: Any = time.time,
    semantic_commitments: Sequence[str] = (),
    cognitive_requests: Sequence[str] = (),
    confidence: float | None = None,
    review_conditions: Sequence[str] = (),
) -> SubjectExecutiveCommit:
    """Đóng kết quả competition thành commit bằng một mapping duy nhất.

    Đây là chỗ duy nhất biến ``winner_index`` tạm thời thành runtime identity.
    Cả fast gate local lẫn deliberator đều đi qua cùng hàm này, nên không tồn tại
    một fallback policy thứ hai chỉ vì provider/model đang mất kết nối.
    """
    candidate = selection.winner(frame)
    arguments = dict(candidate.arguments)
    if not semantic_commitments:
        semantic_commitments = tuple(
            item for item in (
                _compact(arguments.get("semantic_target", ""), 500),
                _compact(arguments.get("speech_act", ""), 160),
            ) if item
        )
    inferred_confidence = _clamp01(
        0.45
        + 0.35 * (1.0 - selection.entropy)
        + 0.20 * min(1.0, max(0.0, selection.margin) / 0.20)
    )
    return SubjectExecutiveCommit(
        commit_id=f"neuro-commit:{uuid.uuid4()}",
        frame_id=frame.frame_id,
        selected_coalition_id=candidate.coalition_id,
        selected_proposal_ref=candidate.primary_proposal_ref,
        semantic_commitments=tuple(
            _compact(item, 500) for item in semantic_commitments if _compact(item, 500)
        )[:8],
        cognitive_requests=tuple(
            _compact(item, 160).lower() for item in cognitive_requests if _compact(item, 160)
        )[:6],
        reason=_compact(reason, 500) or selection.reason,
        confidence=inferred_confidence if confidence is None else _clamp01(confidence),
        review_conditions=tuple(
            _compact(item, 180).lower() for item in review_conditions if _compact(item, 180)
        )[:8],
        created_at=float(clock()),
        source=str(source or "neuro_control"),
    )


@dataclass(frozen=True, slots=True)
class WorkspaceSignal:
    """One bounded item allowed to win global broadcast for this decision cycle."""

    kind: str
    label: str
    salience: float
    confidence: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "label": self.label,
            "salience": self.salience,
            "confidence": self.confidence,
        }


@dataclass(frozen=True, slots=True)
class WorkspaceBroadcast:
    """Small public-safe projection of what currently matters most."""

    signals: tuple[WorkspaceSignal, ...]
    surprise: float
    precision: float
    resource: float
    social_drive: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "signals": [item.to_dict() for item in self.signals],
            "modulators": {
                "surprise": self.surprise,
                "precision": self.precision,
                "resource": self.resource,
                "social_drive": self.social_drive,
            },
        }


@dataclass(frozen=True, slots=True)
class SelectionResult:
    """Result of fast action competition.

    ``winner_index`` is ephemeral and only meaningful inside ``frame``.  This is
    the point: no model-owned runtime identity survives into a later frame.
    """

    winner_index: int
    scores: tuple[float, ...]
    probabilities: tuple[float, ...]
    margin: float
    entropy: float
    requires_deliberation: bool
    parallel_review_recommended: bool
    reason: str
    broadcast: WorkspaceBroadcast

    def winner(self, frame: SubjectDeliberationFrame) -> CoalitionCandidate:
        return frame.coalitions[self.winner_index]


@dataclass(frozen=True, slots=True)
class NeuroDeliberationAdvice:
    """Advisory-only result from slow Neuro cognition.

    ``commit`` describes the choice the slow reviewer would make on the frozen
    frame.  It is deliberately *not* finalized by Subject Core.  ``followup_brief``
    is a semantic continuation brief, never public wording.  Runtime decides
    whether the advice is still current and material enough to surface.
    """

    commit: SubjectExecutiveCommit
    followup_brief: str = ""
    material_delta: float = 0.0
    should_follow_up: bool = False


@dataclass(frozen=True, slots=True)
class ChoiceSemanticAppraisal:
    """Slow semantic measurements for one ephemeral action channel.

    The LLM may estimate these bounded dimensions, but it never returns a winner.
    Local Neuro owns the mapping from measurements to action competition.
    """

    index: int
    motive_validity: float = 0.5
    novelty: float = 0.5
    continuity_fit: float = 0.5
    epistemic_support: float = 0.5
    reversibility: float = 0.5


@dataclass(frozen=True, slots=True)
class NeuroSemanticAppraisal:
    """Typed slow-cognition delta merged into a single Neuro authority."""

    choices: tuple[ChoiceSemanticAppraisal, ...]
    boundary_pressure: float = 0.0
    interruption_cost: float = 0.0
    confidence: float = 0.0
    reason: str = ""
    semantic_commitments: tuple[str, ...] = ()
    cognitive_requests: tuple[str, ...] = ()
    review_conditions: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class NeuroControlConfig:
    """Continuous thresholds for competition and *material* deliberation.

    Margin/entropy/uncertainty describe how indecisive the fast competition is.
    They are diagnostics, not by themselves a reason to spend a remote LLM call.
    Remote deliberation is reserved for ambiguity that also carries material
    consequence, or for an explicit commitment conflict.
    """

    deliberation_margin: float = 0.075
    uncertainty_threshold: float = 0.52
    entropy_threshold: float = 0.82
    surprise_threshold: float = 0.74
    conflict_threshold: float = 0.60
    material_stakes_threshold: float = 0.72
    max_broadcast_signals: int = 3


class NeuroActionSelector:
    """Fast, deterministic action gate over Subject Core coalitions."""

    def __init__(self, config: NeuroControlConfig | None = None) -> None:
        self.config = config or NeuroControlConfig()

    def workspace(self, frame: SubjectDeliberationFrame) -> WorkspaceBroadcast:
        # Production receives a typed SubjectDeliberationFrame. ``getattr``
        # defaults keep the selector usable by degraded adapters/tests that only
        # expose ranked coalitions; absence of a signal means neutral modulation,
        # not invented cognition.
        appraisals = tuple(getattr(frame, "appraisals", ()) or ())
        activations = tuple(getattr(frame, "activations", ()) or ())

        surprise = max(
            (
                max(_clamp01(item.novelty), _clamp01(item.expectation_violation))
                for item in appraisals
            ),
            default=0.0,
        )
        appraisal_uncertainty = max((_clamp01(item.uncertainty) for item in appraisals), default=0.0)
        coalition_uncertainty = min(
            (_clamp01(item.uncertainty, 1.0) for item in frame.coalitions),
            default=1.0,
        )
        # Precision is confidence in the current action landscape, not certainty
        # that every belief in the system is true.
        precision = _clamp01(1.0 - max(appraisal_uncertainty, coalition_uncertainty))
        substrate = getattr(frame, "substrate", None)
        resource = _clamp01(getattr(substrate, "processing_capacity", 1.0), 1.0)
        social_drive = max(
            (
                _clamp01(item.pressure) * (0.55 + 0.45 * _clamp01(item.opportunity))
                for item in activations
                if str(item.need_id).lower() == "connection"
            ),
            default=0.0,
        )

        signals: list[WorkspaceSignal] = []
        for item in appraisals:
            label = str(item.primary_interpretation or "appraisal").strip()[:220]
            salience = max(
                _clamp01(item.goal_relevance),
                _clamp01(abs(float(getattr(item, "goal_congruence", 0.0)))),
                _clamp01(item.relational_significance),
                _clamp01(item.identity_significance),
                _clamp01(item.expectation_violation),
            )
            confidence = _clamp01(1.0 - item.uncertainty)
            signals.append(WorkspaceSignal("appraisal", label, salience, confidence))

        for item in activations:
            salience = _clamp01(
                0.50 * _clamp01(item.pressure)
                + 0.30 * _clamp01(item.urgency)
                + 0.20 * _clamp01(item.persistence)
            )
            signals.append(
                WorkspaceSignal(
                    "need",
                    str(item.need_id),
                    salience,
                    _clamp01(0.5 + 0.5 * item.opportunity),
                )
            )

        # Predictive-processing motif without a duplicate world model: Subject
        # Core already carries a learned competence prediction and historical
        # outcome prior inside each proposal's inner_trace. Their disagreement is
        # useful epistemic surprise. It can win workspace broadcast and lower the
        # confidence of routine gating, but it never manufactures an action.
        predictive_disagreement = 0.0
        for proposal in (getattr(frame, "proposals", ()) or ()):
            arguments = dict(getattr(proposal, "arguments", {}) or {})
            trace = dict(arguments.get("inner_trace", {}) or {})
            if "competence_prediction" not in trace or "historical_success" not in trace:
                continue
            learned = _clamp01(trace.get("competence_prediction"), 0.5)
            historical = _clamp01(trace.get("historical_success"), 0.5)
            disagreement = abs(learned - historical)
            predictive_disagreement = max(predictive_disagreement, disagreement)
            if disagreement >= 0.18:
                signals.append(WorkspaceSignal(
                    "prediction_error_prior",
                    str(getattr(proposal, "action_kind", "action") or "action"),
                    _clamp01(disagreement),
                    _clamp01(1.0 - getattr(proposal, "uncertainty", 0.5), 0.5),
                ))

        surprise = max(surprise, _clamp01(predictive_disagreement))
        precision = _clamp01(precision * (1.0 - 0.35 * predictive_disagreement))

        ordered = tuple(
            sorted(signals, key=lambda item: (-item.salience, -item.confidence, item.kind, item.label))[
                : max(1, int(self.config.max_broadcast_signals))
            ]
        )
        return WorkspaceBroadcast(
            signals=ordered,
            surprise=surprise,
            precision=precision,
            resource=resource,
            social_drive=social_drive,
        )

    @staticmethod
    def _social_speech_act(candidate: CoalitionCandidate) -> str:
        if str(getattr(candidate, "action_kind", "") or "").strip().lower() != "speak":
            return ""
        arguments = dict(getattr(candidate, "arguments", {}) or {})
        speech_act = str(arguments.get("speech_act", "") or "").strip().lower()
        return speech_act if speech_act in {
            "contact_bid", "user_probe", "curiosity_bid", "social_check_in"
        } else ""

    @classmethod
    def _homogeneous_social_tie(
        cls,
        frame: SubjectDeliberationFrame,
        order: Sequence[int],
    ) -> bool:
        """True when the leading competition is only between equivalent social bids.

        This is deliberately narrow. It does not suppress deliberation for a
        social action competing with a task/capability action, nor for commitment
        conflict. It only prevents an LLM round-trip to choose among reversible,
        low-stakes contact/check-in realizations.
        """
        if len(order) < 2:
            return False
        first = cls._social_speech_act(frame.coalitions[order[0]])
        second = cls._social_speech_act(frame.coalitions[order[1]])
        # Different social intents can carry genuinely different consequences
        # (e.g. asking for information vs merely reopening contact). Those should
        # have been coalesced earlier only when they share one objective. This
        # final safety net is intentionally limited to duplicate semantic lanes.
        return bool(first and first == second)

    @staticmethod
    def _commitment_conflict(frame: SubjectDeliberationFrame, candidate: CoalitionCandidate) -> float:
        proposals = {
            item.proposal_id: item
            for item in (getattr(frame, "proposals", ()) or ())
            if getattr(item, "proposal_id", "")
        }
        return max(
            (
                _clamp01(proposals[ref].commitment_conflict)
                for ref in (getattr(candidate, "proposal_refs", ()) or ())
                if ref in proposals
            ),
            default=0.0,
        )

    def _channel_score(
        self,
        frame: SubjectDeliberationFrame,
        candidate: CoalitionCandidate,
        broadcast: WorkspaceBroadcast,
    ) -> float:
        """Modulate existing causal score instead of replacing Subject Core.

        The arbiter already did the structured work.  These small terms only
        change gain according to global state: when precision falls, epistemic
        actions matter more; when resources fall, uncertain/costly channels lose
        ground.  Keeping the modulation weak prevents a pretty neuroscience name
        from silently becoming a second policy engine.
        """

        # Some degraded adapters expose only the already-ranked order and a
        # pragmatic/uncertainty summary. In that case pragmatic value is a safe
        # neutral base; equal channels retain tuple order as the tie-breaker.
        score = float(
            getattr(
                candidate,
                "score",
                getattr(candidate, "expected_pragmatic_value", 0.0),
            )
            or 0.0
        )
        pragmatic = _clamp01(getattr(candidate, "expected_pragmatic_value", 0.0))
        epistemic = _clamp01(getattr(candidate, "expected_epistemic_value", 0.0))
        uncertainty = _clamp01(getattr(candidate, "uncertainty", 0.5), 0.5)
        score += 0.08 * broadcast.precision * pragmatic
        score += 0.10 * (1.0 - broadcast.precision) * epistemic
        score -= 0.08 * (1.0 - broadcast.resource) * uncertainty

        # Outcome-adaptive prior without trainable selector state. Subject Core's
        # competence/history estimators are already updated by real outcomes; the
        # gate reads that learned prior from the proposal it is evaluating. This
        # keeps one learning authority instead of teaching a second policy model
        # behind the arbiter's back.
        proposals = {
            item.proposal_id: item
            for item in (getattr(frame, "proposals", ()) or ())
            if getattr(item, "proposal_id", "")
        }
        learned_values: list[float] = []
        for ref in (getattr(candidate, "proposal_refs", ()) or ()):
            proposal = proposals.get(ref)
            if proposal is None:
                continue
            trace = dict(dict(getattr(proposal, "arguments", {}) or {}).get("inner_trace", {}) or {})
            if "competence_prediction" in trace:
                learned_values.append(_clamp01(trace.get("competence_prediction"), 0.5))
            if "historical_success" in trace:
                learned_values.append(_clamp01(trace.get("historical_success"), 0.5))
        if learned_values:
            learned_prior = sum(learned_values) / len(learned_values)
            score += 0.06 * (learned_prior - 0.5)

        action = str(candidate.action_kind or "").lower()
        arguments = dict(getattr(candidate, "arguments", {}) or {})
        runtime_context = _runtime_context(frame)
        committed = is_committed_speech(candidate)
        blocked, _boundary_reason = hard_expression_boundary(runtime_context)
        # V66: quiet/response-rate/user_busy không còn là speech cost. Chỉ collision
        # trực tiếp mới là boundary. Với commitment đang mở, action family đã được
        # quyết định nên policy legacy không có quyền kéo về SILENCE.
        interruption_cost = 1.0 if blocked else 0.0
        policy_cost = 0.0 if committed else _clamp01(arguments.get("initiative_policy_cost", 0.0))
        social_wait = 1.0 if blocked else 0.0

        if action == "speak":
            score += 0.035 * broadcast.social_drive
            # V66: pressure/freshness chỉ rank nhiều expression commitment, không
            # phải readiness threshold để xin quyền nói.
            expression_pressure = _clamp01(arguments.get("expression_pressure", 0.0))
            expression_freshness = _clamp01(arguments.get("expression_freshness", 0.0))
            expression_satisfaction = _clamp01(arguments.get("expression_satisfaction", 0.0))
            if committed:
                raw_commitment = arguments.get("expression_commitment")
                raw_commitment = raw_commitment if isinstance(raw_commitment, Mapping) else {}
                score += 0.18 * _clamp01(raw_commitment.get("pressure", expression_pressure))
                score += 0.10 * _clamp01(raw_commitment.get("freshness", expression_freshness))
                score += 0.08 * _clamp01(raw_commitment.get("persistence", 1.0 - expression_satisfaction))
            score -= 0.10 * interruption_cost
            score -= 0.24 * policy_cost
            score -= 0.14 * social_wait
            # Seed quality belongs upstream of the public decision. A thin seed
            # should lose utility here rather than win Neuro and get censored by
            # a downstream collection of special cases.
            if arguments.get("seed_id"):
                seed_quality = _clamp01(arguments.get("seed_quality", 0.0))
                motive_strength = _clamp01(arguments.get("seed_motive_strength", 0.0))
                intrusion = _clamp01(arguments.get("intrusion_risk", 0.0))
                score += 0.12 * (seed_quality - 0.5)
                score += 0.07 * (motive_strength - 0.5)
                score -= 0.09 * intrusion
        elif action == "silence":
            # SILENCE là lựa chọn khi chưa có commitment hoặc có hard boundary;
            # nó không được thưởng chỉ vì user im/ít phản hồi.
            score += 0.08 * interruption_cost
        elif action == "body_only":
            score += 0.04 * interruption_cost
        return score

    @staticmethod
    def _softmax(scores: Sequence[float], beta: float) -> tuple[float, ...]:
        if not scores:
            return ()
        peak = max(scores)
        exps = [math.exp(max(-60.0, min(60.0, beta * (score - peak)))) for score in scores]
        total = sum(exps) or 1.0
        return tuple(value / total for value in exps)

    @staticmethod
    def _normalized_entropy(probabilities: Sequence[float]) -> float:
        if len(probabilities) <= 1:
            return 0.0
        entropy = -sum(p * math.log(max(p, 1e-12)) for p in probabilities)
        return _clamp01(entropy / math.log(len(probabilities)))

    def merge_semantic_appraisal(
        self,
        frame: SubjectDeliberationFrame,
        baseline: SelectionResult,
        appraisal: NeuroSemanticAppraisal,
    ) -> SelectionResult:
        """Merge slow semantic measurements, then let local Neuro select once.

        Deep cognition supplies bounded appraisals rather than a choice.  The
        deterministic gate owns the utility transform and final winner, keeping a
        single decision authority while still letting language-level context affect
        autonomous restraint, motive strength, and epistemic support.
        """

        if len(baseline.scores) != len(frame.coalitions):
            return baseline
        metrics = {item.index: item for item in appraisal.choices}
        committed_family = _committed_family(frame)
        boundary = 0.0 if committed_family else _clamp01(appraisal.boundary_pressure)
        interruption = 0.0 if committed_family else _clamp01(appraisal.interruption_cost)
        review_confidence = _clamp01(appraisal.confidence)

        # Chú thích VI: ExpressionCommitment giữ *nội dung muốn nói*, nhưng không
        # nên ép phát ngay khi slow semantic critic vừa đo được motive/continuity/
        # evidence đều rất yếu. Đây là DEFER một tick, không DROP commitment.
        # User silence/busy không tham gia threshold này, nên triết lý V67 về
        # self-owned expression vẫn giữ nguyên; chỉ semantic support của chính ý
        # định mới có quyền trì hoãn nó.
        silence_family = tuple(
            index for index, candidate in enumerate(frame.coalitions)
            if str(getattr(candidate, "action_kind", "") or "").strip().lower() == "silence"
        )
        defer_weak_commitment = False
        semantic_authority_defer = False
        commitment_support = 1.0
        if committed_family and silence_family and review_confidence >= 0.55:
            supports: list[float] = []
            for index in committed_family:
                item = metrics.get(index)
                if item is None:
                    continue
                motive = _clamp01(item.motive_validity, 0.5)
                continuity = _clamp01(item.continuity_fit, 0.5)
                epistemic = _clamp01(item.epistemic_support, 0.5)
                supports.append(0.40 * motive + 0.25 * continuity + 0.35 * epistemic)
            if supports:
                commitment_support = max(supports)
                # V95: semantic critic không còn chỉ là một chút score seasoning.
                # Khi confidence đủ dùng và *chính motive/evidence của commitment*
                # quá yếu, critic có authority DEFER public speech một tick. Đây
                # không phải DROP desire: ExpressionCommitment vẫn còn upstream và
                # V94 proposal lifecycle quyết khi nào evidence mới cho phép thử lại.
                # Threshold 0.36 tái tạo đúng run 14:36 (confidence=0.62, support≈0.23)
                # mà V94 bỏ lọt vì yêu cầu confidence >= 0.65.
                semantic_authority_defer = commitment_support < 0.36
                defer_weak_commitment = commitment_support < 0.42

        merged_scores: list[float] = []
        for index, candidate in enumerate(frame.coalitions):
            score = float(baseline.scores[index])
            item = metrics.get(index)
            action = str(candidate.action_kind or "").strip().lower()
            if item is not None:
                motive = _clamp01(item.motive_validity, 0.5)
                novelty = _clamp01(item.novelty, 0.5)
                continuity = _clamp01(item.continuity_fit, 0.5)
                epistemic = _clamp01(item.epistemic_support, 0.5)
                reversible = _clamp01(item.reversibility, 0.5)
            else:
                motive = novelty = continuity = epistemic = reversible = 0.5

            # Confidence scales the slow lane's influence.  Even a confident
            # reviewer can only modulate the Subject score; it cannot replace it.
            gain = 0.55 + 0.45 * review_confidence
            if action == "speak":
                delta = (
                    0.16 * (motive - 0.5)
                    + 0.10 * (novelty - 0.5)
                    + 0.10 * (continuity - 0.5)
                    + 0.14 * (epistemic - 0.5)
                    + 0.05 * (reversible - 0.5)
                    - 0.22 * boundary
                    - 0.16 * interruption
                )
                # V61: nếu slow appraisal có confidence tốt và báo interruption
                # cao trong khi chính candidate SPEAK có continuity thấp, restraint
                # phải đủ lực để thay đổi quyết định. Bản cũ cho reviewer nói
                # "silence is safest" nhưng utility vẫn thắng SPEAK với margin vài
                # phần nghìn, khiến log và hành vi tự mâu thuẫn. Đây vẫn là local
                # utility transform; model không được chọn action trực tiếp.
                restraint = _clamp01((interruption - 0.55) / 0.45) * review_confidence
                if continuity < 0.50 and restraint > 0.0:
                    delta -= 0.10 * restraint * _clamp01((0.55 - continuity) / 0.55)
                # Unsupported present-world speech should lose decisively before
                # Surface is called, but this remains a score penalty rather than a
                # downstream regex veto.
                if epistemic < 0.22:
                    delta -= 0.18 * ((0.22 - epistemic) / 0.22)
                score += gain * delta
            elif action == "silence":
                restraint = _clamp01((interruption - 0.55) / 0.45) * review_confidence
                score += gain * (
                    0.18 * boundary
                    + 0.13 * interruption
                    + 0.07 * (0.5 - motive)
                    + 0.05 * (0.5 - epistemic)
                    + 0.055 * restraint
                )
            elif action == "body_only":
                score += gain * (
                    0.07 * boundary
                    + 0.05 * interruption
                    + 0.05 * (reversible - 0.5)
                )
            if defer_weak_commitment:
                if index in committed_family:
                    # Penalty đủ lớn để reviewer thật sự có khả năng DEFER thay
                    # vì log nói “nên im” nhưng utility vẫn ép phát như run 13:24.
                    score -= 0.22 + 0.25 * max(0.0, 0.38 - commitment_support)
                elif index in silence_family:
                    score += 0.08 * review_confidence
            merged_scores.append(score)

        scores = tuple(merged_scores)
        if semantic_authority_defer:
            # V95 authority order: semantic critic được quyền DEFER một public
            # commitment semantic-unsupported; Neuro vẫn là bên chọn action cuối
            # nhưng chỉ trong non-speech escape family còn hợp lệ. Surface không
            # bao giờ nhìn thấy motive này nên không thể vô tình hồi sinh nó.
            non_speech_family = tuple(
                index for index, candidate in enumerate(frame.coalitions)
                if str(getattr(candidate, "action_kind", "") or "").strip().lower() in {"silence", "body_only"}
            )
            eligible = non_speech_family or silence_family
        elif defer_weak_commitment:
            eligible = tuple(dict.fromkeys((*committed_family, *silence_family)))
        else:
            eligible = committed_family or tuple(range(len(scores)))
        order = sorted(eligible, key=lambda idx: (-scores[idx], idx))
        winner_index = order[0]
        runner_index = order[1] if len(order) > 1 else winner_index
        margin = scores[winner_index] - scores[runner_index] if len(order) > 1 else 1.0
        beta = 3.0 + 7.0 * baseline.broadcast.precision
        eligible_probs = self._softmax(tuple(scores[idx] for idx in eligible), beta)
        probability_map = dict(zip(eligible, eligible_probs, strict=False))
        probabilities = tuple(probability_map.get(idx, 0.0) for idx in range(len(scores)))
        entropy = self._normalized_entropy(probabilities)
        return SelectionResult(
            winner_index=winner_index,
            scores=scores,
            probabilities=probabilities,
            margin=margin,
            entropy=entropy,
            requires_deliberation=False,
            parallel_review_recommended=False,
            reason=(
                (
                    "semantic_critique_deferred_commitment:v95_authority:"
                    if semantic_authority_defer
                    else (
                        "semantic_critique_deferred_commitment:"
                        if defer_weak_commitment
                        else ("semantic_critique_committed:" if committed_family else "semantic_merge:")
                    )
                )
                + (_compact(appraisal.reason, 240) or "slow_appraisal")
            ),
            broadcast=baseline.broadcast,
        )

    def select(self, frame: SubjectDeliberationFrame) -> SelectionResult:
        if not frame.coalitions:
            raise ValueError("neuro selector requires at least one coalition")

        broadcast = self.workspace(frame)

        # Compatibility/degraded adapters may expose only a ranked tuple instead
        # of the full typed frame. In that case the ranking *is* the available
        # evidence. Do not invent uncertainty and do not wake a deliberator merely
        # because diagnostic detail is absent.
        rank_only = (
            not hasattr(frame, "appraisals")
            or not all(hasattr(item, "score") for item in frame.coalitions)
        )
        if rank_only:
            scores = tuple(1.0 - 0.01 * index for index, _item in enumerate(frame.coalitions))
            probabilities = self._softmax(scores, beta=10.0)
            return SelectionResult(
                winner_index=0,
                scores=scores,
                probabilities=probabilities,
                margin=(scores[0] - scores[1] if len(scores) > 1 else 1.0),
                entropy=self._normalized_entropy(probabilities),
                requires_deliberation=False,
                parallel_review_recommended=False,
                reason="fast_gate:ranked_adapter",
                broadcast=broadcast,
            )

        scores = tuple(self._channel_score(frame, item, broadcast) for item in frame.coalitions)
        runtime_context = _runtime_context(frame)

        # V74 Courtesy Contract là turn-taking boundary cứng. Khi ERIS vừa hỏi
        # và đang chờ user, selector không được để một speech candidate điểm cao
        # vượt qua boundary chỉ vì utility nội tại lớn. Nếu frame có SILENCE, chỉ SILENCE
        # được quyền cạnh tranh cho tick này. Không áp dụng quy tắc này cho các
        # hard boundary khác để tránh thay đổi semantics V67 ngoài phạm vi patch.
        courtesy_hold = bool(
            runtime_context.get("courtesy_hold")
            or runtime_context.get("awaiting_user_answer")
        )
        silence_family = tuple(
            index
            for index, candidate in enumerate(frame.coalitions)
            if str(getattr(candidate, "action_kind", "") or "").strip().lower() == "silence"
        )
        committed_family = _committed_family(frame)
        if courtesy_hold and silence_family:
            eligible = silence_family
        else:
            eligible = committed_family or tuple(range(len(scores)))
        order = sorted(eligible, key=lambda idx: (-scores[idx], idx))
        winner_index = order[0]
        runner_index = order[1] if len(order) > 1 else winner_index
        margin = scores[winner_index] - scores[runner_index] if len(order) > 1 else 1.0

        # Khi commitment tồn tại, xác suất chỉ phân phối trong speech family.
        # SILENCE/BODY_ONLY không còn là phiếu phủ quyết lần hai.
        beta = 3.0 + 7.0 * broadcast.precision
        eligible_probs = self._softmax(tuple(scores[idx] for idx in eligible), beta)
        probability_map = dict(zip(eligible, eligible_probs, strict=False))
        probabilities = tuple(probability_map.get(idx, 0.0) for idx in range(len(scores)))
        entropy = self._normalized_entropy(probabilities)
        winner = frame.coalitions[winner_index]

        # Competition ambiguity and deliberation necessity are different things.
        # A casual turn can legitimately have several nearly-equivalent actions
        # and therefore high entropy. That does not make the turn semantically
        # difficult or worth another network/model round-trip.
        ambiguity: list[str] = []
        if len(frame.coalitions) > 1 and margin < self.config.deliberation_margin:
            ambiguity.append("low_margin")
        winner_uncertainty = _clamp01(getattr(winner, "uncertainty", 0.5), 0.5)
        if winner_uncertainty >= self.config.uncertainty_threshold:
            ambiguity.append("winner_uncertain")
        high_entropy = entropy >= self.config.entropy_threshold
        if high_entropy:
            ambiguity.append("high_entropy")

        # Materiality comes from the causal frame, not from wording heuristics.
        # Goal relevance is intentionally excluded: a user can strongly want a
        # trivial answer. Generic chat also carries a high baseline relational
        # significance in SubjectService, so relational_significance is *not* a
        # high-stakes trigger here. Consequence/identity stakes and explicit
        # commitment conflict are what justify an expensive second opinion.
        decision_stakes = max(
            (
                max(
                    _clamp01(getattr(item, "future_consequence", 0.0)),
                    _clamp01(getattr(item, "identity_significance", 0.0)),
                )
                for item in (getattr(frame, "appraisals", ()) or ())
            ),
            default=0.0,
        )
        conflict = self._commitment_conflict(frame, winner)

        reasons: list[str] = []
        if conflict >= self.config.conflict_threshold:
            reasons.append("commitment_conflict")

        # Entropy is intentionally excluded from the trigger. A distribution can
        # be diffuse simply because several reversible variants are all acceptable.
        # Escalation needs local indecision (margin) or uncertainty *and* stakes.
        consequential_ambiguity = any(
            item in {"low_margin", "winner_uncertain"} for item in ambiguity
        )
        if consequential_ambiguity and decision_stakes >= self.config.material_stakes_threshold:
            reasons.append("material_ambiguity")

        if (
            decision_stakes >= self.config.material_stakes_threshold
            and broadcast.surprise >= self.config.surprise_threshold
            and broadcast.precision < 0.62
        ):
            reasons.append("surprising_material_change")

        requires = bool(reasons)
        homogeneous_tie = self._homogeneous_social_tie(frame, order) and bool(ambiguity)

        # Public chat has a different latency contract from autonomous action.
        # Ambiguity can be worth a *background second look* even when it is not
        # consequential enough to block/override the fast choice. Keep this as a
        # typed signal instead of teaching runtime to parse ``reason`` strings.
        # Entropy alone still does nothing: diffuse but equivalent choices are
        # common in conversation.
        origin = getattr(getattr(frame, "observation", None), "origin", None)
        origin_value = str(getattr(origin, "value", origin) or "").lower()
        user_origin = origin_value == "user"
        parallel_review_recommended = bool(
            user_origin
            and not homogeneous_tie
            and (requires or any(item in {"low_margin", "winner_uncertain"} for item in ambiguity))
        )

        if requires:
            reason = "deliberate:" + ",".join(reasons)
            if ambiguity:
                reason += ";competition=" + ",".join(ambiguity)
        elif homogeneous_tie:
            reason = "fast_gate:homogeneous_social_tie"
        elif ambiguity:
            # The deterministic winner is good enough for the public reply. A
            # user-origin frame may still be reviewed asynchronously, which is
            # advisory cognition rather than a delayed decision.
            reason = "fast_gate:routine_ambiguity:" + ",".join(ambiguity)
        else:
            reason = "fast_gate:clear_winner"

        return SelectionResult(
            winner_index=winner_index,
            scores=scores,
            probabilities=probabilities,
            margin=margin,
            entropy=entropy,
            requires_deliberation=requires,
            parallel_review_recommended=parallel_review_recommended,
            reason=reason,
            broadcast=broadcast,
        )


__all__ = [
    "NeuroActionSelector",
    "NeuroControlConfig",
    "NeuroDeliberationAdvice",
    "ChoiceSemanticAppraisal",
    "NeuroSemanticAppraisal",
    "SelectionResult",
    "WorkspaceBroadcast",
    "WorkspaceSignal",
]
