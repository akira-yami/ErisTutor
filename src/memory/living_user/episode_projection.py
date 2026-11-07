"""Small, deterministic episode views. No inference or embedding on read paths."""
from __future__ import annotations

import hashlib
import json
import math
import os
from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


def content_hash(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":"), default=str).encode("utf-8")).hexdigest()


def episode_hash(episode: Any) -> str:
    row = episode.to_dict()
    # Importance changes during merge/decay do not create a new memory event.
    row.pop("importance", None)
    return content_hash(row)


def token_cost(value: Any) -> int:
    """Conservative UTF-8 byte bound, without downloading a tokenizer in chat.

    Byte-level tokenizers use at most one token per byte. This deliberately
    underfills budgets; it is not a claim about provider usage/billing.
    """
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    return len(text.encode("utf-8"))


def local_date(timestamp: float):
    try:
        zone = ZoneInfo(os.getenv("ERIS_MEMORY_TIMEZONE", "Asia/Ho_Chi_Minh"))
    except ZoneInfoNotFoundError:
        zone = timezone(timedelta(hours=7))
    return datetime.fromtimestamp(timestamp, tz=zone).date()


def time_anchor(timestamp: float) -> str:
    day = local_date(timestamp)
    return max(day - timedelta(days=day.weekday()), day.replace(day=1)).isoformat()


def relative_time(timestamp: float, *, now: float) -> str:
    day, today = local_date(timestamp), local_date(now)
    age = (today - day).days
    if age < 0:
        return f"khoảng tháng {day.month}/{day.year}"
    if age == 0:
        return "hôm nay"
    if age == 1:
        return "hôm qua"
    if age < 7:
        return "mấy hôm trước"
    week = today - timedelta(days=today.weekday())
    if week - timedelta(days=7) <= day < week:
        return "tuần trước"
    if age < 28:
        return "vài tuần trước"
    return f"hồi tháng {day.month}/{day.year}"


def anchor_label(anchor: str, *, now: float) -> str:
    # Stable calendar anchors are stored; relative words are never persisted.
    day = datetime.fromisoformat(anchor).date()
    today = local_date(now)
    week = today - timedelta(days=today.weekday())
    weeks = math.floor((week - (day - timedelta(days=day.weekday()))).days / 7)
    if weeks == 0:
        return "tuần này"
    if weeks == 1:
        return "tuần trước"
    if 1 < weeks < 4:
        return "vài tuần trước"
    return f"hồi tháng {day.month}/{day.year}"


def episode_view(episode: Any, *, now: float) -> dict[str, Any]:
    return {"title": episode.title, "summary": episode.summary,
            "outcome": episode.outcome, "open_loops": list(episode.open_loops),
            "when": relative_time(episode.ended_at, now=now), "scope": episode.scope.to_dict()}
