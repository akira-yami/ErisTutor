from __future__ import annotations
from core.di import resolve_dependency

import re
import time
import unicodedata
from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Sequence

from .contracts import (
    ActionCandidate,
    Belief,
    BeliefHorizon,
    CognitiveEpisode,
    EpisodeFeedback,
    EvidenceRef,
    Hypothesis,
    HypothesisSet,
    LearningUpdate,
    Observation,
    clamp01,
    compact_text,
    stable_id,
    unique_evidence,
)
from .store import EpistemicStore


@dataclass(slots=True)
class EpistemicKernelConfig:
    inferred_confidence_cap: float = 0.90
    session_independent_groups: int = 2
    persistent_independent_episodes: int = 3
    correction_penalty: float = 0.38
    acceptance_reward: float = 0.10
    high_uncertainty_threshold: float = 0.62


class EpistemicKernel:
    """Single authority for V2 beliefs and outcome-calibrated learning."""

    _LOW_SIGNAL = frozenset({"", "ừ", "uh", "uhm", "um", "ok", "oke", "chào", "hello", "hi", "alo", "ờ"})
    _EXPLICIT_USER_FACT = (
        re.compile(r"^(?:tôi|mình|tao|tớ)\s+(?:là|thích|muốn|ghét|đang|cần|không thích)\s+(.+)$", re.I),
        re.compile(r"^(?:my|i)\s+(?:name is|am|like|want|hate|need)\s+(.+)$", re.I),
    )

    def __init__(self, store: EpistemicStore | None = None, *, config: EpistemicKernelConfig | None = None, logger: Any = None) -> None:
        self.store = resolve_dependency(store, lambda: EpistemicStore())
        self.config = resolve_dependency(config, lambda: EpistemicKernelConfig())
        self.logger = logger
        self.store.mode = "authoritative"

    @property
    def authoritative(self) -> bool:
        return True

    # ------------------------------------------------------------------
    # Observation and inference
    # ------------------------------------------------------------------
    def observe(
        self,
        *,
        kind: str,
        content: str,
        source: str,
        timestamp: float | None = None,
        directness: float = 0.0,
        reliability: float = 0.5,
        independence_key: str = "",
        source_kind: str = "runtime",
        episode_id: str = "",
        metadata: Mapping[str, Any] | None = None,
    ) -> Observation:
        clean = compact_text(content, 1200)
        key = independence_key or f"{source}:{episode_id or stable_id('event', kind, clean)}"
        return Observation(
            id="",
            kind=kind,
            content=clean,
            source=source,
            timestamp=float(timestamp or time.time()),
            directness=directness,
            reliability=reliability,
            independence_key=key,
            source_kind=source_kind,
            metadata={**dict(metadata or {}), "episode_id": episode_id},
        )

    def infer(
        self,
        observations: Sequence[Observation],
        *,
        proposals: Iterable[Hypothesis | Mapping[str, Any]] = (),
    ) -> HypothesisSet:
        hypotheses: list[Hypothesis] = []
        for observation in observations:
            if observation.kind == "user_message":
                hypotheses.extend(self._user_message_hypotheses(observation))

        for proposal in proposals:
            hypothesis = proposal if isinstance(proposal, Hypothesis) else self._proposal_from_mapping(proposal)
            if hypothesis is not None:
                hypotheses.append(hypothesis)

        hypotheses = self._dedupe_hypotheses(hypotheses)
        if not hypotheses:
            return HypothesisSet()

        for hypothesis in hypotheses:
            hypothesis.confidence = self._calibrated_confidence(hypothesis.origin, hypothesis.confidence)
            hypothesis.uncertainty = max(hypothesis.uncertainty, 1.0 - hypothesis.confidence)

        ranked = sorted(hypotheses, key=lambda item: (item.confidence - item.overread_risk * 0.45), reverse=True)
        selected = ranked[0]
        runner_up = ranked[1].confidence if len(ranked) > 1 else 0.0
        ambiguity = clamp01(1.0 - max(0.0, selected.confidence - runner_up))
        uncertainty = max(selected.uncertainty, ambiguity * 0.75, selected.overread_risk * 0.75)
        return HypothesisSet(hypotheses=tuple(ranked), selected_id=selected.id, uncertainty=clamp01(uncertainty))

    def _user_message_hypotheses(self, observation: Observation) -> list[Hypothesis]:
        normalized = self._normalize(observation.content)
        refs = observation.evidence_refs
        if normalized in self._LOW_SIGNAL or len(normalized.split()) <= 1:
            neutral = Hypothesis(
                id="",
                claim="The user made a low-signal conversational contact; intent remains unknown.",
                subject="interaction",
                facet="current_intent",
                confidence=0.58,
                uncertainty=0.78,
                overread_risk=0.08,
                evidence_refs=refs,
                origin="low_signal_guard",
                horizon_hint=BeliefHorizon.TURN_STATE,
            )
            alternatives = Hypothesis(
                id="",
                claim="The message may be a greeting, acknowledgement, or simple presence check.",
                subject="interaction",
                facet="alternative_intent",
                confidence=0.42,
                uncertainty=0.82,
                overread_risk=0.12,
                evidence_refs=refs,
                origin="counterfactual_guard",
                horizon_hint=BeliefHorizon.TURN_STATE,
            )
            neutral.counterfactual_ids = (alternatives.id,)
            alternatives.counterfactual_ids = (neutral.id,)
            return [neutral, alternatives]

        explicit = self._extract_explicit_user_fact(observation.content)
        if explicit:
            return [
                Hypothesis(
                    id="",
                    claim=explicit,
                    subject="user",
                    facet="explicit_fact",
                    confidence=min(0.96, observation.reliability * 0.98),
                    uncertainty=0.08,
                    overread_risk=0.02,
                    evidence_refs=refs,
                    origin="explicit_user_statement",
                    horizon_hint=BeliefHorizon.PERSISTENT_BELIEF,
                    direct_assertion=True,
                )
            ]

        literal = Hypothesis(
            id="",
            claim=f"The user is currently discussing: {compact_text(observation.content, 220)}",
            subject="interaction",
            facet="current_topic",
            confidence=min(0.82, observation.reliability * 0.90),
            uncertainty=0.30,
            overread_risk=0.12,
            evidence_refs=refs,
            origin="literal_turn_read",
            horizon_hint=BeliefHorizon.TURN_STATE,
        )
        alternative = Hypothesis(
            id="",
            claim="The user's deeper motive is not established by this message alone.",
            subject="interaction",
            facet="motive_unknown",
            confidence=0.62,
            uncertainty=0.60,
            overread_risk=0.04,
            evidence_refs=refs,
            origin="counterfactual_guard",
            horizon_hint=BeliefHorizon.TURN_STATE,
        )
        literal.counterfactual_ids = (alternative.id,)
        alternative.counterfactual_ids = (literal.id,)
        return [literal, alternative]

    def _proposal_from_mapping(self, proposal: Mapping[str, Any]) -> Hypothesis | None:
        claim = compact_text(proposal.get("claim"), 600)
        if not claim:
            return None
        refs: list[EvidenceRef] = []
        for raw in proposal.get("evidence_refs", ()):
            if isinstance(raw, EvidenceRef):
                refs.append(raw)
            elif isinstance(raw, Mapping):
                refs.append(EvidenceRef.from_mapping(raw))
        origin = str(proposal.get("origin") or "model_proposal")
        return Hypothesis(
            id=str(proposal.get("id") or ""),
            claim=claim,
            subject=str(proposal.get("subject") or "situation"),
            facet=str(proposal.get("facet") or "general"),
            confidence=min(self.config.inferred_confidence_cap, clamp01(proposal.get("confidence", 0.0))),
            uncertainty=clamp01(proposal.get("uncertainty", 1.0), 1.0),
            overread_risk=clamp01(proposal.get("overread_risk", 0.4), 0.4),
            evidence_refs=tuple(refs),
            origin=origin,
            horizon_hint=BeliefHorizon.TURN_STATE,
            direct_assertion=False,
            metadata=dict(proposal.get("metadata") or {}),
        )

    # ------------------------------------------------------------------
    # Action selection and episodes
    # ------------------------------------------------------------------
    def select_action(self, candidates: Sequence[ActionCandidate], *, hypothesis_set: HypothesisSet) -> ActionCandidate | None:
        if not candidates:
            return None
        uncertainty = hypothesis_set.uncertainty
        if uncertainty >= self.config.high_uncertainty_threshold:
            clarification = [item for item in candidates if item.action_type in {"clarify", "neutral_reply"}]
            risky = any(item.high_consequence or item.intrusion_risk >= 0.45 for item in candidates)
            if clarification and risky:
                return max(clarification, key=lambda item: self._action_score(item) + item.epistemic_value)
        return max(candidates, key=self._action_score)

    def start_episode(
        self,
        *,
        channel: str,
        observations: Sequence[Observation],
        hypothesis_set: HypothesisSet,
        action: ActionCandidate | None,
        frame_id: str = "",
        expected_outcome: str = "unknown",
        metadata: Mapping[str, Any] | None = None,
    ) -> CognitiveEpisode:
        episode = CognitiveEpisode(
            id=stable_id("episode", frame_id, channel, time.time_ns()),
            channel=channel,
            started_at=time.time(),
            observations=tuple(observations),
            hypothesis_set=hypothesis_set,
            action=action,
            expected_outcome=expected_outcome,
            frame_id=frame_id,
            metadata=dict(metadata or {}),
        )
        self.store.add_episode(episode)
        return episode

    def close_episode(self, feedback: EpisodeFeedback | Mapping[str, Any], *, episode_id: str = "") -> tuple[LearningUpdate, ...]:
        report = feedback if isinstance(feedback, EpisodeFeedback) else EpisodeFeedback(**{
            key: feedback.get(key) for key in EpisodeFeedback.__dataclass_fields__ if key in feedback
        })
        episode = next((item for item in reversed(self.store.episodes) if item.id == episode_id), None) if episode_id else self.store.open_episode()
        if episode is None:
            return ()
        if episode.status == "closed":
            return episode.learning_updates

        updates: list[LearningUpdate] = []
        selected = episode.hypothesis_set.selected
        if report.tone_feedback:
            updates.append(self._update_procedural_policy(episode, report))
        elif selected is not None:
            if report.corrected or report.confused or report.felt_intruded is True:
                updates.extend(self._learn_negative(episode, selected, report))
            elif report.accepted is True:
                updates.extend(self._learn_positive(episode, selected, report))

        self._update_calibration(selected, report)
        episode.feedback = report
        episode.learning_updates = tuple(updates)
        episode.status = "closed"
        episode.closed_at = time.time()
        self.store.learning_log.extend(updates)
        self.store.add_episode(episode)
        return tuple(updates)

    # ------------------------------------------------------------------
    # Belief learning
    # ------------------------------------------------------------------
    def _learn_positive(self, episode: CognitiveEpisode, hypothesis: Hypothesis, feedback: EpisodeFeedback) -> list[LearningUpdate]:
        if hypothesis.subject == "interaction" and hypothesis.horizon_hint == BeliefHorizon.TURN_STATE:
            return []
        refs = unique_evidence(hypothesis.evidence_refs)
        if not refs or all(not ref.counts_as_independent for ref in refs):
            return []
        belief = self._matching_belief(hypothesis)
        before = belief.confidence if belief else 0.0
        if belief is None:
            horizon = self._promotion_horizon(hypothesis, refs)
            belief = Belief(
                id="",
                claim=hypothesis.claim,
                subject=hypothesis.subject,
                facet=hypothesis.facet,
                horizon=horizon,
                confidence=min(0.96 if hypothesis.direct_assertion else self.config.inferred_confidence_cap, hypothesis.confidence),
                uncertainty=hypothesis.uncertainty,
                positive_evidence=refs,
                source_hypothesis_ids=(hypothesis.id,),
                direct_assertion=hypothesis.direct_assertion,
            )
        else:
            before_keys = set(belief.independent_positive_keys)
            belief.positive_evidence = unique_evidence((*belief.positive_evidence, *refs))
            after_keys = set(belief.independent_positive_keys)
            reward = self.config.acceptance_reward if after_keys - before_keys else 0.0
            belief.confidence = min(0.96 if belief.direct_assertion else self.config.inferred_confidence_cap, belief.confidence + reward)
            belief.uncertainty = clamp01(belief.uncertainty - reward * 0.7)
            belief.horizon = self._promotion_horizon(hypothesis, belief.positive_evidence)
            belief.source_hypothesis_ids = tuple(dict.fromkeys((*belief.source_hypothesis_ids, hypothesis.id)))
            belief.updated_at = time.time()
        self.store.beliefs[belief.id] = belief
        self.store.save()
        return [self._learning("belief", belief.id, "reinforce", before, belief.confidence, "user accepted selected hypothesis", feedback.evidence)]

    def _learn_negative(self, episode: CognitiveEpisode, hypothesis: Hypothesis, feedback: EpisodeFeedback) -> list[LearningUpdate]:
        belief = self._matching_belief(hypothesis)
        if belief is None:
            return [self._learning("hypothesis", hypothesis.id, "reject", hypothesis.confidence, max(0.0, hypothesis.confidence - self.config.correction_penalty), "user correction rejected selected hypothesis", feedback.evidence)]
        before = belief.confidence
        correction_ref = EvidenceRef(
            id=stable_id("feedback", episode.id, feedback.text),
            source="user_feedback",
            independence_key=f"feedback:{episode.id}",
            source_kind="user_direct",
            directness=1.0,
            reliability=max(0.75, feedback.confidence),
            episode_id=episode.id,
        )
        belief.contradictory_evidence = unique_evidence((*belief.contradictory_evidence, correction_ref))
        belief.confidence = clamp01(belief.confidence - self.config.correction_penalty * max(0.5, feedback.confidence))
        belief.uncertainty = clamp01(belief.uncertainty + 0.28)
        belief.updated_at = time.time()
        self.store.beliefs[belief.id] = belief
        self.store.save()
        return [self._learning("belief", belief.id, "weaken", before, belief.confidence, "user correction contradicted belief", (*feedback.evidence, correction_ref.id))]

    def commit_hypothesis(self, hypothesis: Hypothesis) -> Belief | None:
        """Explicit promotion API used by tests/adapters, still governed by provenance."""
        refs = unique_evidence(hypothesis.evidence_refs)
        if not refs or all(not ref.counts_as_independent for ref in refs):
            return None
        horizon = self._promotion_horizon(hypothesis, refs)
        if horizon == BeliefHorizon.TURN_STATE:
            return None
        belief = Belief(
            id="",
            claim=hypothesis.claim,
            subject=hypothesis.subject,
            facet=hypothesis.facet,
            horizon=horizon,
            confidence=min(0.96 if hypothesis.direct_assertion else self.config.inferred_confidence_cap, hypothesis.confidence),
            uncertainty=hypothesis.uncertainty,
            positive_evidence=refs,
            source_hypothesis_ids=(hypothesis.id,),
            direct_assertion=hypothesis.direct_assertion,
        )
        self.store.beliefs[belief.id] = belief
        self.store.save()
        return belief

    def record_explicit_user_facts(self, hypothesis_set: Any) -> tuple[Belief, ...]:
        """Ghi belief NGAY từ những điều user TỰ KHAI trực tiếp (tôi là/thích/muốn...).

        Đây là ground-truth do user cung cấp — KHÔNG phải suy diễn/đoán của ERIS — nên
        ghi ngay khi quan sát là đúng tri thức luận, và là con đường để bản sắc về user
        tích lũy qua phiên thay vì luôn "như mới quen" (#4). Vẫn đi qua
        ``commit_hypothesis`` nên tôn trọng đủ guard provenance: cần evidence độc lập
        (user_direct) và horizon không phải TURN_STATE. Suy diễn của private-mind KHÔNG
        đi đường này (chúng không phải direct_assertion), guard chống tự-tin-vào-phỏng-
        đoán vẫn nguyên vẹn.
        """

        stored: list[Belief] = []
        for hypothesis in getattr(hypothesis_set, "hypotheses", ()) or ():
            if not getattr(hypothesis, "direct_assertion", False):
                continue
            if getattr(hypothesis, "subject", "") != "user":
                continue
            if self._matching_belief(hypothesis) is not None:
                continue
            belief = self.commit_hypothesis(hypothesis)
            if belief is not None:
                stored.append(belief)
        return tuple(stored)

    def recall(self, query: str = "", *, scope: str = "", horizon: BeliefHorizon | str | None = None, limit: int = 12) -> tuple[Belief, ...]:
        needle = self._normalize(query)
        horizon_value = BeliefHorizon(horizon) if horizon else None
        candidates = []
        for belief in self.store.beliefs.values():
            if scope and belief.subject != scope and belief.facet != scope:
                continue
            if horizon_value and belief.horizon != horizon_value:
                continue
            if needle and not all(token in self._normalize(f"{belief.claim} {belief.subject} {belief.facet}") for token in needle.split()):
                continue
            candidates.append(belief)
        candidates.sort(key=lambda item: (item.confidence - item.uncertainty * 0.25, item.updated_at), reverse=True)
        return tuple(candidates[: max(0, limit)])

    # ------------------------------------------------------------------
    # Private mind and projections
    # ------------------------------------------------------------------
    def ingest_private_hypotheses(self, fragments: Iterable[Any]) -> int:
        added = 0
        for fragment in fragments:
            claim = compact_text(getattr(fragment, "claim", ""), 600)
            if not claim:
                continue
            self.store.pending_hypotheses.append({
                "id": str(getattr(fragment, "id", "") or stable_id("private_hyp", claim)),
                "claim": claim,
                "subject": "user" if str(getattr(fragment, "source", "")) == "user_model_evaluator" else "private_mind",
                "facet": str(getattr(fragment, "metadata", {}).get("facet", "general")) if isinstance(getattr(fragment, "metadata", {}), Mapping) else "general",
                "confidence": min(self.config.inferred_confidence_cap, clamp01(getattr(fragment, "confidence", 0.0))),
                "uncertainty": max(0.45, 1.0 - clamp01(getattr(fragment, "confidence", 0.0))),
                "overread_risk": 0.65,
                "origin": "private_mind",
                "evidence_ids": list(getattr(fragment, "evidence_ids", ()) or ()),
                "storable": False,
                "created_at": time.time(),
            })
            added += 1
        if added:
            self.store.pending_hypotheses = self.store.pending_hypotheses[-100:]
            self.store.save()
        return added

    def user_model_snapshot(self, *, limit: int = 12) -> dict[str, Any]:
        beliefs = [item for item in self.recall(scope="user", limit=limit) if item.horizon != BeliefHorizon.TURN_STATE]
        facets: dict[str, list[dict[str, Any]]] = {}
        for belief in beliefs:
            facets.setdefault(belief.facet, []).append({
                "claim": belief.claim,
                "confidence": round(belief.confidence, 3),
                "uncertainty": round(belief.uncertainty, 3),
                "horizon": belief.horizon.value,
                "evidence_ids": [ref.id for ref in belief.positive_evidence[:6]],
            })
        return {"version": 2, "belief_count": len(beliefs), "model_thickness": self._model_thickness(beliefs), "facets": facets}

    def projection_snapshot(self, subject: str, *, limit: int = 12) -> dict[str, Any]:
        """Read-only projection for legacy self/relationship/development adapters."""
        beliefs = [item for item in self.recall(scope=subject, limit=limit) if item.horizon != BeliefHorizon.TURN_STATE]
        return {
            "version": 2,
            "subject": subject,
            "belief_count": len(beliefs),
            "beliefs": [
                {
                    "id": item.id,
                    "claim": item.claim,
                    "facet": item.facet,
                    "horizon": item.horizon.value,
                    "confidence": round(item.confidence, 3),
                    "uncertainty": round(item.uncertainty, 3),
                    "evidence_ids": [ref.id for ref in item.positive_evidence[:8]],
                }
                for item in beliefs
            ],
        }

    def get_projection(self, name: str) -> dict[str, Any]:
        return dict(self.store.projections.get(str(name), {}))

    def set_projection(self, name: str, payload: Mapping[str, Any], *, reason: str = "") -> dict[str, Any]:
        record = dict(payload)
        record["updated_at"] = time.time()
        if reason:
            record["update_reason"] = compact_text(reason, 240)
        self.store.projections[str(name)] = record
        self.store.save()
        return dict(record)

    def reset_subject(self, subject: str) -> None:
        self.store.beliefs = {
            key: belief for key, belief in self.store.beliefs.items() if belief.subject != subject
        }
        self.store.save()

    def ingest_belief_mutation(self, mutation: Any) -> dict[str, Any]:
        value = getattr(mutation, "value", None)
        raw = value if isinstance(value, Mapping) else {"claim": str(value or "")}
        claim = compact_text(raw.get("claim") or raw.get("text") or getattr(mutation, "reason", ""), 600)
        record = {
            "id": str(getattr(mutation, "id", "") or stable_id("private_mutation", claim)),
            "claim": claim,
            "subject": str(raw.get("subject") or "private_mind"),
            "facet": str(raw.get("domain") or raw.get("facet") or "general"),
            "confidence": min(self.config.inferred_confidence_cap, clamp01(getattr(mutation, "confidence", 0.0))),
            "uncertainty": max(0.50, 1.0 - clamp01(getattr(mutation, "confidence", 0.0))),
            "origin": "private_mind_mutation",
            "evidence_ids": list(getattr(mutation, "evidence_ids", ()) or ()),
            "storable": False,
            "created_at": time.time(),
        }
        self.store.pending_hypotheses.append(record)
        self.store.pending_hypotheses = self.store.pending_hypotheses[-100:]
        self.store.save()
        return record

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _promotion_horizon(self, hypothesis: Hypothesis, refs: Sequence[EvidenceRef]) -> BeliefHorizon:
        independent = {ref.independence_key for ref in refs if ref.counts_as_independent}
        independent_episodes = {ref.episode_id for ref in refs if ref.counts_as_independent and ref.episode_id}
        if hypothesis.direct_assertion and any(ref.directness >= 0.9 and ref.source_kind == "user_direct" for ref in refs):
            return BeliefHorizon.PERSISTENT_BELIEF
        if len(independent_episodes) >= self.config.persistent_independent_episodes:
            return BeliefHorizon.PERSISTENT_BELIEF
        if len(independent) >= self.config.session_independent_groups:
            return BeliefHorizon.SESSION_PATTERN
        return BeliefHorizon.TURN_STATE

    def _matching_belief(self, hypothesis: Hypothesis) -> Belief | None:
        key = self._normalize(hypothesis.claim)
        return next((item for item in self.store.beliefs.values() if item.subject == hypothesis.subject and item.facet == hypothesis.facet and self._normalize(item.claim) == key), None)

    def _update_procedural_policy(self, episode: CognitiveEpisode, feedback: EpisodeFeedback) -> LearningUpdate:
        key = f"tone:{episode.action.action_type if episode.action else 'unknown'}"
        policy = self.store.procedural_policies.setdefault(key, {"success": 0.0, "failure": 0.0, "last_feedback": ""})
        before = float(policy.get("failure", 0.0))
        if feedback.relation_improved:
            policy["success"] = float(policy.get("success", 0.0)) + 1.0
            operation = "reinforce"
            after = float(policy["success"])
        else:
            policy["failure"] = before + 1.0
            operation = "suppress"
            after = float(policy["failure"])
        policy["last_feedback"] = compact_text(feedback.text, 240)
        self.store.save()
        return self._learning("procedural_policy", key, operation, before, after, "tone feedback updates behavior policy, not user identity", feedback.evidence)

    def _update_calibration(self, hypothesis: Hypothesis | None, feedback: EpisodeFeedback) -> None:
        if hypothesis is None:
            return
        bucket = self.store.calibration.setdefault(hypothesis.origin, {"predictions": 0.0, "accepted": 0.0, "corrected": 0.0, "intruded": 0.0})
        bucket["predictions"] += 1.0
        if feedback.accepted is True:
            bucket["accepted"] += 1.0
        if feedback.corrected or feedback.confused:
            bucket["corrected"] += 1.0
        if feedback.felt_intruded is True:
            bucket["intruded"] += 1.0

    def _calibrated_confidence(self, origin: str, confidence: float) -> float:
        bucket = self.store.calibration.get(origin)
        if not bucket or float(bucket.get("predictions", 0.0)) < 2.0:
            return min(self.config.inferred_confidence_cap, clamp01(confidence))
        predictions = max(1.0, float(bucket.get("predictions", 0.0)))
        accepted = float(bucket.get("accepted", 0.0))
        corrected = float(bucket.get("corrected", 0.0))
        intruded = float(bucket.get("intruded", 0.0))
        reliability = clamp01((accepted + 1.0) / (predictions + 2.0))
        penalty = clamp01((corrected + intruded) / predictions) * 0.35
        calibrated = confidence * (0.72 + reliability * 0.28) - penalty
        return min(self.config.inferred_confidence_cap, clamp01(calibrated))

    def _action_score(self, candidate: ActionCandidate) -> float:
        policy = self.store.procedural_policies.get(f"tone:{candidate.action_type}", {})
        success = float(policy.get("success", 0.0) or 0.0)
        failure = float(policy.get("failure", 0.0) or 0.0)
        learned_adjustment = (success - failure) / max(4.0, success + failure + 2.0)
        return candidate.utility + learned_adjustment

    def _learning(self, target_kind: str, target_id: str, operation: str, before: float | None, after: float | None, reason: str, evidence: Iterable[str]) -> LearningUpdate:
        return LearningUpdate(
            id=stable_id("learn", target_kind, target_id, operation, time.time_ns()),
            target_kind=target_kind,
            target_id=target_id,
            operation=operation,
            before=before,
            after=after,
            reason=reason,
            evidence_ids=tuple(dict.fromkeys(str(item) for item in evidence if str(item or "").strip())),
        )

    def _extract_explicit_user_fact(self, text: str) -> str:
        clean = compact_text(text, 400)
        for pattern in self._EXPLICIT_USER_FACT:
            if pattern.match(clean):
                return f"User explicitly stated: {clean}"
        return ""

    def _dedupe_hypotheses(self, hypotheses: Sequence[Hypothesis]) -> list[Hypothesis]:
        result: dict[tuple[str, str, str], Hypothesis] = {}
        for item in hypotheses:
            key = (item.subject, item.facet, self._normalize(item.claim))
            old = result.get(key)
            if old is None or item.confidence > old.confidence:
                result[key] = item
        return list(result.values())

    def _model_thickness(self, beliefs: Sequence[Belief]) -> float:
        if not beliefs:
            return 0.0
        avg = sum(item.confidence * (1.0 - item.uncertainty * 0.5) for item in beliefs) / len(beliefs)
        coverage = min(1.0, len({item.facet for item in beliefs}) / 6.0)
        return round(clamp01(avg * 0.7 + coverage * 0.3), 3)

    @staticmethod
    def _normalize(text: str) -> str:
        value = unicodedata.normalize("NFD", str(text or "").lower())
        value = "".join(char for char in value if unicodedata.category(char) != "Mn")
        value = value.replace("đ", "d")
        return " ".join(re.sub(r"[^a-z0-9_\s]", " ", value).split())
