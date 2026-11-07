from __future__ import annotations

from types import SimpleNamespace

import pytest

from actions.capability_runtime import runtime_sensor_health_snapshot
from cortex.mental_material import derive_causal_motives
from runtime.attention_router import AttentionRouter
from runtime.behavior_proposals import ProposalBus
from runtime.chat_cycle import ChatCycleMixin
from runtime.intent_model import normalize_intent
from runtime.world_state import WorldStateService, attach_world_state_projection


class _Logger:
    def debug(self, *_a, **_k):
        pass
    def info(self, *_a, **_k):
        pass
    def warning(self, *_a, **_k):
        pass


class _Host(SimpleNamespace):
    pass


class _ChatHost(ChatCycleMixin):
    pass


def _awareness(*, semantic_delta: bool = True, revision: int = 7, now: float = 1000.0):
    return {
        "schema": "environment_awareness.v1",
        "revision": revision,
        "captured_at": now,
        "situation_timestamp": now - 1.0,
        "focus_from_current_telemetry": True,
        "source": "deep_environment",
        "focused_app": "Godot Engine",
        "focused_process": "godot.exe",
        "focused_object": "shader.gdshader - Godot",
        "activity": "coding",
        "task_hint": "debugging shader",
        "phase": "debugging",
        "last_action": "compile",
        "last_outcome": "failed",
        "attention_state": "focused",
        "interruption_cost": 0.2,
        "busy_score": 0.62,
        "confidence": 0.91,
        "semantic_delta": semantic_delta,
        "wake_recommended": semantic_delta,
        "max_attention_level": 3,
        "evidence_refs": ["uia:shader", "env:task"],
        "recent_events": [{
            "timestamp": now - 1.0,
            "kind": "build_failed",
            "event_key": "build:shader:failed",
            "detail": "shader compile failed",
            "confidence": 0.95,
            "importance": 0.9,
            "novelty": 0.85,
            "attention_level": 3,
            "evidence_refs": ["uia:shader"],
        }],
    }


def test_world_state_separates_facts_from_interpretations_with_provenance() -> None:
    host = _Host(logger=_Logger())
    service = WorldStateService(host, clock=lambda: 1000.0)
    service.ingest_environment(_awareness(), now=1000.0)
    snap = service.snapshot(now=1000.0)

    app = snap.environment.fact("environment.focused_app", now=1000.0)
    task = snap.environment.interpretation("environment.task_hint", now=1000.0)
    assert app is not None and app.value == "Godot Engine"
    assert app.source == "deep_environment"
    assert app.evidence_refs == ("uia:shader", "env:task")
    assert task is not None and task.value == "debugging shader"
    assert task.source == "desktop_semantics"
    assert "environment.focused_app" in task.based_on
    assert snap.environment.fact("environment.task_hint", now=1000.0) is None


def test_world_state_preserves_freshness_instead_of_rejuvenating_old_semantics() -> None:
    host = _Host(logger=_Logger())
    service = WorldStateService(host, clock=lambda: 1200.0)
    old = _awareness(now=1000.0)
    old["focus_from_current_telemetry"] = False
    service.ingest_environment(old, now=1200.0)
    snap = service.snapshot(now=1200.0)
    assert snap.environment.fact("environment.focused_app", now=1200.0) is None
    assert snap.environment.interpretation("environment.task_hint", now=1200.0) is None


def test_same_environment_edge_is_journaled_and_proposed_only_once() -> None:
    host = _Host(logger=_Logger())
    service = WorldStateService(host, clock=lambda: 1000.0)
    raw = _awareness()
    service.ingest_environment(raw, now=1000.0)
    service.ingest_environment(raw, now=1000.5, source="world_refresh")
    env_events = service.journal.find(kind="environment_changed", limit=20)
    assert len(env_events) == 1
    proposals = tuple(getattr(host, "behavior_proposal_bus").tail(50))
    assert sum(1 for p in proposals if p.kind == "generate_seed") == 1
    assert all(not p.public and p.kind != "speak" for p in proposals)


def test_seed_context_is_compact_and_drives_one_environment_motive() -> None:
    host = _Host(logger=_Logger())
    service = WorldStateService(host, clock=lambda: 1000.0)
    service.ingest_environment(_awareness(), now=1000.0)
    ctx = service.snapshot(now=1000.0).seed_context(now=1000.0)

    assert ctx["current_activity"] == "coding"
    assert ctx["current_task"] == "debugging shader"
    assert ctx["meaningful_change"]["event_key"] == "build:shader:failed"
    assert "facts" not in ctx and "interpretations" not in ctx
    motives = derive_causal_motives({"seed_context": ctx, "relationship": {}}, now=1000.0)
    env = [m for m in motives if m.cause_kind == "environment_event"]
    assert len(env) == 1
    assert "debugging shader" in env[0].semantic_focus
    assert "not VLM/pixel" in env[0].semantic_focus


def test_attention_router_never_authorizes_speech() -> None:
    decision = AttentionRouter().route_environment(_awareness())
    assert "update_world" in decision.actions
    assert "generate_seed" in decision.actions
    assert "wake_cognition" in decision.actions
    assert "speak" not in decision.actions


def test_proposal_bus_is_never_a_public_speech_authority() -> None:
    # P2: thậm chí "executive_governor" cũng không thể mạo danh Root/Kernel.
    bus = ProposalBus(clock=lambda: 1000.0)
    for source in ("seed_worker", "executive_governor", "conversation_root_agent_v12"):
        with pytest.raises(PermissionError):
            bus.submit(source=source, kind="speak", public=True)
    assert not bus.tail()


def test_intent_normalizer_distinguishes_environment_text_pixels_and_capability() -> None:
    activity = normalize_intent("em biết tôi đang làm gì không")
    assert activity.domain == "environment"
    assert activity.operation == "inspect_current_state"
    assert activity.granularity == "activity"
    assert activity.environment_sufficient is True

    visual = normalize_intent("nút bên phải trên màn hình màu gì")
    assert visual.domain == "screen" and visual.requires_pixels is True

    text = normalize_intent("đọc dòng code chính xác trên màn hình")
    assert text.domain == "screen" and text.requires_screen_text is True

    capability = normalize_intent("em không xem được màn hình tôi hả")
    assert capability.domain == "environment"
    assert capability.operation == "inspect_perception_capability"


def test_event_journal_explains_why_world_state_contains_a_fact() -> None:
    host = _Host(logger=_Logger())
    service = WorldStateService(host, clock=lambda: 1000.0)
    service.ingest_environment(_awareness(), now=1000.0)
    service.observe_user_turn("em biết tôi đang làm gì không", now=1001.0)
    service.observe_assistant_commit("Bạn đang debug shader.", now=1002.0)
    kinds = [event.kind for event in service.journal.tail(10)]
    assert kinds == ["environment_changed", "user_turn", "assistant_commit"]
    explanation = service.explain("environment.focused_app", now=1002.0)
    assert explanation["facts"][0]["value"] == "Godot Engine"


def test_sensor_health_registry_keeps_vlm_independent_from_uia_ocr_and_deep_env(monkeypatch) -> None:
    monkeypatch.setenv("ERIS_SCREEN_OCR_ENABLED", "1")
    store = SimpleNamespace(snapshot=lambda: SimpleNamespace(backend="rapidocr"))
    host = _Host(
        semantic_environment_worker=object(),
        screen_text_worker=object(),
        screen_evidence_store=store,
        browser_bridge_server=None,
        vision_semantic_compressor=None,
        project_operations=object(),
    )
    health = runtime_sensor_health_snapshot(owner=host)
    assert health["deep_environment"] == "healthy"
    assert health["uia"] == "healthy"
    assert health["ocr"] == "healthy"
    assert health["vlm"] == "unavailable"
    assert health["workspace"] == "healthy"


def test_chat_uses_one_lazy_perception_coordinator_and_registers_late_structured_sources() -> None:
    class Source:
        structured_source_id = "test_bridge"
        engagement_kind = "test"
        def is_engagement_active(self): return True
        def is_state_fresh(self, *, max_age_seconds=30.0): return True
        def matches_turn(self, user_text): return "bridge" in user_text
        def current_grounding_evidence(self, *, max_age_seconds=30.0, relevant_to_turn=False):
            return ({"id": "bridge:1", "claim": "bridge state", "grounded": True},)

    host = _ChatHost()
    host.logger = _Logger()
    host.screen_evidence_store = None
    host.screen_text_worker = None
    host.screen_relevance_router = None
    host.chess_bridge_service = None
    host.structured_evidence_sources = [Source()]

    first = host._ensure_chat_perception_coordinator()
    second = host._ensure_chat_perception_coordinator()
    assert first is second
    assert first.has_fresh_structured_answer("bridge please") is True
    assert first.structured_facts("bridge please")[0]["source"] == "test_bridge"


def test_observer_projection_is_attached_from_same_world_state() -> None:
    host = _Host(logger=_Logger(), active_conversational_topic="shader")
    service = WorldStateService(host, clock=lambda: 1000.0)
    host.world_state_service = service
    service.ingest_environment(_awareness(), now=1000.0)
    packet = attach_world_state_projection(host, {"old": 1}, lane="observer", now=1000.0)
    assert packet["old"] == 1
    assert packet["world_state"]["lane"] == "observer"
    assert packet["world_state"]["environment_awareness"]["focused_app"] == "Godot Engine"
    assert "FACT != INTERPRETATION" in packet["world_state_context"]


def test_world_state_reads_mental_material_from_snapshot_api() -> None:
    """Regression: MentalMaterialRuntime không có panel_view(); V12 phải dùng snapshot()."""
    material = {
        "material_id": "m1",
        "material_kind": "association",
        "summary": "Một liên hệ riêng đang còn sống",
        "activation": 0.81,
    }
    host = _Host(
        logger=_Logger(),
        mental_material_runtime=SimpleNamespace(snapshot=lambda **_kwargs: (material,)),
        active_concern_runtime=SimpleNamespace(snapshot=lambda **_kwargs: ()),
    )
    service = WorldStateService(host, clock=lambda: 1000.0)
    snap = service.refresh_from_host(now=1000.0)

    assert snap.cognition.mental_material == (material,)
    prompt = snap.prompt_context("self_turn", now=1000.0)
    assert "mental_material=" in prompt
    assert "hidden reasoning" in prompt
