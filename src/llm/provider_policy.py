"""Providers removed from the Eris runtime, including stale configuration."""
from urllib.parse import urlsplit


def retired_provider(provider: str = "", *, base_url: str = "", model: str = "") -> bool:
    name = str(provider or "").strip().lower()
    model_name = str(model or "").strip().lower().removeprefix("models/")
    host = (urlsplit(str(base_url or "")).hostname or "").lower()
    return (
        name in {"gemini", "gemma", "groq", "google"}
        or any(part.startswith(("gemini", "gemma")) for part in model_name.split("/"))
        or host == "generativelanguage.googleapis.com"
        or host == "api.groq.com"
    )


def require_active_provider(*, base_url: str, model: str) -> None:
    if retired_provider(base_url=base_url, model=model):
        raise ValueError("Gemini/Gemma/Groq providers are retired from Eris runtime.")
