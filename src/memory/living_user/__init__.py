# -*- coding: utf-8 -*-
"""Living user memory public API."""

from .action_memory import ActionContext, ActionMemoryStore, CommitmentRecord, GoalRecord, WorkflowRecord
from .benchmark import BenchmarkMetrics, LivingUserMemoryBenchmark
from .authority import AuthorityDecision, UserMemoryAuthorityEvent, UserMemoryAuthorityLedger
from .clarification import MemoryClarificationPolicy, MemoryUseDecision
from .context_compiler import ProfileContextCompiler, ProfileContextCompilerConfig, infer_domain
from .curator import LivingUserCuratorConfig, LivingUserCuratorResult, LivingUserMemoryCurator
from .dreamer import UserProfileDreamConfig, UserProfileDreamer
from .episode_builder import EpisodeBuilderConfig, UserEpisodeBuilder
from .episode_store import UserEpisodeStore
from .extractor import ProfileExtractionConfig, UserProfileExtractor, UserProfileModelAdapter
from .legacy_projection import LivingUserModelProjection
from .models import (
    DreamReport,
    EvidenceReference,
    ProfileContextItem,
    ProfileContextPacket,
    ProfileOperation,
    ProfileRecord,
    UserEpisode,
    UserMemoryScope,
)
from .runtime_adapter import LlmServiceUserProfileAdapter
from .gemini_adapter import GeminiWebUserProfileAdapter
from .portrait_store import SavedGeminiMemoryPortrait
from .presenter import MemoryPortrait, MemoryPortraitItem, MemoryPortraitPresenter, MemoryPortraitSection
from .service import LivingUserMemoryConfig, LivingUserMemoryService, UserTurnObservation
from .sources import MemoryExplanation, MemorySourceTracker
from .store import ApplyResult, UserProfileStore, UserProfileStoreConfig
from .trace import MemoryReplayDebugger, MemoryTraceJournal, ReplayReport

__all__ = [
    "ApplyResult",
    "AuthorityDecision",
    "UserMemoryAuthorityEvent",
    "UserMemoryAuthorityLedger",
    "ActionContext",
    "ActionMemoryStore",
    "BenchmarkMetrics",
    "CommitmentRecord",
    "GoalRecord",
    "LivingUserMemoryBenchmark",
    "LivingUserCuratorConfig",
    "LivingUserCuratorResult",
    "LivingUserMemoryCurator",
    "MemoryClarificationPolicy",
    "MemoryReplayDebugger",
    "MemoryTraceJournal",
    "MemoryUseDecision",
    "ReplayReport",
    "WorkflowRecord",
    "DreamReport",
    "EpisodeBuilderConfig",
    "EvidenceReference",
    "LivingUserMemoryConfig",
    "LivingUserMemoryService",
    "LivingUserModelProjection",
    "LlmServiceUserProfileAdapter",
    "GeminiWebUserProfileAdapter",
    "SavedGeminiMemoryPortrait",
    "MemoryPortrait",
    "MemoryPortraitItem",
    "MemoryPortraitPresenter",
    "MemoryPortraitSection",
    "MemoryExplanation",
    "MemorySourceTracker",
    "ProfileContextCompiler",
    "ProfileContextCompilerConfig",
    "ProfileContextItem",
    "ProfileContextPacket",
    "ProfileExtractionConfig",
    "ProfileOperation",
    "ProfileRecord",
    "UserEpisode",
    "UserEpisodeBuilder",
    "UserEpisodeStore",
    "UserMemoryScope",
    "UserProfileDreamConfig",
    "UserProfileDreamer",
    "UserProfileExtractor",
    "UserProfileModelAdapter",
    "UserProfileStore",
    "UserProfileStoreConfig",
    "UserTurnObservation",
    "infer_domain",
]
