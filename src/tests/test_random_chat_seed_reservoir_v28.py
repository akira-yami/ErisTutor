from __future__ import annotations

import time
from types import SimpleNamespace

from llm.deepseek_workers import load_worker_spec, normalize_worker_role
from runtime.ai_orchestrated.random_chat_seed import RandomChatSeed, RandomChatSeedAffinityStore
from runtime.ai_orchestrated.random_chat_seed_service import (
    RandomChatSeedGenerator,
    RandomChatSeedReservoir,
)


def _payload(seed_id: str, *, mode: str = "eris_led", source_kind: str = "eris_self_memory") -> dict:
    premises = {
        "self": "ERIS wants to share that natural conversation systems become amusingly elaborate inside.",
        "shared": "The user and ERIS keep turning tiny dialogue bugs into shared architecture jokes.",
        "scene": "The recent choice to isolate a worker port creates a neat new conversational angle.",
        "lease": "ERIS has a mildly daring opinion about keeping autonomous thoughts ready before speaking.",
        "one": "ERIS finds the separation between semantic seeds and public wording personally satisfying.",
    }
    return {
        "decision": "seed",
        "seed_id": seed_id,
        "mode": mode,
        "premise": premises.get(seed_id, f"ERIS has a concrete thought about {seed_id} worth sharing."),
        "source_kind": source_kind,
        "source_refs": [f"self:{seed_id}"],
        "topic_tags": [f"architecture_{seed_id}"],
        "opening_move": "share_opinion",
        "desired_response": ["optional_reaction"],
        "eris_motive": {
            "kind": "want_to_share",
            "why_now": "The idea remains salient.",
            "personal_stake": 0.72,
            "authenticity": 0.84,
        },
        "risk": {
            "daring": 0.42,
            "uncertainty": 0.34,
            "intrusion_risk": 0.05,
            "face_risk": 0.18,
            "recovery_available": True,
        },
        "estimated_fit": 0.68,
        "confidence": 0.78,
        "tone": "dry_warm",
        "ttl_seconds": 600,
    }


def _snapshot() -> dict:
    return {
        "memory_context": "User likes ERIS architecture discussions and dry technical teasing.",
        "recent_public_history": [
            {"role": "user", "text": "Seed chạy nền đi."},
            {"role": "assistant", "text": "Tách khỏi Surface."},
        ],
        "self_context": {
            "public_fragments": ["ERIS wants to share an architecture observation."],
            "has_public_seed": True,
        },
        "personality_summary": "Warm and dryly playful.",
        "identity_summary": "ERIS values self-expression.",
        "relationship": {"familiarity": 0.7},
        "shared_semantic_context": {},
        "commentary_expectation": {"due": False},
    }


def test_seed_role_has_own_default_port(monkeypatch) -> None:
    monkeypatch.setenv("ERIS_DS2API_ENABLED", "1")
    monkeypatch.setenv("ERIS_DS2API_API_KEY", "shared-key")
    monkeypatch.setenv("ERIS_DS_BASE_URL", "http://127.0.0.1:6011/v1")
    monkeypatch.delenv("ERIS_DS_SEED_BASE_URL", raising=False)

    spec = load_worker_spec("seed")

    assert normalize_worker_role("random_chat_seed") == "seed"
    assert spec.base_url == "http://127.0.0.1:6015/v1"
    assert spec.api_key == "shared-key"
    assert spec.jsonl_enabled is True


def test_generator_owns_prompt_but_uses_common_structured_invoker() -> None:
    calls: list[dict] = []
    client = SimpleNamespace(worker_role="seed", base_url="http://127.0.0.1:6015/v1")

    def invoke(used_client, **kwargs):
        calls.append({"client": used_client, **kwargs})
        return _payload("one")

    generator = RandomChatSeedGenerator(client, invoke_structured=invoke)
    seed = generator.generate({"user_memory": "architecture", "reservoir_context": {"requested_mode": "eris_led"}})

    assert seed.available is True
    assert calls[0]["client"] is client
    assert calls[0]["name"] == "eris_random_chat_seed_background"
    assert calls[0]["inference_tier"] == "subconscious_4b"
    assert "không viết câu public" in calls[0]["system"].lower()


class _SequenceGenerator:
    enabled = True

    def __init__(self, payloads: list[dict]) -> None:
        self.payloads = list(payloads)
        self.requests: list[dict] = []

    def generate(self, request):
        self.requests.append(dict(request))
        if not self.payloads:
            return RandomChatSeed()
        return RandomChatSeed.from_mapping(self.payloads.pop(0))


def _wait_until(predicate, timeout: float = 4.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return False


def test_background_reservoir_accumulates_then_claim_is_model_free(monkeypatch) -> None:
    monkeypatch.setenv("ERIS_RANDOM_CHAT_SEED_RESERVOIR_ENABLED", "1")
    monkeypatch.setenv("ERIS_RANDOM_CHAT_SEED_TARGET_SIZE", "2")
    monkeypatch.setenv("ERIS_RANDOM_CHAT_SEED_MAX_SIZE", "3")
    monkeypatch.setenv("ERIS_RANDOM_CHAT_SEED_GENERATE_INTERVAL_SEC", "1")
    monkeypatch.setenv("ERIS_RANDOM_CHAT_SEED_NONE_BACKOFF_SEC", "2")
    generator = _SequenceGenerator([
        _payload("self", mode="eris_led", source_kind="eris_self_memory"),
        _payload("shared", mode="shared", source_kind="recent_shared_context"),
    ])
    reservoir = RandomChatSeedReservoir(
        generator,
        seed_store=RandomChatSeedAffinityStore(),
    )
    reservoir.update_context(_snapshot())
    reservoir.start()
    try:
        assert _wait_until(lambda: reservoir.snapshot()["ready"] == 2)
        generated_calls = len(generator.requests)

        first = reservoir.claim()
        second = reservoir.claim()

        assert first is not None and second is not None
        assert len(generator.requests) == generated_calls
        assert {first.seed.mode, second.seed.mode} == {"eris_led", "shared"}
        assert first.token != second.token
    finally:
        reservoir.shutdown()


def test_user_turn_invalidates_scene_seed_but_keeps_eris_self_seed(monkeypatch) -> None:
    monkeypatch.setenv("ERIS_RANDOM_CHAT_SEED_RESERVOIR_ENABLED", "1")
    monkeypatch.setenv("ERIS_RANDOM_CHAT_SEED_TARGET_SIZE", "2")
    monkeypatch.setenv("ERIS_RANDOM_CHAT_SEED_GENERATE_INTERVAL_SEC", "1")
    generator = _SequenceGenerator([
        _payload("scene", mode="shared", source_kind="recent_shared_context"),
        _payload("self", mode="eris_led", source_kind="eris_self_memory"),
    ])
    reservoir = RandomChatSeedReservoir(generator, seed_store=RandomChatSeedAffinityStore())
    reservoir.update_context(_snapshot())
    reservoir.start()
    try:
        assert _wait_until(lambda: reservoir.snapshot()["ready"] == 2)
        reservoir.observe_user_turn()
        snap = reservoir.snapshot()

        assert snap["ready"] == 1
        assert snap["seeds"][0]["source_kind"] == "eris_self_memory"
    finally:
        reservoir.shutdown()



def test_dynamic_cadence_counters_do_not_invalidate_reservoir_context(monkeypatch) -> None:
    monkeypatch.setenv("ERIS_RANDOM_CHAT_SEED_RESERVOIR_ENABLED", "1")
    reservoir = RandomChatSeedReservoir(_SequenceGenerator([]))
    first = _snapshot()
    first["commentary_expectation"] = {
        "enabled": True,
        "due": False,
        "reason": "cadence_not_due",
        "seconds_since_commentary": 12.0,
        "seconds_since_user": 20.0,
    }
    second = _snapshot()
    second["commentary_expectation"] = {
        "enabled": True,
        "due": False,
        "reason": "cadence_not_due",
        "seconds_since_commentary": 18.0,
        "seconds_since_user": 26.0,
    }

    assert reservoir.update_context(first) is True
    assert reservoir.update_context(second) is False

def test_release_returns_seed_and_commit_consumes_it(monkeypatch) -> None:
    monkeypatch.setenv("ERIS_RANDOM_CHAT_SEED_RESERVOIR_ENABLED", "1")
    monkeypatch.setenv("ERIS_RANDOM_CHAT_SEED_TARGET_SIZE", "1")
    monkeypatch.setenv("ERIS_RANDOM_CHAT_SEED_GENERATE_INTERVAL_SEC", "1")
    generator = _SequenceGenerator([_payload("lease")])
    reservoir = RandomChatSeedReservoir(generator, seed_store=RandomChatSeedAffinityStore())
    reservoir.update_context(_snapshot())
    reservoir.start()
    try:
        assert _wait_until(lambda: reservoir.snapshot()["ready"] == 1)
        lease = reservoir.claim()
        assert lease is not None
        assert reservoir.snapshot()["ready"] == 0
        assert reservoir.release(lease.token, reason="governor_cooldown") is True
        assert reservoir.snapshot()["ready"] == 1

        lease2 = reservoir.claim()
        assert lease2 is not None
        assert reservoir.commit(lease2.token) is True
        assert reservoir.snapshot()["ready"] == 0
    finally:
        reservoir.shutdown()
