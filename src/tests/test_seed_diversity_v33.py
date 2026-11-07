from __future__ import annotations

from runtime.ai_orchestrated.random_chat_seed import (
    RandomChatSeed,
    assess_seed_quality,
    seed_event_family,
)
from runtime.ai_orchestrated.random_chat_seed_service import (
    RandomChatSeedGenerator,
    RandomChatSeedReservoir,
    _SeedEntry,
)


def _seed(kind: str, **overrides) -> RandomChatSeed:
    payload = dict(
        decision="seed",
        seed_id=f"seed_{kind}",
        mode="shared",
        premise="Một mental event đủ cụ thể để test contract.",
        source_kind="general_knowledge",
        source_refs=(),
        topic_tags=("autonomy", "silence"),
        event_kind=kind,
        epistemic_mode="opinion",
        open_loop=False,
        opening_move="share_observation",
        desired_response=("optional_reaction",),
        eris_motive_kind="want_to_share",
        eris_motive_why_now="Có liên hệ trực tiếp với context vừa xảy ra.",
        personal_stake=0.46,
        authenticity=0.70,
        daring=0.20,
        uncertainty=0.30,
        intrusion_risk=0.12,
        face_risk=0.06,
        recovery_available=True,
        estimated_fit=0.68,
        confidence=0.74,
        tone="casual_warm",
        ttl_seconds=600.0,
    )
    payload.update(overrides)
    return RandomChatSeed(**payload)


def test_compact_curiosity_question_is_not_penalized_for_being_short():
    seed = _seed(
        "curiosity_question",
        premise="Tại sao im lặng lại khó?",
        epistemic_mode="question",
        open_loop=True,
        eris_motive_kind="want_to_know",
        eris_motive_why_now="",
        personal_stake=0.30,
        desired_response=("optional_answer",),
        opening_move="ask_lightly",
    )
    quality = assess_seed_quality(seed)
    assert quality.score > 0.70
    assert "thin_premise" not in quality.reasons


def test_compact_playful_probe_is_first_class_seed_not_reflection_prose():
    seed = _seed(
        "playful_probe",
        premise="Thử đoán một cái nhé?",
        eris_motive_kind="want_to_play",
        eris_motive_why_now="",
        personal_stake=0.24,
        desired_response=("optional_guess",),
        opening_move="offer_micro_game",
    )
    quality = assess_seed_quality(seed)
    assert quality.score > 0.70
    assert seed_event_family(seed.event_kind) == "play"


def test_memory_callback_requires_provenance_instead_of_more_words():
    seed = _seed(
        "memory_callback",
        premise=(
            "Một đoạn hồi tưởng rất dài vẫn không trở thành memory callback hợp lệ chỉ vì nó có nhiều chữ "
            "và nghe có vẻ sâu sắc nếu không biết nó đang callback cái gì."
        ),
        source_kind="eris_self_memory",
        source_refs=(),
        eris_motive_kind="want_to_revisit",
    )
    quality = assess_seed_quality(seed)
    assert quality.score < 0.46
    assert "memory_callback_without_ref" in quality.reasons


def test_event_family_mapping_covers_all_seed_kinds_including_previously_unrequested_ones():
    kinds = {
        "thought", "curiosity_question", "knowledge_gap", "discovery_impulse",
        "speculation", "imagination", "opinion", "association", "memory_callback",
        "playful_probe", "micro_challenge", "unfinished_loop", "creative_impulse",
        "self_reflection", "prediction", "surprise", "contradiction_notice",
    }
    families = {seed_event_family(kind) for kind in kinds}
    assert families == {
        "cognitive", "curiosity", "associative", "play", "creative", "uncertain_world", "self"
    }
    assert seed_event_family("prediction") == "uncertain_world"
    assert seed_event_family("memory_callback") == "associative"
    assert seed_event_family("contradiction_notice") == "cognitive"


def test_reservoir_requests_distinct_event_families_before_repeating_reflective_lane():
    generator = RandomChatSeedGenerator(object(), invoke_structured=lambda *_a, **_k: {})
    reservoir = RandomChatSeedReservoir(generator)
    reservoir.target_size = 7

    requested = []
    for index in range(7):
        kind = reservoir._requested_event_kind_locked()
        requested.append(kind)
        reservoir._entries.append(
            _SeedEntry(
                seed=_seed(kind, seed_id=f"seed_{index}"),
                generated_at=0.0,
                expires_at=10_000.0,
                context_revision=0,
                user_epoch=0,
                context_fingerprint="",
            )
        )

    assert [seed_event_family(kind) for kind in requested] == [
        "curiosity", "cognitive", "associative", "play", "creative", "uncertain_world", "self"
    ]


def test_second_diversity_cycle_reaches_kinds_v32_flat_mix_never_requested():
    generator = RandomChatSeedGenerator(object(), invoke_structured=lambda *_a, **_k: {})
    reservoir = RandomChatSeedReservoir(generator)
    reservoir.target_size = 14

    requested = []
    for index in range(14):
        kind = reservoir._requested_event_kind_locked()
        requested.append(kind)
        reservoir._entries.append(
            _SeedEntry(
                seed=_seed(kind, seed_id=f"seed_{index}"),
                generated_at=0.0,
                expires_at=10_000.0,
                context_revision=0,
                user_epoch=0,
                context_fingerprint="",
            )
        )

    assert "memory_callback" in requested
    assert "prediction" in requested
    assert "contradiction_notice" in requested
    assert len(set(requested)) >= 13


def test_generation_contract_explicitly_says_quality_is_not_length_shaped():
    generator = RandomChatSeedGenerator(object(), invoke_structured=lambda *_a, **_k: {})
    reservoir = RandomChatSeedReservoir(generator, clock=lambda: 1_786_425_600.0)
    reservoir._context_request = {"user_memory": "", "recent_public_history": []}
    request = reservoir._generation_request_locked()
    assert request["reservoir_context"]["requested_event_family"]
    assert request["seed_contract"]["kind_contract"]
    assert "not length-shaped" in request["seed_contract"]["quality_rule"]
