"""Procedural Memory: cách xử lý đã học.

Đây là nơi lưu rule kiểu: khi user sửa hiểu lầm, trả lời trực tiếp trước; khi evidence yếu,
không claim nhìn thấy màn hình. Procedural là policy học được, không phải fact về user.
"""

from __future__ import annotations

import json
import os
import re
import time
import uuid
from dataclasses import dataclass, field
from typing import Any


def iso_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime())


def compact_text(value: Any, *, limit: int = 360) -> str:
    text = " ".join(str(value or "").split())
    if len(text) > limit:
        return text[: limit - 3].rstrip() + "..."
    return text


def clamp01(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def key_for(trigger: str, rule: str) -> str:
    text = f"{trigger}:{rule}".lower()
    text = re.sub(r"[^0-9a-zA-Z_\u00C0-\u1EF9]+", " ", text)
    return " ".join(text.split())[:240]



from .json_store_io import atomic_write_json


@dataclass(slots=True)
class ProceduralMemoryItem:
    id: str
    trigger: str
    rule: str
    confidence: float = 0.70
    evidence_ids: list[str] = field(default_factory=list)
    observations: int = 1
    success_count: int = 0
    failure_count: int = 0
    first_seen_at: str = ""
    updated_at: str = ""
    decay_policy: str = "slow"
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "trigger": self.trigger,
            "rule": self.rule,
            "confidence": round(float(self.confidence), 4),
            "evidence_ids": list(self.evidence_ids),
            "observations": int(self.observations),
            "success_count": int(self.success_count),
            "failure_count": int(self.failure_count),
            "first_seen_at": self.first_seen_at,
            "updated_at": self.updated_at,
            "decay_policy": self.decay_policy,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ProceduralMemoryItem":
        return cls(
            id=str(data.get("id") or f"proc_{uuid.uuid4().hex[:12]}"),
            trigger=compact_text(data.get("trigger") or "", limit=160),
            rule=compact_text(data.get("rule") or data.get("text") or "", limit=420),
            confidence=clamp01(float(data.get("confidence", 0.70) or 0.70)),
            evidence_ids=[str(item) for item in data.get("evidence_ids") or [] if str(item).strip()],
            observations=int(data.get("observations", 1) or 1),
            success_count=int(data.get("success_count", 0) or 0),
            failure_count=int(data.get("failure_count", 0) or 0),
            first_seen_at=str(data.get("first_seen_at") or ""),
            updated_at=str(data.get("updated_at") or ""),
            decay_policy=str(data.get("decay_policy") or "slow"),
            metadata=dict(data.get("metadata") or {}),
        )

    def recall_line(self) -> str:
        prefix = f"when {self.trigger}: " if self.trigger else "procedure: "
        return compact_text(prefix + self.rule, limit=300)


class ProceduralMemoryStore:
    def __init__(self, path: str, *, max_items: int = 160):
        self.path = path
        self.max_items = max(20, int(max_items))

    def load(self) -> list[ProceduralMemoryItem]:
        if not os.path.isfile(self.path) or os.path.getsize(self.path) <= 0:
            return self._default_items()
        with open(self.path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        raw_items = data.get("items") if isinstance(data, dict) else data
        if not isinstance(raw_items, list):
            return self._default_items()
        items = [ProceduralMemoryItem.from_dict(item) for item in raw_items if isinstance(item, dict)]
        return self._merge_defaults(items)

    def save(self, items: list[ProceduralMemoryItem]) -> None:
        items.sort(key=lambda item: (item.confidence, item.success_count - item.failure_count, item.observations), reverse=True)
        atomic_write_json(self.path, {"version": 1, "items": [item.to_dict() for item in items[: self.max_items]]})

    def apply_commit(self, request: Any) -> ProceduralMemoryItem | None:
        rule = compact_text(getattr(request, "content", "") or "", limit=420)
        if not rule:
            return None
        trigger = self._infer_trigger(rule, getattr(request, "reason", "") or "")
        now = iso_now()
        evidence_ids = [str(item) for item in getattr(request, "evidence_ids", []) or [] if str(item).strip()]
        confidence = clamp01(float(getattr(request, "confidence", 0.70) or 0.70))
        items = self.load()
        key = key_for(trigger, rule)
        for item in items:
            if key_for(item.trigger, item.rule) == key:
                item.observations += 1
                item.confidence = clamp01(max(item.confidence, confidence) + 0.02)
                item.updated_at = now
                item.evidence_ids = list(dict.fromkeys([*item.evidence_ids, *evidence_ids]))[-10:]
                self.save(items)
                return item
        item = ProceduralMemoryItem(
            id=f"proc_{uuid.uuid4().hex[:12]}",
            trigger=trigger,
            rule=rule,
            confidence=confidence,
            evidence_ids=evidence_ids,
            observations=1,
            first_seen_at=now,
            updated_at=now,
            decay_policy=str(getattr(request, "decay_policy", "slow") or "slow"),
            metadata={"reason": compact_text(getattr(request, "reason", "") or "", limit=260)},
        )
        items.append(item)
        self.save(items)
        return item

    def recall(self, query: str = "", *, limit: int = 6) -> list[ProceduralMemoryItem]:
        items = self.load()
        if not query:
            return items[:limit]
        q = query.lower()
        scored = []
        for item in items:
            hay = f"{item.trigger} {item.rule}".lower()
            score = sum(1 for token in q.split() if token and token in hay)
            if score:
                scored.append((score + item.confidence, item))
        scored.sort(key=lambda pair: pair[0], reverse=True)
        return [item for _, item in scored[:limit]]

    def _infer_trigger(self, rule: str, reason: str) -> str:
        text = f"{rule} {reason}".lower()
        if any(token in text for token in ("evidence", "bằng chứng", "screen", "màn hình", "overclaim")):
            return "weak_evidence"
        if any(token in text for token in ("correction", "sửa", "sai", "không phải")):
            return "user_correction"
        if any(token in text for token in ("technical", "code", "architecture", "debug")):
            return "technical_task"
        return "matching_context"

    def _default_items(self) -> list[ProceduralMemoryItem]:
        now = iso_now()
        defaults = [
            ("weak_evidence", "Khi evidence yếu, giảm chắc chắn hoặc im; không claim thấy/biết điều chưa có bằng chứng."),
            ("user_correction", "Khi user sửa sai, bỏ frame suy diễn cũ trước, trả lời trực tiếp phần đúng hiện tại."),
            ("technical_task", "Khi task kỹ thuật, ưu tiên contract, code path, edge case và cách nối; không trôi sang thơ hóa."),
        ]
        return [
            ProceduralMemoryItem(
                id=f"proc_builtin_{index}", trigger=trigger, rule=rule, confidence=0.86,
                first_seen_at=now, updated_at=now, decay_policy="none", metadata={"built_in": True}
            )
            for index, (trigger, rule) in enumerate(defaults, start=1)
        ]

    def _merge_defaults(self, items: list[ProceduralMemoryItem]) -> list[ProceduralMemoryItem]:
        existing = {key_for(item.trigger, item.rule) for item in items}
        for item in self._default_items():
            if key_for(item.trigger, item.rule) not in existing:
                items.append(item)
        return items
