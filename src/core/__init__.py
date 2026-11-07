# -*- coding: utf-8 -*-
"""Core contracts.

`CognitiveFrame` nay trỏ tới Cortex V2 canonical frame. Builder sống ở runtime vì
nó cần hiểu lịch sử phiên; core chỉ giữ dữ liệu thuần.
"""

from .cognition import CognitiveFrame
from .evidence import (
    DebugTrace,
    EvidenceItem,
    ModuleReport,
    RawSignal,
    RawSignalValidationError,
    StateCommitRequest,
    make_raw_signal,
)

__all__ = [
    "CognitiveFrame",
    "DebugTrace",
    "EvidenceItem",
    "ModuleReport",
    "RawSignal",
    "RawSignalValidationError",
    "StateCommitRequest",
    "make_raw_signal",
]
