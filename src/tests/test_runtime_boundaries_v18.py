from __future__ import annotations

import json
from dataclasses import dataclass
from types import SimpleNamespace

import pytest

from actions.contracts import ActionExecutionResult
from runtime.no_fallback_policy import build_no_fallback_payload
from runtime.panel_snapshot import write, write_social_observation
from runtime.panel_state import build_panel_state
from runtime.provider_adapters import (
    ActionExecutorProvider,
    MemoryKernelProvider,
    SpeechEngineProvider,
    TokenPressureBudgetProvider,
    build_provider_registry,
    register_or_replace,
)
from runtime.provider_interfaces import ProviderHealth, ProviderRegistry
from runtime.public_surface_commit import PublicSurfaceCommitter
from runtime.stage_ownership import StageOwnership, stage_channel_blocked, stage_ownership_for
from runtime.telemetry_text_policy import is_generic_telemetry_line
from runtime.tts_budget import TtsRequestBudget


class DummyActionExecutor:
    observer = object()

    def execute(self, action_name: str, action_input: str):
        return {"success": True, "message": "ok", "data": {"value": action_input}}


class DummySpeechEngine:
    _ok = True
    _closed = False

    def __init__(self) -> None:
        self.calls = []

    def synthesize(self, text: str, **kwargs):
        self.calls.append((text, kwargs))
        return b"wav"

    def cache_signature(self):
        return "cache:v1"

    def fallback_health_snapshot(self):
        return {"edge": "healthy"}


class DummyMemory:
    def recall(self, request):
        return {"request": request}

    def store(self, record):
        return {"stored": record}


class DummyGovernor:
    def plan_request(self, provider, **kwargs):
        return {"provider": provider, **kwargs}


class BrokenHealthProvider:
    def execute(self, action_name: str, action_input: str = ""):
        return None

    def health(self):
        raise RuntimeError("boom")


def test_provider_registry_and_legacy_adapters_cover_success_and_failure_paths() -> None:
    registry = build_provider_registry(
        action_executor=DummyActionExecutor(),
        speech_engine=DummySpeechEngine(),
        memory=DummyMemory(),
        budget_governor=DummyGovernor(),
    )
    assert registry.kinds() == ("action", "budget", "memory", "speech")
    assert all(item.ready for item in registry.health().values())

    action = registry.get("action").execute("get_local_time", "Bangkok")
    assert isinstance(action, ActionExecutionResult)
    assert action.success is True
    assert action.observed is True

    speech = registry.get("speech")
    contract = SimpleNamespace(
        language="vi",
        locale="vi-VN",
        voice_family="eris",
        turn_id="turn-1",
        segment_id=2,
        interruptible=False,
        prelocalized=True,
        prosody_style="dry",
        prosody_notes="short",
        valence=0.1,
        arousal=0.4,
        stance="teasing",
    )
    assert speech.synthesize(contract, "xin chào") == b"wav"
    assert speech.engine.calls[0][1]["locale"] == "vi-VN"

    memory = registry.get("memory")
    assert memory.query("needle")["request"] == "needle"
    assert memory.commit("record")["stored"] == "record"

    budget = registry.get("budget")
    plan = budget.plan(provider="gemini", lane="chat", prompt_tokens=-1, output_tokens=0)
    assert plan["prompt_tokens"] == 0
    assert plan["requested_output_tokens"] == 1

    registry.unregister("speech")
    assert registry.get_optional("speech") is None
    with pytest.raises(LookupError):
        registry.get("speech")
    with pytest.raises(KeyError):
        registry.register("unknown", object())
    with pytest.raises(TypeError):
        registry.register("speech", object())


def test_provider_health_failure_is_projected_not_raised() -> None:
    registry = ProviderRegistry()
    registry.register("action", BrokenHealthProvider())
    health = registry.health()["action"]
    assert health.ready is False
    assert health.reason == "RuntimeError"


def test_register_or_replace_and_missing_adapter_methods() -> None:
    registry = ProviderRegistry()
    first = ActionExecutorProvider(DummyActionExecutor())
    registry.register("action", first)
    second = ActionExecutorProvider(DummyActionExecutor())
    register_or_replace(registry, "action", second)
    assert registry.get("action") is second

    with pytest.raises(RuntimeError):
        ActionExecutorProvider(object()).execute("x")
    with pytest.raises(RuntimeError):
        SpeechEngineProvider(object()).synthesize({}, "x")
    with pytest.raises(RuntimeError):
        MemoryKernelProvider(object()).query("x")
    with pytest.raises(RuntimeError):
        MemoryKernelProvider(object()).commit("x")
    with pytest.raises(RuntimeError):
        TokenPressureBudgetProvider(object()).plan(
            provider="x", lane="x", prompt_tokens=1, output_tokens=1
        )


def test_stage_ownership_lease_and_helpers() -> None:
    ownership = StageOwnership()
    assert ownership.acquire(owner_id="call-1", kind="call", blocks=("autonomous", "chat")) is True
    assert ownership.acquire(owner_id="call-2", kind="other") is False
    assert ownership.blocks("autonomous") is True
    assert ownership.blocks("autonomous", requester_id="call-1") is False
    assert ownership.release("wrong") is False
    assert ownership.release("call-1") is True
    assert ownership.snapshot().active is False
    with pytest.raises(ValueError):
        ownership.acquire(owner_id="", kind="x")

    owner = SimpleNamespace()
    assert stage_ownership_for(owner) is stage_ownership_for(owner)
    stage_ownership_for(owner).acquire(owner_id="task", kind="task", blocks=("chat",))
    assert stage_channel_blocked(owner, "chat") is True


def test_tts_budget_env_clamping_and_validation(monkeypatch) -> None:
    monkeypatch.setenv("ERIS_TTS_MAX_TRANSCRIPT_CHARS", "10")
    monkeypatch.setenv("ERIS_TTS_MAX_PROMPT_CHARS", "999999")
    monkeypatch.setenv("ERIS_TTS_MAX_STYLE_CHARS", "bad")
    monkeypatch.setenv("ERIS_TTS_MAX_CONCURRENCY", "99")
    budget = TtsRequestBudget.from_env()
    assert budget.max_transcript_chars == 48
    assert budget.max_prompt_chars == 24000
    assert budget.max_style_chars == 900
    assert budget.max_concurrency == 8
    budget.validate(transcript="ok", prompt="prompt", style_text="dry")
    with pytest.raises(RuntimeError, match="transcript"):
        budget.validate(transcript="x" * 49, prompt="")
    with pytest.raises(RuntimeError, match="style"):
        budget.validate(transcript="ok", prompt="", style_text="x" * 901)
    with pytest.raises(RuntimeError, match="prompt"):
        budget.validate(transcript="ok", prompt="x" * 24001)


def test_panel_state_and_snapshot_round_trip(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("ERIS_MEMORY_CORE_DIR", str(tmp_path))
    monkeypatch.setenv("ERIS_PANEL_SNAPSHOT", "1")
    monkeypatch.setenv("ERIS_PANEL_SNAPSHOT_MIN_SEC", "0")

    (tmp_path / "development_profile.json").write_text(
        json.dumps({"axes": {"warmth_temperature": {"delta": 0.25}}}),
        encoding="utf-8",
    )
    (tmp_path / "anchor_state.json").write_text(
        json.dumps({"attachment_longarc": 0.4}), encoding="utf-8"
    )
    (tmp_path / "private_concerns.json").write_text(
        json.dumps({"concerns": [{"theme": "build", "recurrence": 2, "pressure": 0.5}]}),
        encoding="utf-8",
    )

    appraisal = SimpleNamespace(
        valence=0.2, arousal=0.4, tenderness=0.1, threat=0.0, stance="focused"
    )
    write(
        appraisal=appraisal,
        expression_gate={"want_to_say": 0.8, "suppression_pressure": 0.1, "allowed_expression": 0.7},
        tom_read="user seems focused ~0.8",
        channel="chat",
        signal="expanded",
    )
    write_social_observation(
        {"event": "repair"},
        relationship_state={"trust": 0.6},
        cognitive_views={"summary": "stable"},
    )

    state = build_panel_state()
    assert state["identity"]["alive"] is True
    assert state["identity"]["channel"] == "chat"
    assert state["affect"]["stance"] == "focused"
    assert state["tom_read"]["confidence"] == 0.8
    assert state["n2"]["live"] is True
    assert state["n2"]["attachment"]["value"] == 0.4
    assert state["interiority"]["items"][0]["theme"] == "build"
    assert state["social_observer"]["event"] == "repair"


def test_public_surface_commit_and_no_fallback_payload() -> None:
    committer = PublicSurfaceCommitter()
    surfaces = committer.build("(nháy mắt) Xin chào `secret_code()`")
    assert "nháy mắt" not in surfaces.display_text.lower()
    assert "nháy mắt" not in surfaces.memory_text.lower()
    assert surfaces.raw_text.startswith("(")
    assert committer.clean_fact("(cười khẽ) fact") == "fact"

    payload = json.loads(build_no_fallback_payload(channel="chat", reason="provider_down"))
    assert payload["intent"] == "no_fallback_block"
    assert payload["should_speak"] is True
    assert "provider_down" in payload["debug_trace"]
    silent = json.loads(build_no_fallback_payload(channel="autonomous", reason="blocked"))
    assert silent["should_speak"] is False


def test_generic_telemetry_filter() -> None:
    assert is_generic_telemetry_line("") is True
    assert is_generic_telemetry_line("Active window is Code") is True
    assert is_generic_telemetry_line("3 browser tabs are visible") is True
    assert is_generic_telemetry_line("2 windows are open") is True
    assert is_generic_telemetry_line("Tests failed in service.py") is False
