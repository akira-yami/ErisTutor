# -*- coding: utf-8 -*-
"""Mental Material contracts cho ERIS V45.

V45 tách ba chuyện trước đây bị ``random_chat_seed`` trộn vào cùng một object:

1. ``CausalMotive``: vì sao đầu óc đang chuyển động *trước khi* có premise.
2. ``MentalMaterial``: thứ vừa nảy ra trong đầu, chưa mặc định là lời muốn nói.
3. Conversation planning: hỏi/chia sẻ/chọc thế nào là việc của ExpressionUrge + Neuro + Surface.

Điểm quan trọng nhất là motive không được do model viết sau khi nó vừa bịa premise.
Runtime dựng motive từ state/event có trước. Seed worker chỉ được làm associative
materialization cho motive đó. Như vậy ``why_now`` là provenance, không phải văn
mẫu tự hợp thức hóa chính output của model.
"""
from __future__ import annotations

import hashlib
import re
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Mapping, Sequence


MENTAL_MATERIAL_VERSION = "eris.mental_material.v45.causal_association"

MATERIAL_KINDS = {
    "observation",
    "opinion",
    "association",
    "memory_callback",
    "unfinished_thought",
    "desire",
    "plan",
    "reaction",
    "question",
    "playful_impulse",
    "small_contact",
    "self_reflection",
    "hunch",
    # V76: đoạn kể/ẩn dụ được đánh dấu imagination, không được giả thành ký ức thật.
    "story_fragment",
}

# Mapping này chỉ phục vụ compatibility với CognitiveEvent/ConcernStore cũ.
# MentalMaterial mới là ontology chính; event_kind không còn là thứ generator
# dùng để quyết định conversation shape.
MATERIAL_TO_EVENT_KIND = {
    "observation": "thought",
    "opinion": "opinion",
    "association": "association",
    "memory_callback": "memory_callback",
    "unfinished_thought": "unfinished_loop",
    "desire": "thought",
    "plan": "thought",
    "reaction": "thought",
    "question": "curiosity_question",
    "playful_impulse": "playful_probe",
    "small_contact": "thought",
    "self_reflection": "self_reflection",
    "hunch": "speculation",
    "story_fragment": "thought",
}

MATERIAL_TO_EPISTEMIC = {
    "observation": "bounded_inference",
    "opinion": "opinion",
    "association": "bounded_inference",
    "memory_callback": "bounded_inference",
    "unfinished_thought": "question",
    "desire": "opinion",
    "plan": "opinion",
    "reaction": "opinion",
    "question": "question",
    "playful_impulse": "imagination",
    "small_contact": "bounded_inference",
    "self_reflection": "opinion",
    "hunch": "speculation",
    "story_fragment": "imagination",
}

# Motive runtime-owned vẫn dùng vocabulary cũ ở boundary để không phá Subject.
MATERIAL_MOTIVE_TO_LEGACY = {
    "engage_user_event": "want_to_explore",
    "continue_conversation_trajectory": "want_to_revisit",
    "continue_concern": "want_to_revisit",
    "revisit_memory": "want_to_revisit",
    "continue_private_thought": "want_to_express",
    "notice_environment_change": "want_to_share",
    "react_to_self_outcome": "want_to_express",
    "share_internal_material": "want_to_share",
}


def _compact(value: Any, limit: int = 420) -> str:
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


def _seq(value: Any) -> Sequence[Any]:
    return value if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)) else ()


def _digest(*parts: Any) -> str:
    raw = "\x1f".join(_compact(part, 1200) for part in parts)
    return hashlib.sha256(raw.encode("utf-8", errors="replace")).hexdigest()[:20]


def _semantic_words(text: str) -> tuple[str, ...]:
    words = re.findall(r"[\w\u00c0-\u024f\u1e00-\u1eff]+", str(text or "").lower())
    return tuple(word for word in words if len(word) >= 3)[:24]


@dataclass(frozen=True, slots=True)
class CausalMotive:
    """Nguyên nhân runtime-owned tồn tại trước material generation."""

    cause_id: str
    cause_kind: str
    motive_kind: str
    semantic_focus: str
    why_now: str
    strength: float
    source_refs: tuple[str, ...] = ()
    allowed_material_kinds: tuple[str, ...] = ()
    generation_budget: int = 1
    relationship_modulation: float = 0.0
    # V70: private rumination metadata. Đây là lực tạo material, KHÔNG phải quyền nói.
    rumination_attractor: float = 0.0
    rumination_awareness: float = 0.0
    development_required: bool = False
    allowed_development_modes: tuple[str, ...] = ()

    @property
    def legacy_motive_kind(self) -> str:
        return MATERIAL_MOTIVE_TO_LEGACY.get(self.motive_kind, "want_to_share")

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["schema"] = MENTAL_MATERIAL_VERSION
        payload["legacy_motive_kind"] = self.legacy_motive_kind
        return payload


@dataclass(slots=True)
class MentalMaterial:
    """Một mảnh nội tâm, chưa mặc định là concern hay speech candidate."""

    material_id: str
    material_kind: str
    content: str
    cause_id: str
    cause_kind: str
    motive_kind: str
    source_kind: str
    source_refs: tuple[str, ...]
    topic_tags: tuple[str, ...]
    epistemic_mode: str
    open_loop: bool
    cause_strength: float
    quality: float
    confidence: float
    uncertainty: float
    created_at: float
    last_input_at: float
    ttl_seconds: float
    expected_update: tuple[str, ...] = ()
    # V70: semantic relation của một private return với concern cha.
    development_mode: str = ""
    development_score: float = 0.0
    development_required: bool = False
    rumination_return: bool = False
    cause_semantic_focus: str = ""
    parent_concern_id: str = ""
    freshness: float = 1.0
    satisfaction: float = 0.0
    echo_count: int = 0
    promoted_to_concern: bool = False
    expressed_at: float = 0.0

    def effective_freshness(self, now: float) -> float:
        age = max(0.0, float(now) - self.last_input_at)
        half_life = max(75.0, min(420.0, self.ttl_seconds * 0.42))
        decay = 0.5 ** (age / half_life)
        return _clamp01(self.freshness * decay * (0.93 ** self.echo_count))

    def activation(self, now: float) -> float:
        fresh = self.effective_freshness(now)
        return _clamp01(
            0.38 * self.cause_strength
            + 0.28 * self.quality
            + 0.18 * self.confidence
            + 0.16 * fresh
            - 0.55 * self.satisfaction
        )

    def concern_pull(self, now: float) -> float:
        kind_bias = {
            "unfinished_thought": 0.32,
            "question": 0.28,
            "hunch": 0.18,
            "self_reflection": 0.16,
            "memory_callback": 0.12,
            "association": 0.10,
        }.get(self.material_kind, 0.02)
        open_loop = 0.22 if self.open_loop else 0.0
        return _clamp01(0.58 * self.activation(now) + kind_bias + open_loop)

    def expression_pull(self, now: float) -> float:
        kind_bias = {
            "reaction": 0.24,
            "opinion": 0.22,
            "playful_impulse": 0.25,
            "small_contact": 0.20,
            "desire": 0.18,
            "observation": 0.15,
            "memory_callback": 0.14,
            "association": 0.11,
            "question": 0.13,
            "plan": 0.10,
            "self_reflection": 0.06,
            "unfinished_thought": 0.04,
            "hunch": 0.10,
        }.get(self.material_kind, 0.04)
        # Question chỉ được ưu tiên biểu đạt nếu câu trả lời thực sự có nơi để đi.
        # Không blacklist small talk; chỉ ngăn question generator hỏi cho có hội thoại.
        update_bonus = 0.10 if self.material_kind == "question" and self.expected_update else 0.0
        return _clamp01(0.64 * self.activation(now) + kind_bias + update_bonus - 0.45 * self.satisfaction)

    def lifecycle_state(self, now: float) -> str:
        fresh = self.effective_freshness(now)
        if fresh < 0.10 or self.activation(now) < 0.18:
            return "fading"
        if self.expression_pull(now) >= 0.60:
            return "expressible"
        if self.concern_pull(now) >= 0.58:
            return "concern"
        return "thought"

    def to_dict(self, now: float) -> dict[str, Any]:
        return {
            "schema": MENTAL_MATERIAL_VERSION,
            "material_id": self.material_id,
            "material_kind": self.material_kind,
            "content": self.content,
            "cause_id": self.cause_id,
            "cause_kind": self.cause_kind,
            "motive_kind": self.motive_kind,
            "source_kind": self.source_kind,
            "source_refs": list(self.source_refs),
            "topic_tags": list(self.topic_tags),
            "epistemic_mode": self.epistemic_mode,
            "open_loop": self.open_loop,
            "expected_update": list(self.expected_update),
            "development_mode": self.development_mode,
            "development_score": round(self.development_score, 3),
            "development_required": self.development_required,
            "rumination_return": self.rumination_return,
            "cause_semantic_focus": self.cause_semantic_focus,
            "parent_concern_id": self.parent_concern_id,
            "cause_strength": round(self.cause_strength, 3),
            "quality": round(self.quality, 3),
            "confidence": round(self.confidence, 3),
            "uncertainty": round(self.uncertainty, 3),
            "freshness": round(self.effective_freshness(now), 3),
            "activation": round(self.activation(now), 3),
            "concern_pull": round(self.concern_pull(now), 3),
            "expression_pull": round(self.expression_pull(now), 3),
            "state": self.lifecycle_state(now),
            "satisfaction": round(self.satisfaction, 3),
            "echo_count": self.echo_count,
            "age_seconds": round(max(0.0, now - self.created_at), 1),
        }


def derive_causal_motives(context: Mapping[str, Any], *, now: float | None = None) -> tuple[CausalMotive, ...]:
    """Dựng motive từ state có trước, không gọi model và không sinh premise.

    Relationship/mood chỉ modulation. Chúng không được tự tạo semantic focus.
    Vì thế ``connection_pressure=.9`` không đủ để bịa một câu hỏi xã giao. Muốn
    small talk, vẫn cần một user event/memory/thought cụ thể để nói *về cái gì*.
    """

    current = float(now if now is not None else time.time())
    motives: list[CausalMotive] = []
    relationship = context.get("relationship") if isinstance(context.get("relationship"), Mapping) else {}
    connection = _clamp01(relationship.get("connection_pressure", relationship.get("outreach_drive", 0.0)))

    recent_user = [item for item in _seq(context.get("recent_user_events")) if isinstance(item, Mapping)]
    for index, item in enumerate(recent_user[-3:]):
        text = _compact(item.get("text", item.get("content", "")), 520)
        if not text:
            continue
        event_ref = _compact(item.get("event_ref", item.get("turn_id", item.get("event_id", ""))), 120)
        cause_id = f"user_event:{event_ref or _digest(text)}"
        motives.append(CausalMotive(
            cause_id=cause_id,
            cause_kind="user_event",
            motive_kind="engage_user_event",
            semantic_focus=text,
            why_now=f"new_or_recent_user_event:{cause_id}",
            strength=_clamp01(0.72 + 0.10 * connection - 0.05 * (len(recent_user[-3:]) - index - 1)),
            source_refs=(cause_id,),
            allowed_material_kinds=(
                "reaction", "opinion", "association", "question", "playful_impulse",
                "small_contact", "hunch", "observation", "story_fragment",
            ),
            generation_budget=2 if index == len(recent_user[-3:]) - 1 else 1,
            relationship_modulation=connection,
        ))

    # V75: một chat reply đã consume user_event để ngăn double-reply, nhưng topic
    # vẫn có thể tiếp tục sống trong private cognition. Trajectory tạo một causal
    # motive RIÊNG, vì thế seed/autonomous có thể đào sâu sau chat mà không giả rằng
    # user vẫn đang nợ câu trả lời cũ.
    trajectory = context.get("conversation_trajectory") if isinstance(context.get("conversation_trajectory"), Mapping) else {}
    if trajectory and bool(trajectory.get("active", True)) and not bool(trajectory.get("closed", False)):
        topic_id = _compact(trajectory.get("topic_id", ""), 100)
        focus = _compact(trajectory.get("current_focus", ""), 620)
        root = _compact(trajectory.get("root_focus", ""), 420)
        extension = _compact(trajectory.get("last_assistant_extension", trajectory.get("last_assistant_text", "")), 420)
        revision = max(0, int(trajectory.get("revision", 0) or 0))
        momentum = _clamp01(trajectory.get("momentum", 0.0))
        energy = _clamp01(min(1.0, float(trajectory.get("energy", 0.0) or 0.0)))
        lifecycle_state = _compact(trajectory.get("lifecycle_state", "developing"), 24).lower()
        private_budget = max(0, int(trajectory.get("private_budget", 0) or 0))
        external_epoch = int(float(trajectory.get("last_external_input_at", 0.0) or 0.0))
        engagement = _clamp01(trajectory.get("user_engagement", 0.0))
        depth = max(0, int(trajectory.get("depth", 0) or 0))
        development_mode = _compact(trajectory.get("development_mode", "deepen"), 24).lower()
        if (
            topic_id and focus and momentum >= 0.20 and energy > 0.04 and private_budget > 0
            and lifecycle_state not in {"satiated", "dormant"}
        ):
            semantic_focus = (
                f"topic_id={topic_id}; current_focus={focus}; "
                f"root_focus={root or focus}; prior_eris_extension={extension or '<none>'}; "
                f"trajectory_depth={depth}; preferred_development={development_mode or 'deepen'}. "
                "Phát triển cùng semantic spine; không mở chủ đề ngẫu nhiên. "
                "prior_eris_extension chỉ là ngữ cảnh hội thoại, không phải bằng chứng factual."
            )
            refs = tuple(
                dict.fromkeys(
                    str(item).strip() for item in _seq(trajectory.get("source_refs"))
                    if str(item or "").strip()
                )
            )[:8]
            # V76: budget không được reset chỉ vì assistant commit tăng revision.
            # Epoch chỉ đổi khi có external/user input mới thật.
            cause_id = f"trajectory:{topic_id}:e{external_epoch or revision}"
            motives.append(CausalMotive(
                cause_id=cause_id,
                cause_kind="conversation_trajectory",
                motive_kind="continue_conversation_trajectory",
                semantic_focus=_compact(semantic_focus, 1200),
                why_now=(
                    f"conversation_trajectory_active:{topic_id}:depth={depth}:"
                    f"momentum={momentum:.2f}:energy={energy:.2f}:engagement={engagement:.2f}:life={lifecycle_state}"
                ),
                strength=_clamp01(0.66 + 0.22 * momentum + 0.08 * engagement),
                source_refs=tuple(dict.fromkeys((*refs, f"trajectory:{topic_id}")))[:10],
                allowed_material_kinds=(
                    "reaction", "opinion", "association", "unfinished_thought",
                    "hunch", "self_reflection", "question", "story_fragment",
                ),
                generation_budget=max(1, min(2, private_budget)),
                relationship_modulation=connection,
                development_required=True,
                allowed_development_modes=("deepen", "connect", "reinterpret", "test", "personalize"),
            ))

    # WorldState v1: SeedContext is the preferred compact environmental cause.
    # The seed worker no longer needs to inspect the full raw environment packet
    # just to decide what the current computer activity means for association.
    seed_environment_motive_added = False
    seed_context = context.get("seed_context") if isinstance(context.get("seed_context"), Mapping) else {}
    if seed_context:
        change = seed_context.get("meaningful_change") if isinstance(seed_context.get("meaningful_change"), Mapping) else {}
        attention = seed_context.get("attention") if isinstance(seed_context.get("attention"), Mapping) else {}
        semantic_delta = bool(change.get("semantic_delta", False))
        actions = tuple(str(item) for item in _seq(attention.get("actions")) if str(item or "").strip())
        event_conf = _clamp01(change.get("confidence", 0.0))
        event_key = _compact(change.get("event_key", change.get("kind", "")), 140)
        if (semantic_delta or "generate_seed" in actions) and event_conf >= 0.42:
            app = _compact(seed_context.get("focused_app", ""), 120)
            focus = _compact(seed_context.get("focused_object", ""), 240)
            activity = _compact(seed_context.get("current_activity", ""), 180)
            task = _compact(seed_context.get("current_task", ""), 240)
            phase = _compact(seed_context.get("phase", ""), 120)
            detail = _compact(change.get("detail", ""), 320)
            parts = []
            if app:
                parts.append(f"app={app}")
            if focus:
                parts.append(f"focus={focus}")
            if activity and activity.casefold() not in {"unknown", "none"}:
                parts.append(f"activity={activity}")
            if task and task.casefold() not in {"unknown", "none"}:
                parts.append(f"task={task}")
            if phase and phase.casefold() not in {"unknown", "none"}:
                parts.append(f"phase={phase}")
            if change.get("kind"):
                parts.append(f"recent_change={_compact(change.get('kind'), 72)}")
            if detail:
                parts.append(f"change_detail={detail}")
            if parts:
                world_revision = max(0, int(seed_context.get("world_revision", 0) or 0))
                env_revision = max(0, int(seed_context.get("environment_revision", 0) or 0))
                refs = tuple(
                    dict.fromkeys(str(item).strip() for item in _seq(seed_context.get("evidence_refs")) if str(item or "").strip())
                )[:10]
                salience = _clamp01(attention.get("salience", 0.0))
                novelty = _clamp01(change.get("novelty", 0.0))
                importance = _clamp01(change.get("importance", 0.0))
                strength = _clamp01(0.42 + 0.20 * event_conf + 0.18 * salience + 0.10 * novelty + 0.08 * importance + 0.05 * connection)
                cause_token = event_key or f"w{world_revision}:e{env_revision}"
                motives.append(CausalMotive(
                    cause_id=f"world_environment:{cause_token}",
                    cause_kind="environment_event",
                    motive_kind="notice_environment_change",
                    semantic_focus=_compact(
                        "WorldState SeedContext changed: " + "; ".join(parts) + ". "
                        "Use only this compact structured context; facts and interpretations are distinct and this is not VLM/pixel evidence.",
                        1200,
                    ),
                    why_now=f"world_state_environment_change:world={world_revision}:environment={env_revision}:confidence={event_conf:.2f}:salience={salience:.2f}",
                    strength=strength,
                    source_refs=refs or (f"world_state:r{world_revision}",),
                    allowed_material_kinds=(
                        "observation", "reaction", "association", "small_contact",
                        "hunch", "playful_impulse", "question",
                    ),
                    generation_budget=1,
                    relationship_modulation=connection,
                ))
                seed_environment_motive_added = True

    # Unified Environment Awareness: a *changed*, fresh structured situation may
    # become a private causal motive. This lets the seed worker associate with
    # what is actually happening on the computer instead of producing context-
    # free thoughts. Static focus is not enough: no semantic edge => no new motive.
    environment = (
        context.get("environment_awareness")
        if isinstance(context.get("environment_awareness"), Mapping)
        else {}
    )
    if environment and not seed_environment_motive_added:
        env_conf = _clamp01(environment.get("confidence", 0.0))
        env_age = max(0.0, float(environment.get("age_seconds", 9999.0) or 9999.0))
        env_fresh = bool(environment.get("fresh", env_age <= 120.0)) and env_age <= 120.0
        semantic_delta = bool(environment.get("semantic_delta", False))
        recent_environment_events = [
            item for item in _seq(environment.get("recent_events")) if isinstance(item, Mapping)
        ]
        recent_semantic = []
        for item in recent_environment_events[-8:]:
            event_age = max(0.0, current - float(item.get("timestamp", 0.0) or 0.0))
            event_conf = _clamp01(item.get("confidence", 0.0))
            if (bool(item.get("semantic", True)) or int(item.get("attention_level", 0) or 0) >= 1) and event_age <= 45.0 and event_conf >= 0.45:
                recent_semantic.append(item)
        if env_fresh and env_conf >= 0.55 and (semantic_delta or recent_semantic):
            latest_event = recent_semantic[-1] if recent_semantic else {}
            app = _compact(environment.get("focused_app", environment.get("focused_process", "")), 120)
            process = _compact(environment.get("focused_process", ""), 120)
            focused_object = _compact(environment.get("focused_object", ""), 240)
            activity = _compact(environment.get("activity", ""), 180)
            task_hint = _compact(environment.get("task_hint", ""), 240)
            phase = _compact(environment.get("phase", ""), 120)
            action = _compact(environment.get("last_action", ""), 180)
            outcome = _compact(environment.get("last_outcome", ""), 180)
            event_kind = _compact(latest_event.get("kind", ""), 72)
            event_detail = _compact(latest_event.get("detail", ""), 280)
            parts = []
            if app:
                parts.append(f"app={app}")
            if process and process.casefold() != app.casefold():
                parts.append(f"process={process}")
            if focused_object:
                parts.append(f"focus={focused_object}")
            if activity and activity.casefold() not in {"unknown", "none"}:
                parts.append(f"activity={activity}")
            if task_hint and task_hint.casefold() not in {"unknown", "none"}:
                parts.append(f"task={task_hint}")
            if phase and phase.casefold() not in {"unknown", "none"}:
                parts.append(f"phase={phase}")
            if action and action.casefold() not in {"unknown", "none"}:
                parts.append(f"last_action={action}")
            if outcome and outcome.casefold() not in {"unknown", "none"}:
                parts.append(f"last_outcome={outcome}")
            if event_kind:
                parts.append(f"recent_change={event_kind}")
            if event_detail:
                parts.append(f"change_detail={event_detail}")
            if parts:
                revision = max(0, int(environment.get("revision", 0) or 0))
                event_key = _compact(latest_event.get("event_key", ""), 120)
                cause_token = event_key or f"r{revision}"
                refs = [
                    str(item).strip()
                    for item in _seq(environment.get("evidence_refs"))
                    if str(item or "").strip()
                ]
                refs.extend(
                    str(item).strip()
                    for item in _seq(latest_event.get("evidence_refs"))
                    if str(item or "").strip()
                )
                importance = _clamp01(latest_event.get("importance", 0.0)) if latest_event else 0.0
                attention = max(0, min(4, int(latest_event.get("attention_level", environment.get("max_attention_level", 0)) or 0)))
                strength = _clamp01(0.44 + 0.26 * env_conf + 0.12 * importance + 0.05 * attention + 0.06 * connection)
                motives.append(CausalMotive(
                    cause_id=f"environment:r{revision}:{cause_token}",
                    cause_kind="environment_event",
                    motive_kind="notice_environment_change",
                    semantic_focus=_compact(
                        "Structured current environment changed: " + "; ".join(parts) + ". "
                        "Use only these structured facts; this is not screenshot/VLM evidence.",
                        1200,
                    ),
                    why_now=f"environment_semantic_change:revision={revision}:age={env_age:.1f}:confidence={env_conf:.2f}",
                    strength=strength,
                    source_refs=tuple(dict.fromkeys(refs))[:10] or (f"environment:r{revision}",),
                    allowed_material_kinds=(
                        "observation", "reaction", "association", "small_contact",
                        "hunch", "playful_impulse", "question",
                    ),
                    generation_budget=1,
                    relationship_modulation=connection,
                ))

    self_memory = context.get("eris_self_memory") if isinstance(context.get("eris_self_memory"), Mapping) else {}
    concerns = [item for item in _seq(self_memory.get("active_concerns")) if isinstance(item, Mapping)]
    for item in concerns[:6]:
        subject = _compact(item.get("subject", ""), 520)
        cid = _compact(item.get("concern_id", ""), 100)
        if not subject or not cid:
            continue
        activation = _clamp01(item.get("activation", 0.0))
        novelty = _clamp01(item.get("novelty", 0.0))
        habituation = _clamp01(item.get("habituation", 0.0))
        attractor = _clamp01(item.get("attractor_strength", 0.0))
        awareness = _clamp01(item.get("rumination_awareness", 0.0))
        meta = item.get("metadata") if isinstance(item.get("metadata"), Mapping) else {}
        completion = meta.get("completion") if isinstance(meta.get("completion"), Mapping) else {}
        expressive_completion = _clamp01(completion.get("expressive", 0.0))
        epistemic_completion = _clamp01(completion.get("epistemic", 0.0))
        mutation_count = int(item.get("mutation_count", 0) or 0)
        epistemic_residual = _clamp01(1.0 - epistemic_completion)
        # V70: một concern đã consummate vẫn có thể sinh private material nhờ
        # attractor + epistemic residual. Không cộng speech pressure vào đây.
        strength = _clamp01(
            0.30 * activation
            + 0.18 * novelty
            + 0.34 * attractor
            + 0.10 * (1.0 - habituation)
            + 0.08 * epistemic_residual
        )
        if strength < 0.28:
            continue
        rumination_meta = meta.get("rumination") if isinstance(meta.get("rumination"), Mapping) else {}
        development_required = bool(
            rumination_meta.get("development_required", False)
            or expressive_completion >= 0.72
            or (awareness >= 0.48 and attractor >= 0.40)
        )
        cause_id = f"concern:{cid}:m{mutation_count}"
        motives.append(CausalMotive(
            cause_id=cause_id,
            cause_kind="active_concern",
            motive_kind="continue_concern",
            semantic_focus=subject,
            why_now=(
                f"active_concern:{cid}:mutation={mutation_count}:"
                f"attractor={attractor:.2f}:awareness={awareness:.2f}"
            ),
            strength=strength,
            source_refs=(cid,),
            allowed_material_kinds=(
                "unfinished_thought", "opinion", "association", "question", "hunch", "self_reflection", "story_fragment",
            ),
            generation_budget=2 if (mutation_count > 0 or attractor >= 0.55) else 1,
            relationship_modulation=connection,
            rumination_attractor=attractor,
            rumination_awareness=awareness,
            development_required=development_required,
            allowed_development_modes=(
                "deepen", "contradict", "connect", "reinterpret", "test", "personalize",
            ) if development_required else (),
        ))

    workspace = [item for item in _seq(self_memory.get("private_life_workspace")) if isinstance(item, Mapping)]
    for item in workspace[-6:]:
        content = _compact(item.get("content", item.get("summary", "")), 520)
        mid = _compact(item.get("id", ""), 100) or _digest(content)
        if not content:
            continue
        cause_id = f"private_thought:{mid}"
        refs = tuple(str(x) for x in _seq(item.get("evidence_refs")) if str(x).strip())[:8]
        motives.append(CausalMotive(
            cause_id=cause_id,
            cause_kind="private_thought",
            motive_kind="continue_private_thought",
            semantic_focus=content,
            why_now=f"private_thought_active:{mid}",
            strength=_clamp01(0.44 + 0.12 * bool(refs)),
            source_refs=refs or (cause_id,),
            allowed_material_kinds=(
                "association", "opinion", "unfinished_thought", "desire", "plan", "self_reflection",
            ),
            generation_budget=1,
            relationship_modulation=connection,
        ))

    # Memory là nguồn semantic thật nhưng yếu hơn event hiện tại. Không cắt nó bỏ,
    # vì callback đời thường là một phần quan trọng của cảm giác continuity.
    user_memory = _compact(context.get("user_memory", ""), 1800)
    if user_memory:
        memory_focus = _compact(user_memory, 520)
        cause_id = f"memory_context:{_digest(memory_focus)}"
        motives.append(CausalMotive(
            cause_id=cause_id,
            cause_kind="memory_context",
            motive_kind="revisit_memory",
            semantic_focus=memory_focus,
            why_now=f"memory_resonance_available:{cause_id}",
            strength=_clamp01(0.34 + 0.08 * connection),
            source_refs=(cause_id,),
            allowed_material_kinds=("memory_callback", "association", "opinion", "question", "small_contact"),
            generation_budget=1,
            relationship_modulation=connection,
        ))

    # Dedup cause id và ưu tiên cause có lực trước. Diversity diễn ra sau bước
    # này, không được đảo ngược thành "reservoir thiếu play nên hãy bịa play".
    unique: dict[str, CausalMotive] = {}
    for motive in motives:
        current = unique.get(motive.cause_id)
        if current is None or motive.strength > current.strength:
            unique[motive.cause_id] = motive
    ranked = sorted(unique.values(), key=lambda item: (item.strength, item.cause_kind == "user_event"), reverse=True)
    return tuple(ranked)


__all__ = [
    "MENTAL_MATERIAL_VERSION",
    "MATERIAL_KINDS",
    "MATERIAL_TO_EVENT_KIND",
    "MATERIAL_TO_EPISTEMIC",
    "CausalMotive",
    "MentalMaterial",
    "derive_causal_motives",
]
