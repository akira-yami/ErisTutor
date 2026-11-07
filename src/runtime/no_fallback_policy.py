from __future__ import annotations

import json
import os
from typing import Iterable


def no_fallback_mode_enabled() -> bool:
    try:
        from runtime.runtime_settings_store import runtime_bool

        # Store lưu chiều dương ``auto_fallback``; no-fallback là phủ định.
        return not runtime_bool("chat", "auto_fallback", default=True)
    except Exception:
        raw = os.getenv("ERIS_NO_FALLBACK_MODE")
        if raw is None:
            return False
        return raw.strip().lower() in {"1", "true", "yes", "on", "y"}


def build_no_fallback_payload(
    *,
    channel: str,
    reason: str,
    text: str = "",
    should_speak: bool | None = None,
    parse_mode: str = "no_fallback_block",
    confidence: float = 0.0,
    reasoning_tags: Iterable[str] = (),
) -> str:
    mode_text = (text or _default_no_fallback_text(channel=channel, reason=reason)).strip()
    speak = bool(mode_text) if should_speak is None else bool(should_speak)
    payload = {
        "text": mode_text if speak else "",
        "intent": "no_fallback_block",
        "action": "none",
        "reasoning_tags": ["no_fallback_mode", channel, *tuple(reasoning_tags or ())],
        "should_speak": speak,
        "confidence": max(0.0, float(confidence or 0.0)),
        "animation": "stand",
        "direction": 0,
        "target_x": -1.0,
        "bounce_action": "",
        "extracted_fact": "",
        "debug_trace": f"no_fallback_mode:{channel}:{reason}",
        "next_animation": "",
        "system_action": "",
        "action_input": "",
        "parse_mode": parse_mode,
    }
    return json.dumps(payload, ensure_ascii=False)


def _default_no_fallback_text(*, channel: str, reason: str) -> str:
    reason_text = str(reason or "blocked").strip()
    if channel == "chat":
        # Keep the user-facing block clean. The detailed reason stays in debug_trace.
        return "Tôi chặn lượt này vì no-fallback mode đang bật."
    if channel == "session_ritual":
        return ""
    if channel in {"directive", "autonomous", "force_speak", "presence"}:
        return ""
    return f"No-fallback mode blocked this path: {reason_text}."
