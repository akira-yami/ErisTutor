from types import SimpleNamespace

from cortex.active_concern import match_concern, private_concern_view
from cortex.private_mind.concern_store import ConcernStore
from cortex.private_mind.contracts import PrivateConcern, ThoughtFragment
from cortex.contracts import ThoughtObject, ThoughtStreamReport
from cortex.internal_agenda import InternalAgendaItem, InternalAgendaStack
from runtime.active_concern_runtime import ActiveConcernRuntime
from runtime.subject_proposals import AutonomousProposalSource


def _candidate(**overrides):
    data = dict(
        kind="random_chat",
        topic="autonomy cần biết kiềm chế để không làm phiền",
        source="random_chat_seed",
        confidence=0.82,
        relevance=0.78,
        urgency=0.20,
        requires_grounding=False,
        seed_intrusion_risk=0.18,
        intrusion_risk=0.18,
        seed_quality_score=0.88,
        seed_specificity=0.80,
        seed_motive_strength=0.70,
        seed_semantic_completeness=0.86,
        seed_id="seed.v40.autonomy",
        seed_epistemic_mode="opinion",
        seed_temporal_context={},
        evidence_refs=("turn:1",),
        speech_act="share_self_originated_thought",
        tone="natural",
        uncertainty=0.28,
        source_kind="recent_shared_context",
        seed_event_kind="thought",
    )
    data.update(overrides)
    return SimpleNamespace(**data)


def _affordance(candidate=None):
    return SimpleNamespace(
        affordance_id="aff:v40:1",
        owner="random_chat_seed",
        candidate=candidate or _candidate(),
        metadata={},
    )


def test_private_concern_recurrence_is_not_mutation_without_new_evidence():
    concern = PrivateConcern(
        id="c1", theme="autonomy và restraint", evidence_ids=("e1",),
        pressure=0.6, uncertainty=0.4, metadata={"mutation_count": 0},
    )
    concern.merge_fragment(ThoughtFragment(
        id="f1", source="test", claim="autonomy và restraint",
        confidence=0.7, pressure=0.6, evidence_ids=("e1",),
    ))
    assert concern.recurrence == 2
    assert concern.metadata["reactivation_count"] == 1
    assert concern.metadata.get("mutation_count", 0) == 0
    assert concern.metadata["echo_streak"] == 1


def test_new_evidence_counts_as_mutation_and_resets_echo():
    concern = PrivateConcern(
        id="c1", theme="autonomy và restraint", evidence_ids=("e1",),
        pressure=0.6, uncertainty=0.4, metadata={"mutation_count": 0, "echo_streak": 3},
    )
    concern.merge_fragment(ThoughtFragment(
        id="f2", source="test", claim="autonomy và restraint",
        confidence=0.8, pressure=0.7, evidence_ids=("e2",),
    ))
    assert concern.metadata["mutation_count"] == 1
    assert concern.metadata["echo_streak"] == 0
    assert "e2" in concern.evidence_ids


def test_surface_habituation_reduces_speech_pressure_without_erasing_concern():
    import time
    now = time.time()
    base = PrivateConcern(
        id="c1", theme="autonomy và restraint", evidence_ids=("e1", "e2"),
        pressure=0.72, uncertainty=0.35, recurrence=4,
        created_at=now - 1200, updated_at=now - 60,
        metadata={"mutation_count": 0, "last_mutation_at": now - 7200, "surfaced_count": 0},
    )
    fresh = private_concern_view(base, now=now)
    base.metadata["surfaced_count"] = 4
    base.metadata["last_surfaced_at"] = now - 30
    repeated = private_concern_view(base, now=now)
    assert repeated.activation > 0.4
    assert repeated.habituation > fresh.habituation
    assert repeated.speech_pressure < fresh.speech_pressure
    assert repeated.state == "habituated"



def test_scene_interruption_does_not_weaken_intrinsic_concern_speech_pressure():
    import time
    now = time.time()
    concern = PrivateConcern(
        id="c-self-owned", theme="một ý Eris thực sự muốn nói", evidence_ids=("e1",),
        pressure=0.72, uncertainty=0.35, recurrence=2,
        created_at=now - 300, updated_at=now - 20,
        metadata={"mutation_count": 1, "last_mutation_at": now - 10, "surfaced_count": 0},
    )
    open_scene = private_concern_view(concern, now=now, interruption_cost=0.0)
    busy_scene = private_concern_view(concern, now=now, interruption_cost=1.0)
    assert busy_scene.interruption_cost == 1.0  # telemetry vẫn còn cho wording
    assert busy_scene.speech_pressure == open_scene.speech_pressure

def test_matching_concern_enriches_proposal_before_neuro():
    concerns = ({
        "concern_id": "concern:autonomy",
        "subject": "autonomy cần restraint để không làm phiền",
        "activation": 0.72,
        "novelty": 0.64,
        "habituation": 0.52,
        "speech_pressure": 0.38,
        "age_seconds": 400,
        "mutation_count": 2,
        "state": "simmering",
        "trajectory": "stable",
    },)
    proposal = AutonomousProposalSource.from_affordance(
        _affordance(), turn_id="autonomous:1", active_concerns=concerns,
    )
    assert proposal is not None
    assert proposal["arguments"]["active_concern_id"] == "concern:autonomy"
    assert proposal["arguments"]["concern_habituation"] == 0.52
    assert "continuity" in proposal["supporting_need_ids"]
    # Habituation được đưa vào cost/conflict trước Neuro, không đợi downstream chặn.
    assert proposal["substrate_cost"] > 0.12
    assert proposal["commitment_conflict"] > 0.0


def test_unrelated_concern_does_not_pollute_candidate():
    proposal = AutonomousProposalSource.from_affordance(
        _affordance(),
        turn_id="autonomous:1",
        active_concerns=({
            "concern_id": "c-weather",
            "subject": "thời tiết ngày mai ở biển",
            "activation": 0.9,
            "speech_pressure": 0.9,
        },),
    )
    assert proposal is not None
    assert "active_concern_id" not in proposal["arguments"]


def test_seed_can_enter_concern_without_public_speech():
    store = ConcernStore()
    host = SimpleNamespace(
        private_mind_daemon=SimpleNamespace(concern_store=store),
        last_cortex_attention=SimpleNamespace(interrupt_cost=0.25),
        logger=None,
    )
    import time
    current = time.time()
    runtime = ActiveConcernRuntime(host)
    created = runtime.absorb_affordances((_affordance(),), now=current)
    assert created
    snapshot = runtime.snapshot(now=current + 1.0)
    assert snapshot
    assert snapshot[0]["surfaced_count"] == 0
    assert snapshot[0]["state"] in {"simmering", "reactivated", "watching"}


def test_public_surface_creates_habituation_receipt_not_resolution():
    store = ConcernStore()
    host = SimpleNamespace(
        private_mind_daemon=SimpleNamespace(concern_store=store),
        last_cortex_attention=SimpleNamespace(interrupt_cost=0.0),
        logger=None,
    )
    import time
    current = time.time()
    runtime = ActiveConcernRuntime(host)
    concern_id = runtime.absorb_affordances((_affordance(),), now=current)[0]
    before = store.get(concern_id)
    before_pressure = before.pressure
    assert runtime.note_public_surface(concern_id, now=current + 10.0, evidence_ref="plan:1")
    after = store.get(concern_id)
    assert after.status == "open"
    assert after.metadata["surfaced_count"] == 1
    assert after.pressure < before_pressure
    assert "plan:1" in after.evidence_ids


def test_match_concern_prefers_semantic_continuity_not_activation_alone():
    matched, score = match_concern(
        "tôi vẫn nghĩ autonomy cần restraint",
        (
            {"concern_id": "wrong", "subject": "âm nhạc và guitar", "activation": 1.0},
            {"concern_id": "right", "subject": "autonomy và restraint", "activation": 0.55},
        ),
    )
    assert matched is not None
    assert matched["concern_id"] == "right"
    assert score > 0.3


def test_projection_reads_thought_stream_and_agenda_without_second_store():
    import time
    now = time.time()
    store = ConcernStore()
    agenda = InternalAgendaStack()
    agenda.add(
        kind="open_question", summary="vì sao autonomy dễ thành intrusion",
        evidence_ids=("turn:9",), pressure=0.66, urgency=0.30, confidence=0.72, speech_value=0.62,
    )
    thought = ThoughtObject(
        thought_id="t40", mode="association",
        topic_seed="autonomy và intrusion", partial_text="autonomy dễ trượt thành intrusion nếu không có inhibition",
        unresolved_reason="chưa biết ngưỡng nào hợp", tension=0.68, novelty=0.70, persistence=0.72,
        recurrence_count=2, surfaced_count=0, evidence=("turn:9",),
        created_at=now - 300, updated_at=now - 20, last_reactivated_at=now - 20,
    )
    stream = SimpleNamespace(latest_report=lambda: ThoughtStreamReport(recent_thoughts=(thought,)))
    host = SimpleNamespace(
        private_mind_daemon=SimpleNamespace(concern_store=store),
        thought_stream=stream, internal_agenda=agenda,
        last_cortex_attention=SimpleNamespace(interrupt_cost=0.1), logger=None,
    )
    runtime = ActiveConcernRuntime(host)
    snapshot = runtime.snapshot(now=now)
    assert snapshot
    sources = {src for item in snapshot for src in item.get("source_kinds", ())}
    assert "thought_stream" in sources
    assert any(str(src).startswith("agenda:") for src in sources)
    assert len(store.all()) == 0  # projection không copy thought/agenda vào ConcernStore


def test_seed_paraphrases_merge_into_one_private_concern():
    import time
    now = time.time()
    store = ConcernStore()
    host = SimpleNamespace(
        private_mind_daemon=SimpleNamespace(concern_store=store),
        last_cortex_attention=SimpleNamespace(interrupt_cost=0.0), logger=None,
    )
    runtime = ActiveConcernRuntime(host)
    first = _affordance(_candidate(seed_id="seed.a", topic="autonomy cần restraint để không làm phiền"))
    second = _affordance(_candidate(seed_id="seed.b", topic="restraint giúp autonomy không biến thành làm phiền"))
    runtime.absorb_affordances((first,), now=now)
    runtime.absorb_affordances((second,), now=now + 2)
    opened = store.open_concerns(limit=10)
    assert len(opened) == 1
    assert opened[0].recurrence >= 2
    assert "seed.a" in opened[0].evidence_ids
    assert "seed.b" in opened[0].evidence_ids
