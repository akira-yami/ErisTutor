# -*- coding: utf-8 -*-
"""
cortex/private_mind/contracts.py
================================

Hợp đồng dữ liệu cho "private mind" của ERIS.

Tầng này KHÔNG sinh lời thoại public. Nó chỉ biến tín hiệu runtime thành:
- ThoughtFragment: mảnh nhận định có evidence;
- PrivateConcern: mối bận tâm riêng còn mở;
- InternalJudgment: phán quyết nội tâm đã được synthesize;
- StateMutation: yêu cầu đổi bias/state tương lai;
- SurfaceCandidate: phần rất nhỏ có thể nổi lên SurfaceArbiter.

Nếu thiếu contract này rồi cứ thả nhiều API tự nói với nhau, ta không có cognition.
Ta có một đàn vẹt đắt tiền đang tranh micro. Công nghệ tiến bộ thật cảm động.
"""

from __future__ import annotations

import hashlib
import math
import time
import uuid
from dataclasses import asdict, dataclass, field, is_dataclass
from typing import Any, Iterable, Literal, Mapping


_VALID_INTENTS = ("sincere", "playful", "teasing", "speculative")
_VALID_STANCES = ("grounded", "ungrounded_aware", "ungrounded_blind")


def normalize_stance(value: Any) -> str | None:
    """Chuẩn hoá epistemic_stance.

    Trả về một stance hợp lệ trong {grounded, ungrounded_aware, ungrounded_blind},
    hoặc None nếu vắng/ngoài tập (Req 5.1/5.9). None là giá trị dành riêng để
    Epistemic_Classifier gán sau.
    """
    v = str(value or "").strip().lower()
    return v if v in _VALID_STANCES else None


def normalize_intent_field(value: Any) -> str:
    """Chuẩn hoá intent ở tầng contract.

    Trả về một intent hợp lệ trong {sincere, playful, teasing, speculative},
    hoặc `sincere` nếu vắng/ngoài tập (Req 5.2/5.8). Việc default kiểu Cartesian
    (`speculative` khi không có anchor) được áp dụng sau, tại valve.
    """
    v = str(value or "").strip().lower()
    return v if v in _VALID_INTENTS else "sincere"


ConcernStatus = Literal["open", "watching", "blocked", "resolved", "rejected", "stale"]
MutationOperation = Literal["set", "prefer", "suppress", "increase", "decrease", "append", "merge", "decay"]
MutationTarget = Literal[
    "agenda_stack",
    "repair_memory",
    "belief_core",
    "speech_bias",
    "inquiry_plan",
    "thought_stream_pressure",
    "surface_candidate_queue",
    "active_state",
    "private_concern",
]
SurfaceChannel = Literal["none", "surface_arbiter", "memory_update_only", "body_only"]


def now_ts() -> float:
    return time.time()


def clamp01(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except Exception:
        number = float(default)
    if math.isnan(number) or math.isinf(number):
        number = float(default)
    return max(0.0, min(1.0, number))


def compact_text(value: Any, limit: int = 480) -> str:
    text = " ".join(str(value or "").replace("\x00", " ").split()).strip()
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + "…"


def stable_id(prefix: str, *parts: Any, size: int = 16) -> str:
    base = "|".join(compact_text(part, 360).lower() for part in parts if str(part or "").strip())
    if not base:
        base = uuid.uuid4().hex
    digest = hashlib.sha1(base.encode("utf-8", errors="ignore")).hexdigest()[:size]
    return f"{prefix}.{digest}"


def to_plain(value: Any) -> Any:
    """Serialize mềm để debug/persist JSON.

    Không nhét object sống vào memory. Object sống trong JSON là cách một repo
    gọi `repr()` là persistence, rồi ngồi khóc lúc load lại.
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
class RankedHypothesis:
    """Một giả thuyết có điểm và evidence rõ ràng."""

    label: str
    description: str = ""
    confidence: float = 0.0
    evidence_ids: tuple[str, ...] = ()
    pressure: float = 0.0
    rejected: bool = False
    reason: str = ""

    def __post_init__(self) -> None:
        self.label = compact_text(self.label, 160)
        self.description = compact_text(self.description, 420)
        self.confidence = clamp01(self.confidence)
        self.pressure = clamp01(self.pressure)
        self.evidence_ids = tuple(str(x) for x in self.evidence_ids if str(x or "").strip())[:16]
        self.reason = compact_text(self.reason, 260)

    @property
    def score(self) -> float:
        return clamp01(0.62 * self.confidence + 0.38 * self.pressure)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> "RankedHypothesis":
        return cls(
            label=str(data.get("label") or data.get("claim") or "hypothesis"),
            description=str(data.get("description") or data.get("reason") or ""),
            confidence=clamp01(data.get("confidence", 0.0)),
            evidence_ids=tuple(str(x) for x in (data.get("evidence_ids") or data.get("evidence") or ()) if str(x).strip()),
            pressure=clamp01(data.get("pressure", 0.0)),
            rejected=bool(data.get("rejected", False)),
            reason=str(data.get("reason") or ""),
        )


@dataclass(slots=True)
class ThoughtFragment:
    """Mảnh suy nghĩ riêng do mini-evaluator tạo ra.

    Fragment không phải lời thoại. Nó là một claim có confidence, pressure và
    evidence. Nếu fragment không có evidence thì chỉ là tiếng ồn có áo JSON.
    """

    source: str
    claim: str
    confidence: float = 0.0
    evidence_ids: tuple[str, ...] = ()
    pressure: float = 0.0
    suggested_question: str = ""
    hypotheses: tuple[RankedHypothesis, ...] = ()
    privacy_level: str = "private"
    created_at: float = field(default_factory=now_ts)
    id: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.source = compact_text(self.source or "unknown_evaluator", 96)
        self.claim = compact_text(self.claim, 640)
        self.confidence = clamp01(self.confidence)
        self.pressure = clamp01(self.pressure)
        self.suggested_question = compact_text(self.suggested_question, 360)
        self.evidence_ids = tuple(str(x) for x in self.evidence_ids if str(x or "").strip())[:24]
        self.privacy_level = compact_text(self.privacy_level or "private", 32)
        self.hypotheses = tuple(
            item if isinstance(item, RankedHypothesis) else RankedHypothesis.from_mapping(item)  # type: ignore[arg-type]
            for item in tuple(self.hypotheses or ())[:8]
        )
        self.metadata = dict(self.metadata or {})
        if not self.id:
            self.id = stable_id("tf", self.source, self.claim, *self.evidence_ids)

    @property
    def score(self) -> float:
        return clamp01(0.55 * self.confidence + 0.45 * self.pressure)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["hypotheses"] = [h.to_dict() for h in self.hypotheses]
        data["metadata"] = to_plain(self.metadata)
        data["score"] = round(self.score, 4)
        return data

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> "ThoughtFragment":
        return cls(
            id=str(data.get("id") or ""),
            source=str(data.get("source") or "unknown_evaluator"),
            claim=str(data.get("claim") or data.get("dominant_evaluation") or data.get("summary") or ""),
            confidence=clamp01(data.get("confidence", 0.0)),
            evidence_ids=tuple(str(x) for x in (data.get("evidence_ids") or data.get("evidence") or ()) if str(x).strip()),
            pressure=clamp01(data.get("pressure", data.get("surface_pressure", 0.0))),
            suggested_question=str(data.get("suggested_question") or data.get("open_question") or ""),
            hypotheses=tuple(RankedHypothesis.from_mapping(x) for x in tuple(data.get("hypotheses") or ()) if isinstance(x, Mapping)),
            privacy_level=str(data.get("privacy_level") or "private"),
            created_at=float(data.get("created_at") or now_ts()),
            metadata=dict(data.get("metadata") or {}),
        )


@dataclass(slots=True)
class PrivateConcern:
    """Mối bận tâm riêng có đời sống qua nhiều tick.

    Đây là thứ biến fragment rời rạc thành "điều còn treo trong đầu". Không có
    concern thì private mind chỉ là log formatter đắt tiền, đúng kiểu nhân loại
    hay gọi là kiến trúc vì nó có thư mục riêng.
    """

    id: str
    theme: str
    evidence_ids: tuple[str, ...] = ()
    open_questions: tuple[str, ...] = ()
    hypotheses: tuple[RankedHypothesis, ...] = ()
    pressure: float = 0.0
    uncertainty: float = 0.0
    recurrence: int = 1
    status: ConcernStatus = "open"
    last_reviewed_at: float = field(default_factory=now_ts)
    future_biases: tuple[str, ...] = ()
    created_at: float = field(default_factory=now_ts)
    updated_at: float = field(default_factory=now_ts)
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.theme = compact_text(self.theme, 360)
        if not self.id:
            self.id = stable_id("concern", self.theme)
        self.evidence_ids = tuple(str(x) for x in self.evidence_ids if str(x or "").strip())[:64]
        self.open_questions = tuple(compact_text(x, 260) for x in self.open_questions if str(x or "").strip())[:12]
        self.hypotheses = tuple(
            item if isinstance(item, RankedHypothesis) else RankedHypothesis.from_mapping(item)  # type: ignore[arg-type]
            for item in tuple(self.hypotheses or ())[:12]
        )
        self.pressure = clamp01(self.pressure)
        self.uncertainty = clamp01(self.uncertainty)
        self.recurrence = max(1, int(self.recurrence or 1))
        self.future_biases = tuple(compact_text(x, 160) for x in self.future_biases if str(x or "").strip())[:12]
        self.metadata = dict(self.metadata or {})

    def merge_fragment(self, fragment: ThoughtFragment) -> "PrivateConcern":
        """Cập nhật concern từ fragment mới.

        Áp lực tăng theo recurrence, uncertainty giảm nhẹ nếu nhiều evaluator cùng
        hướng. Không làm màu, chỉ đủ để state sau có bias thật.
        """

        now = now_ts()
        self.recurrence += 1
        prev_evidence_count = len(self.evidence_ids)
        prev_question_count = len(self.open_questions)
        prev_hypothesis_labels = {h.label.lower() for h in self.hypotheses}
        merged_evidence = [*self.evidence_ids]
        for eid in fragment.evidence_ids:
            if eid not in merged_evidence:
                merged_evidence.append(eid)
        self.evidence_ids = tuple(merged_evidence[:64])
        had_new_evidence = len(self.evidence_ids) > prev_evidence_count
        if fragment.suggested_question and fragment.suggested_question not in self.open_questions:
            self.open_questions = tuple([*self.open_questions, fragment.suggested_question][:12])
        merged_hypotheses = list(self.hypotheses)
        existing = {h.label.lower(): idx for idx, h in enumerate(merged_hypotheses)}
        for hyp in fragment.hypotheses:
            key = hyp.label.lower()
            if key in existing:
                old = merged_hypotheses[existing[key]]
                merged_hypotheses[existing[key]] = RankedHypothesis(
                    label=old.label,
                    description=old.description or hyp.description,
                    confidence=max(old.confidence * 0.96, hyp.confidence),
                    evidence_ids=tuple(dict.fromkeys((*old.evidence_ids, *hyp.evidence_ids)))[:16],
                    pressure=max(old.pressure * 0.94, hyp.pressure),
                    rejected=old.rejected and hyp.rejected,
                    reason=old.reason or hyp.reason,
                )
            else:
                merged_hypotheses.append(hyp)
        self.hypotheses = tuple(sorted(merged_hypotheses, key=lambda h: h.score, reverse=True)[:12])
        # V40: recurrence chỉ nói rằng concern quay lại. Mutation chỉ tăng khi có
        # nội dung/evidence thật sự mới. Hai khái niệm này tách ra để Neuro không
        # thưởng cho việc nghĩ lặp cùng một thứ như thể đó là insight mới.
        new_question = len(self.open_questions) > prev_question_count
        new_hypothesis = any(h.label.lower() not in prev_hypothesis_labels for h in self.hypotheses)
        self.metadata["reactivation_count"] = int(self.metadata.get("reactivation_count", 0) or 0) + 1
        self.metadata["last_reactivated_at"] = now
        if had_new_evidence or new_question or new_hypothesis:
            self.metadata["mutation_count"] = int(self.metadata.get("mutation_count", 0) or 0) + 1
            self.metadata["last_mutation_at"] = now
        if had_new_evidence:
            # Có bằng chứng mới thật → mối bận tâm được nuôi chính đáng, cho phép leo.
            self.pressure = clamp01(max(self.pressure * 0.92, fragment.pressure) + min(0.12, 0.018 * self.recurrence))
            self.uncertainty = clamp01(min(self.uncertainty or (1.0 - fragment.confidence), 1.0 - max(fragment.confidence, 0.05)) * 0.97)
            self.metadata["echo_streak"] = 0
        else:
            # Echo thuần: fragment chỉ vọng lại chính evidence cũ, không có gì mới.
            # Đây chính là nhiên liệu của vòng "áp lực về áp lực" — nghĩ lại nỗi lo
            # rồi tự coi việc nghĩ đó là bằng chứng nỗi lo lớn hơn. Hạ nhiệt thay vì
            # khuếch đại: pressure nguội dần, độ bất định nhích lên (nghĩ mãi không ra).
            self.pressure = clamp01(self.pressure * 0.90)
            self.uncertainty = clamp01(self.uncertainty + 0.02)
            # Đếm số lần nghĩ-lại-không-ra-gì-mới, để concern_store biết khi nào nên buông.
            self.metadata["echo_streak"] = int(self.metadata.get("echo_streak", 0) or 0) + 1
        self.last_reviewed_at = now
        self.updated_at = now
        self.status = "open" if self.status in {"stale", "blocked"} else self.status
        self.metadata.setdefault("fragment_sources", [])
        sources = list(self.metadata.get("fragment_sources") or [])
        if fragment.source not in sources:
            sources.append(fragment.source)
        self.metadata["fragment_sources"] = sources[:12]
        return self

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["hypotheses"] = [h.to_dict() for h in self.hypotheses]
        data["metadata"] = to_plain(self.metadata)
        return data

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> "PrivateConcern":
        return cls(
            id=str(data.get("id") or ""),
            theme=str(data.get("theme") or data.get("claim") or "private concern"),
            evidence_ids=tuple(str(x) for x in (data.get("evidence_ids") or ()) if str(x).strip()),
            open_questions=tuple(str(x) for x in (data.get("open_questions") or ()) if str(x).strip()),
            hypotheses=tuple(RankedHypothesis.from_mapping(x) for x in tuple(data.get("hypotheses") or ()) if isinstance(x, Mapping)),
            pressure=clamp01(data.get("pressure", 0.0)),
            uncertainty=clamp01(data.get("uncertainty", 0.0)),
            recurrence=int(data.get("recurrence") or 1),
            status=str(data.get("status") or "open"),  # type: ignore[arg-type]
            last_reviewed_at=float(data.get("last_reviewed_at") or now_ts()),
            future_biases=tuple(str(x) for x in (data.get("future_biases") or ()) if str(x).strip()),
            created_at=float(data.get("created_at") or now_ts()),
            updated_at=float(data.get("updated_at") or now_ts()),
            metadata=dict(data.get("metadata") or {}),
        )


@dataclass(slots=True)
class StateMutation:
    """Một yêu cầu đổi state/bias tương lai.

    Đây là cầu nối giữa "đã nghĩ" và "sẽ hành xử khác". Không có mutation bus,
    API chỉ đang viết nhật ký nội tâm cho đẹp. Rất thi vị, và vô dụng.
    """

    target: MutationTarget | str
    operation: MutationOperation | str
    value: Any
    confidence: float = 0.0
    evidence_ids: tuple[str, ...] = ()
    reason: str = ""
    priority: float = 0.0
    ttl_seconds: float = 3600.0
    created_at: float = field(default_factory=now_ts)
    id: str = ""

    def __post_init__(self) -> None:
        self.target = compact_text(self.target, 80)
        self.operation = compact_text(self.operation, 32)
        self.confidence = clamp01(self.confidence)
        self.priority = clamp01(self.priority)
        self.evidence_ids = tuple(str(x) for x in self.evidence_ids if str(x or "").strip())[:24]
        self.reason = compact_text(self.reason, 360)
        if not self.id:
            self.id = stable_id("mut", self.target, self.operation, to_plain(self.value), *self.evidence_ids)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "target": self.target,
            "operation": self.operation,
            "value": to_plain(self.value),
            "confidence": self.confidence,
            "evidence_ids": list(self.evidence_ids),
            "reason": self.reason,
            "priority": self.priority,
            "ttl_seconds": self.ttl_seconds,
            "created_at": self.created_at,
        }

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> "StateMutation":
        return cls(
            id=str(data.get("id") or ""),
            target=str(data.get("target") or "active_state"),
            operation=str(data.get("operation") or "merge"),
            value=data.get("value"),
            confidence=clamp01(data.get("confidence", 0.0)),
            evidence_ids=tuple(str(x) for x in (data.get("evidence_ids") or data.get("evidence") or ()) if str(x).strip()),
            reason=str(data.get("reason") or ""),
            priority=clamp01(data.get("priority", 0.0)),
            ttl_seconds=float(data.get("ttl_seconds") or 3600.0),
            created_at=float(data.get("created_at") or now_ts()),
        )


@dataclass(slots=True)
class SurfaceCandidate:
    """Ứng viên được phép nổi lên SurfaceArbiter.

    Đây vẫn chưa phải câu nói. Nó chỉ là impulse đã được lọc private-chain.
    """

    kind: str
    topic_seed: str
    usefulness: float = 0.0
    confidence: float = 0.0
    interrupt_cost: float = 0.0
    evidence_ids: tuple[str, ...] = ()
    reason: str = ""
    channel: SurfaceChannel = "surface_arbiter"
    created_at: float = field(default_factory=now_ts)
    id: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    epistemic_stance: str | None = None
    intent: str = "sincere"

    def __post_init__(self) -> None:
        self.kind = compact_text(self.kind or "private_mind_surface", 80)
        self.topic_seed = compact_text(self.topic_seed, 360)
        self.usefulness = clamp01(self.usefulness)
        self.confidence = clamp01(self.confidence)
        self.interrupt_cost = clamp01(self.interrupt_cost)
        self.evidence_ids = tuple(str(x) for x in self.evidence_ids if str(x or "").strip())[:24]
        self.reason = compact_text(self.reason, 260)
        self.metadata = dict(self.metadata or {})
        self.epistemic_stance = normalize_stance(self.epistemic_stance)
        self.intent = normalize_intent_field(self.intent)
        if not self.id:
            self.id = stable_id("surf", self.kind, self.topic_seed, *self.evidence_ids)

    @property
    def allowed(self) -> bool:
        return bool(self.topic_seed and self.channel != "none" and self.usefulness >= 0.55 and self.confidence >= 0.55 and self.interrupt_cost <= 0.45)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "topic_seed": self.topic_seed,
            "usefulness": self.usefulness,
            "confidence": self.confidence,
            "interrupt_cost": self.interrupt_cost,
            "evidence_ids": list(self.evidence_ids),
            "reason": self.reason,
            "channel": self.channel,
            "created_at": self.created_at,
            "metadata": to_plain(self.metadata),
            "epistemic_stance": self.epistemic_stance,
            "intent": self.intent,
        }

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> "SurfaceCandidate":
        return cls(
            id=str(data.get("id") or ""),
            kind=str(data.get("kind") or "private_mind_surface"),
            topic_seed=str(data.get("topic_seed") or data.get("seed") or data.get("summary") or ""),
            usefulness=clamp01(data.get("usefulness", data.get("speech_value", 0.0))),
            confidence=clamp01(data.get("confidence", 0.0)),
            interrupt_cost=clamp01(data.get("interrupt_cost", 0.0)),
            evidence_ids=tuple(str(x) for x in (data.get("evidence_ids") or ()) if str(x).strip()),
            reason=str(data.get("reason") or ""),
            channel=str(data.get("channel") or "surface_arbiter"),  # type: ignore[arg-type]
            created_at=float(data.get("created_at") or now_ts()),
            metadata=dict(data.get("metadata") or {}),
            epistemic_stance=normalize_stance(data.get("epistemic_stance")),
            intent=normalize_intent_field(data.get("intent")),
        )


@dataclass(slots=True)
class InternalJudgment:
    """Phán quyết nội tâm sau khi local-model/heuristic synthesize fragments."""

    dominant_judgment: str
    ranked_hypotheses: tuple[RankedHypothesis, ...] = ()
    rejected_hypotheses: tuple[RankedHypothesis, ...] = ()
    open_questions: tuple[str, ...] = ()
    private_concern_update: dict[str, Any] = field(default_factory=dict)
    state_mutations: tuple[StateMutation, ...] = ()
    surface_candidate: SurfaceCandidate | None = None
    confidence: float = 0.0
    pressure: float = 0.0
    evidence_ids: tuple[str, ...] = ()
    source: str = "inner_synthesizer"
    created_at: float = field(default_factory=now_ts)
    metadata: dict[str, Any] = field(default_factory=dict)
    epistemic_stance: str | None = None
    intent: str = "sincere"

    def __post_init__(self) -> None:
        self.dominant_judgment = compact_text(self.dominant_judgment, 700)
        self.ranked_hypotheses = tuple(sorted((
            h if isinstance(h, RankedHypothesis) else RankedHypothesis.from_mapping(h)  # type: ignore[arg-type]
            for h in tuple(self.ranked_hypotheses or ())[:12]
        ), key=lambda h: h.score, reverse=True))
        self.rejected_hypotheses = tuple(
            h if isinstance(h, RankedHypothesis) else RankedHypothesis.from_mapping(h)  # type: ignore[arg-type]
            for h in tuple(self.rejected_hypotheses or ())[:8]
        )
        self.open_questions = tuple(compact_text(q, 260) for q in self.open_questions if str(q or "").strip())[:10]
        self.private_concern_update = dict(self.private_concern_update or {})
        self.state_mutations = tuple(
            m if isinstance(m, StateMutation) else StateMutation.from_mapping(m)  # type: ignore[arg-type]
            for m in tuple(self.state_mutations or ())[:16]
        )
        if self.surface_candidate is not None and not isinstance(self.surface_candidate, SurfaceCandidate):
            self.surface_candidate = SurfaceCandidate.from_mapping(self.surface_candidate)  # type: ignore[arg-type]
        self.confidence = clamp01(self.confidence)
        self.pressure = clamp01(self.pressure)
        self.evidence_ids = tuple(str(x) for x in self.evidence_ids if str(x or "").strip())[:48]
        self.source = compact_text(self.source, 80)
        self.metadata = dict(self.metadata or {})
        self.epistemic_stance = normalize_stance(self.epistemic_stance)
        self.intent = normalize_intent_field(self.intent)

    def to_dict(self) -> dict[str, Any]:
        return {
            "dominant_judgment": self.dominant_judgment,
            "ranked_hypotheses": [h.to_dict() for h in self.ranked_hypotheses],
            "rejected_hypotheses": [h.to_dict() for h in self.rejected_hypotheses],
            "open_questions": list(self.open_questions),
            "private_concern_update": to_plain(self.private_concern_update),
            "state_mutations": [m.to_dict() for m in self.state_mutations],
            "surface_candidate": self.surface_candidate.to_dict() if self.surface_candidate else None,
            "confidence": self.confidence,
            "pressure": self.pressure,
            "evidence_ids": list(self.evidence_ids),
            "source": self.source,
            "created_at": self.created_at,
            "metadata": to_plain(self.metadata),
            "epistemic_stance": self.epistemic_stance,
            "intent": self.intent,
        }

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> "InternalJudgment":
        surface = data.get("surface_candidate")
        return cls(
            dominant_judgment=str(data.get("dominant_judgment") or data.get("judgment") or data.get("summary") or ""),
            ranked_hypotheses=tuple(RankedHypothesis.from_mapping(x) for x in tuple(data.get("ranked_hypotheses") or []) if isinstance(x, Mapping)),
            rejected_hypotheses=tuple(RankedHypothesis.from_mapping(x) for x in tuple(data.get("rejected_hypotheses") or []) if isinstance(x, Mapping)),
            open_questions=tuple(str(x) for x in (data.get("open_questions") or ()) if str(x).strip()),
            private_concern_update=dict(data.get("private_concern_update") or {}),
            state_mutations=tuple(StateMutation.from_mapping(x) for x in tuple(data.get("state_mutations") or []) if isinstance(x, Mapping)),
            surface_candidate=SurfaceCandidate.from_mapping(surface) if isinstance(surface, Mapping) else None,
            confidence=clamp01(data.get("confidence", 0.0)),
            pressure=clamp01(data.get("pressure", 0.0)),
            evidence_ids=tuple(str(x) for x in (data.get("evidence_ids") or data.get("evidence") or ()) if str(x).strip()),
            source=str(data.get("source") or "inner_synthesizer"),
            created_at=float(data.get("created_at") or now_ts()),
            metadata=dict(data.get("metadata") or {}),
            epistemic_stance=normalize_stance(data.get("epistemic_stance")),
            intent=normalize_intent_field(data.get("intent")),
        )


@dataclass(slots=True)
class EvidenceDossier:
    """Hồ sơ context chung cho mini-evaluator và inner synthesizer."""

    dossier_id: str
    created_at: float = field(default_factory=now_ts)
    recent_user_turns: tuple[dict[str, Any], ...] = ()
    runtime_state: dict[str, Any] = field(default_factory=dict)
    memory_hits: tuple[dict[str, Any], ...] = ()
    prior_repair_failures: tuple[dict[str, Any], ...] = ()
    active_goal: str = ""
    current_task: str = ""
    surface_outcome: dict[str, Any] = field(default_factory=dict)
    concern_snapshots: tuple[dict[str, Any], ...] = ()
    user_model_snapshot: dict[str, Any] = field(default_factory=dict)
    evidence_index: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "dossier_id": self.dossier_id,
            "created_at": self.created_at,
            "recent_user_turns": to_plain(self.recent_user_turns),
            "runtime_state": to_plain(self.runtime_state),
            "memory_hits": to_plain(self.memory_hits),
            "prior_repair_failures": to_plain(self.prior_repair_failures),
            "active_goal": self.active_goal,
            "current_task": self.current_task,
            "surface_outcome": to_plain(self.surface_outcome),
            "concern_snapshots": to_plain(self.concern_snapshots),
            "user_model_snapshot": to_plain(self.user_model_snapshot),
            "evidence_index": to_plain(self.evidence_index),
            "metadata": to_plain(self.metadata),
        }

    @property
    def evidence_ids(self) -> tuple[str, ...]:
        ids = list(self.evidence_index.keys())
        for turn in self.recent_user_turns:
            eid = str(turn.get("evidence_id") or "")
            if eid and eid not in ids:
                ids.append(eid)
        return tuple(ids[:96])


@dataclass(slots=True)
class PrivateMindTickResult:
    """Kết quả một tick private mind."""

    dossier: EvidenceDossier
    fragments: tuple[ThoughtFragment, ...]
    concerns: tuple[PrivateConcern, ...]
    judgment: InternalJudgment | None
    applied_mutations: tuple[dict[str, Any], ...] = ()
    surface_candidate: SurfaceCandidate | None = None
    debug: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "dossier": self.dossier.to_dict(),
            "fragments": [f.to_dict() for f in self.fragments],
            "concerns": [c.to_dict() for c in self.concerns],
            "judgment": self.judgment.to_dict() if self.judgment else None,
            "applied_mutations": to_plain(self.applied_mutations),
            "surface_candidate": self.surface_candidate.to_dict() if self.surface_candidate else None,
            "debug": to_plain(self.debug),
        }
