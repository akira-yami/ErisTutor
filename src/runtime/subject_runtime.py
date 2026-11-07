# -*- coding: utf-8 -*-
"""Top-level execution across MindLoop, actuator, dialogue and audit boundaries."""
from __future__ import annotations

import inspect
import time
import uuid
from dataclasses import dataclass
from typing import Any, Callable, Protocol

from cortex.subject.contracts import LearningReceipt, MindDecision, WorldObservation
from cortex.subject.service import SubjectService
from runtime.audit import AuditEvent, AuditEventKind
from runtime.mind_loop import MindLoop
from runtime.world.contracts import ActionReceipt, ActuatorPort


class DialoguePort(Protocol):
    async def deliver(self, decision: MindDecision) -> Any: ...


class AuditSink(Protocol):
    def append(self, event: AuditEvent) -> Any: ...


@dataclass(frozen=True, slots=True)
class RuntimeResult:
    decision: MindDecision
    action_receipt: ActionReceipt | None
    learning_receipt: LearningReceipt | None
    dialogue_receipt: Any = None


class SubjectRuntime:
    """Thực thi một observation sau khi MindLoop đã tạo quyết định có authorship.

    Runtime không giữ hay bind Executive. Executive dependency đã được inject vào
    MindLoop tại composition root, nên không còn mutable wiring sau bootstrap.
    """

    def __init__(
        self,
        *,
        mind: SubjectService,
        actuator: ActuatorPort,
        dialogue: DialoguePort | None,
        audit: AuditSink | None,
        mind_loop: MindLoop | None = None,
        clock: Callable[[], float] = time.time,
        id_factory: Callable[[], str] | None = None,
    ) -> None:
        self._mind = mind
        self._mind_loop = mind_loop or MindLoop(subject=mind)
        self._actuator = actuator
        self._dialogue = dialogue
        self._audit = audit
        self._clock = clock
        self._id_factory = id_factory or (lambda: str(uuid.uuid4()))

    async def _audit_append(self, event: AuditEvent) -> None:
        if self._audit is None:
            return
        result = self._audit.append(event)
        if inspect.isawaitable(result):
            await result

    async def handle(self, observation: WorldObservation) -> RuntimeResult:
        await self._audit_append(AuditEvent(
            event_id=self._id_factory(),
            subject_id=observation.subject_id,
            kind=AuditEventKind.OBSERVATION,
            entity_id=observation.observation_id,
            created_at=self._clock(),
            payload=observation.to_dict(),
            causal_refs=observation.causal_refs,
            correlation_id=observation.correlation_id,
            algorithm_version=self._mind.config.algorithm_version,
        ))
        decision = await self._mind_loop.deliberate(
            observation,
            mode="runtime",
            user_text=str(observation.context.get("user_text") or ""),
            snapshot=dict(observation.context),
        )
        await self._audit_append(AuditEvent(
            event_id=self._id_factory(),
            subject_id=observation.subject_id,
            kind=AuditEventKind.DECISION,
            entity_id=decision.decision_id,
            created_at=self._clock(),
            payload=decision.to_dict(),
            causal_refs=(observation.observation_id, *decision.appraisal_refs, decision.substrate_ref),
            correlation_id=observation.correlation_id,
            algorithm_version=self._mind.config.algorithm_version,
        ))

        if decision.proposed_action == "speak" and self._dialogue is not None:
            dialogue_receipt = await self._dialogue.deliver(decision)
            return RuntimeResult(
                decision=decision,
                action_receipt=None,
                learning_receipt=None,
                dialogue_receipt=dialogue_receipt,
            )

        request = self._mind.to_action_request(
            decision,
            requested_capabilities=tuple(
                str(item) for item in decision.arguments.get("requested_capabilities", ())
            ) or (decision.proposed_action,),
            expected_outcomes=tuple(
                str(item) for item in decision.arguments.get("expected_outcomes", ())
            ),
        )
        await self._audit_append(AuditEvent(
            event_id=self._id_factory(),
            subject_id=observation.subject_id,
            kind=AuditEventKind.ACTION_REQUEST,
            entity_id=request.action_id,
            created_at=self._clock(),
            payload={
                "action_id": request.action_id,
                "decision_id": request.decision_id,
                "kind": request.kind,
                "arguments": dict(request.arguments),
                "requested_capabilities": list(request.requested_capabilities),
                "authorship_draft_id": request.authorship_draft_id,
            },
            causal_refs=(decision.decision_id, request.authorship_draft_id),
            correlation_id=observation.correlation_id,
            algorithm_version=self._mind.config.algorithm_version,
        ))
        receipt = await self._actuator.execute(request)
        outcome = receipt.to_world_outcome()
        await self._audit_append(AuditEvent(
            event_id=self._id_factory(),
            subject_id=observation.subject_id,
            kind=AuditEventKind.WORLD_OUTCOME,
            entity_id=outcome.outcome_id,
            created_at=self._clock(),
            payload=outcome.to_dict(),
            causal_refs=(outcome.action_id, outcome.decision_id),
            correlation_id=observation.correlation_id,
            algorithm_version=self._mind.config.algorithm_version,
        ))
        learning = await self._mind.observe_outcome(outcome)
        await self._audit_append(AuditEvent(
            event_id=self._id_factory(),
            subject_id=observation.subject_id,
            kind=AuditEventKind.LEARNING,
            entity_id=learning.receipt_id,
            created_at=self._clock(),
            payload={
                "outcome_id": learning.outcome_id,
                "closed_episode_ids": list(learning.closed_episode_ids),
                "belief_revision_ids": list(learning.belief_revision_ids),
                "authorship_id": learning.authorship_id,
            },
            causal_refs=(learning.outcome_id, learning.authorship_id, *learning.closed_episode_ids),
            correlation_id=observation.correlation_id,
            algorithm_version=self._mind.config.algorithm_version,
        ))
        return RuntimeResult(decision=decision, action_receipt=receipt, learning_receipt=learning)
