# -*- coding: utf-8 -*-
"""Kiểu dữ liệu canonical cho living user memory.

Tầng này không gọi model và không chạm persistence. Nó chỉ mô tả dữ liệu mà
các tầng khác được phép trao đổi. Giữ contract gọn giúp tránh chuyện mỗi module
phát minh một kiểu ``dict`` khác nhau rồi gọi đó là kiến trúc.
"""
from __future__ import annotations

import hashlib
import json
import re
import time
from dataclasses import dataclass, field
from typing import Any, Literal, Mapping

MemoryTier = Literal["working", "habit", "core_anchor"]
Explicitness = Literal["explicit", "observed", "inferred"]
ProfileStatus = Literal["active", "superseded", "disputed", "archived", "deleted"]
Sensitivity = Literal["normal", "personal", "private", "sensitive"]
ScopeKind = Literal["global", "project", "session", "temporary", "private"]
OperationKind = Literal["add", "reinforce", "update", "weaken", "supersede", "merge", "archive", "delete"]
Cardinality = Literal["single", "multi"]


def now_ts() -> float:
    return time.time()


def clamp01(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = default
    if number != number:  # NaN
        number = default
    return max(0.0, min(1.0, number))


def compact_text(value: Any, limit: int = 500) -> str:
    text = " ".join(str(value or "").split())
    return text if len(text) <= limit else text[: limit - 3].rstrip() + "..."


def normalize_path(value: str) -> str:
    """Chuẩn hóa profile path nhưng không ép vào ontology đóng."""
    text = str(value or "").strip().lower().replace("-", "_").replace("/", ".")
    text = re.sub(r"[^a-z0-9_.]+", "_", text)
    text = re.sub(r"[._]{2,}", ".", text).strip("._")
    return text[:180]


def value_fingerprint(value: Any) -> str:
    try:
        payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    except TypeError:
        payload = repr(value)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


@dataclass(frozen=True, slots=True)
class UserMemoryScope:
    """Phạm vi hiệu lực của profile item.

    ``project`` cần key dự án. ``session`` cần session id. Global không cần key.
    Tách scope khỏi path để cùng một preference có thể khác nhau theo dự án.
    """

    kind: ScopeKind = "global"
    key: str = ""

    def __post_init__(self) -> None:
        allowed = {"global", "project", "session", "temporary", "private"}
        if self.kind not in allowed:
            object.__setattr__(self, "kind", "global")
        object.__setattr__(self, "key", compact_text(self.key, 160))

    @property
    def identity(self) -> str:
        return f"{self.kind}:{self.key}" if self.key else self.kind

    def matches(self, active: "UserMemoryScope | None") -> bool:
        if self.kind == "global":
            return True
        if active is None:
            return False
        return self.kind == active.kind and (not self.key or self.key == active.key)

    def to_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, "key": self.key}

    @classmethod
    def from_value(cls, value: Any) -> "UserMemoryScope":
        if isinstance(value, cls):
            return value
        if isinstance(value, Mapping):
            return cls(kind=str(value.get("kind") or "global"), key=str(value.get("key") or ""))  # type: ignore[arg-type]
        raw = str(value or "global").strip()
        if ":" in raw:
            kind, key = raw.split(":", 1)
            return cls(kind=kind or "global", key=key)  # type: ignore[arg-type]
        return cls(kind=raw or "global")  # type: ignore[arg-type]


@dataclass(slots=True)
class EvidenceReference:
    """Nguồn dẫn có thể audit được."""

    id: str
    source_type: str = "unknown"
    excerpt: str = ""
    timestamp: float = field(default_factory=now_ts)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "source_type": self.source_type,
            "excerpt": compact_text(self.excerpt, 500),
            "timestamp": float(self.timestamp),
            "metadata": dict(self.metadata),
        }


@dataclass(slots=True)
class ProfileRecord:
    """Một mẩu chân dung người dùng có evidence, thời gian và độ chắc chắn."""

    id: str
    path: str
    value: Any
    value_type: str = "text"
    confidence: float = 0.5
    explicitness: Explicitness = "inferred"
    sensitivity: Sensitivity = "normal"
    tier: MemoryTier = "working"
    scope: UserMemoryScope = field(default_factory=UserMemoryScope)
    cardinality: Cardinality = "single"
    status: ProfileStatus = "active"
    evidence_ids: tuple[str, ...] = ()
    recurrence: int = 1
    source: str = "unknown"
    valid_from: float | None = None
    valid_until: float | None = None
    expires_at: float | None = None
    created_at: float = field(default_factory=now_ts)
    updated_at: float = field(default_factory=now_ts)
    last_confirmed_at: float = field(default_factory=now_ts)
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.path = normalize_path(self.path)
        self.confidence = clamp01(self.confidence, 0.5)
        if self.explicitness not in {"explicit", "observed", "inferred"}:
            self.explicitness = "inferred"
        if self.sensitivity not in {"normal", "personal", "private", "sensitive"}:
            self.sensitivity = "normal"
        if self.status not in {"active", "superseded", "disputed", "archived", "deleted"}:
            self.status = "active"
        if self.tier not in {"working", "habit", "core_anchor"}:
            self.tier = determine_memory_tier(self.path, self.explicitness, self.recurrence, self.value)
        self.scope = UserMemoryScope.from_value(self.scope)
        self.cardinality = "multi" if self.cardinality == "multi" else "single"
        self.evidence_ids = tuple(dict.fromkeys(str(x) for x in self.evidence_ids if str(x).strip()))[:96]
        self.recurrence = max(1, int(self.recurrence or 1))
        self.metadata = dict(self.metadata or {})
        if not self.id:
            # ID là identity của một phiên bản record, không phải identity của path.
            # Single-valued record cũ phải còn tồn tại ở trạng thái superseded.
            seed = f"{self.scope.identity}|{self.path}|{value_fingerprint(self.value)}|{self.created_at:.6f}"
            self.id = f"upr_{hashlib.sha256(seed.encode()).hexdigest()[:18]}"

    @property
    def is_current(self) -> bool:
        now = now_ts()
        if self.status != "active":
            return False
        if self.valid_from is not None and now < self.valid_from:
            return False
        if self.valid_until is not None and now > self.valid_until:
            return False
        if self.expires_at is not None and now > self.expires_at:
            return False
        return True

    @property
    def trust_weight(self) -> float:
        explicit_bonus = {"explicit": 1.0, "observed": 0.82, "inferred": 0.62}[self.explicitness]
        recurrence_bonus = min(1.0, 0.45 + 0.12 * self.recurrence)
        tier_bonus = 0.15 if self.tier == "core_anchor" else (0.05 if self.tier == "habit" else 0.0)
        return clamp01(self.confidence * 0.70 + explicit_bonus * 0.15 + recurrence_bonus * 0.10 + tier_bonus)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "path": self.path,
            "value": self.value,
            "value_type": self.value_type,
            "confidence": round(self.confidence, 5),
            "explicitness": self.explicitness,
            "sensitivity": self.sensitivity,
            "tier": self.tier,
            "scope": self.scope.to_dict(),
            "cardinality": self.cardinality,
            "status": self.status,
            "evidence_ids": list(self.evidence_ids),
            "recurrence": self.recurrence,
            "source": self.source,
            "valid_from": self.valid_from,
            "valid_until": self.valid_until,
            "expires_at": self.expires_at,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "last_confirmed_at": self.last_confirmed_at,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ProfileRecord":
        return cls(
            id=str(data.get("id") or ""),
            path=str(data.get("path") or ""),
            value=data.get("value"),
            value_type=str(data.get("value_type") or "text"),
            confidence=clamp01(data.get("confidence"), 0.5),
            explicitness=str(data.get("explicitness") or "inferred"),  # type: ignore[arg-type]
            sensitivity=str(data.get("sensitivity") or "normal"),  # type: ignore[arg-type]
            tier=str(data.get("tier") or "working"),  # type: ignore[arg-type]
            scope=UserMemoryScope.from_value(data.get("scope")),
            cardinality=str(data.get("cardinality") or "single"),  # type: ignore[arg-type]
            status=str(data.get("status") or "active"),  # type: ignore[arg-type]
            evidence_ids=tuple(data.get("evidence_ids") or ()),
            recurrence=int(data.get("recurrence") or 1),
            source=str(data.get("source") or "unknown"),
            valid_from=float(data["valid_from"]) if data.get("valid_from") is not None else None,
            valid_until=float(data["valid_until"]) if data.get("valid_until") is not None else None,
            expires_at=float(data["expires_at"]) if data.get("expires_at") is not None else None,
            created_at=float(data.get("created_at") or now_ts()),
            updated_at=float(data.get("updated_at") or now_ts()),
            last_confirmed_at=float(data.get("last_confirmed_at") or data.get("updated_at") or now_ts()),
            metadata=dict(data.get("metadata") or {}),
        )


@dataclass(slots=True)
class ProfileOperation:
    """Patch có kiểm soát do extractor/dreamer đề xuất."""

    op: OperationKind
    path: str
    value: Any = None
    old_value: Any = None
    confidence: float = 0.5
    explicitness: Explicitness = "inferred"
    sensitivity: Sensitivity = "normal"
    tier: MemoryTier | None = None
    scope: UserMemoryScope = field(default_factory=UserMemoryScope)
    cardinality: Cardinality = "single"
    evidence_ids: tuple[str, ...] = ()
    reason: str = ""
    source: str = "unknown"
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.path = normalize_path(self.path)
        self.confidence = clamp01(self.confidence, 0.5)
        if self.tier is not None and self.tier not in {"working", "habit", "core_anchor"}:
            self.tier = None
        self.scope = UserMemoryScope.from_value(self.scope)
        self.cardinality = "multi" if self.cardinality == "multi" else "single"
        self.evidence_ids = tuple(dict.fromkeys(str(x) for x in self.evidence_ids if str(x).strip()))[:96]
        self.reason = compact_text(self.reason, 420)
        self.metadata = dict(self.metadata or {})

    def to_dict(self) -> dict[str, Any]:
        return {
            "op": self.op,
            "path": self.path,
            "value": self.value,
            "old_value": self.old_value,
            "confidence": self.confidence,
            "explicitness": self.explicitness,
            "sensitivity": self.sensitivity,
            "tier": self.tier,
            "scope": self.scope.to_dict(),
            "cardinality": self.cardinality,
            "evidence_ids": list(self.evidence_ids),
            "reason": self.reason,
            "source": self.source,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ProfileOperation":
        op = str(data.get("op") or "add")
        if op not in {"add", "reinforce", "update", "weaken", "supersede", "merge", "archive", "delete"}:
            op = "add"
        return cls(
            op=op,  # type: ignore[arg-type]
            path=str(data.get("path") or ""),
            value=data.get("value"),
            old_value=data.get("old_value"),
            confidence=clamp01(data.get("confidence"), 0.5),
            explicitness=str(data.get("explicitness") or "inferred"),  # type: ignore[arg-type]
            sensitivity=str(data.get("sensitivity") or "normal"),  # type: ignore[arg-type]
            scope=UserMemoryScope.from_value(data.get("scope")),
            cardinality=str(data.get("cardinality") or "single"),  # type: ignore[arg-type]
            evidence_ids=tuple(data.get("evidence_ids") or ()),
            reason=str(data.get("reason") or ""),
            source=str(data.get("source") or "unknown"),
            metadata=dict(data.get("metadata") or {}),
        )


@dataclass(slots=True)
class UserEpisode:
    """Tóm tắt một phiên/sự kiện có giá trị cho việc hiểu người dùng."""

    id: str
    session_id: str
    title: str
    summary: str
    outcome: str = ""
    topics: tuple[str, ...] = ()
    entities: tuple[str, ...] = ()
    decisions: tuple[str, ...] = ()
    open_loops: tuple[str, ...] = ()
    user_signals: tuple[str, ...] = ()
    evidence_ids: tuple[str, ...] = ()
    importance: float = 0.5
    scope: UserMemoryScope = field(default_factory=UserMemoryScope)
    started_at: float = field(default_factory=now_ts)
    ended_at: float = field(default_factory=now_ts)
    source: str = "episode_builder"
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.title = compact_text(self.title, 160)
        self.summary = compact_text(self.summary, 1200)
        self.outcome = compact_text(self.outcome, 500)
        self.topics = tuple(dict.fromkeys(compact_text(x, 100) for x in self.topics if str(x).strip()))[:24]
        self.entities = tuple(dict.fromkeys(compact_text(x, 100) for x in self.entities if str(x).strip()))[:24]
        self.decisions = tuple(dict.fromkeys(compact_text(x, 260) for x in self.decisions if str(x).strip()))[:20]
        self.open_loops = tuple(dict.fromkeys(compact_text(x, 260) for x in self.open_loops if str(x).strip()))[:20]
        self.user_signals = tuple(dict.fromkeys(compact_text(x, 260) for x in self.user_signals if str(x).strip()))[:24]
        self.evidence_ids = tuple(dict.fromkeys(str(x) for x in self.evidence_ids if str(x).strip()))[:128]
        self.importance = clamp01(self.importance, 0.5)
        self.scope = UserMemoryScope.from_value(self.scope)
        self.metadata = dict(self.metadata or {})
        if not self.id:
            seed = f"{self.session_id}|{self.started_at}|{self.summary}"
            self.id = f"uep_{hashlib.sha256(seed.encode()).hexdigest()[:18]}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "session_id": self.session_id,
            "title": self.title,
            "summary": self.summary,
            "outcome": self.outcome,
            "topics": list(self.topics),
            "entities": list(self.entities),
            "decisions": list(self.decisions),
            "open_loops": list(self.open_loops),
            "user_signals": list(self.user_signals),
            "evidence_ids": list(self.evidence_ids),
            "importance": self.importance,
            "scope": self.scope.to_dict(),
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "source": self.source,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "UserEpisode":
        return cls(
            id=str(data.get("id") or ""),
            session_id=str(data.get("session_id") or ""),
            title=str(data.get("title") or ""),
            summary=str(data.get("summary") or ""),
            outcome=str(data.get("outcome") or ""),
            topics=tuple(data.get("topics") or ()),
            entities=tuple(data.get("entities") or ()),
            decisions=tuple(data.get("decisions") or ()),
            open_loops=tuple(data.get("open_loops") or ()),
            user_signals=tuple(data.get("user_signals") or ()),
            evidence_ids=tuple(data.get("evidence_ids") or ()),
            importance=clamp01(data.get("importance"), 0.5),
            scope=UserMemoryScope.from_value(data.get("scope")),
            started_at=float(data.get("started_at") or now_ts()),
            ended_at=float(data.get("ended_at") or now_ts()),
            source=str(data.get("source") or "episode_builder"),
            metadata=dict(data.get("metadata") or {}),
        )


@dataclass(slots=True)
class ProfileContextItem:
    path: str
    value: Any
    score: float
    reason: str
    evidence_ids: tuple[str, ...] = ()
    scope: UserMemoryScope = field(default_factory=UserMemoryScope)
    explicitness: Explicitness = "inferred"
    # Cách response model được phép dùng memory này. ``clarify`` không phải fact.
    use_mode: str = "direct"
    uncertainty_note: str = ""


@dataclass(slots=True)
class ProfileContextPacket:
    query: str
    domain: str
    items: tuple[ProfileContextItem, ...] = ()
    episodes: tuple[UserEpisode, ...] = ()
    # ActionContext để kiểu Any nhằm tránh vòng import models -> action_memory.
    action_context: Any = None
    clarifications: tuple[str, ...] = ()
    proactive_cue: str = ""
    token_budget: int = 900
    diagnostics: tuple[str, ...] = ()
    episode_recap: str = ""

    def render_prompt_context(self) -> str:
        has_actions = self.action_context is not None and bool(
            getattr(self.action_context, "goals", ())
            or getattr(self.action_context, "commitments", ())
            or getattr(self.action_context, "workflows", ())
        )
        if not self.items and not self.episodes and not has_actions and not self.proactive_cue and not self.episode_recap:
            return ""
        lines = [
            "USER CONTEXT (dùng để cá nhân hóa, không đọc lại như hồ sơ, không biến suy đoán thành sự thật):"
        ]
        if self.proactive_cue:
            lines.append(f"[PROACTIVE_MEMORY_NUDGE]\n{self.proactive_cue}")
        # Companion Consolidation: profile có thể nhớ self-report/inference về
        # kinh nghiệm, nhưng đó KHÔNG phải mastery evidence. Nếu không khóa ở đây,
        # writer có thể tự nhảy sang "user advanced" và bỏ qua AdaptiveCompanion.
        if any(_is_competence_profile_path(item.path) for item in self.items):
            lines.append(
                "COMPETENCE BOUNDARY: các claim về experience/skill trong USER CONTEXT chỉ là context; "
                "không được dùng để quyết định mức dạy, skip basics hay gắn nhãn trình độ. "
                "Nếu runtime có evidence-based skill model, model đó mới là authority cho competence."
            )
        for item in self.items:
            marker = "đã nói rõ" if item.explicitness == "explicit" else ("quan sát lặp lại" if item.explicitness == "observed" else "suy luận thận trọng")
            permission = {
                "direct": "có thể dùng trực tiếp",
                "hedged": "chỉ dùng với cách nói dè dặt",
                "clarify": "không coi là sự thật; xác nhận nếu cần",
                "conflict": "đang mâu thuẫn; không tự chọn",
            }.get(item.use_mode, "không tự mở rộng")
            lines.append(f"- {item.path}: {compact_text(item.value, 300)} [{marker}; {permission}; scope={item.scope.identity}]")
        if self.clarifications:
            lines.append("MEMORY UNCERTAINTY:")
            lines.extend(f"- {compact_text(note, 360)}" for note in self.clarifications)
        if self.episodes:
            from .episode_projection import relative_time
            lines.append("EPISODES LIÊN QUAN:")
            current_time = now_ts()
            for episode in self.episodes:
                lines.append(f"- [{relative_time(episode.ended_at, now=current_time)}] {episode.title or 'Phiên trước'}: {episode.summary}")
                if episode.outcome:
                    lines.append(f"  Kết quả ghi nhận: {episode.outcome}")
                # Chỉ hiển thị việc còn mở nếu episode diễn ra trong vòng 72 giờ qua (tránh lôi việc dang dở cũ mèm vào hiện tại)
                if episode.open_loops and (current_time - episode.started_at <= 72 * 3600):
                    lines.append(f"  Việc còn mở: {'; '.join(episode.open_loops[:3])}")
        if self.episode_recap:
            lines.append(f"[EPISODE_RECAP]\nKý ức khái quát, nguồn thứ cấp: {self.episode_recap}\n[/EPISODE_RECAP]")
        if self.action_context is not None:
            render_action = getattr(self.action_context, "render_prompt_context", None)
            action_text = str(render_action() or "") if callable(render_action) else ""
            if action_text:
                lines.append(action_text)
        return "\n".join(lines)


def _is_competence_profile_path(path: str) -> bool:
    """Nhận diện profile path mô tả năng lực để không lấn skill-evidence authority."""
    normalized = normalize_path(path)
    prefixes = (
        "technical.experience",
        "technical.skill",
        "technical.competence",
        "technical.proficiency",
        "skills.",
        "competence.",
        "learning.mastery",
    )
    return any(normalized == prefix.rstrip(".") or normalized.startswith(prefix) for prefix in prefixes)


@dataclass(slots=True)
class DreamReport:
    applied: int = 0
    rejected: int = 0
    operations: tuple[ProfileOperation, ...] = ()
    episode_ids: tuple[str, ...] = ()
    diagnostics: tuple[str, ...] = ()


def determine_memory_tier(
    path: str,
    explicitness: str = "inferred",
    recurrence: int = 1,
    value: Any = None,
) -> MemoryTier:
    """Xác định nấc lưu trữ: core_anchor (bất biến), habit (thói quen lặp lại), working (tạm thời)."""
    norm_p = normalize_path(path)

    # 1. Core Anchor: Bản sắc cốt lõi, quan hệ nền tảng với ERIS, danh tính creator, thỏa thuận chủ động rõ ràng
    if (
        norm_p.startswith(("identity.role", "identity.name", "identity.creator", "relationship.creator"))
        or any(k in norm_p for k in ("role_as_creator", "creator_relationship", "core_bond", "eris_creation"))
    ):
        return "core_anchor"
    if (
        explicitness == "explicit"
        and norm_p.startswith((
            "relationship.initiative_permission",
            "relationship.initiative",
            "relationship.interruption_tolerance",
            "communication.interruption_tolerance",
        ))
    ):
        return "core_anchor"

    # 2. Habit: Thói quen, phong cách giao tiếp, sở thích lặp lại bền vững, mức độ chịu làm phiền
    if (
        norm_p.startswith((
            "communication.preferred_language",
            "communication.tone",
            "communication.interruption_tolerance",
            "behavior.",
            "workflow.",
            "learning.style",
            "relationship.initiative",
            "relationship.interruption_tolerance",
            "relationship.boundaries",
        ))
        or (recurrence >= 2 and not norm_p.startswith(("state.", "context.", "recent_")))
    ):
        return "habit"

    # 3. Working: Tâm trạng nhất thời, ngữ cảnh phiên hiện tại, quan sát đơn lẻ
    return "working"


__all__ = [
    "Cardinality",
    "DreamReport",
    "EvidenceReference",
    "Explicitness",
    "MemoryTier",
    "OperationKind",
    "ProfileContextItem",
    "ProfileContextPacket",
    "ProfileOperation",
    "ProfileRecord",
    "ProfileStatus",
    "Sensitivity",
    "UserEpisode",
    "UserMemoryScope",
    "clamp01",
    "compact_text",
    "determine_memory_tier",
    "normalize_path",
    "now_ts",
    "value_fingerprint",
]
