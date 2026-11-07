# -*- coding: utf-8 -*-
"""Private mind package cho ERIS.

Public API cố ý nhỏ: contract, concern store, dossier, evaluators, synthesizer,
mutation bus và surface valve. Đừng import vòng vào coordinator ở đây. Import vòng
là nghi lễ hiến tế thời gian debug, không phải kiến trúc.
"""

from .contracts import (
    EvidenceDossier,
    InternalJudgment,
    PrivateConcern,
    PrivateMindTickResult,
    RankedHypothesis,
    StateMutation,
    SurfaceCandidate,
    ThoughtFragment,
)
from .concern_store import ConcernStore
from .comfort_appraiser import ComfortAppraisal, ComfortAppraiser
from .evidence_dossier import EvidenceDossierBuilder
from .inner_synthesizer import LocalModelInnerSynthesizer
from .mini_evaluators import (
    USER_MODEL_EVALUATOR_NAME,
    FutureBiasEvaluator,
    IntentEvaluator,
    MemoryResonanceEvaluator,
    MiniEvaluatorPipeline,
    RiskAndPermissionEvaluator,
    UserModelEvaluator,
)
from .state_mutation_bus import StateMutationBus
from .surface_valve import SurfaceValve
from .thought_continuity import ThoughtContinuityStore, ThoughtEntry
from .user_model import (
    UserBelief,
    UserModelConfig,
    UserModelStore,
    default_user_model_path,
)

__all__ = [
    "ComfortAppraisal",
    "ComfortAppraiser",
    "ConcernStore",
    "EvidenceDossier",
    "EvidenceDossierBuilder",
    "FutureBiasEvaluator",
    "LocalModelInnerSynthesizer",
    "IntentEvaluator",
    "InternalJudgment",
    "MemoryResonanceEvaluator",
    "MiniEvaluatorPipeline",
    "PrivateConcern",
    "PrivateMindTickResult",
    "RankedHypothesis",
    "RiskAndPermissionEvaluator",
    "StateMutation",
    "StateMutationBus",
    "SurfaceCandidate",
    "SurfaceValve",
    "ThoughtContinuityStore",
    "ThoughtEntry",
    "ThoughtFragment",
    "USER_MODEL_EVALUATOR_NAME",
    "UserBelief",
    "UserModelConfig",
    "UserModelEvaluator",
    "UserModelStore",
    "default_user_model_path",
]
