from __future__ import annotations

"""
Các schema nền cho hệ memory của ERIS.

File này chỉ định nghĩa hình dạng dữ liệu. Nó KHÔNG tự quyết định có nên lưu
một ký ức hay không. Quyền quyết định đó thuộc StateCommitter và
MemoryCognitionCore. Nếu schema tự ý làm policy, hệ thống sẽ có thêm một
đường ghi nhớ ngầm, và đó là loại lỗi kiến trúc rất biết cách phá repo trong
im lặng.
"""

import time
import uuid
from dataclasses import asdict, dataclass, field, fields, is_dataclass
from enum import Enum
from typing import Any, Mapping, MutableMapping


SCHEMA_VERSION = 1


def now_iso() -> str:
    """Trả timestamp ISO đơn giản, có timezone local."""

    return time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime())


def make_memory_id(prefix: str) -> str:
    """Tạo id ngắn nhưng đủ ổn định cho record memory."""

    clean_prefix = "".join(ch for ch in str(prefix or "mem").lower() if ch.isalnum() or ch in {"_", "-"})
    clean_prefix = clean_prefix.strip("_- ") or "mem"
    return f"{clean_prefix}_{uuid.uuid4().hex[:16]}"


def clamp01(value: Any, *, default: float = 0.0) -> float:
    """Ép một giá trị về khoảng [0, 1]. Dữ liệu memory không cần drama số học."""

    try:
        number = float(value)
    except (TypeError, ValueError):
        number = float(default)
    return max(0.0, min(1.0, number))


def _tuple_str(value: Any) -> tuple[str, ...]:
    """Chuẩn hóa list/tuple/set/string về tuple[str, ...]."""

    if value is None:
        return ()
    if isinstance(value, str):
        return (value,) if value else ()
    if isinstance(value, (list, tuple, set, frozenset)):
        return tuple(str(item) for item in value if str(item))
    return (str(value),) if str(value) else ()


def _dict_value(value: Any) -> dict[str, Any]:
    """Chuẩn hóa một object mapping thành dict thường."""

    if isinstance(value, Mapping):
        return dict(value)
    return {}


class MemoryTarget(str, Enum):
    """Các đích commit mà MemoryCognitionCore có thể route tới."""

    EVENT_LEDGER = "event_ledger"
    ACTIVE_STATE = "active_state"
    EPISODIC_MEMORY = "episodic_memory"
    SEMANTIC_MEMORY = "semantic_memory"
    RELATIONAL_MEMORY = "relational_memory"
    AFFECTIVE_RESIDUE_MEMORY = "affective_residue_memory"
    PROCEDURAL_MEMORY = "procedural_memory"
    REPAIR_MEMORY = "repair_memory"

    # Các target mở rộng trong PDF/roadmap. Chưa module nào bắt buộc phải dùng ngay,
    # nhưng schema nên biết trước để refactor sau không phải đập lại enum.
    NORM_MEMORY = "norm_memory"
    GOAL_MEMORY = "goal_memory"
    WORLDVIEW_MEMORY = "worldview_memory"
    BELIEF_CORE = "belief_core"
    SOCIOCULTURAL_MEMORY = "sociocultural_memory"
    SPATIAL_MEMORY = "spatial_memory"
    TEMPORAL_RHYTHM_MEMORY = "temporal_rhythm_memory"
    BODY_RHYTHM_MEMORY = "body_rhythm_memory"
    TOM_HISTORY = "tom_history"
    HOMEOSTATIC_BASELINE = "homeostatic_baseline"
    SELF_CONTINUITY = "self_continuity"


class DecayPolicy(str, Enum):
    """Chính sách mục rữa của memory."""

    NONE = "none"
    SLOW = "slow"
    NORMAL = "normal"
    FAST = "fast"
    SESSION_ONLY = "session_only"


class PrivacyLevel(str, Enum):
    """Mức riêng tư của evidence/memory."""

    PUBLIC = "public"
    NORMAL = "normal"
    PRIVATE = "private"
    SENSITIVE = "sensitive"


class MemoryStatus(str, Enum):
    """Trạng thái sống của một record memory."""

    ACTIVE = "active"
    TENTATIVE = "tentative"
    CONTRADICTED = "contradicted"
    REJECTED = "rejected"
    ARCHIVED = "archived"


class CommitOutcome(str, Enum):
    """Kết quả xử lý một StateCommitRequest."""

    STORED = "stored"
    TENTATIVE = "tentative"
    REJECTED = "rejected"
    SKIPPED = "skipped"
    ROUTED = "routed"
    ERROR = "error"


@dataclass
class CommitDecision:
    """Audit result sau khi StateCommitter/MemoryCognitionCore xử lý một request."""

    request_id: str = ""
    target: str = ""
    outcome: str = CommitOutcome.SKIPPED.value
    reason: str = ""
    stored_id: str = ""
    confidence: float = 0.0
    timestamp: str = field(default_factory=now_iso)
    warnings: tuple[str, ...] = field(default_factory=tuple)
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.confidence = clamp01(self.confidence)
        self.warnings = _tuple_str(self.warnings)
        self.metadata = _dict_value(self.metadata)


@dataclass
class MemoryRecordBase:
    """Base record cho các memory đã qua commit protocol."""

    id: str = ""
    content: str = ""
    confidence: float = 0.0
    evidence_ids: tuple[str, ...] = field(default_factory=tuple)
    created_at: str = field(default_factory=now_iso)
    updated_at: str = field(default_factory=now_iso)
    source: str = "state_commit"
    status: str = MemoryStatus.ACTIVE.value
    privacy_level: str = PrivacyLevel.NORMAL.value
    decay_policy: str = DecayPolicy.NORMAL.value
    tags: tuple[str, ...] = field(default_factory=tuple)
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.id:
            self.id = make_memory_id(self.__class__.__name__.replace("MemoryRecord", "").lower() or "memory")
        self.content = str(self.content or "")
        self.confidence = clamp01(self.confidence)
        self.evidence_ids = _tuple_str(self.evidence_ids)
        self.tags = _tuple_str(self.tags)
        self.metadata = _dict_value(self.metadata)

    def mark_tentative(self, reason: str = "") -> None:
        """Đánh dấu record là tentative, thường dùng khi confidence thấp nhưng chưa đáng bỏ."""

        self.status = MemoryStatus.TENTATIVE.value
        if reason:
            self.metadata = {**self.metadata, "tentative_reason": reason}
        self.updated_at = now_iso()

    def touch(self, *, confidence: float | None = None, evidence_ids: tuple[str, ...] | None = None) -> None:
        """Cập nhật timestamp và có thể merge confidence/evidence."""

        self.updated_at = now_iso()
        if confidence is not None:
            self.confidence = max(self.confidence, clamp01(confidence))
        if evidence_ids:
            self.evidence_ids = tuple(dict.fromkeys((*self.evidence_ids, *_tuple_str(evidence_ids))))


@dataclass
class EventLedgerEntry:
    """Sự kiện thô hoặc gần-thô. Ledger là append-only, không rewrite quá khứ."""

    id: str = field(default_factory=lambda: make_memory_id("event"))
    frame_id: str = ""
    source: str = "unknown"
    content: str = ""
    evidence_ids: tuple[str, ...] = field(default_factory=tuple)
    timestamp: str = field(default_factory=now_iso)
    reliability: float = 1.0
    privacy_level: str = PrivacyLevel.NORMAL.value
    tags: tuple[str, ...] = field(default_factory=tuple)
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.content = str(self.content or "")
        self.evidence_ids = _tuple_str(self.evidence_ids)
        self.reliability = clamp01(self.reliability, default=1.0)
        self.tags = _tuple_str(self.tags)
        self.metadata = _dict_value(self.metadata)


@dataclass
class ActiveStateSnapshot:
    """Snapshot overwrite cho trạng thái đang là của runtime."""

    updated_at: str = field(default_factory=now_iso)
    current_goal: str = ""
    session_arc: str = "unknown"
    active_project: str = ""
    active_task: str = ""
    relation_state: dict[str, Any] = field(default_factory=dict)
    affect_state: dict[str, Any] = field(default_factory=dict)
    homeostasis: dict[str, Any] = field(default_factory=dict)
    working_threads: tuple[str, ...] = field(default_factory=tuple)
    open_questions: tuple[str, ...] = field(default_factory=tuple)
    self_continuity: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.relation_state = _dict_value(self.relation_state)
        self.affect_state = _dict_value(self.affect_state)
        self.homeostasis = _dict_value(self.homeostasis)
        self.working_threads = _tuple_str(self.working_threads)
        self.open_questions = _tuple_str(self.open_questions)
        self.self_continuity = _dict_value(self.self_continuity)
        self.metadata = _dict_value(self.metadata)


@dataclass
class EpisodicMemoryRecord(MemoryRecordBase):
    """Memory về một chuyện cụ thể đã xảy ra."""

    frame_id: str = ""
    scene: str = ""
    user_input: str = ""
    eris_action: str = ""
    outcome: str = ""
    salience: float = 0.0

    def __post_init__(self) -> None:
        super().__post_init__()
        self.salience = clamp01(self.salience)


@dataclass
class SemanticMemoryRecord(MemoryRecordBase):
    """Fact ổn định hoặc gần ổn định. Nếu chưa chắc, status phải là tentative."""

    domain: str = "general"
    observations: tuple[str, ...] = field(default_factory=tuple)
    contradiction_ids: tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        super().__post_init__()
        self.observations = _tuple_str(self.observations)
        self.contradiction_ids = _tuple_str(self.contradiction_ids)


@dataclass
class RelationalMemoryRecord(MemoryRecordBase):
    """Pattern quan hệ/tương tác tích tụ qua nhiều turn."""

    pattern: str = ""
    valence: float = 0.0
    intensity: float = 0.0
    source_event_ids: tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        super().__post_init__()
        self.valence = max(-1.0, min(1.0, float(self.valence or 0.0)))
        self.intensity = clamp01(self.intensity)
        self.source_event_ids = _tuple_str(self.source_event_ids)


@dataclass
class AffectiveResidueRecord(MemoryRecordBase):
    """Dư âm cảm xúc như pressure nội bộ, không phải lời tuyên bố cảm xúc công khai."""

    residue_type: str = "unspecified"
    intensity: float = 0.0
    valence: float = 0.0
    source_event_ids: tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        super().__post_init__()
        self.intensity = clamp01(self.intensity)
        self.valence = max(-1.0, min(1.0, float(self.valence or 0.0)))
        self.source_event_ids = _tuple_str(self.source_event_ids)


@dataclass
class ProceduralMemoryRecord(MemoryRecordBase):
    """Luật xử lý đã học: gặp trigger X thì nên xử lý bằng cách Y."""

    trigger: str = ""
    handling_rule: str = ""
    success_count: int = 0
    failure_count: int = 0

    def __post_init__(self) -> None:
        super().__post_init__()
        self.success_count = max(0, int(self.success_count or 0))
        self.failure_count = max(0, int(self.failure_count or 0))


@dataclass
class RepairMemoryRecord(MemoryRecordBase):
    """Memory sửa lỗi: user correction, wrong assumption, suppression rule lần sau."""

    failure_type: str = "unknown"
    trigger_context: str = ""
    wrong_assumption: str = ""
    user_correction: str = ""
    repair_strategy: str = ""
    future_suppression_rule: str = ""


@dataclass
class MemoryPacket:
    """Gói recall đã được cắt budget để downstream dùng, thay vì nhét cả database vào prompt."""

    episodic: tuple[str, ...] = field(default_factory=tuple)
    semantic: tuple[str, ...] = field(default_factory=tuple)
    relational: tuple[str, ...] = field(default_factory=tuple)
    affective_residue: tuple[str, ...] = field(default_factory=tuple)
    procedural: tuple[str, ...] = field(default_factory=tuple)
    temporal: tuple[str, ...] = field(default_factory=tuple)
    spatial: tuple[str, ...] = field(default_factory=tuple)
    worldview: tuple[str, ...] = field(default_factory=tuple)
    confidence_notes: tuple[str, ...] = field(default_factory=tuple)
    token_budget: int = 1200

    def __post_init__(self) -> None:
        for name in (
            "episodic",
            "semantic",
            "relational",
            "affective_residue",
            "procedural",
            "temporal",
            "spatial",
            "worldview",
            "confidence_notes",
        ):
            setattr(self, name, _tuple_str(getattr(self, name)))
        self.token_budget = max(0, int(self.token_budget or 0))


@dataclass
class MemoryStateBundle:
    """Một bundle JSON đầy đủ cho persistence.

    Các store riêng có thể dùng toàn bộ bundle hoặc chỉ đọc collection mình cần.
    """

    schema_version: int = SCHEMA_VERSION
    updated_at: str = field(default_factory=now_iso)
    event_ledger: tuple[EventLedgerEntry, ...] = field(default_factory=tuple)
    active_state: ActiveStateSnapshot = field(default_factory=ActiveStateSnapshot)
    episodic_memory: tuple[EpisodicMemoryRecord, ...] = field(default_factory=tuple)
    semantic_memory: tuple[SemanticMemoryRecord, ...] = field(default_factory=tuple)
    relational_memory: tuple[RelationalMemoryRecord, ...] = field(default_factory=tuple)
    affective_residue_memory: tuple[AffectiveResidueRecord, ...] = field(default_factory=tuple)
    procedural_memory: tuple[ProceduralMemoryRecord, ...] = field(default_factory=tuple)
    repair_memory: tuple[RepairMemoryRecord, ...] = field(default_factory=tuple)
    commit_audit: tuple[CommitDecision, ...] = field(default_factory=tuple)
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.schema_version = int(self.schema_version or SCHEMA_VERSION)
        self.event_ledger = _coerce_record_tuple(self.event_ledger, EventLedgerEntry)
        self.active_state = coerce_dataclass(ActiveStateSnapshot, self.active_state)
        self.episodic_memory = _coerce_record_tuple(self.episodic_memory, EpisodicMemoryRecord)
        self.semantic_memory = _coerce_record_tuple(self.semantic_memory, SemanticMemoryRecord)
        self.relational_memory = _coerce_record_tuple(self.relational_memory, RelationalMemoryRecord)
        self.affective_residue_memory = _coerce_record_tuple(self.affective_residue_memory, AffectiveResidueRecord)
        self.procedural_memory = _coerce_record_tuple(self.procedural_memory, ProceduralMemoryRecord)
        self.repair_memory = _coerce_record_tuple(self.repair_memory, RepairMemoryRecord)
        self.commit_audit = _coerce_record_tuple(self.commit_audit, CommitDecision)
        self.metadata = _dict_value(self.metadata)

    def with_updated_timestamp(self) -> "MemoryStateBundle":
        """Cập nhật timestamp in-place rồi trả chính bundle để tiện chain."""

        self.updated_at = now_iso()
        return self


def coerce_dataclass(cls: type[Any], value: Any) -> Any:
    """Ép dict/dataclass/object về dataclass cls, bỏ qua field lạ.

    Persistence cần chịu được schema cũ. Nếu mỗi lần thêm field lại nổ load JSON,
    đó không phải strict typing, đó là tự phá dữ liệu với vẻ mặt nghiêm túc.
    """

    if isinstance(value, cls):
        return value
    if is_dataclass(value):
        value = asdict(value)
    if not isinstance(value, Mapping):
        return cls()
    allowed = {item.name for item in fields(cls)}
    filtered = {key: val for key, val in dict(value).items() if key in allowed}
    return cls(**filtered)


def _coerce_record_tuple(value: Any, cls: type[Any]) -> tuple[Any, ...]:
    if value is None:
        return ()
    if isinstance(value, Mapping):
        # Một số legacy store lưu map id -> record. Chấp nhận luôn, khỏi làm mình làm mẩy.
        items = value.values()
    elif isinstance(value, (list, tuple, set, frozenset)):
        items = value
    else:
        items = (value,)
    return tuple(coerce_dataclass(cls, item) for item in items)


def to_plain_data(value: Any) -> Any:
    """Chuyển dataclass/enum/tuple thành dữ liệu JSON-friendly."""

    if isinstance(value, Enum):
        return value.value
    if is_dataclass(value):
        return {key: to_plain_data(val) for key, val in asdict(value).items()}
    if isinstance(value, Mapping):
        return {str(key): to_plain_data(val) for key, val in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [to_plain_data(item) for item in value]
    return value


def bundle_from_mapping(data: Mapping[str, Any] | None) -> MemoryStateBundle:
    """Tạo MemoryStateBundle từ dict thô đã load từ JSON."""

    if not isinstance(data, Mapping):
        return MemoryStateBundle()
    allowed = {item.name for item in fields(MemoryStateBundle)}
    filtered: MutableMapping[str, Any] = {key: value for key, value in dict(data).items() if key in allowed}
    return MemoryStateBundle(**filtered)


def bundle_to_mapping(bundle: MemoryStateBundle) -> dict[str, Any]:
    """Chuyển MemoryStateBundle về dict JSON-friendly."""

    return to_plain_data(bundle.with_updated_timestamp())


COLLECTION_BY_TARGET: dict[str, str] = {
    MemoryTarget.EVENT_LEDGER.value: "event_ledger",
    MemoryTarget.EPISODIC_MEMORY.value: "episodic_memory",
    MemoryTarget.SEMANTIC_MEMORY.value: "semantic_memory",
    MemoryTarget.RELATIONAL_MEMORY.value: "relational_memory",
    MemoryTarget.AFFECTIVE_RESIDUE_MEMORY.value: "affective_residue_memory",
    MemoryTarget.PROCEDURAL_MEMORY.value: "procedural_memory",
    MemoryTarget.REPAIR_MEMORY.value: "repair_memory",
}


RECORD_CLASS_BY_TARGET: dict[str, type[Any]] = {
    MemoryTarget.EVENT_LEDGER.value: EventLedgerEntry,
    MemoryTarget.ACTIVE_STATE.value: ActiveStateSnapshot,
    MemoryTarget.EPISODIC_MEMORY.value: EpisodicMemoryRecord,
    MemoryTarget.SEMANTIC_MEMORY.value: SemanticMemoryRecord,
    MemoryTarget.RELATIONAL_MEMORY.value: RelationalMemoryRecord,
    MemoryTarget.AFFECTIVE_RESIDUE_MEMORY.value: AffectiveResidueRecord,
    MemoryTarget.PROCEDURAL_MEMORY.value: ProceduralMemoryRecord,
    MemoryTarget.REPAIR_MEMORY.value: RepairMemoryRecord,
}
