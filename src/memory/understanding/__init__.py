"""Tầng đọc nghĩa trung gian cho memory intake."""

from .meaning_frame import EntityRef, MeaningFrame, MeaningParseResult
from .semantic_parser import FallbackSurfaceSemanticParser, ModelSemanticParser, StructuredFrameParser
from .entity_linker import link_entity

__all__ = [
    "EntityRef",
    "MeaningFrame",
    "MeaningParseResult",
    "FallbackSurfaceSemanticParser",
    "ModelSemanticParser",
    "StructuredFrameParser",
    "link_entity",
]
