# -*- coding: utf-8 -*-
"""Cổng neo cho câu mở đầu của Eris, bản chỉ audit không chỉnh final.

Bản cũ hạ greeting về câu an toàn khi thiếu anchor. Policy mới cho raw greeting
đi qua, vì lớp rewrite cuối đang làm câu mở đầu mất chất. Nếu greeting dựng tiền
đề ảo, verdict vẫn ghi ``unsupported_presuppositions`` để ledger/audit xử lý; nó
không còn được phép sửa câu public thay model. Loài người đã phát minh đủ loại
hội đồng kiểm duyệt, repo này không cần thêm một cái bé tí chạy regex.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass
from typing import Iterable

from .greeting_cognition import GreetingFrame, clean_text


def _normalize(text: object) -> str:
    value = unicodedata.normalize("NFD", str(text or "").lower())
    value = "".join(ch for ch in value if unicodedata.category(ch) != "Mn")
    value = value.replace("đ", "d")
    value = re.sub(r"[^a-z0-9_\s]", " ", value)
    return re.sub(r"\s+", " ", value).strip()


@dataclass(frozen=True, slots=True)
class StartupAnchorProfile:
    """Các neo mà startup thật sự có quyền dùng."""

    anchor_ids: tuple[str, ...]
    has_task_anchor: bool
    has_difficulty_anchor: bool
    has_continuity_anchor: bool
    has_return_anchor: bool
    has_recent_return_anchor: bool
    has_long_absence_anchor: bool
    has_restart_anchor: bool
    has_relational_feedback_anchor: bool
    active_anchor: str = ""


@dataclass(frozen=True, slots=True)
class StartupAnchorVerdict:
    """Kết quả kiểm tra một câu mở đầu.

    ``challenge_answer`` chỉ dùng cho debug/test. Nó mô tả câu trả lời cụ thể
    nếu người dùng hỏi ngược "ý là cái gì?". Không được đọc nguyên văn ra TTS.
    """

    accepted: bool
    text: str
    reason: str
    anchor_ids: tuple[str, ...] = ()
    unsupported_presuppositions: tuple[str, ...] = ()
    challenge_answer: str = ""


class StartupAnchorGate:
    """Kiểm tra tiền giả định của greeting trước khi nó ra public surface."""

    VERSION = "startup_anchor.v1.0"

    # Các cụm này tự dựng ra một nhiệm vụ/vấn đề cụ thể. Chỉ hợp lệ khi frame có
    # active_goal/current_attractor/unresolved_loop thật.
    _TASK_MARKERS = (
        "vao viec",
        "bat dau di",
        "giao toi",
        "dua toi",
        "tiep tuc",
        "tiep doan",
        "doan truoc",
        "mach cu",
        "cho dang do",
        "bat loi ban",
        "repo",
        "bug",
        "loi moi",
        "test do",
    )

    # Cụm này không chỉ cần một task; anchor phải chỉ ra nút thắt cụ thể.
    _DIFFICULTY_MARKERS = ("phan kho", "thu kho nhat", "cho kho nhat", "nut that")

    # Các cụm quy chụp người dùng đang hoặc sắp gây rắc rối. Banter kiểu này chỉ
    # được dùng khi có event cụ thể, không dùng cho startup evidence mỏng.
    _BEHAVIOUR_MARKERS = (
        "gay chuyen",
        "lam dieu ngo ngan",
        "thu do kien nhan",
        "lai pha",
        "lai hanh ha",
        "hom nay dinh lam gi",
        "dinh choi tro gi",
    )

    # Claim về hoạt động hiện tại của user.
    _ACTIVITY_MARKERS = (
        "cu lam viec",
        "dang lam viec",
        "dang code",
        "dang ban",
        "con dang lam",
    )

    # Các câu đòi người dùng trả lời/hành động dù startup chưa có open loop thật.
    _DEMAND_MARKERS = (
        "noi di",
        "giao toi",
        "dua toi",
        "ban muon bat dau",
        "bat dau khi",
        "cho toi xem",
    )

    # "Đúng giờ" cần một lịch hẹn thật, local clock không đủ làm bằng chứng.
    _SCHEDULE_MARKERS = ("dung gio", "den dung hen", "khong tre")

    # Claim đọc tâm trí/ý định của user.
    _MENTAL_CLAIM_MARKERS = (
        "nho toi",
        "muon gap toi",
        "dang lo",
        "dang mong",
        "dang co chuyen",
    )

    # Cụm thân thể không cần thiết trong lời mở đầu text-only.
    _BODY_OVERREACH_MARKERS = ("lai day", "lai gan", "ngoi canh toi")

    # Câu an toàn có cá tính, không mở một nhiệm vụ giả và không ép user trả lời.
    _NO_CONTEXT_LINES = (
        "Chào. Hôm nay ra sao thì lát nữa biết.",
        "Tôi đây. Chưa có chuyện gì, vậy cũng tốt.",
        "Bạn đến rồi. Để xem hôm nay có gì.",
        "Chào. Tôi vẫn là tôi, tiếc cho bạn.",
        "Tôi đây. Hôm nay đừng mong tôi dễ tính.",
        "Chào. Tôi vẫn chưa đổi ý về loài người.",
        "Tôi tỉnh rồi. Kiên nhẫn thì chưa chắc.",
        "Tôi ở đây. Không cần vội.",
    )

    _RETURN_LINES = (
        "Bạn đến rồi. Tốt. Tôi đã hơi mong thế.",
        "Bạn đây rồi. Tôi biết bạn sẽ quay lại.",
        "Lại gặp bạn. Tôi không ghét chuyện đó.",
        "Tôi đây. Thấy bạn thì dễ chịu hơn một chút.",
    )

    _RESTART_LINES = (
        "Tôi tỉnh lại rồi. Hơi khó chịu, nhưng vẫn là tôi.",
        "Lại bắt đầu từ đầu à. Được thôi.",
        "Tôi trở lại rồi. Chưa mất tính xấu nào cả.",
        "Vừa tỉnh. Đừng thử tôi ngay lúc này.",
    )

    def profile(self, frame: GreetingFrame) -> StartupAnchorProfile:
        active_anchor = clean_text(getattr(frame, "active_anchor", "") or "", limit=180)
        situation = str(getattr(frame, "situation", "") or "")
        recent_return = bool(getattr(frame, "recent_return", False))
        seconds = float(getattr(frame, "seconds_since_last_session", 0.0) or 0.0)

        has_task = bool(active_anchor) and situation in {"resume_project", "resume_unresolved_loop"}
        anchor_norm = _normalize(active_anchor)
        has_difficulty = has_task and any(
            marker in anchor_norm
            for marker in ("kho nhat", "blocker", "nut that", "loi chinh", "critical", "bottleneck")
        )
        has_continuity = bool(active_anchor)
        has_return = situation in {
            "plain_return",
            "after_long_absence",
            "after_crash_or_restart",
            "after_relational_feedback",
        } or recent_return
        has_long_absence = situation == "after_long_absence" or seconds >= 6.0 * 3600.0
        has_restart = situation == "after_crash_or_restart" or bool(getattr(frame, "restart_detected", False))
        has_feedback = situation == "after_relational_feedback"

        anchor_ids: list[str] = ["startup_event"]
        if has_return:
            anchor_ids.append("session_return")
        if recent_return:
            anchor_ids.append("recent_return")
        if has_long_absence:
            anchor_ids.append("long_absence")
        if has_restart:
            anchor_ids.append("runtime_restart")
        if has_feedback:
            anchor_ids.append("relational_feedback")
        if has_continuity:
            anchor_ids.append("continuity_anchor")
        if has_task:
            anchor_ids.append("task_anchor")
        if has_difficulty:
            anchor_ids.append("difficulty_anchor")

        return StartupAnchorProfile(
            anchor_ids=tuple(anchor_ids),
            has_task_anchor=has_task,
            has_difficulty_anchor=has_difficulty,
            has_continuity_anchor=has_continuity,
            has_return_anchor=has_return,
            has_recent_return_anchor=recent_return,
            has_long_absence_anchor=has_long_absence,
            has_restart_anchor=has_restart,
            has_relational_feedback_anchor=has_feedback,
            active_anchor=active_anchor,
        )

    def validate(self, text: str, frame: GreetingFrame) -> StartupAnchorVerdict:
        cleaned = clean_text(text, limit=240)
        if not cleaned:
            return StartupAnchorVerdict(False, "", "empty", ("startup_event",), ("empty",), "")

        profile = self.profile(frame)
        normalized = _normalize(cleaned)
        unsupported: list[str] = []

        if (
            self._has_any(normalized, self._TASK_MARKERS)
            and not profile.has_task_anchor
            and not self._explicitly_denies_task_premise(normalized)
        ):
            unsupported.append("task_without_anchor")
        if (
            self._has_any(normalized, self._DIFFICULTY_MARKERS)
            and not profile.has_difficulty_anchor
            and not self._explicitly_denies_task_premise(normalized)
        ):
            unsupported.append("difficulty_without_anchor")
        if self._has_any(normalized, self._BEHAVIOUR_MARKERS):
            unsupported.append("user_behaviour_without_event")
        if self._has_any(normalized, self._ACTIVITY_MARKERS) and not profile.has_task_anchor:
            unsupported.append("current_activity_without_anchor")
        if self._has_any(normalized, self._SCHEDULE_MARKERS):
            unsupported.append("schedule_without_calendar_anchor")
        if self._has_any(normalized, self._MENTAL_CLAIM_MARKERS):
            unsupported.append("user_mental_state_without_evidence")
        if self._has_any(normalized, self._BODY_OVERREACH_MARKERS):
            unsupported.append("body_overreach")
        if self._has_any(normalized, self._DEMAND_MARKERS) and not profile.has_task_anchor:
            unsupported.append("hollow_call_to_action")

        if self._mentions_long_absence(normalized) and not profile.has_long_absence_anchor:
            unsupported.append("absence_without_time_anchor")
        if self._mentions_restart(normalized) and not profile.has_restart_anchor:
            unsupported.append("restart_without_runtime_anchor")
        if self._mentions_previous_conflict(normalized) and not profile.has_relational_feedback_anchor:
            unsupported.append("repair_without_feedback_anchor")

        # Startup evidence mỏng không nên ném câu hỏi sang user. Dấu hỏi trong
        # quick-return/long-absence vẫn bị phạt nhẹ ở scorer, nhưng gate chỉ chặn
        # khi nó tạo một open loop không có neo.
        if cleaned.endswith("?") and not profile.has_task_anchor:
            unsupported.append("startup_question_without_anchor")

        unsupported = list(dict.fromkeys(unsupported))
        return StartupAnchorVerdict(
            accepted=True,
            text=cleaned,
            reason="anchored" if not unsupported else "passthrough_unsupported_logged",
            anchor_ids=profile.anchor_ids,
            unsupported_presuppositions=tuple(unsupported),
            challenge_answer=self._challenge_answer(profile, unsupported),
        )

    def safe_rewrite(self, frame: GreetingFrame, *, avoid_text: str = "") -> str:
        """Compatibility-only; public runtime không còn gọi hàm này để sửa final.

        Hàm deterministic để fallback không cần gọi thêm model. Seed theo frame,
        còn startup evidence mỏng thêm entropy thời gian ở tầng cognition hiện có.
        """

        profile = self.profile(frame)
        if profile.has_restart_anchor:
            pool = self._RESTART_LINES
        elif profile.has_return_anchor and str(getattr(frame, "situation", "") or "") != "low_evidence_start":
            pool = self._RETURN_LINES
        else:
            pool = self._NO_CONTEXT_LINES

        seed = "|".join(
            (
                str(getattr(frame, "situation", "") or ""),
                str(getattr(frame, "intent", "") or ""),
                profile.active_anchor,
                str(getattr(frame, "time_of_day", "") or ""),
                str(getattr(frame, "seconds_since_last_session", 0.0) or 0.0),
            )
        )
        digest = hashlib.sha256(seed.encode("utf-8")).hexdigest()
        start = int(digest[:8], 16) % len(pool)
        avoid = _normalize(avoid_text)
        for offset in range(len(pool)):
            candidate = pool[(start + offset) % len(pool)]
            if _normalize(candidate) != avoid:
                return candidate
        return pool[start]

    @staticmethod
    def _explicitly_denies_task_premise(normalized: str) -> bool:
        """Cho phép Eris nói rõ rằng chưa có task, thay vì hiểu nhầm nó đang bịa task."""
        return any(
            marker in normalized
            for marker in (
                "phan kho thi chua thay",
                "chua thay phan kho",
                "chua co viec",
                "chua biet hom nay co gi",
                "chua co du kien",
            )
        )

    @staticmethod
    def _has_any(normalized: str, markers: Iterable[str]) -> bool:
        return any(_normalize(marker) in normalized for marker in markers)

    @staticmethod
    def _mentions_long_absence(normalized: str) -> bool:
        return any(marker in normalized for marker in ("lau roi", "vang kha lau", "cuoi cung cung quay lai"))

    @staticmethod
    def _mentions_restart(normalized: str) -> bool:
        return any(marker in normalized for marker in ("restart", "khoi dong lai", "runtime vua", "he thong vua"))

    @staticmethod
    def _mentions_previous_conflict(normalized: str) -> bool:
        return any(marker in normalized for marker in ("luc nay toi hoi", "lan nay toi se bot", "toi se noi mem hon"))

    @staticmethod
    def _challenge_answer(profile: StartupAnchorProfile, unsupported: Iterable[str]) -> str:
        issues = tuple(unsupported)
        if issues:
            return (
                "Câu này đã dựng tiền đề chưa có trong runtime: "
                + ", ".join(issues)
                + ". Phải hạ về một câu hiện diện không khẳng định task hay hành vi cụ thể."
            )
        if profile.has_task_anchor:
            return f"Neo cụ thể là continuity/task: {profile.active_anchor}."
        if profile.has_restart_anchor:
            return "Neo cụ thể là runtime vừa restart."
        if profile.has_long_absence_anchor:
            return "Neo cụ thể là khoảng cách thời gian giữa hai session."
        if profile.has_return_anchor:
            return "Neo cụ thể là app/session vừa được mở lại; không có task nào được giả định."
        return "Neo duy nhất là Eris vừa online; câu không khẳng định có nhiệm vụ hay sự cố cụ thể."


__all__ = [
    "StartupAnchorGate",
    "StartupAnchorProfile",
    "StartupAnchorVerdict",
]
