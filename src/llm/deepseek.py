"""DeepSeek provider constants and request-policy helpers.

The runtime uses the official OpenAI-compatible endpoint.  Provider-specific
logic lives here so legacy aggregator defaults do not leak across process manager,
key pools and transport payloads.
"""
from __future__ import annotations

import os

DEEPSEEK_BASE_URL = "https://api.deepseek.com"
DEEPSEEK_DEFAULT_MODEL = "deepseek-v4-flash"
DEEPSEEK_STRONG_MODEL = "deepseek-v4-pro"
DEEPSEEK_MODELS = frozenset({DEEPSEEK_DEFAULT_MODEL, DEEPSEEK_STRONG_MODEL})
_DEPRECATED_MODELS = {
    "deepseek-chat": (DEEPSEEK_DEFAULT_MODEL, "disabled"),
    "deepseek-reasoner": (DEEPSEEK_DEFAULT_MODEL, "enabled"),
}


def is_deepseek(base_url: str | None = None, model_name: str | None = None) -> bool:
    url = str(base_url or "").strip().lower()
    model = str(model_name or "").strip().lower()
    return "api.deepseek.com" in url or model.startswith("deepseek-")


def normalize_model(model_name: str | None) -> str:
    value = str(model_name or "").strip().lower()
    if not value:
        return DEEPSEEK_DEFAULT_MODEL
    return _DEPRECATED_MODELS.get(value, (value, ""))[0]


def deprecated_model_mode(model_name: str | None) -> str:
    value = str(model_name or "").strip().lower()
    return _DEPRECATED_MODELS.get(value, ("", ""))[1]


def thinking_mode(model_name: str | None = None, requested: str | None = None) -> str:
    """Return ``enabled`` or ``disabled`` for DeepSeek requests.

    ERIS defaults to non-thinking mode for low-latency companion chat and to
    preserve temperature/penalty behaviour.  Complex tasks can opt in with
    ``ERIS_DEEPSEEK_THINKING=enabled``.  A deprecated ``deepseek-reasoner``
    model name still maps to V4 Flash with thinking enabled.
    """

    requested_raw = str(requested or "").strip().lower()
    if requested_raw in {"1", "true", "yes", "on", "enabled", "enable", "thinking"}:
        return "enabled"
    if requested_raw in {"0", "false", "no", "off", "disabled", "disable", "non-thinking", "non_thinking"}:
        return "disabled"

    raw = str(
        os.environ.get(
            "ERIS_DEEPSEEK_THINKING",
            os.environ.get("ERIS_DEEPSEEK_THINKING_MODE", ""),
        )
        or ""
    ).strip().lower()
    if raw in {"1", "true", "yes", "on", "enabled", "enable", "thinking"}:
        return "enabled"
    if raw in {"0", "false", "no", "off", "disabled", "disable", "non-thinking", "non_thinking"}:
        return "disabled"
    legacy_mode = deprecated_model_mode(model_name)
    return legacy_mode or "disabled"


def reasoning_effort(requested: str | None = None) -> str:
    raw = str(requested or os.environ.get("ERIS_DEEPSEEK_REASONING_EFFORT", "high") or "high").strip().lower()
    if raw in {"max", "xhigh", "maximum"}:
        return "max"
    return "high"


def supports_image_input(base_url: str | None = None, model_name: str | None = None) -> bool:
    """DeepSeek's current public Chat Completions schema is text-only.

    Keep the check explicit so the screen captioner does not send image_url
    payloads to a text endpoint and then spend several retries discovering the
    obvious through HTTP 400 responses.
    """

    return not is_deepseek(base_url, model_name)
