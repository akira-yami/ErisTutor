from __future__ import annotations

"""Router quyết định none -> text evidence -> vision escalation."""

import re
import time
import unicodedata

from .evidence_models import ScreenRouteDecision
from .ocr_config import screen_ocr_enabled
from .screen_anaphora_rescue import ScreenAnaphoraRescue
from .screen_evidence_store import ScreenEvidenceStore


def _norm(text: str) -> str:
    value = unicodedata.normalize("NFD", str(text or "").lower()).replace("đ", "d")
    value = "".join(ch for ch in value if unicodedata.category(ch) != "Mn")
    return re.sub(r"[^a-z0-9_#+.\-]+", " ", value).strip()


def _has_marker(normalized: str, marker: str) -> bool:
    marker = _norm(marker)
    if not marker:
        return False
    return f" {marker} " in f" {normalized} "


class ScreenRelevanceRouter:
    SCREEN_MARKERS = (
        "man hinh", "screen", "vision", "tren man", "cua so nay",
        "nhin man hinh", "xem man hinh", "nhin man hinh tui", "nhin man hinh toi",
        "em nhin man hinh", "em xem man hinh",
        "xem di", "ban xem di", "dang hien gi", "toi dang code gi", "doan dang mo",
        "file dang mo", "loi nay tren man hinh", "tab nay", "panel nay", "tab dang mo",
        "tab telemetry", "panel telemetry", "cua so telemetry", "doc tab telemetry",
        "doc duoc tab telemetry", "telemetry cua toi", "telemetry toi",
        # Music / Media screen references
        "bai dang nghe", "dang nghe bai", "bai toi dang nghe", "bai hat", "nhac",
        "bai gi", "nhac gi", "dang nghe nhac", "dang phat", "media", "spotify", "youtube",
        # PATCH: các câu đời thường sau một lượt vision cũng là tham chiếu màn hình.
        # Không có nhóm này, câu kiểu “em còn mù vision hả” bị route về mode=none.
        "mu vision", "con mu vision", "em con mu vision",
        "khong nhin duoc", "khong thay duoc", "khong thay gi",
    )
    TEXT_MARKERS = (
        # "chu" đã xóa: particle cuối câu "chứ" bị false-match → text_task=True sai
        "text", "dong", "log", "terminal", "console", "code", "traceback",
        "exception", "error", "loi", "thong bao", "ghi gi", "doc", "file", "ham",
        "class", "tab", "panel", "telemetry", "uia", "ocr", "metric", "status",
        "bai", "bai hat", "nhac", "tieu de", "artist", "ca si", "song", "track", "spotify", "youtube", "media",
    )
    SCREEN_CAPABILITY_MARKERS = (
        "doc duoc", "nhin duoc", "thay duoc", "xem duoc", "co doc", "co thay",
        "khong doc duoc", "khong nhin duoc", "khong thay duoc",
        # PATCH: “mù vision” là capability complaint, không phải small talk.
        "mu", "blind",
    )
    SCREEN_RESOURCE_MARKERS = (
        "telemetry", "tab", "panel", "uia", "ocr", "cua so",
        # PATCH: ghép capability + vision/screen để cứu câu hỏi về thị giác.
        "vision", "man hinh", "screen", "mat", "bai", "nhac", "media", "spotify", "youtube",
    )
    # Yêu cầu model TỰ NHÌN vào ảnh — bỏ qua OCR text matching, thẳng vào vision.
    # Khác VISUAL_MARKERS (hỏi về layout/màu sắc): đây là "phân tích cái đang hiện"
    # mà không biết trước nội dung là gì.
    DIRECT_VISION_MARKERS = (
        "tu xem", "tu nhin", "xem thu", "nhin thu", "xem giup", "nhin giup",
        "ban co the xem", "ban co the nhin", "em xem di", "em nhin di",
        "em nhin man hinh", "em xem man hinh",
        "nhin man hinh tui", "nhin man hinh toi",
        "tu phan tich", "phan tich man hinh", "xem man hinh giup",
        "look at", "take a look", "check the screen", "see for yourself",
    )
    VISUAL_MARKERS = ("mau", "bo cuc", "layout", "icon", "anh nay", "hinh anh", "giao dien", "ui", "dep", "xau", "vi tri", "can le", "kich thuoc", "bieu do", "chart")
    STRONG_MARKERS = ("chinh xac", "xac nhan", "evidence", "bang chung", "doc dung", "kiem tra ky", "verify")

    def __init__(self, store: ScreenEvidenceStore) -> None:
        self.store = store
        self.anaphora_rescue = ScreenAnaphoraRescue()

    def route(
        self,
        user_text: str,
        *,
        previous_assistant_text: str = "",
    ) -> ScreenRouteDecision:
        normalized = _norm(user_text)
        references = any(_has_marker(normalized, marker) for marker in self.SCREEN_MARKERS)
        # Câu hỏi capability kiểu "em đọc được tab telemetry tôi chưa" không nhất
        # thiết chứa chữ "màn hình". Ghép verb đọc/nhìn với resource UI để router
        # hiểu đây vừa là capability inquiry vừa là current-screen reference.
        if not references:
            references = (
                any(_has_marker(normalized, marker) for marker in self.SCREEN_CAPABILITY_MARKERS)
                and any(_has_marker(normalized, marker) for marker in self.SCREEN_RESOURCE_MARKERS)
            )
        resolution = self.anaphora_rescue.resolve(
            user_text,
            previous_assistant_text=previous_assistant_text,
            explicit_reference=references,
        )
        references = bool(references or resolution.references_screen)
        if not references:
            return ScreenRouteDecision(
                mode="none",
                reason="no_screen_reference",
                debug={"anaphora_reason": resolution.reason},
            )

        # Marker trực tiếp vẫn được tính từ câu user hiện tại. Câu Eris trước chỉ
        # cứu referent và query OCR, không được tự biến một yêu cầu text thành
        # yêu cầu bố cục/ảnh mạnh hơn.
        #
        # DIRECT_VISION: user bảo "tự xem đi" → yêu cầu xem màn hình tổng quát.
        # Không force needs_visual — OCR đọc được (lyrics, tiêu đề...) thì dùng OCR;
        # chỉ escalate vision khi OCR thực sự không có evidence nào.
        is_direct_vision = any(_has_marker(normalized, marker) for marker in self.DIRECT_VISION_MARKERS)
        needs_visual = any(_has_marker(normalized, marker) for marker in self.VISUAL_MARKERS)
        requires_strong = any(_has_marker(normalized, marker) for marker in self.STRONG_MARKERS)
        text_task = any(_has_marker(normalized, marker) for marker in self.TEXT_MARKERS)
        # Direct vision request cũng là generic — lấy toàn bộ fresh blocks, không filter theo overlap.
        generic = (is_direct_vision or references) and not text_task and not needs_visual
        evidence = self.store.query(
            resolution.query_text or user_text,
            generic_screen_request=bool(generic or resolution.inherited),
        )
        useful_evidence = tuple(
            block for block in evidence
            if not any(prefix in str(getattr(block, "text", "") or "").lower() for prefix in ("[chu nhan]:", "[user]:", "chu nhan:"))
        )
        confidence = max((block.confidence for block in useful_evidence), default=0.0)
        stale = bool(evidence and time.time() - max(block.captured_at for block in evidence) > 8.0)

        debug = {
            "anaphora_inherited": resolution.inherited,
            "anaphora_reason": resolution.reason,
            "query_used_previous_turn": bool(resolution.inherited and previous_assistant_text),
        }

        if needs_visual:
            return ScreenRouteDecision(
                "vision", True, True, requires_strong,
                "visual_layout_or_image_requested", evidence, confidence, stale, debug,
            )
        if evidence and confidence >= 0.72 and not stale:
            reason = "fresh_screen_text_available_after_anaphora" if resolution.inherited else "fresh_screen_text_available"
            return ScreenRouteDecision(
                "text_evidence", True, False, requires_strong, reason, evidence, confidence, False, debug,
            )
        # OCR tắt: vision thay OCR. Tham chiếu tường minh (hoặc "tự xem đi") chụp
        # ngay lượt này; đại từ kế thừa thuần vẫn xếp hàng vision cho lượt sau.
        vision_now = (not screen_ocr_enabled()) and (not resolution.inherited or is_direct_vision)
        if evidence and confidence >= 0.48:
            if requires_strong:
                return ScreenRouteDecision(
                    "vision", True, vision_now, True, "screen_text_not_strong_enough",
                    evidence, confidence, stale, debug,
                )
            reason = "screen_text_available_but_soft_after_anaphora" if resolution.inherited else "screen_text_available_but_soft"
            return ScreenRouteDecision(
                "text_evidence_soft", True, False, False, reason, evidence, confidence, stale, debug,
            )
        reason = (
            "anaphora_without_screen_text_escalate_vision"
            if resolution.inherited
            else "no_usable_screen_text_escalate_vision"
        )
        return ScreenRouteDecision(
            "vision", True, vision_now, requires_strong, reason, (), 0.0, False, debug,
        )
