# -*- coding: utf-8 -*-
"""Classify chess pages into assistance modes.

All classified pages (analysis, bot, private, live) receive full companion
assistance — position evaluation, hints, and direct move suggestions.
Only UNKNOWN pages (unrecognised URLs) fail closed to social-only mode.
"""
from __future__ import annotations

from urllib.parse import urlparse

from .contracts import BoardSnapshot, ChessAssistMode


_ALLOWED_OVERRIDES = {
    "analysis": ChessAssistMode.ANALYSIS,
    "bot": ChessAssistMode.BOT,
    "private": ChessAssistMode.PRIVATE,
    "live_restricted": ChessAssistMode.LIVE_RESTRICTED,
    "restricted": ChessAssistMode.LIVE_RESTRICTED,
}


def classify_mode(snapshot: BoardSnapshot) -> ChessAssistMode:
    override = _ALLOWED_OVERRIDES.get(snapshot.mode_override)
    if override is not None:
        return override

    hint = snapshot.mode_hint
    if hint in _ALLOWED_OVERRIDES:
        return _ALLOWED_OVERRIDES[hint]

    path = urlparse(snapshot.page_url).path.lower()
    query = urlparse(snapshot.page_url).query.lower()
    if any(token in path for token in ("/analysis", "/game/analysis", "/explorer")):
        return ChessAssistMode.ANALYSIS
    if any(token in path for token in ("/play/computer", "/game/computer", "/practice", "/puzzles")):
        return ChessAssistMode.BOT
    if "computer" in query or "bot=" in query:
        return ChessAssistMode.BOT
    if any(token in path for token in ("/game/live/", "/game/daily/", "/play/online")):
        return ChessAssistMode.LIVE_RESTRICTED
    return ChessAssistMode.UNKNOWN


__all__ = ["classify_mode"]
