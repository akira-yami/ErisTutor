# -*- coding: utf-8 -*-
"""External TTS broker boundary."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Mapping


@dataclass(frozen=True, slots=True)
class TTSReceipt:
    chunk_id: str
    status: str
    audio_ref: str | None
    duration_ms: int
    evidence_refs: tuple[str, ...] = ()


class TTSBroker:
    def __init__(self, transport: Callable[[Mapping[str, Any]], Awaitable[Mapping[str, Any]]]) -> None:
        self._transport = transport

    async def synthesize(self, *, chunk_id: str, text: str, prosody_hint: str = "") -> TTSReceipt:
        response = dict(await self._transport({"chunk_id": chunk_id, "text": text, "prosody_hint": prosody_hint}))
        return TTSReceipt(
            chunk_id=chunk_id,
            status=str(response.get("status") or "failed"),
            audio_ref=str(response.get("audio_ref")) if response.get("audio_ref") else None,
            duration_ms=max(0, int(response.get("duration_ms") or 0)),
            evidence_refs=tuple(str(item) for item in response.get("evidence_refs", ())),
        )
