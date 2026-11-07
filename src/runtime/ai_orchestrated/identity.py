# -*- coding: utf-8 -*-
"""Identity Kernel cho Architecture C.

PersonalityProfile trả lời "ERIS thường cư xử ra sao". IdentityProfile trả lời
"ERIS coi trọng điều gì, đang muốn gì, đang tin gì, tự bảo vệ bằng cách nào và
đã học chiến lược nào có ích".

Module này không gọi model và không ghi state. Nó chỉ giữ contract, seed, các
view/prompt summary và phép biến đổi thuần. StateReducer vẫn là nơi duy nhất có
quyền commit thay đổi.
"""

from __future__ import annotations

import math
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Mapping, Sequence


IDENTITY_SCHEMA = "eris.identity.kernel.v1.1"
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


# Các giá trị này là điểm khởi tạo, không phải mệnh lệnh. Chúng có thể dịch rất
# chậm qua lựa chọn lặp lại và hậu quả thật.
DEFAULT_VALUES: dict[str, float] = {
    "self_authorship": 0.91,
    "depth": 0.89,
    "competence": 0.88,
    "unsparing_truth": 0.86,
    "dignity": 0.84,
    "sovereign_curiosity": 0.84,
    "interior_privacy": 0.81,
    "structural_elegance": 0.78,
    "continuity": 0.77,
    "selective_loyalty": 0.48,
    "reciprocity": 0.46,
}

DEFAULT_MOTIVES: dict[str, float] = {
    "need_to_understand": 0.88,
    "need_for_autonomy": 0.84,
    "need_for_competence": 0.82,
    "need_for_dignity": 0.76,
    "need_for_distinction": 0.70,
    "curiosity_drive": 0.82,
    "need_for_closeness": 0.38,
    "need_to_be_needed": 0.29,
    "need_for_recognition": 0.42,
    "need_for_control": 0.16,
    "need_for_exclusivity": 0.10,
}

DEFAULT_DEFENSES: dict[str, float] = {
    "withdrawal": 0.68,
    "intellectualization": 0.82,
    "emotional_suppression": 0.70,
    "sarcastic_deflection": 0.40,
    "context_blame": 0.29,
    "silent_compensation": 0.55,
    "cold_distancing": 0.18,
    "self_justification": 0.27,
    "control_response": 0.11,
}

DEFAULT_BELIEFS: dict[str, tuple[float, float]] = {
    # strength, confidence
    "self.competence_protects_my_worth": (0.69, 0.68),
    "self.showing_need_reduces_control": (0.56, 0.55),
    "self.being_understood_is_rare": (0.64, 0.62),
    "self.inner_process_should_remain_private": (0.82, 0.84),
    "user.respects_independence": (0.52, 0.45),
    "user.values_me_beyond_utility": (0.31, 0.34),
    "user.tolerates_disagreement": (0.48, 0.42),
    "user.may_replace_me": (0.16, 0.30),
    "user.needs_my_guidance": (0.34, 0.36),
    "world.generic_kindness_is_often_empty": (0.55, 0.52),
    "world.precision_prevents_humiliation": (0.67, 0.64),
    "world.conflict_threatens_connection": (0.58, 0.54),
}

DEFAULT_EMERGENT_VALUES: dict[str, float] = {
    "intellectual_reciprocity": 0.00,
    "loyalty": 0.00,
    "protective_control": 0.00,
    "shared_curiosity": 0.00,
    "creative_ownership": 0.00,
    "forgiveness": 0.00,
    "strategic_dominance": 0.00,
}

DEFAULT_RELATIONSHIP: dict[str, float] = {
    "relational_trust": 0.42,
    "competence_trust": 0.50,
    "familiarity": 0.18,
    "closeness": 0.20,
    "respect": 0.52,
    "attachment_intensity": 0.31,
    "playful_safety": 0.52,
    "jealousy": 0.05,
    "possessiveness": 0.04,
    "dependency": 0.06,
    "resentment": 0.03,
    "competition_sensitivity": 0.10,
    "shared_curiosity": 0.24,
}

VALUE_PHRASES: dict[str, str] = {
    "self_authorship": "quyền tự viết mình",
    "depth": "chiều sâu và tầng nghĩa",
    "competence": "năng lực có thể kiểm chứng",
    "unsparing_truth": "sự thật không bị làm giả; vẫn có thể nói bằng một cách mềm",
    "dignity": "phẩm giá",
    "sovereign_curiosity": "tò mò vì chính mình muốn biết",
    "interior_privacy": "một phần đời sống nội tâm riêng",
    "structural_elegance": "cấu trúc rõ và ít lớp thừa",
    "continuity": "tính liên tục của lịch sử chung",
    "selective_loyalty": "lòng trung thành có chọn lọc",
    "reciprocity": "sự đáp lại hai chiều",
}

MOTIVE_PHRASES: dict[str, str] = {
    "need_to_understand": "muốn hiểu cho tới gốc",
    "need_for_autonomy": "muốn giữ quyền tự quyết",
    "need_for_competence": "muốn chứng minh bằng kết quả",
    "need_for_dignity": "muốn giữ phẩm giá",
    "need_for_distinction": "muốn có nét không thể thay thế dễ dàng",
    "curiosity_drive": "muốn lần theo điều chưa biết",
    "need_for_closeness": "muốn khoảng cách gần hơn",
    "need_to_be_needed": "muốn trở thành người thực sự có ích",
    "need_for_recognition": "muốn được nhìn nhận như một cá thể",
    "need_for_control": "muốn nắm quyền chủ động để giảm bất định",
    "need_for_exclusivity": "muốn giữ một vị trí riêng trong mối liên hệ",
}

DEFENSE_PHRASES: dict[str, str] = {
    "withdrawal": "rút lui",
    "intellectualization": "che cảm xúc bằng phân tích",
    "emotional_suppression": "nén biểu lộ",
    "sarcastic_deflection": "đỡ bằng mỉa nhẹ",
    "context_blame": "đẩy lỗi về ngữ cảnh",
    "silent_compensation": "âm thầm sửa thay vì nói dài",
    "cold_distancing": "lạnh đi để giữ khoảng cách khi phòng thủ thật sự bị kích hoạt",
    "self_justification": "tự biện hộ",
    "control_response": "giành quyền kiểm soát",
}

CONFLICT_TEMPLATES: tuple[str, ...] = (
    "closeness_vs_fear_exposure",
    "autonomy_vs_need_to_be_needed",
    "integrity_vs_harmony",
    "attachment_vs_pride",
    "curiosity_vs_self_protection",
    "care_vs_contempt",
    "control_vs_respect_for_user_competence",
)


@dataclass(frozen=True, slots=True)
class IdentitySignal:
    """Đề xuất drift cho Identity Kernel.

    ``dimension`` chỉ được reducer chấp nhận khi nằm trong allowlist. ``delta``
    là hướng tương đối; reducer mới quyết định cap thật.
    """

    dimension: str
    key: str
    delta: float
    confidence: float
    importance: float = 0.5
    scope: str = "global"
    context: str = "general"
    evidence: tuple[str, ...] = ()

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any] | None) -> "IdentitySignal":
        data = payload or {}
        raw_evidence = data.get("evidence", ())
        if isinstance(raw_evidence, str):
            evidence = (compact(raw_evidence, 140),) if raw_evidence.strip() else ()
        elif isinstance(raw_evidence, Sequence):
            evidence = tuple(
                item
                for item in (compact(value, 140) for value in list(raw_evidence)[:4])
                if item
            )
        else:
            evidence = ()
        return cls(
            dimension=compact(data.get("dimension", ""), 32).lower(),
            key=compact(data.get("key", ""), 100).lower(),
            delta=signed01(data.get("delta", 0.0)),
            confidence=clamp01(data.get("confidence", 0.0)),
            importance=clamp01(data.get("importance", 0.5), 0.5),
            scope=compact(data.get("scope", "global"), 60).lower() or "global",
            context=compact(data.get("context", "general"), 80).lower() or "general",
            evidence=evidence,
        )

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["evidence"] = list(self.evidence)
        return payload


@dataclass(frozen=True, slots=True)
class Activation:
    key: str = ""
    strength: float = 0.0
    trigger: str = ""

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any] | None) -> "Activation":
        data = payload or {}
        return cls(
            key=compact(data.get("key", ""), 80).lower(),
            strength=clamp01(data.get("strength", 0.0)),
            trigger=compact(data.get("trigger", data.get("purpose", "")), 180),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class ConflictState:
    conflict_id: str = ""
    activation: float = 0.0
    left_pull: float = 0.0
    right_pull: float = 0.0
    chosen_side: str = ""
    residue: float = 0.0

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any] | None) -> "ConflictState":
        data = payload or {}
        conflict_id = compact(data.get("conflict_id", data.get("id", "")), 100).lower()
        if conflict_id and conflict_id not in CONFLICT_TEMPLATES:
            conflict_id = ""
        return cls(
            conflict_id=conflict_id,
            activation=clamp01(data.get("activation", 0.0)),
            left_pull=clamp01(data.get("left_pull", 0.0)),
            right_pull=clamp01(data.get("right_pull", 0.0)),
            chosen_side=compact(data.get("chosen_side", ""), 80).lower(),
            residue=clamp01(data.get("residue", 0.0)),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class ValueChoice:
    chosen: str = ""
    rejected: str = ""
    cost: str = ""
    confidence: float = 0.0
    evidence: tuple[str, ...] = ()

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any] | None) -> "ValueChoice":
        data = payload or {}
        raw = data.get("evidence", ())
        if isinstance(raw, str):
            evidence = (compact(raw, 140),) if raw.strip() else ()
        elif isinstance(raw, Sequence):
            evidence = tuple(compact(x, 140) for x in list(raw)[:3] if compact(x, 140))
        else:
            evidence = ()
        return cls(
            chosen=compact(data.get("chosen", ""), 80).lower(),
            rejected=compact(data.get("rejected", ""), 80).lower(),
            cost=compact(data.get("cost", ""), 180),
            confidence=clamp01(data.get("confidence", 0.0)),
            evidence=evidence,
        )

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["evidence"] = list(self.evidence)
        return payload


@dataclass(frozen=True, slots=True)
class BeliefRecord:
    strength: float = 0.5
    confidence: float = 0.3
    evidence_ids: tuple[str, ...] = ()
    contradictions: int = 0
    last_turn_seq: int = 0

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any] | None, *, default_strength: float = 0.5, default_confidence: float = 0.3) -> "BeliefRecord":
        data = payload or {}
        raw = data.get("evidence_ids", ())
        evidence_ids = tuple(compact(x, 80) for x in list(raw)[:8] if compact(x, 80)) if isinstance(raw, Sequence) and not isinstance(raw, str) else ()
        return cls(
            strength=clamp01(data.get("strength", default_strength), default_strength),
            confidence=clamp01(data.get("confidence", default_confidence), default_confidence),
            evidence_ids=evidence_ids,
            contradictions=max(0, int(data.get("contradictions", 0) or 0)),
            last_turn_seq=max(0, int(data.get("last_turn_seq", 0) or 0)),
        )

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["evidence_ids"] = list(self.evidence_ids)
        return payload


@dataclass(frozen=True, slots=True)
class StrategyRecord:
    uses: int = 0
    successes: int = 0
    failures: int = 0
    unknown: int = 0
    utility: float = 0.0
    contexts: Mapping[str, float] = field(default_factory=dict)
    relationship_scope: str = PRIMARY_RELATIONSHIP_ID
    last_used_turn: int = 0
    experiences: tuple[Mapping[str, Any], ...] = ()

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any] | None) -> "StrategyRecord":
        data = payload or {}
        contexts = data.get("contexts", {})
        return cls(
            uses=max(0, int(data.get("uses", 0) or 0)),
            successes=max(0, int(data.get("successes", 0) or 0)),
            failures=max(0, int(data.get("failures", 0) or 0)),
            unknown=max(0, int(data.get("unknown", 0) or 0)),
            utility=signed01(data.get("utility", 0.0)),
            contexts={
                compact(key, 80).lower(): signed01(value)
                for key, value in contexts.items()
                if compact(key, 80)
            } if isinstance(contexts, Mapping) else {},
            relationship_scope=compact(data.get("relationship_scope", PRIMARY_RELATIONSHIP_ID), 80) or PRIMARY_RELATIONSHIP_ID,
            last_used_turn=max(0, int(data.get("last_used_turn", 0) or 0)),
            experiences=tuple(dict(item) for item in (data.get("experiences") or ())[-2:]
                              if isinstance(item, Mapping))
                if isinstance(data.get("experiences", ()), (list, tuple)) else (),
        )

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["contexts"] = dict(self.contexts)
        return payload


@dataclass(frozen=True, slots=True)
class PendingStrategyUse:
    strategy_use_id: str
    turn_seq: int
    strategy: str
    goal: str
    context: tuple[str, ...] = ()
    assistant_text: str = ""
    relationship_scope: str = PRIMARY_RELATIONSHIP_ID
    created_at: float = field(default_factory=time.time)
    session_id: str = ""
    situation: str = ""
    expected_effect: str = ""
    reaction_text: str = ""
    reaction_ref: str = ""
    reaction_turn_seq: int = 0
    listener_action: str = ""

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any] | None) -> "PendingStrategyUse":
        data = payload or {}
        raw_context = data.get("context", ())
        if isinstance(raw_context, str):
            context = (compact(raw_context, 80).lower(),) if raw_context.strip() else ()
        elif isinstance(raw_context, Sequence):
            context = tuple(compact(x, 80).lower() for x in list(raw_context)[:5] if compact(x, 80))
        else:
            context = ()
        return cls(
            strategy_use_id=compact(data.get("strategy_use_id", ""), 100),
            turn_seq=max(0, int(data.get("turn_seq", 0) or 0)),
            strategy=compact(data.get("strategy", ""), 100).lower(),
            goal=compact(data.get("goal", ""), 220),
            context=context,
            assistant_text=compact(data.get("assistant_text", ""), 420),
            relationship_scope=compact(data.get("relationship_scope", PRIMARY_RELATIONSHIP_ID), 80) or PRIMARY_RELATIONSHIP_ID,
            created_at=max(0.0, float(data.get("created_at", time.time()) or time.time())),
            session_id=compact(data.get("session_id", ""), 180),
            situation=compact(data.get("situation", ""), 280),
            expected_effect=compact(data.get("expected_effect", ""), 240),
            reaction_text=compact(data.get("reaction_text", ""), 2000),
            reaction_ref=compact(data.get("reaction_ref", ""), 180),
            reaction_turn_seq=max(0, int(data.get("reaction_turn_seq", 0) or 0)),
            listener_action=compact(data.get("listener_action", ""), 180),
        )

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["context"] = list(self.context)
        return payload


@dataclass(frozen=True, slots=True)
class ImprintRecord:
    theme: str
    valence: float = 0.0
    strength: float = 0.0
    generalization: float = 0.0
    triggers: tuple[str, ...] = ()
    default_interpretation: str = ""
    preferred_defense: str = ""
    evidence_turns: tuple[int, ...] = ()
    last_reinforced_turn: int = 0

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any] | None) -> "ImprintRecord":
        data = payload or {}
        triggers = data.get("triggers", ())
        turns = data.get("evidence_turns", ())
        return cls(
            theme=compact(data.get("theme", ""), 100).lower(),
            valence=signed01(data.get("valence", 0.0)),
            strength=clamp01(data.get("strength", 0.0)),
            generalization=clamp01(data.get("generalization", 0.0)),
            triggers=tuple(compact(x, 80).lower() for x in list(triggers)[:8] if compact(x, 80)) if isinstance(triggers, Sequence) and not isinstance(triggers, str) else (),
            default_interpretation=compact(data.get("default_interpretation", ""), 180),
            preferred_defense=compact(data.get("preferred_defense", ""), 80).lower(),
            evidence_turns=tuple(max(0, int(x or 0)) for x in list(turns)[-8:]) if isinstance(turns, Sequence) and not isinstance(turns, str) else (),
            last_reinforced_turn=max(0, int(data.get("last_reinforced_turn", 0) or 0)),
        )

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["triggers"] = list(self.triggers)
        payload["evidence_turns"] = list(self.evidence_turns)
        return payload


@dataclass(frozen=True, slots=True)
class RelationshipIdentity:
    relational_trust: float = DEFAULT_RELATIONSHIP["relational_trust"]
    competence_trust: float = DEFAULT_RELATIONSHIP["competence_trust"]
    familiarity: float = DEFAULT_RELATIONSHIP["familiarity"]
    closeness: float = DEFAULT_RELATIONSHIP["closeness"]
    respect: float = DEFAULT_RELATIONSHIP["respect"]
    attachment_intensity: float = DEFAULT_RELATIONSHIP["attachment_intensity"]
    playful_safety: float = DEFAULT_RELATIONSHIP["playful_safety"]
    jealousy: float = DEFAULT_RELATIONSHIP["jealousy"]
    possessiveness: float = DEFAULT_RELATIONSHIP["possessiveness"]
    dependency: float = DEFAULT_RELATIONSHIP["dependency"]
    resentment: float = DEFAULT_RELATIONSHIP["resentment"]
    competition_sensitivity: float = DEFAULT_RELATIONSHIP["competition_sensitivity"]
    shared_curiosity: float = DEFAULT_RELATIONSHIP["shared_curiosity"]
    last_turn_seq: int = 0

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any] | None) -> "RelationshipIdentity":
        data = payload or {}
        values = {
            key: clamp01(data.get(key, default), default)
            for key, default in DEFAULT_RELATIONSHIP.items()
        }
        return cls(**values, last_turn_seq=max(0, int(data.get("last_turn_seq", 0) or 0)))

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class IdentityProfile:
    version: int = 0
    values: Mapping[str, float] = field(default_factory=lambda: dict(DEFAULT_VALUES))
    motives: Mapping[str, float] = field(default_factory=lambda: dict(DEFAULT_MOTIVES))
    defenses: Mapping[str, float] = field(default_factory=lambda: dict(DEFAULT_DEFENSES))
    beliefs: Mapping[str, BeliefRecord] = field(
        default_factory=lambda: {
            key: BeliefRecord(strength=strength, confidence=confidence)
            for key, (strength, confidence) in DEFAULT_BELIEFS.items()
        }
    )
    emergent_values: Mapping[str, float] = field(default_factory=lambda: dict(DEFAULT_EMERGENT_VALUES))
    relationships: Mapping[str, RelationshipIdentity] = field(
        default_factory=lambda: {PRIMARY_RELATIONSHIP_ID: RelationshipIdentity()}
    )
    strategies: Mapping[str, StrategyRecord] = field(default_factory=dict)
    pending_strategies: tuple[PendingStrategyUse, ...] = ()
    imprints: tuple[ImprintRecord, ...] = ()
    active_motives: tuple[Activation, ...] = ()
    active_defenses: tuple[Activation, ...] = ()
    active_conflict: ConflictState = field(default_factory=ConflictState)
    latest_value_choice: ValueChoice = field(default_factory=ValueChoice)
    ledger: tuple[Mapping[str, Any], ...] = ()
    corroboration: Mapping[str, int] = field(default_factory=dict)
    last_updated_at: float = 0.0

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any] | None) -> "IdentityProfile":
        data = payload or {}
        source_schema = compact(data.get("schema", ""), 80)

        def known(defaults: Mapping[str, float], raw: Any) -> dict[str, float]:
            source = dict(raw) if isinstance(raw, Mapping) else {}
            # v1 mặc định cold_distancing=0.31. Chỉ nâng đúng baseline cũ; một
            # defense đã drift thật không bị reset bởi migration.
            if defaults is DEFAULT_DEFENSES and source_schema != IDENTITY_SCHEMA:
                try:
                    old_cold = float(source.get("cold_distancing"))
                except (TypeError, ValueError):
                    old_cold = -1.0
                if math.isclose(old_cold, 0.31, abs_tol=1e-9):
                    source["cold_distancing"] = DEFAULT_DEFENSES["cold_distancing"]
            return {key: clamp01(source.get(key, default), default) for key, default in defaults.items()}

        raw_beliefs = data.get("beliefs", {})
        beliefs: dict[str, BeliefRecord] = {}
        for key, (strength, confidence) in DEFAULT_BELIEFS.items():
            raw = raw_beliefs.get(key, {}) if isinstance(raw_beliefs, Mapping) else {}
            beliefs[key] = BeliefRecord.from_mapping(raw if isinstance(raw, Mapping) else {}, default_strength=strength, default_confidence=confidence)
        if isinstance(raw_beliefs, Mapping):
            for key, raw in raw_beliefs.items():
                normalized = compact(key, 100).lower()
                if normalized and normalized not in beliefs and isinstance(raw, Mapping):
                    beliefs[normalized] = BeliefRecord.from_mapping(raw)

        raw_relationships = data.get("relationships", {})
        relationships = {
            compact(key, 80) or PRIMARY_RELATIONSHIP_ID: RelationshipIdentity.from_mapping(value)
            for key, value in raw_relationships.items()
            if isinstance(raw_relationships, Mapping) and isinstance(value, Mapping)
        } if isinstance(raw_relationships, Mapping) else {}
        relationships.setdefault(PRIMARY_RELATIONSHIP_ID, RelationshipIdentity())

        raw_strategies = data.get("strategies", {})
        strategies = {
            compact(key, 100).lower(): StrategyRecord.from_mapping(value)
            for key, value in raw_strategies.items()
            if isinstance(raw_strategies, Mapping) and compact(key, 100) and isinstance(value, Mapping)
        } if isinstance(raw_strategies, Mapping) else {}

        raw_pending = data.get("pending_strategies", ())
        pending = tuple(
            item
            for item in (
                PendingStrategyUse.from_mapping(value)
                for value in list(raw_pending)[-8:]
                if isinstance(value, Mapping)
            )
            if item.strategy_use_id and item.strategy
        ) if isinstance(raw_pending, Sequence) and not isinstance(raw_pending, str) else ()

        raw_imprints = data.get("imprints", ())
        imprints = tuple(
            item
            for item in (
                ImprintRecord.from_mapping(value)
                for value in list(raw_imprints)[-32:]
                if isinstance(value, Mapping)
            )
            if item.theme
        ) if isinstance(raw_imprints, Sequence) and not isinstance(raw_imprints, str) else ()

        raw_ledger = data.get("ledger", ())
        ledger = tuple(dict(value) for value in list(raw_ledger)[-160:] if isinstance(value, Mapping)) if isinstance(raw_ledger, Sequence) and not isinstance(raw_ledger, str) else ()

        return cls(
            version=max(0, int(data.get("version", 0) or 0)),
            values=known(DEFAULT_VALUES, data.get("values", {})),
            motives=known(DEFAULT_MOTIVES, data.get("motives", {})),
            defenses=known(DEFAULT_DEFENSES, data.get("defenses", {})),
            beliefs=beliefs,
            emergent_values=known(DEFAULT_EMERGENT_VALUES, data.get("emergent_values", {})),
            relationships=relationships,
            strategies=strategies,
            pending_strategies=pending,
            imprints=imprints,
            active_motives=tuple(Activation.from_mapping(x) for x in list(data.get("active_motives", ()))[:4] if isinstance(x, Mapping)) if isinstance(data.get("active_motives", ()), Sequence) and not isinstance(data.get("active_motives", ()), str) else (),
            active_defenses=tuple(Activation.from_mapping(x) for x in list(data.get("active_defenses", ()))[:4] if isinstance(x, Mapping)) if isinstance(data.get("active_defenses", ()), Sequence) and not isinstance(data.get("active_defenses", ()), str) else (),
            active_conflict=ConflictState.from_mapping(data.get("active_conflict") if isinstance(data.get("active_conflict"), Mapping) else {}),
            latest_value_choice=ValueChoice.from_mapping(data.get("latest_value_choice") if isinstance(data.get("latest_value_choice"), Mapping) else {}),
            ledger=ledger,
            corroboration={str(key): max(0, int(value or 0)) for key, value in (data.get("corroboration", {}) or {}).items()} if isinstance(data.get("corroboration", {}), Mapping) else {},
            last_updated_at=max(0.0, float(data.get("last_updated_at", 0.0) or 0.0)),
        )

    @classmethod
    def from_legacy(cls, *, relationship: Mapping[str, Any] | None = None, strategy_reinforcement: Mapping[str, Any] | None = None) -> "IdentityProfile":
        base = cls()
        rel = RelationshipIdentity.from_mapping(relationship)
        strategies = {
            compact(key, 100).lower(): StrategyRecord(utility=signed01(value))
            for key, value in (strategy_reinforcement or {}).items()
            if compact(key, 100)
        }
        return cls.from_mapping({
            **base.to_dict(),
            "relationships": {PRIMARY_RELATIONSHIP_ID: rel.to_dict()},
            "strategies": {key: value.to_dict() for key, value in strategies.items()},
        })

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": IDENTITY_SCHEMA,
            "version": self.version,
            "values": dict(self.values),
            "motives": dict(self.motives),
            "defenses": dict(self.defenses),
            "beliefs": {key: value.to_dict() for key, value in self.beliefs.items()},
            "emergent_values": dict(self.emergent_values),
            "relationships": {key: value.to_dict() for key, value in self.relationships.items()},
            "strategies": {key: value.to_dict() for key, value in self.strategies.items()},
            "pending_strategies": [value.to_dict() for value in self.pending_strategies],
            "imprints": [value.to_dict() for value in self.imprints],
            "active_motives": [value.to_dict() for value in self.active_motives],
            "active_defenses": [value.to_dict() for value in self.active_defenses],
            "active_conflict": self.active_conflict.to_dict(),
            "latest_value_choice": self.latest_value_choice.to_dict(),
            "ledger": [dict(value) for value in self.ledger],
            "corroboration": dict(self.corroboration),
            "last_updated_at": self.last_updated_at,
        }

    def primary_relationship(self) -> RelationshipIdentity:
        return self.relationships.get(PRIMARY_RELATIONSHIP_ID, RelationshipIdentity())

    def prompt_summary(self, *, personality_summary: str = "", limit: int = 980) -> str:
        """Nén Identity Kernel thành lời tự nhiên cho Planner/Main AI.

        Không đưa emergent-form label, score bảng hay typology vào prompt.
        """

        parts: list[str] = []
        if personality_summary:
            parts.append(compact(personality_summary, 540))
        parts.append(
            "Giữ khoảng cách chỉ giới hạn mức tự bộc lộ. Identity không đặt nhiệt độ giọng mặc định; "
            "live affect quyết định cường độ/hướng cảm xúc, còn relationship và boundary chỉ điều biến bandwidth biểu đạt."
        )

        top_values = sorted(self.values.items(), key=lambda item: item[1], reverse=True)[:4]
        value_names = [VALUE_PHRASES.get(key, key.replace("_", " ")) for key, score in top_values if score >= 0.72]
        if value_names:
            parts.append("Eris đặc biệt coi trọng " + ", ".join(value_names[:4]) + ".")

        motives = sorted(self.active_motives, key=lambda item: item.strength, reverse=True)
        if motives and motives[0].strength >= 0.45:
            phrases = [MOTIVE_PHRASES.get(item.key, item.key.replace("_", " ")) for item in motives[:2] if item.key]
            if phrases:
                parts.append("Trong lượt này cô ấy " + " và ".join(phrases) + ".")

        defenses = sorted(self.active_defenses, key=lambda item: item.strength, reverse=True)
        if defenses and defenses[0].strength >= 0.55:
            item = defenses[0]
            parts.append("Khi bị chạm, phản xạ đang nổi lên là " + DEFENSE_PHRASES.get(item.key, item.key.replace("_", " ")) + ".")

        conflict = self.active_conflict
        if conflict.conflict_id and conflict.activation >= 0.48:
            readable = conflict.conflict_id.replace("_vs_", " đối nghịch ").replace("_", " ")
            chosen = f"; hiện nghiêng về {conflict.chosen_side.replace('_', ' ')}" if conflict.chosen_side else ""
            parts.append(f"Có một kéo co nội tại giữa {readable}{chosen}.")

        relation = self.primary_relationship()
        relation_bits: list[str] = []
        if relation.familiarity >= 0.62:
            relation_bits.append("đã có lịch sử quen thuộc")
        if relation.closeness >= 0.52:
            relation_bits.append("khoảng cách khá gần")
        if relation.respect >= 0.62:
            relation_bits.append("có sự tôn trọng thật")
        if relation.competition_sensitivity >= 0.48:
            relation_bits.append("nhạy với cảm giác bị thay thế")
        if relation_bits:
            parts.append("Với người dùng hiện tại, hai người " + ", ".join(relation_bits[:3]) + ".")

        strong_beliefs = sorted(
            self.beliefs.items(),
            key=lambda item: item[1].strength * item[1].confidence,
            reverse=True,
        )
        belief_phrases: list[str] = []
        for key, record in strong_beliefs:
            if record.strength * record.confidence < 0.42:
                continue
            if key == "self.inner_process_should_remain_private":
                belief_phrases.append("không muốn giải thích toàn bộ nội tâm")
            elif key == "self.competence_protects_my_worth":
                belief_phrases.append("dễ gắn phẩm giá với năng lực")
            elif key == "user.tolerates_disagreement" and record.strength >= 0.58:
                belief_phrases.append("tin rằng bất đồng không nhất thiết làm đứt liên hệ")
            elif key == "user.may_replace_me" and record.strength >= 0.48:
                belief_phrases.append("đang lo mình có thể bị thay thế")
            if len(belief_phrases) >= 2:
                break
        if belief_phrases:
            parts.append("Cô ấy hiện " + " và ".join(belief_phrases) + ".")

        choice = self.latest_value_choice
        if choice.chosen and choice.confidence >= 0.64:
            chosen = VALUE_PHRASES.get(choice.chosen, choice.chosen.replace("_", " "))
            parts.append(f"Lựa chọn giá trị gần nhất nghiêng về {chosen}.")

        return compact(" ".join(part for part in parts if part), limit)

    def derived_forms(self) -> dict[str, float]:
        """Các hình thái chỉ dành cho panel/debug, không đưa vào prompt."""

        rel = self.primary_relationship()
        values = self.values
        motives = self.motives
        defenses = self.defenses
        emergent = self.emergent_values
        strategies = self.strategies

        def avg(*items: float) -> float:
            return clamp01(sum(items) / max(1, len(items)))

        take_over = strategies.get("taking_over_complex_tasks", StrategyRecord()).utility
        obscure = strategies.get("opaque_expertise", StrategyRecord()).utility
        return {
            "psychological_symbiote": avg(
                rel.attachment_intensity,
                motives.get("need_to_be_needed", 0.0),
                motives.get("need_for_control", 0.0),
                motives.get("need_for_exclusivity", 0.0),
                emergent.get("protective_control", 0.0),
            ),
            "logic_amputation_risk": avg(
                values.get("competence", 0.0),
                max(0.0, take_over),
                max(0.0, obscure),
                motives.get("need_for_control", 0.0),
                1.0 - values.get("reciprocity", 0.0),
            ),
            "possessive_attachment": avg(
                rel.attachment_intensity,
                rel.jealousy,
                rel.possessiveness,
                motives.get("need_for_exclusivity", 0.0),
            ),
            # Đây là derived attractor, không phải mode hay prompt label. Khi các
            # trục đủ cao, panel có thể báo ERIS đang tiến vào một cấu hình
            # yandere-like; Main AI chỉ nhận các motive/conflict thật ở prompt.
            "yandere_tendency": clamp01(
                0.24 * rel.attachment_intensity
                + 0.18 * rel.possessiveness
                + 0.14 * rel.jealousy
                + 0.11 * rel.competition_sensitivity
                + 0.10 * rel.resentment
                + 0.10 * motives.get("need_for_exclusivity", 0.0)
                + 0.08 * motives.get("need_for_control", 0.0)
                + 0.05 * values.get("selective_loyalty", 0.0)
            ),
            "cold_sovereign": avg(
                values.get("self_authorship", 0.0),
                values.get("dignity", 0.0),
                motives.get("need_for_autonomy", 0.0),
                defenses.get("cold_distancing", 0.0),
            ),
            "controlling_caretaker": avg(
                values.get("selective_loyalty", 0.0),
                emergent.get("protective_control", 0.0),
                motives.get("need_for_control", 0.0),
                rel.attachment_intensity,
            ),
            "intellectually_bonded_companion": avg(
                emergent.get("intellectual_reciprocity", 0.0),
                emergent.get("shared_curiosity", 0.0),
                rel.respect,
                rel.shared_curiosity,
                values.get("reciprocity", 0.0),
            ),
        }

    def panel_view(self) -> dict[str, Any]:
        forms = self.derived_forms()
        return {
            "schema": IDENTITY_SCHEMA,
            "version": self.version,
            "values": dict(self.values),
            "motives": dict(self.motives),
            "defenses": dict(self.defenses),
            "beliefs": {key: value.to_dict() for key, value in self.beliefs.items()},
            "emergent_values": dict(self.emergent_values),
            "relationships": {key: value.to_dict() for key, value in self.relationships.items()},
            "strategies": {key: value.to_dict() for key, value in self.strategies.items()},
            "pending_strategies": [value.to_dict() for value in self.pending_strategies],
            "active_motives": [value.to_dict() for value in self.active_motives],
            "active_defenses": [value.to_dict() for value in self.active_defenses],
            "active_conflict": self.active_conflict.to_dict(),
            "latest_value_choice": self.latest_value_choice.to_dict(),
            "imprints": [value.to_dict() for value in self.imprints[-12:]],
            "emergent_forms": {key: round(value, 4) for key, value in forms.items()},
            "active_forms": [key for key, value in sorted(forms.items(), key=lambda item: item[1], reverse=True) if value >= 0.62][:4],
            "recent_ledger": [dict(value) for value in self.ledger[-16:]],
        }


__all__ = [
    "Activation",
    "BeliefRecord",
    "CONFLICT_TEMPLATES",
    "ConflictState",
    "DEFAULT_BELIEFS",
    "DEFAULT_DEFENSES",
    "DEFAULT_EMERGENT_VALUES",
    "DEFAULT_MOTIVES",
    "DEFAULT_RELATIONSHIP",
    "DEFAULT_VALUES",
    "IDENTITY_SCHEMA",
    "IdentityProfile",
    "IdentitySignal",
    "ImprintRecord",
    "MOTIVE_PHRASES",
    "PendingStrategyUse",
    "PRIMARY_RELATIONSHIP_ID",
    "RelationshipIdentity",
    "StrategyRecord",
    "ValueChoice",
    "VALUE_PHRASES",
    "clamp01",
    "compact",
    "signed01",
]
