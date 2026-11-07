# -*- coding: utf-8 -*-
"""Cổng biến sự kiện runtime thành WorldObservation có provenance.

Ingress chuẩn hóa input và giữ direct chat availability-first. User turns được
fast-commit trong một MindLoop rồi chỉ *lên lịch* advisory Neuro continuation;
autonomous/blocking deliberation vẫn thuộc MindLoop. Adapter frame/outcome cũ đã xóa.
"""
from __future__ import annotations

import uuid
from typing import Any, Callable, Mapping, Sequence

from cortex.subject.contracts import (
    MindDecision, ObservationOrigin, SubjectDeliberationFrame,
    WorldObservation,
)
from cortex.subject.service import SubjectService
from runtime.mind_loop import DeliberationSession, MindLoop, SubjectCommitPort
from runtime.subject_proposals import AutonomousProposalSource



class SubjectIngress:
    """Tạo observation cho chat/autonomous và giao session cho MindLoop."""

    def __init__(
        self,
        service: SubjectService,
        *,
        mind_loop: MindLoop | None = None,
        autonomous_proposals: AutonomousProposalSource | None = None,
        neuro_continuation: Any | None = None,
        proposal_mirror: Callable[[Sequence[Mapping[str, Any]]], Any] | None = None,
    ) -> None:
        self.service = service
        self.mind_loop = mind_loop or MindLoop(subject=service)
        self.autonomous_proposals = autonomous_proposals or AutonomousProposalSource(service)
        self.neuro_continuation = neuro_continuation
        # Chỉ đọc/chiếu đề xuất; Subject vẫn sở hữu deliberation gốc.
        self._proposal_mirror = proposal_mirror

    def user_observation(
        self,
        user_text: str,
        *,
        created_at: float,
        turn_id: str,
        planning_context: Mapping[str, Any] | None = None,
    ) -> WorldObservation:
        text = str(user_text or "").strip()
        context = dict(planning_context or {})
        uncertainty = 0.65 if "?" in text else 0.35
        initiative_runtime = (
            context.get("initiative_runtime_context")
            if isinstance(context.get("initiative_runtime_context"), Mapping)
            else {}
        )
        handoff = (
            context.get("conversation_handoff")
            if isinstance(context.get("conversation_handoff"), Mapping)
            else {}
        )
        trajectory = (
            context.get("conversation_trajectory")
            if isinstance(context.get("conversation_trajectory"), Mapping)
            else {}
        )
        causal_discourse = (
            context.get("causal_discourse")
            if isinstance(context.get("causal_discourse"), Mapping)
            else {}
        )
        courtesy = (
            context.get("conversation_courtesy")
            if isinstance(context.get("conversation_courtesy"), Mapping)
            else {}
        )
        life_horizon = (
            context.get("life_horizon")
            if isinstance(context.get("life_horizon"), Mapping)
            else {}
        )
        persistent_individual = (
            context.get("persistent_individual")
            if isinstance(context.get("persistent_individual"), Mapping)
            else {}
        )
        explicit_proposals = []
        repair_proposal = context.get("conversation_repair_proposal")
        if isinstance(repair_proposal, Mapping):
            # V71: causal repair là typed action proposal đã có evidence. Subject
            # Core được quyền cân nhắc nó như một hành động thật thay vì chỉ thấy
            # nhãn cảm xúc rồi hỏi user một câu context-free.
            explicit_proposals.append(dict(repair_proposal))
        return WorldObservation(
            observation_id=f"user-turn:{turn_id}:{uuid.uuid4()}",
            subject_id=self.service.config.subject_id,
            kind="user_turn",
            created_at=float(created_at),
            origin=ObservationOrigin.USER,
            context={
                "context_key": "direct_chat",
                "user_text": text,
                "affected_need_ids": ("connection", "coherence", "continuity"),
                "goal_relevance": 0.92,
                "novelty": 0.45,
                "expectation_violation": 0.10,
                "controllability": 0.88,
                "relational_significance": 0.76,
                "uncertainty": uncertainty,
                "primary_interpretation": "người dùng chủ động mở một lượt hội thoại công khai",
                "action_proposals": tuple(explicit_proposals),
                "runtime_context": {
                    "memory_available": bool(context.get("memory_available")),
                    "engagement_active": bool((context.get("engagement_context") or {}).get("active", False))
                    if isinstance(context.get("engagement_context"), Mapping)
                    else False,
                    "active_concerns": tuple(
                        dict(item) for item in tuple(context.get("active_concerns", ()) or ())[:6]
                        if isinstance(item, Mapping)
                    ),
                    # V71: hai object này là state giữa hai người, không phải
                    # long-term memory. Đưa thẳng vào runtime_context để cả Subject
                    # và Neuro semantic critic nhìn cùng một handoff authoritative.
                    "conversation_handoff": dict(handoff),
                    "conversation_trajectory": dict(trajectory),
                    "causal_discourse": dict(causal_discourse),
                    # V74: Courtesy phải nằm top-level runtime_context để hard
                    # expression boundary đọc deterministic, không parse prompt.
                    "conversation_courtesy": dict(courtesy),
                    "life_horizon": dict(life_horizon),
                    # V76: ecology/relational-plan/self-model projection typed.
                    "persistent_individual": dict(persistent_individual),
                    "courtesy_hold": bool(courtesy.get("courtesy_hold", courtesy.get("effective_wait", False))),
                    "awaiting_user_answer": bool(courtesy.get("awaiting_user_answer", False)),
                    "pending_question_id": str(courtesy.get("pending_question_id", "") or ""),
                    **dict(initiative_runtime),
                },
            },
            evidence_refs=(turn_id,),
            causal_refs=(),
            correlation_id=turn_id,
        )

    def prepare_session(
        self,
        observation: WorldObservation,
        *,
        mode: str,
        user_text: str = "",
        planning_context: Mapping[str, Any] | None = None,
    ) -> DeliberationSession:
        """Một cổng deliberation cho mọi origin.

        V26 bỏ ý tưởng chat/autonomous là hai tâm trí. Chúng chỉ là hai nguồn
        observation khác nhau đi qua cùng MindLoop và cùng Subject commit.
        """
        return self.mind_loop.prepare_sync(
            observation,
            mode=mode,
            user_text=user_text,
            snapshot=planning_context or {},
        )

    def complete_session(
        self,
        session: DeliberationSession,
        *,
        committer: SubjectCommitPort | None = None,
    ) -> MindDecision:
        return self.mind_loop.complete_sync(session, committer=committer)

    def observe(
        self,
        observation: WorldObservation,
        *,
        mode: str,
        user_text: str = "",
        planning_context: Mapping[str, Any] | None = None,
        committer: SubjectCommitPort | None = None,
    ) -> MindDecision:
        session = self.prepare_session(
            observation,
            mode=mode,
            user_text=user_text,
            planning_context=planning_context,
        )
        return self.complete_session(session, committer=committer)

    def prepare_user_session(
        self,
        user_text: str,
        *,
        created_at: float,
        turn_id: str,
        planning_context: Mapping[str, Any] | None = None,
    ) -> DeliberationSession:
        self.service.note_relational_user_turn(
            now=created_at,
            meaningful=bool(str(user_text or "").strip()),
            engagement=0.82,
            evidence_ref=turn_id,
            persist=True,
        )
        observation = self.user_observation(
            user_text,
            created_at=created_at,
            turn_id=turn_id,
            planning_context=planning_context,
        )
        return self.prepare_session(
            observation,
            mode="interactive",
            user_text=user_text,
            planning_context=planning_context,
        )


    def autonomous_observation(
        self,
        blackboard: Any,
        affordances: Sequence[Any],
        *,
        created_at: float,
        turn_id: str,
        planning_context: Mapping[str, Any] | None = None,
    ) -> WorldObservation:
        context = dict(planning_context or {})
        proposals, proposal_context = self.autonomous_proposals.collect(
            blackboard,
            affordances,
            turn_id=turn_id,
            planning_context=context,
        )
        # P2: chia sẻ semantic hints vào bus cho Root, không thay đổi các
        # proposal do Subject dùng tính utility hay action của MindLoop.
        if callable(self._proposal_mirror):
            try:
                self._proposal_mirror(proposals)
            except Exception:
                # Optional projection; không làm đổ nguyên một Subject turn.
                pass
        reply_expected = bool(proposal_context["reply_expected"])
        user_busy = bool(proposal_context.get("user_busy", False))
        engagement_active = bool(proposal_context["engagement_active"])
        relational = context.get("relational_state") if isinstance(context.get("relational_state"), Mapping) else {}
        initiative_runtime = (
            context.get("initiative_runtime_context")
            if isinstance(context.get("initiative_runtime_context"), Mapping)
            else {}
        )
        handoff = (
            context.get("conversation_handoff")
            if isinstance(context.get("conversation_handoff"), Mapping)
            else {}
        )
        trajectory = (
            context.get("conversation_trajectory")
            if isinstance(context.get("conversation_trajectory"), Mapping)
            else {}
        )
        causal_discourse = (
            context.get("causal_discourse")
            if isinstance(context.get("causal_discourse"), Mapping)
            else {}
        )
        courtesy = (
            context.get("conversation_courtesy")
            if isinstance(context.get("conversation_courtesy"), Mapping)
            else {}
        )
        life_horizon = (
            context.get("life_horizon")
            if isinstance(context.get("life_horizon"), Mapping)
            else {}
        )
        persistent_individual = (
            context.get("persistent_individual")
            if isinstance(context.get("persistent_individual"), Mapping)
            else {}
        )
        relational_pressure = float(relational.get("connection_pressure", 0.0) or 0.0)
        relational_opportunity = float(relational.get("opportunity", 0.0) or 0.0)

        affected = tuple(dict.fromkeys(
            need for proposal in proposals for need in proposal.get("supporting_need_ids", ())
        )) or ("coherence",)
        return WorldObservation(
            observation_id=f"autonomous-turn:{turn_id}:{uuid.uuid4()}",
            subject_id=self.service.config.subject_id,
            kind="autonomous_opportunity",
            created_at=float(created_at),
            origin=ObservationOrigin.INTERNAL,
            context={
                "context_key": "autonomous",
                "affected_need_ids": affected,
                "goal_relevance": max(0.48 if proposals else 0.10, min(0.90, relational_pressure)),
                "novelty": float(proposal_context["novelty"]),
                "expectation_violation": 0.15,
                "controllability": 0.90,
                "relational_significance": max(0.54, min(1.0, relational_pressure)),
                "identity_significance": 0.28,
                "future_consequence": max(0.22, min(0.72, relational_pressure * 0.68)),
                "uncertainty": 0.42,
                "primary_interpretation": "runtime có các cơ hội hành động tự chủ cần Subject+Neuro cân nhắc",
                "action_proposals": tuple(proposals),
                "runtime_context": {
                    "reply_expected": reply_expected,
                    "user_busy": user_busy,
                    "engagement_active": engagement_active,
                    "affordance_count": len(affordances),
                    "snapshot_revision": context.get("state_revision", 0),
                    "partner_available": relational.get("partner_availability", relational_opportunity),
                    "interruption_cost": relational.get("interruption_cost", 0.25),
                    "sharing_signal": relational.get("sharing_pressure", 0.0),
                    "social_curiosity": relational.get("social_curiosity", 0.0),
                    "unresolved_relational": relational.get("affiliation_deficit", 0.0),
                    "relational_evidence_refs": tuple(relational.get("evidence_refs", ())),
                    "active_concerns": tuple(
                        dict(item) for item in tuple(context.get("active_concerns", ()) or ())[:6]
                        if isinstance(item, Mapping)
                    ),
                    "conversation_handoff": dict(handoff),
                    "conversation_trajectory": dict(trajectory),
                    "causal_discourse": dict(causal_discourse),
                    "conversation_courtesy": dict(courtesy),
                    "life_horizon": dict(life_horizon),
                    # V76: ecology/relational-plan/self-model projection typed.
                    "persistent_individual": dict(persistent_individual),
                    "courtesy_hold": bool(courtesy.get("courtesy_hold", courtesy.get("effective_wait", False))),
                    "awaiting_user_answer": bool(courtesy.get("awaiting_user_answer", False)),
                    "pending_question_id": str(courtesy.get("pending_question_id", "") or ""),
                    **dict(initiative_runtime),
                },
            },
            evidence_refs=(turn_id,),
            correlation_id=turn_id,
        )

    def prepare_autonomous_session(
        self,
        blackboard: Any,
        affordances: Sequence[Any],
        *,
        created_at: float,
        turn_id: str,
        planning_context: Mapping[str, Any] | None = None,
    ) -> DeliberationSession:
        observation = self.autonomous_observation(
            blackboard,
            affordances,
            created_at=created_at,
            turn_id=turn_id,
            planning_context=planning_context,
        )
        return self.prepare_session(
            observation,
            mode="autonomous",
            planning_context=planning_context,
        )


    def parallel_cognition_state(self, turn_id: str = "") -> dict[str, Any]:
        runtime = self.neuro_continuation
        public_state = getattr(runtime, "public_state", None)
        if not callable(public_state):
            return {
                "deep_thought_running": False,
                "may_follow_up": False,
                "policy": "parallel_nonblocking",
            }
        try:
            state = public_state(turn_id)
            return dict(state) if isinstance(state, Mapping) else {}
        except Exception:
            return {
                "deep_thought_running": False,
                "may_follow_up": False,
                "policy": "parallel_nonblocking",
            }

    def observe_user_turn(
        self,
        user_text: str,
        *,
        created_at: float,
        turn_id: str,
        planning_context: Mapping[str, Any] | None = None,
        committer: SubjectCommitPort | None = None,
    ) -> MindDecision:
        """Availability-first direct chat with optional parallel Neuro review.

        ``committer`` is accepted for API compatibility but direct chat no longer
        lets a remote committer sit on the public latency path.  The deterministic
        Neuro gate commits immediately; a materially consequential frame may then
        be reviewed advisory-only by ``NeuroContinuationRuntime``.
        """
        del committer
        continuation = self.neuro_continuation
        begin_turn = getattr(continuation, "begin_user_turn", None)
        if callable(begin_turn):
            begin_turn(turn_id)

        session = self.prepare_user_session(
            user_text,
            created_at=created_at,
            turn_id=turn_id,
            planning_context=planning_context,
        )
        fast = self.mind_loop.complete_fast_sync(session)

        schedule = getattr(continuation, "schedule", None)
        if callable(schedule) and fast.selection.parallel_review_recommended:
            try:
                schedule(
                    frame=session.frame,
                    selection=fast.selection,
                    baseline_commit=fast.commit,
                    origin_turn_id=turn_id,
                    user_text=user_text,
                    snapshot=planning_context or {},
                )
            except Exception:
                # Deep review is best-effort by design. The committed public path
                # remains valid even when the continuation worker is unavailable.
                pass
        return fast.decision




__all__ = ["SubjectIngress"]
