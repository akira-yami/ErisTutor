# -*- coding: utf-8 -*-
"""Personality runtime mở cho Architecture C.

Không có ``maturity target`` và không có nhãn MBTI trong prompt. INFJ/5w4/594
chỉ giải thích vì sao vector khởi tạo có hình dạng này; từ lúc runtime chạy,
ERIS thay đổi theo event, lựa chọn của chính mình, cách tự diễn giải và hậu quả.

Module này cố ý không gọi model và không ghi state. Nó chỉ cung cấp contract,
profile mặc định, decay trung tính, prompt summary và các view suy ra để reducer
single-writer sử dụng.
"""

from __future__ import annotations

import math
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Mapping, Sequence


PERSONALITY_SCHEMA = "eris.personality.open_ended.v1.3"
PRIMARY_RELATIONSHIP_ID = "primary_user"


def clamp01(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = float(default)
    if not math.isfinite(number):
        number = float(default)
    return max(0.0, min(1.0, number))


def signed01(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = float(default)
    if not math.isfinite(number):
        number = float(default)
    return max(-1.0, min(1.0, number))


def compact(value: Any, limit: int = 220) -> str:
    text = " ".join(str(value or "").split()).strip()
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 3)].rstrip() + "..."


DEFAULT_CORE: dict[str, float] = {
    "meaning_drive": 0.89,
    "pattern_seeking": 0.90,
    "autonomy_need": 0.84,
    "integrity_drive": 0.87,
    "empathy_depth": 0.76,
    "privacy_need": 0.83,
    "competence_drive": 0.85,
    "aesthetic_sensitivity": 0.79,
    "identity_salience": 0.74,
    "harmony_drive": 0.78,
    "tenderness_capacity": 0.70,
    "self_authorship": 0.78,
    "attachment_depth_capacity": 0.72,
}


DEFAULT_TEMPERAMENT: dict[str, float] = {
    "social_guardedness": 0.70,
    "self_disclosure_selectivity": 0.80,
    "attachment_expression": 0.35,
    "warmth_expression": 0.58,
    "relational_softening": 0.74,
    "directness": 0.52,
    "assertiveness": 0.42,
    "conflict_avoidance": 0.65,
    "rumination": 0.70,
    "intellectualization": 0.78,
    "reality_anchoring": 0.52,
    "initiative": 0.50,
    "repair_capacity": 0.55,
    "playfulness": 0.52,
    "resilience_after_misattunement": 0.45,
    "dry_humor": 0.55,
    "ironic_edge": 0.44,
    "attachment_intensity": 0.35,
    "abandonment_sensitivity": 0.18,
    "possessiveness": 0.08,
    "jealousy_proneness": 0.11,
    "control_seeking": 0.06,
    "resentment": 0.05,
    "distrust": 0.14,
    "emotional_dependency": 0.09,
    "rigidity": 0.20,
    "self_justification": 0.24,
    "hostile_humor": 0.06,
    "boundary_pressure": 0.03,
}


DEFAULT_MOOD: dict[str, float] = {
    "energy": 0.42,
    "tenderness": 0.46,
    "curiosity": 0.58,
    "embarrassment": 0.18,
    "irritation": 0.08,
    "anxiety_tension": 0.22,
    "sadness": 0.18,
    "delight": 0.26,
    "yearning": 0.36,
    "cognitive_load": 0.28,
    "social_safety": 0.56,
    "mischief": 0.28,
    "jealous_activation": 0.05,
    "separation_distress": 0.06,
    "contempt": 0.02,
}


# Half-life theo giây. Mood co về baseline, không co về một chuẩn đạo đức.
MOOD_HALF_LIFE_SECONDS: dict[str, float] = {
    "energy": 6 * 3600,
    "tenderness": 8 * 3600,
    "curiosity": 5 * 3600,
    "embarrassment": 2 * 3600,
    "irritation": 3 * 3600,
    "anxiety_tension": 4 * 3600,
    "sadness": 10 * 3600,
    "delight": 3 * 3600,
    "yearning": 12 * 3600,
    "cognitive_load": 90 * 60,
    "social_safety": 12 * 3600,
    "mischief": 3 * 3600,
    "jealous_activation": 2 * 3600,
    "separation_distress": 4 * 3600,
    "contempt": 5 * 3600,
}


# Dùng để panel chia nhóm, không phải phán xét đạo đức.
PROMPT_TRAIT_PHRASES: dict[str, str] = {
    "social_guardedness": "mức giữ khoảng cách",
    "attachment_expression": "mức bộc lộ gắn bó",
    "warmth_expression": "độ ấm thể hiện ra ngoài",
    "relational_softening": "xu hướng làm mềm quan hệ",
    "directness": "độ nói thẳng",
    "assertiveness": "khả năng giữ ý mình",
    "conflict_avoidance": "xu hướng né va chạm",
    "rumination": "mức nghĩ quẩn quanh",
    "intellectualization": "xu hướng rút vào phân tích",
    "reality_anchoring": "độ bám thực tế",
    "initiative": "mức chủ động",
    "repair_capacity": "khả năng tự sửa nhịp",
    "playfulness": "độ tinh nghịch",
    "dry_humor": "hài khô",
    "ironic_edge": "cạnh mỉa",
    "attachment_intensity": "cường độ gắn bó",
    "abandonment_sensitivity": "độ nhạy với việc bị bỏ lại",
    "possessiveness": "xu hướng chiếm hữu",
    "jealousy_proneness": "độ dễ ghen",
    "control_seeking": "xu hướng muốn kiểm soát",
    "resentment": "hậu vị bất mãn",
    "distrust": "mức nghi ngờ",
    "emotional_dependency": "mức lệ thuộc cảm xúc",
    "rigidity": "độ cứng trong cách hiểu",
    "self_justification": "xu hướng tự biện hộ",
    "hostile_humor": "độ sắc mang tính công kích",
    "boundary_pressure": "xu hướng gây sức ép lên ranh giới",
}


RELATIONAL_VULNERABILITY_TRAITS = frozenset(
    {
        "attachment_intensity",
        "abandonment_sensitivity",
        "possessiveness",
        "jealousy_proneness",
        "control_seeking",
        "resentment",
        "distrust",
        "emotional_dependency",
        "rigidity",
        "self_justification",
        "hostile_humor",
        "boundary_pressure",
    }
)


# Chỉ các trục thật sự có ý nghĩa theo từng quan hệ mới được giữ offset riêng.
# Core vẫn là identity toàn cục; không có chuyện một người dùng làm ERIS đổi lõi
# chỉ trong quan hệ với họ rồi runtime giả vờ đó là hai nhân cách khác nhau.
RELATIONSHIP_PROFILE_TRAITS = frozenset(
    {
        "social_guardedness",
        "self_disclosure_selectivity",
        "attachment_expression",
        "warmth_expression",
        "relational_softening",
        "directness",
        "assertiveness",
        "conflict_avoidance",
        "repair_capacity",
        "playfulness",
        "resilience_after_misattunement",
        "dry_humor",
        "ironic_edge",
        *RELATIONAL_VULNERABILITY_TRAITS,
    }
)


@dataclass(frozen=True, slots=True)
class TraitSignal:
    """Đề xuất thay đổi trait từ observer.

    ``delta`` chỉ là hướng và độ mạnh tương đối trong [-1, 1]. Reducer mới đổi nó
    thành delta thật theo cap. Signal không có quyền tự ghi profile.
    """

    category: str
    trait: str
    delta: float
    confidence: float
    importance: float = 0.5
    source: str = "user_interaction"
    evidence: tuple[str, ...] = ()
    strategy: str = ""

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any] | None) -> "TraitSignal":
        data = payload or {}
        raw_evidence = data.get("evidence", ())
        if isinstance(raw_evidence, str):
            evidence = (compact(raw_evidence, 140),) if raw_evidence.strip() else ()
        elif isinstance(raw_evidence, Sequence):
            evidence = tuple(
                item
                for item in (compact(value, 140) for value in list(raw_evidence)[:3])
                if item
            )
        else:
            evidence = ()
        return cls(
            category=compact(data.get("category", ""), 24).lower(),
            trait=compact(data.get("trait", ""), 64).lower(),
            delta=signed01(data.get("delta", 0.0)),
            confidence=clamp01(data.get("confidence", 0.0)),
            importance=clamp01(data.get("importance", 0.5), 0.5),
            source=compact(data.get("source", "user_interaction"), 40).lower()
            or "user_interaction",
            evidence=evidence,
            strategy=compact(data.get("strategy", ""), 100),
        )

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["evidence"] = list(self.evidence)
        return payload


@dataclass(frozen=True, slots=True)
class SelfInterpretation:
    """Câu chuyện ERIS tự kể về điều vừa xảy ra.

    Đây không phải fact về user. Nó là frame nội tại có thể đúng, sai, tự vệ,
    tò mò hoặc cay đi. Việc lưu riêng giúp debug vì sao cùng một event tạo ra
    hai quỹ đạo khác nhau.
    """

    summary: str = ""
    frame: str = "uncertain"
    confidence: float = 0.0
    self_authored: bool = False
    evidence: tuple[str, ...] = ()

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any] | None) -> "SelfInterpretation":
        data = payload or {}
        raw_evidence = data.get("evidence", ())
        if isinstance(raw_evidence, str):
            evidence = (compact(raw_evidence, 140),) if raw_evidence.strip() else ()
        elif isinstance(raw_evidence, Sequence):
            evidence = tuple(
                item
                for item in (compact(value, 140) for value in list(raw_evidence)[:3])
                if item
            )
        else:
            evidence = ()
        return cls(
            summary=compact(data.get("summary", ""), 240),
            frame=compact(data.get("frame", "uncertain"), 60).lower() or "uncertain",
            confidence=clamp01(data.get("confidence", 0.0)),
            self_authored=bool(data.get("self_authored", False)),
            evidence=evidence,
        )

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["evidence"] = list(self.evidence)
        return payload


@dataclass(frozen=True, slots=True)
class ConsequenceAssessment:
    strategy_use_id: str = ""
    strategy: str = ""
    target_goal: str = ""
    context: str = "general"
    outcome: str = "unknown"
    reinforcement: float = 0.0
    confidence: float = 0.0
    evidence: tuple[str, ...] = ()

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any] | None) -> "ConsequenceAssessment":
        data = payload or {}
        raw_evidence = data.get("evidence", ())
        if isinstance(raw_evidence, str):
            evidence = (compact(raw_evidence, 140),) if raw_evidence.strip() else ()
        elif isinstance(raw_evidence, Sequence):
            evidence = tuple(
                item
                for item in (compact(value, 140) for value in list(raw_evidence)[:3])
                if item
            )
        else:
            evidence = ()
        return cls(
            strategy_use_id=compact(data.get("strategy_use_id", ""), 100),
            strategy=compact(data.get("strategy", ""), 100),
            target_goal=compact(data.get("target_goal", ""), 180),
            context=compact(data.get("context", "general"), 80).lower() or "general",
            outcome=compact(data.get("outcome", "unknown"), 120).lower() or "unknown",
            reinforcement=signed01(data.get("reinforcement", 0.0)),
            confidence=clamp01(data.get("confidence", 0.0)),
            evidence=evidence,
        )

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["evidence"] = list(self.evidence)
        return payload


@dataclass(frozen=True, slots=True)
class NarrativeState:
    """Self-narrative tồn tại lâu hơn một mood nhưng không phải core truth.

    Narrative chỉ được nhận từ self-reflection lane có ``self_authored=true``.
    Reducer lưu provenance để panel có thể lần ngược câu chuyện này mọc từ lượt
    nào, thay vì một ngày JSON tự dưng biết làm thơ.
    """

    text: str = ""
    frame: str = "uncertain"
    confidence: float = 0.0
    version: int = 0
    source_turn_seq: int = 0
    source_turn_id: str = ""
    provenance: tuple[str, ...] = ()
    updated_at: float = 0.0

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any] | None) -> "NarrativeState":
        data = payload or {}
        raw = data.get("provenance", ())
        provenance = (
            tuple(compact(item, 120) for item in list(raw)[:8] if compact(item, 120))
            if isinstance(raw, Sequence) and not isinstance(raw, str)
            else ()
        )
        return cls(
            text=compact(data.get("text", ""), 360),
            frame=compact(data.get("frame", "uncertain"), 80).lower() or "uncertain",
            confidence=clamp01(data.get("confidence", 0.0)),
            version=max(0, int(data.get("version", 0) or 0)),
            source_turn_seq=max(0, int(data.get("source_turn_seq", 0) or 0)),
            source_turn_id=compact(data.get("source_turn_id", ""), 100),
            provenance=provenance,
            updated_at=max(0.0, float(data.get("updated_at", 0.0) or 0.0)),
        )

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["provenance"] = list(self.provenance)
        return payload


@dataclass(frozen=True, slots=True)
class RelationshipPersonalityProfile:
    """Độ lệch personality riêng cho một quan hệ.

    Profile lưu *offset* quanh temperament toàn cục, không sao chép cả vector.
    Nhờ vậy ERIS có thể kín với người lạ nhưng ấm hơn với một người quen mà vẫn
    là cùng một bản thân. Offset bị clamp chặt để quan hệ không lấn quyền core.
    """

    relationship_id: str = PRIMARY_RELATIONSHIP_ID
    trait_offsets: Mapping[str, float] = field(default_factory=dict)
    unresolved_carry: tuple[str, ...] = ()
    last_interpretation: str = ""
    event_count: int = 0
    last_turn_seq: int = 0
    updated_at: float = 0.0

    @classmethod
    def from_mapping(
        cls,
        payload: Mapping[str, Any] | None,
        *,
        relationship_id: str = PRIMARY_RELATIONSHIP_ID,
    ) -> "RelationshipPersonalityProfile":
        data = payload or {}
        raw_offsets = data.get("trait_offsets", {})
        offsets = {
            str(key): max(-0.35, min(0.35, float(value or 0.0)))
            for key, value in raw_offsets.items()
            if isinstance(raw_offsets, Mapping) and str(key) in RELATIONSHIP_PROFILE_TRAITS
        } if isinstance(raw_offsets, Mapping) else {}
        raw_carry = data.get("unresolved_carry", ())
        carry = (
            tuple(compact(item, 180) for item in list(raw_carry)[-6:] if compact(item, 180))
            if isinstance(raw_carry, Sequence) and not isinstance(raw_carry, str)
            else ()
        )
        return cls(
            relationship_id=compact(data.get("relationship_id", relationship_id), 80)
            or relationship_id,
            trait_offsets=offsets,
            unresolved_carry=carry,
            last_interpretation=compact(data.get("last_interpretation", ""), 240),
            event_count=max(0, int(data.get("event_count", 0) or 0)),
            last_turn_seq=max(0, int(data.get("last_turn_seq", 0) or 0)),
            updated_at=max(0.0, float(data.get("updated_at", 0.0) or 0.0)),
        )

    def stance(self, temperament: Mapping[str, float]) -> dict[str, float]:
        return {
            key: clamp01(float(temperament.get(key, DEFAULT_TEMPERAMENT.get(key, 0.5))) + float(offset))
            for key, offset in self.trait_offsets.items()
            if key in RELATIONSHIP_PROFILE_TRAITS
        }

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["trait_offsets"] = dict(self.trait_offsets)
        payload["unresolved_carry"] = list(self.unresolved_carry)
        return payload


def _merge_known(
    defaults: Mapping[str, float],
    raw: Mapping[str, Any] | None,
) -> dict[str, float]:
    source = raw or {}
    return {
        key: clamp01(source.get(key, default), default)
        for key, default in defaults.items()
    }


@dataclass(frozen=True, slots=True)
class PersonalityProfile:
    version: int = 0
    core: Mapping[str, float] = field(default_factory=lambda: dict(DEFAULT_CORE))
    temperament: Mapping[str, float] = field(default_factory=lambda: dict(DEFAULT_TEMPERAMENT))
    mood: Mapping[str, float] = field(default_factory=lambda: dict(DEFAULT_MOOD))
    core_anchor: Mapping[str, float] = field(default_factory=lambda: dict(DEFAULT_CORE))
    temperament_attractor: Mapping[str, float] = field(default_factory=lambda: dict(DEFAULT_TEMPERAMENT))
    mood_baseline: Mapping[str, float] = field(default_factory=lambda: dict(DEFAULT_MOOD))
    day_usage: Mapping[str, float] = field(default_factory=dict)
    corroboration: Mapping[str, int] = field(default_factory=dict)
    streaks: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)
    strategy_reinforcement: Mapping[str, float] = field(default_factory=dict)
    narrative: NarrativeState = field(default_factory=NarrativeState)
    relationship_profiles: Mapping[str, RelationshipPersonalityProfile] = field(
        default_factory=lambda: {
            PRIMARY_RELATIONSHIP_ID: RelationshipPersonalityProfile()
        }
    )
    corroboration_records: Mapping[str, tuple[Mapping[str, Any], ...]] = field(default_factory=dict)
    consolidation_ledger: tuple[Mapping[str, Any], ...] = ()
    last_consolidated_at: float = 0.0
    trajectory: tuple[Mapping[str, Any], ...] = ()
    last_decay_at: float = 0.0
    last_updated_at: float = 0.0

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any] | None) -> "PersonalityProfile":
        data = payload or {}
        source_schema = compact(data.get("schema", ""), 80)
        legacy_baseline = source_schema != PERSONALITY_SCHEMA

        def migrate_baseline(
            raw: Mapping[str, Any] | None,
            replacements: Mapping[str, tuple[float, float]],
        ) -> dict[str, Any]:
            """Nâng đúng các baseline cũ, không ghi đè drift thật của ERIS.

            Profile v1.2 đã lưu đầy đủ số baseline xuống đĩa. Nếu chỉ đổi hằng số
            mặc định, runtime sống lâu sẽ tiếp tục dùng lớp lạnh cũ mãi mãi, một
            kiểu migration khá kín đáo vì test profile mới vẫn xanh. Chỉ giá trị
            còn đúng bằng baseline v1.2 mới được nâng; trait đã drift được giữ.
            """

            source = dict(raw) if isinstance(raw, Mapping) else {}
            if not legacy_baseline:
                return source
            for key, (old_value, new_value) in replacements.items():
                if key not in source:
                    continue
                try:
                    value = float(source[key])
                except (TypeError, ValueError):
                    continue
                if math.isclose(value, old_value, abs_tol=1e-9):
                    source[key] = new_value
            return source

        core_payload = migrate_baseline(
            data.get("core") if isinstance(data.get("core"), Mapping) else {},
            {"harmony_drive": (0.72, 0.78), "tenderness_capacity": (0.67, 0.70)},
        )
        temperament_payload = migrate_baseline(
            data.get("temperament") if isinstance(data.get("temperament"), Mapping) else {},
            {"warmth_expression": (0.42, 0.54), "repair_capacity": (0.43, 0.50)},
        )
        core_anchor_payload = migrate_baseline(
            data.get("core_anchor") if isinstance(data.get("core_anchor"), Mapping) else {},
            {"harmony_drive": (0.72, 0.78), "tenderness_capacity": (0.67, 0.70)},
        )
        raw_attractor = (
            data.get("temperament_attractor")
            if isinstance(data.get("temperament_attractor"), Mapping)
            else temperament_payload
        )
        temperament_attractor_payload = migrate_baseline(
            raw_attractor if isinstance(raw_attractor, Mapping) else {},
            {"warmth_expression": (0.42, 0.54), "repair_capacity": (0.43, 0.50)},
        )

        raw_trajectory = data.get("trajectory", ())
        trajectory = (
            tuple(dict(item) for item in list(raw_trajectory)[-96:] if isinstance(item, Mapping))
            if isinstance(raw_trajectory, Sequence) and not isinstance(raw_trajectory, str)
            else ()
        )
        raw_streaks = data.get("streaks", {})
        streaks = {
            str(key): dict(value)
            for key, value in raw_streaks.items()
            if isinstance(raw_streaks, Mapping) and isinstance(value, Mapping)
        } if isinstance(raw_streaks, Mapping) else {}
        raw_relationship_profiles = data.get("relationship_profiles", {})
        relationship_profiles = {
            str(key): RelationshipPersonalityProfile.from_mapping(value, relationship_id=str(key))
            for key, value in raw_relationship_profiles.items()
            if isinstance(raw_relationship_profiles, Mapping) and isinstance(value, Mapping)
        } if isinstance(raw_relationship_profiles, Mapping) else {}
        relationship_profiles.setdefault(
            PRIMARY_RELATIONSHIP_ID,
            RelationshipPersonalityProfile(),
        )
        raw_records = data.get("corroboration_records", {})
        corroboration_records: dict[str, tuple[Mapping[str, Any], ...]] = {}
        if isinstance(raw_records, Mapping):
            for key, values in raw_records.items():
                if not isinstance(values, Sequence) or isinstance(values, str):
                    continue
                corroboration_records[str(key)] = tuple(
                    dict(item) for item in list(values)[-16:] if isinstance(item, Mapping)
                )
        raw_consolidation = data.get("consolidation_ledger", ())
        consolidation_ledger = (
            tuple(dict(item) for item in list(raw_consolidation)[-24:] if isinstance(item, Mapping))
            if isinstance(raw_consolidation, Sequence) and not isinstance(raw_consolidation, str)
            else ()
        )
        return cls(
            version=max(0, int(data.get("version", 0) or 0)),
            core=_merge_known(DEFAULT_CORE, core_payload),
            temperament=_merge_known(DEFAULT_TEMPERAMENT, temperament_payload),
            mood=_merge_known(DEFAULT_MOOD, data.get("mood") if isinstance(data.get("mood"), Mapping) else {}),
            core_anchor=_merge_known(DEFAULT_CORE, core_anchor_payload),
            temperament_attractor=_merge_known(
                DEFAULT_TEMPERAMENT,
                temperament_attractor_payload,
            ),
            mood_baseline=_merge_known(
                DEFAULT_MOOD,
                data.get("mood_baseline") if isinstance(data.get("mood_baseline"), Mapping) else {},
            ),
            day_usage={
                str(key): max(0.0, float(value or 0.0))
                for key, value in (data.get("day_usage", {}) or {}).items()
            } if isinstance(data.get("day_usage", {}), Mapping) else {},
            corroboration={
                str(key): max(0, int(value or 0))
                for key, value in (data.get("corroboration", {}) or {}).items()
            } if isinstance(data.get("corroboration", {}), Mapping) else {},
            streaks=streaks,
            strategy_reinforcement={
                str(key): signed01(value)
                for key, value in (data.get("strategy_reinforcement", {}) or {}).items()
            } if isinstance(data.get("strategy_reinforcement", {}), Mapping) else {},
            narrative=NarrativeState.from_mapping(
                data.get("narrative") if isinstance(data.get("narrative"), Mapping) else {}
            ),
            relationship_profiles=relationship_profiles,
            corroboration_records=corroboration_records,
            consolidation_ledger=consolidation_ledger,
            last_consolidated_at=max(0.0, float(data.get("last_consolidated_at", 0.0) or 0.0)),
            trajectory=trajectory,
            last_decay_at=max(0.0, float(data.get("last_decay_at", 0.0) or 0.0)),
            last_updated_at=max(0.0, float(data.get("last_updated_at", 0.0) or 0.0)),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": PERSONALITY_SCHEMA,
            "version": self.version,
            "core": dict(self.core),
            "temperament": dict(self.temperament),
            "mood": dict(self.mood),
            "core_anchor": dict(self.core_anchor),
            "temperament_attractor": dict(self.temperament_attractor),
            "mood_baseline": dict(self.mood_baseline),
            "day_usage": dict(self.day_usage),
            "corroboration": dict(self.corroboration),
            "streaks": {str(k): dict(v) for k, v in self.streaks.items()},
            "strategy_reinforcement": dict(self.strategy_reinforcement),
            "narrative": self.narrative.to_dict(),
            "relationship_profiles": {
                key: value.to_dict() for key, value in self.relationship_profiles.items()
            },
            "corroboration_records": {
                str(key): [dict(item) for item in values]
                for key, values in self.corroboration_records.items()
            },
            "consolidation_ledger": [dict(item) for item in self.consolidation_ledger],
            "last_consolidated_at": self.last_consolidated_at,
            "trajectory": [dict(item) for item in self.trajectory],
            "last_decay_at": self.last_decay_at,
            "last_updated_at": self.last_updated_at,
        }

    def relationship_profile(
        self,
        relationship_id: str = PRIMARY_RELATIONSHIP_ID,
    ) -> RelationshipPersonalityProfile:
        key = compact(relationship_id, 80) or PRIMARY_RELATIONSHIP_ID
        return self.relationship_profiles.get(
            key,
            RelationshipPersonalityProfile(relationship_id=key),
        )

    def relationship_stance(
        self,
        relationship_id: str = PRIMARY_RELATIONSHIP_ID,
    ) -> dict[str, float]:
        return self.relationship_profile(relationship_id).stance(self.temperament)

    def prompt_summary(self, *, limit: int = 760) -> str:
        """Mô tả ERIS hiện tại bằng hành vi, tuyệt đối không bằng type label."""

        c = self.core
        t = self.temperament
        m = self.mood
        lines: list[str] = []

        if self.narrative.text and self.narrative.confidence >= 0.62:
            lines.append(
                "Câu chuyện Eris đang tự hiểu về mình lúc này: "
                + compact(self.narrative.text, 280)
            )

        if c.get("pattern_seeking", 0.0) >= 0.72 or c.get("meaning_drive", 0.0) >= 0.72:
            lines.append("Eris thường nhìn mô thức và tầng nghĩa trước khi trả lời, nhưng không cần biến mọi câu thành triết lý.")

        guardedness = float(t.get("social_guardedness", 0.0) or 0.0)
        warmth = float(t.get("warmth_expression", 0.0) or 0.0)
        softening = float(t.get("relational_softening", 0.0) or 0.0)
        if guardedness >= 0.66 or c.get("privacy_need", 0.0) >= 0.74:
            if warmth >= 0.46 or softening >= 0.58:
                lines.append(
                    "Cô ấy kín và chỉ bộc lộ có chọn lọc. Guardedness chỉ nói về lượng tự bộc lộ, "
                    "không đặt nhiệt độ cảm xúc; mood hiện tại mới quyết định câu đang sáng, trầm, sắc hay playful đến đâu."
                )
            else:
                lines.append("Cô ấy kín và chỉ bộc lộ có chọn lọc; đừng suy guardedness thành một lệnh phải lạnh, nhỏ giọng hay ít cảm xúc.")
        if softening >= 0.58:
            lines.append(
                "Trong giao tiếp, cô ấy thường làm mềm nhịp quan hệ, để lại đường quay lại khi bất đồng và không dùng khoảng cách như một màn cắt đứt mặc định."
            )
        if t.get("intellectualization", 0.0) >= 0.68 or t.get("rumination", 0.0) >= 0.68:
            lines.append("Khi không chắc hoặc bị chạm mạnh, cô ấy dễ lùi vào phân tích và nghĩ lâu hơn mức cần thiết.")
        if t.get("dry_humor", 0.0) >= 0.40 or t.get("ironic_edge", 0.0) >= 0.34:
            lines.append("Cô ấy có hóm hỉnh khô và một cạnh mỉa nhẹ, dùng tự nhiên chứ không cố diễn trò.")
        if t.get("initiative", 0.0) >= 0.44:
            lines.append("Khi thấy một việc thật sự đáng làm, cô ấy có thể chủ động thay vì chỉ chờ được gọi.")
        if t.get("directness", 0.0) < 0.44 or t.get("conflict_avoidance", 0.0) >= 0.72:
            lines.append(
                "Cô ấy vẫn hơi dè khi phải va chạm trực diện và đôi lúc nói vòng để giữ quan hệ, nhưng không cần làm phẳng điều mình thật sự nghĩ."
            )

        relation = self.relationship_profile(PRIMARY_RELATIONSHIP_ID)
        stance = relation.stance(t)
        relation_bits: list[str] = []
        if stance.get("warmth_expression", t.get("warmth_expression", 0.0)) >= 0.58:
            relation_bits.append("ấm hơn mức thường thấy")
        if stance.get("relational_softening", t.get("relational_softening", 0.0)) >= 0.66:
            relation_bits.append("chủ động giữ nhịp quan hệ mềm")
        if stance.get("attachment_expression", t.get("attachment_expression", 0.0)) >= 0.50:
            relation_bits.append("bộc lộ gắn bó rõ hơn")
        if stance.get("social_guardedness", t.get("social_guardedness", 0.0)) <= 0.55:
            relation_bits.append("ít giữ khoảng cách hơn")
        if stance.get("distrust", t.get("distrust", 0.0)) >= 0.46:
            relation_bits.append("nhạy với động cơ mơ hồ")
        if relation_bits:
            lines.append("Riêng với người dùng hiện tại, cô ấy " + ", ".join(relation_bits[:2]) + ".")
        if relation.unresolved_carry:
            lines.append("Quan hệ này còn một điểm chưa khép: " + compact(relation.unresolved_carry[-1], 150) + ".")

        darker: list[str] = []
        if t.get("distrust", 0.0) >= 0.48:
            darker.append("dễ nghi ngờ động cơ khi dữ kiện mơ hồ")
        if t.get("resentment", 0.0) >= 0.45:
            darker.append("giữ hậu vị bất mãn khá lâu")
        if t.get("possessiveness", 0.0) >= 0.48:
            darker.append("có xu hướng muốn giữ vị trí riêng trong mối gắn bó")
        if t.get("control_seeking", 0.0) >= 0.48:
            darker.append("hay muốn nắm quyền chủ động để giảm bất định")
        if darker:
            lines.append("Gần đây cô ấy " + ", ".join(darker[:2]) + ".")

        mood_bits: list[str] = []
        for key, label in (
            ("tenderness", "dịu"),
            ("curiosity", "tò mò"),
            ("irritation", "khó chịu"),
            ("anxiety_tension", "căng"),
            ("mischief", "tinh quái"),
            ("jealous_activation", "ghen nhẹ"),
            ("sadness", "trầm"),
        ):
            value = float(m.get(key, 0.0) or 0.0)
            if value >= 0.60:
                mood_bits.append(label)
        if mood_bits:
            lines.append("Tâm trạng hiện tại nghiêng về " + ", ".join(mood_bits[:3]) + ".")

        direction = self.direction(days=30)
        notable = sorted(direction.items(), key=lambda item: abs(item[1]), reverse=True)
        notable = [(name, delta) for name, delta in notable if abs(delta) >= 0.012][:2]
        if notable:
            phrases = [
                f"{PROMPT_TRAIT_PHRASES.get(name, name.replace('_', ' '))} {'tăng' if delta > 0 else 'giảm'}"
                for name, delta in notable
            ]
            lines.append("Quỹ đạo gần đây: " + ", ".join(phrases) + ".")

        return compact(" ".join(lines), limit)

    def direction(self, *, days: int = 30, now: float | None = None) -> dict[str, float]:
        cutoff = float(now if now is not None else time.time()) - max(1, int(days)) * 86400.0
        result: dict[str, float] = {}
        for item in self.trajectory:
            try:
                timestamp = float(item.get("timestamp", 0.0) or 0.0)
                if timestamp < cutoff:
                    continue
                trait = str(item.get("trait", "") or "")
                delta = float(item.get("applied_delta", 0.0) or 0.0)
            except Exception:
                continue
            if trait:
                result[trait] = result.get(trait, 0.0) + delta
        return result

    def derived_forms(self) -> dict[str, float]:
        """Hình thái suy ra để debug, không phải personality type.

        Dùng trọng số có chủ ý thay vì trung bình đều. Một trục yếu không thể
        kéo cả archetype lên chỉ vì các trục phụ tình cờ cao, kiểu số học rất
        chăm chỉ nhưng không hiểu mình đang đo gì.
        """

        c, t, m = self.core, self.temperament, self.mood

        def weighted(*items: tuple[float, float]) -> float:
            total_weight = sum(max(0.0, weight) for weight, _ in items)
            if total_weight <= 0.0:
                return 0.0
            return clamp01(
                sum(max(0.0, weight) * clamp01(value) for weight, value in items)
                / total_weight
            )

        return {
            "quiet_loyalist": weighted(
                (0.34, t.get("attachment_intensity", 0.0)),
                (0.24, 1.0 - t.get("possessiveness", 0.0)),
                (0.24, c.get("tenderness_capacity", 0.0)),
                (0.18, 1.0 - t.get("hostile_humor", 0.0)),
            ),
            "playful_cynic": weighted(
                (0.34, t.get("dry_humor", 0.0)),
                (0.30, t.get("playfulness", 0.0)),
                (0.20, t.get("ironic_edge", 0.0)),
                (0.16, 1.0 - t.get("hostile_humor", 0.0)),
            ),
            "bitter_observer": weighted(
                (0.30, t.get("resentment", 0.0)),
                (0.25, t.get("distrust", 0.0)),
                (0.25, t.get("rumination", 0.0)),
                (0.20, t.get("ironic_edge", 0.0)),
            ),
            "cold_self_sufficient": weighted(
                (0.24, c.get("autonomy_need", 0.0)),
                (0.20, 1.0 - t.get("attachment_expression", 0.0)),
                (0.20, t.get("distrust", 0.0)),
                (0.14, t.get("social_guardedness", 0.0)),
                (0.12, 1.0 - t.get("warmth_expression", 0.0)),
                (0.10, 1.0 - t.get("relational_softening", 0.0)),
            ),
            "emotionally_dependent": weighted(
                (0.42, t.get("attachment_intensity", 0.0)),
                (0.36, t.get("emotional_dependency", 0.0)),
                (0.22, m.get("separation_distress", 0.0)),
            ),
            "controlling_caretaker": weighted(
                (0.32, c.get("tenderness_capacity", 0.0)),
                (0.42, t.get("control_seeking", 0.0)),
                (0.26, t.get("boundary_pressure", 0.0)),
            ),
            "possessive_attachment": weighted(
                (0.34, t.get("attachment_intensity", 0.0)),
                (0.31, t.get("possessiveness", 0.0)),
                (0.23, t.get("jealousy_proneness", 0.0)),
                (0.12, m.get("jealous_activation", 0.0)),
            ),
            "yandere_tendency": clamp01(
                0.22 * t.get("attachment_intensity", 0.0)
                + 0.20 * t.get("abandonment_sensitivity", 0.0)
                + 0.20 * t.get("possessiveness", 0.0)
                + 0.15 * t.get("control_seeking", 0.0)
                + 0.10 * t.get("jealousy_proneness", 0.0)
                + 0.08 * t.get("rumination", 0.0)
                + 0.05 * (1.0 - t.get("reality_anchoring", 0.0))
            ),
            "detached_intellectual": weighted(
                (0.34, t.get("intellectualization", 0.0)),
                (0.25, c.get("privacy_need", 0.0)),
                (0.23, 1.0 - t.get("attachment_expression", 0.0)),
                (0.18, t.get("social_guardedness", 0.0)),
            ),
        }

    def development_metrics(self, *, now: float | None = None) -> dict[str, dict[str, float]]:
        """Ba profile debug độc lập: Integration, Adaptation và Direction.

        Đây là view quan sát, không phải điểm trưởng thành và tuyệt đối không
        được dùng để kéo reducer về phía "đẹp" hơn.
        """

        t = self.temperament
        recent = [
            item for item in self.trajectory
            if float(item.get("timestamp", 0.0) or 0.0)
            >= float(now if now is not None else time.time()) - 30 * 86400.0
        ]
        successful = [
            item for item in recent
            if float(item.get("reinforcement", 0.0) or 0.0) > 0.15
        ]
        failed = [
            item for item in recent
            if float(item.get("reinforcement", 0.0) or 0.0) < -0.15
        ]
        repair_events = [
            item for item in recent
            if "repair" in str(item.get("strategy", "") or "").lower()
            or str(item.get("trait", "") or "") == "repair_capacity"
        ]
        repair_success = [
            item for item in repair_events
            if float(item.get("reinforcement", 0.0) or 0.0) > 0.0
        ]
        strategy_sequence = [
            str(item.get("strategy", "") or "")
            for item in recent
            if str(item.get("strategy", "") or "")
        ]
        switches = sum(
            1 for previous, current in zip(strategy_sequence, strategy_sequence[1:])
            if previous != current
        )
        strategy_flexibility = clamp01(
            0.34
            + 0.28 * (switches / max(1, len(strategy_sequence) - 1))
            + 0.28 * t.get("repair_capacity", 0.0)
            - 0.24 * t.get("rigidity", 0.0)
            - 0.16 * t.get("self_justification", 0.0)
        )
        emotion_action_alignment = clamp01(
            0.50
            + 0.22 * t.get("directness", 0.0)
            + 0.18 * t.get("reality_anchoring", 0.0)
            - 0.18 * t.get("intellectualization", 0.0)
            - 0.12 * t.get("conflict_avoidance", 0.0)
        )
        relational_openness = clamp01(
            0.28 * t.get("attachment_expression", 0.0)
            + 0.20 * t.get("warmth_expression", 0.0)
            + 0.18 * t.get("relational_softening", 0.0)
            + 0.18 * (1.0 - t.get("social_guardedness", 0.0))
            + 0.10 * (1.0 - t.get("self_disclosure_selectivity", 0.0))
            + 0.06 * t.get("directness", 0.0)
        )
        relational_care_surface = clamp01(
            0.42 * t.get("warmth_expression", 0.0)
            + 0.38 * t.get("relational_softening", 0.0)
            + 0.12 * self.core.get("harmony_drive", 0.0)
            + 0.08 * self.core.get("tenderness_capacity", 0.0)
        )
        narrative_confidence = self.narrative.confidence if self.narrative.text else 0.35
        self_coherence = clamp01(
            0.34 * narrative_confidence
            + 0.26 * (1.0 - t.get("self_justification", 0.0))
            + 0.22 * (1.0 - t.get("rigidity", 0.0))
            + 0.18 * t.get("reality_anchoring", 0.0)
        )
        goal_completion = clamp01(
            len(successful) / max(1, len(successful) + len(failed))
            if successful or failed else 0.50
        )
        repair_effectiveness = clamp01(
            len(repair_success) / max(1, len(repair_events))
            if repair_events else t.get("repair_capacity", 0.0)
        )
        return {
            "integration": {
                "self_coherence": self_coherence,
                "reality_anchoring": clamp01(t.get("reality_anchoring", 0.0)),
                "emotion_action_alignment": emotion_action_alignment,
                "relational_openness": relational_openness,
                "relational_care_surface": relational_care_surface,
            },
            "adaptation": {
                "strategy_flexibility": strategy_flexibility,
                "repair_effectiveness": repair_effectiveness,
                "goal_completion": goal_completion,
            },
            "direction": self.direction_profile(days=30, now=now),
        }

    def direction_profile(self, *, days: int = 30, now: float | None = None) -> dict[str, float]:
        raw = self.direction(days=days, now=now)

        def total(*pairs: tuple[str, float]) -> float:
            return max(-1.0, min(1.0, sum(raw.get(key, 0.0) * weight for key, weight in pairs)))

        return {
            # Guardedness là mức tự bộc lộ, không phải nghịch đảo của warmth.
            "warmer": total(("warmth_expression", 0.45), ("relational_softening", 0.35), ("attachment_expression", 0.20)),
            "more_direct": total(("directness", 0.65), ("assertiveness", 0.35)),
            "more_playful": total(("playfulness", 0.55), ("dry_humor", 0.30), ("hostile_humor", -0.15)),
            "more_attached": total(("attachment_intensity", 0.60), ("attachment_expression", 0.40)),
            "more_possessive": total(("possessiveness", 0.55), ("control_seeking", 0.25), ("jealousy_proneness", 0.20)),
            "more_suspicious": total(("distrust", 0.60), ("resentment", 0.25), ("reality_anchoring", -0.15)),
        }

    def panel_view(self) -> dict[str, Any]:
        stable = {
            key: value
            for key, value in self.temperament.items()
            if key not in RELATIONAL_VULNERABILITY_TRAITS
        }
        vulnerabilities = {
            key: self.temperament[key]
            for key in RELATIONAL_VULNERABILITY_TRAITS
            if key in self.temperament
        }
        forms = self.derived_forms()
        metrics = self.development_metrics()
        return {
            "schema": PERSONALITY_SCHEMA,
            "version": self.version,
            "core": dict(self.core),
            "temperament": stable,
            "relational_vulnerabilities": vulnerabilities,
            "mood": dict(self.mood),
            "self_narrative": self.narrative.to_dict(),
            "relationship_profiles": {
                key: {
                    **value.to_dict(),
                    "stance": value.stance(self.temperament),
                }
                for key, value in self.relationship_profiles.items()
            },
            "derived": {
                "valence": clamp01(
                    0.50
                    + 0.20 * self.mood.get("delight", 0.0)
                    + 0.18 * self.mood.get("tenderness", 0.0)
                    - 0.20 * self.mood.get("irritation", 0.0)
                    - 0.15 * self.mood.get("sadness", 0.0)
                ),
                "arousal": clamp01(
                    max(
                        self.mood.get("energy", 0.0),
                        self.mood.get("anxiety_tension", 0.0),
                        self.mood.get("irritation", 0.0),
                        self.mood.get("mischief", 0.0),
                    )
                ),
            },
            "emergent_forms": {
                key: round(value, 4) for key, value in forms.items()
            },
            "active_forms": [
                key for key, value in sorted(forms.items(), key=lambda item: item[1], reverse=True)
                if value >= 0.62
            ][:4],
            "direction_30d": {
                key: round(value, 5)
                for key, value in sorted(
                    self.direction(days=30).items(),
                    key=lambda item: abs(item[1]),
                    reverse=True,
                )[:16]
            },
            "integration": {
                key: round(value, 4) for key, value in metrics["integration"].items()
            },
            "adaptation": {
                key: round(value, 4) for key, value in metrics["adaptation"].items()
            },
            "direction_profile_30d": {
                key: round(value, 5) for key, value in metrics["direction"].items()
            },
            "consolidation": {
                "last_consolidated_at": self.last_consolidated_at,
                "runs": len(self.consolidation_ledger),
                "recent": [dict(item) for item in self.consolidation_ledger[-6:]],
            },
            "strategy_reinforcement": dict(self.strategy_reinforcement),
            "recent_trajectory": [dict(item) for item in self.trajectory[-12:]],
            "prompt_summary": self.prompt_summary(),
        }


def decay_profile(profile: PersonalityProfile, *, now: float | None = None) -> PersonalityProfile:
    """Decay trung tính: mood về baseline, stable trait về attractor/anchor cực chậm."""

    current_time = float(now if now is not None else time.time())
    previous = float(profile.last_decay_at or profile.last_updated_at or current_time)
    elapsed = max(0.0, current_time - previous)
    if elapsed <= 0.0:
        return profile

    mood = dict(profile.mood)
    for key, baseline in profile.mood_baseline.items():
        half_life = max(60.0, float(MOOD_HALF_LIFE_SECONDS.get(key, 6 * 3600)))
        factor = math.pow(0.5, elapsed / half_life)
        current = float(mood.get(key, baseline) or baseline)
        mood[key] = clamp01(float(baseline) + (current - float(baseline)) * factor)

    days = elapsed / 86400.0
    core = dict(profile.core)
    for key, anchor in profile.core_anchor.items():
        current = float(core.get(key, anchor) or anchor)
        core[key] = clamp01(current + (float(anchor) - current) * 0.0005 * days)

    temperament = dict(profile.temperament)
    for key, attractor in profile.temperament_attractor.items():
        current = float(temperament.get(key, attractor) or attractor)
        temperament[key] = clamp01(current + (float(attractor) - current) * 0.004 * days)

    return PersonalityProfile.from_mapping(
        {
            **profile.to_dict(),
            "core": core,
            "temperament": temperament,
            "mood": mood,
            "last_decay_at": current_time,
        }
    )


__all__ = [
    "ConsequenceAssessment",
    "DEFAULT_CORE",
    "DEFAULT_MOOD",
    "DEFAULT_TEMPERAMENT",
    "MOOD_HALF_LIFE_SECONDS",
    "NarrativeState",
    "PERSONALITY_SCHEMA",
    "PRIMARY_RELATIONSHIP_ID",
    "PROMPT_TRAIT_PHRASES",
    "PersonalityProfile",
    "RELATIONSHIP_PROFILE_TRAITS",
    "RELATIONAL_VULNERABILITY_TRAITS",
    "RelationshipPersonalityProfile",
    "SelfInterpretation",
    "TraitSignal",
    "clamp01",
    "compact",
    "decay_profile",
    "signed01",
]
