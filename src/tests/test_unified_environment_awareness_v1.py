from __future__ import annotations

from types import SimpleNamespace

from cortex.mental_material import derive_causal_motives
from runtime.ai_orchestrated.desktop_semantics import DesktopSemanticEvent, UserSituation
from runtime.ai_orchestrated.random_chat_seed import RandomChatSeed
from runtime.ai_orchestrated.random_chat_seed_service import RandomChatSeedReservoir, _SeedEntry
from runtime.environment_awareness import ensure_environment_awareness_hub


class _Host(SimpleNamespace):
    pass


class _Logger:
    def debug(self, *args, **kwargs):
        pass


class _Generator:
    enabled = True


class _MachineSnapshot:
    def to_machine_context(self):
        return {
            "active_app": "Godot Engine",
            "active_process": "godot.exe",
            "active_window_title": "shader.gdshader - Eris",
        }


def _situation(now: float = 1000.0, *, semantic_delta: bool = True) -> UserSituation:
    event = DesktopSemanticEvent(
        timestamp=now - 2.0,
        kind="focus_changed",
        category="editor",
        semantic=True,
        attention_level=2,
        confidence=0.88,
        novelty=0.72,
        importance=0.64,
        source="uia",
        app="Godot Engine",
        detail="Shader editor focused",
        event_key="focus:shader-editor",
        evidence_refs=("uia:shader",),
    )
    return UserSituation(
        timestamp=now - 1.0,
        activity="coding",
        task_hint="editing shader",
        phase="debugging",
        focused_app="Godot Engine",
        focused_process="godot.exe",
        focused_object="shader.gdshader",
        last_action="focus_editor",
        last_outcome="unknown",
        attention_state="focused",
        confidence=0.86,
        semantic_delta=semantic_delta,
        wake_recommended=semantic_delta,
        max_attention_level=2,
        recent_events=(event,),
        evidence_refs=("env:situation",),
    )


def test_environment_awareness_hub_is_shared_structured_projection() -> None:
    host = _Host(logger=_Logger(), last_user_situation=_situation())
    hub = ensure_environment_awareness_hub(host)
    snap = hub.refresh(machine_snapshot=_MachineSnapshot(), now=1000.0, source="test")

    assert snap.focused_app == "Godot Engine"
    assert snap.focused_process == "godot.exe"
    assert snap.focused_object == "shader.gdshader - Eris"
    assert snap.task_hint == "editing shader"
    assert snap.focus_from_current_telemetry is True
    assert host.environment_awareness_hub is hub
    assert host.last_environment_awareness is snap
    facts = snap.grounding_facts(now=1000.0)
    assert {item["kind"] for item in facts} == {"deep_environment_focus", "deep_environment_task"}
    assert "not VLM/pixel" in snap.to_dict(now=1000.0)["epistemic_contract"]


def test_seed_context_receives_environment_awareness_and_fingerprint_tracks_it() -> None:
    reservoir = RandomChatSeedReservoir(_Generator(), clock=lambda: 1000.0)
    snapshot = {
        "environment_awareness": {
            "schema": "environment_awareness.v1",
            "revision": 7,
            "captured_at": 1000.0,
            "situation_timestamp": 999.0,
            "age_seconds": 1.0,
            "fresh": True,
            "source": "deep_environment_event",
            "focused_app": "Godot Engine",
            "focused_process": "godot.exe",
            "focused_object": "shader.gdshader",
            "activity": "coding",
            "task_hint": "editing shader",
            "phase": "debugging",
            "confidence": 0.86,
            "semantic_delta": True,
            "wake_recommended": True,
            "max_attention_level": 2,
            "evidence_refs": ["env:situation"],
            "recent_events": [{
                "timestamp": 998.0,
                "kind": "focus_changed",
                "attention_level": 2,
                "confidence": 0.88,
                "importance": 0.64,
                "source": "uia",
                "app": "Godot Engine",
                "detail": "Shader editor focused",
                "event_key": "focus:shader-editor",
                "evidence_refs": ["uia:shader"],
            }],
        },
        "recent_public_history": [],
    }
    request = reservoir._context_from_snapshot(snapshot)
    assert request["environment_awareness"]["focused_app"] == "Godot Engine"
    assert request["environment_awareness"]["task_hint"] == "editing shader"
    projection = reservoir._fingerprint_projection(request)
    assert projection["environment_awareness"]["revision"] == 7
    assert projection["environment_awareness"]["recent_event_keys"] == ["focus:shader-editor"]


def test_environment_change_becomes_seed_causal_motive_but_static_scene_does_not() -> None:
    base = {
        "relationship": {"connection_pressure": 0.2},
        "environment_awareness": {
            "revision": 7,
            "age_seconds": 1.0,
            "fresh": True,
            "focused_app": "Godot Engine",
            "focused_process": "godot.exe",
            "focused_object": "shader.gdshader",
            "activity": "coding",
            "task_hint": "editing shader",
            "phase": "debugging",
            "confidence": 0.86,
            "semantic_delta": True,
            "max_attention_level": 2,
            "evidence_refs": ["env:situation"],
            "recent_events": [{
                "timestamp": 998.0,
                "kind": "focus_changed",
                "semantic": True,
                "attention_level": 2,
                "confidence": 0.88,
                "importance": 0.64,
                "detail": "Shader editor focused",
                "event_key": "focus:shader-editor",
                "evidence_refs": ["uia:shader"],
            }],
        },
    }
    motives = derive_causal_motives(base, now=1000.0)
    environment = [item for item in motives if item.cause_kind == "environment_event"]
    assert len(environment) == 1
    assert environment[0].cause_id == "environment:r7:focus:shader-editor"
    assert "editing shader" in environment[0].semantic_focus

    static = dict(base)
    static_env = dict(base["environment_awareness"])
    static_env["semantic_delta"] = False
    static_env["recent_events"] = []
    static["environment_awareness"] = static_env
    assert not [item for item in derive_causal_motives(static, now=1000.0) if item.cause_kind == "environment_event"]


def test_environment_seed_carrier_keeps_current_environment_provenance() -> None:
    seed = RandomChatSeed.from_material_mapping(
        {
            "decision": "seed",
            "premise": "Có vẻ bạn lại quay về chỉnh shader.",
            "material_kind": "observation",
            "confidence": 0.8,
        },
        causal_motive={
            "cause_id": "environment:focus:shader-editor",
            "cause_kind": "environment_event",
            "motive_kind": "notice_environment_change",
            "semantic_focus": "app=Godot Engine; task=editing shader",
            "why_now": "environment_semantic_change",
            "strength": 0.7,
            "source_refs": ["uia:shader"],
            "allowed_material_kinds": ["observation", "reaction"],
        },
    )
    assert seed.decision == "seed"
    assert seed.source_kind == "current_environment"
    assert seed.cause_kind == "environment_event"


def test_environment_seed_remains_eligible_for_short_context_drift() -> None:
    reservoir = RandomChatSeedReservoir(_Generator(), clock=lambda: 1000.0)
    seed = RandomChatSeed.from_material_mapping(
        {
            "decision": "seed",
            "premise": "Bạn vừa quay lại shader editor.",
            "material_kind": "observation",
            "confidence": 0.8,
        },
        causal_motive={
            "cause_id": "environment:focus:shader-editor",
            "cause_kind": "environment_event",
            "motive_kind": "notice_environment_change",
            "semantic_focus": "app=Godot Engine; task=editing shader",
            "why_now": "environment_semantic_change",
            "strength": 0.7,
            "source_refs": ["uia:shader"],
            "allowed_material_kinds": ["observation"],
        },
    )
    reservoir._context_revision = 5
    reservoir._user_epoch = 2
    entry = _SeedEntry(
        seed=seed, generated_at=999.0, expires_at=1100.0, context_revision=4,
        user_epoch=2, context_fingerprint="x",
    )
    assert reservoir._entry_compatible_locked(entry) is True
    reservoir._context_revision = 7
    assert reservoir._entry_compatible_locked(entry) is False
