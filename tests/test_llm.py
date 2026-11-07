"""Unit tests for the LLM subsystem (Step 1)."""
from __future__ import annotations

from llm.model_catalog import DEFAULT_OPEN_MODEL_ID, requires_open_model_hygiene
from llm.provider_errors import ProviderErrorKind, classify_provider_error
from llm.provider_policy import retired_provider
from llm.dialogue_recovery import recover_dialogue


def test_classify_provider_error_rate_limit() -> None:
    assert classify_provider_error("Resource exhausted: quota exceeded", status_code=429) == ProviderErrorKind.RATE_LIMIT


def test_classify_provider_error_key_revoked() -> None:
    assert classify_provider_error("API key not valid", status_code=401) == ProviderErrorKind.KEY_REVOKED


def test_classify_provider_error_project_permission() -> None:
    assert classify_provider_error("Permission denied", status_code=403) == ProviderErrorKind.PROJECT_PERMISSION


def test_model_catalog() -> None:
    assert DEFAULT_OPEN_MODEL_ID == "gemma-4-31b-it"
    assert requires_open_model_hygiene("gemma-4-31b-it") is True
    assert requires_open_model_hygiene("deepseek-chat") is False


def test_retired_provider() -> None:
    assert retired_provider("gemini") is True
    assert retired_provider(base_url="https://generativelanguage.googleapis.com") is True
    assert retired_provider("deepseek") is False


def test_recover_dialogue() -> None:
    schema = {"json_schema": {"name": "eris_public_reply"}}
    res = recover_dialogue("Xin chào bạn!", schema)
    assert res is not None
    assert res["text"] == "Xin chào bạn!"
    assert res["llm_malformed_json_repair"] is True
