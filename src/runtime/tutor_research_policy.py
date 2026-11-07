# -*- coding: utf-8 -*-
"""Chính sách tra cứu nhẹ qua Tutor hiện có.

Module này cố ý KHÔNG dựng Deep Research API, Google grounding, agent nền hay
EvidenceBundle nhiều tầng. Nó chỉ quyết định khi nào đáng nhờ Tutor, resolve câu
follow-up ngắn về đúng chủ đề trước đó, rồi bọc chủ đề bằng một prompt gọn để
Tutor tự dùng khả năng tìm kiếm của giao diện web nếu cần.

Mục tiêu là giữ chi phí thấp:
- câu kiến thức đơn giản vẫn để main brain trả lời;
- chỉ câu có yêu cầu nghiên cứu/kiểm chứng rõ hoặc đủ phức tạp mới đi Tutor;
- prompt và câu trả lời đều có hard budget;
- không gọi thêm LLM chỉ để rewrite, distill hay "phân tích về việc phân tích".
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Iterable, Sequence


def _runtime_bool(*path: str, default: bool = True) -> bool:
    """Đọc runtime_settings nếu có, fallback env/giá trị mặc định.

    Detector search/research phải nghe công tắc UI, không phải tự ý mở browser như
    một con mèo biết dùng Wi-Fi.
    """

    try:  # pragma: no cover - runtime store có thể chưa có trong unit test lẻ
        from runtime.runtime_settings_store import runtime_bool
        return bool(runtime_bool(*path, default=default))
    except Exception:
        return bool(default)

try:  # pragma: no cover
    from cortex.tutor.secret_scrubber import scrub_secrets
except Exception:  # pragma: no cover
    def scrub_secrets(text: object) -> str:  # type: ignore
        return str(text or "")


_EXPLICIT_RESEARCH_MARKERS = (
    "nghien cuu", "dao sau", "tim hieu ky", "tra ky", "tra cuu ky",
    "kiem chung", "xac minh", "tim nguon", "tim tai lieu",
    "hoi tutor", "hoi su phu", "hoi gemini", "nho gemini", "keu gemini",
    "gemini hay", "gemini nghien cuu", "tutor nghien cuu",
    "fix elegant", "cach fix", "deep research", "research ky", "research sau",
)

_EXPLICIT_SEARCH_MARKERS = (
    # Search bình thường: user muốn Eris ra ngoài web kiểm tra, nhưng chưa cần
    # phân tích sâu. Tách khỏi research để khỏi mỗi câu “tra cứu giúp” lại kéo
    # theo nghi lễ nghiên cứu nhiều bước, một thú vui rất tốn tiền của loài người.
    "search web", "gemini search", "google thu", "google giup", "len web",
    "len mang", "tim kiem", "tra cuu", "tim giup", "tim ho", "xem web",
    "tim tren web", "check web", "check giup", "check thu", "fact check nhanh",
    "co nguon khong", "dan nguon", "nguon dau", "link dau",
)

_FRESHNESS_MARKERS = (
    "hom nay", "bay gio", "hien tai", "moi nhat", "gan day", "vua ra",
    "vua cong bo", "cap nhat", "lich", "gia", "ty gia", "thoi tiet",
    "tin moi", "phien ban moi", "release", "ban moi", "dang hot",
)

# Dùng để bắt câu hỏi lái/lắt léo: user không nói thẳng "search web",
# nhưng bản chất là đang hỏi một claim có thể sai vì thời điểm, chính sách,
# trạng thái tài khoản/dịch vụ, tin đồn, phiên bản, giá, luật, lịch, v.v.
# Regex thuần kiểu marker vẫn chỉ là hàng rào ngoài cổng, nên khối này cố tình
# chấm theo cặp "dấu hiệu cần verify" + "miền dễ thay đổi" thay vì bắt một câu
# thần chú duy nhất. Con người rất sáng tạo khi muốn làm mơ hồ mọi thứ.
_VERIFY_TRAP_MARKERS = (
    "co that", "that khong", "chuan khong", "dung khong", "co dung la",
    "co phai la", "co phai", "nghe noi", "hinh nhu", "tin don",
    "fake khong", "scam khong", "lua dao khong", "uy tin khong",
    "bi ban", "bi khoa", "bi chan", "bi vo hieu hoa", "het han",
    "con dung duoc", "con hoat dong", "co con", "da doi chua", "doi chua",
    "vua bi", "sap bi", "dang bi", "co bi", "co duoc khong", "hop le khong",
)

_UPDATE_SENSITIVE_MARKERS = (
    "api", "key", "token", "model", "gemini", "openai", "chatgpt",
    "claude", "openrouter", "qwen", "gemma", "terra", "workspace",
    "tai khoan", "account", "server", "website", "web", "app", "plugin",
    "extension", "repo", "github", "driver", "windows", "godot",
    "version", "phien ban", "ban moi", "release", "gia", "ty gia",
    "lich", "su kien", "thoi tiet", "luat", "quy dinh", "chinh sach",
    "ceo", "chu tich", "tong thong", "bo truong", "mini world", "game",
    "ban acc", "green net", "greennet", "workspace",
)

_COMPLEXITY_MARKERS = (
    "so sanh", "danh gia", "phan tich", "chi tiet", "toan dien", "toan bo",
    "chuyen sau", "nhieu nguon", "co nguon", "dan nguon", "bang chung",
    "moi nhat", "hien tai", "vua cong bo", "cap nhat", "thuc hu",
    "kiem tra lai", "doi chieu",
)

_REFERENCE_MARKERS = (
    "phan nay", "phan do", "phan tren", "cai nay", "cai do", "cai tren",
    "y nay", "y do", "y vua roi", "o tren", "vua nay", "luc nay", "hoi nay",
    "chi tiet hon", "sau hon", "ky hon", "them di", "vu nay", "vu do",
)

_FILLER_WORDS = {
    "di", "nhe", "nha", "thu", "dum", "giup", "toi", "cho", "ban", "em",
    "nghien", "cuu", "dao", "sau", "tim", "hieu", "ky", "tra", "cuu", "lai",
    "chi", "tiet", "hon", "phan", "cai", "y", "nay", "do", "tren", "vua",
    "roi", "luc", "hoi", "them", "vu", "ve", "gemini", "tutor", "su", "phu",
    "search", "web", "research", "please", "hay", "fix", "elegant",
}

_SOURCE_HEADINGS = ("nguon", "nguồn", "sources", "tham khao", "tham khảo")


def normalize_marker_text(text: object) -> str:
    """Chuẩn hóa tiếng Việt để detector không phải nuôi cả sở thú dấu câu."""

    value = unicodedata.normalize("NFKD", str(text or ""))
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    value = value.lower().replace("đ", "d")
    value = re.sub(r"[^a-z0-9]+", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def _has_marker(normalized: str, marker: str) -> bool:
    """Match marker theo ranh giới token, không match bậy `gia` trong `giao`."""

    if not normalized or not marker:
        return False
    pattern = r"(?<![a-z0-9])" + re.escape(marker.strip()) + r"(?![a-z0-9])"
    return re.search(pattern, normalized) is not None


def _has_any_marker(normalized: str, markers: Sequence[str] | Iterable[str]) -> bool:
    return any(_has_marker(normalized, marker) for marker in markers)


def is_explicit_research_request(text: object) -> bool:
    normalized = normalize_marker_text(text)
    return bool(normalized and _has_any_marker(normalized, _EXPLICIT_RESEARCH_MARKERS))


def is_explicit_search_request(text: object) -> bool:
    """User chủ động yêu cầu search/tra cứu nhanh, không nhất thiết research sâu."""

    normalized = normalize_marker_text(text)
    return bool(normalized and _has_any_marker(normalized, _EXPLICIT_SEARCH_MARKERS))


def needs_fresh_web(text: object) -> bool:
    """Câu hỏi có tính thời điểm nên main brain không được tự tin từ trí nhớ cũ."""

    normalized = normalize_marker_text(text)
    return bool(normalized and _has_any_marker(normalized, _FRESHNESS_MARKERS))


def needs_external_verification(text: object) -> bool:
    """Bắt câu hỏi lái cần verify dù user không nói chữ search.

    Ví dụ: "workspace này bị ban à", "key này còn dùng được không",
    "nghe nói model X đổi policy đúng không". Nếu chỉ dùng explicit marker,
    các câu này sẽ lọt và Terra có thể trả bằng trí nhớ cũ. Không vui.
    """

    raw = str(text or "")
    normalized = normalize_marker_text(raw)
    if not normalized:
        return False
    if needs_fresh_web(normalized):
        return True

    has_trap = _has_any_marker(normalized, _VERIFY_TRAP_MARKERS)
    if not has_trap:
        return False

    # Nếu hỏi một claim lắt léo trong miền dễ thay đổi thì route search nhanh.
    if _has_any_marker(normalized, _UPDATE_SENSITIVE_MARKERS):
        return True

    # Câu có URL/domain hoặc mã phiên bản thường cần xác minh ngoài, dù user không
    # dùng từ "search".
    if re.search(r"\b(?:https?://|www\.|[a-z0-9-]+\.(?:com|net|org|io|dev|ai|vn))\b", raw, re.I):
        return True
    if re.search(r"\bv?\d+(?:\.\d+){1,3}\b", raw, re.I):
        return True

    return False


def _looks_reference_only(text: object) -> bool:
    normalized = normalize_marker_text(text)
    if not normalized:
        return True
    if _has_any_marker(normalized, _REFERENCE_MARKERS):
        # Nếu ngoài cụm tham chiếu vẫn còn một chủ đề đủ rõ thì không coi là reference-only.
        tokens = [token for token in normalized.split() if token not in _FILLER_WORDS]
        return len(tokens) <= 2
    tokens = [token for token in normalized.split() if token not in _FILLER_WORDS]
    return len(tokens) <= 1 and is_explicit_research_request(text)



_CORRECTION_MARKERS = (
    "khong y toi", "khong phai", "y toi la", "toi noi", "toi muon noi",
    "y la", "dung hon", "chinh lai", "sua lai", "ý tôi", "không phải",
)

@dataclass(frozen=True, slots=True)
class FollowupAnchorCandidate:
    text: str
    score: float
    reason: str


@dataclass(frozen=True, slots=True)
class FollowupAnchorResolution:
    topic: str
    confidence: float
    anchor_text: str = ""
    anchor_index: int = -1
    assumption_note: str = ""
    misread_risk: str = "low"  # low | medium | high
    candidates: tuple[FollowupAnchorCandidate, ...] = ()


def _content_tokens(text: object) -> set[str]:
    normalized = normalize_marker_text(text)
    tokens: set[str] = set()
    for token in normalized.split():
        if token in _FILLER_WORDS:
            continue
        if len(token) < 3:
            continue
        # Những từ điều phối/research không giúp chọn anchor.
        if token in {"nghien", "cuu", "search", "web", "tutor", "gemini", "hay", "giup", "kiem", "chung", "chi", "tiet", "hon"}:
            continue
        tokens.add(token)
    return tokens


def _candidate_items(prior_user_turns: Sequence[object] | Iterable[object]) -> list[str]:
    out: list[str] = []
    for item in list(prior_user_turns or ()):
        text = str(getattr(item, "text", item) or "").strip()
        if not text:
            continue
        if _looks_reference_only(text):
            continue
        out.append(text)
    return out[-8:]


def _anchor_score(current: str, candidate: str, *, recency_rank: int, current_tokens: set[str]) -> FollowupAnchorCandidate:
    candidate_norm = normalize_marker_text(candidate)
    candidate_tokens = _content_tokens(candidate)
    overlap = len(current_tokens & candidate_tokens)
    # recency_rank: 0 là gần nhất. Neo hội thoại thường gần đây, nhưng nếu user nêu
    # "Gemma" thì candidate có Gemma phải thắng candidate gần hơn. Không thì Eris lại
    # nghiên cứu nhầm dòng trên, một môn thể thao regex nguy hiểm.
    score = 0.42 + max(0.0, 0.22 - 0.035 * recency_rank)
    reasons = [f"recent={recency_rank}"]
    if overlap:
        score += min(0.42, 0.18 * overlap)
        reasons.append(f"overlap={overlap}")
    if any(tok in candidate_norm for tok in ("gemini", "gemma", "tutor", "eris", "tts", "qwen")):
        score += 0.05
        reasons.append("project_entity")
    if len(candidate_tokens) >= 4:
        score += 0.04
        reasons.append("substantive")
    return FollowupAnchorCandidate(text=candidate[:900], score=min(1.0, score), reason=",".join(reasons))


def resolve_research_anchor(current_text: object, prior_user_turns: Sequence[object] | Iterable[object] = ()) -> FollowupAnchorResolution:
    """Resolve follow-up research/search về anchor có confidence và rủi ro.

    Bản cũ chỉ trả string nên dễ "lấy turn gần nhất". Bản này vẫn không gọi LLM,
    nhưng giữ candidate score, correction focus và assumption_note để ledger/panel
    biết Eris đang bám neo nào. Nhỏ gọn, ít token, bớt phong cách bói toán.
    """

    current = str(current_text or "").strip()
    current_norm = normalize_marker_text(current)
    candidates_raw = _candidate_items(prior_user_turns)
    current_tokens = _content_tokens(current)
    has_reference = _has_any_marker(current_norm, _REFERENCE_MARKERS)
    has_correction = _has_any_marker(current_norm, _CORRECTION_MARKERS)

    # Nếu câu hiện tại đã là một query độc lập, giữ nguyên. Nhưng nếu nó vừa có
    # "phần/cái/ý" vừa có entity như Gemma, ta vẫn gắn anchor để không mất ngữ cảnh.
    independent = bool(current and not _looks_reference_only(current))
    if independent and not (has_reference or has_correction):
        return FollowupAnchorResolution(
            topic=current[:900],
            confidence=0.93,
            anchor_text="",
            anchor_index=-1,
            assumption_note="query_standalone",
            misread_risk="low",
            candidates=(),
        )

    scored: list[tuple[int, FollowupAnchorCandidate]] = []
    for rev_idx, candidate in enumerate(reversed(candidates_raw)):
        if normalize_marker_text(candidate) == current_norm:
            continue
        scored.append((len(candidates_raw) - 1 - rev_idx, _anchor_score(current, candidate, recency_rank=rev_idx, current_tokens=current_tokens)))
    scored.sort(key=lambda item: item[1].score, reverse=True)
    packed = tuple(candidate for _, candidate in scored[:4])

    if not scored:
        if current:
            return FollowupAnchorResolution(
                topic=current[:900],
                confidence=0.48 if independent else 0.18,
                assumption_note="no_prior_anchor_found",
                misread_risk="medium" if independent else "high",
                candidates=(),
            )
        return FollowupAnchorResolution(topic="", confidence=0.0, assumption_note="empty", misread_risk="high")

    anchor_idx, best = scored[0]
    confidence = max(0.0, min(0.96, best.score))
    risk = "low" if confidence >= 0.72 else "medium" if confidence >= 0.52 else "high"
    assumption = "resolved_from_recent_context"
    topic = best.text

    # Correction/focus: "không, ý tôi là phần Gemma" cần giữ cả focus hiện tại và
    # anchor cũ. Nếu chỉ trả nguyên câu hiện tại, Tutor sẽ search một mẩu cụt ngủn.
    focus_tokens = sorted(current_tokens)
    if independent and (has_reference or has_correction) and focus_tokens:
        focus = " ".join(focus_tokens[:8])
        topic = f"{current} | Neo hội thoại: {best.text}"[:900]
        assumption = f"focused_followup:{focus}"
        confidence = min(0.96, max(confidence, 0.68))
        risk = "medium" if confidence < 0.76 else "low"

    if not independent and current:
        assumption = "reference_followup_resolved"

    return FollowupAnchorResolution(
        topic=topic[:900],
        confidence=confidence,
        anchor_text=best.text,
        anchor_index=anchor_idx,
        assumption_note=assumption,
        misread_risk=risk,
        candidates=packed,
    )


def resolve_research_topic(current_text: object, prior_user_turns: Sequence[object] | Iterable[object] = ()) -> str:
    """Backward-compatible wrapper: chỉ trả topic đã resolve."""

    return resolve_research_anchor(current_text, prior_user_turns).topic


@dataclass(frozen=True, slots=True)
class TutorResearchDecision:
    should_delegate: bool
    score: float
    reason: str


@dataclass(frozen=True, slots=True)
class TutorDelegationDecision:
    should_delegate: bool
    score: float
    reason: str
    mode: str = "none"  # none | quick_search | research


RESEARCH_MODE_RETIRED = True


def score_tutor_delegation(text: object, *, intent: str = "", meaning_missing: bool = False) -> TutorDelegationDecision:
    """Gate chung cho Tutor web: quick_search hoặc research.

    - research: cue đào sâu/kiểm chứng phức tạp, có thể cần phân tích lại.
    - quick_search: user chủ động muốn search hoặc câu có freshness rõ.
    - none: kiến thức phổ thông để main Terra tự trả lời.
    """

    # Master switch phụ cho route chủ động. TutorConfig.enabled vẫn là cửa cuối,
    # nhưng gate ở đây giúp Eris không hứa search khi UI đã tắt.
    if not _runtime_bool("tutor", "enabled", default=True):
        return TutorDelegationDecision(False, 0.0, "tutor_disabled", "none")

    quick_enabled = _runtime_bool("tutor", "quick_search_enabled", default=True)

    research = score_research_need(text, intent=intent, meaning_missing=meaning_missing)
    if research.should_delegate:
        if RESEARCH_MODE_RETIRED:
            if quick_enabled:
                return TutorDelegationDecision(True, min(0.88, research.score), "research_retired_to_quick_search", "quick_search")
            return TutorDelegationDecision(False, research.score, "research_retired", "none")
        if _runtime_bool("tutor", "research_enabled", default=False):
            return TutorDelegationDecision(True, research.score, research.reason, "research")
        return TutorDelegationDecision(False, research.score, "research_disabled", "none")

    normalized = normalize_marker_text(text)
    if not normalized:
        return TutorDelegationDecision(False, 0.0, "empty", "none")

    if is_explicit_search_request(text):
        return TutorDelegationDecision(bool(quick_enabled), 0.88, "explicit_search_cue" if quick_enabled else "quick_search_disabled", "quick_search" if quick_enabled else "none")

    # Fresh/current queries are dangerous for a frozen/common-knowledge answer.
    # Route only when the turn is actually world knowledge or the classifier is unsure.
    allowed_uncertain_intents = {"world_knowledge", "unclear", "unknown", "other", ""}
    intent_norm = str(intent or "").strip().lower()
    if needs_fresh_web(text) and intent_norm in allowed_uncertain_intents:
        return TutorDelegationDecision(bool(quick_enabled), 0.78, "freshness_web_needed" if quick_enabled else "quick_search_disabled", "quick_search" if quick_enabled else "none")

    # Hỏi lái: không có chữ search, nhưng đang kiểm claim/status/policy trong miền
    # dễ thay đổi. Dùng quick_search, không kéo research relay dài dòng.
    if needs_external_verification(text) and intent_norm in allowed_uncertain_intents:
        return TutorDelegationDecision(bool(quick_enabled), 0.74, "implicit_verification_risk" if quick_enabled else "quick_search_disabled", "quick_search" if quick_enabled else "none")

    return TutorDelegationDecision(False, research.score, research.reason, "none")


def score_research_need(text: object, *, intent: str = "", meaning_missing: bool = False) -> TutorResearchDecision:
    """Gate rẻ cho Tutor.

    Câu đơn giản kiểu "X là gì" không còn tự động đẩy sang Tutor chỉ vì classifier
    gọi nó là world_knowledge. Con người đã phát minh ra classifier rồi lại bắt nó
    gọi thêm một model cho mọi câu hỏi hai dòng, một chuỗi thức ăn rất văn minh.
    """

    normalized = normalize_marker_text(text)
    if not normalized:
        return TutorResearchDecision(False, 0.0, "empty")

    if is_explicit_research_request(text):
        return TutorResearchDecision(True, 1.0, "explicit_research_cue")

    if str(intent or "").strip().lower() != "world_knowledge":
        return TutorResearchDecision(False, 0.0, "not_world_knowledge")

    score = 0.20
    reasons: list[str] = ["world_knowledge"]

    marker_hits = sum(1 for marker in _COMPLEXITY_MARKERS if _has_marker(normalized, marker))
    if marker_hits:
        score += min(0.48, 0.18 * marker_hits)
        reasons.append(f"complexity_markers={marker_hits}")

    word_count = len(normalized.split())
    if word_count >= 22:
        score += 0.16
        reasons.append("long_query")
    elif word_count >= 14:
        score += 0.08
        reasons.append("medium_query")

    clause_count = sum(normalized.count(token) for token in (" va ", " nhung ", " hay ", " dong thoi "))
    if clause_count >= 2:
        score += 0.10
        reasons.append("multi_clause")

    if meaning_missing:
        score += 0.08
        reasons.append("semantic_uncertainty")

    score = max(0.0, min(1.0, score))
    return TutorResearchDecision(score >= 0.58, score, ",".join(reasons))




def anchor_resolution_needs_clarification(
    resolution: FollowupAnchorResolution | object,
    *,
    confidence_floor: float = 0.55,
    allow_standalone: bool = True,
) -> bool:
    """True nếu follow-up mơ hồ quá, nên hỏi lại thay vì search nhầm.

    Luật này bảo vệ đúng case user nói “cái đó/ý trên” nhưng ledger không có anchor
    đủ chắc. Search nhầm còn tệ hơn không search, vì nó sinh ra một đống nguồn đúng
    cho câu hỏi sai. Một thành tựu bi thảm, nhưng rất phổ biến.
    """

    try:
        conf = float(getattr(resolution, "confidence", 0.0) or 0.0)
        risk = str(getattr(resolution, "misread_risk", "") or "").strip().lower()
        note = str(getattr(resolution, "assumption_note", "") or "").strip().lower()
        anchor_text = str(getattr(resolution, "anchor_text", "") or "").strip()
        topic = str(getattr(resolution, "topic", "") or "").strip()
    except Exception:
        return True
    if not topic:
        return True
    if allow_standalone and note == "query_standalone":
        return False
    if risk == "high":
        return True
    if conf < float(confidence_floor):
        return True
    # Reference follow-up mà không tìm được anchor thật thì nên hỏi lại, không cosplay
    # trí nhớ hội thoại.
    if note in {"no_prior_anchor_found", "empty"} and not anchor_text:
        return True
    return False


def build_anchor_clarification_text(resolution: FollowupAnchorResolution | object = None) -> str:
    """Một câu hỏi lại ngắn cho low-confidence anchor."""

    try:
        candidates = list(getattr(resolution, "candidates", ()) or ()) if resolution is not None else []
    except Exception:
        candidates = []
    if candidates:
        preview = str(getattr(candidates[0], "text", "") or "").strip()
        if len(preview) > 90:
            preview = preview[:87].rstrip() + "..."
        if preview:
            return f"Tôi chưa chắc bạn muốn tôi tra đúng phần “{preview}” hay phần khác. Chốt giúp tôi neo trước đã."
    return "Tôi chưa chắc bạn muốn tôi tra phần nào. Chỉ rõ neo một câu đã, rồi tôi lục Tutor cho khỏi search nhầm."


def build_lightweight_search_prompt(
    topic: object,
    *,
    context: object = "",
    max_words: int = 160,
    max_chars: int = 395,
) -> str:
    """Prompt search nhanh cho Tutor web, rẻ hơn research-followup."""

    topic_text = " ".join(scrub_secrets(topic).split())
    context_text = " ".join(scrub_secrets(context).split())
    max_words = max(60, min(260, int(max_words or 160)))
    max_chars = max(220, min(395, int(max_chars or 395)))
    prefix = (
        "Hãy search web nhanh để kiểm tra câu hỏi sau. "
        f"Trả lời tiếng Việt tối đa {max_words} từ; nêu kết luận chính, "
        "ghi nguồn/link nếu tìm thấy, và nói rõ nếu chưa đủ chắc. Câu hỏi: "
    )
    suffix = f" | Ngữ cảnh: {context_text}" if context_text else ""
    available = max_chars - len(prefix) - len(suffix)
    if available < 40 and suffix:
        suffix = ""
        available = max_chars - len(prefix)
    topic_text = topic_text[: max(1, available)].rstrip(" .|:")
    prompt = f"{prefix}{topic_text}{suffix}".strip()
    return prompt[:max_chars].rstrip()


def build_lightweight_research_prompt(
    topic: object,
    *,
    context: object = "",
    max_words: int = 280,
    max_chars: int = 395,
) -> str:
    """Bọc chủ đề bằng lời nhắc nghiên cứu rẻ, vừa contract TutorQuestion 400 ký tự."""

    topic_text = " ".join(scrub_secrets(topic).split())
    context_text = " ".join(scrub_secrets(context).split())
    max_words = max(80, min(600, int(max_words or 280)))
    max_chars = max(220, min(395, int(max_chars or 395)))

    prefix = (
        "Hãy dùng tìm kiếm web nếu cần để kiểm tra câu hỏi sau. "
        f"Trả lời tiếng Việt cô đọng, tối đa {max_words} từ; ưu tiên nguồn chính thức, "
        "nêu rõ điều chưa chắc và ghi 2-4 nguồn ở cuối. Câu hỏi: "
    )
    suffix = f" | Ngữ cảnh: {context_text}" if context_text else ""
    available = max_chars - len(prefix) - len(suffix)
    if available < 40 and suffix:
        suffix = ""
        available = max_chars - len(prefix)
    topic_text = topic_text[: max(1, available)].rstrip(" .|:")
    prompt = f"{prefix}{topic_text}{suffix}".strip()
    return prompt[:max_chars].rstrip()


def compact_research_answer(text: object, *, max_words: int = 320) -> str:
    """Hard cap không dùng model, cố giữ phần nguồn nếu Tutor trả lời quá dài."""

    value = scrub_secrets(text).strip()
    if not value:
        return ""
    words = value.split()
    max_words = max(100, min(800, int(max_words or 320)))
    if len(words) <= max_words:
        return value

    lines = value.splitlines()
    source_index = -1
    for idx, line in enumerate(lines):
        normalized = normalize_marker_text(line)
        if any(normalized.startswith(head) for head in _SOURCE_HEADINGS):
            source_index = idx
            break

    if source_index >= 0:
        source_text = "\n".join(lines[source_index:]).strip()
        source_words = source_text.split()[: min(80, max_words // 3)]
        head_budget = max_words - len(source_words) - 8
        head = " ".join(words[: max(40, head_budget)]).rstrip()
        tail = " ".join(source_words).rstrip()
        return f"{head}\n\n[Đã rút gọn để giữ ngân sách.]\n{tail}".strip()

    return " ".join(words[:max_words]).rstrip() + " … [đã rút gọn]"


__all__ = [
    "FollowupAnchorCandidate",
    "FollowupAnchorResolution",
    "TutorDelegationDecision",
    "TutorResearchDecision",
    "anchor_resolution_needs_clarification",
    "build_anchor_clarification_text",
    "build_lightweight_research_prompt",
    "build_lightweight_search_prompt",
    "compact_research_answer",
    "is_explicit_research_request",
    "is_explicit_search_request",
    "needs_external_verification",
    "needs_fresh_web",
    "RESEARCH_MODE_RETIRED",
    "normalize_marker_text",
    "resolve_research_anchor",
    "resolve_research_topic",
    "score_research_need",
    "score_tutor_delegation",
]
