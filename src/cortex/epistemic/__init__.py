"""Epistemic Architecture V2.

The package owns structured observations, competing hypotheses, calibrated
beliefs, cognitive episodes, and outcome-driven learning.  It deliberately has
no dependency on an LLM: models may propose hypotheses, but only this kernel can
promote them into beliefs.
"""

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
)
from .kernel import EpistemicKernel, EpistemicKernelConfig
from .projections import EpistemicAgentMindProjection, EpistemicUserModelProjection, epistemic_development_system
from .runtime_registry import get_default_kernel, set_default_kernel
from .store import EpistemicStore

__all__ = [
    "ActionCandidate",
    "Belief",
    "BeliefHorizon",
    "CognitiveEpisode",
    "EpisodeFeedback",
    "EpistemicKernel",
    "EpistemicKernelConfig",
    "EpistemicStore",
    "EpistemicAgentMindProjection",
    "EpistemicUserModelProjection",
    "EvidenceRef",
    "Hypothesis",
    "HypothesisSet",
    "LearningUpdate",
    "Observation",
    "epistemic_development_system",
    "get_default_kernel",
    "set_default_kernel",
]
