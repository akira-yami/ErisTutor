# -*- coding: utf-8 -*-
"""
cortex.tutor — Curiosity Tutor (Gia sư Tò mò).

Đóng vòng học còn thiếu của ERIS: bị bất ngờ → muốn biết → đi hỏi → tiêu hóa →
lần sau hiểu hơn. Toàn bộ chạy ngoài hot path, mặc định TẮT (Master_Switch).

Xem .kiro/specs/curiosity-tutor/ cho requirements + design.
"""

from __future__ import annotations

from .contracts import (
    FailReason,
    GapSource,
    KnowledgeGap,
    TutorAnswer,
    TutorEpisode,
    TutorQuestion,
    TutorStage,
)
from .config import TutorConfig, TutorLogger, TutorRiskGuard
from .gap_collector import GapCollector
from .curiosity_gate import CuriosityGate
from .question_formulator import QuestionFormulator
from .claim_digest import ClaimDigest
from .share_impulse import LearningShareImpulse
from .knowledge_provider import KnowledgeProvider
from .knowledge_classifier import KnowledgeClassifier
from .curiosity_router import CuriosityRouter

__all__ = [
    "FailReason",
    "GapSource",
    "KnowledgeGap",
    "TutorAnswer",
    "TutorEpisode",
    "TutorQuestion",
    "TutorStage",
    "TutorConfig",
    "TutorLogger",
    "TutorRiskGuard",
    "GapCollector",
    "CuriosityGate",
    "QuestionFormulator",
    "ClaimDigest",
    "LearningShareImpulse",
    "KnowledgeProvider",
    "KnowledgeClassifier",
    "CuriosityRouter",
]
