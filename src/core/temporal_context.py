# -*- coding: utf-8 -*-
"""Ngữ cảnh thời gian chuẩn dùng chung cho toàn bộ runtime Eris.

Khác với ``time.time()`` rải rác để tính timeout/cooldown, ``TemporalContext``
trả lời các câu mà cognition thật sự cần biết:
- hiện tại là ngày nào, thứ mấy, mấy giờ theo múi giờ của người dùng;
- đang là sáng, chiều, tối hay khuya;
- phiên hiện tại đã chạy bao lâu;
- người dùng/ERIS vừa nói cách đây bao lâu;
- lần mở phiên trước là vừa nãy, hôm qua hay nhiều ngày trước.

Mặc định dùng ``Asia/Ho_Chi_Minh``. Có thể đổi bằng biến môi trường
``ERIS_TIMEZONE``. Module chỉ cung cấp evidence; nó không tự ép Eris nhắc giờ
trong mọi câu như một chiếc đồng hồ báo thức mắc chứng thích diễn thuyết.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone, tzinfo
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


DEFAULT_TIMEZONE = "Asia/Ho_Chi_Minh"

_WEEKDAY_VI = (
    "Thứ Hai",
    "Thứ Ba",
    "Thứ Tư",
    "Thứ Năm",
    "Thứ Sáu",
    "Thứ Bảy",
    "Chủ Nhật",
)


def _safe_epoch(value: Any) -> float:
    try:
        return max(0.0, float(value or 0.0))
    except (TypeError, ValueError):
        return 0.0


def resolve_timezone(name: str | None = None) -> tuple[str, tzinfo]:
    """Trả về tên múi giờ hợp lệ và đối tượng ``ZoneInfo`` tương ứng.

    Nếu config sai, fallback về Việt Nam thay vì làm sập hot path chỉ vì ai đó
    gõ ``Asia/HoChiMinh`` thiếu dấu gạch dưới. Một lỗi cấu hình không đáng được
    quyền kết liễu cả nhân cách VTuber.
    """

    candidate = str(name or os.environ.get("ERIS_TIMEZONE", DEFAULT_TIMEZONE) or DEFAULT_TIMEZONE).strip()
    try:
        return candidate, ZoneInfo(candidate)
    except (ZoneInfoNotFoundError, ValueError):
        # Windows không luôn mang sẵn IANA timezone database. Nếu package
        # ``tzdata`` chưa được cài, fallback UTC+7 vẫn giữ đúng giờ Việt Nam
        # thay vì để toàn runtime chết ngay khi import đồng hồ.
        try:
            return DEFAULT_TIMEZONE, ZoneInfo(DEFAULT_TIMEZONE)
        except (ZoneInfoNotFoundError, ValueError):
            return DEFAULT_TIMEZONE, timezone(timedelta(hours=7), name="UTC+07:00")


def classify_time_of_day(hour: int) -> tuple[str, str]:
    """Phân loại giờ địa phương thành nhịp ngày ổn định cho cognition."""

    hour = int(hour) % 24
    if 4 <= hour < 6:
        return "dawn", "rạng sáng"
    if 6 <= hour < 12:
        return "morning", "buổi sáng"
    if 12 <= hour < 14:
        return "noon", "buổi trưa"
    if 14 <= hour < 18:
        return "afternoon", "buổi chiều"
    if 18 <= hour < 22:
        return "evening", "buổi tối"
    return "late_night", "đêm khuya"


def _calendar_relation(now_local: datetime, previous_epoch: float, timezone: tzinfo) -> tuple[str, str]:
    if previous_epoch <= 0.0:
        return "first_or_unknown_session", "chưa có mốc phiên trước đáng tin"

    previous_local = datetime.fromtimestamp(previous_epoch, timezone)
    seconds = max(0.0, now_local.timestamp() - previous_epoch)
    day_delta = (now_local.date() - previous_local.date()).days

    if seconds <= 180.0:
        return "quick_return", "vừa quay lại sau ít phút"
    if day_delta == 0:
        return "same_day_return", "quay lại trong cùng ngày"
    if day_delta == 1:
        return "yesterday_return", "lần trước là hôm qua"
    if day_delta > 1:
        return f"days_ago:{day_delta}", f"lần trước cách đây {day_delta} ngày"
    # Đồng hồ hệ thống bị chỉnh lùi hoặc dữ liệu cũ mang timezone khác.
    return "clock_shift_or_future_marker", "mốc phiên trước lệch với đồng hồ hiện tại"


@dataclass(frozen=True, slots=True)
class TemporalContext:
    """Snapshot thời gian bất biến cho đúng một chu kỳ cognition."""

    now_epoch: float
    now_iso: str
    timezone: str
    local_date: str
    local_time: str
    weekday: str
    weekday_vi: str
    hour: int
    minute: int
    time_of_day: str
    time_of_day_vi: str
    is_weekend: bool
    session_started_at: str
    session_elapsed_seconds: float
    since_last_user_turn_seconds: float | None
    since_last_assistant_turn_seconds: float | None
    since_last_session_seconds: float | None
    last_seen_at: str
    calendar_relation: str
    calendar_relation_vi: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_prompt_block(self) -> str:
        """Render block ngắn cho Qwen, kèm luật chống lạm dụng thời gian."""

        payload: dict[str, Any] = {
            "now": self.now_iso,
            "timezone": self.timezone,
            "weekday": self.weekday_vi,
            "time_of_day": self.time_of_day,
            "calendar_relation": self.calendar_relation,
            "session_elapsed_s": round(self.session_elapsed_seconds, 1),
        }
        if self.since_last_user_turn_seconds is not None:
            payload["since_last_user_turn_s"] = round(self.since_last_user_turn_seconds, 1)
        if self.since_last_assistant_turn_seconds is not None:
            payload["since_last_assistant_turn_s"] = round(self.since_last_assistant_turn_seconds, 1)
        if self.since_last_session_seconds is not None:
            payload["since_last_session_s"] = round(self.since_last_session_seconds, 1)

        compact = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        return "\n".join(
            (
                f"[TEMPORAL_CONTEXT: {compact}]",
                "[TEMPORAL_RULE: dùng mốc này để hiểu 'hôm nay/mai/tối nay/lâu rồi'; "
                "không tự nhắc ngày giờ nếu không liên quan và không đoán hoạt động của người dùng chỉ từ giờ.]",
            )
        )


class TemporalContextBuilder:
    """Dựng ``TemporalContext`` từ đồng hồ và các mốc runtime đã biết."""

    def __init__(self, *, timezone_name: str | None = None, session_started_at: float | None = None) -> None:
        self.timezone_name, self.timezone = resolve_timezone(timezone_name)
        self.session_started_at = _safe_epoch(session_started_at) or time.time()

    def build(
        self,
        *,
        now: float | None = None,
        last_user_turn_at: float | None = None,
        last_assistant_turn_at: float | None = None,
        last_session_at: float | None = None,
        session_started_at: float | None = None,
    ) -> TemporalContext:
        current = _safe_epoch(now) or time.time()
        started = _safe_epoch(session_started_at) or self.session_started_at
        if started > current:
            started = current

        local = datetime.fromtimestamp(current, self.timezone)
        period, period_vi = classify_time_of_day(local.hour)
        previous_session = _safe_epoch(last_session_at)
        relation, relation_vi = _calendar_relation(local, previous_session, self.timezone)

        def age(marker: float | None) -> float | None:
            value = _safe_epoch(marker)
            if value <= 0.0:
                return None
            return max(0.0, current - value)

        previous_local = (
            datetime.fromtimestamp(previous_session, self.timezone).isoformat(timespec="seconds")
            if previous_session > 0.0
            else ""
        )
        started_local = datetime.fromtimestamp(started, self.timezone).isoformat(timespec="seconds")

        return TemporalContext(
            now_epoch=current,
            now_iso=local.isoformat(timespec="seconds"),
            timezone=self.timezone_name,
            local_date=local.date().isoformat(),
            local_time=local.strftime("%H:%M:%S"),
            weekday=local.strftime("%A"),
            weekday_vi=_WEEKDAY_VI[local.weekday()],
            hour=local.hour,
            minute=local.minute,
            time_of_day=period,
            time_of_day_vi=period_vi,
            is_weekend=local.weekday() >= 5,
            session_started_at=started_local,
            session_elapsed_seconds=max(0.0, current - started),
            since_last_user_turn_seconds=age(last_user_turn_at),
            since_last_assistant_turn_seconds=age(last_assistant_turn_at),
            since_last_session_seconds=age(last_session_at),
            last_seen_at=previous_local,
            calendar_relation=relation,
            calendar_relation_vi=relation_vi,
        )


def build_temporal_context_from_runtime(runtime_owner: Any, *, now: float | None = None) -> TemporalContext:
    """Dựng context từ runtime mà không buộc caller biết cấu trúc coordinator."""

    current = _safe_epoch(now) or time.time()
    if runtime_owner is None:
        return TemporalContextBuilder(session_started_at=current).build(now=current)

    started = _safe_epoch(getattr(runtime_owner, "_temporal_session_started_at", 0.0)) or current
    builder = getattr(runtime_owner, "_temporal_context_builder", None)
    configured_timezone, _ = resolve_timezone()
    if not isinstance(builder, TemporalContextBuilder) or builder.timezone_name != configured_timezone:
        # Settings có thể đổi múi giờ khi runtime đang sống. Không giữ builder cũ
        # rồi bắt user restart chỉ vì một string config, nghi lễ đó không cần thiết.
        builder = TemporalContextBuilder(
            timezone_name=configured_timezone,
            session_started_at=started,
        )
        try:
            runtime_owner._temporal_context_builder = builder
        except Exception:
            pass

    last_session_at = 0.0
    try:
        state_getter = getattr(runtime_owner, "_get_internal_state", None)
        state = state_getter() if callable(state_getter) else None
        last_session_at = _safe_epoch(getattr(state, "last_session_ritual_at", 0.0))
    except Exception:
        last_session_at = 0.0

    return builder.build(
        now=current,
        session_started_at=started,
        last_user_turn_at=getattr(runtime_owner, "_temporal_last_user_turn_at", 0.0),
        last_assistant_turn_at=getattr(runtime_owner, "_temporal_last_assistant_turn_at", 0.0),
        last_session_at=last_session_at,
    )


__all__ = [
    "DEFAULT_TIMEZONE",
    "TemporalContext",
    "TemporalContextBuilder",
    "build_temporal_context_from_runtime",
    "classify_time_of_day",
    "resolve_timezone",
]
