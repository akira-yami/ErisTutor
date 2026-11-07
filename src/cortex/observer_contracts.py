# src/cortex/observer_contracts.py
"""
Contract cho Background Observer của ERIS.

Tầng này không trả lời user, không commit memory, không tự hành động.
Nó chỉ biến tín hiệu nền thành frame nhỏ để Attention/WorkingMemory/BodyBias đọc.
"""

from __future__ import annotations

import hashlib
import time
import unicodedata
from dataclasses import dataclass, field
from typing import Any, Literal, Mapping, Optional


FocusKind = Literal[
    "code",
    "docs",
    "browser",
    "chat",
    "media",
    "game",
    "system",
    "unknown",
]

ActivityIntent = Literal[
    "debugging",
    "designing",
    "reading",
    "searching",
    "communicating",
    "resting",
    "fragmented",
    "idle",
    "unknown",
]

ObserverAction = Literal[
    "observe_only",
    "update_body_bias",
    "call_vision_compressor",
    "wake_main_cognition",
    "suppress",
]

BudgetHint = Literal["fast", "normal", "deep", "reflection"]

PrivacyLevel = Literal["public", "normal", "private", "sensitive"]


ALLOWED_FOCUS_KINDS: set[str] = {
    "code",
    "docs",
    "browser",
    "chat",
    "media",
    "game",
    "system",
    "unknown",
}

ALLOWED_ACTIVITY_INTENTS: set[str] = {
    "debugging",
    "designing",
    "reading",
    "searching",
    "communicating",
    "resting",
    "fragmented",
    "idle",
    "unknown",
}

ALLOWED_OBSERVER_ACTIONS: set[str] = {
    "observe_only",
    "update_body_bias",
    "call_vision_compressor",
    "wake_main_cognition",
    "suppress",
}


@dataclass
class SignalSnapshot:
    """Một lát cắt tín hiệu nền đã được làm nhẹ.

    Đây không phải raw telemetry đầy đủ. Nó là bản rút gọn để observer đọc nhanh.
    """

    timestamp: float = field(default_factory=time.time)

    active_window_title: str = ""
    active_app: str = ""
    focus_kind_hint: str = "unknown"
    url_or_path_hint: str = ""

    idle_seconds: float = 0.0
    recent_events: list[str] = field(default_factory=list)

    ocr_text: str = ""
    ocr_hash: str = ""
    screen_hash: str = ""

    cpu_percent: float = 0.0
    ram_percent: float = 0.0

    body_state: str = ""
    chat_visible: bool = False

    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.ocr_text and not self.ocr_hash:
            self.ocr_hash = stable_hash(self.ocr_text)

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> "SignalSnapshot":
        """Tạo snapshot từ dict telemetry/se tùy shape.

        Hàm này cố tình mềm để dễ nối vào repo hiện tại mà không phải sửa 12 file.
        """

        def pick(*names: str, default: Any = "") -> Any:
            for name in names:
                if name in data and data[name] is not None:
                    return data[name]
            return default

        recent = pick("recent_events", "events", default=[])
        if isinstance(recent, str):
            recent = [recent]
        elif not isinstance(recent, list):
            recent = list(recent) if recent else []

        return cls(
            timestamp=float(pick("timestamp", "time", default=time.time())),
            active_window_title=str(pick("active_window_title", "window_title", "title", default="")),
            active_app=str(pick("active_app", "app", "process", default="")),
            focus_kind_hint=str(pick("focus_kind", "focus_kind_hint", "window_kind", default="unknown")),
            url_or_path_hint=str(pick("url", "path", "file_path", "url_or_path_hint", default="")),
            idle_seconds=float(pick("idle_seconds", "idle", default=0.0) or 0.0),
            recent_events=[compact_text(str(x), 180) for x in recent],
            ocr_text=str(pick("ocr_text", "screen_text", "visible_text", default="")),
            ocr_hash=str(pick("ocr_hash", default="")),
            screen_hash=str(pick("screen_hash", "image_hash", default="")),
            cpu_percent=float(pick("cpu_percent", "cpu", default=0.0) or 0.0),
            ram_percent=float(pick("ram_percent", "ram", "memory_percent", default=0.0) or 0.0),
            body_state=str(pick("body_state", "posture", default="")),
            chat_visible=bool(pick("chat_visible", "is_chat_visible", default=False)),
            metadata=dict(pick("metadata", default={}) or {}),
        )

    def compact_summary(self, *, max_ocr_chars: int = 900, max_events: int = 8) -> str:
        """Text ngắn để đưa vào observer LLM nếu cần."""

        events = self.recent_events[-max_events:]
        parts = [
            f"active_app={self.active_app or 'unknown'}",
            f"active_window={compact_text(self.active_window_title, 180)}",
            f"focus_hint={self.focus_kind_hint or 'unknown'}",
            f"idle_seconds={self.idle_seconds:.1f}",
            f"cpu={self.cpu_percent:.1f}",
            f"ram={self.ram_percent:.1f}",
            f"chat_visible={self.chat_visible}",
        ]
        if self.url_or_path_hint:
            parts.append(f"path_or_url={compact_text(self.url_or_path_hint, 220)}")
        if events:
            parts.append("recent_events=" + " | ".join(compact_text(e, 120) for e in events))
        if self.ocr_text:
            parts.append("ocr=" + compact_text(self.ocr_text, max_ocr_chars))
        return "\n".join(parts)


@dataclass
class SignalDiff:
    """Diff giữa snapshot mới và trạng thái trước đó."""

    window_changed: bool = False
    app_changed: bool = False
    focus_changed: bool = False
    ocr_changed: bool = False
    screen_changed: bool = False
    event_changed: bool = False
    resource_pressure_changed: bool = False

    ocr_similarity: float = 1.0
    event_similarity: float = 1.0

    diff_score: float = 0.0
    repetition: float = 0.0
    novelty: float = 0.0
    idle_transition: Literal["active", "short_idle", "long_idle", "deep_idle"] = "active"

    reason: list[str] = field(default_factory=list)

    def should_run_text_observer(self, min_diff: float = 0.18) -> bool:
        return self.diff_score >= min_diff or self.ocr_changed or self.event_changed

    def should_consider_vision(self, min_diff: float = 0.20) -> bool:
        return self.diff_score >= min_diff and (self.screen_changed or self.ocr_changed or self.window_changed)


@dataclass
class ObserverFrame:
    """Kết quả quan sát nền.

    Lưu ý: frame này chỉ là hint. Không module nào nên coi nó là chân lý tuyệt đối.
    """

    focus_kind: FocusKind = "unknown"
    activity_intent: ActivityIntent = "unknown"

    salience: float = 0.0
    novelty: float = 0.0
    repetition: float = 0.0
    ambiguity: float = 0.0
    possible_error: float = 0.0
    user_presence: float = 0.0

    recommended_action: ObserverAction = "observe_only"
    recommended_budget: BudgetHint = "fast"
    confidence: float = 0.0

    should_call_vision: bool = False
    should_wake_main: bool = False
    should_interrupt_user: bool = False

    privacy_level: PrivacyLevel = "normal"
    evidence: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    debug: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.focus_kind = coerce_focus_kind(self.focus_kind)
        self.activity_intent = coerce_activity_intent(self.activity_intent)
        self.recommended_action = coerce_observer_action(self.recommended_action)
        self.salience = clamp01(self.salience)
        self.novelty = clamp01(self.novelty)
        self.repetition = clamp01(self.repetition)
        self.ambiguity = clamp01(self.ambiguity)
        self.possible_error = clamp01(self.possible_error)
        self.user_presence = clamp01(self.user_presence)
        self.confidence = clamp01(self.confidence)

    @property
    def is_actionable(self) -> bool:
        return self.recommended_action in {
            "update_body_bias",
            "call_vision_compressor",
            "wake_main_cognition",
        }

    def to_evidence_claim(self) -> str:
        """Render claim an toàn cho AttentionSalience.

        Không dùng nhãn `possible_error` trong text vì attention lexical có thể
        thấy chữ "error" rồi nâng risk dù giá trị là 0.00. Metric lỗi được nén
        thành `err=` và chỉ tag riêng mới nói đó là error-signal thật.
        """

        return (
            f"observer focus={self.focus_kind}; "
            f"intent={self.activity_intent}; "
            f"sal={self.salience:.2f}; "
            f"nov={self.novelty:.2f}; "
            f"rep={self.repetition:.2f}; "
            f"err={self.possible_error:.2f}; "
            f"presence={self.user_presence:.2f}; "
            f"action={self.recommended_action}"
        )

    def attention_tags(self) -> tuple[str, ...]:
        """Tags dành cho attention/working memory.

        Tags mới là nơi báo risk/debug thật. Text claim chỉ giữ metric nén.
        """

        tags = ["background_observer", f"observer_focus:{self.focus_kind}", f"observer_intent:{self.activity_intent}"]
        if self.possible_error >= 0.55:
            tags.append("observer_error_signal")
        if self.should_call_vision:
            tags.append("observer_call_vision")
        if self.should_wake_main:
            tags.append("observer_wake_hint")
        if self.repetition >= 0.65:
            tags.append("observer_repetition")
        if self.novelty >= 0.55:
            tags.append("observer_novelty")
        return tuple(tags)

    def to_body_bias_hint(self) -> dict[str, float | str]:
        """Hint nhẹ cho BodyBiasProjector.

        Không phải lệnh chuyển động. Godot/body autonomy vẫn tự quyết định.
        """

        curiosity = 0.0
        overload = 0.0
        solitude = 0.0
        preferred_anchor = "bottom_right"

        if self.focus_kind == "code":
            curiosity += 0.12 * self.salience
            preferred_anchor = "code_side"
        elif self.focus_kind in {"docs", "browser"}:
            curiosity += 0.06 * self.salience
            preferred_anchor = "code_side"
        elif self.focus_kind in {"media", "game"}:
            solitude += 0.08
            preferred_anchor = "bottom_right"

        if self.possible_error >= 0.60:
            curiosity += 0.12
            overload += 0.05

        if self.activity_intent in {"reading", "designing"}:
            solitude += 0.04
        if self.activity_intent in {"resting", "idle"}:
            solitude += 0.08

        return {
            "curiosity": clamp01(curiosity),
            "overload": clamp01(overload),
            "solitude": clamp01(solitude),
            "focus_kind": self.focus_kind,
            "preferred_anchor": preferred_anchor,
        }


@dataclass
class SemanticSceneNote:
    """Ghi chú semantic từ Qwen vision/compressor.

    File này khai báo trước để các bước sau không phải đổi contract.
    """

    visible_context: str = ""
    important_text: list[str] = field(default_factory=list)
    activity_intent: ActivityIntent = "unknown"
    semantic_family: str = "unknown"

    novelty: float = 0.0
    salience: float = 0.0
    possible_error: float = 0.0
    should_interrupt_user: bool = False
    confidence: float = 0.0

    # TẦNG-1: nghĩa + thay đổi là công dân hạng nhất, không chỉ là caption.
    content_summary: str = ""      # nội dung THẬT là gì (vd: "video u sầu về con mèo già")
    content_affect: str = ""       # sắc thái nội dung: sad|tense|joyful|neutral|...
    relation_to_user: str = ""     # cảnh này liên hệ gì với việc người dùng đang làm
    delta: str = ""                # đổi gì so với scene trước (rỗng nếu không rõ/không đổi)
    scene_id: str = ""             # id ổn định của scene này
    prev_scene_id: str = ""        # nối với scene ngay trước để "thay đổi" có chỗ bám

    source: Literal["qwen4b_vision", "ocr_only", "unknown"] = "unknown"
    evidence: list[str] = field(default_factory=list)
    debug: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.activity_intent = coerce_activity_intent(self.activity_intent)
        self.novelty = clamp01(self.novelty)
        self.salience = clamp01(self.salience)
        self.possible_error = clamp01(self.possible_error)
        self.confidence = clamp01(self.confidence)
        self.visible_context = compact_text(self.visible_context, 260)
        self.important_text = [compact_text(x, 180) for x in self.important_text[:8]]
        self.content_summary = compact_text(self.content_summary, 260)
        self.content_affect = compact_text(self.content_affect, 80)
        self.relation_to_user = compact_text(self.relation_to_user, 200)
        self.delta = compact_text(self.delta, 200)

    def to_evidence_claim(self) -> str:
        text = "; ".join(self.important_text[:3])
        parts = [
            f"scene={self.content_summary or self.visible_context}",
            f"family={self.semantic_family}",
            f"intent={self.activity_intent}",
        ]
        if self.content_affect:
            parts.append(f"affect={self.content_affect}")
        if self.delta:
            parts.append(f"delta={self.delta}")
        if self.relation_to_user:
            parts.append(f"relation={self.relation_to_user}")
        if text:
            parts.append(f"important_text={text}")
        parts.append(f"salience={self.salience:.2f}")
        parts.append(f"possible_error={self.possible_error:.2f}")
        return "; ".join(parts)


def clamp01(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = default
    return max(0.0, min(1.0, number))


def stable_hash(text: str) -> str:
    normalized = normalize_text(text)
    return hashlib.sha1(normalized.encode("utf-8", errors="ignore")).hexdigest()[:16]


def normalize_text(text: str) -> str:
    if not text:
        return ""
    value = unicodedata.normalize("NFKC", str(text))
    value = " ".join(value.replace("\x00", " ").split())
    return value.strip()


def normalize_ascii_fold(text: str) -> str:
    """Normalize dùng cho matching nhẹ tiếng Việt/Anh.

    Bỏ dấu để rule-based observer nhận được cả `lỗi` và `loi`.
    """

    text = normalize_text(text).lower()
    decomposed = unicodedata.normalize("NFD", text)
    return "".join(ch for ch in decomposed if unicodedata.category(ch) != "Mn")


def compact_text(text: str, max_chars: int = 240) -> str:
    value = normalize_text(text)
    if len(value) <= max_chars:
        return value
    return value[: max(0, max_chars - 1)].rstrip() + "…"


def coerce_focus_kind(value: Any) -> FocusKind:
    text = str(value or "unknown").strip().lower()
    return text if text in ALLOWED_FOCUS_KINDS else "unknown"  # type: ignore[return-value]


def coerce_activity_intent(value: Any) -> ActivityIntent:
    text = str(value or "unknown").strip().lower()
    return text if text in ALLOWED_ACTIVITY_INTENTS else "unknown"  # type: ignore[return-value]


def coerce_observer_action(value: Any) -> ObserverAction:
    text = str(value or "observe_only").strip().lower()
    return text if text in ALLOWED_OBSERVER_ACTIONS else "observe_only"  # type: ignore[return-value]
