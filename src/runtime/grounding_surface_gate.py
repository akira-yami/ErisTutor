# -*- coding: utf-8 -*-
"""Gate claim public dùng chung cho chat và autonomous.

Một câu public chỉ được phép claim tri giác/hành động khi SourceGroundingReport
có evidence tương ứng. Module này không viết lại câu, không sinh fallback và
không tự quyết nội dung. Nó chỉ trả verdict typed để cả hai lane dùng đúng cùng
một luật, thay vì chat nghiêm còn autonomous đi cửa sau như một quan chức nhỏ.
"""
from __future__ import annotations

from dataclasses import dataclass
import os
import re
import unicodedata
from typing import Any, Sequence


def _fold_vi(value: Any) -> str:
    """Chuẩn hóa tiếng Việt để các guard hẹp không phụ thuộc dấu/case."""

    text = unicodedata.normalize("NFD", str(value or "").lower())
    text = "".join(ch for ch in text if unicodedata.category(ch) != "Mn")
    text = text.replace("đ", "d")
    text = re.sub(r"[^a-z0-9_\s]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


# Chỉ bắt các claim về trạng thái hiện tại mà ERIS phải có sensor/evidence mới
# được nói như fact. Danh sách cố ý hẹp để không biến grounding guard thành một
# semantic classifier thứ hai.
#
# ĐÃ XOÁ (quá rộng, chặn cả thơ/văn thuần ngôn ngữ không phải perception claim):
#   - "ngoai troi / ben ngoai / ngoai kia / ngoai cua so" — autonomous dùng trong ẩn dụ
#   - "troi dang / hien tai / bay gio" — nói về thời tiết theo cảm xúc, không phải claim
#   - "hom nay troi / thoi tiet" — idem
# Chỉ giữ lại claim trực tiếp về perception sensor (thấy/nhìn/quan sát).
_DIRECT_OBSERVATION_PATTERNS = (
    re.compile(r"\btoi (?:dang )?(?:thay|nhin thay|nhin|quan sat)\b"),
    re.compile(r"\bnhin ra ngoai\b"),
)

_PRESENT_ENVIRONMENT_PATTERNS = (
    re.compile(r"\btroi (?:chac |co le |hinh nhu |dang |sap |da |co ve )*(?:toi|toi dan|mua|nang|nong|lanh|am u|am|lanh)\b"),
    re.compile(r"\b(?:ngoai troi|ben ngoai|ngoai kia|ngoai cua so)\b.*\b(?:toi|mua|nang|gio|yen|vang|im)\b"),
    re.compile(r"\b(?:gio|gió) (?:ben ngoai|ngoai troi|ngoai kia|ngoai cua so)\b"),
    re.compile(r"\b(?:anh sang yeu|anh sang|anh den|den trong phong|phong .*den|phong .*sang)\b"),
    re.compile(r"\b(?:khong gian|can phong|phong) (?:co ve |that |dang |rat )?(?:yen tinh|yen binh|im lang|vang lang)\b"),
    re.compile(r"\b(?:dem nay|toi nay|chieu nay|sang nay|hom nay) (?:that |co ve |rat )?(?:yen tinh|yen binh|im lang|vang lang)\b"),
    # Bare "âm thanh" is often a retrospective/abstract noun ("sức nặng của
    # âm thanh"), not evidence that ERIS currently hears something. Require an
    # explicit present-perception construction for generic sound.
    re.compile(r"\b(?:toi (?:dang )?(?:nghe|nghe thay)|dang nghe)\b.*\b(?:tieng|am thanh)\b"),
    re.compile(r"\b(?:tieng mua|tieng gio|tieng dong)\b.*\b(?:ngoai|ben ngoai|trong phong|luc nay|bay gio|dang)\b"),
    re.compile(r"\b(?:nhiet do|thoi tiet) (?:dang |hien |hom nay |bay gio |co ve )?\b"),
)

_PRESENT_USER_STATE_PATTERNS = (
    re.compile(r"\b(?:chac |co le |hinh nhu )?ban (?:dang |sap |co ve )?(?:met|buon ngu|ngoi may|ngoi truoc may|di ngu)\b"),
    re.compile(r"\buser (?:dang |sap |co ve )?(?:met|buon ngu|ngoi may|ngoi truoc may|di ngu)\b"),
)

_DIRECT_ENVIRONMENT_REF_PREFIXES = (
    "environment:",
    "weather:",
    "sensor:",
)

_DIRECT_OBSERVATION_REF_PREFIXES = (
    *_DIRECT_ENVIRONMENT_REF_PREFIXES,
    "vision:",
    "screen:",
    "telemetry:",
    "desktop:",
)

_CORRECTION_MARKERS = (
    "khong phai", "dau co", "co dau", "lam gi co", "sai", "nham",
    "cai gi", "xanh cai gi", "sap toi", "toi den noi", "noi gi vay",
    "noi gi the", "gi vay ma", "gi vay",
)


def _autonomous_strict_grounding_enabled() -> bool:
    """Env flag để tắt grounding gate cho autonomous khi creative mode."""
    raw = str(os.environ.get("ERIS_AUTONOMOUS_STRICT_GROUNDING", "1") or "1").strip().lower()
    return raw not in {"0", "false", "off", "no"}


def has_current_world_claim(text: str) -> bool:
    """True khi câu đang khẳng định một trạng thái hiện tại cần evidence."""

    folded = _fold_vi(text)
    return bool(
        folded
        and any(
            pattern.search(folded)
            for pattern in (
                *_DIRECT_OBSERVATION_PATTERNS,
                *_PRESENT_ENVIRONMENT_PATTERNS,
                *_PRESENT_USER_STATE_PATTERNS,
            )
        )
    )


def _matches_any(folded: str, patterns: Sequence[re.Pattern[str]]) -> bool:
    return any(pattern.search(folded) for pattern in patterns)


def current_world_claim_without_evidence(
    text: str,
    evidence_refs: Sequence[str] | None = None,
    *,
    claim_scope: str = "",
) -> bool:
    """Chặn claim hiện-tại thiếu evidence nhưng tôn trọng provenance của intent.

    ``claim_scope`` comes from the already-approved SpeechIntent.  A memory, past
    conversation or internal interpretation is not magically a live sensor claim
    merely because its wording contains nouns such as "âm thanh" or "im lặng".
    Explicit first-person perception ("tôi đang thấy/nghe...") remains guarded
    regardless of scope.
    """

    folded = _fold_vi(text)
    if not folded:
        return False
    refs = tuple(str(item or "").strip().lower() for item in (evidence_refs or ()) if str(item or "").strip())
    scope = str(claim_scope or "").strip().lower()
    non_live_scopes = {
        "past_conversation", "memory", "internal_interpretation",
        "hypothesis", "opinion", "retrospective",
    }
    if _matches_any(folded, _DIRECT_OBSERVATION_PATTERNS):
        return not any(ref.startswith(_DIRECT_OBSERVATION_REF_PREFIXES) for ref in refs)
    if scope in non_live_scopes:
        return False
    if _matches_any(folded, (*_PRESENT_ENVIRONMENT_PATTERNS, *_PRESENT_USER_STATE_PATTERNS)):
        return not any(ref.startswith(_DIRECT_ENVIRONMENT_REF_PREFIXES) for ref in refs)
    return False


def is_grounding_correction(user_text: str, previous_assistant_text: str) -> bool:
    """Nhận diện hẹp việc user sửa một claim tri giác/trạng thái vừa rồi của ERIS.

    Đây không phải bộ phân loại disagreement tổng quát. Nó chỉ bật khi câu ERIS
    trước đó có hình dạng current-world claim và user hiện tại chứa dấu hiệu phủ
    định/chỉnh fact đủ rõ. Nhờ vậy "xanh cái gì" không bị biến thành tranh luận
    quan điểm như lần chạy lỗi.
    """

    if not has_current_world_claim(previous_assistant_text):
        return False
    folded = _fold_vi(user_text)
    if not folded:
        return False
    return any(marker in folded for marker in _CORRECTION_MARKERS)


@dataclass(frozen=True, slots=True)
class GroundingSurfaceVerdict:
    allowed: bool
    annotations: tuple[Any, ...] = ()

    @property
    def violation_code(self) -> str:
        return "" if self.allowed else "UNGROUNDED_PERCEPTION_OR_ACTION_CLAIM"

    def retry_instruction(self) -> str:
        if self.allowed:
            return ""
        return (
            "The draft made a perception/action claim without current evidence. "
            "Rewrite it without claiming that ERIS is literally seeing, reading, opening, "
            "or completing anything. A hypothesis must name its evidence and leave room "
            "for the user to correct it."
        )


def evaluate_grounded_surface(
    engine: Any,
    text: str,
    *,
    report: Any = None,
    channel: str = "",
) -> GroundingSurfaceVerdict:
    """Chạy đúng một luật annotation cho mọi public lane.

    channel="autonomous": nếu ERIS_AUTONOMOUS_STRICT_GROUNDING=0 thì bỏ qua gate
    để không chặn creative speech không có perception claim thật.
    """

    if str(channel or "").strip().lower() == "autonomous" and not _autonomous_strict_grounding_enabled():
        return GroundingSurfaceVerdict(True, ())
    annotate = getattr(engine, "annotate_claims", None)
    if not callable(annotate) or not str(text or "").strip():
        return GroundingSurfaceVerdict(True, ())
    try:
        annotations = tuple(
            item
            for item in annotate(str(text or ""), report=report)
            if str(getattr(item, "claim_status", "grounded") or "grounded") != "grounded"
        )
    except Exception:
        # Gate không được tự bịa lỗi grounding khi chính annotator hỏng. Caller log
        # riêng, còn câu vẫn đi qua acceptance gate khác.
        return GroundingSurfaceVerdict(True, ())
    return GroundingSurfaceVerdict(not annotations, annotations)


__all__ = [
    "GroundingSurfaceVerdict",
    "_autonomous_strict_grounding_enabled",
    "current_world_claim_without_evidence",
    "evaluate_grounded_surface",
    "has_current_world_claim",
    "is_grounding_correction",
]
