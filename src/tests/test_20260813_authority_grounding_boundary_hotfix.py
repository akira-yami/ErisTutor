from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
import time


def test_internal_sound_language_is_not_live_sensor_claim():
    from runtime.grounding_surface_gate import current_world_claim_without_evidence

    text = "Sức nặng của cái hả không đến từ sự im lặng trước đó hay từ chính âm thanh."
    assert current_world_claim_without_evidence(text, ()) is False
    assert current_world_claim_without_evidence(
        text, (), claim_scope="internal_interpretation"
    ) is False
    assert current_world_claim_without_evidence(
        "Tôi đang nghe âm thanh ngoài kia.", (), claim_scope="current_environment"
    ) is True


def test_public_topic_boundary_survives_restart_and_user_can_reopen(tmp_path, monkeypatch):
    monkeypatch.setenv("ERIS_MEMORY_CORE_DIR", str(tmp_path))
    from runtime.public_topic_boundary import PublicTopicBoundaryStore

    now = time.time()
    first = PublicTopicBoundaryStore(path=tmp_path / "public_topic_boundaries.json")
    boundary = first.close_topic(
        "coi như vụ hả này đi vào dĩ vãng nha",
        context_text="Mình đang nói về tiếng hả hồi nãy.",
        now=now,
        ttl_seconds=10000.0,
    )
    assert boundary is not None
    assert "ha" in boundary.topic_terms

    restarted = PublicTopicBoundaryStore(path=tmp_path / "public_topic_boundaries.json")
    assert restarted.is_blocked("Tôi lại nghĩ về tiếng hả hồi nãy.", now=now + 1.0) is True
    assert restarted.is_blocked("Tôi muốn nói về chess openings.", now=now + 1.0) is False

    reopened = restarted.note_user_turn("vụ hả hồi nãy thực ra tôi hỏi lại nhé", now=now + 2.0)
    assert reopened

    restarted_again = PublicTopicBoundaryStore(path=tmp_path / "public_topic_boundaries.json")
    assert restarted_again.is_blocked("Tôi lại nghĩ về tiếng hả hồi nãy.", now=now + 3.0) is False


def test_durable_topic_boundary_blocks_mental_material_public_projection(tmp_path, monkeypatch):
    monkeypatch.setenv("ERIS_MEMORY_CORE_DIR", str(tmp_path))
    from runtime.mental_material_runtime import MentalMaterialRuntime
    from runtime.public_topic_boundary import ensure_public_topic_boundary_store
    from runtime.ai_orchestrated.random_chat_seed import RandomChatSeed

    now = time.time()
    host = SimpleNamespace(logger=None)
    store = ensure_public_topic_boundary_store(host)
    store.close_topic("cho vụ hả này qua nhé", now=now, ttl_seconds=10000.0)

    rt = MentalMaterialRuntime(host)
    seed = RandomChatSeed.from_material_mapping(
        {
            "decision": "seed",
            "seed_id": "material.hah.persisted",
            "material_kind": "opinion",
            "premise": "tiếp tục nghĩ về tiếng hả theo một cách đủ cụ thể để material vẫn tồn tại private",
            "topic_tags": ["hah"],
            "epistemic_mode": "opinion",
            "confidence": 0.9,
        },
        causal_motive={
            "cause_id": "concern.hah",
            "cause_kind": "active_concern",
            "motive_kind": "continue_concern",
            "strength": 0.9,
            "source_refs": ["concern.hah"],
            "legacy_motive_kind": "want_to_revisit",
        },
        requested_mode="eris_led",
    )
    assert rt.ingest_seed(seed, now=now + 1.0) is not None
    assert rt.snapshot(now=now + 2.0)  # private cognition still has the material
    assert rt.wake_pressure(now=now + 2.0) == 0.0
    assert rt.project(now=now + 2.0) == ((), ())


def test_surface_realization_failure_retries_once_then_retires_public_attempt():
    from runtime.expression_urge_runtime import ExpressionUrgeRuntime

    host = SimpleNamespace(logger=None)
    rt = ExpressionUrgeRuntime(host)
    urge = rt._ingest_content(
        input_key="material:1",
        content="một suy nghĩ nội tại đủ dài để thử hiện thực hóa public nhưng không phải mất khỏi cognition",
        motive="share",
        source="mental_material",
        evidence_refs=("mental:1",),
        now=100.0,
        base_strength=0.9,
        freshness=0.95,
        source_material_id="mental:1",
    )
    assert urge is not None

    assert rt.note_realization_failure(
        urge.urge_id, reason="surface_current_world_claim_without_evidence", now=101.0
    ) is True
    assert urge.surface_blocked_until > 101.0
    assert urge.satisfaction < 0.98
    assert rt.wake_pressure(now=102.0) == 0.0

    assert rt.note_realization_failure(
        urge.urge_id, reason="surface_current_world_claim_without_evidence", now=112.0
    ) is True
    assert urge.satisfaction >= 0.98
    assert urge.urge_id not in rt._urges  # only the public projection is retired


def test_transient_surface_failure_uses_backoff_not_semantic_retirement():
    from runtime.expression_urge_runtime import ExpressionUrgeRuntime

    rt = ExpressionUrgeRuntime(SimpleNamespace(logger=None))
    urge = rt._ingest_content(
        input_key="material:2",
        content="một nội dung khác đủ dài để provider có thể tạm thời thất bại nhưng được retry sau",
        motive="share",
        source="mental_material",
        evidence_refs=("mental:2",),
        now=100.0,
        base_strength=0.9,
        freshness=0.95,
    )
    assert urge is not None
    rt.note_realization_failure(urge.urge_id, reason="surface_provider_timeout", now=101.0)
    rt.note_realization_failure(urge.urge_id, reason="surface_provider_timeout", now=106.0)
    assert urge.urge_id in rt._urges
    assert urge.satisfaction < 0.98
    assert urge.surface_blocked_until > 106.0


def test_maintenance_preview_is_idempotent_during_ui_burst(tmp_path):
    from runtime.maintenance_service import MaintenanceService

    memory_core = SimpleNamespace(base_dir=str(tmp_path))
    owner = SimpleNamespace(memory_core=memory_core)
    service = MaintenanceService(owner)
    options = {"components": ["refresh_conversation", "reset_private_mind"]}

    first = service.preview("factory_reset", options=options)
    second = service.preview("factory_reset", options=options)
    assert first["confirmation_token"] == second["confirmation_token"]
    assert first["preview_id"] == second["preview_id"]


def test_danger_zone_ui_has_client_side_preview_coalescing():
    source = Path("body/UdpListener.SettingsDanger.cs").read_text(encoding="utf-8")
    assert "_dangerPreviewInFlight" in source
    assert "DangerPreviewDebounceMs" in source
    assert "Đang dùng lại preview còn hiệu lực từ Brain" in source
