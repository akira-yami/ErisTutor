# -*- coding: utf-8 -*-
"""Long-running interaction engagements for ERIS."""

from .adapters import AdapterRegistry, EngagementAdapter
from .consumer import ConsumerPollResult, EngagementEventConsumer
from .supervisor import ConsumerTickReport, EngagementConsumerSupervisor
from .contracts import (
    ContinuityPolicy,
    Engagement,
    EngagementConsumerState,
    EngagementDeadLetter,
    EngagementDeliveryReceipt,
    EngagementEvent,
    EngagementEventBatch,
    EngagementProposal,
    EngagementReplayReport,
    EngagementReceiptAudit,
    EngagementStreamAudit,
    FocusMode,
    InactivityBehavior,
    InitiativeMode,
    InteractionAction,
    InteractionContract,
    InteractionDecision,
    InteractionSignal,
    LifecycleSignal,
    LifecycleStatus,
    PlannerProjection,
)
from .detector import EngagementDetector
from .evidence import EvidenceBinding, EvidenceEnvelope, EvidenceRouter
from .policy import AttentionManager, EngagementInteractionPolicy
from .profiles import EngagementProfile, EngagementProfileRegistry
from .registry import EngagementRegistry
from .recovery import EngagementRecoveryReport, EngagementRecoveryService
from .runtime import EngagementRuntime
from .snapshot_journal import EngagementSnapshotJournal, SnapshotJournalLoadResult

__all__ = [
    "AdapterRegistry",
    "AttentionManager",
    "ContinuityPolicy",
    "ConsumerPollResult",
    "ConsumerTickReport",
    "Engagement",
    "EngagementConsumerState",
    "EngagementDeadLetter",
    "EngagementDeliveryReceipt",
    "EngagementAdapter",
    "EngagementDetector",
    "EvidenceBinding",
    "EvidenceEnvelope",
    "EvidenceRouter",
    "EngagementEvent",
    "EngagementEventBatch",
    "EngagementEventConsumer",
    "EngagementConsumerSupervisor",
    "EngagementInteractionPolicy",
    "EngagementProposal",
    "EngagementReplayReport",
    "EngagementReceiptAudit",
    "EngagementStreamAudit",
    "EngagementProfile",
    "EngagementProfileRegistry",
    "EngagementRecoveryReport",
    "EngagementRecoveryService",
    "EngagementRegistry",
    "EngagementRuntime",
    "EngagementSnapshotJournal",
    "SnapshotJournalLoadResult",
    "FocusMode",
    "InactivityBehavior",
    "InitiativeMode",
    "InteractionAction",
    "InteractionContract",
    "InteractionDecision",
    "InteractionSignal",
    "LifecycleSignal",
    "LifecycleStatus",
    "PlannerProjection",
]
