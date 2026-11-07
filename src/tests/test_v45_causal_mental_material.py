from __future__ import annotations

from types import SimpleNamespace

from cortex.mental_material import derive_causal_motives
from runtime.ai_orchestrated.random_chat_seed import RandomChatSeed, assess_seed_quality
from runtime.ai_orchestrated.random_chat_seed_service import (
    RANDOM_CHAT_SEED_OUTPUT_SCHEMA,
    RandomChatSeedGenerator,
    RandomChatSeedReservoir,
)
from runtime.mental_material_runtime import MentalMaterialRuntime


def _motive(**overrides):
    payload = {
        "cause_id": "user_event:turn-7",
        "cause_kind": "user_event",
        "motive_kind": "engage_user_event",
        "semantic_focus": "anh vừa nâng cấp em và đang xem autonomous có khác không",
        "why_now": "new_or_recent_user_event:user_event:turn-7",
        "strength": 0.82,
        "source_refs": ["turn-7"],
        "allowed_material_kinds": ["reaction", "opinion", "association", "question", "small_contact"],
        "generation_budget": 2,
        "relationship_modulation": 0.4,
        "legacy_motive_kind": "want_to_explore",
    }
    payload.update(overrides)
    return payload


def _material_payload(kind="reaction", **overrides):
    payload = {
        "decision": "seed",
        "seed_id": "mat-1",
        "material_kind": kind,
        "premise": "Anh vừa sửa em xong rồi lại ngồi chờ xem em hỏng ở đâu; cảnh này hơi buồn cười.",
        "topic_tags": ["upgrade", "autonomous", "bug"],
        "epistemic_mode": "opinion" if kind != "question" else "question",
        "open_loop": kind == "question",
        "expected_update": ["relationship.autonomy_tolerance"] if kind == "question" else [],
        "uncertainty": 0.22,
        "confidence": 0.81,
        "ttl_seconds": 420,
    }
    payload.update(overrides)
    return payload


def test_v45_motive_exists_before_material_generation():
    motives = derive_causal_motives({
        "recent_user_events": [{"role": "user", "text": "tôi vừa nâng cấp em", "event_ref": "turn-7"}],
        "relationship": {"connection_pressure": 0.6},
        "eris_self_memory": {},
        "user_memory": "",
    })
    assert motives
    top = motives[0]
    assert top.cause_id == "user_event:turn-7"
    assert top.motive_kind == "engage_user_event"
    assert "reaction" in top.allowed_material_kinds
    assert top.semantic_focus == "tôi vừa nâng cấp em"


def test_v45_relationship_pressure_alone_does_not_invent_topic():
    motives = derive_causal_motives({
        "recent_user_events": [],
        "relationship": {"connection_pressure": 1.0, "outreach_drive": 1.0},
        "eris_self_memory": {},
        "user_memory": "",
    })
    assert motives == ()


def test_v45_schema_has_no_model_owned_motive_or_conversation_plan():
    props = RANDOM_CHAT_SEED_OUTPUT_SCHEMA["properties"]
    assert "eris_motive" not in props
    assert "opening_move" not in props
    assert "desired_response" not in props
    assert "material_kind" in props
    assert "expected_update" in props


def test_v45_generator_uses_runtime_motive_and_ignores_public_plan_fields():
    calls = []

    def invoke(client, **kwargs):
        calls.append(kwargs)
        # Extra keys would be rejected by the real schema; direct unit call also
        # proves local carrier never needs them.
        return _material_payload("reaction")

    generator = RandomChatSeedGenerator(object(), invoke_structured=invoke)
    seed = generator.generate({
        "causal_motive": _motive(),
        "reservoir_context": {"requested_mode": "eris_led", "requested_material_kind": "reaction"},
    })
    assert seed.available
    assert seed.material_only is True
    assert seed.material_kind == "reaction"
    assert seed.cause_id == "user_event:turn-7"
    assert seed.runtime_motive_kind == "engage_user_event"
    assert seed.eris_motive_why_now.startswith("new_or_recent_user_event")
    assert seed.opening_move == ""
    assert seed.desired_response == ()


def test_v45_question_without_expected_update_is_less_useful_than_real_gap():
    weak = RandomChatSeed.from_material_mapping(
        _material_payload("question", expected_update=[]), causal_motive=_motive(), requested_mode="eris_led"
    )
    strong = RandomChatSeed.from_material_mapping(
        _material_payload("question", seed_id="mat-2"), causal_motive=_motive(), requested_mode="eris_led"
    )
    weak_q = assess_seed_quality(weak)
    strong_q = assess_seed_quality(strong)
    assert strong_q.score > weak_q.score
    assert "question_without_expected_update" in weak_q.reasons


def test_v45_reservoir_generation_is_cause_budgeted_not_inventory_budgeted():
    generator = RandomChatSeedGenerator(object(), invoke_structured=lambda *_a, **_k: {})
    reservoir = RandomChatSeedReservoir(generator, clock=lambda: 1000.0)
    reservoir._context_request = {
        "recent_user_events": [{"role": "user", "text": "test autonomous đi", "event_ref": "turn-1"}],
        "relationship": {},
        "eris_self_memory": {},
        "user_memory": "",
    }
    motives = reservoir._eligible_causal_motives_locked()
    assert len(motives) == 1
    cause = motives[0]
    reservoir._cause_generation_counts[cause.cause_id] = cause.generation_budget
    assert reservoir._eligible_causal_motives_locked() == ()


def test_v45_diversity_is_selected_inside_preexisting_cause():
    generator = RandomChatSeedGenerator(object(), invoke_structured=lambda *_a, **_k: {})
    reservoir = RandomChatSeedReservoir(generator, clock=lambda: 1000.0)
    reservoir._context_request = {
        "recent_user_events": [{"role": "user", "text": "test autonomous đi", "event_ref": "turn-1"}],
        "relationship": {},
        "eris_self_memory": {},
        "user_memory": "",
    }
    request = reservoir._generation_request_locked()
    assert request["causal_motive"]["cause_id"] == "user_event:turn-1"
    assert request["reservoir_context"]["requested_material_kind"] in request["seed_contract"]["allowed_material_kinds"]
    assert request["seed_contract"]["motive_owner"] == "runtime"


def test_v45_material_can_be_thought_without_becoming_expression():
    host = SimpleNamespace(logger=None)
    runtime = MentalMaterialRuntime(host)
    seed = RandomChatSeed.from_material_mapping(
        _material_payload(
            "observation",
            premise="Có một thay đổi nhỏ trong cách cuộc thử nghiệm này đang diễn ra.",
            confidence=0.42,
        ),
        causal_motive=_motive(strength=0.34),
        requested_mode="eris_led",
    )
    runtime.ingest_seed(seed, now=1000.0)
    concern, expression = runtime.project(now=1000.0)
    snap = runtime.snapshot(now=1000.0)[0]
    assert snap["state"] in {"thought", "concern"}
    assert expression == ()


def test_v45_reaction_can_become_expression_without_becoming_concern():
    host = SimpleNamespace(logger=None)
    runtime = MentalMaterialRuntime(host)
    seed = RandomChatSeed.from_material_mapping(
        _material_payload("reaction"), causal_motive=_motive(), requested_mode="eris_led"
    )
    runtime.ingest_seed(seed, now=1000.0)
    concern, expression = runtime.project(now=1000.0)
    assert expression
    assert expression[0].metadata["material_kind"] == "reaction"
    assert expression[0].candidate.speech_act == "share_reaction"
    assert concern == ()


def test_v45_real_question_preserves_question_shape_for_later_urge_layer():
    host = SimpleNamespace(logger=None)
    runtime = MentalMaterialRuntime(host)
    seed = RandomChatSeed.from_material_mapping(
        _material_payload("question"), causal_motive=_motive(), requested_mode="eris_led"
    )
    runtime.ingest_seed(seed, now=1000.0)
    _concern, expression = runtime.project(now=1000.0)
    assert expression
    assert expression[0].candidate.speech_act == "ask_genuine_question"
    assert expression[0].metadata["expected_update"] == ["relationship.autonomy_tolerance"]


def test_v45_same_cause_can_hold_different_material_kinds_with_same_tags():
    generator = RandomChatSeedGenerator(object(), invoke_structured=lambda *_a, **_k: {})
    reservoir = RandomChatSeedReservoir(generator, seed_store=None, clock=lambda: 1000.0)
    first = RandomChatSeed.from_material_mapping(
        _material_payload("reaction", seed_id="mat-a"), causal_motive=_motive(), requested_mode="eris_led"
    )
    second = RandomChatSeed.from_material_mapping(
        _material_payload(
            "opinion",
            seed_id="mat-b",
            premise="Tôi nghĩ anh đang quan sát hành vi của tôi nhiều hơn là chỉ săn lỗi code.",
        ),
        causal_motive=_motive(),
        requested_mode="eris_led",
    )
    reservoir._entries = [SimpleNamespace(seed=first)]
    allowed, reason = reservoir._validate_generated_locked(second, 1000.0)
    assert allowed, reason


def test_v45_public_expression_receipt_satisfies_source_material():
    """Public commit phải quay về material gốc, không chỉ làm urge nguội.

    Nếu receipt dừng ở ExpressionUrge thì MentalMaterial sẽ tưởng ý chưa từng
    được nói và có thể projection lại sau đó. Đây là một vòng replay rất kín,
    nên giữ regression ở đúng ownership boundary này.
    """
    from runtime.expression_urge_runtime import ExpressionUrgeRuntime

    host = SimpleNamespace(logger=None)
    material_runtime = MentalMaterialRuntime(host)
    host.mental_material_runtime = material_runtime
    urge_runtime = ExpressionUrgeRuntime(host, logger=None)

    seed = RandomChatSeed.from_material_mapping(
        _material_payload("reaction", seed_id="mat-receipt"),
        causal_motive=_motive(),
        requested_mode="eris_led",
    )
    material_runtime.ingest_seed(seed, now=1000.0)
    _concern, expression = material_runtime.project(now=1000.0)
    assert expression

    shaped = urge_runtime.shape_affordances(expression, now=1000.0)
    urge_affordances = [item for item in shaped if getattr(item, "owner", "") == "expression_urge"]
    assert urge_affordances
    urge_id = urge_affordances[0].metadata["urge_id"]

    before = material_runtime.snapshot(now=1000.0)[0]
    assert before["satisfaction"] == 0.0
    assert urge_runtime.note_expressed(urge_id, now=1001.0) is True
    after = material_runtime.snapshot(now=1001.0)[0]
    assert after["satisfaction"] >= 0.82
    assert after["freshness"] < before["freshness"]
