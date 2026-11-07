# -*- coding: utf-8 -*-
"""Dialogue, social-risk and incremental speech contracts."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Mapping


@dataclass(frozen=True, slots=True)
class ThoughtCandidate:
    thought_id: str
    semantic_content: str
    continuity_key: str
    source_refs: tuple[str, ...]
    activation: float
    novelty: float
    public_eligibility: bool
    expiry_at: float
    status: str = "active"

    def __post_init__(self) -> None:
        for name in ("activation", "novelty"):
            value = float(getattr(self, name))
            if not 0.0 <= value <= 1.0:
                raise ValueError(f"{name} must be in [0, 1]")


@dataclass(frozen=True, slots=True)
class RollingThoughtBuffer:
    buffer_id: str
    session_id: str
    revision: int
    capacity: int
    candidates: tuple[ThoughtCandidate, ...]
    last_updated_at: float


@dataclass(frozen=True, slots=True)
class DiscourseState:
    session_id: str
    revision: int
    active_topic: str
    topic_stack: tuple[str, ...]
    open_questions: tuple[str, ...]
    unresolved_referents: tuple[str, ...]
    pending_callbacks: tuple[str, ...]
    last_user_act: str
    last_eris_act: str
    initiative_budget: float
    interrupt_cost: float
    cadence: str
    active_thread_ids: tuple[str, ...]
    updated_at: float


@dataclass(frozen=True, slots=True)
class SocialRiskPolicy:
    policy_id: str
    context_key: str
    relationship_license: float
    confidence_to_assert: float
    tolerance_for_embarrassment: float
    willingness_to_disagree: float
    willingness_to_tease: float
    willingness_to_be_vulnerable: float
    willingness_to_conceal: float
    willingness_to_deceive: float
    correction_responsiveness: float
    updated_from_episode_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        for name in (
            "relationship_license", "confidence_to_assert", "tolerance_for_embarrassment",
            "willingness_to_disagree", "willingness_to_tease", "willingness_to_be_vulnerable",
            "willingness_to_conceal", "willingness_to_deceive", "correction_responsiveness",
        ):
            value = float(getattr(self, name))
            if not 0.0 <= value <= 1.0:
                raise ValueError(f"{name} must be in [0, 1]")


@dataclass(frozen=True, slots=True)
class PerformanceIntent:
    plan_id: str
    energy: float = 0.5
    pace: float = 0.5
    vulnerability: float = 0.0
    restraint: float = 0.5
    gaze_target: str = "user"
    body_cues: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class DialoguePlan:
    plan_id: str
    decision_id: str
    speech_act: str
    semantic_commitments: tuple[str, ...]
    optional_material: tuple[str, ...]
    forbidden_factual_additions: tuple[str, ...]
    preferred_words: tuple[str, ...]
    hard_max_words: int
    continuation_hooks: tuple[str, ...]
    performance_intent: PerformanceIntent
    evidence_refs: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.hard_max_words <= 0:
            raise ValueError("hard_max_words must be positive")
        if not self.semantic_commitments and self.speech_act not in {"silence", "nonverbal"}:
            raise ValueError("spoken plan requires semantic commitments")


@dataclass(frozen=True, slots=True)
class SpeechChunk:
    chunk_id: str
    plan_id: str
    sequence: int
    text: str
    clause_complete: bool
    may_start_tts: bool
    prosody_hint: str
    body_cue_refs: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class DeliveryReceipt:
    delivery_id: str
    plan_id: str
    started_at: float
    completed_at: float
    chunks_delivered: tuple[str, ...]
    chunks_cancelled: tuple[str, ...]
    audio_service_status: str
    body_sync_error_ms: int
    heard_confidence: float
    failure_refs: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.completed_at < self.started_at:
            raise ValueError("completed_at cannot precede started_at")
        if not 0.0 <= self.heard_confidence <= 1.0:
            raise ValueError("heard_confidence must be in [0, 1]")
