# -*- coding: utf-8 -*-
"""Public-writer port owned by :mod:`runtime.conversation.kernel`."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Protocol, Sequence, TYPE_CHECKING

from .contracts import ConversationEvent
from .delivery import SurfaceEnvelope

if TYPE_CHECKING:
    from .kernel import KernelTurn


@dataclass(frozen=True, slots=True)
class RenderedConversationMove:
    surface: SurfaceEnvelope
    decision_payload: Mapping[str, Any] = field(default_factory=dict)
    metadata: Mapping[str, Any] = field(default_factory=dict)


class ConversationWriter(Protocol):
    def realize(
        self,
        *,
        turn: "KernelTurn",
        event: ConversationEvent,
        recent_history: Sequence[Mapping[str, Any]],
        runtime_context: Mapping[str, Any],
    ) -> RenderedConversationMove:
        ...


__all__ = ["ConversationWriter", "RenderedConversationMove"]
