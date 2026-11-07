# -*- coding: utf-8 -*-
"""
src/cortex/daydream_consolidation.py
====================================

Tầng 2b - Daydream Consolidation.

Đây là nơi Qwen 4B GGUF được phép nghiền ngẫm memory packet đã lọc sẵn.
Nó không chạy theo timer mù. CortexRuntime gọi khi attention và budget cho phép,
rồi ModelBudgetPolicy vẫn có quyền veto nếu máy đang bận.

Luật chống ảo giác:
- Input packet nào cũng có `id` và `evidence_ids`.
- Insight trả về bắt buộc có `evidence_ids`.
- evidence_ids phải khớp với input ban đầu.
- Thiếu evidence hoặc evidence lạ -> reject insight ngay.

Không nguồn thì không vào agenda. Đơn giản, lạnh lùng, và đáng tiếc là cần thiết.
"""

from __future__ import annotations
from core.di import resolve_dependency

import json
import math
import os
import re
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Iterable, Literal, Mapping, Optional

try:
    from cortex.internal_agenda import InternalAgendaStack
except Exception:  # pragma: no cover
    from .internal_agenda import InternalAgendaStack  # type: ignore

try:
    from cortex.endogenous_drive import RollingInternalState
    from cortex.user_context_stream import RollingUserContext
except Exception:  # pragma: no cover
    from .endogenous_drive import RollingInternalState  # type: ignore
    from .user_context_stream import RollingUserContext  # type: ignore

PacketKind = Literal["semantic", "unresolved", "repair", "episodic", "relational", "affective", "procedural"]
InsightKind = Literal["memory_pattern", "repair_need", "open_question", "relationship_pattern", "technical_pattern", "self_continuity"]


def _clamp01(value: Any, default: float = 0.0) -> float:
    try:
        n = float(value)
    except Exception:
        n = default
    if math.isnan(n) or math.isinf(n):
        n = default
    return max(0.0, min(1.0, n))


def _compact(text: Any, limit: int = 800) -> str:
    value = " ".join(str(text or "").replace("\x00", " ").split()).strip()
    if len(value) <= limit:
        return value
    return value[: max(0, limit - 1)].rstrip() + "…"


@dataclass(slots=True)
class DaydreamPacket:
    """Một mảnh memory đã lọc để 4B đọc."""

    id: str
    kind: PacketKind
    text: str
    evidence_ids: tuple[str, ...]
    confidence: float = 0.5
    importance: float = 0.5
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.id = str(self.id or "").strip()
        self.kind = str(self.kind or "semantic")  # type: ignore[assignment]
        self.text = _compact(self.text, 1000)
        self.evidence_ids = tuple(str(x) for x in self.evidence_ids if str(x).strip())[:12]
        self.confidence = _clamp01(self.confidence, 0.5)
        self.importance = _clamp01(self.importance, 0.5)

    def to_prompt_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "text": self.text,
            "evidence_ids": list(self.evidence_ids),
            "confidence": round(self.confidence, 3),
            "importance": round(self.importance, 3),
        }


@dataclass(slots=True)
class DaydreamInsight:
    kind: InsightKind
    summary: str
    evidence_ids: tuple[str, ...]
    confidence: float = 0.0
    pressure: float = 0.0
    speech_value: float = 0.0
    proposed_agenda_kind: str = "open_question"
    rejected: bool = False
    reject_reason: str = ""
    raw: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.summary = _compact(self.summary, 600)
        self.evidence_ids = tuple(str(x) for x in self.evidence_ids if str(x).strip())[:16]
        self.confidence = _clamp01(self.confidence)
        self.pressure = _clamp01(self.pressure)
        self.speech_value = _clamp01(self.speech_value)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class DaydreamResult:
    ok: bool
    insights: list[DaydreamInsight] = field(default_factory=list)
    rejected: list[DaydreamInsight] = field(default_factory=list)
    model_used: str = ""
    reason: str = ""
    raw: dict[str, Any] = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "insights": [x.to_dict() for x in self.insights],
            "rejected": [x.to_dict() for x in self.rejected],
            "model_used": self.model_used,
            "reason": self.reason,
            "raw": self.raw,
            "created_at": self.created_at,
        }


@dataclass(slots=True)
class DaydreamConsolidationConfig:
    enabled: bool = field(default_factory=lambda: os.getenv("ERIS_DAYDREAM_CONSOLIDATION", "1").strip().lower() not in {"0", "false", "no", "off"})
    model: str = field(default_factory=lambda: os.getenv("ERIS_DAYDREAM_MODEL", "remote_daydream").strip())
    min_interval_seconds: float = field(default_factory=lambda: float(os.getenv("ERIS_DAYDREAM_MIN_INTERVAL", "300")))
    max_packets: int = field(default_factory=lambda: int(os.getenv("ERIS_DAYDREAM_MAX_PACKETS", "20")))
    max_prompt_chars: int = field(default_factory=lambda: int(os.getenv("ERIS_DAYDREAM_MAX_PROMPT_CHARS", "12000")))
    keep_alive: str | int | None = field(default_factory=lambda: os.getenv("ERIS_DAYDREAM_KEEP_ALIVE", "0"))
    temperature: float = 0.0
    min_confidence: float = 0.45


class DaydreamConsolidator:
    """Sở hữu path gọi Qwen 4B để chưng cất memory."""

    def __init__(self, *, llm_client: Any = None, config: DaydreamConsolidationConfig | None = None, logger: Any = None) -> None:
        self.client = llm_client
        self.config = resolve_dependency(config, lambda: DaydreamConsolidationConfig())
        self.logger = logger
        self.last_run_at = 0.0
        self.last_result: DaydreamResult | None = None

    def should_trigger(
        self,
        *,
        user_context: RollingUserContext | None = None,
        internal_state: RollingInternalState | None = None,
        now: float | None = None,
    ) -> tuple[bool, str]:
        """Cổng áp lực cho 4B.

        Không có `timer says run`. Chỉ có idle/pressure thật.
        """

        if not self.config.enabled:
            return False, "daydream_disabled"
        current = float(now or time.time())
        if current - self.last_run_at < self.config.min_interval_seconds:
            return False, "daydream_cooldown"
        idle = float(getattr(user_context, "idle_seconds", 0.0) or 0.0) if user_context else 0.0
        consolidation = float(getattr(internal_state, "consolidation_pressure", 0.0) or 0.0) if internal_state else 0.0
        insight = float(getattr(internal_state, "insight_pressure", 0.0) or 0.0) if internal_state else 0.0
        if idle > 300:
            return True, "idle_gt_300s"
        if consolidation > 0.65:
            return True, "consolidation_pressure_gt_065"
        if insight > 0.70:
            return True, "insight_pressure_gt_070"
        return False, "pressure_below_threshold"

    def consolidate(
        self,
        packets: Iterable[DaydreamPacket],
        *,
        agenda_stack: InternalAgendaStack | None = None,
        context: Mapping[str, Any] | None = None,
    ) -> DaydreamResult:
        """Chạy consolidation và validate evidence.

        Nếu không có client, trả skip rõ ràng. Không fallback bịa insight, vì thế giới đã
        có đủ người tự tin nói khi không biết.
        """

        if not self.config.enabled:
            return DaydreamResult(ok=False, reason="daydream_disabled", model_used=self.config.model)
        selected = self._select_packets(packets)
        if not selected:
            return DaydreamResult(ok=False, reason="no_packets", model_used=self.config.model)
        if self.client is None:
            return DaydreamResult(ok=False, reason="no_4b_client", model_used=self.config.model)

        prompt = self._build_prompt(selected, context=dict(context or {}))
        try:
            data = self._call_json(prompt)
        except Exception as exc:
            self._log("warning", "Daydream consolidation failed: %s", exc)
            result = DaydreamResult(ok=False, reason=f"model_error:{exc}", model_used=self.config.model)
            self.last_result = result
            return result

        result = self._validate_result(data, selected)
        result.model_used = self.config.model
        self.last_run_at = time.time()
        self.last_result = result
        if agenda_stack is not None:
            self._push_agenda(result, agenda_stack)
        return result

    def _select_packets(self, packets: Iterable[DaydreamPacket]) -> list[DaydreamPacket]:
        valid = [p for p in packets if p.id and p.text and p.evidence_ids]
        valid.sort(key=lambda p: (p.importance, p.confidence), reverse=True)
        return valid[: max(1, int(self.config.max_packets))]

    def _build_prompt(self, packets: list[DaydreamPacket], *, context: Mapping[str, Any]) -> str:
        payload = {
            "task": "ERIS daydream consolidation",
            "rules": [
                "Return JSON object only.",
                "Every insight must cite evidence_ids from the packets.",
                "Do not invent facts not present in packets.",
                "Prefer open questions or repair hints over confident conclusions when evidence is weak.",
                "If nothing useful, return insights: [].",
            ],
            "schema": {
                "insights": [
                    {
                        "kind": "memory_pattern|repair_need|open_question|relationship_pattern|technical_pattern|self_continuity",
                        "summary": "short grounded Vietnamese or English summary",
                        "evidence_ids": ["must match input evidence_ids"],
                        "confidence": "0..1",
                        "pressure": "0..1",
                        "speech_value": "0..1",
                        "proposed_agenda_kind": "memory_consolidation|repair|open_question|technical_followup|relationship_tension|self_continuity|misc",
                    }
                ]
            },
            "context": dict(context or {}),
            "packets": [p.to_prompt_dict() for p in packets],
        }
        text = json.dumps(payload, ensure_ascii=False)
        if len(text) <= self.config.max_prompt_chars:
            return text
        # Cắt text packet, giữ ids/evidence. Cắt evidence là tự bắn vào chân validation.
        for p in payload["packets"]:  # type: ignore[index]
            p["text"] = _compact(p.get("text", ""), 420)
        return json.dumps(payload, ensure_ascii=False)[: self.config.max_prompt_chars]

    def _call_json(self, prompt: str) -> dict[str, Any]:
        messages = [
            {"role": "system", "content": "You are ERIS daydream consolidator. Return JSON only."},
            {"role": "user", "content": prompt},
        ]
        if callable(getattr(self.client, "chat_json", None)):
            result = self.client.chat_json(self.config.model, messages, keep_alive=self.config.keep_alive, temperature=self.config.temperature)
            parsed = getattr(result, "parsed", None)
            if isinstance(parsed, Mapping):
                return dict(parsed)
            raise RuntimeError(str(getattr(result, "error", "invalid_json")))
        if callable(getattr(self.client, "generate_json", None)):
            result = self.client.generate_json(self.config.model, prompt, keep_alive=self.config.keep_alive, temperature=self.config.temperature)
            parsed = getattr(result, "parsed", None)
            if isinstance(parsed, Mapping):
                return dict(parsed)
            raise RuntimeError(str(getattr(result, "error", "invalid_json")))
        if callable(getattr(self.client, "create_chat_completion", None)):
            from runtime.inference_broker import inference_context
            with inference_context(
                "memory_digest",
                optional=True,
                timeout_seconds=20.0,
                cancel_scope="background:daydream_consolidation",
                coalesce_key="daydream:latest",
                metadata={"operation": "daydream_consolidation"},
            ):
                response = self.client.create_chat_completion(messages=messages, temperature=self.config.temperature)
            content = self._extract_content(response)
            return self._parse_json_text(content)
        raise RuntimeError("unsupported_4b_client")

    def _extract_content(self, response: Any) -> str:
        if isinstance(response, str):
            return response
        if isinstance(response, Mapping):
            try:
                return str(response["choices"][0]["message"]["content"])
            except Exception:
                return str(response.get("content") or response)
        return str(response)

    def _parse_json_text(self, text: str) -> dict[str, Any]:
        raw = str(text or "").strip()
        if raw.startswith("```"):
            raw = re.sub(r"^```(?:json)?", "", raw.strip(), flags=re.I).strip()
            raw = re.sub(r"```$", "", raw.strip()).strip()
        try:
            data = json.loads(raw)
            if isinstance(data, Mapping):
                return dict(data)
        except Exception:
            pass
        match = re.search(r"\{.*\}", raw, flags=re.S)
        if match:
            data = json.loads(match.group(0))
            if isinstance(data, Mapping):
                return dict(data)
        raise RuntimeError("invalid_json")

    def _validate_result(self, data: Mapping[str, Any], packets: list[DaydreamPacket]) -> DaydreamResult:
        allowed_evidence = {eid for p in packets for eid in p.evidence_ids}
        raw_items = data.get("insights", []) if isinstance(data, Mapping) else []
        if not isinstance(raw_items, list):
            raw_items = []
        accepted: list[DaydreamInsight] = []
        rejected: list[DaydreamInsight] = []
        for raw in raw_items[:12]:
            if not isinstance(raw, Mapping):
                continue
            insight = DaydreamInsight(
                kind=str(raw.get("kind") or "open_question"),  # type: ignore[arg-type]
                summary=str(raw.get("summary") or ""),
                evidence_ids=tuple(str(x) for x in (raw.get("evidence_ids") or [])),
                confidence=_clamp01(raw.get("confidence", 0.0)),
                pressure=_clamp01(raw.get("pressure", 0.0)),
                speech_value=_clamp01(raw.get("speech_value", 0.0)),
                proposed_agenda_kind=str(raw.get("proposed_agenda_kind") or "open_question"),
                raw=dict(raw),
            )
            if not insight.summary:
                insight.rejected = True
                insight.reject_reason = "missing_summary"
            elif not insight.evidence_ids:
                insight.rejected = True
                insight.reject_reason = "missing_evidence_ids"
            elif any(eid not in allowed_evidence for eid in insight.evidence_ids):
                insight.rejected = True
                insight.reject_reason = "unknown_evidence_id"
            elif insight.confidence < self.config.min_confidence:
                insight.rejected = True
                insight.reject_reason = "low_confidence"
            if insight.rejected:
                rejected.append(insight)
            else:
                accepted.append(insight)
        return DaydreamResult(ok=bool(accepted), insights=accepted, rejected=rejected, raw=dict(data), reason="validated")

    def _push_agenda(self, result: DaydreamResult, agenda_stack: InternalAgendaStack) -> None:
        for insight in result.insights:
            agenda_stack.add(
                kind=str(insight.proposed_agenda_kind or "open_question"),  # type: ignore[arg-type]
                summary=insight.summary,
                evidence_ids=insight.evidence_ids,
                pressure=insight.pressure,
                urgency=max(0.10, insight.pressure * 0.55),
                confidence=insight.confidence,
                speech_value=insight.speech_value,
                source="daydream_consolidation",
                ttl_seconds=7200,
                payload={"insight_kind": insight.kind, "model": self.config.model},
            )

    def _log(self, level: str, msg: str, *args: Any) -> None:
        logger = self.logger
        fn = getattr(logger, level, None) if logger is not None else None
        if callable(fn):
            fn(msg, *args)
