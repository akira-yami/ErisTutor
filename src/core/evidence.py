from __future__ import annotations

"""
evidence.py - RawSignal contract cho ERIS Cognitive Pipeline V3
===============================================================

File này là mảnh đầu tiên của Phase 0: chuẩn hóa tín hiệu thô trước khi
đẩy sang Evidence Normalizer / Event Ledger.

RawSignal KHÔNG được tự diễn giải ý nghĩa.
Nó chỉ ghi lại thứ đã đi vào runtime:
    - user vừa gõ gì
    - cửa sổ nào đang active
    - browser đang ở đâu
    - telemetry đọc được gì
    - vision/OCR thấy dữ kiện gì
    - body/runtime/system/memory/time gửi tín hiệu gì

Nếu muốn suy luận "user đang bực", "đây là né việc", "đây là joke",
hãy làm ở EvidenceItem / MeaningHypothesis / TheoryOfMind. Đừng nhét
suy diễn vào RawSignal rồi sau này giả vờ hệ thống có grounding.
Một lỗi thiết kế nhỏ, một đời debug như khảo cổ học.
"""

from dataclasses import dataclass, field, replace
from math import isfinite
import json
import time
import uuid
from typing import Any, Literal, Mapping, cast


SignalSource = Literal[
    "user_chat",
    "active_window",
    "browser",
    "telemetry",
    "vision",
    "ocr",
    "body",
    "system_log",
    "memory",
    "time",
]

PrivacyLevel = Literal["public", "normal", "private", "sensitive"]
RawOrInferred = Literal["raw", "inferred"]


ALLOWED_SIGNAL_SOURCES: tuple[str, ...] = (
    "user_chat",
    "active_window",
    "browser",
    "telemetry",
    "vision",
    "ocr",
    "body",
    "system_log",
    "memory",
    "time",
)

ALLOWED_PRIVACY_LEVELS: tuple[str, ...] = (
    "public",
    "normal",
    "private",
    "sensitive",
)


# Alias để các tầng collector gọi thoải mái hơn mà contract vẫn sạch.
# Ví dụ collector có thể gửi "chat" hoặc "window", file này ép về đúng source chuẩn.
_SOURCE_ALIASES: dict[str, str] = {
    "chat": "user_chat",
    "user": "user_chat",
    "user_input": "user_chat",
    "window": "active_window",
    "active_app": "active_window",
    "browser_title": "browser",
    "browser_url": "browser",
    "screen": "vision",
    "vision_fact": "vision",
    "text_ocr": "ocr",
    "runtime": "system_log",
    "log": "system_log",
    "long_term_memory": "memory",
    "clock": "time",
}


class RawSignalValidationError(ValueError):
    """Lỗi dữ liệu RawSignal.

    Tách exception riêng để collector/runtime có thể bắt chính xác lỗi contract,
    thay vì nuốt chung với ValueError rồi tự hỏi vì sao hệ thống chết như một
    con bot prompt rẻ tiền.
    """


def now_timestamp() -> float:
    """Trả Unix timestamp hiện tại.

    Dùng float để thống nhất với pipeline runtime và dễ tính recency/duration.
    """

    return time.time()


def normalize_signal_source(source: str) -> SignalSource:
    """Chuẩn hóa source về đúng tập source trong PDF.

    RawSignal chỉ chấp nhận 10 nguồn chuẩn:
    user_chat, active_window, browser, telemetry, vision, ocr, body,
    system_log, memory, time.
    """

    normalized = str(source or "").strip().lower()
    normalized = _SOURCE_ALIASES.get(normalized, normalized)

    if normalized not in ALLOWED_SIGNAL_SOURCES:
        allowed = ", ".join(ALLOWED_SIGNAL_SOURCES)
        raise RawSignalValidationError(
            f"source không hợp lệ: {source!r}. Source hợp lệ: {allowed}"
        )

    return cast(SignalSource, normalized)


def normalize_privacy_level(privacy_level: str) -> PrivacyLevel:
    """Chuẩn hóa privacy_level về public/normal/private/sensitive."""

    normalized = str(privacy_level or "normal").strip().lower()
    if normalized not in ALLOWED_PRIVACY_LEVELS:
        allowed = ", ".join(ALLOWED_PRIVACY_LEVELS)
        raise RawSignalValidationError(
            f"privacy_level không hợp lệ: {privacy_level!r}. Mức hợp lệ: {allowed}"
        )

    return cast(PrivacyLevel, normalized)


def clamp_reliability(value: float) -> float:
    """Ép reliability vào [0.0, 1.0].

    Hàm này dùng cho collector thực dụng. Dataclass vẫn validate nghiêm trong
    __post_init__. Nghĩa là khi tạo trực tiếp mà truyền bậy, lỗi sẽ nổ sớm.
    Khó chịu, nhưng sạch.
    """

    number = float(value)
    if not isfinite(number):
        return 0.0
    return max(0.0, min(1.0, number))


def make_signal_id(source: str, timestamp: float | None = None) -> str:
    """Tạo id đủ ổn định để trace trong EventLedger.

    Format có source + mili-giây + uuid ngắn:
        raw.user_chat.1760000000000.a1b2c3d4

    Không dùng content để hash ở đây vì content có thể private/sensitive.
    """

    ts = now_timestamp() if timestamp is None else float(timestamp)
    safe_source = normalize_signal_source(source)
    millis = int(ts * 1000)
    suffix = uuid.uuid4().hex[:8]
    return f"raw.{safe_source}.{millis}.{suffix}"


@dataclass(slots=True, frozen=True)
class RawSignal:
    """Tín hiệu thô trước khi thành evidence.

    Đây là hợp đồng nhỏ nhưng cực quan trọng:
    - `id`: khóa trace duy nhất cho EventLedger/debug.
    - `source`: nguồn tín hiệu chuẩn trong pipeline.
    - `content`: dữ liệu gốc. Có thể là str, dict, list, số, hoặc object đơn giản.
    - `timestamp`: Unix timestamp dạng float.
    - `reliability`: độ tin cậy của nguồn, không phải confidence của suy luận.
    - `privacy_level`: mức nhạy cảm của dữ liệu thô.
    - `raw`: mặc định True để đánh dấu đây là fact chưa diễn giải.

    RawSignal không chứa:
    - meaning
    - ToM estimate
    - emotion inference
    - action decision
    - prompt style

    Nếu bạn nhét mấy thứ đó vào đây, pipeline sẽ thành bùn có type hint.
    """

    id: str
    source: SignalSource
    content: Any
    timestamp: float
    reliability: float = 1.0
    privacy_level: PrivacyLevel = "normal"
    raw: bool = True

    def __post_init__(self) -> None:
        """Validate và chuẩn hóa nhẹ sau khi khởi tạo."""

        signal_id = str(self.id or "").strip()
        if not signal_id:
            raise RawSignalValidationError("RawSignal.id không được rỗng.")

        source = normalize_signal_source(str(self.source))
        privacy_level = normalize_privacy_level(str(self.privacy_level))

        timestamp = float(self.timestamp)
        if not isfinite(timestamp) or timestamp <= 0.0:
            raise RawSignalValidationError(
                f"timestamp không hợp lệ: {self.timestamp!r}. Cần Unix timestamp > 0."
            )

        reliability = float(self.reliability)
        if not isfinite(reliability) or not 0.0 <= reliability <= 1.0:
            raise RawSignalValidationError(
                f"reliability không hợp lệ: {self.reliability!r}. Cần nằm trong [0.0, 1.0]."
            )

        object.__setattr__(self, "id", signal_id)
        object.__setattr__(self, "source", source)
        object.__setattr__(self, "timestamp", timestamp)
        object.__setattr__(self, "reliability", reliability)
        object.__setattr__(self, "privacy_level", privacy_level)
        object.__setattr__(self, "raw", bool(self.raw))

    @classmethod
    def create(
        cls,
        *,
        source: str,
        content: Any,
        timestamp: float | None = None,
        reliability: float = 1.0,
        privacy_level: str = "normal",
        signal_id: str | None = None,
        raw: bool = True,
    ) -> "RawSignal":
        """Factory chính cho collector.

        Collector nên gọi hàm này thay vì tự dựng id/timestamp thủ công.
        Đời đã đủ đau với race condition rồi, đừng thêm id rỗng vào nữa.
        """

        ts = now_timestamp() if timestamp is None else float(timestamp)
        normalized_source = normalize_signal_source(source)
        normalized_privacy = normalize_privacy_level(privacy_level)
        rid = signal_id or make_signal_id(normalized_source, ts)

        return cls(
            id=rid,
            source=normalized_source,
            content=content,
            timestamp=ts,
            reliability=float(reliability),
            privacy_level=normalized_privacy,
            raw=raw,
        )

    @classmethod
    def from_chat(
        cls,
        text: str,
        *,
        timestamp: float | None = None,
        reliability: float = 1.0,
        privacy_level: str = "normal",
    ) -> "RawSignal":
        """Tạo signal từ câu chat của user."""

        return cls.create(
            source="user_chat",
            content=str(text),
            timestamp=timestamp,
            reliability=reliability,
            privacy_level=privacy_level,
        )

    @classmethod
    def from_active_window(
        cls,
        window_title: str,
        *,
        app_name: str | None = None,
        timestamp: float | None = None,
        reliability: float = 0.92,
        privacy_level: str = "normal",
    ) -> "RawSignal":
        """Tạo signal từ cửa sổ/app đang active."""

        content: dict[str, Any] = {"window_title": str(window_title)}
        if app_name:
            content["app_name"] = str(app_name)

        return cls.create(
            source="active_window",
            content=content,
            timestamp=timestamp,
            reliability=reliability,
            privacy_level=privacy_level,
        )

    @classmethod
    def from_browser(
        cls,
        *,
        title: str = "",
        url: str = "",
        tabs: list[str] | tuple[str, ...] | None = None,
        timestamp: float | None = None,
        reliability: float = 0.86,
        privacy_level: str = "normal",
    ) -> "RawSignal":
        """Tạo signal từ browser title/url/tab.

        URL/tab có thể nhạy cảm. Collector nên tự nâng privacy_level lên
        private/sensitive nếu chứa mail, token, banking, form riêng tư, v.v.
        """

        return cls.create(
            source="browser",
            content={
                "title": str(title),
                "url": str(url),
                "tabs": list(tabs or ()),
            },
            timestamp=timestamp,
            reliability=reliability,
            privacy_level=privacy_level,
        )

    @classmethod
    def from_telemetry(
        cls,
        telemetry: Mapping[str, Any],
        *,
        timestamp: float | None = None,
        reliability: float = 0.88,
        privacy_level: str = "normal",
    ) -> "RawSignal":
        """Tạo signal từ telemetry hệ thống.

        Telemetry là dữ kiện nền: idle time, CPU/RAM, open windows, recent events.
        Nó không được tự kết luận user đang lười hay đang né việc. Đó là tầng sau.
        """

        return cls.create(
            source="telemetry",
            content=dict(telemetry),
            timestamp=timestamp,
            reliability=reliability,
            privacy_level=privacy_level,
        )

    @classmethod
    def from_vision(
        cls,
        facts: str | Mapping[str, Any],
        *,
        timestamp: float | None = None,
        reliability: float = 0.72,
        privacy_level: str = "private",
    ) -> "RawSignal":
        """Tạo signal từ vision facts.

        Vision dễ sai và dễ dính dữ liệu riêng tư, nên default reliability thấp hơn
        và privacy mặc định là private. Khôn lên một chút, đỡ phải xin lỗi về sau.
        """

        return cls.create(
            source="vision",
            content=facts,
            timestamp=timestamp,
            reliability=reliability,
            privacy_level=privacy_level,
        )

    @classmethod
    def from_ocr(
        cls,
        text: str,
        *,
        timestamp: float | None = None,
        reliability: float = 0.68,
        privacy_level: str = "private",
    ) -> "RawSignal":
        """Tạo signal từ OCR text."""

        return cls.create(
            source="ocr",
            content=str(text),
            timestamp=timestamp,
            reliability=reliability,
            privacy_level=privacy_level,
        )

    @classmethod
    def from_body(
        cls,
        body_state: Mapping[str, Any],
        *,
        timestamp: float | None = None,
        reliability: float = 0.95,
        privacy_level: str = "normal",
    ) -> "RawSignal":
        """Tạo signal từ body state: vị trí, pose, motion, touch/grab, v.v."""

        return cls.create(
            source="body",
            content=dict(body_state),
            timestamp=timestamp,
            reliability=reliability,
            privacy_level=privacy_level,
        )

    @classmethod
    def from_system_log(
        cls,
        message: str | Mapping[str, Any],
        *,
        timestamp: float | None = None,
        reliability: float = 0.82,
        privacy_level: str = "normal",
    ) -> "RawSignal":
        """Tạo signal từ system log/runtime log."""

        return cls.create(
            source="system_log",
            content=message,
            timestamp=timestamp,
            reliability=reliability,
            privacy_level=privacy_level,
        )

    @classmethod
    def from_memory(
        cls,
        memory_payload: Mapping[str, Any],
        *,
        timestamp: float | None = None,
        reliability: float = 0.70,
        privacy_level: str = "private",
    ) -> "RawSignal":
        """Tạo signal từ memory recall.

        Memory là nguồn có ích nhưng không phải chân lý. Nó có decay, contradiction,
        source và permission riêng ở tầng memory cognition.
        """

        return cls.create(
            source="memory",
            content=dict(memory_payload),
            timestamp=timestamp,
            reliability=reliability,
            privacy_level=privacy_level,
        )

    @classmethod
    def from_time(
        cls,
        time_payload: Mapping[str, Any] | None = None,
        *,
        timestamp: float | None = None,
        reliability: float = 1.0,
        privacy_level: str = "public",
    ) -> "RawSignal":
        """Tạo signal từ thời gian/session clock."""

        ts = now_timestamp() if timestamp is None else float(timestamp)
        content = dict(time_payload or {})
        content.setdefault("timestamp", ts)
        content.setdefault("local_time_hint", time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts)))

        return cls.create(
            source="time",
            content=content,
            timestamp=ts,
            reliability=reliability,
            privacy_level=privacy_level,
        )

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "RawSignal":
        """Khôi phục RawSignal từ dict.

        Dùng cho EventLedger load lại JSONL hoặc test.
        """

        return cls.create(
            signal_id=str(data.get("id", "") or make_signal_id(str(data.get("source", "system_log")))),
            source=str(data.get("source", "system_log")),
            content=data.get("content"),
            timestamp=float(data.get("timestamp", now_timestamp())),
            reliability=float(data.get("reliability", 1.0)),
            privacy_level=str(data.get("privacy_level", "normal")),
            raw=bool(data.get("raw", True)),
        )

    def with_reliability(self, reliability: float) -> "RawSignal":
        """Trả bản sao với reliability mới.

        Không mutate object cũ vì RawSignal nên cư xử như record append-only.
        """

        return replace(self, reliability=clamp_reliability(reliability))

    def with_privacy(self, privacy_level: str) -> "RawSignal":
        """Trả bản sao với privacy_level mới."""

        return replace(self, privacy_level=normalize_privacy_level(privacy_level))

    @property
    def is_private(self) -> bool:
        """True nếu signal không nên surface công khai."""

        return self.privacy_level in {"private", "sensitive"}

    @property
    def is_sensitive(self) -> bool:
        """True nếu signal cần xử lý cực kỳ tiết chế."""

        return self.privacy_level == "sensitive"

    @property
    def is_low_reliability(self) -> bool:
        """True nếu nguồn yếu, tầng sau nên hạ confidence inference."""

        return self.reliability < 0.50

    def summarize_content(self, *, max_length: int = 160) -> str:
        """Tạo bản tóm tắt ngắn để debug mà không dump content quá dài.

        Không dùng hàm này để thay thế content gốc trong EventLedger.
        Nó chỉ là mặt nạ debug. Đừng biến mask thành truth, nhân loại đã thử rồi.
        """

        if isinstance(self.content, str):
            text = self.content
        else:
            try:
                text = json.dumps(self.content, ensure_ascii=False, default=str, sort_keys=True)
            except TypeError:
                text = repr(self.content)

        text = " ".join(text.split())
        if len(text) <= max_length:
            return text
        return text[: max(0, max_length - 1)].rstrip() + "…"

    def to_dict(self) -> dict[str, Any]:
        """Chuyển RawSignal thành dict để ghi ledger hoặc truyền module."""

        return {
            "id": self.id,
            "source": self.source,
            "content": self.content,
            "timestamp": self.timestamp,
            "reliability": self.reliability,
            "privacy_level": self.privacy_level,
            "raw": self.raw,
        }

    def to_safe_debug_dict(self) -> dict[str, Any]:
        """Dict debug an toàn hơn: không dump nguyên content private/sensitive."""

        return {
            "id": self.id,
            "source": self.source,
            "content_summary": self.summarize_content(max_length=120),
            "timestamp": round(self.timestamp, 3),
            "reliability": round(self.reliability, 3),
            "privacy_level": self.privacy_level,
            "raw": self.raw,
            "content_hidden": self.is_private,
        }

    def to_json(self) -> str:
        """Serialize RawSignal sang JSON UTF-8 friendly."""

        return json.dumps(self.to_dict(), ensure_ascii=False, default=str, separators=(",", ":"))


@dataclass(slots=True, frozen=True)
class EvidenceItem:
    """Claim đã chuẩn hóa từ RawSignal để các tầng sau suy luận có grounding.

    RawSignal ghi nhận dữ liệu thô. EvidenceItem là câu claim nhỏ, có source,
    confidence, reliability và tag để attention/working memory chọn lọc.
    """

    id: str
    source_signal_id: str
    source: SignalSource
    claim: str
    timestamp: float
    confidence: float = 0.65
    reliability: float = 0.70
    raw_or_inferred: RawOrInferred = "raw"
    privacy_level: PrivacyLevel = "normal"
    tags: tuple[str, ...] = field(default_factory=tuple)
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        evidence_id = str(self.id or "").strip()
        if not evidence_id:
            raise RawSignalValidationError("EvidenceItem.id không được rỗng.")
        claim = str(self.claim or "").strip()
        if not claim:
            raise RawSignalValidationError("EvidenceItem.claim không được rỗng.")
        raw_or_inferred = str(self.raw_or_inferred or "raw").strip().lower()
        if raw_or_inferred not in {"raw", "inferred"}:
            raise RawSignalValidationError("EvidenceItem.raw_or_inferred phải là raw hoặc inferred.")

        object.__setattr__(self, "id", evidence_id)
        object.__setattr__(self, "source_signal_id", str(self.source_signal_id or "").strip())
        object.__setattr__(self, "source", normalize_signal_source(str(self.source)))
        object.__setattr__(self, "claim", claim)
        object.__setattr__(self, "timestamp", float(self.timestamp))
        object.__setattr__(self, "confidence", clamp_reliability(float(self.confidence)))
        object.__setattr__(self, "reliability", clamp_reliability(float(self.reliability)))
        object.__setattr__(self, "raw_or_inferred", cast(RawOrInferred, raw_or_inferred))
        object.__setattr__(self, "privacy_level", normalize_privacy_level(str(self.privacy_level)))
        object.__setattr__(
            self,
            "tags",
            tuple(dict.fromkeys(str(tag).strip() for tag in self.tags if str(tag or "").strip())),
        )
        object.__setattr__(self, "metadata", dict(self.metadata or {}))

    @classmethod
    def from_raw_signal(
        cls,
        signal: RawSignal,
        *,
        claim: str | None = None,
        confidence: float | None = None,
        tags: tuple[str, ...] | list[str] = (),
        raw_or_inferred: str = "raw",
        evidence_id: str | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> "EvidenceItem":
        text = claim if claim is not None else signal.summarize_content(max_length=240)
        return cls(
            id=evidence_id or f"ev.{signal.id}",
            source_signal_id=signal.id,
            source=signal.source,
            claim=str(text),
            timestamp=signal.timestamp,
            confidence=signal.reliability if confidence is None else float(confidence),
            reliability=signal.reliability,
            raw_or_inferred=raw_or_inferred,  # type: ignore[arg-type]
            privacy_level=signal.privacy_level,
            tags=tuple(str(tag) for tag in tags),
            metadata=dict(metadata or {}),
        )

    @property
    def is_private(self) -> bool:
        return self.privacy_level in {"private", "sensitive"}

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "source_signal_id": self.source_signal_id,
            "source": self.source,
            "claim": self.claim,
            "timestamp": self.timestamp,
            "confidence": self.confidence,
            "reliability": self.reliability,
            "raw_or_inferred": self.raw_or_inferred,
            "privacy_level": self.privacy_level,
            "tags": list(self.tags),
            "metadata": dict(self.metadata),
        }


@dataclass(slots=True)
class ModuleReport:
    """Kết quả chuẩn mà một module ghi lại vào CognitiveFrame."""

    module_name: str
    confidence: float
    pressure: float
    evidence_used: list[str]
    result: Any
    warnings: list[str] = field(default_factory=list)
    debug: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.module_name = str(self.module_name or "unknown_module").strip()
        self.confidence = clamp_reliability(float(self.confidence))
        self.pressure = clamp_reliability(float(self.pressure))
        self.evidence_used = list(dict.fromkeys(str(item) for item in self.evidence_used if str(item or "").strip()))
        self.warnings = list(dict.fromkeys(str(item) for item in self.warnings if str(item or "").strip()))
        self.debug = dict(self.debug or {})

    def to_dict(self) -> dict[str, Any]:
        return {
            "module_name": self.module_name,
            "confidence": self.confidence,
            "pressure": self.pressure,
            "evidence_used": list(self.evidence_used),
            "result": self.result.to_dict() if callable(getattr(self.result, "to_dict", None)) else self.result,
            "warnings": list(self.warnings),
            "debug": dict(self.debug),
        }


@dataclass(slots=True)
class StateCommitRequest:
    """Yêu cầu ghi state/memory sau khi frame đã được guard.

    Contract này phải đủ rộng cho commit protocol hiện tại: permission, decay,
    source_module và tags đều cần đi cùng request. Nếu không, downstream lại phải
    nhét chúng vào metadata như buôn lậu qua biên giới JSON.
    """

    target: str
    content: Any
    evidence_ids: list[str] = field(default_factory=list)
    confidence: float = 0.60
    permission_required: bool = True
    storage_permission: bool = False
    decay_policy: str = "normal"
    reason: str = ""
    source_module: str = ""
    tags: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.target = str(self.target or "unknown_target").strip()
        self.evidence_ids = list(dict.fromkeys(str(item) for item in self.evidence_ids if str(item or "").strip()))
        self.confidence = clamp_reliability(float(self.confidence))
        self.permission_required = bool(self.permission_required)
        self.storage_permission = bool(self.storage_permission)
        self.decay_policy = str(self.decay_policy or "normal").strip()
        self.reason = str(self.reason or "").strip()
        self.source_module = str(self.source_module or "").strip()
        self.tags = list(dict.fromkeys(str(item) for item in self.tags if str(item or "").strip()))
        self.metadata = dict(self.metadata or {})
        self.metadata.setdefault("permission_required", self.permission_required)
        self.metadata.setdefault("storage_permission", self.storage_permission)
        self.metadata.setdefault("decay_policy", self.decay_policy)
        if self.source_module:
            self.metadata.setdefault("source_module", self.source_module)
        if self.tags:
            self.metadata.setdefault("tags", list(self.tags))

    def to_dict(self) -> dict[str, Any]:
        return {
            "target": self.target,
            "content": self.content,
            "evidence_ids": list(self.evidence_ids),
            "confidence": self.confidence,
            "permission_required": self.permission_required,
            "storage_permission": self.storage_permission,
            "decay_policy": self.decay_policy,
            "reason": self.reason,
            "source_module": self.source_module,
            "tags": list(self.tags),
            "metadata": dict(self.metadata),
        }


@dataclass(slots=True)
class DebugTrace:
    """Trace nhẹ cho audit/debug của một frame."""

    entries: list[dict[str, Any]] = field(default_factory=list)

    def add(self, *, actor: str, event: str, data: Mapping[str, Any] | None = None) -> None:
        self.entries.append(
            {
                "timestamp": now_timestamp(),
                "actor": str(actor or "runtime"),
                "event": str(event or "event"),
                "data": dict(data or {}),
            }
        )

    def to_dict(self) -> dict[str, Any]:
        return {"entries": list(self.entries)}


def raw_signal(
    source: str,
    content: Any,
    *,
    timestamp: float | None = None,
    reliability: float = 1.0,
    privacy_level: str = "normal",
    signal_id: str | None = None,
) -> RawSignal:
    """Shortcut ngắn cho collector.

    Ví dụ:
        signal = raw_signal("user_chat", "sửa lỗi này")
    """

    return RawSignal.create(
        source=source,
        content=content,
        timestamp=timestamp,
        reliability=reliability,
        privacy_level=privacy_level,
        signal_id=signal_id,
    )


make_raw_signal = raw_signal


__all__ = [
    "ALLOWED_PRIVACY_LEVELS",
    "ALLOWED_SIGNAL_SOURCES",
    "DebugTrace",
    "EvidenceItem",
    "ModuleReport",
    "PrivacyLevel",
    "RawOrInferred",
    "RawSignal",
    "RawSignalValidationError",
    "SignalSource",
    "StateCommitRequest",
    "clamp_reliability",
    "make_raw_signal",
    "make_signal_id",
    "normalize_privacy_level",
    "normalize_signal_source",
    "now_timestamp",
    "raw_signal",
]
