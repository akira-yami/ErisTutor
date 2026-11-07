# -*- coding: utf-8 -*-
"""Boundary dữ liệu delivery V12.

Adapter transport hiện tại vẫn ở public_output_runtime; module này định nghĩa
surface envelope để migration không lẫn display text với speech text.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Protocol, TYPE_CHECKING

if TYPE_CHECKING:
    from .kernel import KernelTurn
    from .writer import RenderedConversationMove


@dataclass(frozen=True, slots=True)
class SurfaceEnvelope:
    display_text: str
    speech_text: str = ""
    language: str = "auto"
    alignment_safe: bool = False
    metadata: Mapping[str, Any] | None = None


class ConversationDelivery(Protocol):
    def deliver(
        self,
        *,
        turn: "KernelTurn",
        rendered: "RenderedConversationMove",
        delivery_id: str,
    ) -> None:
        ...


__all__ = ["ConversationDelivery", "SurfaceEnvelope"]
