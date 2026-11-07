# -*- coding: utf-8 -*-
"""Random-chat seed generation state for autonomous ERIS speech.

A seed is not public wording. It is a small, typed premise that gives Surface a
real reason to speak. The state store deliberately keeps three things separate:

* user affinity: what the user tends to welcome;
* ERIS interest: what ERIS still wants to share;
* strategy fit: whether a particular opening/mode worked at that moment.

A weak or negative user response may reduce user/strategy fit, but it must not
erase ERIS's own interest. Otherwise "personality" quietly degenerates into an
engagement optimizer, which is a rather elaborate way to build a waiter.
"""
from __future__ import annotations

import hashlib
import re
import threading
import time
from dataclasses import asdict, dataclass, field, replace
from typing import Any, Callable, Mapping, Sequence

from cortex.mental_material import MATERIAL_KINDS, MATERIAL_TO_EPISTEMIC, MATERIAL_TO_EVENT_KIND


RANDOM_CHAT_SEED_SCHEMA = "eris.random_chat_seed.v1"

_ALLOWED_MODES = {"user_led", "shared", "eris_led", "experimental"}
_ALLOWED_SOURCES = {
    "user_memory",
    "recent_shared_context",
    "eris_self_memory",
    "weak_association",
    # Nguồn mới vẫn do đúng một seed worker tạo; đây chỉ là provenance semantic.
    "private_life_workspace",
    "endogenous_curiosity",
    "general_knowledge",
    "world_hypothesis",
    "discovery_impulse",
    "play_impulse",
    "question_impulse",
}
_ALLOWED_MOTIVES = {
    "want_to_share",
    "want_to_express",
    "want_to_test",
    "want_to_connect",
    "want_to_amuse",
    "want_to_know",
    "want_to_explore",
    "want_to_play",
    "want_to_revisit",
    "want_to_create",
}

_ALLOWED_EVENT_KINDS = {
    "thought", "curiosity_question", "knowledge_gap", "discovery_impulse",
    "speculation", "imagination", "opinion", "association", "memory_callback",
    "playful_probe", "micro_challenge", "unfinished_loop", "creative_impulse",
    "self_reflection", "prediction", "surprise", "contradiction_notice",
}

# Event kinds are deliberately grouped by *mental function*.  Reservoir diversity
# should not be fooled by four different labels that all realize as reflective
# prose.  Families are also used by the quality scorer so a compact question or
# playful probe is not penalized for failing to look like an essay.
_EVENT_FAMILY_BY_KIND = {
    "thought": "cognitive",
    "opinion": "cognitive",
    "surprise": "cognitive",
    "contradiction_notice": "cognitive",
    "curiosity_question": "curiosity",
    "knowledge_gap": "curiosity",
    "discovery_impulse": "curiosity",
    "association": "associative",
    "memory_callback": "associative",
    "unfinished_loop": "associative",
    "playful_probe": "play",
    "micro_challenge": "play",
    "imagination": "creative",
    "creative_impulse": "creative",
    "speculation": "uncertain_world",
    "prediction": "uncertain_world",
    "self_reflection": "self",
}


def seed_event_family(event_kind: str) -> str:
    """Return the stable mental-event family for a seed kind."""

    return _EVENT_FAMILY_BY_KIND.get(str(event_kind or "").strip().lower(), "cognitive")
_ALLOWED_EPISTEMIC_MODES = {
    "grounded_fact", "bounded_inference", "speculation", "imagination",
    "opinion", "question", "research_intent",
}

_GENERIC_PATTERNS = (
    "mình vẫn ở đây",
    "tôi vẫn ở đây",
    "i am still here",
    "i'm still here",
    "cần gì cứ nói",
    "muốn nói gì",
    "what do you want to talk about",
    "supply a topic",
    "fill the silence",
    "lấp im lặng",
)

_POSITIVE_EXPLICIT = re.compile(
    r"(?:\bthích\b.*(?:kiểu|cách|eris|nói|chia sẻ)|"
    r"(?:hay|được|ổn|đúng gu)\s+(?:đó|á|nha|rồi)|"
    r"cứ\s+(?:nói|chia sẻ|làm)\s+(?:vậy|kiểu đó)|"
    r"nói\s+kiểu\s+này|"
    r"(?:i\s+like|love\s+this|keep\s+doing|more\s+like\s+this))",
    re.IGNORECASE,
)
_NEGATIVE_EXPLICIT = re.compile(
    r"(?:\bđừng\b|không\s+thích|phiền(?:\s+quá)?|im\s+đi|thôi\s+đừng|"
    r"khỏi\s+(?:nói|chia sẻ)|stop\b|don't\b|do\s+not\b)",
    re.IGNORECASE,
)
_LAUGHTER = re.compile(r"(?:ha){2,}|(?:hi){2,}|\bkk+k*\b|: ?\)\)+|= ?\)\)+|😂|🤣", re.IGNORECASE)


def _compact(value: Any, limit: int = 320) -> str:
    text = " ".join(str(value or "").split()).strip()
    return text if len(text) <= limit else text[: max(0, limit - 1)].rstrip() + "…"


def _clamp01(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = default
    if number != number:
        number = default
    return max(0.0, min(1.0, number))


def _sequence(value: Any) -> Sequence[Any]:
    return value if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)) else ()


def _clean_tag(value: Any) -> str:
    text = _compact(value, 64).lower().replace("-", "_").replace(" ", "_")
    text = re.sub(r"[^a-z0-9_\u00c0-\u024f\u1e00-\u1eff]", "", text)
    return text.strip("_")[:48]


def _token_set(text: str) -> set[str]:
    return {
        token
        for token in re.findall(r"[\w\u00c0-\u024f\u1e00-\u1eff]+", text.lower())
        if len(token) >= 3
    }


def _similarity(left: str, right: str) -> float:
    a, b = _token_set(left), _token_set(right)
    if not a or not b:
        return 0.0
    return len(a & b) / max(1, len(a | b))


def _seed_id(premise: str, mode: str) -> str:
    payload = f"{mode}|{premise}|{time.time_ns()}"
    return "rseed_" + hashlib.sha256(payload.encode("utf-8", errors="ignore")).hexdigest()[:18]


@dataclass(frozen=True, slots=True)
class RandomChatSeed:
    decision: str = "none"
    seed_id: str = ""
    mode: str = "shared"
    premise: str = ""
    source_kind: str = "recent_shared_context"
    source_refs: tuple[str, ...] = ()
    topic_tags: tuple[str, ...] = ()
    event_kind: str = "thought"
    epistemic_mode: str = "opinion"
    open_loop: bool = False
    opening_move: str = "share_observation"
    desired_response: tuple[str, ...] = ()
    eris_motive_kind: str = "want_to_share"
    eris_motive_why_now: str = ""
    personal_stake: float = 0.0
    authenticity: float = 0.0
    daring: float = 0.0
    uncertainty: float = 0.0
    intrusion_risk: float = 0.0
    face_risk: float = 0.0
    recovery_available: bool = False
    estimated_fit: float = 0.0
    confidence: float = 0.0
    tone: str = "casual_warm"
    ttl_seconds: float = 90.0
    # V45: production seed chỉ là associative material carrier. Các field dưới
    # được runtime gắn từ CausalMotive có trước, không phải model tự khai sau khi
    # đã nghĩ ra premise. Legacy seed vẫn để material_only=False.
    material_only: bool = False
    material_id: str = ""
    material_kind: str = ""
    cause_id: str = ""
    cause_kind: str = ""
    cause_strength: float = 0.0
    runtime_motive_kind: str = ""
    expected_update: tuple[str, ...] = ()
    # V70: relation semantic của material với active concern cha. Model chỉ đề
    # xuất mode; runtime sẽ chấm delta lại trước khi coi đó là mutation.
    development_mode: str = ""
    development_required: bool = False
    cause_semantic_focus: str = ""
    rumination_return: bool = False

    @property
    def available(self) -> bool:
        return (
            self.decision == "seed"
            and bool(self.premise)
            and bool(self.topic_tags)
            and self.mode in _ALLOWED_MODES
            and self.source_kind in _ALLOWED_SOURCES
            and self.eris_motive_kind in _ALLOWED_MOTIVES
            and self.event_kind in _ALLOWED_EVENT_KINDS
            and self.epistemic_mode in _ALLOWED_EPISTEMIC_MODES
        )

    @property
    def generic(self) -> bool:
        lowered = self.premise.lower()
        return any(pattern in lowered for pattern in _GENERIC_PATTERNS)

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["schema"] = RANDOM_CHAT_SEED_SCHEMA
        payload["source_refs"] = list(self.source_refs)
        payload["topic_tags"] = list(self.topic_tags)
        payload["desired_response"] = list(self.desired_response)
        payload["expected_update"] = list(self.expected_update)
        return payload

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any] | None) -> "RandomChatSeed":
        data = payload or {}
        decision = _compact(data.get("decision", "none"), 16).lower() or "none"
        if decision not in {"seed", "none"}:
            decision = "none"
        mode = _compact(data.get("mode", "shared"), 24).lower() or "shared"
        if mode not in _ALLOWED_MODES:
            mode = "shared"
        premise = _compact(data.get("premise", ""), 520)
        source_kind = _compact(data.get("source_kind", "recent_shared_context"), 40).lower()
        if source_kind not in _ALLOWED_SOURCES:
            source_kind = "recent_shared_context"
        motive = data.get("eris_motive") if isinstance(data.get("eris_motive"), Mapping) else {}
        risk = data.get("risk") if isinstance(data.get("risk"), Mapping) else {}
        source_refs = tuple(
            item for item in (_compact(value, 140) for value in _sequence(data.get("source_refs"))[:8]) if item
        )
        tags = tuple(
            dict.fromkeys(
                tag for tag in (_clean_tag(value) for value in _sequence(data.get("topic_tags"))[:8]) if tag
            )
        )
        event_kind = _compact(data.get("event_kind", "thought"), 48).lower() or "thought"
        if event_kind not in _ALLOWED_EVENT_KINDS:
            event_kind = "thought"
        epistemic_mode = _compact(data.get("epistemic_mode", "opinion"), 48).lower() or "opinion"
        if epistemic_mode not in _ALLOWED_EPISTEMIC_MODES:
            epistemic_mode = "opinion"
        desired = tuple(
            item for item in (_compact(value, 64).lower() for value in _sequence(data.get("desired_response"))[:6]) if item
        )
        motive_kind = _compact(motive.get("kind", data.get("eris_motive_kind", "want_to_share")), 40).lower()
        if motive_kind not in _ALLOWED_MOTIVES:
            motive_kind = "want_to_share"
        seed_id = _compact(data.get("seed_id", ""), 80) or (_seed_id(premise, mode) if premise else "")
        expected_update = tuple(
            item for item in (_compact(value, 100) for value in _sequence(data.get("expected_update"))[:8]) if item
        )
        return cls(
            decision=decision,
            seed_id=seed_id,
            mode=mode,
            premise=premise,
            source_kind=source_kind,
            source_refs=source_refs,
            topic_tags=tags,
            event_kind=event_kind,
            epistemic_mode=epistemic_mode,
            open_loop=bool(data.get("open_loop", event_kind in {"curiosity_question", "knowledge_gap", "discovery_impulse", "unfinished_loop"})),
            opening_move=_compact(data.get("opening_move", "share_observation"), 64).lower() or "share_observation",
            desired_response=desired,
            eris_motive_kind=motive_kind,
            eris_motive_why_now=_compact(motive.get("why_now", data.get("eris_motive_why_now", "")), 220),
            personal_stake=_clamp01(motive.get("personal_stake", data.get("personal_stake", 0.0))),
            authenticity=_clamp01(motive.get("authenticity", data.get("authenticity", 0.0))),
            daring=_clamp01(risk.get("daring", data.get("daring", 0.0))),
            uncertainty=_clamp01(risk.get("uncertainty", data.get("uncertainty", 0.0))),
            intrusion_risk=_clamp01(risk.get("intrusion_risk", data.get("intrusion_risk", 0.0))),
            face_risk=_clamp01(risk.get("face_risk", data.get("face_risk", 0.0))),
            recovery_available=bool(risk.get("recovery_available", data.get("recovery_available", False))),
            estimated_fit=_clamp01(data.get("estimated_fit", 0.0)),
            confidence=_clamp01(data.get("confidence", 0.0)),
            tone=_compact(data.get("tone", "casual_warm"), 48).lower() or "casual_warm",
            ttl_seconds=max(60.0, min(1800.0, float(data.get("ttl_seconds", 600.0) or 600.0))),
            material_only=bool(data.get("material_only", False)),
            material_id=_compact(data.get("material_id", seed_id), 100) or seed_id,
            material_kind=_compact(data.get("material_kind", ""), 48).lower(),
            cause_id=_compact(data.get("cause_id", ""), 120),
            cause_kind=_compact(data.get("cause_kind", ""), 64).lower(),
            cause_strength=_clamp01(data.get("cause_strength", 0.0)),
            runtime_motive_kind=_compact(data.get("runtime_motive_kind", ""), 64).lower(),
            expected_update=expected_update,
            development_mode=_compact(data.get("development_mode", ""), 24).lower(),
            development_required=bool(data.get("development_required", False)),
            cause_semantic_focus=_compact(data.get("cause_semantic_focus", ""), 520),
            rumination_return=bool(data.get("rumination_return", False)),
        )

    @classmethod
    def from_material_mapping(
        cls,
        payload: Mapping[str, Any] | None,
        *,
        causal_motive: Mapping[str, Any],
        requested_mode: str = "eris_led",
    ) -> "RandomChatSeed":
        """Dựng carrier V45: model chỉ sở hữu material, runtime sở hữu motive.

        ``opening_move`` và ``desired_response`` cố ý để rỗng. Conversation shape
        được quyết muộn hơn từ material lifecycle, tránh seed vừa sinh đã mặc áo
        một câu public tương lai.
        """

        data = dict(payload or {})
        decision = _compact(data.get("decision", "none"), 16).lower()
        if decision != "seed":
            return cls()
        premise = _compact(data.get("premise", data.get("content", "")), 520)
        material_kind = _compact(data.get("material_kind", "observation"), 48).lower()
        if material_kind not in MATERIAL_KINDS:
            return cls()
        cause_id = _compact(causal_motive.get("cause_id", ""), 120)
        cause_kind = _compact(causal_motive.get("cause_kind", ""), 64).lower()
        runtime_motive_kind = _compact(causal_motive.get("motive_kind", "share_internal_material"), 64).lower()
        if not premise or not cause_id:
            return cls()
        mode = _compact(requested_mode, 24).lower() or "eris_led"
        if mode not in _ALLOWED_MODES:
            mode = "eris_led"
        source_kind = {
            "user_event": "recent_shared_context",
            "conversation_trajectory": "recent_shared_context",
            "active_concern": "endogenous_curiosity",
            "private_thought": "private_life_workspace",
            "memory_context": "user_memory",
            "environment_event": "current_environment",
            "self_outcome": "eris_self_memory",
        }.get(cause_kind, "weak_association")
        # Provenance chỉ lấy từ causal state runtime-owned. Model không được tự
        # cấp source_ref cho premise của chính nó rồi gọi đó là evidence.
        source_refs = tuple(dict.fromkeys(
            item for item in (
                *(_compact(value, 140) for value in _sequence(causal_motive.get("source_refs"))[:8]),
                cause_id,
            ) if item
        ))[:12]
        tags = tuple(dict.fromkeys(
            tag for tag in (_clean_tag(value) for value in _sequence(data.get("topic_tags"))[:8]) if tag
        ))
        if not tags:
            tags = tuple(_clean_tag(x) for x in _token_set(premise) if _clean_tag(x))[:4]
        expected_update = tuple(
            item for item in (_compact(value, 100) for value in _sequence(data.get("expected_update"))[:8]) if item
        )
        event_kind = MATERIAL_TO_EVENT_KIND.get(material_kind, "thought")
        epistemic_mode = _compact(data.get("epistemic_mode", MATERIAL_TO_EPISTEMIC.get(material_kind, "bounded_inference")), 48).lower()
        if epistemic_mode not in _ALLOWED_EPISTEMIC_MODES:
            epistemic_mode = MATERIAL_TO_EPISTEMIC.get(material_kind, "bounded_inference")
        uncertainty = _clamp01(data.get("uncertainty", 0.28))
        confidence = _clamp01(data.get("confidence", 0.68), 0.68)
        cause_strength = _clamp01(causal_motive.get("strength", 0.0))
        legacy_motive = _compact(causal_motive.get("legacy_motive_kind", "want_to_share"), 40).lower()
        if legacy_motive not in _ALLOWED_MOTIVES:
            legacy_motive = "want_to_share"
        seed_id = _compact(data.get("seed_id", ""), 80) or (_seed_id(premise, mode) if premise else "")
        material_id = _compact(data.get("material_id", seed_id), 100) or seed_id
        allowed_dev = {str(x).strip().lower() for x in _sequence(causal_motive.get("allowed_development_modes"))}
        proposed_dev = _compact(data.get("development_mode", ""), 24).lower()
        development_mode = proposed_dev if proposed_dev in allowed_dev else ""
        rumination_return = bool(
            causal_motive.get("cause_kind") == "active_concern"
            and float(causal_motive.get("rumination_attractor", 0.0) or 0.0) >= 0.35
        )
        return cls(
            decision="seed",
            seed_id=seed_id,
            mode=mode,
            premise=premise,
            source_kind=source_kind,
            source_refs=source_refs,
            topic_tags=tags,
            event_kind=event_kind,
            epistemic_mode=epistemic_mode,
            open_loop=bool(data.get("open_loop", material_kind in {"question", "unfinished_thought"})),
            opening_move="",
            desired_response=(),
            eris_motive_kind=legacy_motive,
            eris_motive_why_now=_compact(causal_motive.get("why_now", ""), 220),
            personal_stake=cause_strength,
            authenticity=_clamp01(0.46 + 0.34 * confidence + 0.20 * bool(source_refs)),
            daring=_clamp01(data.get("daring", 0.18), 0.18),
            uncertainty=uncertainty,
            intrusion_risk=_clamp01(data.get("intrusion_risk", 0.12), 0.12),
            face_risk=_clamp01(data.get("face_risk", 0.08), 0.08),
            recovery_available=True,
            estimated_fit=_clamp01(0.38 + 0.44 * cause_strength + 0.12 * confidence),
            confidence=confidence,
            tone="natural",
            ttl_seconds=max(90.0, min(1800.0, float(data.get("ttl_seconds", 600.0) or 600.0))),
            material_only=True,
            material_id=material_id,
            material_kind=material_kind,
            cause_id=cause_id,
            cause_kind=cause_kind,
            cause_strength=cause_strength,
            runtime_motive_kind=runtime_motive_kind,
            expected_update=expected_update,
            development_mode=development_mode,
            development_required=bool(causal_motive.get("development_required", False)),
            cause_semantic_focus=_compact(causal_motive.get("semantic_focus", ""), 520),
            rumination_return=rumination_return,
        )


@dataclass(frozen=True, slots=True)
class SeedQualityAssessment:
    """Deterministic quality signal for a semantic seed.

    This is deliberately not another language-model vote.  It measures whether
    the seed already contains enough semantic substance for Neuro/Surface to use
    without inventing a situation around it.
    """

    score: float
    specificity: float
    motive_strength: float
    semantic_completeness: float
    epistemic_integrity: float
    reasons: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "score": round(self.score, 4),
            "specificity": round(self.specificity, 4),
            "motive_strength": round(self.motive_strength, 4),
            "semantic_completeness": round(self.semantic_completeness, 4),
            "epistemic_integrity": round(self.epistemic_integrity, 4),
            "reasons": list(self.reasons),
        }


def assess_seed_quality(seed: RandomChatSeed) -> SeedQualityAssessment:
    """Score a seed by the contract of its *mental event kind*.

    V32 accidentally rewarded prose length, rich ``why_now`` text and reflective
    framing for every seed.  The model did exactly what that objective asked and
    the reservoir drifted toward mini-essays.  V33 instead asks whether a seed is
    a good instance of its own kind: a curiosity question may be short, a memory
    callback needs provenance, a playful probe needs an actual playable move, and
    only reflective/cognitive kinds benefit from extra semantic depth.

    This function still does not decide whether ERIS should speak now.  It only
    determines whether the stocked premise is useful enough to become a candidate.
    """

    premise = _compact(seed.premise, 520)
    tokens = _token_set(premise)

    # V45 material-only seed không được chấm theo conversation shape. Không còn
    # thưởng opening_move/desired_response/why_now do model viết. Motive strength
    # ở đây chính là strength của causal source runtime-owned.
    if seed.material_only:
        reasons: list[str] = []
        token_count = len(tokens)
        has_tags = bool(seed.topic_tags)
        has_refs = bool(seed.source_refs)
        kind_ok = seed.material_kind in MATERIAL_KINDS
        premise_core = 1.0 if token_count >= 5 else (0.72 if token_count >= 3 else 0.25 if token_count else 0.0)
        provenance = _clamp01(0.55 * (1.0 if has_refs else 0.0) + 0.45 * seed.cause_strength)
        kind_fit = 1.0 if kind_ok else 0.0
        question_fit = 1.0
        if seed.material_kind == "question":
            question_fit = 1.0 if seed.expected_update else 0.58
            if not seed.expected_update:
                reasons.append("question_without_expected_update")
        epistemic_integrity = 1.0
        if seed.epistemic_mode == "grounded_fact" and not has_refs:
            epistemic_integrity = 0.45
            reasons.append("grounded_fact_without_refs")
        if seed.generic:
            epistemic_integrity = min(epistemic_integrity, 0.35)
            reasons.append("generic_presence")
        specificity = _clamp01(0.48 * premise_core + 0.26 * provenance + 0.16 * (1.0 if has_tags else 0.0) + 0.10 * question_fit)
        motive_strength = _clamp01(seed.cause_strength)
        semantic_completeness = _clamp01(0.38 * premise_core + 0.24 * kind_fit + 0.20 * provenance + 0.18 * question_fit)
        score = _clamp01(
            0.34 * specificity
            + 0.24 * motive_strength
            + 0.26 * semantic_completeness
            + 0.16 * epistemic_integrity
        )
        if seed.generic:
            score = min(score, 0.28)
        if seed.material_kind == "question" and not seed.expected_update:
            # Câu hỏi không có state nào để cập nhật chỉ là conversation filler
            # đội lốt curiosity. Small talk vẫn hợp lệ ở small_contact; question
            # thì phải có gap thật.
            score = min(score, 0.42)
        if not kind_ok:
            score = min(score, 0.30)
            reasons.append("unknown_material_kind")
        return SeedQualityAssessment(
            score=score,
            specificity=specificity,
            motive_strength=motive_strength,
            semantic_completeness=semantic_completeness,
            epistemic_integrity=_clamp01(epistemic_integrity),
            reasons=tuple(dict.fromkeys(reasons)),
        )
    token_count = len(tokens)
    why_now = _compact(seed.eris_motive_why_now, 220)
    why_tokens = _token_set(why_now)
    family = seed_event_family(seed.event_kind)

    has_tags = bool(seed.topic_tags)
    has_two_tags = len(seed.topic_tags) >= 2
    has_refs = bool(seed.source_refs)
    has_why = bool(why_tokens)
    has_response_shape = bool(seed.desired_response)
    has_move = bool(seed.opening_move)
    is_question_mode = seed.epistemic_mode in {"question", "research_intent"}
    is_uncertain_mode = seed.epistemic_mode in {"bounded_inference", "speculation", "imagination"}

    # ``premise_core`` is intentionally coarse.  Passing 90 characters no longer
    # earns anything.  Compact seeds win by being structurally appropriate for
    # their event kind, not by producing more prose.
    premise_core = 1.0 if token_count >= 5 else (0.72 if token_count >= 3 else 0.32 if token_count else 0.0)
    anchor_strength = _clamp01(
        0.38 * (1.0 if has_tags else 0.0)
        + 0.24 * (1.0 if has_two_tags else 0.0)
        + 0.23 * (1.0 if has_refs else 0.0)
        + 0.15 * (1.0 if has_why else 0.0)
    )

    reasons: list[str] = []

    # Kind-specific fit.  These rules are intentionally permissive about length
    # and strict about the *shape* of the mental event.
    if seed.event_kind == "curiosity_question":
        kind_fit = _clamp01(
            0.32 * premise_core
            + 0.30 * (1.0 if is_question_mode else 0.0)
            + 0.20 * (1.0 if seed.open_loop else 0.0)
            + 0.18 * (1.0 if has_tags else 0.0)
        )
        if not is_question_mode:
            reasons.append("question_without_question_epistemics")
    elif seed.event_kind in {"knowledge_gap", "discovery_impulse"}:
        kind_fit = _clamp01(
            0.26 * premise_core
            + 0.28 * (1.0 if seed.open_loop else 0.0)
            + 0.24 * (1.0 if is_question_mode else 0.0)
            + 0.12 * (1.0 if seed.eris_motive_kind in {"want_to_know", "want_to_explore"} else 0.0)
            + 0.10 * (1.0 if has_tags else 0.0)
        )
        if not seed.open_loop:
            reasons.append("discovery_without_open_loop")
    elif seed.event_kind in {"playful_probe", "micro_challenge"}:
        kind_fit = _clamp01(
            0.25 * premise_core
            + 0.24 * (1.0 if has_move else 0.0)
            + 0.23 * (1.0 if has_response_shape else 0.0)
            + 0.18 * (1.0 if seed.eris_motive_kind in {"want_to_play", "want_to_test", "want_to_amuse"} else 0.0)
            + 0.10 * (1.0 if seed.intrusion_risk <= 0.35 else 0.0)
        )
        if not has_response_shape:
            reasons.append("play_without_response_shape")
    elif seed.event_kind == "memory_callback":
        kind_fit = _clamp01(
            0.22 * premise_core
            + 0.42 * (1.0 if has_refs else 0.0)
            + 0.18 * (1.0 if has_tags else 0.0)
            + 0.18 * (1.0 if seed.eris_motive_kind == "want_to_revisit" else 0.0)
        )
        if not has_refs:
            reasons.append("memory_callback_without_ref")
    elif seed.event_kind == "unfinished_loop":
        kind_fit = _clamp01(
            0.24 * premise_core
            + 0.32 * (1.0 if seed.open_loop else 0.0)
            + 0.22 * (1.0 if has_refs else 0.0)
            + 0.12 * (1.0 if has_tags else 0.0)
            + 0.10 * (1.0 if seed.eris_motive_kind in {"want_to_revisit", "want_to_explore", "want_to_know"} else 0.0)
        )
        if not seed.open_loop:
            reasons.append("unfinished_loop_closed")
    elif seed.event_kind == "association":
        kind_fit = _clamp01(
            0.30 * premise_core
            + 0.26 * (1.0 if has_two_tags else 0.0)
            + 0.24 * (1.0 if has_refs else 0.0)
            + 0.20 * (1.0 if has_why else 0.0)
        )
    elif seed.event_kind in {"imagination", "creative_impulse"}:
        kind_fit = _clamp01(
            0.30 * premise_core
            + 0.25 * (1.0 if seed.epistemic_mode == "imagination" else 0.0)
            + 0.20 * (1.0 if seed.eris_motive_kind in {"want_to_create", "want_to_play", "want_to_explore"} else 0.0)
            + 0.15 * (1.0 if has_tags else 0.0)
            + 0.10 * (1.0 if has_move else 0.0)
        )
    elif seed.event_kind in {"speculation", "prediction"}:
        kind_fit = _clamp01(
            0.28 * premise_core
            + 0.28 * (1.0 if is_uncertain_mode else 0.0)
            + 0.18 * min(1.0, seed.uncertainty / 0.35)
            + 0.16 * (1.0 if has_tags else 0.0)
            + 0.10 * (1.0 if has_why else 0.0)
        )
        if not is_uncertain_mode:
            reasons.append("uncertain_event_overstated")
    elif seed.event_kind in {"surprise", "contradiction_notice"}:
        kind_fit = _clamp01(
            0.30 * premise_core
            + 0.28 * (1.0 if has_refs else 0.0)
            + 0.22 * (1.0 if has_why else 0.0)
            + 0.20 * (1.0 if has_tags else 0.0)
        )
    elif seed.event_kind == "self_reflection":
        kind_fit = _clamp01(
            0.32 * premise_core
            + 0.26 * (1.0 if seed.eris_motive_kind in {"want_to_express", "want_to_share"} else 0.0)
            + 0.22 * (1.0 if has_why else 0.0)
            + 0.20 * (1.0 if has_tags else 0.0)
        )
    else:  # thought / opinion and future cognitive additions
        kind_fit = _clamp01(
            0.30 * premise_core
            + 0.28 * anchor_strength
            + 0.22 * (1.0 if has_why or has_refs else 0.0)
            + 0.20 * (1.0 if seed.epistemic_mode in {"opinion", "bounded_inference", "grounded_fact"} else 0.0)
        )

    # ``specificity`` now means event-specific semantic anchoring rather than
    # character count.  Keep the public field name for compatibility.
    specificity = _clamp01(0.72 * kind_fit + 0.28 * anchor_strength)

    # Why-now is useful, but it must not force every seed to write a paragraph.
    # Event coherence and declared stake can carry compact curiosity/play seeds.
    motive_strength = _clamp01(
        0.27 * seed.personal_stake
        + 0.27 * seed.authenticity
        + 0.26 * kind_fit
        + 0.20 * (1.0 if has_why else 0.0)
    )

    semantic_completeness = _clamp01(
        0.28 * kind_fit
        + 0.24 * (1.0 if has_tags else 0.0)
        + 0.16 * (1.0 if has_move else 0.0)
        + 0.16 * (1.0 if seed.event_kind in _ALLOWED_EVENT_KINDS else 0.0)
        + 0.16 * (1.0 if seed.epistemic_mode in _ALLOWED_EPISTEMIC_MODES else 0.0)
    )

    epistemic_integrity = 1.0
    if seed.generic:
        reasons.append("generic_presence")
        epistemic_integrity -= 0.55
    if seed.epistemic_mode == "grounded_fact" and not seed.source_refs:
        reasons.append("grounded_fact_without_refs")
        epistemic_integrity -= 0.45
    if seed.source_kind == "world_hypothesis" and seed.epistemic_mode == "grounded_fact":
        reasons.append("world_hypothesis_promoted_to_fact")
        epistemic_integrity -= 0.45
    if family in {"cognitive", "self"} and token_count <= 5 and not has_refs and not has_why:
        # Keep the old diagnostic for true fortune-cookie fragments, but do not
        # apply it to valid compact questions/play/creative impulses.
        reasons.append("thin_premise")
    if not has_why and seed.personal_stake < 0.28 and family in {"cognitive", "self"}:
        reasons.append("weak_why_now")
    epistemic_integrity = _clamp01(epistemic_integrity)

    score = _clamp01(
        0.35 * specificity
        + 0.25 * motive_strength
        + 0.24 * semantic_completeness
        + 0.16 * epistemic_integrity
    )
    if seed.generic:
        score = min(score, 0.24)

    # Fail only event-shape violations, not brevity itself.
    if seed.event_kind == "memory_callback" and not has_refs:
        score = min(score, 0.40)
    if seed.event_kind in {"knowledge_gap", "discovery_impulse", "unfinished_loop"} and not seed.open_loop:
        score = min(score, 0.42)
    if seed.event_kind == "curiosity_question" and not is_question_mode:
        score = min(score, 0.42)
    if seed.event_kind in {"speculation", "prediction"} and not is_uncertain_mode:
        score = min(score, 0.42)
    if family in {"cognitive", "self"} and token_count <= 5 and not has_refs and not has_why:
        score = min(score, 0.42)

    return SeedQualityAssessment(
        score=score,
        specificity=specificity,
        motive_strength=motive_strength,
        semantic_completeness=semantic_completeness,
        epistemic_integrity=epistemic_integrity,
        reasons=tuple(dict.fromkeys(reasons)),
    )


@dataclass(frozen=True, slots=True)
class SeedOutcome:
    seed_id: str
    classification: str
    score: float
    explicit: bool
    evidence_id: str = ""
    promoted_paths: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class RandomChatSeedState:
    version: int = 0
    affinities: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)
    eris_interests: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)
    recent: tuple[Mapping[str, Any], ...] = ()
    pending: Mapping[str, Any] = field(default_factory=dict)
    promoted: tuple[str, ...] = ()
    positive_streak: int = 0
    negative_streak: int = 0
    updated_at: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": "eris.random_chat_seed_state.v1",
            "version": self.version,
            "affinities": {str(k): dict(v) for k, v in self.affinities.items()},
            "eris_interests": {str(k): dict(v) for k, v in self.eris_interests.items()},
            "recent": [dict(item) for item in self.recent[-80:]],
            "pending": dict(self.pending),
            "promoted": list(self.promoted),
            "positive_streak": self.positive_streak,
            "negative_streak": self.negative_streak,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any] | None) -> "RandomChatSeedState":
        data = payload or {}
        affinities = data.get("affinities") if isinstance(data.get("affinities"), Mapping) else {}
        interests = data.get("eris_interests") if isinstance(data.get("eris_interests"), Mapping) else {}
        recent = tuple(dict(item) for item in _sequence(data.get("recent"))[-80:] if isinstance(item, Mapping))
        pending = dict(data.get("pending")) if isinstance(data.get("pending"), Mapping) else {}
        return cls(
            version=max(0, int(data.get("version", 0) or 0)),
            affinities={str(k): dict(v) for k, v in affinities.items() if isinstance(v, Mapping)},
            eris_interests={str(k): dict(v) for k, v in interests.items() if isinstance(v, Mapping)},
            recent=recent,
            pending=pending,
            promoted=tuple(str(x) for x in _sequence(data.get("promoted")) if str(x).strip()),
            positive_streak=max(0, int(data.get("positive_streak", 0) or 0)),
            negative_streak=max(0, int(data.get("negative_streak", 0) or 0)),
            updated_at=max(0.0, float(data.get("updated_at", 0.0) or 0.0)),
        )


class RandomChatSeedAffinityStore:
    """Persist seed receipts, learn user fit, and keep ERIS interest independent."""

    def __init__(
        self,
        *,
        initial_state: Mapping[str, Any] | None = None,
        persist: Callable[[Mapping[str, Any]], None] | None = None,
        preference_callback: Callable[..., str | None] | None = None,
        logger: Any | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._state = RandomChatSeedState.from_mapping(initial_state)
        self._persist = persist
        self._preference_callback = preference_callback
        self._logger = logger
        self._clock = clock
        self._lock = threading.RLock()

    def selection_context(self, *, now: float | None = None) -> dict[str, Any]:
        current = float(now if now is not None else self._clock())
        with self._lock:
            self._expire_pending_locked(current)
            affinities = sorted(
                (dict(value, key=key) for key, value in self._state.affinities.items()),
                key=lambda row: (float(row.get("score", 0.0) or 0.0), int(row.get("positive", 0) or 0)),
                reverse=True,
            )[:24]
            interests = sorted(
                (dict(value, key=key) for key, value in self._state.eris_interests.items()),
                key=lambda row: float(row.get("salience", 0.0) or 0.0),
                reverse=True,
            )[:16]
            adaptive = 0.46 + min(0.16, self._state.positive_streak * 0.04) - min(0.24, self._state.negative_streak * 0.08)
            daring_ceiling = max(0.28, min(0.82, adaptive))
            return {
                "schema": "eris.random_chat_seed_selection.v1",
                "user_affinities": affinities,
                "eris_interests": interests,
                "recent_seeds": [
                    {
                        "seed_id": item.get("seed_id", ""),
                        "premise": item.get("premise", ""),
                        "topic_tags": list(item.get("topic_tags", ()) or ()),
                        "mode": item.get("mode", ""),
                        "event_kind": item.get("event_kind", "thought"),
                        "epistemic_mode": item.get("epistemic_mode", "opinion"),
                        "opening_move": item.get("opening_move", ""),
                        "outcome": item.get("outcome", ""),
                        "spoken_at": item.get("spoken_at", 0.0),
                    }
                    for item in self._state.recent[-12:]
                ],
                "adaptive_daring": round(max(0.20, min(0.75, adaptive)), 3),
                "daring_ceiling": round(daring_ceiling, 3),
                "exploration_mix": {
                    "shared": 0.42,
                    "user_led": 0.23,
                    "eris_led": 0.25,
                    "experimental": 0.10,
                },
                "learning_rule": (
                    "A negative response lowers user/strategy fit only. It never erases ERIS interest. "
                    "Silence is neutral. Explicit preference or three separate positive outcomes may be promoted."
                ),
            }

    def validate_seed(self, seed: RandomChatSeed, *, now: float | None = None) -> tuple[bool, str]:
        current = float(now if now is not None else self._clock())
        if not seed.available:
            return False, "seed_incomplete"
        if seed.generic:
            return False, "seed_generic_presence"
        if seed.intrusion_risk > 0.46:
            return False, "seed_intrusion_risk"
        if seed.daring > 0.86:
            return False, "seed_daring_out_of_lane"
        # V45 material-only seed chưa phải conversation move. Mode/daring affinity
        # chỉ có ý nghĩa sau khi material trở thành ExpressionUrge; đừng reject một
        # thought tốt vì nó chưa mang đủ "ego stake" để được nói ngay.
        if not seed.material_only and seed.mode == "eris_led" and (seed.personal_stake < 0.34 or seed.authenticity < 0.38):
            return False, "eris_led_without_ego_stake"
        context = self.selection_context(now=current)
        if not seed.material_only and seed.daring > float(context.get("daring_ceiling", 0.60) or 0.60) + 0.10:
            return False, "seed_exceeds_adaptive_daring"
        with self._lock:
            for item in reversed(self._state.recent[-16:]):
                if current - float(item.get("spoken_at", 0.0) or 0.0) > 7200.0:
                    continue
                if _similarity(seed.premise, str(item.get("premise", "") or "")) >= 0.72:
                    return False, "seed_recent_duplicate"
                old_tags = set(str(x) for x in (item.get("topic_tags", ()) or ()))
                if old_tags and set(seed.topic_tags) == old_tags and current - float(item.get("spoken_at", 0.0) or 0.0) < 1200.0:
                    return False, "seed_topic_cooldown"
        return True, "allowed"

    def note_spoken(
        self,
        seed: RandomChatSeed | Mapping[str, Any],
        *,
        assistant_turn_id: str,
        text: str,
        now: float | None = None,
    ) -> None:
        item = seed if isinstance(seed, RandomChatSeed) else RandomChatSeed.from_mapping(seed)
        if not item.available:
            return
        current = float(now if now is not None else self._clock())
        receipt = {
            **item.to_dict(),
            "assistant_turn_id": _compact(assistant_turn_id, 120),
            "surface_text": _compact(text, 520),
            "spoken_at": current,
            "outcome": "pending",
        }
        with self._lock:
            interests = {key: dict(value) for key, value in self._state.eris_interests.items()}
            for tag in item.topic_tags:
                key = f"tag:{tag}"
                old = dict(interests.get(key, {}))
                salience = max(float(old.get("salience", 0.0) or 0.0) * 0.96, item.personal_stake)
                interests[key] = {
                    "salience": round(_clamp01(salience), 4),
                    "authenticity": round(max(float(old.get("authenticity", 0.0) or 0.0), item.authenticity), 4),
                    "last_shared_at": current,
                    "shares": int(old.get("shares", 0) or 0) + 1,
                }
            recent = list(self._state.recent)
            recent.append(receipt)
            self._state = replace(
                self._state,
                version=self._state.version + 1,
                eris_interests=interests,
                recent=tuple(recent[-80:]),
                pending=receipt,
                updated_at=current,
            )
            self._persist_locked()

    def observe_user_turn(
        self,
        user_text: str,
        *,
        user_turn_id: str = "",
        now: float | None = None,
    ) -> SeedOutcome | None:
        current = float(now if now is not None else self._clock())
        text = _compact(user_text, 1200)
        if not text:
            return None
        with self._lock:
            self._expire_pending_locked(current)
            pending = dict(self._state.pending)
            if not pending:
                return None
            spoken_at = float(pending.get("spoken_at", 0.0) or 0.0)
            if current - spoken_at > 900.0:
                return None
            classification, score, explicit = self._classify_response(text)
            evidence_id = _compact(user_turn_id, 120) or f"seed_response:{int(current * 1000)}"
            affinities = {key: dict(value) for key, value in self._state.affinities.items()}
            feature_keys = [f"tag:{tag}" for tag in pending.get("topic_tags", ()) or ()]
            feature_keys.extend((
                f"mode:{pending.get('mode', 'shared')}",
                f"event:{pending.get('event_kind', 'thought')}",
                f"epistemic:{pending.get('epistemic_mode', 'opinion')}",
                f"move:{pending.get('opening_move', 'share_observation')}",
            ))
            for key in dict.fromkeys(feature_keys):
                old = dict(affinities.get(key, {}))
                positive = int(old.get("positive", 0) or 0)
                neutral = int(old.get("neutral", 0) or 0)
                negative = int(old.get("negative", 0) or 0)
                old_score = float(old.get("score", 0.0) or 0.0)
                if classification == "positive":
                    positive += 1
                    new_score = old_score * 0.86 + score * 0.14
                elif classification == "negative":
                    negative += 1
                    new_score = old_score * 0.78 - abs(score) * 0.22
                else:
                    neutral += 1
                    new_score = old_score * 0.98
                affinities[key] = {
                    "score": round(max(-1.0, min(1.0, new_score)), 4),
                    "positive": positive,
                    "neutral": neutral,
                    "negative": negative,
                    "last_outcome": classification,
                    "last_evidence_id": evidence_id,
                    "updated_at": current,
                }
            promoted = list(self._state.promoted)
            promoted_paths: list[str] = []
            if classification == "positive":
                pos_streak = self._state.positive_streak + 1
                neg_streak = 0
            elif classification == "negative":
                pos_streak = 0
                neg_streak = self._state.negative_streak + 1
            else:
                pos_streak = 0
                neg_streak = 0
            for tag in pending.get("topic_tags", ()) or ():
                key = f"tag:{tag}"
                stats = affinities.get(key, {})
                should_promote = explicit or int(stats.get("positive", 0) or 0) >= 3
                if not should_promote or key in promoted:
                    continue
                path = self._promote_preference(
                    tag=str(tag),
                    explicit=explicit,
                    confidence=0.94 if explicit else min(0.88, 0.62 + 0.06 * int(stats.get("positive", 0) or 0)),
                    evidence_ids=(str(pending.get("assistant_turn_id", "") or ""), evidence_id),
                    seed=pending,
                )
                if path:
                    promoted.append(key)
                    promoted_paths.append(path)
            recent = list(self._state.recent)
            for index in range(len(recent) - 1, -1, -1):
                if str(recent[index].get("seed_id", "")) == str(pending.get("seed_id", "")):
                    recent[index] = {
                        **recent[index],
                        "outcome": classification,
                        "outcome_score": score,
                        "response_text": text[:520],
                        "response_turn_id": evidence_id,
                        "responded_at": current,
                    }
                    break
            self._state = replace(
                self._state,
                version=self._state.version + 1,
                affinities=affinities,
                recent=tuple(recent[-80:]),
                pending={},
                promoted=tuple(dict.fromkeys(promoted)),
                positive_streak=pos_streak,
                negative_streak=neg_streak,
                updated_at=current,
            )
            self._persist_locked()
            return SeedOutcome(
                seed_id=str(pending.get("seed_id", "") or ""),
                classification=classification,
                score=score,
                explicit=explicit,
                evidence_id=evidence_id,
                promoted_paths=tuple(promoted_paths),
            )

    def snapshot(self, *, now: float | None = None) -> dict[str, Any]:
        """Trả state hiện tại và giữ contract ``snapshot(now=...)`` của runtime.

        ``now`` được nhận để bootstrap/diagnostics có thể dùng chung một clock
        injected với các subsystem khác. Snapshot là thao tác chỉ đọc, vì vậy
        nó không tự expire hoặc persist state chỉ vì health monitor hỏi dữ liệu.
        """
        _ = now
        with self._lock:
            return self._state.to_dict()

    @staticmethod
    def _classify_response(text: str) -> tuple[str, float, bool]:
        if _NEGATIVE_EXPLICIT.search(text):
            return "negative", -0.95, True
        if _POSITIVE_EXPLICIT.search(text):
            return "positive", 0.95, True
        compact = " ".join(text.split())
        if _LAUGHTER.search(compact):
            return "positive", 0.72, False
        if len(compact) >= 28 or "?" in compact or "？" in compact:
            return "positive", 0.58, False
        if len(compact) <= 10:
            return "neutral", 0.0, False
        return "neutral", 0.0, False

    def _promote_preference(
        self,
        *,
        tag: str,
        explicit: bool,
        confidence: float,
        evidence_ids: tuple[str, ...],
        seed: Mapping[str, Any],
    ) -> str:
        callback = self._preference_callback
        if callback is None:
            return ""
        slug = _clean_tag(tag)
        if not slug:
            return ""
        path = f"preferences.autonomous_chat.{slug}"
        value = {
            "topic_tag": slug,
            "preference": "welcomes_eris_sharing",
            "opening_move": str(seed.get("opening_move", "") or ""),
            "seed_mode": str(seed.get("mode", "") or ""),
        }
        try:
            result = callback(
                path=path,
                value=value,
                explicitness="explicit" if explicit else "observed",
                confidence=confidence,
                evidence_ids=tuple(item for item in evidence_ids if item),
                metadata={
                    "source": "random_chat_seed_outcome",
                    "seed_id": str(seed.get("seed_id", "") or ""),
                    "topic_tag": slug,
                },
            )
            return str(result or path)
        except Exception as exc:  # persistence must not break chat
            self._log("debug", "[random chat seed] preference promotion failed: %s", exc)
            return ""

    def _expire_pending_locked(self, now: float) -> None:
        pending = dict(self._state.pending)
        if not pending:
            return
        # No response is not rejection. Expire silently and preserve ERIS interest.
        if now - float(pending.get("spoken_at", 0.0) or 0.0) <= 900.0:
            return
        self._state = replace(
            self._state,
            version=self._state.version + 1,
            pending={},
            updated_at=now,
        )
        self._persist_locked()

    def _persist_locked(self) -> None:
        if self._persist is None:
            return
        try:
            self._persist(self._state.to_dict())
        except Exception as exc:
            self._log("warning", "[random chat seed] persist failed: %s", exc)

    def _log(self, level: str, message: str, *args: Any) -> None:
        if self._logger is None:
            return
        try:
            getattr(self._logger, level)(message, *args)
        except Exception:
            pass


__all__ = [
    "RANDOM_CHAT_SEED_SCHEMA",
    "RandomChatSeed",
    "RandomChatSeedAffinityStore",
    "SeedQualityAssessment",
    "assess_seed_quality",
    "seed_event_family",
    "RandomChatSeedState",
    "SeedOutcome",
]
