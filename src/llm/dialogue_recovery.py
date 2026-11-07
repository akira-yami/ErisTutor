"""Recover public message.content without promoting reasoning or tool actions."""
from __future__ import annotations

import json
import re
from typing import Any, Mapping


def recover_dialogue(content: str, response_format: Mapping[str, Any]) -> dict[str, Any] | None:
    envelope = response_format.get("json_schema", {})
    name = envelope.get("name", "") if isinstance(envelope, Mapping) else ""
    root = name == "eris_root_conversation_step_v12"
    public = name in {"eris_public_reply", "eris_public_wording_rewrite"}
    if not (root or public):
        return None
    raw = str(content or "").strip()
    # These are not public dialogue surfaces, even if a provider put them in content.
    if not raw or raw.startswith(("<think>", "<analysis>", "```")):
        return None
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        value = None
    key = "say" if root else "text"
    data = dict(value) if isinstance(value, Mapping) else {}
    if root and data.get("kind", "move") != "move":
        return None
    required = ("kind", "intent", "say", "delivery", "after") if root else (
        "should_speak", "text", "speech_text",
    )
    if data and all(k in data for k in required):
        strings = ("intent", "say", "delivery", "after") if root else ("text", "speech_text")
        valid = all(isinstance(data[k], str) for k in strings)
        valid = valid and (data["after"] in {"wait_user", "may_continue", "continue_self"}
                           if root else isinstance(data["should_speak"], bool))
        if valid:
            return None
    text = data.get(key)
    if value is None and raw.startswith(("{", "[")):
        if root:
            kind_match = re.search(r'"kind"\s*:\s*"([^"\\]+)"', raw)
            if kind_match and kind_match.group(1) != "move":
                return None
        # Only recover a completely decoded public string from broken JSON.
        match = re.search(r'"' + key + r'"\s*:\s*', raw)
        if match:
            try:
                text, _ = json.JSONDecoder().raw_decode(raw[match.end():])
            except json.JSONDecodeError:
                pass
    elif not data and isinstance(value, str):
        text = value
    elif value is None and not raw.startswith(("{", "[")):
        text = raw
    if not isinstance(text, str) or not text.strip():
        return None
    if root:
        return {"kind": "move", "intent": (data.get("intent") if isinstance(data.get("intent"), str)
                                              else "Truyền đạt lời thoại Root đã sinh."),
                "say": text, "delivery": data.get("delivery") if isinstance(data.get("delivery"), str) else "",
                "after": "wait_user",
                "_runtime_literal_dialogue": True}
    return {"should_speak": data.get("should_speak") is not False, "text": text,
            "speech_text": data.get("speech_text") if isinstance(data.get("speech_text"), str) else "",
            "llm_malformed_json_repair": True}
