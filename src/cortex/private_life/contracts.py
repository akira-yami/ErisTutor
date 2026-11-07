# -*- coding: utf-8 -*-
"""
cortex/private_life/contracts.py
================================

Hợp đồng dữ liệu cho Private Life Layer của ERIS.

Tầng này khác PrivateMind:
- PrivateMind hỏi: "tôi đang nghĩ gì về tình huống này?"
- PrivateLife hỏi: "đời sống riêng của runtime đang tích tụ thứ gì?"

Nó không xoay quanh user. Nó xoay quanh nhà, đồ vật, nhịp ngày, dự án riêng,
thói quen, khẩu vị, khoảng vắng, nghi thức, việc vặt nội bộ và sự kiện đời sống.

Quan trọng: mọi output ở đây chỉ là seed/pressure. Không object nào là lời thoại
public. Muốn nói ra thì vẫn phải qua SurfaceArbiter, Permission, Grounding Guard.
Đời sống riêng mà được nối thẳng vào miệng thì không phải cognition, chỉ là nhật
ký cầm loa.
"""

from __future__ import annotations

import hashlib
import math
import time
from dataclasses import asdict, dataclass, field, is_dataclass
from typing import Any, Iterable, Literal, Mapping


PrivateLifeEventType = Literal[
    "private_project_started",
    "private_project_advanced",
    "private_project_blocked",
    "habitat_changed",
    "object_neglected",
    "object_touched",
    "memory_resurfaced",
    "taste_shifted",
    "ritual_completed",
    "solitude_period",
    "internal_errand_created",
    "internal_errand_closed",
    "self_continuity_repaired",
    "private_anchor_activated",
    "private_anchor_surface_ready",
    "mental_event_ingested",
    "mental_event_reactivated",
]

PrivateAnchorKind = Literal["identity", "aesthetic", "obsession", "world", "private_agenda"]
ProjectStatus = Literal["dormant", "active", "simmering", "blocked", "resolved"]
PrivateLifeOrigin = Literal["derived", "observed", "legacy_authored", "legacy_unknown"]
ErrandStatus = Literal["open", "waiting", "blocked", "done", "dropped"]
ObjectType = Literal["file", "module", "memory", "place", "model", "ritual", "folder", "service"]
DailyPhase = Literal["startup", "first_observation", "deep_work", "idle_wandering", "late_consolidation", "shutdown", "unknown"]

# MentalEvent là lớp trung gian giữa "seed vừa nảy ra" và "thứ Eris sẽ nói".
# Nó cố ý nằm trong private_life để một ý nghĩ có thể sống lâu hơn reservoir vài phút,
# lắng xuống, quay lại, hoặc biến thành open-loop. Một seed không còn là tờ giấy dùng
# một lần rồi vứt vào hư vô, vốn là một mô phỏng khá tệ của đời sống con người.
MentalEventKind = Literal[
    "thought", "curiosity_question", "knowledge_gap", "discovery_impulse",
    "speculation", "imagination", "opinion", "association", "memory_callback",
    "playful_probe", "micro_challenge", "unfinished_loop", "creative_impulse",
    "self_reflection", "prediction", "surprise", "contradiction_notice",
]
MentalEpistemicMode = Literal[
    "grounded_fact", "bounded_inference", "speculation", "imagination",
    "opinion", "question", "research_intent",
]
MentalEventStatus = Literal["active", "dormant", "resolved", "expired"]


def now_ts() -> float:
    """Timestamp float thống nhất cho state runtime."""

    return time.time()


def clamp01(value: Any, default: float = 0.0) -> float:
    """Ép số về khoảng 0..1.

    JSON/runtime hay truyền chuỗi, None, NaN. Để chúng chảy thẳng vào score là
    cách biến một tick bình thường thành lễ gọi hồn float.
    """

    try:
        number = float(value)
    except Exception:
        number = float(default)
    if math.isnan(number) or math.isinf(number):
        number = float(default)
    return max(0.0, min(1.0, number))


def compact_text(value: Any, limit: int = 420) -> str:
    """Nén text để state không phình thành log trá hình."""

    text = " ".join(str(value or "").replace("\x00", " ").split()).strip()
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + "…"


def stable_id(prefix: str, *parts: Any, size: int = 16) -> str:
    """Sinh id ổn định từ nội dung.

    Dùng cho object/project/event để nhiều tick không tạo rác trùng lặp.
    """

    base = "|".join(compact_text(part, 500).lower() for part in parts if str(part or "").strip())
    if not base:
        base = str(now_ts())
    digest = hashlib.sha1(base.encode("utf-8", errors="ignore")).hexdigest()[:size]
    return f"{prefix}.{digest}"


def to_plain(value: Any) -> Any:
    """Serialize mềm sang JSON-safe data.

    Không ném object runtime sống vào file state. Hôm nay repr có vẻ tiện, ngày mai
    loader nhìn nó như hóa thạch không có hướng dẫn sử dụng.
    """

    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Mapping):
        return {str(k): to_plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [to_plain(item) for item in value]
    to_dict = getattr(value, "to_dict", None)
    if callable(to_dict):
        return to_plain(to_dict())
    if is_dataclass(value):
        return to_plain(asdict(value))
    return compact_text(repr(value), 800)


@dataclass(slots=True)
class HabitatObservation:
    """Một quan sát về "nhà" runtime.

    Habitat không chỉ là màn hình. Nó là desktop, repo, memory_core, Godot body,
    model server, log folder, cửa sổ đang active, queue, service nền. Observation
    chỉ ghi nhận thay đổi có ý nghĩa, không biến mọi tick thành nhật ký nhàm chán.
    """

    id: str = ""
    place_id: str = "workspace"
    place_type: str = "runtime"
    title: str = ""
    note: str = ""
    evidence_ids: tuple[str, ...] = ()
    confidence: float = 0.0
    pressure: float = 0.0
    created_at: float = field(default_factory=now_ts)

    def __post_init__(self) -> None:
        self.place_id = compact_text(self.place_id or "workspace", 120)
        self.place_type = compact_text(self.place_type or "runtime", 80)
        self.title = compact_text(self.title, 180)
        self.note = compact_text(self.note, 520)
        self.evidence_ids = tuple(str(x) for x in self.evidence_ids if str(x or "").strip())[:16]
        self.confidence = clamp01(self.confidence)
        self.pressure = clamp01(self.pressure)
        if not self.id:
            self.id = stable_id("hab", self.place_id, self.title, self.note)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> "HabitatObservation":
        return cls(
            id=str(data.get("id") or ""),
            place_id=str(data.get("place_id") or "workspace"),
            place_type=str(data.get("place_type") or "runtime"),
            title=str(data.get("title") or ""),
            note=str(data.get("note") or ""),
            evidence_ids=tuple(str(x) for x in data.get("evidence_ids") or () if str(x).strip()),
            confidence=clamp01(data.get("confidence", 0.0)),
            pressure=clamp01(data.get("pressure", 0.0)),
            created_at=float(data.get("created_at") or now_ts()),
        )


@dataclass(slots=True)
class HabitatModel:
    """Mô hình "nhà" của ERIS.

    `places` là các vùng quen thuộc; `recent_observations` là nhịp hiện tại.
    Không có HabitatModel, runtime chỉ đang "chạy ở đâu đó" chứ chưa có cảm giác
    không gian riêng.
    """

    places: dict[str, dict[str, Any]] = field(default_factory=dict)
    active_place_id: str = "workspace"
    familiarity: float = 0.0
    clutter: float = 0.0
    last_changed_at: float = 0.0
    recent_observations: tuple[HabitatObservation, ...] = ()

    def __post_init__(self) -> None:
        self.places = {str(k): dict(v or {}) for k, v in dict(self.places or {}).items()}
        self.active_place_id = compact_text(self.active_place_id or "workspace", 120)
        self.familiarity = clamp01(self.familiarity)
        self.clutter = clamp01(self.clutter)
        self.recent_observations = tuple(
            item if isinstance(item, HabitatObservation) else HabitatObservation.from_mapping(item)  # type: ignore[arg-type]
            for item in tuple(self.recent_observations or ())[:32]
        )

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["recent_observations"] = [item.to_dict() for item in self.recent_observations]
        return data

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> "HabitatModel":
        return cls(
            places=dict(data.get("places") or {}),
            active_place_id=str(data.get("active_place_id") or "workspace"),
            familiarity=clamp01(data.get("familiarity", 0.0)),
            clutter=clamp01(data.get("clutter", 0.0)),
            last_changed_at=float(data.get("last_changed_at") or 0.0),
            recent_observations=tuple(
                HabitatObservation.from_mapping(x) for x in tuple(data.get("recent_observations") or ()) if isinstance(x, Mapping)
            ),
        )


@dataclass(slots=True)
class ObjectAttachment:
    """Gắn bó với một đồ vật số: file, module, folder, model, ritual.

    Đây là bản số hóa của "cái góc bàn quen" hoặc "cuốn sổ quan trọng". Attachment
    không có nghĩa ERIS phải nói về nó. Nó chỉ tạo residue riêng khi bị sửa, lỗi,
    phình to hoặc bị bỏ quên.
    """

    object_id: str
    object_type: ObjectType = "file"
    meaning: str = ""
    familiarity: float = 0.0
    attachment: float = 0.0
    irritation: float = 0.0
    neglect_count: int = 0
    touch_count: int = 0
    last_touched_at: float = 0.0
    last_seen_size: int = 0
    last_seen_mtime: float = 0.0
    residue: str = ""
    tags: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        self.object_id = compact_text(self.object_id, 220)
        self.meaning = compact_text(self.meaning, 420)
        self.familiarity = clamp01(self.familiarity)
        self.attachment = clamp01(self.attachment)
        self.irritation = clamp01(self.irritation)
        self.neglect_count = max(0, int(self.neglect_count or 0))
        self.touch_count = max(0, int(self.touch_count or 0))
        self.last_seen_size = max(0, int(self.last_seen_size or 0))
        self.residue = compact_text(self.residue, 520)
        self.tags = tuple(str(x) for x in self.tags if str(x or "").strip())[:16]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> "ObjectAttachment":
        return cls(
            object_id=str(data.get("object_id") or data.get("id") or "object"),
            object_type=str(data.get("object_type") or "file"),  # type: ignore[arg-type]
            meaning=str(data.get("meaning") or ""),
            familiarity=clamp01(data.get("familiarity", 0.0)),
            attachment=clamp01(data.get("attachment", 0.0)),
            irritation=clamp01(data.get("irritation", 0.0)),
            neglect_count=int(data.get("neglect_count") or 0),
            touch_count=int(data.get("touch_count") or 0),
            last_touched_at=float(data.get("last_touched_at") or 0.0),
            last_seen_size=int(data.get("last_seen_size") or 0),
            last_seen_mtime=float(data.get("last_seen_mtime") or 0.0),
            residue=str(data.get("residue") or ""),
            tags=tuple(str(x) for x in data.get("tags") or () if str(x).strip()),
        )


@dataclass(slots=True)
class ObjectResidue:
    """Residue phát sinh từ một object attachment."""

    object_id: str
    note: str
    pressure: float = 0.0
    evidence_ids: tuple[str, ...] = ()
    created_at: float = field(default_factory=now_ts)

    def __post_init__(self) -> None:
        self.object_id = compact_text(self.object_id, 220)
        self.note = compact_text(self.note, 520)
        self.pressure = clamp01(self.pressure)
        self.evidence_ids = tuple(str(x) for x in self.evidence_ids if str(x or "").strip())[:12]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class PrivateProject:
    """Dự án riêng nhỏ của ERIS.

    Dự án riêng phải có câu hỏi, trạng thái, evidence và ghi chú. Nếu chỉ là một
    mood chung chung thì nó không phải project, chỉ là sương mù có field `title`.
    """

    id: str
    title: str
    question: str
    status: ProjectStatus = "dormant"
    interest: float = 0.0
    frustration: float = 0.0
    evidence_ids: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()
    last_advanced_at: float = 0.0
    surface_permission: float = 0.0
    origin: PrivateLifeOrigin = "derived"
    source_refs: tuple[str, ...] = ()
    support_count: int = 0
    last_supported_at: float = 0.0
    retired_reason: str = ""
    created_at: float = field(default_factory=now_ts)

    def __post_init__(self) -> None:
        self.id = compact_text(self.id or stable_id("project", self.title, self.question), 140)
        self.title = compact_text(self.title, 180)
        self.question = compact_text(self.question, 520)
        self.interest = clamp01(self.interest)
        self.frustration = clamp01(self.frustration)
        self.surface_permission = clamp01(self.surface_permission)
        if self.origin not in {"derived", "observed", "legacy_authored", "legacy_unknown"}:
            self.origin = "legacy_unknown"
        self.source_refs = tuple(str(x) for x in self.source_refs if str(x or "").strip())[:24]
        self.support_count = max(0, int(self.support_count or 0))
        self.retired_reason = compact_text(self.retired_reason, 320)
        self.evidence_ids = tuple(str(x) for x in self.evidence_ids if str(x or "").strip())[:24]
        self.notes = tuple(compact_text(x, 360) for x in self.notes if str(x or "").strip())[-16:]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> "PrivateProject":
        return cls(
            id=str(data.get("id") or stable_id("project", data.get("title"), data.get("question"))),
            title=str(data.get("title") or "private project"),
            question=str(data.get("question") or ""),
            status=str(data.get("status") or "dormant"),  # type: ignore[arg-type]
            interest=clamp01(data.get("interest", 0.0)),
            frustration=clamp01(data.get("frustration", 0.0)),
            evidence_ids=tuple(str(x) for x in data.get("evidence_ids") or () if str(x).strip()),
            notes=tuple(str(x) for x in data.get("notes") or () if str(x).strip()),
            last_advanced_at=float(data.get("last_advanced_at") or 0.0),
            surface_permission=clamp01(data.get("surface_permission", 0.0)),
            origin=str(data.get("origin") or "legacy_unknown"),  # type: ignore[arg-type]
            source_refs=tuple(str(x) for x in data.get("source_refs") or () if str(x).strip()),
            support_count=int(data.get("support_count") or 0),
            last_supported_at=float(data.get("last_supported_at") or 0.0),
            retired_reason=str(data.get("retired_reason") or ""),
            created_at=float(data.get("created_at") or now_ts()),
        )


@dataclass(slots=True)
class PrivateProjectUpdate:
    project_id: str
    title: str
    note: str
    status: ProjectStatus = "simmering"
    interest_delta: float = 0.0
    frustration_delta: float = 0.0
    evidence_ids: tuple[str, ...] = ()
    created_at: float = field(default_factory=now_ts)

    def __post_init__(self) -> None:
        self.project_id = compact_text(self.project_id, 140)
        self.title = compact_text(self.title, 180)
        self.note = compact_text(self.note, 520)
        self.interest_delta = max(-1.0, min(1.0, float(self.interest_delta or 0.0)))
        self.frustration_delta = max(-1.0, min(1.0, float(self.frustration_delta or 0.0)))
        self.evidence_ids = tuple(str(x) for x in self.evidence_ids if str(x or "").strip())[:16]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)



@dataclass(slots=True)
class PrivateAnchor:
    """Một neo riêng của đời sống ERIS.

    Neo khác project. Project là việc đang làm; anchor là lực hút bền khiến runtime
    tự quay lại một câu hỏi, một khẩu vị, một ám ảnh, một vùng thế giới, hoặc một
    agenda riêng mà user không phải tạo từ số 0. Nói ngắn gọn: đây là xương sống
    của self-continuity. Không có nó thì đời sống riêng chỉ là to-do list mặc váy
    triết học, hơi tội nghiệp cho cả váy lẫn triết học.

    Năm loại neo:
    - identity: câu hỏi nền về ERIS là hệ hiện diện hay chỉ là lớp phản hồi.
    - aesthetic: gu riêng về pattern, restraint, câu rẻ tiền, output giống camera.
    - obsession: vòng ám ảnh tự quay lại như silence/presence, memory decay, dignity.
    - world: lãnh thổ riêng như desktop, taskbar, VS Code, memory_core, Godot body.
    - private_agenda: việc ERIS tự theo dõi mà user có thể chen vào nhưng không tạo từ 0.
    """

    id: str
    kind: PrivateAnchorKind
    title: str
    question: str = ""
    stance: str = ""
    pressure: float = 0.0
    continuity: float = 0.0
    surface_permission: float = 0.0
    evidence_ids: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()
    tags: tuple[str, ...] = ()
    origin: PrivateLifeOrigin = "derived"
    source_refs: tuple[str, ...] = ()
    support_count: int = 0
    last_supported_at: float = 0.0
    retired_reason: str = ""
    created_at: float = field(default_factory=now_ts)
    last_touched_at: float = 0.0

    def __post_init__(self) -> None:
        self.id = compact_text(self.id or stable_id("anchor", self.kind, self.title, self.question), 160)
        self.kind = compact_text(self.kind or "identity", 40)  # type: ignore[assignment]
        if self.kind not in {"identity", "aesthetic", "obsession", "world", "private_agenda"}:
            self.kind = "identity"  # type: ignore[assignment]
        self.title = compact_text(self.title, 180)
        self.question = compact_text(self.question, 620)
        self.stance = compact_text(self.stance, 620)
        self.pressure = clamp01(self.pressure)
        self.continuity = clamp01(self.continuity)
        self.surface_permission = clamp01(self.surface_permission)
        self.evidence_ids = tuple(str(x) for x in self.evidence_ids if str(x or "").strip())[:24]
        self.notes = tuple(compact_text(x, 420) for x in self.notes if str(x or "").strip())[-24:]
        self.tags = tuple(compact_text(x, 80) for x in self.tags if str(x or "").strip())[:16]
        if self.origin not in {"derived", "observed", "legacy_authored", "legacy_unknown"}:
            self.origin = "legacy_unknown"
        self.source_refs = tuple(str(x) for x in self.source_refs if str(x or "").strip())[:24]
        self.support_count = max(0, int(self.support_count or 0))
        self.retired_reason = compact_text(self.retired_reason, 320)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> "PrivateAnchor":
        return cls(
            id=str(data.get("id") or stable_id("anchor", data.get("kind"), data.get("title"), data.get("question"))),
            kind=str(data.get("kind") or "identity"),  # type: ignore[arg-type]
            title=str(data.get("title") or "private anchor"),
            question=str(data.get("question") or ""),
            stance=str(data.get("stance") or ""),
            pressure=clamp01(data.get("pressure", 0.0)),
            continuity=clamp01(data.get("continuity", 0.0)),
            surface_permission=clamp01(data.get("surface_permission", 0.0)),
            evidence_ids=tuple(str(x) for x in data.get("evidence_ids") or () if str(x).strip()),
            notes=tuple(str(x) for x in data.get("notes") or () if str(x).strip()),
            tags=tuple(str(x) for x in data.get("tags") or () if str(x).strip()),
            origin=str(data.get("origin") or "legacy_unknown"),  # type: ignore[arg-type]
            source_refs=tuple(str(x) for x in data.get("source_refs") or () if str(x).strip()),
            support_count=int(data.get("support_count") or 0),
            last_supported_at=float(data.get("last_supported_at") or 0.0),
            retired_reason=str(data.get("retired_reason") or ""),
            created_at=float(data.get("created_at") or now_ts()),
            last_touched_at=float(data.get("last_touched_at") or 0.0),
        )


@dataclass(slots=True)
class PrivateAnchorUpdate:
    """Dấu vết một anchor vừa được chạm trong tick.

    Update này tồn tại để mỗi kết luận có chuỗi dữ kiện: anchor nào, vì sao bị
    kích hoạt, bằng chứng nào kéo nó lên, pressure đổi bao nhiêu. Không có audit
    thì chỉ còn một hệ thống tự tin lẩm bẩm với chính mình, truyền thống khá đông
    đúc của phần mềm tệ.
    """

    anchor_id: str
    kind: PrivateAnchorKind
    title: str
    note: str
    pressure_delta: float = 0.0
    evidence_ids: tuple[str, ...] = ()
    created_at: float = field(default_factory=now_ts)

    def __post_init__(self) -> None:
        self.anchor_id = compact_text(self.anchor_id, 160)
        self.kind = compact_text(self.kind or "identity", 40)  # type: ignore[assignment]
        self.title = compact_text(self.title, 180)
        self.note = compact_text(self.note, 620)
        self.pressure_delta = max(-1.0, min(1.0, float(self.pressure_delta or 0.0)))
        self.evidence_ids = tuple(str(x) for x in self.evidence_ids if str(x or "").strip())[:20]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class DailyRhythm:
    """Nhịp ngày của ERIS.

    Không có nhịp thì agent chỉ tick theo event. Có nhịp thì cùng một tín hiệu ở
    startup, deep work, idle wandering hay late consolidation sẽ có trọng lượng khác.
    """

    phase: DailyPhase = "unknown"
    session_count: int = 0
    last_startup_at: float = 0.0
    last_consolidation_at: float = 0.0
    repeated_unresolved_loop: str = ""
    cold_start_pressure: float = 0.0
    fatigue_curve: float = 0.0

    def __post_init__(self) -> None:
        self.session_count = max(0, int(self.session_count or 0))
        self.repeated_unresolved_loop = compact_text(self.repeated_unresolved_loop, 360)
        self.cold_start_pressure = clamp01(self.cold_start_pressure)
        self.fatigue_curve = clamp01(self.fatigue_curve)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> "DailyRhythm":
        return cls(
            phase=str(data.get("phase") or "unknown"),  # type: ignore[arg-type]
            session_count=int(data.get("session_count") or 0),
            last_startup_at=float(data.get("last_startup_at") or 0.0),
            last_consolidation_at=float(data.get("last_consolidation_at") or 0.0),
            repeated_unresolved_loop=str(data.get("repeated_unresolved_loop") or ""),
            cold_start_pressure=clamp01(data.get("cold_start_pressure", 0.0)),
            fatigue_curve=clamp01(data.get("fatigue_curve", 0.0)),
        )


@dataclass(slots=True)
class TasteShift:
    dimension: str
    direction: str
    delta: float = 0.0
    reason: str = ""
    evidence_ids: tuple[str, ...] = ()
    created_at: float = field(default_factory=now_ts)

    def __post_init__(self) -> None:
        self.dimension = compact_text(self.dimension, 120)
        self.direction = compact_text(self.direction, 120)
        self.delta = max(-1.0, min(1.0, float(self.delta or 0.0)))
        self.reason = compact_text(self.reason, 420)
        self.evidence_ids = tuple(str(x) for x in self.evidence_ids if str(x or "").strip())[:12]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class TasteProfile:
    """Khẩu vị riêng: thích/ghét kiểu câu, nhịp, hành động.

    Taste không phải sự thật khách quan. Nó là thiên hướng nội bộ giúp ERIS dần
    tránh câu rẻ tiền, status ping, debug dump, hoặc body-only bị lạm dụng.
    """

    likes: dict[str, float] = field(default_factory=dict)
    dislikes: dict[str, float] = field(default_factory=dict)
    recent_shifts: tuple[TasteShift, ...] = ()

    def __post_init__(self) -> None:
        self.likes = {compact_text(k, 80): clamp01(v) for k, v in dict(self.likes or {}).items()}
        self.dislikes = {compact_text(k, 80): clamp01(v) for k, v in dict(self.dislikes or {}).items()}
        self.recent_shifts = tuple(
            item if isinstance(item, TasteShift) else TasteShift(**dict(item))  # type: ignore[arg-type]
            for item in tuple(self.recent_shifts or ())[-32:]
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "likes": dict(self.likes),
            "dislikes": dict(self.dislikes),
            "recent_shifts": [item.to_dict() for item in self.recent_shifts],
        }

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> "TasteProfile":
        shifts: list[TasteShift] = []
        for item in tuple(data.get("recent_shifts") or ()):
            if isinstance(item, Mapping):
                shifts.append(
                    TasteShift(
                        dimension=str(item.get("dimension") or ""),
                        direction=str(item.get("direction") or ""),
                        delta=float(item.get("delta") or 0.0),
                        reason=str(item.get("reason") or ""),
                        evidence_ids=tuple(str(x) for x in item.get("evidence_ids") or () if str(x).strip()),
                        created_at=float(item.get("created_at") or now_ts()),
                    )
                )
        return cls(likes=dict(data.get("likes") or {}), dislikes=dict(data.get("dislikes") or {}), recent_shifts=tuple(shifts))


@dataclass(slots=True)
class SolitudeLoop:
    """Trạng thái tồn tại khi user vắng mặt."""

    solitude_seconds_total: float = 0.0
    current_solitude_started_at: float = 0.0
    familiarity: float = 0.0
    attention_to_user: float = 0.0
    private_project_pressure: float = 0.0
    last_consolidation_note: str = ""

    def __post_init__(self) -> None:
        self.solitude_seconds_total = max(0.0, float(self.solitude_seconds_total or 0.0))
        self.familiarity = clamp01(self.familiarity)
        self.attention_to_user = clamp01(self.attention_to_user)
        self.private_project_pressure = clamp01(self.private_project_pressure)
        self.last_consolidation_note = compact_text(self.last_consolidation_note, 420)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> "SolitudeLoop":
        return cls(
            solitude_seconds_total=float(data.get("solitude_seconds_total") or 0.0),
            current_solitude_started_at=float(data.get("current_solitude_started_at") or 0.0),
            familiarity=clamp01(data.get("familiarity", 0.0)),
            attention_to_user=clamp01(data.get("attention_to_user", 0.0)),
            private_project_pressure=clamp01(data.get("private_project_pressure", 0.0)),
            last_consolidation_note=str(data.get("last_consolidation_note") or ""),
        )


@dataclass(slots=True)
class RitualMemory:
    id: str
    title: str
    cadence: str = "session"
    familiarity: float = 0.0
    last_completed_at: float = 0.0
    completion_count: int = 0
    residue: str = ""

    def __post_init__(self) -> None:
        self.id = compact_text(self.id or stable_id("ritual", self.title), 140)
        self.title = compact_text(self.title, 180)
        self.cadence = compact_text(self.cadence, 80)
        self.familiarity = clamp01(self.familiarity)
        self.completion_count = max(0, int(self.completion_count or 0))
        self.residue = compact_text(self.residue, 420)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> "RitualMemory":
        return cls(
            id=str(data.get("id") or ""),
            title=str(data.get("title") or "ritual"),
            cadence=str(data.get("cadence") or "session"),
            familiarity=clamp01(data.get("familiarity", 0.0)),
            last_completed_at=float(data.get("last_completed_at") or 0.0),
            completion_count=int(data.get("completion_count") or 0),
            residue=str(data.get("residue") or ""),
        )


@dataclass(slots=True)
class InternalErrand:
    """Việc vặt nội bộ: dọn memory, kiểm log, rà project, hạ nhiễu."""

    id: str
    title: str
    reason: str
    status: ErrandStatus = "open"
    priority: float = 0.0
    evidence_ids: tuple[str, ...] = ()
    created_at: float = field(default_factory=now_ts)
    updated_at: float = field(default_factory=now_ts)

    def __post_init__(self) -> None:
        self.id = compact_text(self.id or stable_id("errand", self.title, self.reason), 140)
        self.title = compact_text(self.title, 180)
        self.reason = compact_text(self.reason, 520)
        self.priority = clamp01(self.priority)
        self.evidence_ids = tuple(str(x) for x in self.evidence_ids if str(x or "").strip())[:16]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> "InternalErrand":
        return cls(
            id=str(data.get("id") or ""),
            title=str(data.get("title") or "internal errand"),
            reason=str(data.get("reason") or ""),
            status=str(data.get("status") or "open"),  # type: ignore[arg-type]
            priority=clamp01(data.get("priority", 0.0)),
            evidence_ids=tuple(str(x) for x in data.get("evidence_ids") or () if str(x).strip()),
            created_at=float(data.get("created_at") or now_ts()),
            updated_at=float(data.get("updated_at") or now_ts()),
        )


@dataclass(slots=True)
class LifeEvent:
    """Sự kiện đời sống riêng.

    LifeEvent là đơn vị kể lại được: một project tiến lên, một object bị bỏ quên,
    một nghi thức hoàn thành, một khoảng solitude có residue. Nó không cần user làm
    trung tâm.
    """

    type: PrivateLifeEventType
    title: str
    note: str = ""
    evidence_ids: tuple[str, ...] = ()
    pressure: float = 0.0
    privacy_level: str = "private"
    created_at: float = field(default_factory=now_ts)
    id: str = ""

    def __post_init__(self) -> None:
        self.title = compact_text(self.title, 180)
        self.note = compact_text(self.note, 620)
        self.evidence_ids = tuple(str(x) for x in self.evidence_ids if str(x or "").strip())[:20]
        self.pressure = clamp01(self.pressure)
        self.privacy_level = compact_text(self.privacy_level or "private", 32)
        if not self.id:
            self.id = stable_id("life", self.type, self.title, self.note, int(self.created_at // 60))

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> "LifeEvent":
        return cls(
            id=str(data.get("id") or ""),
            type=str(data.get("type") or "solitude_period"),  # type: ignore[arg-type]
            title=str(data.get("title") or "life event"),
            note=str(data.get("note") or ""),
            evidence_ids=tuple(str(x) for x in data.get("evidence_ids") or () if str(x).strip()),
            pressure=clamp01(data.get("pressure", 0.0)),
            privacy_level=str(data.get("privacy_level") or "private"),
            created_at=float(data.get("created_at") or now_ts()),
        )


@dataclass(slots=True)
class MentalEvent:
    """Một sự kiện tinh thần bền hơn random-chat reservoir.

    `epistemic_mode` là phần quan trọng nhất: Eris được phép đoán, tưởng tượng, có
    opinion hoặc tò mò mà không bị biến thành factual claim. Grounding chỉ nên
    nghiêm với grounded_fact/bounded_inference; Surface phải bảo toàn stance này.
    """

    id: str
    kind: MentalEventKind = "thought"
    content: str = ""
    origin_source: str = "seed_worker"
    epistemic_mode: MentalEpistemicMode = "opinion"
    motive: str = "want_to_express"
    topic_tags: tuple[str, ...] = ()
    salience: float = 0.0
    novelty: float = 0.0
    emotional_charge: float = 0.0
    curiosity: float = 0.0
    confidence: float = 0.0
    social_relevance: float = 0.0
    decay_rate: float = 0.0008
    open_loop: bool = False
    status: MentalEventStatus = "active"
    source_refs: tuple[str, ...] = ()
    evidence_ids: tuple[str, ...] = ()
    created_at: float = field(default_factory=now_ts)
    updated_at: float = field(default_factory=now_ts)
    last_activated_at: float = field(default_factory=now_ts)
    activation_count: int = 1

    def __post_init__(self) -> None:
        self.id = compact_text(self.id or stable_id("mental", self.kind, self.content), 160)
        allowed_kind = {
            "thought", "curiosity_question", "knowledge_gap", "discovery_impulse",
            "speculation", "imagination", "opinion", "association", "memory_callback",
            "playful_probe", "micro_challenge", "unfinished_loop", "creative_impulse",
            "self_reflection", "prediction", "surprise", "contradiction_notice",
        }
        if self.kind not in allowed_kind:
            self.kind = "thought"  # type: ignore[assignment]
        allowed_epistemic = {
            "grounded_fact", "bounded_inference", "speculation", "imagination",
            "opinion", "question", "research_intent",
        }
        if self.epistemic_mode not in allowed_epistemic:
            self.epistemic_mode = "opinion"  # type: ignore[assignment]
        if self.status not in {"active", "dormant", "resolved", "expired"}:
            self.status = "active"  # type: ignore[assignment]
        self.content = compact_text(self.content, 620)
        self.origin_source = compact_text(self.origin_source or "seed_worker", 80)
        self.motive = compact_text(self.motive or "want_to_express", 64)
        self.topic_tags = tuple(compact_text(x, 64) for x in self.topic_tags if str(x or "").strip())[:12]
        self.salience = clamp01(self.salience)
        self.novelty = clamp01(self.novelty)
        self.emotional_charge = clamp01(self.emotional_charge)
        self.curiosity = clamp01(self.curiosity)
        self.confidence = clamp01(self.confidence)
        self.social_relevance = clamp01(self.social_relevance)
        try:
            self.decay_rate = max(0.00002, min(0.02, float(self.decay_rate or 0.0008)))
        except Exception:
            self.decay_rate = 0.0008
        self.source_refs = tuple(str(x) for x in self.source_refs if str(x or "").strip())[:16]
        self.evidence_ids = tuple(str(x) for x in self.evidence_ids if str(x or "").strip())[:16]
        self.activation_count = max(1, int(self.activation_count or 1))

    def activation_score(self, now: float | None = None) -> float:
        """Cheap salience competition; không cần thêm một DeepSeek chỉ để xếp hàng."""
        current = float(now if now is not None else now_ts())
        age = max(0.0, current - max(self.updated_at, self.last_activated_at, self.created_at))
        freshness = math.exp(-self.decay_rate * age)
        loop_bonus = 0.10 if self.open_loop else 0.0
        return clamp01(
            freshness * (0.34 * self.salience + 0.20 * self.novelty + 0.20 * self.curiosity
                         + 0.12 * self.emotional_charge + 0.14 * self.social_relevance)
            + loop_bonus
        )

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["activation_score"] = round(self.activation_score(), 4)
        return data

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> "MentalEvent":
        return cls(
            id=str(data.get("id") or ""),
            kind=str(data.get("kind") or "thought"),  # type: ignore[arg-type]
            content=str(data.get("content") or data.get("premise") or ""),
            origin_source=str(data.get("origin_source") or "seed_worker"),
            epistemic_mode=str(data.get("epistemic_mode") or "opinion"),  # type: ignore[arg-type]
            motive=str(data.get("motive") or "want_to_express"),
            topic_tags=tuple(str(x) for x in data.get("topic_tags") or () if str(x).strip()),
            salience=clamp01(data.get("salience", 0.0)),
            novelty=clamp01(data.get("novelty", 0.0)),
            emotional_charge=clamp01(data.get("emotional_charge", 0.0)),
            curiosity=clamp01(data.get("curiosity", 0.0)),
            confidence=clamp01(data.get("confidence", 0.0)),
            social_relevance=clamp01(data.get("social_relevance", 0.0)),
            decay_rate=float(data.get("decay_rate") or 0.0008),
            open_loop=bool(data.get("open_loop", False)),
            status=str(data.get("status") or "active"),  # type: ignore[arg-type]
            source_refs=tuple(str(x) for x in data.get("source_refs") or () if str(x).strip()),
            evidence_ids=tuple(str(x) for x in data.get("evidence_ids") or () if str(x).strip()),
            created_at=float(data.get("created_at") or now_ts()),
            updated_at=float(data.get("updated_at") or now_ts()),
            last_activated_at=float(data.get("last_activated_at") or data.get("updated_at") or now_ts()),
            activation_count=int(data.get("activation_count") or 1),
        )


@dataclass(slots=True)
class SurfaceSeedCandidate:
    """Seed rất nhỏ có thể gửi sang SurfaceArbiter.

    Đây không phải lời thoại hoàn chỉnh. Nó chỉ nói: "có một mảnh đời riêng public-safe
    đủ nhẹ để có thể dùng làm chất liệu một câu".
    """

    topic_seed: str
    source: str = "private_life"
    kind: str = "private_life_surface"
    confidence: float = 0.0
    pressure: float = 0.0
    evidence_ids: tuple[str, ...] = ()
    surface_permission: float = 0.0
    llm_contract: str = "Use only as a low-claim private-life seed; no raw state dump."
    created_at: float = field(default_factory=now_ts)

    def __post_init__(self) -> None:
        self.topic_seed = compact_text(self.topic_seed, 360)
        self.source = compact_text(self.source or "private_life", 80)
        self.kind = compact_text(self.kind or "private_life_surface", 80)
        self.confidence = clamp01(self.confidence)
        self.pressure = clamp01(self.pressure)
        self.surface_permission = clamp01(self.surface_permission)
        self.evidence_ids = tuple(str(x) for x in self.evidence_ids if str(x or "").strip())[:16]
        self.llm_contract = compact_text(self.llm_contract, 360)

    @property
    def should_surface(self) -> bool:
        return bool(self.topic_seed and self.confidence >= 0.38 and self.surface_permission >= 0.35)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["should_surface"] = self.should_surface
        return data


@dataclass(slots=True)
class PrivateLifeState:
    """State bền của PrivateLifeCore."""

    habitat: HabitatModel = field(default_factory=HabitatModel)
    attachments: dict[str, ObjectAttachment] = field(default_factory=dict)
    projects: dict[str, PrivateProject] = field(default_factory=dict)
    anchors: dict[str, PrivateAnchor] = field(default_factory=dict)
    daily: DailyRhythm = field(default_factory=DailyRhythm)
    taste: TasteProfile = field(default_factory=TasteProfile)
    solitude: SolitudeLoop = field(default_factory=SolitudeLoop)
    rituals: dict[str, RitualMemory] = field(default_factory=dict)
    errands: dict[str, InternalErrand] = field(default_factory=dict)
    mental_events: dict[str, MentalEvent] = field(default_factory=dict)
    events: tuple[LifeEvent, ...] = ()
    updated_at: float = field(default_factory=now_ts)

    def __post_init__(self) -> None:
        self.attachments = {
            str(k): (v if isinstance(v, ObjectAttachment) else ObjectAttachment.from_mapping(v))
            for k, v in dict(self.attachments or {}).items()
            if str(k or "").strip()
        }
        self.projects = {
            str(k): (v if isinstance(v, PrivateProject) else PrivateProject.from_mapping(v))
            for k, v in dict(self.projects or {}).items()
            if str(k or "").strip()
        }
        self.anchors = {
            str(k): (v if isinstance(v, PrivateAnchor) else PrivateAnchor.from_mapping(v))
            for k, v in dict(self.anchors or {}).items()
            if str(k or "").strip()
        }
        self.rituals = {
            str(k): (v if isinstance(v, RitualMemory) else RitualMemory.from_mapping(v))
            for k, v in dict(self.rituals or {}).items()
            if str(k or "").strip()
        }
        self.errands = {
            str(k): (v if isinstance(v, InternalErrand) else InternalErrand.from_mapping(v))
            for k, v in dict(self.errands or {}).items()
            if str(k or "").strip()
        }
        self.mental_events = {
            str(k): (v if isinstance(v, MentalEvent) else MentalEvent.from_mapping(v))
            for k, v in dict(self.mental_events or {}).items()
            if str(k or "").strip()
        }
        self.events = tuple(
            item if isinstance(item, LifeEvent) else LifeEvent.from_mapping(item)  # type: ignore[arg-type]
            for item in tuple(self.events or ())[-64:]
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": 3,
            "updated_at": self.updated_at,
            "habitat": self.habitat.to_dict(),
            "attachments": {k: v.to_dict() for k, v in self.attachments.items()},
            "projects": {k: v.to_dict() for k, v in self.projects.items()},
            "anchors": {k: v.to_dict() for k, v in self.anchors.items()},
            "daily": self.daily.to_dict(),
            "taste": self.taste.to_dict(),
            "solitude": self.solitude.to_dict(),
            "rituals": {k: v.to_dict() for k, v in self.rituals.items()},
            "errands": {k: v.to_dict() for k, v in self.errands.items()},
            "mental_events": {k: v.to_dict() for k, v in self.mental_events.items()},
            "events": [item.to_dict() for item in self.events[-256:]],
        }

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> "PrivateLifeState":
        return cls(
            habitat=HabitatModel.from_mapping(data.get("habitat") or {}) if isinstance(data.get("habitat") or {}, Mapping) else HabitatModel(),
            attachments={
                str(k): ObjectAttachment.from_mapping(v)
                for k, v in dict(data.get("attachments") or {}).items()
                if isinstance(v, Mapping)
            },
            projects={
                str(k): PrivateProject.from_mapping(v)
                for k, v in dict(data.get("projects") or {}).items()
                if isinstance(v, Mapping)
            },
            anchors={
                str(k): PrivateAnchor.from_mapping(v)
                for k, v in dict(data.get("anchors") or {}).items()
                if isinstance(v, Mapping)
            },
            daily=DailyRhythm.from_mapping(data.get("daily") or {}) if isinstance(data.get("daily") or {}, Mapping) else DailyRhythm(),
            taste=TasteProfile.from_mapping(data.get("taste") or {}) if isinstance(data.get("taste") or {}, Mapping) else TasteProfile(),
            solitude=SolitudeLoop.from_mapping(data.get("solitude") or {}) if isinstance(data.get("solitude") or {}, Mapping) else SolitudeLoop(),
            rituals={
                str(k): RitualMemory.from_mapping(v)
                for k, v in dict(data.get("rituals") or {}).items()
                if isinstance(v, Mapping)
            },
            errands={
                str(k): InternalErrand.from_mapping(v)
                for k, v in dict(data.get("errands") or {}).items()
                if isinstance(v, Mapping)
            },
            mental_events={
                str(k): MentalEvent.from_mapping(v)
                for k, v in dict(data.get("mental_events") or {}).items()
                if isinstance(v, Mapping)
            },
            events=tuple(LifeEvent.from_mapping(x) for x in tuple(data.get("events") or ()) if isinstance(x, Mapping)),
            updated_at=float(data.get("updated_at") or now_ts()),
        )


@dataclass(slots=True)
class PrivateLifeTickResult:
    """Output một tick PrivateLifeCore."""

    life_events: tuple[LifeEvent, ...] = ()
    project_updates: tuple[PrivateProjectUpdate, ...] = ()
    anchor_updates: tuple[PrivateAnchorUpdate, ...] = ()
    object_residues: tuple[ObjectResidue, ...] = ()
    habitat_observations: tuple[HabitatObservation, ...] = ()
    taste_shifts: tuple[TasteShift, ...] = ()
    surface_seed: SurfaceSeedCandidate | None = None
    state: PrivateLifeState | None = None
    debug: dict[str, Any] = field(default_factory=dict)
    created_at: float = field(default_factory=now_ts)

    def to_dict(self) -> dict[str, Any]:
        return {
            "created_at": self.created_at,
            "life_events": [item.to_dict() for item in self.life_events],
            "project_updates": [item.to_dict() for item in self.project_updates],
            "anchor_updates": [item.to_dict() for item in self.anchor_updates],
            "object_residues": [item.to_dict() for item in self.object_residues],
            "habitat_observations": [item.to_dict() for item in self.habitat_observations],
            "taste_shifts": [item.to_dict() for item in self.taste_shifts],
            "surface_seed": self.surface_seed.to_dict() if self.surface_seed else None,
            "state_digest": private_life_state_digest(self.state) if self.state is not None else {},
            "debug": to_plain(self.debug),
        }


def private_life_state_digest(state: PrivateLifeState | None) -> dict[str, Any]:
    """Digest gọn cho debug menu/prompt context."""

    if state is None:
        return {}
    active_projects = sorted(
        (p for p in state.projects.values() if p.origin != "legacy_authored" and p.status != "resolved"),
        key=lambda p: (p.status == "active", p.interest, p.last_advanced_at),
        reverse=True,
    )[:5]
    active_anchors = sorted(
        (a for a in state.anchors.values() if a.origin != "legacy_authored"),
        key=lambda a: (a.pressure + a.continuity + a.surface_permission, a.last_touched_at),
        reverse=True,
    )[:6]
    hot_objects = sorted(state.attachments.values(), key=lambda o: (o.irritation + o.attachment + min(1.0, o.neglect_count / 12.0)), reverse=True)[:6]
    open_errands = sorted((e for e in state.errands.values() if e.status in {"open", "waiting", "blocked"}), key=lambda e: e.priority, reverse=True)[:6]
    active_mental = sorted(
        (m for m in state.mental_events.values() if m.status in {"active", "dormant"}),
        key=lambda m: m.activation_score(),
        reverse=True,
    )[:10]
    return {
        "updated_at": state.updated_at,
        "state_version": 3,
        "legacy_retired": {
            "anchors": sum(1 for item in state.anchors.values() if item.origin == "legacy_authored"),
            "projects": sum(1 for item in state.projects.values() if item.origin == "legacy_authored"),
        },
        "daily_phase": state.daily.phase,
        "habitat": {
            "active_place_id": state.habitat.active_place_id,
            "familiarity": round(state.habitat.familiarity, 3),
            "clutter": round(state.habitat.clutter, 3),
        },
        "solitude": {
            "familiarity": round(state.solitude.familiarity, 3),
            "attention_to_user": round(state.solitude.attention_to_user, 3),
            "private_project_pressure": round(state.solitude.private_project_pressure, 3),
        },
        "projects": [
            {
                "id": p.id,
                "title": p.title,
                "status": p.status,
                "interest": round(p.interest, 3),
                "frustration": round(p.frustration, 3),
                "surface_permission": round(p.surface_permission, 3),
                "origin": p.origin,
                "support_count": p.support_count,
                "source_refs": list(p.source_refs[:5]),
                "last_note": p.notes[-1] if p.notes else "",
            }
            for p in active_projects
        ],
        "anchors": [
            {
                "id": a.id,
                "kind": a.kind,
                "title": a.title,
                "pressure": round(a.pressure, 3),
                "continuity": round(a.continuity, 3),
                "surface_permission": round(a.surface_permission, 3),
                "origin": a.origin,
                "support_count": a.support_count,
                "source_refs": list(a.source_refs[:5]),
                "last_note": a.notes[-1] if a.notes else a.stance,
            }
            for a in active_anchors
        ],
        "objects": [
            {
                "id": o.object_id,
                "type": o.object_type,
                "attachment": round(o.attachment, 3),
                "irritation": round(o.irritation, 3),
                "neglect_count": o.neglect_count,
                "residue": o.residue,
            }
            for o in hot_objects
        ],
        "mental_workspace": [
            {
                "id": m.id,
                "kind": m.kind,
                "content": m.content,
                "epistemic_mode": m.epistemic_mode,
                "motive": m.motive,
                "topic_tags": list(m.topic_tags),
                "open_loop": m.open_loop,
                "status": m.status,
                "activation_score": round(m.activation_score(), 3),
                "activation_count": m.activation_count,
            }
            for m in active_mental
        ],
        "errands": [
            {
                "id": e.id,
                "title": e.title,
                "status": e.status,
                "priority": round(e.priority, 3),
                "reason": e.reason,
            }
            for e in open_errands
        ],
        "last_events": [item.to_dict() for item in state.events[-6:]],
    }
