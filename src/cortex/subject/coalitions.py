# -*- coding: utf-8 -*-
"""Dựng, chấm và materialize coalition cho Subject Core.

Module này không còn độc quyền chọn hành động. Nó chuẩn bị tập lựa chọn có nền
nhân quả để Executive cam kết; ``choose`` chỉ còn là fallback tương thích cho
những caller cũ và test deterministic.
"""
from __future__ import annotations

import hashlib
import math
import random
from dataclasses import dataclass
from typing import Any, Callable, Mapping

from .contracts import (
    ActionProposal,
    Belief,
    CoalitionCandidate,
    CommitmentLedger,
    MindDecision,
    OrganismicSubstrate,
    Value,
)


@dataclass(frozen=True, slots=True)
class CoalitionScore:
    coalition_id: str
    proposals: tuple[ActionProposal, ...]
    score: float
    pragmatic_value: float
    epistemic_value: float
    uncertainty: float


class CoalitionArbiter:
    DEFAULT_WEIGHTS: Mapping[str, float] = {
        "expected_need_relief": 0.30,
        "expected_action_efficacy": 0.20,
        "expected_epistemic_value": 0.15,
        "value_congruence": 0.15,
        "project_contribution": 0.10,
        "historical_success": 0.10,
        "substrate_cost": -0.15,
        "uncertainty": -0.10,
        "commitment_conflict": -0.10,
    }

    def __init__(
        self,
        *,
        id_factory: Callable[[], str],
        clock: Callable[[], float],
        random_source: random.Random | None = None,
        weights: Mapping[str, float] | None = None,
        deterministic: bool = True,
    ) -> None:
        self._id_factory = id_factory
        self._clock = clock
        self._random = random_source or random.Random(0)
        self._weights = dict(weights or self.DEFAULT_WEIGHTS)
        self._deterministic = deterministic

    @staticmethod
    def _compatibility_key(proposal: ActionProposal) -> tuple[str, tuple[tuple[str, str], ...]]:
        stable_args = tuple(sorted((str(key), repr(value)) for key, value in proposal.arguments.items()))
        return proposal.action_kind, stable_args

    def build_compatible_coalitions(
        self,
        proposals: tuple[ActionProposal, ...],
    ) -> tuple[tuple[ActionProposal, ...], ...]:
        grouped: dict[tuple[str, tuple[tuple[str, str], ...]], list[ActionProposal]] = {}
        for proposal in proposals:
            grouped.setdefault(self._compatibility_key(proposal), []).append(proposal)
        return tuple(tuple(grouped[key]) for key in sorted(grouped, key=repr))

    def _score(self, coalition: tuple[ActionProposal, ...]) -> CoalitionScore:
        if not coalition:
            raise ValueError("coalition must not be empty")

        def aggregate(name: str) -> float:
            values = [float(getattr(proposal, name)) for proposal in coalition]
            # Nhiều motive tương thích được cộng bằng chứng, nhưng không được nhân
            # utility vô hạn chỉ vì cùng một ý được diễn đạt thành nhiều proposal.
            return min(1.0, max(values) + 0.15 * sum(sorted(values, reverse=True)[1:]))

        components = {name: aggregate(name) for name in self._weights}
        total = sum(self._weights[name] * components[name] for name in self._weights)
        digest = hashlib.sha256(
            "|".join(sorted(p.proposal_id for p in coalition)).encode("utf-8")
        ).hexdigest()[:16]
        return CoalitionScore(
            coalition_id=f"coalition:{digest}",
            proposals=coalition,
            score=total,
            pragmatic_value=max(
                0.0,
                min(
                    1.0,
                    (
                        components["expected_need_relief"]
                        + components["expected_action_efficacy"]
                        + components["value_congruence"]
                    )
                    / 3.0,
                ),
            ),
            epistemic_value=components["expected_epistemic_value"],
            uncertainty=components["uncertainty"],
        )

    @staticmethod
    def _primary(proposals: tuple[ActionProposal, ...]) -> ActionProposal:
        return sorted(proposals, key=lambda p: (-p.expected_need_relief, p.proposal_id))[0]

    def rank(self, proposals: tuple[ActionProposal, ...]) -> tuple[CoalitionCandidate, ...]:
        """Trả tập coalition đã chấm để Executive lựa chọn.

        Hàm này cố ý không tạo ``MindDecision``. Quyền cam kết nằm ở Executive;
        Subject Core chỉ bảo đảm mọi lựa chọn đều có proposal và evidence hợp lệ.
        """
        if not proposals:
            raise ValueError("at least one proposal is required")
        scores = tuple(self._score(item) for item in self.build_compatible_coalitions(proposals))
        ordered = sorted(scores, key=lambda item: (-item.score, item.coalition_id))
        output: list[CoalitionCandidate] = []
        for score in ordered:
            primary = self._primary(score.proposals)
            supporting = tuple(
                dict.fromkeys(
                    need
                    for proposal in score.proposals
                    for need in proposal.supporting_need_ids
                )
            )
            evidence = tuple(
                dict.fromkeys(
                    ref
                    for proposal in score.proposals
                    for ref in proposal.evidence_refs
                )
            )
            output.append(
                CoalitionCandidate(
                    coalition_id=score.coalition_id,
                    proposal_refs=tuple(p.proposal_id for p in score.proposals),
                    primary_proposal_ref=primary.proposal_id,
                    action_kind=primary.action_kind,
                    arguments=primary.arguments,
                    supporting_need_ids=supporting,
                    score=score.score,
                    expected_pragmatic_value=score.pragmatic_value,
                    expected_epistemic_value=score.epistemic_value,
                    uncertainty=score.uncertainty,
                    evidence_refs=evidence,
                )
            )
        return tuple(output)

    def select_default(
        self,
        candidates: tuple[CoalitionCandidate, ...],
        *,
        temperature: float = 0.20,
    ) -> CoalitionCandidate:
        """Fallback khi Executive không tồn tại; không dùng ở đường production mới."""
        if not candidates:
            raise ValueError("at least one coalition candidate is required")
        if self._deterministic or temperature <= 0 or len(candidates) == 1:
            return candidates[0]
        maximum = max(item.score for item in candidates)
        weights = [math.exp((item.score - maximum) / max(temperature, 1e-6)) for item in candidates]
        return self._random.choices(candidates, weights=weights, k=1)[0]

    def build_decision(
        self,
        candidate: CoalitionCandidate,
        *,
        proposals: tuple[ActionProposal, ...],
        all_candidates: tuple[CoalitionCandidate, ...],
        beliefs: tuple[Belief, ...] = (),
        values: tuple[Value, ...] = (),
        observation_ref: str = "",
        appraisal_refs: tuple[str, ...] = (),
        substrate_ref: str = "",
        executive_trace: Mapping[str, Any] | None = None,
    ) -> MindDecision:
        """Materialize quyết định sau khi Executive đã cam kết một coalition."""
        proposal_index = {proposal.proposal_id: proposal for proposal in proposals}
        winner_proposals = tuple(
            proposal_index[ref]
            for ref in candidate.proposal_refs
            if ref in proposal_index
        )
        if not winner_proposals or candidate.primary_proposal_ref not in proposal_index:
            raise ValueError("selected coalition does not match prepared proposals")
        primary = proposal_index[candidate.primary_proposal_ref]
        supporting = tuple(
            dict.fromkeys(
                need
                for proposal in winner_proposals
                for need in proposal.supporting_need_ids
            )
        )
        dissenting = tuple(
            dict.fromkeys(
                need
                for other in all_candidates
                if other.coalition_id != candidate.coalition_id
                for need in other.supporting_need_ids
                if need not in supporting
            )
        )

        # Chỉ giữ refs thực sự liên quan. Executive được thấy projection bounded,
        # không được trao một kho nội tâm toàn tri để rồi vô tình viết lại chính nó.
        value_refs: list[str] = []
        for proposal in winner_proposals:
            trace = proposal.arguments.get("inner_trace")
            if isinstance(trace, Mapping):
                value_refs.extend(str(item) for item in trace.get("value_refs", ()))
        if not value_refs and primary.arguments.get("principle"):
            principle = str(primary.arguments.get("principle")).casefold()
            value_refs.extend(
                value.value_id
                for value in values
                if principle in value.statement.casefold()
                or value.statement.casefold() in principle
            )
        belief_refs = tuple(
            belief.belief_id
            for belief in beliefs
            if belief.status == "active" and belief.confidence >= 0.55
        )[:8]

        arguments = dict(primary.arguments)
        if executive_trace:
            arguments["executive_commit"] = dict(executive_trace)

        return MindDecision(
            decision_id=self._id_factory(),
            coalition_id=candidate.coalition_id,
            proposed_action=primary.action_kind,
            arguments=arguments,
            expected_pragmatic_value=candidate.expected_pragmatic_value,
            expected_epistemic_value=candidate.expected_epistemic_value,
            uncertainty=candidate.uncertainty,
            supporting_need_ids=supporting,
            dissenting_need_ids=dissenting,
            belief_refs=belief_refs,
            value_refs=tuple(dict.fromkeys(value_refs))[:8],
            observation_ref=observation_ref,
            appraisal_refs=appraisal_refs,
            substrate_ref=substrate_ref,
            proposal_refs=tuple(proposal.proposal_id for proposal in winner_proposals),
            created_at=self._clock(),
        )

    def choose(
        self,
        proposals: tuple[ActionProposal, ...],
        *,
        beliefs: tuple[Belief, ...] = (),
        values: tuple[Value, ...] = (),
        commitments: tuple[CommitmentLedger, ...] = (),
        substrate: OrganismicSubstrate | None = None,
        observation_ref: str = "",
        appraisal_refs: tuple[str, ...] = (),
        substrate_ref: str = "",
        temperature: float = 0.20,
    ) -> MindDecision:
        """API tương thích: chọn mặc định rồi materialize như trước."""
        del commitments, substrate
        candidates = self.rank(proposals)
        winner = self.select_default(candidates, temperature=temperature)
        return self.build_decision(
            winner,
            proposals=proposals,
            all_candidates=candidates,
            beliefs=beliefs,
            values=values,
            observation_ref=observation_ref,
            appraisal_refs=appraisal_refs,
            substrate_ref=substrate_ref,
            executive_trace={"source": "coalition_arbiter_fallback"},
        )
