from __future__ import annotations

"""Cứu tham chiếu màn hình bị lược trong nhịp chat ngắn.

Người dùng thường không lặp lại đủ danh từ sau khi Eris vừa hỏi về một video,
bài nhạc hay thứ đang mở. Họ chỉ nói ``tự xem đi``, ``đang nghe mà`` hoặc
``cái đó kìa``. Router từ khóa thuần sẽ đánh rơi quan hệ này vì câu hiện tại
không còn chữ ``màn hình``.

Module này chỉ nối đại từ/lời đáp ngắn với *lượt công khai ngay trước đó*. Nó
không tự suy ra nội dung màn hình và không biến mọi chữ ``nó`` thành lệnh OCR.
Evidence vẫn phải do ScreenEvidenceStore hoặc vision cung cấp.
"""

from dataclasses import dataclass
import re
import unicodedata


def _norm(text: str) -> str:
    value = unicodedata.normalize("NFD", str(text or "").lower()).replace("đ", "d")
    value = "".join(ch for ch in value if unicodedata.category(ch) != "Mn")
    return re.sub(r"[^a-z0-9_#+.\-]+", " ", value).strip()


def _has_phrase(normalized: str, phrase: str) -> bool:
    item = _norm(phrase)
    return bool(item and re.search(r"(?:^|\s)" + re.escape(item) + r"(?:\s|$)", normalized))


@dataclass(frozen=True, slots=True)
class ScreenAnaphoraResolution:
    references_screen: bool = False
    inherited: bool = False
    reason: str = "none"
    query_text: str = ""
    previous_assistant_text: str = ""


class ScreenAnaphoraRescue:
    """Bind một follow-up ngắn vào chủ thể màn hình/media của câu Eris trước."""

    # Đây là tín hiệu follow-up, không phải bằng chứng màn hình.
    FOLLOW_UP_MARKERS = (
        "tu xem di", "xem di", "nhin di", "doc di", "thi xem di",
        "cai nay", "cai do", "cai kia", "no do", "no day", "do ne", "day ne",
        "dang nghe ma", "dang xem ma", "dang phat ma", "tren do", "o tren do",
        "thay chua", "doc chua", "xem chua",
        # PATCH: câu kiểm tra năng lực vision ngay sau lượt “nhìn màn hình”
        # phải kế thừa anchor màn hình từ câu trước, không bị rơi về chat thường.
        "mu vision ha", "con mu vision ha", "em con mu vision ha",
        "khong nhin duoc ha", "khong thay duoc ha",
    )
    DEICTIC_MARKERS = (
        "no", "cai nay", "cai do", "cai kia", "do", "day", "kia", "tren do",
    )
    # Chủ thể trong câu Eris trước phải có khả năng được kiểm tra trên màn hình.
    PRIOR_SCREEN_ANCHORS = (
        "man hinh", "screen", "vision", "cua so", "tab", "panel", "trinh duyet", "browser",
        "youtube", "spotify", "video", "clip", "bai hat", "bai nhac", "nhac",
        "dang nghe", "dang xem", "dang phat", "game", "anh", "hinh", "code",
        "file dang mo", "loi dang hien",
        # PATCH: nếu assistant vừa nói/nhắc đang nhìn hoặc đọc, follow-up sau đó
        # được coi là còn nằm trong cuộc nói chuyện về màn hình.
        "toi thay", "toi nhin", "dang nhin", "doc duoc", "thay duoc",
    )

    def resolve(
        self,
        user_text: str,
        *,
        previous_assistant_text: str = "",
        explicit_reference: bool = False,
    ) -> ScreenAnaphoraResolution:
        current = " ".join(str(user_text or "").split()).strip()
        previous = " ".join(str(previous_assistant_text or "").split()).strip()
        if not current:
            return ScreenAnaphoraResolution(query_text="")

        if explicit_reference:
            return ScreenAnaphoraResolution(
                references_screen=True,
                inherited=False,
                reason="explicit_screen_reference",
                query_text=self._query_text(current, previous),
                previous_assistant_text=previous,
            )

        if not previous:
            return ScreenAnaphoraResolution(query_text=current)

        current_norm = _norm(current)
        previous_norm = _norm(previous)
        prior_has_screen_anchor = any(
            _has_phrase(previous_norm, marker) for marker in self.PRIOR_SCREEN_ANCHORS
        )
        if not prior_has_screen_anchor:
            return ScreenAnaphoraResolution(query_text=current, previous_assistant_text=previous)

        words = current_norm.split()
        short_follow_up = len(words) <= 14 and len(current) <= 120
        marked_follow_up = any(
            _has_phrase(current_norm, marker) for marker in self.FOLLOW_UP_MARKERS
        )
        deictic_follow_up = short_follow_up and any(
            _has_phrase(current_norm, marker) for marker in self.DEICTIC_MARKERS
        )
        # Một mệnh lệnh xem/đọc ngắn có thể không chứa đúng preset vì tiếng Việt
        # chat rất tự do: ``tự mà xem``, ``đọc thử coi``. Chỉ nhận khi câu trước
        # đã neo vào media/UI để tránh biến mọi chữ "xem" thành screenshot.
        imperative_follow_up = short_follow_up and bool(
            re.search(r"(?:^|\s)(?:xem|nhin|doc)(?:\s|$)", current_norm)
        )

        if not (marked_follow_up or deictic_follow_up or imperative_follow_up):
            return ScreenAnaphoraResolution(query_text=current, previous_assistant_text=previous)

        return ScreenAnaphoraResolution(
            references_screen=True,
            inherited=True,
            reason="screen_reference_inherited_from_previous_public_turn",
            query_text=self._query_text(current, previous),
            previous_assistant_text=previous,
        )

    @staticmethod
    def _query_text(current: str, previous: str) -> str:
        if not previous:
            return current
        # Store dùng overlap token. Ghép một đoạn ngắn của câu trước giúp
        # ``tự xem đi`` truy hồi được block có chữ video/YouTube/tên bài.
        return f"{current}\nNgữ cảnh câu Eris trước: {previous[:320]}"


__all__ = ["ScreenAnaphoraResolution", "ScreenAnaphoraRescue"]
