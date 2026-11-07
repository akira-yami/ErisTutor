from __future__ import annotations

"""
memory_query_repair.py

Repair prior cho MemoryQueryCompiler.

Khi user sửa lỗi recall:
- Lưu failed_ir
- Lưu corrected_ir
- Lần sau gặp query cùng cấu trúc, phạt hypothesis cũ và boost hypothesis đúng.

Đây không phải fallback keyword. Đây là học từ correction dưới dạng prior.
"""

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Iterable, Optional
import json
import re
import time
import unicodedata

from .memory_query_ir import MemoryQueryIR, QueryHypothesis, clamp01


@dataclass(slots=True)
class MemoryQueryRepairItem:
    id: str
    failed_user_text: str
    correction_text: str
    failed_signature: str
    corrected_signature: str
    corrected_ir: MemoryQueryIR
    failure_type: str = "wrong_memory_query_binding"
    confidence: float = 0.85
    created_at: float = field(default_factory=time.time)
    metadata: dict[str, str] = field(default_factory=dict)


@dataclass(slots=True)
class RepairPriorResult:
    hypotheses: tuple[QueryHypothesis, ...]
    applied_items: tuple[str, ...]
    notes: tuple[str, ...]


class MemoryQueryRepairStore:
    """
    Store nhỏ cho repair query.

    Có thể thay bằng repair_memory thật của project sau.
    """

    def __init__(self, items: Optional[Iterable[MemoryQueryRepairItem]] = None) -> None:
        self.items: list[MemoryQueryRepairItem] = list(items or [])

    def record(
        self,
        *,
        failed_user_text: str,
        correction_text: str,
        failed_ir: MemoryQueryIR,
        corrected_ir: MemoryQueryIR,
        confidence: float = 0.85,
    ) -> MemoryQueryRepairItem:
        item = MemoryQueryRepairItem(
            id=f"mqrepair-{int(time.time() * 1000)}",
            failed_user_text=failed_user_text,
            correction_text=correction_text,
            failed_signature=failed_ir.signature(),
            corrected_signature=corrected_ir.signature(),
            corrected_ir=corrected_ir,
            confidence=clamp01(confidence),
        )
        self.items.append(item)
        return item

    def apply_prior(
        self,
        *,
        user_text: str,
        hypotheses: Iterable[QueryHypothesis],
    ) -> RepairPriorResult:
        hypos = list(hypotheses)
        if not hypos or not self.items:
            return RepairPriorResult(tuple(hypos), (), ())

        norm_text = normalize_vi(user_text)
        applied: list[str] = []
        notes: list[str] = []
        adjusted: list[QueryHypothesis] = []

        for hypo in hypos:
            score = hypo.score
            penalties = list(hypo.penalties)
            reasons = list(hypo.reasons)

            for item in self.items:
                similarity = text_similarity(norm_text, normalize_vi(item.failed_user_text))
                if similarity < 0.35:
                    continue

                if hypo.ir.signature() == item.failed_signature:
                    penalty = 0.18 * item.confidence * similarity
                    score = max(0.0, score - penalty)
                    penalties.append(f"repair_negative_prior:{item.id}:{penalty:.2f}")
                    applied.append(item.id)

                if hypo.ir.signature() == item.corrected_signature:
                    boost = 0.16 * item.confidence * similarity
                    score = min(1.0, score + boost)
                    reasons.append(f"repair_positive_prior:{item.id}:{boost:.2f}")
                    applied.append(item.id)

            adjusted.append(
                QueryHypothesis(
                    ir=hypo.ir,
                    score=clamp01(score),
                    source=hypo.source,
                    reasons=tuple(reasons),
                    penalties=tuple(penalties),
                )
            )

        adjusted.sort(key=lambda h: h.score, reverse=True)
        if applied:
            notes.append("repair_prior_applied")
        return RepairPriorResult(
            hypotheses=tuple(adjusted),
            applied_items=tuple(dict.fromkeys(applied)),
            notes=tuple(notes),
        )

    def save_json(self, path: str | Path) -> None:
        data = []
        for item in self.items:
            row = asdict(item)
            row["corrected_ir"] = _ir_to_dict(item.corrected_ir)
            data.append(row)
        Path(path).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    @classmethod
    def load_json(cls, path: str | Path) -> "MemoryQueryRepairStore":
        p = Path(path)
        if not p.exists():
            return cls()
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            items = []
            for row in data:
                corrected_ir = _ir_from_dict(row.get("corrected_ir") or {})
                if corrected_ir is None:
                    continue
                items.append(
                    MemoryQueryRepairItem(
                        id=str(row["id"]),
                        failed_user_text=str(row["failed_user_text"]),
                        correction_text=str(row["correction_text"]),
                        failed_signature=str(row["failed_signature"]),
                        corrected_signature=str(row["corrected_signature"]),
                        corrected_ir=corrected_ir,
                        failure_type=str(row.get("failure_type", "wrong_memory_query_binding")),
                        confidence=float(row.get("confidence", 0.85)),
                        created_at=float(row.get("created_at", time.time())),
                        metadata=dict(row.get("metadata") or {}),
                    )
                )
            return cls(items)
        except Exception:
            return cls()


# ---------------------------------------------------------------------------
# Serialization helpers, deliberately local to avoid forcing a global schema.
# ---------------------------------------------------------------------------


def _ir_to_dict(ir: MemoryQueryIR) -> dict:
    return {
        "intent": ir.intent.value,
        "target": {
            "role": ir.target.role.value,
            "act": ir.target.act.value,
            "ordinal": ir.target.ordinal,
            "quantity": ir.target.quantity,
            "recent": ir.target.recent,
            "fact_key": ir.target.fact_key,
            "semantic_hint": ir.target.semantic_hint,
            "tags": list(ir.target.tags),
        },
        "relation": {
            "relation": ir.relation.relation.value,
            "anchor": _selector_to_dict(ir.relation.anchor) if ir.relation.anchor else None,
        },
        "surface": ir.surface.value,
        "confidence": ir.confidence,
        "ambiguity": ir.ambiguity,
        "evidence": list(ir.evidence),
        "warnings": list(ir.warnings),
        "raw_text": ir.raw_text,
    }


def _selector_to_dict(sel):
    return {
        "role": sel.role.value,
        "act": sel.act.value,
        "ordinal": sel.ordinal,
        "quantity": sel.quantity,
        "recent": sel.recent,
        "fact_key": sel.fact_key,
        "semantic_hint": sel.semantic_hint,
        "tags": list(sel.tags),
    }


def _ir_from_dict(data: dict) -> Optional[MemoryQueryIR]:
    try:
        from .memory_query_ir import (
            ActRef,
            MemoryRelation,
            MemorySelector,
            QueryIntent,
            RelationRef,
            RoleRef,
            SurfaceMode,
        )

        target_data = data.get("target") or {}
        rel_data = data.get("relation") or {}
        anchor_data = rel_data.get("anchor")

        def selector(d):
            return MemorySelector(
                role=RoleRef(d.get("role", "any")),
                act=ActRef(d.get("act", "any")),
                ordinal=d.get("ordinal"),
                quantity=d.get("quantity"),
                recent=bool(d.get("recent", False)),
                fact_key=d.get("fact_key"),
                semantic_hint=d.get("semantic_hint"),
                tags=tuple(d.get("tags") or ()),
            )

        return MemoryQueryIR(
            intent=QueryIntent(data.get("intent", "unknown")),
            target=selector(target_data),
            relation=MemoryRelation(
                RelationRef(rel_data.get("relation", "self")),
                anchor=selector(anchor_data) if anchor_data else None,
            ),
            surface=SurfaceMode(data.get("surface", "exact")),
            confidence=float(data.get("confidence", 0.0)),
            ambiguity=float(data.get("ambiguity", 1.0)),
            evidence=tuple(data.get("evidence") or ()),
            warnings=tuple(data.get("warnings") or ()),
            raw_text=str(data.get("raw_text", "")),
        )
    except Exception:
        return None


def normalize_vi(text: str) -> str:
    text = unicodedata.normalize("NFD", text.lower())
    text = "".join(ch for ch in text if unicodedata.category(ch) != "Mn")
    text = re.sub(r"[^a-z0-9\s_.-]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def text_similarity(a: str, b: str) -> float:
    a_tokens = set(normalize_vi(a).split())
    b_tokens = set(normalize_vi(b).split())
    if not a_tokens or not b_tokens:
        return 0.0
    return len(a_tokens & b_tokens) / len(a_tokens | b_tokens)
