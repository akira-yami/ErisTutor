# -*- coding: utf-8 -*-
"""Contract thuần dữ liệu cho Cortex V2.

Package này không import runtime, model client, storage, TTS hay Godot. Specialist
chỉ được trả projection có evidence; quyền nói thuộc public writer và quyền ghi
state thuộc reducer.
"""

from .contracts import (
    CognitiveEvidence,
    CognitiveFrame,
    DiscourseProjection,
    EpistemicProjection,
    Hypothesis,
    PreResponseCognitiveBrief,
    ProjectionFailure,
    SemanticProjection,
    SocialProjection,
    StyleProjection,
)

__all__ = [
    "CognitiveEvidence",
    "CognitiveFrame",
    "DiscourseProjection",
    "EpistemicProjection",
    "Hypothesis",
    "PreResponseCognitiveBrief",
    "ProjectionFailure",
    "SemanticProjection",
    "SocialProjection",
    "StyleProjection",
]
