from __future__ import annotations

from dataclasses import dataclass
import re
import unicodedata


@dataclass(frozen=True)
class ConversationLane:
    """Quyết định lượt chat đi đường nhanh hay đường phân tích đầy đủ.

    Đường nhanh vẫn giữ validator xác định và FinalSpeechAuthority. Nó chỉ bỏ
    những lần gọi model phụ/candidate không cần thiết ở lời chào, lời đáp xã hội
    ngắn và repair đã có contract rõ. Ít hội đồng hơn thường giúp câu nói còn
    nguyên giọng hơn, một phát hiện khá đau lòng nhưng hữu ích.
    """

    name: str
    fast: bool
    reason: str

    def to_dict(self) -> dict[str, object]:
        return {"name": self.name, "fast": self.fast, "reason": self.reason}


def _norm(text: str) -> str:
    value = unicodedata.normalize("NFD", str(text or ""))
    value = "".join(ch for ch in value if unicodedata.category(ch) != "Mn")
    value = value.lower().replace("đ", "d")
    return re.sub(r"\s+", " ", value).strip()


def classify_conversation_lane(user_text: str, contract=None) -> ConversationLane:
    """Chọn fast lane bảo thủ, không dùng cho task/tool/tri thức bên ngoài."""

    normalized = _norm(user_text)
    move = str(getattr(contract, "response_move", "") or "").strip().lower()
    evidence_domain = str(getattr(contract, "evidence_domain", "") or "").strip().lower()

    heavy_markers = (
        "search", "tim tren web", "tra cuu", "nghien cuu", "repo", "file", "code",
        "sua loi", "chay test", "terminal", "cmd", "powershell", "github", "http://", "https://",
        "man hinh", "anh nay", "log", "tai lieu", "pdf",
    )
    if any(marker in normalized for marker in heavy_markers):
        return ConversationLane("full", False, "external_or_tool_task")

    if evidence_domain in {"world_current", "world_stable", "project", "screen", "runtime"}:
        return ConversationLane("full", False, f"evidence_domain:{evidence_domain}")

    fast_moves = {
        "natural_reply",
        "restrained_warmth",
        "relational_reflection",
        "relational_repair",
        "specific_repair",
        "clarify_repair",
        "warm_tease",
    }
    short_social = len(normalized) <= 220 and move in fast_moves
    greeting = len(normalized) <= 100 and any(
        token in normalized
        for token in ("hello", "helloooo", "xin chao", "chao eris", "eris oi", "em oi")
    )
    if short_social or greeting:
        reason = f"social_move:{move}" if short_social else "simple_greeting"
        return ConversationLane("human_fast", True, reason)

    return ConversationLane("full", False, "default_full_reasoning")
