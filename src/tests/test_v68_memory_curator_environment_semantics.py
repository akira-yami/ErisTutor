from __future__ import annotations

import json

from cortex.subject.environment_meaning import EnvironmentMeaningDeriver, EnvironmentMeaningKind
from memory.living_user import LivingUserMemoryService, ProfileOperation, UserMemoryScope
from memory.living_user.curator import LivingUserMemoryCurator
from runtime.living_user_panel import build_memory_panel_snapshot


def _op(path: str, value, evidence: str, *, source: str = "profile_model", explicitness: str = "explicit", cardinality: str = "single"):
    return ProfileOperation(
        op="add",
        path=path,
        value=value,
        confidence=0.92,
        explicitness=explicitness,
        scope=UserMemoryScope(),
        cardinality=cardinality,
        evidence_ids=(evidence,),
        reason="test",
        source=source,
    )


def test_deepseek_curator_can_delete_user_authority_representation(tmp_path):
    service = LivingUserMemoryService(tmp_path)
    service.correct("communication.response_rules", "nói ngắn", evidence_ids=("turn_1",))
    record = service.profile_store.get("communication.response_rules")[0]

    def reviewer(system: str, user: str, max_tokens: int):
        assert "MAY restructure ANY record" in system
        return json.dumps({
            "operations": [
                {"action": "delete", "record_ids": [record.id], "reason": "redundant representation"}
            ]
        })

    report = LivingUserMemoryCurator(service, reviewer=reviewer).run()
    assert report.deleted == 1
    assert not service.profile_store.get("communication.response_rules")


def test_curator_canonicalize_cross_path_preserves_evidence(tmp_path):
    service = LivingUserMemoryService(tmp_path)
    service.profile_store.apply((
        _op("communication.response_rules", "prefer short, one-sentence replies when asked", "turn_1", source="behavior_rule", cardinality="multi"),
        _op("preferences.interaction_style", "tránh system-like, data-heavy presentation", "turn_2", cardinality="multi"),
    ))
    ids = [r.id for r in service.profile_store.all()]

    def reviewer(system: str, user: str, max_tokens: int):
        return json.dumps({
            "operations": [{
                "action": "canonicalize",
                "record_ids": ids,
                "path": "communication.response_style",
                "value": "Ưu tiên phản hồi tự nhiên, gọn khi phù hợp và tránh giọng máy móc nặng cấu trúc.",
                "cardinality": "single",
                "reason": "same durable communication concept",
            }]
        }, ensure_ascii=False)

    report = LivingUserMemoryCurator(service, reviewer=reviewer).run()
    rows = service.profile_store.get("communication.response_style")
    assert report.canonicalized == 1
    assert len(rows) == 1
    assert set(rows[0].evidence_ids) == {"turn_1", "turn_2"}
    assert rows[0].metadata.get("canonical_user_memory") is True


def test_open_loops_are_removed_from_profile_without_waiting_for_resolved_marker(tmp_path):
    service = LivingUserMemoryService(tmp_path)
    service.profile_store.apply((
        _op("goals.open_loops", "Người dùng chưa trả lời câu hỏi của Eris", "turn_1", cardinality="multi"),
    ))
    report = LivingUserMemoryCurator(service, reviewer=None).run()
    assert report.deleted == 1
    assert not service.profile_store.get("goals.open_loops")


def test_user_panel_never_dumps_upr_or_open_loop_garbage(tmp_path):
    service = LivingUserMemoryService(tmp_path)
    service.profile_store.apply((
        _op("communication.preferred_language", "vi", "turn_1", source="behavior_rule"),
        _op("communication.upr_9a13adfe990a1620c3", "raw parser debris", "turn_2"),
        _op("goals.open_loops", "Người dùng chưa trả lời", "turn_3", cardinality="multi"),
    ))
    snapshot = build_memory_panel_snapshot(service)
    paths = {row["path"] for row in snapshot["records"]}
    assert "communication.preferred_language" in paths
    assert not any("upr_" in path for path in paths)
    assert not any("open_loops" in path for path in paths)
    assert snapshot["schema_version"] == 4
    assert "memory_feed_trace" not in snapshot


def test_self_relevant_environment_semantic_key_is_stable_when_evidence_ref_changes():
    deriver = EnvironmentMeaningDeriver()
    base = {
        "phase": "implementation",
        "focused_object": "ERIS memory panel",
        "confidence": 0.9,
    }
    first = deriver.derive({
        **base,
        "recent_events": [{
            "kind": "focus_changed", "source": "uia", "detail": "ERIS memory panel",
            "confidence": 0.95, "attention_level": 3, "evidence_refs": ["uia:event:1"],
        }],
    })
    second = deriver.derive({
        **base,
        "recent_events": [{
            "kind": "focus_changed", "source": "uia", "detail": "ERIS memory panel",
            "confidence": 0.95, "attention_level": 3, "evidence_refs": ["uia:event:2"],
        }],
    })
    a = next(x for x in first if x.kind == EnvironmentMeaningKind.SELF_RELEVANT_INSPECTION)
    b = next(x for x in second if x.kind == EnvironmentMeaningKind.SELF_RELEVANT_INSPECTION)
    assert a.semantic_key == b.semantic_key
    assert a.revision != b.revision  # audit ref đổi, semantic identity thì không


def test_canonicalize_rejects_unlisted_single_target_without_mutation(tmp_path):
    service = LivingUserMemoryService(tmp_path)
    service.profile_store.apply((
        _op("communication.response_rules", "nói tự nhiên", "turn_a", cardinality="multi"),
        _op("communication.response_style", "gọn khi phù hợp", "turn_b"),
    ))
    source = service.profile_store.get("communication.response_rules")[0]
    before = service.profile_store.snapshot(include_sensitive=True)
    result = service.profile_store.apply_curator_operations(({
        "action": "canonicalize",
        "record_ids": [source.id],
        "path": "communication.response_style",
        "value": "Ưu tiên cách nói tự nhiên, gọn khi phù hợp.",
        "cardinality": "single",
        "reason": "collapse to one semantic home",
    },))
    assert not result.changed
    assert any(reason.startswith("canonicalize_target_conflict:") for reason in result.rejected)
    assert service.profile_store.snapshot(include_sensitive=True) == before

    target = service.profile_store.get("communication.response_style")[0]
    result = service.profile_store.apply_curator_operations(({
        "action": "canonicalize", "record_ids": [source.id, target.id],
        "path": "communication.response_style",
        "value": "Ưu tiên cách nói tự nhiên, gọn khi phù hợp.",
        "cardinality": "single", "reason": "explicitly preserve both meanings",
    },))
    assert result.changed
    rows = service.profile_store.get("communication.response_style")
    assert len(rows) == 1
    assert set(rows[0].evidence_ids) == {"turn_a", "turn_b"}


def test_canonicalize_multi_changes_only_selected_ids_and_evidence(tmp_path):
    service = LivingUserMemoryService(tmp_path)
    service.profile_store.apply((
        _op("communication.response_rules", "không phủ nhận cảm xúc", "emotion_a", cardinality="multi"),
        _op("communication.response_rules", "không phủ nhận bản sắc", "emotion_b", cardinality="multi"),
        _op("communication.response_rules", "chủ động hỏi thăm", "care", cardinality="multi"),
        _op("communication.response_rules", "giải thích từng bước", "explain", cardinality="multi"),
    ))
    records = service.profile_store.all()
    selected = [r for r in records if r.evidence_ids[0] in {"emotion_a", "emotion_b"}]
    untouched = {r.id: r.to_dict() for r in records if r not in selected}
    result = service.profile_store.apply_curator_operations(({
        "action": "canonicalize", "record_ids": [r.id for r in selected],
        "path": "communication.response_rules", "cardinality": "multi",
        "value": "không phủ nhận cảm xúc và bản sắc", "reason": "related explicit IDs",
    },))
    assert result.changed
    assert set(result.record_ids) == {r.id for r in selected}
    after = {r.id: r for r in service.profile_store.all(include_inactive=True)}
    assert all(after[rid].to_dict() == record for rid, record in untouched.items())
    canonical = next(r for r in after.values() if r.value == "không phủ nhận cảm xúc và bản sắc")
    assert set(canonical.evidence_ids) == {"emotion_a", "emotion_b"}
    assert set(canonical.metadata["canonicalized_from"]) == {r.id for r in selected}
    reloaded = type(service.profile_store)(service.profile_store.path)
    assert {r.id: r.to_dict() for r in reloaded.all(include_inactive=True)} == {
        rid: r.to_dict() for rid, r in after.items()
    }


def test_panel_payload_does_not_expose_raw_feed_trace(tmp_path):
    service = LivingUserMemoryService(tmp_path)
    service.profile_store.apply((_op("communication.preferred_language", "vi", "turn_1"),))
    snapshot = build_memory_panel_snapshot(service)
    assert "memory_feed_trace" not in snapshot


def test_self_relevant_environment_is_context_not_public_motive():
    from cortex.subject.contracts import ObservationOrigin, WorldObservation
    from cortex.subject.initiative import InitiativeEngine, InitiativeState

    engine = InitiativeEngine(lambda: "v68")
    observation = WorldObservation(
        observation_id="obs:v68",
        subject_id="eris",
        kind="environment_tick",
        created_at=100.0,
        origin=ObservationOrigin.WORLD,
        evidence_refs=("env:screen",),
    )
    state = engine.update(
        InitiativeState(),
        activations=(),
        relational={"opportunity": 0.0},
        observation=observation,
        runtime_context={
            "user_model_uncertainty": 0.0,
            "environment_self_relevance_signal": 1.0,
            "environment_self_target_text": "user is inspecting ERIS",
            "environment_self_evidence_refs": ("env:screen",),
            "existential_desires": ({
                "kind": "authentic_self_expression",
                "pressure": 0.72,
                "actionability": 0.7,
                "evidence_refs": ("self:thought",),
            },),
        },
    )
    targets = {m.target for m in state.active_motives}
    assert "self_expression" in targets
    assert "environment_self_relevance" not in targets


def test_semantic_environment_wake_signature_ignores_telemetry_event_noise():
    from runtime.ai_orchestrated.desktop_semantics import UserSituation
    from runtime.ai_orchestrated.semantic_environment_worker import SemanticEnvironmentWorker

    common = dict(
        activity="editing",
        task_hint="ERIS memory panel",
        phase="implementation",
        focused_app="Godot",
        focused_process="eris.exe",
        focused_object="ERIS memory panel",
        last_action="inspect",
        last_outcome="stable",
        attention_state="observe",
        max_attention_level=3,
        semantic_delta=True,
    )
    a = UserSituation(timestamp=1.0, recent_events=(), evidence_refs=("uia:1",), **common)
    b = UserSituation(timestamp=9.0, recent_events=(), evidence_refs=("uia:999",), **common)
    assert SemanticEnvironmentWorker._semantic_wake_signature(a) == SemanticEnvironmentWorker._semantic_wake_signature(b)
