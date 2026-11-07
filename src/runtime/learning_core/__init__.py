"""Learning kernel duoc Eris adaptation tu y tuong/algorithm cua DeepTutor.

Boundary can nho:
- Package nay la deterministic learning substrate, KHONG phai root agent.
- DeepTutor-derived phan duoc giu: contracts, mastery gate, navigation policy,
  scheduler/review, choice grading va pending-interaction mechanics.
- Eris tu so huu: activation vao hoi thoai, TutorReasoner, TurnAgentLoop,
  ConversationDirector, voice pacing, persistence integration va public writer.

Khong module nao trong package nay duoc phep import/so huu TTS, public writer,
conversation pacing hay autonomous foreground lifecycle. Neu can mot capability
ngoai learning kernel, hay dua no qua LearningService/TutorRole boundary.

Nguon adaptation: HKUDS/DeepTutor @ ef2d9e5c, Apache License 2.0.
Xem THIRD_PARTY_NOTICES.md o repo root.
"""
from __future__ import annotations

from runtime.learning_core.contracts import (
    KnowledgePoint,
    LearnerProfile,
    LearningEvidence,
    LearningPath,
    MasteryRecord,
    PendingInteraction,
    ReviewItem,
)
from runtime.learning_core.mastery import (
    MasteryPolicy,
    compute_mastery,
    gate_passed,
)
from runtime.learning_core.policy import (
    NavigationPolicy,
    TutorStep,
    next_step,
)
from runtime.learning_core.scheduler import (
    ReviewScheduler,
    compute_retrievability,
    due_items,
)
from runtime.learning_core.grading import (
    GradingResult,
    grade_choice,
)
from runtime.learning_core.pending import (
    PendingInteractionLedger,
    resolve_pending,
)
from runtime.learning_core.activation import (
    ActivationCooldown,
    ActivationDecision,
    ActivationLevel,
    decide_activation,
)
from runtime.learning_core.service import LearningService
from runtime.learning_core.store import LearningStore

__all__ = [
    # contracts
    "KnowledgePoint",
    "LearnerProfile",
    "LearningEvidence",
    "LearningPath",
    "MasteryRecord",
    "PendingInteraction",
    "ReviewItem",
    # mastery
    "MasteryPolicy",
    "compute_mastery",
    "gate_passed",
    # policy
    "NavigationPolicy",
    "TutorStep",
    "next_step",
    # scheduler
    "ReviewScheduler",
    "compute_retrievability",
    "due_items",
    # grading
    "GradingResult",
    "grade_choice",
    # pending
    "PendingInteractionLedger",
    "resolve_pending",
    # activation
    "ActivationCooldown",
    "ActivationDecision",
    "ActivationLevel",
    "decide_activation",
    # service + store
    "LearningService",
    "LearningStore",
]
