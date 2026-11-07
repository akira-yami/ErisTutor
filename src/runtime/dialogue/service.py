# -*- coding: utf-8 -*-
"""Dialogue delivery service from MindDecision to DeliveryReceipt."""
from __future__ import annotations

import asyncio
import time
import uuid
from typing import Callable

from cortex.subject.contracts import MindDecision
from .contracts import DialoguePlan, PerformanceIntent, SocialRiskPolicy
from .discourse import DiscourseService
from .incremental_speech import IncrementalSpeechCoordinator
from .realizer import NaturalDialogueRealizer
from .rolling_thought import RollingThoughtService


class DialogueService:
    def __init__(
        self,
        *,
        realizer: NaturalDialogueRealizer,
        coordinator: IncrementalSpeechCoordinator,
        discourse: DiscourseService,
        thoughts: RollingThoughtService,
        social_policy_provider: Callable[[str], SocialRiskPolicy],
        clock: Callable[[], float] = time.time,
        id_factory: Callable[[], str] | None = None,
    ) -> None:
        self._realizer = realizer
        self._coordinator = coordinator
        self._discourse = discourse
        self._thoughts = thoughts
        self._social_policy_provider = social_policy_provider
        self._clock = clock
        self._id_factory = id_factory or (lambda: str(uuid.uuid4()))

    def plan_from_decision(self, decision: MindDecision) -> tuple[str, DialoguePlan]:
        session_id = str(decision.arguments.get("session_id") or "default")
        commitments = tuple(str(item).strip() for item in decision.arguments.get("semantic_commitments", ()) if str(item).strip())
        if not commitments:
            text = str(decision.arguments.get("text") or "").strip()
            if text:
                commitments = (text,)
        if not commitments:
            raise ValueError("speak decision requires semantic_commitments or text")
        plan_id = self._id_factory()
        performance = PerformanceIntent(
            plan_id=plan_id,
            energy=max(0.0, min(1.0, float(decision.arguments.get("energy", 0.5)))),
            pace=max(0.0, min(1.0, float(decision.arguments.get("pace", 0.5)))),
            vulnerability=max(0.0, min(1.0, float(decision.arguments.get("vulnerability", 0.0)))),
            restraint=max(0.0, min(1.0, float(decision.arguments.get("restraint", 0.5)))),
            gaze_target=str(decision.arguments.get("gaze_target") or "user"),
            body_cues=tuple(str(item) for item in decision.arguments.get("body_cues", ())),
        )
        plan = DialoguePlan(
            plan_id=plan_id,
            decision_id=decision.decision_id,
            speech_act=str(decision.arguments.get("speech_act") or "inform"),
            semantic_commitments=commitments,
            optional_material=tuple(str(item) for item in decision.arguments.get("optional_material", ())),
            forbidden_factual_additions=tuple(str(item) for item in decision.arguments.get("forbidden_factual_additions", ())),
            preferred_words=tuple(str(item) for item in decision.arguments.get("preferred_words", ())),
            hard_max_words=max(1, int(decision.arguments.get("hard_max_words", 120))),
            continuation_hooks=tuple(str(item) for item in decision.arguments.get("continuation_hooks", ())),
            performance_intent=performance,
            evidence_refs=tuple(dict.fromkeys((decision.observation_ref, *decision.belief_refs, *decision.appraisal_refs))),
        )
        return session_id, plan

    async def deliver(self, decision: MindDecision, *, cancellation: asyncio.Event | None = None):
        session_id, plan = self.plan_from_decision(decision)
        cancellation = cancellation or asyncio.Event()
        discourse = self._discourse.get(session_id)
        thoughts = self._thoughts.get(session_id)
        policy = self._social_policy_provider(session_id)
        chunks = self._realizer.realize(plan=plan, discourse=discourse, thoughts=thoughts, social_policy=policy)
        receipt = await self._coordinator.speak(chunks=chunks, performance=plan.performance_intent, cancellation=cancellation)
        self._discourse.update(
            session_id,
            eris_act=plan.speech_act,
            active_topic=str(decision.arguments.get("topic") or discourse.active_topic),
            initiative_delta=-0.1,
        )
        return receipt
