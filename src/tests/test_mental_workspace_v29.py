from __future__ import annotations

import time
from types import SimpleNamespace

from cortex.private_life import PrivateLifeCore, PrivateLifeState
from runtime.ai_orchestrated.random_chat_seed import RandomChatSeed
from runtime.ai_orchestrated.random_chat_seed_service import RandomChatSeedReservoir
from runtime.ai_orchestrated.self_context import build_autonomous_self_context


def _seed(seed_id: str = "weather_guess", *, event_kind: str = "speculation", epistemic_mode: str = "speculation") -> RandomChatSeed:
    return RandomChatSeed.from_mapping({
        "decision": "seed",
        "seed_id": seed_id,
        "mode": "eris_led",
        "premise": "Có khi hôm nay trời đẹp; tôi chỉ đang đoán thôi.",
        "source_kind": "world_hypothesis",
        "source_refs": [],
        "topic_tags": ["weather", "today"],
        "event_kind": event_kind,
        "epistemic_mode": epistemic_mode,
        "open_loop": event_kind in {"knowledge_gap", "discovery_impulse"},
        "opening_move": "share_speculation",
        "desired_response": ["optional_reaction"],
        "eris_motive": {
            "kind": "want_to_express",
            "why_now": "Một giả thuyết nhẹ tự nảy ra.",
            "personal_stake": 0.45,
            "authenticity": 0.78,
        },
        "risk": {
            "daring": 0.22,
            "uncertainty": 0.76,
            "intrusion_risk": 0.02,
            "face_risk": 0.05,
            "recovery_available": True,
        },
        "estimated_fit": 0.42,
        "confidence": 0.38,
        "tone": "casual_warm",
        "ttl_seconds": 600,
    })


def test_seed_event_is_persistable_private_life_mental_event(tmp_path) -> None:
    core = PrivateLifeCore(root_dir=tmp_path)
    event = core.ingest_seed_mental_event(_seed())

    assert event is not None
    assert event.kind == "speculation"
    assert event.epistemic_mode == "speculation"
    assert event.origin_source == "world_hypothesis"
    assert core.state.mental_events[event.id].content.startswith("Có khi hôm nay")

    payload = core.state.to_dict()
    restored = PrivateLifeState.from_mapping(payload)
    assert payload["version"] == 3
    assert restored.mental_events[event.id].epistemic_mode == "speculation"


def test_same_topic_reactivates_instead_of_spamming_workspace(tmp_path) -> None:
    core = PrivateLifeCore(root_dir=tmp_path)
    first = core.ingest_seed_mental_event(_seed("a"))
    second = core.ingest_seed_mental_event(_seed("b"))

    assert first is not None and second is not None
    assert first.id == second.id
    assert second.activation_count == 2
    assert len(core.state.mental_events) == 1


def test_workspace_is_exposed_to_next_seed_generation_context(tmp_path) -> None:
    core = PrivateLifeCore(root_dir=tmp_path)
    core.ingest_seed_mental_event(_seed())
    digest = {"current": core.digest()}
    host = SimpleNamespace(
        private_life_digest=digest,
        private_life_surface_queue=[],
        private_mind_daemon_digest={},
        private_mind_surface_queue=[],
        rolling_internal_state=None,
        cortex_attention_state=None,
        internal_agenda_items=[],
        active_causal_threads=[],
    )

    context = build_autonomous_self_context(host).to_dict()
    assert context["private_life_workspace"]
    assert context["private_life_workspace"][0]["epistemic_mode"] == "speculation"


class _OneSeedGenerator:
    enabled = True

    def __init__(self) -> None:
        self.used = False

    def generate(self, request):
        if self.used:
            return RandomChatSeed()
        self.used = True
        return _seed("sink")


def test_reservoir_sends_valid_seed_to_mental_event_sink(monkeypatch) -> None:
    monkeypatch.setenv("ERIS_RANDOM_CHAT_SEED_RESERVOIR_ENABLED", "1")
    monkeypatch.setenv("ERIS_RANDOM_CHAT_SEED_TARGET_SIZE", "1")
    monkeypatch.setenv("ERIS_RANDOM_CHAT_SEED_GENERATE_INTERVAL_SEC", "1")
    seen: list[RandomChatSeed] = []
    reservoir = RandomChatSeedReservoir(_OneSeedGenerator(), mental_event_sink=seen.append)
    reservoir.update_context({
        "memory_context": "",
        "recent_public_history": [],
        "self_context": {},
        "relationship": {},
        "shared_semantic_context": {},
    })
    reservoir.start()
    try:
        deadline = time.time() + 3.0
        while time.time() < deadline and not seen:
            time.sleep(0.03)
        assert seen
        assert seen[0].event_kind == "speculation"
        assert seen[0].epistemic_mode == "speculation"
    finally:
        reservoir.shutdown()
