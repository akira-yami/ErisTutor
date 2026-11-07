# -*- coding: utf-8 -*-
"""Typed Subject Core performance frame for Godot/Live2D transport."""
from __future__ import annotations

import inspect
from dataclasses import dataclass
from typing import Any, Callable, Mapping

from .contracts import PerformanceIntent, SpeechChunk


@dataclass(frozen=True, slots=True)
class BodyDeliveryReceipt:
    plan_id: str
    sequence: int
    status: str
    sync_error_ms: int = 0
    evidence_refs: tuple[str, ...] = ()


class SubjectPerformanceBodyAdapter:
    PROTOCOL = "eris.subject.performance"
    SCHEMA_VERSION = 1
    MAX_BODY_CUES = 32
    MAX_TEXT_FIELD = 256

    def __init__(self, transport: Callable[[Mapping[str, Any]], Any]) -> None:
        self._transport = transport

    @staticmethod
    def _score(value: float) -> float:
        return max(0.0, min(1.0, float(value)))

    @classmethod
    def _text(cls, value: Any) -> str:
        return str(value or "")[: cls.MAX_TEXT_FIELD]

    @classmethod
    def build_frame(
        cls,
        *,
        plan_id: str,
        chunk: SpeechChunk,
        performance: PerformanceIntent,
        decision_id: str = "",
        delivery_id: str = "",
    ) -> dict[str, Any]:
        plan_id = cls._text(plan_id)
        if not plan_id:
            raise ValueError("plan_id cannot be empty")
        cues: list[str] = []
        for raw in (*performance.body_cues, *chunk.body_cue_refs):
            cue = cls._text(raw).strip()
            if cue and cue not in cues:
                cues.append(cue)
            if len(cues) >= cls.MAX_BODY_CUES:
                break
        return {
            "protocol": cls.PROTOCOL,
            "schema_version": cls.SCHEMA_VERSION,
            "plan_id": plan_id,
            "decision_id": cls._text(decision_id),
            "delivery_id": cls._text(delivery_id),
            "sequence": max(0, int(chunk.sequence)),
            "energy": cls._score(performance.energy),
            "pace": cls._score(performance.pace),
            "vulnerability": cls._score(performance.vulnerability),
            "restraint": cls._score(performance.restraint),
            "gaze_target": cls._text(performance.gaze_target) or "user",
            "body_cues": cues,
        }

    @classmethod
    def validate_frame(cls, frame: Mapping[str, Any]) -> bool:
        try:
            if frame.get("protocol") != cls.PROTOCOL or int(frame.get("schema_version", -1)) != cls.SCHEMA_VERSION:
                return False
            if not cls._text(frame.get("plan_id")):
                return False
            if int(frame.get("sequence", -1)) < 0:
                return False
            for name in ("energy", "pace", "vulnerability", "restraint"):
                value = float(frame[name])
                if not 0.0 <= value <= 1.0:
                    return False
            cues = frame.get("body_cues", ())
            if not isinstance(cues, (list, tuple)) or len(cues) > cls.MAX_BODY_CUES:
                return False
            return all(isinstance(item, str) and 0 < len(item) <= cls.MAX_TEXT_FIELD for item in cues)
        except (KeyError, TypeError, ValueError):
            return False

    async def send_performance(
        self,
        *,
        plan_id: str,
        chunk: SpeechChunk,
        performance: PerformanceIntent,
        decision_id: str = "",
        delivery_id: str = "",
    ) -> BodyDeliveryReceipt:
        frame = self.build_frame(
            plan_id=plan_id,
            chunk=chunk,
            performance=performance,
            decision_id=decision_id,
            delivery_id=delivery_id,
        )
        result = self._transport(frame)
        if inspect.isawaitable(result):
            result = await result
        result = dict(result or {})
        return BodyDeliveryReceipt(
            plan_id=plan_id,
            sequence=chunk.sequence,
            status=str(result.get("status") or "sent"),
            sync_error_ms=int(result.get("sync_error_ms") or 0),
            evidence_refs=tuple(str(item) for item in result.get("evidence_refs", ())),
        )
