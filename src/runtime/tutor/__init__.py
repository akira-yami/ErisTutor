# -*- coding: utf-8 -*-
"""Tutor cognition layer của Eris.

Package này đứng *trên* learning_core và *dưới* public writer/delivery runtime.
"""
from runtime.tutor.contracts import (
    ContextTurn,
    LearnerHypothesis,
    MoveEnvelope,
    SemanticAssessment,
    TeachingMove,
    TutorDecision,
    TutorDiagnosis,
    TutorObservation,
    TutorState,
)
from runtime.tutor.executive import TutorExecutive
from runtime.tutor.composition import TutorRuntimeBundle, build_tutor_runtime
from runtime.tutor.policy import TutorGuardrailPolicy
from runtime.tutor.model_reasoner import RuntimeTutorReasoner
from runtime.tutor.reasoner import (
    ConservativeTutorReasoner,
    MappingTutorReasonerAdapter,
    TutorReasoner,
)

__all__ = [
    "ConservativeTutorReasoner",
    "ContextTurn",
    "LearnerHypothesis",
    "MappingTutorReasonerAdapter",
    "MoveEnvelope",
    "RuntimeTutorReasoner",
    "SemanticAssessment",
    "TeachingMove",
    "TutorDecision",
    "TutorRuntimeBundle",
    "TutorDiagnosis",
    "TutorExecutive",
    "TutorGuardrailPolicy",
    "TutorObservation",
    "TutorReasoner",
    "TutorState",
    "build_tutor_runtime",
]
