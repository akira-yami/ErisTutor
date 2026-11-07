from __future__ import annotations

"""
orientation_context.py
======================

Compatibility façade + lexical analyzer cho Orientation/Ni/GoalPersistence.

Bản cũ của file này ôm quá nhiều việc:
    - normalize text
    - keyword bank
    - goal inference
    - closure detection
    - goal matching
    - activity inference
    - orientation scoring

Bản này cố ý hạ vai trò của file xuống còn ba nhóm việc:
    1. Contract chung: enum/dataclass để module cũ và module semantic mới dùng chung.
    2. Surface lexical helpers: normalize, token, anchor, closure, negation.
    3. Lexical orientation analyzer: đủ chạy khi chưa có semantic engine.

Semantic thật hơn nằm ở các file mới:
    - semantic_encoder.py
    - semantic_hypothesis.py
    - semantic_prototypes.py
    - orientation_semantics.py

Quy tắc thiết kế
----------------
- File này không còn là "semantic brain".
- Lexical chỉ dùng để tạo anchor/veto và fallback khi semantic chưa sẵn sàng.
- Những thứ cần chính xác như filename, negation, "chưa xong", "vẫn lỗi" vẫn nên
  ở lexical. Embedding mà đoán closure thì rất dễ tự tin sai, một đặc sản máy móc
  đáng được nhốt lại.
- Lexical helper trong file này chỉ là baseline dữ liệu. Engine chính dài hạn là
  HybridOrientationInterpreter trong orientation_semantics.py.
"""

import re
import unicodedata
from dataclasses import dataclass, field
from enum import Enum
from typing import Iterable, Mapping, Sequence


# ---------------------------------------------------------------------------
# Public exports
# ---------------------------------------------------------------------------

__all__ = [
    # Regex / constants
    "STOP_WORDS",
    "TECHNICAL_TERMS",
    "LEARNING_TERMS",
    "MEDIA_TERMS",
    "SHORT_FORM_TERMS",
    "PLAY_TERMS",
    "COMMUNICATION_TERMS",
    "RECOVERY_TERMS",
    "AVOIDANCE_TERMS",
    "SYSTEM_WAIT_TERMS",
    "TASK_VERBS_STRONG",
    "TASK_VERBS_SOFT",
    "DESIGN_TERMS",
    "CASUAL_MARKERS",
    "NEGATION_TERMS",
    "CLOSURE_TERMS",
    "REJECTION_AFTER_CLOSURE",
    "FILE_OR_MODULE_RE",
    # Helpers
    "clamp01",
    "clamp",
    "clean_text",
    "normalize",
    "tokens",
    "phrase_in",
    "has_any",
    "count_hits",
    "unique_recent",
    "extract_anchors",
    "goal_match_score",
    "has_negated_closure",
    "infer_closure_signal",
    "infer_goal_text_signal",
    # Types
    "ActivityIntentLabel",
    "GoalRelationLabel",
    "GoalTextKind",
    "ActivityIntent",
    "GoalRelation",
    "OrientationSignal",
    "GoalTextSignal",
    "ClosureSignal",
    "SemanticContextProbe",
]


# ---------------------------------------------------------------------------
# Text normalization / lexical helpers
# ---------------------------------------------------------------------------

_REMOVABLE_URL_BITS = re.compile(r"https?://|www\.|[?&=#%]")
_WORD_RE = re.compile(r"[a-z0-9#+.]+")

# Giữ stopword ở đây vì đây là phần lexical nền dùng bởi anchor và goal matching.
# Không nhét semantic label/prototype vào đây nữa. Prototype đã thuộc semantic_prototypes.py.
STOP_WORDS: set[str] = {
    # English function words
    "and", "the", "for", "from", "this", "that", "with", "then", "than",
    "into", "onto", "about", "what", "when", "where", "which", "while",
    "because", "should", "could", "would", "there", "their", "have", "has",
    "had", "was", "were", "are", "is", "be", "been", "being", "can", "will",
    "just", "only", "also", "very", "more", "most", "less", "some", "any",
    # Vietnamese function words after diacritic stripping
    "dang", "trong", "cua", "voi", "khi", "thi", "neu", "ban", "toi",
    "mot", "nhung", "khong", "duoc", "cho", "cai", "nay", "kia", "roi",
    "nhe", "nhi", "vay", "day", "nua", "qua", "hoi", "lam", "nha", "the",
    "la", "va", "hay", "de", "ve", "ra", "vao", "len", "xuong", "di",
}


def clamp01(value: object) -> float:
    """Ép một giá trị về [0, 1].

    Helper nhỏ nhưng dùng xuyên nhiều module. Để ở đây để tránh mỗi file tự viết
    một bản rồi mỗi bản sai theo phong cách riêng, một truyền thống hơi thừa.
    """

    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    if number != number:  # NaN
        return 0.0
    return max(0.0, min(1.0, number))


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, float(value)))


def clean_text(value: object, *, limit: int = 180) -> str:
    """Dọn text để lưu summary/evidence ngắn.

    Không dùng hàm này để match ngữ nghĩa sâu. Nó chỉ là vệ sinh đầu vào.
    """

    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if limit > 3 and len(text) > limit:
        return text[: limit - 3].rstrip() + "..."
    return text


def normalize(value: object) -> str:
    """Chuẩn hóa text cho lexical matching.

    - Lowercase.
    - Bỏ dấu tiếng Việt.
    - Đổi "đ" thành "d" thủ công vì Unicode decomposition không xử lý nó.
    - Dọn URL punctuation dễ gây nhiễu.

    Đây không phải semantic normalization. Nó chỉ giúp lexical anchor ổn định hơn.
    """

    raw = str(value or "").strip().lower().replace("đ", "d")
    raw = unicodedata.normalize("NFKD", raw)
    raw = "".join(ch for ch in raw if not unicodedata.combining(ch))
    raw = _REMOVABLE_URL_BITS.sub(" ", raw)
    raw = re.sub(r"[^a-z0-9#+./:\s_-]+", " ", raw)
    raw = raw.replace("_", " ").replace("-", " ")
    return " ".join(raw.split())


def tokens(value: object, *, keep_short_code_tokens: bool = True) -> set[str]:
    """Token lexical cho anchor/goal matching.

    Token ngắn bị bỏ, trừ vài token code như AI/UI/C#/C++.
    """

    result: set[str] = set()
    for token in _WORD_RE.findall(normalize(value)):
        if token in STOP_WORDS:
            continue
        if len(token) < 3 and not (keep_short_code_tokens and token in {"c#", "c++", "ai", "ui"}):
            continue
        result.add(token)
    return result


def phrase_in(normalized: str, phrase: str) -> bool:
    phrase_norm = normalize(phrase)
    if not phrase_norm:
        return False
    return phrase_norm in normalized


def has_any(normalized: str, phrases: Sequence[str]) -> bool:
    return any(phrase_in(normalized, phrase) for phrase in phrases)


def count_hits(normalized: str, phrases: Sequence[str]) -> int:
    return sum(1 for phrase in phrases if phrase_in(normalized, phrase))


def unique_recent(items: Iterable[str], *, limit: int = 8) -> tuple[str, ...]:
    """Giữ item unique theo thứ tự gần nhất.

    Dùng cho evidence/anchor. Không phải memory dedupe toàn cục.
    """

    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        text = clean_text(item, limit=160)
        key = normalize(text)
        if not text or key in seen:
            continue
        seen.add(key)
        out.append(text)
    return tuple(out[-limit:])


# ---------------------------------------------------------------------------
# Lexical phrase banks
# ---------------------------------------------------------------------------
# Những nhóm này chỉ là surface anchors. Semantic prototypes nằm ở
# semantic_prototypes.py. Đừng nhét thêm “ý nghĩa sâu” vào đây rồi gọi nó là
# semantic. Cái đó là keyword wearing a tiny philosopher hat.

TECHNICAL_TERMS: tuple[str, ...] = (
    "code", "coding", "source", "bug", "debug", "fix", "terminal", "commit",
    "review", "python", "c#", "c++", "godot", "vscode", "visual studio", "cursor",
    "api", "sdk", "stack overflow", "stackoverflow", "github", "git", "readme",
    "traceback", "exception", "error", "build", "compile", "runtime", "script",
    "class", "function", "method", "json", "xml", "udp", "http", "llm", "model",
    "coordinator", "engine", "module", "dataclass", "pipeline", "parser", "guard",
    "test", "pytest", "import", "syntax", "refactor", "semantic", "heuristic",
)

LEARNING_TERMS: tuple[str, ...] = (
    "tutorial", "course", "lesson", "guide", "docs", "documentation", "manual",
    "how to", "example", "explain", "giai thich", "huong dan", "hoc", "tai lieu",
    "fix loi", "sua loi", "cach fix", "learn", "reference", "research", "paper",
)

MEDIA_TERMS: tuple[str, ...] = (
    "youtube", "youtu", "video", "playlist", "podcast", "music", "lofi", "soundtrack",
    "nhac", "bai hat", "background", "stream", "watch", "xem video",
)

SHORT_FORM_TERMS: tuple[str, ...] = (
    "shorts", "tiktok", "reel", "reels", "doomscroll", "scroll vo thuc", "keo vo thuc",
)

PLAY_TERMS: tuple[str, ...] = (
    "steam", "game", "gaming", "anime", "manga", "movie", "netflix", "film", "trailer",
    "rimworld", "skyrim", "factorio", "genshin", "wuthering", "wuwa", "toram",
)

COMMUNICATION_TERMS: tuple[str, ...] = (
    "zalo", "messenger", "facebook", "discord", "slack", "teams", "telegram",
    "gmail", "mail", "meeting", "meet", "chat", "call", "zoom", "tin nhan", "nhan tin",
)

RECOVERY_TERMS: tuple[str, ...] = (
    "break", "rest", "relax", "chill", "nghi", "thu gian", "giai lao", "nghe nhac",
    "nghi mat", "hoi phuc", "met", "burnout", "tam nghi", "di ia", "an com", "ngu",
)

AVOIDANCE_TERMS: tuple[str, ...] = (
    "ne viec", "tron viec", "luoi", "procrastinate", "procrastination", "mat tap trung",
    "le viec", "lan nua", "cuon", "nghien", "sa lay", "avoid", "escape from friction",
)

SYSTEM_WAIT_TERMS: tuple[str, ...] = (
    "install", "installer", "update", "download", "loading", "build", "compile", "deploy",
    "render", "wait", "waiting", "cho build", "dang build", "dang cai", "setup", "restart",
)

TASK_VERBS_STRONG: tuple[str, ...] = (
    "fix", "sua", "debug", "implement", "add", "them", "build", "tao", "viet",
    "refactor", "review", "danh gia", "test", "wire", "connect", "noi", "rewrite",
    "nang cap", "toi uu", "patch", "replace", "split", "tach", "merge", "code lai",
)

TASK_VERBS_SOFT: tuple[str, ...] = (
    "nen lam", "can lam", "phai lam", "huong fix", "cach fix", "doc benh", "xem loi",
    "giai thich", "phan tich", "kiem tra", "soi", "danh gia", "on chua", "co nen",
)

DESIGN_TERMS: tuple[str, ...] = (
    "architecture", "kien truc", "design", "thiet ke", "pipeline", "cortex", "runtime",
    "semantic", "emergent", "memory", "state", "continuity", "relationship", "dynamics",
    "infj", "eris", "ni", "fe", "ti", "se", "goal", "planner", "speech", "meaning",
    "orientation", "hypothesis", "encoder", "prototype", "arbitration",
)

FILE_OR_MODULE_RE = re.compile(r"\b[a-zA-Z_][a-zA-Z0-9_]*(?:\.py|\.cs|\.gd|\.tscn|\.json|\.txt|\.xml)\b")

CASUAL_MARKERS: tuple[str, ...] = (
    "dang chill", "noi chuyen", "tam su", "cho vui", "ke di", "hihi", "haha", "ban gai",
    "di ia", "toi vui", "cuoc doi", "hom nay", "nghi xiu", "thich qua", "kaka", "hehe",
)

NEGATION_TERMS: tuple[str, ...] = (
    "khong", "chua", "not", "no", "never", "van chua", "chua he", "chua phai", "dau co",
)

CLOSURE_TERMS: tuple[str, ...] = (
    "done", "fixed", "resolved", "solved", "complete", "completed", "close_goal",
    "xong", "da xong", "giai quyet", "dong goal", "ổn rồi", "on roi", "hết lỗi", "het loi",
)

REJECTION_AFTER_CLOSURE: tuple[str, ...] = (
    "chua xong", "khong xong", "not done", "not fixed", "not resolved", "van loi",
    "van chua", "chua fixed", "chua resolve", "con loi", "fail", "sai roi", "chua on",
)


# ---------------------------------------------------------------------------
# Shared contracts
# ---------------------------------------------------------------------------

class ActivityIntentLabel(str, Enum):
    RESEARCH = "research"
    TECHNICAL_FOCUS = "technical_focus"
    SUPPORTIVE_BACKGROUND = "supportive_background"
    RECOVERY = "recovery"
    PLAY_OR_RECOVERY = "play_or_recovery"
    POSSIBLE_AVOIDANCE = "possible_avoidance"
    FRAGMENTED_SWITCHING = "fragmented_switching"
    SOCIAL_COORDINATION = "social_coordination"
    SYSTEM_WAITING = "system_waiting"
    UNKNOWN = "unknown"


class GoalRelationLabel(str, Enum):
    ALIGNED = "aligned"
    ADJACENT = "adjacent"
    NEUTRAL = "neutral"
    CONFLICTING = "conflicting"
    UNKNOWN = "unknown"


class GoalTextKind(str, Enum):
    CONCRETE_TASK = "concrete_task"
    DEBUG_THREAD = "debug_thread"
    DESIGN_DIRECTION = "design_direction"
    RESEARCH_THREAD = "research_thread"
    REVIEW_REQUEST = "review_request"
    CORRECTION_THREAD = "correction_thread"
    CASUAL = "casual"
    RECOVERY = "recovery"
    COMMENTARY = "commentary"
    NONE = "none"


@dataclass(frozen=True, slots=True)
class ActivityIntent:
    label: str = ActivityIntentLabel.UNKNOWN.value
    confidence: float = 0.25
    evidence: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class GoalRelation:
    label: str = GoalRelationLabel.UNKNOWN.value
    confidence: float = 0.25
    evidence: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class OrientationSignal:
    intent: ActivityIntent
    relation: GoalRelation
    orientation_coherence: float
    goal_conflict_score: float
    intervention_pressure: float
    confidence: float
    reason_span: str
    evidence: tuple[str, ...] = ()
    channels: Mapping[str, float] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class GoalTextSignal:
    """Tín hiệu rút từ user text để GoalPersistence dùng.

    File này chỉ cung cấp classifier lexical mỏng. Semantic goal classification mới
    nên nằm trong semantic_hypothesis/prototype hoặc engine cao hơn.
    """

    kind: str = GoalTextKind.NONE.value
    summary: str = ""
    confidence: float = 0.0
    strength: float = 0.0
    urgency: float = 0.0
    persist_permission: float = 0.0
    intervention_permission: float = 0.0
    actionability: float = 0.0
    evidence: tuple[str, ...] = ()
    anchors: tuple[str, ...] = ()

    @property
    def should_persist(self) -> bool:
        return self.persist_permission >= 0.55 and self.confidence >= 0.42 and bool(self.summary)

    @property
    def can_intervene(self) -> bool:
        return self.intervention_permission >= 0.55 and self.actionability >= 0.45


@dataclass(frozen=True, slots=True)
class ClosureSignal:
    closed: bool
    confidence: float = 0.0
    evidence: tuple[str, ...] = ()


@dataclass(slots=True)
class SemanticContextProbe:
    """Input nhẹ cho orientation.

    Giữ contract này ở đây vì cả lexical fallback và semantic hybrid đều cần cùng
    shape. Đây là dữ liệu, không phải engine.
    """

    focus_kind: str = "general"
    focus_text: str = ""
    window_text: str = ""
    browsing_text: str = ""
    memory_text: str = ""
    current_text: str = ""
    idle_seconds: float = 0.0
    open_window_count: int = 0
    active_goal_strength: float = 0.0
    active_goal_type: str = ""
    active_goal_intervention_permission: float = 0.0
    active_goal_summary: str = ""

    @property
    def combined_text(self) -> str:
        return " ".join(
            part
            for part in (
                self.focus_text,
                self.window_text,
                self.browsing_text,
                self.memory_text,
                self.current_text,
                self.active_goal_summary,
            )
            if part
        )


# ---------------------------------------------------------------------------
# Surface anchors / closure / goal helpers
# ---------------------------------------------------------------------------


def extract_anchors(text: str, *, max_items: int = 10) -> tuple[str, ...]:
    """Rút anchor lexical có ích cho semantic và goal merge.

    Anchor không phải kết luận. Nó chỉ nói: "có vật cụ thể ở đây" như file,
    module, framework, hoặc token dài đáng chú ý.
    """

    norm = normalize(text)
    result: list[str] = []

    for match in FILE_OR_MODULE_RE.findall(str(text or "")):
        result.append(match)

    for phrase_group in (TECHNICAL_TERMS, DESIGN_TERMS):
        for phrase in phrase_group:
            p = normalize(phrase)
            if p and p in norm:
                result.append(phrase)

    for token in sorted(tokens(text)):
        if len(token) >= 5:
            result.append(token)

    return unique_recent(result, limit=max_items)


def goal_match_score(
    left: str,
    right: str,
    *,
    left_anchors: Sequence[str] = (),
    right_anchors: Sequence[str] = (),
) -> float:
    """Khớp goal bằng token + anchor, dùng như fallback rẻ.

    Semantic thật có thể làm tốt hơn, nhưng goal merge vẫn cần anchor chính xác.
    Hai câu cùng có chữ "fix" không tự nhiên thành cùng một goal. Đáng tiếc là
    nhiều codebase đã sống như thể điều đó là chân lý.
    """

    left_tokens = tokens(left)
    right_tokens = tokens(right)
    if not left_tokens or not right_tokens:
        return 0.0

    token_overlap = len(left_tokens & right_tokens)
    token_ratio = token_overlap / max(1, min(len(left_tokens), len(right_tokens)))

    la = {normalize(a) for a in left_anchors if normalize(a)}
    ra = {normalize(a) for a in right_anchors if normalize(a)}
    anchor_overlap = len(la & ra)
    anchor_ratio = anchor_overlap / max(1, min(len(la), len(ra))) if la and ra else 0.0

    score = token_ratio * 0.50 + min(1.0, token_overlap / 5.0) * 0.16 + anchor_ratio * 0.34
    if anchor_overlap >= 1:
        score += 0.18
    if token_overlap >= 5:
        score += 0.06
    return clamp01(score)


def has_negated_closure(text: str) -> bool:
    """True nếu text có closure bị phủ định.

    Ví dụ:
        - "chưa xong"
        - "not fixed"
        - "vẫn lỗi"

    Đây là nơi lexical phải giữ quyền veto. Vector mà lẫn "xong" với "chưa
    xong" thì hệ thống sẽ đóng goal sai, rồi ngồi tự hào trong log.
    """

    norm = normalize(text)
    if has_any(norm, REJECTION_AFTER_CLOSURE):
        return True

    words = norm.split()
    closure_words = {normalize(term) for term in CLOSURE_TERMS}
    neg_words = {normalize(term) for term in NEGATION_TERMS}
    for i, word in enumerate(words):
        if word in closure_words:
            window = words[max(0, i - 3): i]
            if any(w in neg_words for w in window):
                return True
    return False


def infer_closure_signal(text: str, *, thought_stream_report: object = None) -> ClosureSignal:
    """Closure fallback cho GoalPersistence.

    Có thể nhận thêm thought_stream_report cũ để giữ tương thích, nhưng file này
    không biết chi tiết thought stream.
    """

    norm = normalize(text)
    evidence: list[str] = []
    confidence = 0.0

    if has_negated_closure(text):
        return ClosureSignal(False, 0.0, ("negated_closure",))

    if has_any(norm, CLOSURE_TERMS):
        evidence.append("closure_phrase")
        confidence = 0.74

    for disturbance in tuple(getattr(thought_stream_report, "disturbances", ()) or ()):  # backward-safe
        if bool(getattr(disturbance, "closes_loop", False)):
            evidence.append("thought_stream_closes_loop")
            confidence = max(confidence, 0.64)

    return ClosureSignal(confidence >= 0.55, clamp01(confidence), tuple(evidence))


def infer_goal_text_signal(text: str, *, channel: str = "chat", force_speak: bool = False) -> GoalTextSignal:
    """Classifier lexical mỏng cho text user.

    Đây là bản fallback để GoalPersistence cũ không chết. Nó không cố hiểu sâu.
    Semantic classification nên được thêm ở tầng mới bằng prototype/hypothesis.
    """

    raw = clean_text(text, limit=220)
    norm = normalize(raw)
    if not raw:
        return GoalTextSignal()

    anchors = extract_anchors(raw)
    evidence: list[str] = []
    token_count = len(tokens(raw))
    is_direct_channel = channel in {"chat", "force_speak"} or bool(force_speak)

    strong_task = has_any(norm, TASK_VERBS_STRONG)
    soft_task = has_any(norm, TASK_VERBS_SOFT)
    has_design = has_any(norm, DESIGN_TERMS)
    has_tech = has_any(norm, TECHNICAL_TERMS)
    has_learning = has_any(norm, LEARNING_TERMS)
    has_file_anchor = bool(FILE_OR_MODULE_RE.search(raw))
    has_question_shape = "?" in raw or has_any(norm, ("sao", "vi sao", "tai sao", "nen", "co nen", "what", "why", "how"))
    correction_like = has_any(norm, ("sai roi", "khong phai", "thieu", "chua dung", "nham", "fix lai", "doc lai", "van loi"))

    if has_any(norm, CASUAL_MARKERS) and not (strong_task or anchors):
        return GoalTextSignal(
            kind=GoalTextKind.CASUAL.value,
            summary=raw,
            confidence=0.58,
            evidence=("casual_marker",),
            anchors=anchors,
        )

    if has_any(norm, RECOVERY_TERMS) and not (strong_task or anchors):
        return GoalTextSignal(
            kind=GoalTextKind.RECOVERY.value,
            summary=raw,
            confidence=0.62,
            evidence=("recovery_marker",),
            anchors=anchors,
        )

    # Correction thread: user đang sửa hướng hiểu/làm của ERIS.
    if is_direct_channel and correction_like and (has_tech or has_design or anchors):
        evidence.append("technical_or_design_correction")
        return GoalTextSignal(
            kind=GoalTextKind.CORRECTION_THREAD.value,
            summary=raw,
            confidence=0.66,
            strength=0.54,
            urgency=0.46,
            persist_permission=0.62,
            intervention_permission=0.42,
            actionability=0.56,
            evidence=tuple(evidence),
            anchors=anchors,
        )

    # Concrete task: lệnh rõ + anchor kỹ thuật/design/file.
    if is_direct_channel and (strong_task or has_file_anchor) and (has_tech or has_design or anchors):
        evidence.append("explicit_task_with_anchor")
        return GoalTextSignal(
            kind=GoalTextKind.CONCRETE_TASK.value,
            summary=raw,
            confidence=0.76,
            strength=0.72,
            urgency=0.52,
            persist_permission=0.82,
            intervention_permission=0.58,
            actionability=0.78,
            evidence=tuple(evidence),
            anchors=anchors,
        )

    # Review/debug: câu hỏi hoặc yêu cầu đánh giá kỹ thuật.
    if is_direct_channel and (soft_task or has_question_shape) and (has_tech or has_design):
        kind = GoalTextKind.DEBUG_THREAD.value if has_any(norm, ("loi", "error", "bug", "debug", "fail")) else GoalTextKind.REVIEW_REQUEST.value
        evidence.append("technical_question_or_review")
        return GoalTextSignal(
            kind=kind,
            summary=raw,
            confidence=0.64,
            strength=0.58,
            urgency=0.42,
            persist_permission=0.64,
            intervention_permission=0.38,
            actionability=0.54,
            evidence=tuple(evidence),
            anchors=anchors,
        )

    # Design direction: có hướng kiến trúc nhưng chưa chắc là task cụ thể.
    if is_direct_channel and has_design and token_count >= 5:
        evidence.append("design_direction")
        return GoalTextSignal(
            kind=GoalTextKind.DESIGN_DIRECTION.value,
            summary=raw,
            confidence=0.58,
            strength=0.50,
            urgency=0.30,
            persist_permission=0.58,
            intervention_permission=0.26,
            actionability=0.38,
            evidence=tuple(evidence),
            anchors=anchors,
        )

    if is_direct_channel and has_learning and (has_question_shape or has_tech):
        evidence.append("research_thread")
        return GoalTextSignal(
            kind=GoalTextKind.RESEARCH_THREAD.value,
            summary=raw,
            confidence=0.55,
            strength=0.42,
            urgency=0.24,
            persist_permission=0.52,
            intervention_permission=0.18,
            actionability=0.32,
            evidence=tuple(evidence),
            anchors=anchors,
        )

    return GoalTextSignal(
        kind=GoalTextKind.COMMENTARY.value if token_count >= 5 else GoalTextKind.NONE.value,
        summary=raw,
        confidence=0.30,
        evidence=("insufficient_goal_commitment",),
        anchors=anchors,
    )


# ---------------------------------------------------------------------------
# Lexical baseline interpreter
# ---------------------------------------------------------------------------
