"""Read a Canvas JSON draft from a completion response."""
from collections.abc import Mapping
import json

from llm.json_sanitizer import extract_last_valid_json_object, strip_reasoning_envelope


def read_model_object(response: object) -> dict | None:
    if isinstance(response, str):
        content = response
    elif isinstance(response, Mapping):
        content = response.get("output_text") or response.get("text") or response.get("content")
        if not isinstance(content, str):
            choices = response.get("choices")
            if isinstance(choices, list):
                for choice in choices:
                    message = choice.get("message") if isinstance(choice, Mapping) else None
                    if isinstance(message, Mapping) and isinstance(message.get("content"), str):
                        content = message["content"]
                        break
    else:
        return None
    if not isinstance(content, str):
        return None
    raw = extract_last_valid_json_object(strip_reasoning_envelope(content))
    return json.loads(raw) if raw else None
