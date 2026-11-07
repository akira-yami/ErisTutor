# -*- coding: utf-8 -*-
"""Clause-level TTS and body coordination with cancellation."""
from __future__ import annotations

import asyncio
import time
import uuid
from typing import Any, AsyncIterator, Awaitable, Callable, Protocol

from .contracts import DeliveryReceipt, PerformanceIntent, SpeechChunk


class TTSLike(Protocol):
    async def synthesize(self, *, chunk_id: str, text: str, prosody_hint: str = "") -> Any: ...


class BodyLike(Protocol):
    async def send_performance(self, *, plan_id: str, chunk: SpeechChunk, performance: PerformanceIntent) -> Any: ...


class IncrementalSpeechCoordinator:
    def __init__(
        self,
        *,
        tts_broker: TTSLike,
        body_adapter: BodyLike | None = None,
        clock: Callable[[], float] = time.time,
        id_factory: Callable[[], str] | None = None,
    ) -> None:
        self._tts = tts_broker
        self._body = body_adapter
        self._clock = clock
        self._id_factory = id_factory or (lambda: str(uuid.uuid4()))

    async def speak(
        self,
        *,
        chunks: AsyncIterator[SpeechChunk],
        performance: PerformanceIntent,
        cancellation: asyncio.Event,
    ) -> DeliveryReceipt:
        started = self._clock()
        delivered: list[str] = []
        cancelled: list[str] = []
        failures: list[str] = []
        audio_status = "not_started"
        body_errors: list[int] = []

        async for chunk in chunks:
            if cancellation.is_set():
                cancelled.append(chunk.chunk_id)
                continue
            try:
                if self._body is None:
                    tts_receipt = await self._tts.synthesize(chunk_id=chunk.chunk_id, text=chunk.text, prosody_hint=chunk.prosody_hint)
                    body_receipt = None
                else:
                    async with asyncio.TaskGroup() as group:
                        tts_task = group.create_task(self._tts.synthesize(chunk_id=chunk.chunk_id, text=chunk.text, prosody_hint=chunk.prosody_hint))
                        body_task = group.create_task(self._body.send_performance(plan_id=chunk.plan_id, chunk=chunk, performance=performance))
                    tts_receipt = tts_task.result()
                    body_receipt = body_task.result()
                audio_status = str(getattr(tts_receipt, "status", "ok"))
                body_errors.append(abs(int(getattr(body_receipt, "sync_error_ms", 0))) if body_receipt is not None else 0)
                delivered.append(chunk.chunk_id)
            except Exception as exc:
                failures.append(f"{chunk.chunk_id}:{type(exc).__name__}")
                audio_status = "failed"
                if cancellation.is_set():
                    cancelled.append(chunk.chunk_id)
                else:
                    break

        completed = self._clock()
        heard_confidence = 0.0 if not delivered else max(0.0, min(1.0, len(delivered) / max(1, len(delivered) + len(failures))))
        return DeliveryReceipt(
            delivery_id=self._id_factory(),
            plan_id=performance.plan_id,
            started_at=started,
            completed_at=completed,
            chunks_delivered=tuple(delivered),
            chunks_cancelled=tuple(cancelled),
            audio_service_status=audio_status,
            body_sync_error_ms=max(body_errors, default=0),
            heard_confidence=heard_confidence,
            failure_refs=tuple(failures),
        )
