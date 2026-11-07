from __future__ import annotations

"""
memory_packet_builder.py
========================

Biến MemoryRetrievalResult thành MemoryPacket tương thích downstream.

MemoryPacket vẫn là object trung gian cho prompt/attention/appraisal.
Nhưng từ giờ activations đã có score_breakdown thật, không phải đo lại từ string.
"""

from dataclasses import dataclass, field
from typing import Any

try:
    from .memory_record import compact_text, clamp01
    from .memory_activation import MemoryActivation, MemoryRetrievalResult
except Exception:  # pragma: no cover
    from memory_record import compact_text, clamp01
    from memory_activation import MemoryActivation, MemoryRetrievalResult


@dataclass(slots=True)
class MemoryPacket:
    # Metadata typed của chính lượt retrieve; không phải store thứ hai.
    source_records: tuple[Any, ...] = ()
    episodic: list[str] = field(default_factory=list)
    semantic: list[str] = field(default_factory=list)
    relational: list[str] = field(default_factory=list)
    affective_residue: list[str] = field(default_factory=list)
    procedural: list[str] = field(default_factory=list)
    repair: list[str] = field(default_factory=list)
    temporal: list[str] = field(default_factory=list)
    spatial: list[str] = field(default_factory=list)
    worldview: list[str] = field(default_factory=list)
    confidence_notes: list[str] = field(default_factory=list)
    token_budget: int = 900
    source_counts: dict[str, int] = field(default_factory=dict)
    score: float = 0.0
    dominant_layer: str = ""
    attention_bias: float = 0.0
    appraisal_bias: float = 0.0
    wording_bias: float = 0.0
    silence_bias: float = 0.0
    activations: tuple[MemoryActivation, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "episodic": list(self.episodic),
            "semantic": list(self.semantic),
            "relational": list(self.relational),
            "affective_residue": list(self.affective_residue),
            "procedural": list(self.procedural),
            "repair": list(self.repair),
            "temporal": list(self.temporal),
            "spatial": list(self.spatial),
            "worldview": list(self.worldview),
            "confidence_notes": list(self.confidence_notes),
            "token_budget": int(self.token_budget),
            "source_counts": dict(self.source_counts),
            "score": round(float(self.score), 3),
            "dominant_layer": self.dominant_layer,
            "attention_bias": round(float(self.attention_bias), 3),
            "appraisal_bias": round(float(self.appraisal_bias), 3),
            "wording_bias": round(float(self.wording_bias), 3),
            "silence_bias": round(float(self.silence_bias), 3),
            "activations": [item.to_dict() for item in self.activations],
        }

    def to_attention_lines(self, *, max_items: int = 5) -> tuple[str, ...]:
        return tuple(item.to_attention_line() for item in self.activations[:max_items])

    def render_prompt_context(self, *, max_lines: int = 18) -> str:
        if not self.activations:
            return ""

        # Sort activations descending by score
        sorted_activations = sorted(self.activations, key=lambda a: a.score, reverse=True)

        # Limit to top 3-5 activations
        limit = max(3, min(5, len(sorted_activations)))
        top_activations = sorted_activations[:limit]

        layer_names = {
            "semantic": "semantic_memory",
            "relational": "relational_memory",
            "repair": "repair_memory",
            "procedural": "procedural_memory",
            "affective_residue": "affective_residue_memory",
            "episodic": "episodic_memory",
        }

        lines = [
            "memory_context: use these as known facts about the user/context. Do not fabricate beyond what is listed. Do not use as current screen or file evidence.",
        ]
        for activation in top_activations:
            anchors = ",".join(activation.matched_anchors[:4])
            anchor_part = f", anchors={anchors}" if anchors else ""
            layer_name = layer_names.get(activation.layer, activation.layer)
            lines.append(
                f"- {layer_name}: {activation.text}"
                f" (score={activation.score:.2f}{anchor_part})"
            )
        return "\n".join(lines)


class MemoryPacketBuilder:
    def from_retrieval_result(self, result: MemoryRetrievalResult, *, token_budget: int = 900) -> MemoryPacket:
        packet = MemoryPacket(
            token_budget=token_budget,
            score=result.score,
            dominant_layer=result.dominant_layer,
            attention_bias=result.attention_bias,
            appraisal_bias=result.appraisal_bias,
            wording_bias=result.wording_bias,
            silence_bias=result.silence_bias,
            activations=result.activations,
            source_records=result.source_records,
        )

        groups = {
            "semantic": packet.semantic,
            "relational": packet.relational,
            "episodic": packet.episodic,
            "procedural": packet.procedural,
            "affective_residue": packet.affective_residue,
            "repair": packet.repair,
        }

        seen: set[tuple[str, str]] = set()
        for activation in result.activations:
            group = groups.get(activation.layer)
            if group is None:
                continue
            key = (activation.layer, activation.text)
            if key in seen:
                continue
            seen.add(key)
            group.append(activation.text)

        packet.source_counts = {name: len(values) for name, values in groups.items()}
        packet.confidence_notes = self._confidence_notes(packet)
        return self.fit_budget(packet, token_budget=token_budget)

    def fit_budget(self, packet: MemoryPacket, *, token_budget: int) -> MemoryPacket:
        budget_chars = max(400, int(token_budget or 900) * 4)
        groups = [packet.repair, packet.procedural, packet.semantic, packet.relational, packet.affective_residue, packet.episodic]
        used = 0
        for group in groups:
            kept: list[str] = []
            for line in group:
                cost = len(line) + 12
                if used + cost > budget_chars:
                    continue
                kept.append(line)
                used += cost
            group[:] = kept
        return packet

    def _confidence_notes(self, packet: MemoryPacket) -> list[str]:
        notes: list[str] = []
        if packet.affective_residue:
            notes.append("Affective residue là pressure nội bộ, không được surface như claim cảm xúc trực tiếp.")
        if packet.relational:
            notes.append("Relational memory là nhịp quan hệ tích tụ, không phải bằng chứng hiện tại.")
        if packet.episodic:
            notes.append("Episodic memory là sự kiện quá khứ; chỉ surface nếu user hỏi hoặc relevance rất cao.")
        if packet.repair:
            notes.append("Repair memory là hậu quả quá khứ; dùng để chỉnh chiến lược, không phải luật cấm.")
        if packet.activations:
            notes.append("Memory activation có score breakdown; không dùng như current screen/file evidence.")
        return notes[:6]
