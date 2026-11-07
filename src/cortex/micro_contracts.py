from __future__ import annotations

from dataclasses import dataclass
import re
import unicodedata
from typing import Literal

IssueSeverity = Literal["fatal", "rewriteable", "soft"]
FamilySource = Literal[
    "unknown",
    "meaning_layer",
    "speech_core",
    "action_planner",
    "coordinator",
    "manual",
    "lexical_fingerprint",
    "fallback_keyword",
]


@dataclass(frozen=True, slots=True)
class MicroLinePolicyResult:
    """Kết quả bước chọn dạng câu trước khi gọi LLM hoặc trước khi gửi final.

    Lớp này chỉ nói "dạng câu nào đang được phép". Nó không được tự hiểu
    ý nghĩa sâu, không được tự quyết định personality và không được đoán tâm lý
    user. Những việc đó thuộc MeaningLayer, SpeechCore, RelationshipDynamics
    hoặc PersonalityLens. Ở đây chỉ là cửa contract nhỏ để câu public không lặp,
    không bịa màn hình, không claim quá quyền.
    """

    allowed_line_type: str = "silence"
    reject_reasons: tuple[str, ...] = ()
    signal_score: float = 0.0
    risk_level: str = "texture"
    anchor_source: str = ""
    anchor_text: str = ""

    @property
    def allowed(self) -> bool:
        return not self.reject_reasons


@dataclass(frozen=True, slots=True)
class SemanticRepeatResult:
    """Kết quả kiểm tra lặp nghĩa.

    family_source là trường quan trọng nhất trong bản tách mới.
    - meaning_layer/speech_core/action_planner/coordinator/manual: family đáng tin.
    - lexical_fingerprint: chỉ là trùng câu hoặc gần trùng bề mặt.
    - fallback_keyword: chỉ là marker yếu, KHÔNG được tự chặn tư duy.

    Trước đây fallback keyword tự nói "tôi yếu" rồi vẫn block. Khá đúng tinh
    thần phần mềm: tự thú nhận tội nhưng vẫn giữ chìa khóa nhà kho.
    """

    rejected: bool = False
    family: str = ""
    reason: str = ""
    family_source: str = "unknown"
    confidence: float = 0.0
    weak: bool = False
    fingerprint: str = ""


@dataclass(frozen=True, slots=True)
class ValidationIssue:
    """Một lỗi policy có severity rõ ràng.

    - fatal: câu nên bị bỏ hoặc im, thường do grounding/capability/dependency rõ.
    - rewriteable: câu có thể rewrite để giữ ý nhưng giảm rủi ro.
    - soft: chỉ là dấu hiệu yếu, dùng để hạ confidence/debug, KHÔNG nên chặn.

    Public API cũ validate_text() chỉ trả fatal + rewriteable để tránh các hint
    mềm làm coordinator panic. validate_text_detailed() mới trả đầy đủ.
    """

    code: str
    severity: IssueSeverity = "fatal"
    hint: str = ""


@dataclass(frozen=True, slots=True)
class RelationalContext:
    """Context quan hệ dùng để giới hạn mức ERIS được lộ attachment.

    Các score này phải được dựng từ lịch sử thật, không phải một câu user đơn lẻ.
    Quan hệ là quán tính và tích tụ. Nếu regex thấy chữ "nhớ" rồi bật bi kịch,
    đó không phải INFJ; đó là máy bán kịch bản đang giảm giá.
    """

    attachment_level: float = 0.0
    continuity_score: float = 0.0
    social_resonance: float = 0.0
    recent_presence_density: float = 0.0
    abandonment_sensitivity: float = 0.0
    trust_stability: float = 0.0
    relationship_stage: str = "unknown"


@dataclass(frozen=True, slots=True)
class RelationalRewritePlan:
    """Plan rewrite relational, không phải câu thoại canon.

    seed_line được giữ để tương thích API cũ rewrite_relational_line(). Code mới
    nên ưu tiên style_hint/forbidden_moves/theme/stage rồi để LLM sinh câu, vì
    policy mà viết thay toàn bộ câu thì ERIS lại thành bảng variant đội tóc giả.
    """

    theme: str = ""
    stage: str = "none"
    allowed_exposure: float = 0.0
    seed_line: str = ""
    style_hint: str = ""
    forbidden_moves: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class Anchor:
    """Nguồn neo cho một câu quan sát.

    Observation phải có anchor. Residue/presence thì không bắt buộc, vì chúng là
    nhịp hiện diện chứ không phải claim nhìn màn hình.
    """

    source: str = ""
    text: str = ""
    confidence: float = 0.0


TRUSTED_FAMILY_SOURCES = {
    "meaning_layer",
    "speech_core",
    "action_planner",
    "coordinator",
    "manual",
}


def clamp01(value) -> float:
    """Ép số về 0..1, chống NaN và input kỳ quặc."""

    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return 0.0
    if numeric != numeric:  # NaN guard.
        return 0.0
    return max(0.0, min(1.0, numeric))


def normalize_text(text: str) -> str:
    """Chuẩn hóa tiếng Việt cho heuristic bề mặt.

    Hàm này phục vụ lint, không phục vụ hiểu nghĩa sâu. Dùng nó để làm feature
    yếu thì được; dùng nó để kết luận ý định user thì lại quay về thời đồ đá.
    """

    lowered = str(text or "").strip().lower()
    try:
        remojibake = lowered.encode("latin1").decode("utf-8")
        if remojibake:
            lowered = remojibake
    except (UnicodeEncodeError, UnicodeDecodeError):
        pass
    lowered = unicodedata.normalize("NFD", lowered)
    lowered = "".join(ch for ch in lowered if unicodedata.category(ch) != "Mn")
    lowered = lowered.replace("đ", "d").replace("Đ", "d")
    lowered = re.sub(r"[^\w\s]", " ", lowered)
    return " ".join(lowered.split())


def clean_family(family: str) -> str:
    """Làm sạch semantic family để tránh leak ký tự lạ vào trace."""

    family = str(family or "").strip().lower()
    return re.sub(r"[^a-z0-9_\-]", "", family)


def family_source_is_trusted(source: str) -> bool:
    return str(source or "").strip().lower() in TRUSTED_FAMILY_SOURCES


def word_count(text: str) -> int:
    return len([item for item in re.split(r"\s+", str(text or "").strip(" .?!")) if item])


def content_tokens(text: str, *, min_len: int = 3) -> tuple[str, ...]:
    """Token nội dung nhẹ, bỏ filler phổ biến để tính fingerprint/overlap."""

    normalized = normalize_text(text)
    ignored = {
        "ban", "toi", "mot", "nhung", "van", "dang", "hoi", "nay", "kia",
        "la", "co", "thi", "roi", "cho", "cai", "do", "day", "nay", "nua",
        "thoi", "nhu", "the", "voi", "cua", "trong", "ngoai", "len", "xuong",
    }
    return tuple(
        token for token in normalized.split()
        if len(token) >= min_len and token not in ignored
    )


def lexical_fingerprint(text: str, *, max_tokens: int = 8) -> str:
    """Fingerprint bề mặt để bắt câu y hệt/gần y hệt.

    Đây không phải semantic family. Nó chỉ nói: câu này trông giống câu cũ.
    Exact lexical repeat có thể block nhẹ; fallback keyword thì không.
    """

    tokens = content_tokens(text)
    if not tokens:
        return ""
    return " ".join(tokens[:max_tokens])


def phrase_in_normalized(normalized: str, phrase: str) -> bool:
    """Match phrase có biên token tương đối sạch.

    Vẫn là heuristic, nhưng ít tệ hơn `marker in normalized`. Vâng, tiêu chuẩn
    kỹ thuật đôi khi chỉ là "ít tệ hơn". Buồn nhưng dùng được.
    """

    normalized = str(normalized or "")
    phrase = str(phrase or "").strip()
    if not normalized or not phrase:
        return False
    escaped = re.escape(phrase)
    return re.search(rf"(?<![a-z0-9_]){escaped}(?![a-z0-9_])", normalized) is not None


def any_phrase(normalized: str, phrases: tuple[str, ...] | list[str] | set[str]) -> bool:
    return any(phrase_in_normalized(normalized, phrase) for phrase in phrases)


def dedupe_issues(issues: list[ValidationIssue] | tuple[ValidationIssue, ...]) -> tuple[ValidationIssue, ...]:
    seen: set[str] = set()
    deduped: list[ValidationIssue] = []
    for issue in issues:
        if not issue.code or issue.code in seen:
            continue
        seen.add(issue.code)
        deduped.append(issue)
    return tuple(deduped)
