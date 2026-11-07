"""Repair Memory: ghi lỗi, sửa sai, suppression rule tương lai.

Nếu user nói "không phải", hệ thống không được chỉ xin lỗi rồi quên. Nó phải lưu loại lỗi,
assumption sai, evidence, chiến lược sửa. Đây là nơi ERIS học bớt ngu theo thời gian, một kỳ vọng khiêm tốn.
"""

from __future__ import annotations

import json
import os
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Literal

FailureType = Literal[
    "misread",
    "overexplained",
    "underacted",
    "tone_mismatch",
    "wrong_abstraction",
    "overread",
    "privacy_intrusion",
    "body_mismatch",
    "culture_misread",
    "assistant_flattening",
    "overhumanization",
    "unknown",
]


def iso_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime())


def compact_text(value: Any, *, limit: int = 420) -> str:
    text = " ".join(str(value or "").split())
    if len(text) > limit:
        return text[: limit - 3].rstrip() + "..."
    return text


def clamp01(value: float) -> float:
    return max(0.0, min(1.0, float(value)))



from .json_store_io import atomic_write_json


@dataclass(slots=True)
class RepairMemoryItem:
    id: str
    failure_type: FailureType
    trigger_context: str
    wrong_assumption: str
    user_correction: str = ""
    repair_strategy: str = ""
    future_suppression_rule: str = ""
    confidence: float = 0.70
    evidence_ids: list[str] = field(default_factory=list)
    created_at: str = ""
    updated_at: str = ""
    recurrence_count: int = 1
    decay_policy: str = "slow"
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "failure_type": self.failure_type,
            "trigger_context": self.trigger_context,
            "wrong_assumption": self.wrong_assumption,
            "user_correction": self.user_correction,
            "repair_strategy": self.repair_strategy,
            "future_suppression_rule": self.future_suppression_rule,
            "confidence": round(float(self.confidence), 4),
            "evidence_ids": list(self.evidence_ids),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "recurrence_count": int(self.recurrence_count),
            "decay_policy": self.decay_policy,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "RepairMemoryItem":
        return cls(
            id=str(data.get("id") or f"rep_{uuid.uuid4().hex[:12]}"),
            failure_type=str(data.get("failure_type") or "unknown"),  # type: ignore[arg-type]
            trigger_context=compact_text(data.get("trigger_context") or ""),
            wrong_assumption=compact_text(data.get("wrong_assumption") or ""),
            user_correction=compact_text(data.get("user_correction") or ""),
            repair_strategy=compact_text(data.get("repair_strategy") or ""),
            future_suppression_rule=compact_text(data.get("future_suppression_rule") or ""),
            confidence=clamp01(float(data.get("confidence", 0.70) or 0.70)),
            evidence_ids=[str(item) for item in data.get("evidence_ids") or [] if str(item).strip()],
            created_at=str(data.get("created_at") or ""),
            updated_at=str(data.get("updated_at") or ""),
            recurrence_count=int(data.get("recurrence_count", 1) or 1),
            decay_policy=str(data.get("decay_policy") or "slow"),
            metadata=dict(data.get("metadata") or {}),
        )

    def recall_line(self) -> str:
        return compact_text(
            f"repair/{self.failure_type}: avoid {self.wrong_assumption}; strategy={self.repair_strategy}; rule={self.future_suppression_rule}",
            limit=320,
        )


class RepairMemoryStore:
    def __init__(self, path: str, *, max_items: int = 160):
        self.path = path
        self.max_items = max(20, int(max_items))

    def load(self) -> list[RepairMemoryItem]:
        if not os.path.isfile(self.path) or os.path.getsize(self.path) <= 0:
            return []
        with open(self.path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        raw_items = data.get("items") if isinstance(data, dict) else data
        if not isinstance(raw_items, list):
            return []
        return [RepairMemoryItem.from_dict(item) for item in raw_items if isinstance(item, dict)]

    def save(self, items: list[RepairMemoryItem]) -> None:
        items.sort(key=lambda item: (item.confidence, item.recurrence_count, item.updated_at), reverse=True)
        atomic_write_json(self.path, {"version": 1, "items": [item.to_dict() for item in items[: self.max_items]]})

    def apply_commit(self, request: Any) -> RepairMemoryItem | None:
        content = compact_text(getattr(request, "content", "") or "", limit=640)
        if not content:
            return None
        reason = compact_text(getattr(request, "reason", "") or "", limit=400)
        now = iso_now()
        failure_type = self._infer_failure_type(content, reason)
        wrong_assumption = self._extract_field(content, "wrong_assumption") or self._guess_wrong_assumption(content, reason)
        user_correction = self._extract_field(content, "user_correction") or ""
        repair_strategy = self._extract_field(content, "repair_strategy") or self._default_strategy(failure_type)
        future_rule = self._extract_field(content, "future_suppression_rule") or self._default_suppression_rule(failure_type)
        trigger_context = self._extract_field(content, "trigger_context") or reason or content
        evidence_ids = [str(item) for item in getattr(request, "evidence_ids", []) or [] if str(item).strip()]
        confidence = clamp01(float(getattr(request, "confidence", 0.70) or 0.70))
        key = f"{failure_type}:{wrong_assumption}:{future_rule}".lower()
        items = self.load()
        for item in items:
            if f"{item.failure_type}:{item.wrong_assumption}:{item.future_suppression_rule}".lower() == key:
                item.recurrence_count += 1
                item.confidence = clamp01(max(item.confidence, confidence) + 0.025)
                item.updated_at = now
                item.evidence_ids = list(dict.fromkeys([*item.evidence_ids, *evidence_ids]))[-10:]
                self.save(items)
                return item
        item = RepairMemoryItem(
            id=f"rep_{uuid.uuid4().hex[:12]}",
            failure_type=failure_type,
            trigger_context=trigger_context,
            wrong_assumption=wrong_assumption,
            user_correction=user_correction,
            repair_strategy=repair_strategy,
            future_suppression_rule=future_rule,
            confidence=confidence,
            evidence_ids=evidence_ids,
            created_at=now,
            updated_at=now,
            decay_policy=str(getattr(request, "decay_policy", "slow") or "slow"),
            metadata={"raw_content": content, "reason": reason},
        )
        items.append(item)
        self.save(items)
        return item

    def recall(self, query: str = "", *, limit: int = 6) -> list[RepairMemoryItem]:
        items = self.load()
        if not query:
            return items[:limit]
        q = query.lower()
        scored = []
        for item in items:
            hay = f"{item.failure_type} {item.trigger_context} {item.wrong_assumption} {item.future_suppression_rule}".lower()
            score = sum(1 for token in q.split() if token and token in hay)
            if score:
                scored.append((score + item.confidence + item.recurrence_count * 0.05, item))
        scored.sort(key=lambda pair: pair[0], reverse=True)
        return [item for _, item in scored[:limit]]

    def _extract_field(self, text: str, field_name: str) -> str:
        # Hỗ trợ content dạng nhiều dòng: "wrong_assumption: ...".
        lowered = field_name.lower()
        for line in str(text or "").splitlines():
            if ":" not in line:
                continue
            key, value = line.split(":", 1)
            if key.strip().lower() == lowered:
                return compact_text(value, limit=360)
        return ""

    def _infer_failure_type(self, content: str, reason: str) -> FailureType:
        text = f"{content} {reason}".lower()
        if "overread" in text or "suy diễn" in text:
            return "overread"
        if "misread" in text or "hiểu sai" in text or "không phải" in text:
            return "misread"
        if "overexplain" in text or "dài" in text or "lải" in text:
            return "overexplained"
        if "underacted" in text or "thiếu" in text or "chưa làm" in text:
            return "underacted"
        if "tone" in text or "giọng" in text:
            return "tone_mismatch"
        if "privacy" in text or "riêng tư" in text:
            return "privacy_intrusion"
        if "body" in text or "motion" in text:
            return "body_mismatch"
        return "unknown"

    def _guess_wrong_assumption(self, content: str, reason: str) -> str:
        return compact_text(reason or content, limit=260) or "unknown_wrong_assumption"

    def _default_strategy(self, failure_type: FailureType) -> str:
        mapping = {
            "overread": "Giảm suy diễn tâm lý; surface expression chỉ được grounded/practical nếu confidence thấp.",
            "misread": "Acknowledge correction ngắn, bỏ frame sai, trả lời intent hiện tại.",
            "overexplained": "Nén câu trả lời trước; chỉ mở rộng khi user yêu cầu.",
            "underacted": "Làm đúng hành động/file/code trước khi giải thích dài.",
            "tone_mismatch": "Điều chỉnh tone theo context kỹ thuật/repair, không diễn persona quá tay.",
            "privacy_intrusion": "Không hỏi/nhớ/nhắc dữ kiện riêng nếu permission chưa rõ.",
            "body_mismatch": "Đưa body action qua modality sync trước khi execute.",
        }
        return mapping.get(failure_type, "Ghi lỗi, giảm tự tin ở pattern tương tự, ưu tiên evidence mới.")

    def _default_suppression_rule(self, failure_type: FailureType) -> str:
        mapping = {
            "overread": "Không nói trực tiếp emotional interpretation khi overread_risk cao hoặc evidence mỏng.",
            "misread": "Không build tiếp trên assistant turn bị user phủ định.",
            "overexplained": "Nếu request là code/file cụ thể, trả artifact trước, diễn giải sau.",
            "underacted": "Nếu user nói thiếu/chưa làm, ưu tiên patch output ngay.",
            "tone_mismatch": "Khi user đang căng kỹ thuật, giảm poetic/symbolic pressure.",
            "privacy_intrusion": "Storage cần permission + evidence + target hợp lệ.",
        }
        return mapping.get(failure_type, "Pattern tương tự phải đi qua repair check trước khi surface.")
