# -*- coding: utf-8 -*-
"""Character V0.4: test tại canonical stores, không chỉ test prompt/JSON helper."""
from __future__ import annotations

from types import SimpleNamespace

from memory.claim_provenance import classify_ingress, create_provenance, provenance_of
from memory.memory_cognition import MemoryCognitionCore
from memory.memory_kernel import MemoryKernel
from memory.semantic_memory import SemanticMemoryStore


def commit_request(text: str, root: str, *, origin="user_statement", key="user.name", value="Minh", corrects=""):
    provenance = create_provenance(
        origin, root_event_ids=[root], subject="user" if origin == "user_statement" else "unknown",
        scope="conversation" if origin == "user_statement" else "derived",
        assertion="direct" if origin == "user_statement" else "inferred", corrects_claim_ref=corrects,
    )
    return SimpleNamespace(target="semantic_memory", content=text, confidence=0.9,
                           evidence_ids=[root], storage_permission=True, permission_required=False,
                           decay_policy="slow", reason="test", tags=[],
                           metadata={"fact_key": key, "value": value, "claim_provenance": provenance})


def test_assistant_statement_proves_only_utterance(tmp_path):
    core = MemoryCognitionCore(str(tmp_path / "core"))
    result = core.commit(commit_request("User đi Nhật rồi", "delivery:1", origin="assistant_utterance"))
    assert result.outcome == "rejected"
    assert not core.semantic.load()
    # Utterance vẫn có thể lưu trong event ledger, không tự biến thành user fact.


def test_observer_summary_cannot_become_explicit_user_statement(tmp_path):
    core = MemoryCognitionCore(str(tmp_path / "core"))
    kernel = MemoryKernel.from_existing_core(core)
    plan = kernel.plan_ingest(
        "Tên tôi là Minh", turn_id="user:123", source="ai_post_turn_observer",
        context={"claim_provenance": classify_ingress("ai_post_turn_observer", turn_id="user:123")},
    )
    for request in plan.commit_requests:
        if request["target"] == "semantic_memory":
            assert request["metadata"]["claim_provenance"]["origin_kind"] == "observer_summary"
            assert core.commit(request).outcome == "rejected"
    assert not core.semantic.load()


def test_fiction_scope_and_unknown_cannot_promote_to_fact(tmp_path):
    core = MemoryCognitionCore(str(tmp_path / "core"))
    for kind in ("fiction", "unknown", "runtime_state", "observer_summary"):
        request = commit_request("Người dùng từng hứa điều X", "same-root", origin=kind)
        assert core.commit(request).outcome == "rejected"
    assert not core.semantic.load()
    assert provenance_of({"claim_provenance": {"schema": "bad", "origin_kind": "user_statement"}})["origin_kind"] == "unknown"


def test_verified_user_statement_still_ingests_and_reloads(tmp_path):
    core_dir = str(tmp_path / "core")
    core = MemoryCognitionCore(core_dir)
    result = core.commit(commit_request("Tên tôi là Minh", "user:1"))
    assert result.outcome == "accepted", result
    stored = core.semantic.load()[0]
    assert stored.metadata["lineage_root_ids"] == ["user:1"]
    reloaded = MemoryCognitionCore(core_dir)
    assert reloaded.semantic.load()[0].metadata["lineage_root_ids"] == ["user:1"]


def test_same_root_replay_does_not_raise_observations_or_confidence(tmp_path):
    core = MemoryCognitionCore(str(tmp_path / "core"))
    request = commit_request("Tên tôi là Minh", "user:1")
    core.commit(request)
    before = core.semantic.load()[0]
    core.commit(request)
    after = core.semantic.load()[0]
    assert after.observations == before.observations
    assert after.confidence == before.confidence
    assert after.metadata["lineage_root_ids"] == ["user:1"]


def test_summary_of_same_root_is_not_new_evidence(tmp_path):
    core = MemoryCognitionCore(str(tmp_path / "core"))
    core.commit(commit_request("Tên tôi là Minh", "user:1"))
    before = core.semantic.load()[0]
    summary = commit_request("Tên tôi là Minh", "user:1", origin="observer_summary")
    assert core.commit(summary).outcome == "rejected"
    after = core.semantic.load()[0]
    assert (before.observations, before.confidence) == (after.observations, after.confidence)


def test_replay_after_reload_is_idempotent(tmp_path):
    root = str(tmp_path / "core")
    core = MemoryCognitionCore(root)
    core.commit(commit_request("Tên tôi là Minh", "user:1"))
    before = core.semantic.load()[0]
    restarted = MemoryCognitionCore(root)
    restarted.commit(commit_request("Tên tôi là Minh", "user:1"))
    after = restarted.semantic.load()[0]
    assert (before.observations, before.confidence) == (after.observations, after.confidence)


def test_genuine_new_user_source_remains_admissible(tmp_path):
    core = MemoryCognitionCore(str(tmp_path / "core"))
    core.commit(commit_request("Tên tôi là Minh", "user:1"))
    core.commit(commit_request("Tên tôi là Minh", "user:2"))
    item = core.semantic.load()[0]
    assert item.observations == 2
    assert item.metadata["lineage_root_ids"] == ["user:1", "user:2"]


def test_correction_supersedes_claim_but_preserves_history_after_reload(tmp_path):
    root = str(tmp_path / "core")
    core = MemoryCognitionCore(root)
    core.commit(commit_request("Tên tôi là Minh", "user:1"))
    first = core.semantic.load()[0]
    core.commit(commit_request("Tên tôi là Khang", "user:2", value="Khang", corrects=first.id))
    restarted = MemoryCognitionCore(root)
    items = restarted.semantic.load()
    assert len(items) == 1
    assert "Khang" in items[0].claim
    assert any("Minh" in c for c in items[0].metadata.get("previous_claims", []))
    assert items[0].metadata["corrects_claim_ref"] == first.id
    assert items[0].metadata["lineage_root_ids"] == ["user:1", "user:2"]


def test_legacy_without_origin_is_unknown_not_invented_user(tmp_path):
    item_store = SemanticMemoryStore(str(tmp_path / "semantic.json"))
    legacy = SimpleNamespace(target="semantic_memory", content="Tên tôi là Minh", confidence=0.85,
                             evidence_ids=["legacy-id"], metadata={"fact_key": "user.name"}, tags=[], reason="legacy")
    stored = item_store.apply_commit(legacy)
    assert provenance_of(stored.metadata)["origin_kind"] == "unknown"
    # Legacy unverified không tạo được bằng chứng mới cho item typed.
    item_store.apply_commit(commit_request("Tên tôi là Minh", "new-user:1"))
    loaded = item_store.load()[0]
    assert loaded.observations == 2  # existing legacy claim + genuinely new typed user source


def test_observer_cannot_spoof_user_origin_in_context(tmp_path):
    """Worker tự viết claim_provenance=user không thể vượt nguồn ở ingress."""
    core = MemoryCognitionCore(str(tmp_path / "core"))
    kernel = MemoryKernel.from_existing_core(core)
    injected = classify_ingress("chat_turn", turn_id="user:spoof")
    # Dùng chính adapter kernel; fast policy có thể không đề xuất commit nào
    # cho câu này, nên không được để test pass một cách rỗng.
    request = commit_request("Tên tôi là Minh", "observer:1").__dict__
    request = kernel._annotate_request(
        request, mode="semantic_digest", source="ai_post_turn_observer",
        context={"claim_provenance": injected}, evidence_ids=["observer:1"],
    )
    assert request["metadata"]["claim_provenance"]["origin_kind"] == "observer_summary"
    assert request["metadata"]["source_grounded"] is False
    assert core.commit(request).outcome == "rejected"
    assert not core.semantic.load()


def test_committer_rejects_mismatched_typed_origin_and_runtime_source(tmp_path):
    """Nếu có hai nhãn nguồn trái nhau, durable owner phải chặn thay vì chọn nhãn có lợi."""
    core = MemoryCognitionCore(str(tmp_path / "core"))
    request = commit_request("Tên tôi là Minh", "observer:7")
    request.metadata["memory_kernel_source"] = "ai_post_turn_observer"
    verdict = core.commit(request)
    assert verdict.outcome == "rejected"
    assert verdict.reason == "ingress_origin_mismatch"
    assert not core.semantic.load()


def test_observer_does_not_write_living_user_profile(tmp_path):
    """Không chỉ SemanticStore; LivingUser.observe_turn cũng là một đường factual promotion."""
    core = MemoryCognitionCore(str(tmp_path / "core"))
    kernel = MemoryKernel.from_existing_core(core)

    class ObserverRecorder:
        def __init__(self):
            self.calls = []
        def observe_turn(self, *args, **kwargs):
            self.calls.append((args, kwargs))
            raise AssertionError("observer không được ghi hồ sơ người dùng")
        def sync_semantic_memory(self):
            # Sync đọc semantic store, không infer từ raw observer text.
            return SimpleNamespace(changed=False)

    recorder = ObserverRecorder()
    kernel.living_user = recorder
    plan = kernel.plan_ingest("Tôi sống ở Đà Nẵng", turn_id="u:1", source="ai_post_turn_observer")
    result = kernel.commit_plan(plan, observe_living_user=True, sync_living_user_semantic=True)
    assert recorder.calls == []
    assert result.debug["living_user"]["reason"] == "non_user_provenance"
    assert not core.semantic.load()


def test_correction_wrong_fact_key_is_rejected_without_corrupting_other_fact(tmp_path):
    """Có ID thật không có nghĩa được sửa nhầm sang chủ đề khác."""
    core = MemoryCognitionCore(str(tmp_path / "core"))
    assert core.commit(commit_request("Tên tôi là Minh", "user:1")).outcome == "accepted"
    first = core.semantic.load()[0]
    wrong = commit_request("Tôi ở Huế", "user:2", key="user.city", value="Huế", corrects=first.id)
    verdict = core.commit(wrong)
    assert verdict.outcome == "rejected"
    assert "correction_target_fact_key_mismatch" in verdict.reason
    after = core.semantic.load()
    assert len(after) == 1
    assert after[0].claim == first.claim
    assert after[0].metadata["lineage_root_ids"] == ["user:1"]


def test_correction_missing_target_does_not_create_fake_history(tmp_path):
    core = MemoryCognitionCore(str(tmp_path / "core"))
    verdict = core.commit(commit_request("Tên tôi là Minh", "user:2", corrects="sem_does_not_exist"))
    assert verdict.outcome == "rejected"
    assert not core.semantic.load()


def test_root_lineage_remains_complete_past_evidence_prompt_cap(tmp_path):
    """Không lấy danh sách evidence_id đã cap làm bộ chống duplicate sau restart."""
    base = str(tmp_path / "core")
    core = MemoryCognitionCore(base)
    for index in range(20):
        verdict = core.commit(commit_request("Tên tôi là Minh", f"user:{index}"))
        assert verdict.outcome == "accepted"
    record = core.semantic.load()[0]
    assert record.observations == 20
    assert len(record.metadata["lineage_root_ids"]) == 20
    restarted = MemoryCognitionCore(base)
    previous_confidence = restarted.semantic.load()[0].confidence
    restarted.commit(commit_request("Tên tôi là Minh", "user:0"))
    again = restarted.semantic.load()[0]
    assert again.observations == 20
    assert again.confidence == previous_confidence


def test_duplicate_receipt_with_same_source_does_not_learn_traits(tmp_path):
    """Dùng hai lần chính request verified không làm tăng confidence hoặc ghi memory type mới."""
    core = MemoryCognitionCore(str(tmp_path / "core"))
    statement = commit_request("Tên tôi là Minh", "user:1")
    core.commit(statement)
    old = core.semantic.load()[0]
    for _ in range(4):
        assert core.commit(statement).outcome == "accepted"
    new = core.semantic.load()[0]
    assert (new.observations, new.confidence) == (old.observations, old.confidence)


def test_legacy_replay_with_new_envelope_ids_does_not_reinforce(tmp_path):
    """Legacy không có typed root không được nhận observations mới chỉ vì UUID đổi."""
    store = SemanticMemoryStore(str(tmp_path / "semantic.json"))
    def legacy(evidence_id):
        return SimpleNamespace(target="semantic_memory", content="Tên tôi là Minh", confidence=0.95,
                               evidence_ids=[evidence_id], metadata={"fact_key": "user.name"},
                               tags=[], reason="legacy")
    first = store.apply_commit(legacy("legacy:1"))
    first_confidence = first.confidence
    for i in range(2, 5):
        store.apply_commit(legacy(f"legacy:{i}"))
    again = store.load()[0]
    assert again.observations == 1
    assert again.confidence == first_confidence
    assert provenance_of(again.metadata)["origin_kind"] == "unknown"


def test_reload_lookup_uses_corrected_value_and_keeps_superseded_claim(tmp_path):
    directory = str(tmp_path / "core")
    core = MemoryCognitionCore(directory)
    core.commit(commit_request("Tên tôi là Minh", "user:1"))
    old = core.semantic.load()[0]
    core.commit(commit_request("Tên tôi là Khang", "user:2", value="Khang", corrects=old.id))
    restarted = MemoryKernel.from_existing_core(MemoryCognitionCore(directory))
    found = restarted.lookup_fact("user.name")
    assert found is not None
    # Fact trả ra current, không đảo lại phiên bản cũ.
    assert "Khang" in str(found)
    assert "Minh" not in str(getattr(found, "claim", ""))
    assert any("Minh" in text for text in restarted.core.semantic.load()[0].metadata["previous_claims"])


def test_background_assistant_role_overrides_chat_source_and_cannot_promote(tmp_path):
    """source=chat_turn nhưng role=assistant phải giữ assistant_utterance ở mọi tầng."""
    core = MemoryCognitionCore(str(tmp_path / "core"))
    kernel = MemoryKernel.from_existing_core(core)
    source = "chat_turn"
    context = {
        "background_role": "assistant",
        # Có cả metadata giả danh user nhưng runtime role vẫn thắng.
        "claim_provenance": classify_ingress("chat_turn", turn_id="assistant:1"),
    }
    request = kernel._annotate_request(
        commit_request("Tên tôi là Minh", "assistant:1").__dict__,
        mode="semantic_digest", source=source, evidence_ids=["assistant:1"],
        context=context,
    )
    assert request["metadata"]["claim_provenance"]["origin_kind"] == "assistant_utterance"
    assert request["metadata"]["source_grounded"] is False
    assert core.commit(request).outcome == "rejected"
    assert not core.semantic.load()
    assert classify_ingress(source, role="assistant")["origin_kind"] == "assistant_utterance"


def test_user_pipeline_rejects_observer_before_curator_dispatch(tmp_path):
    """Observer đưa nhầm vào user-turn pipeline không được kích hoạt curator."""
    from runtime.turn_memory_pipeline import TurnMemoryPipeline, TurnMemoryPipelineConfig

    class CountingKernel:
        def __init__(self):
            self.calls = 0
        def plan_ingest(self, *args, **kwargs):
            self.calls += 1
            raise AssertionError("không được gọi user memory plan")

    kernel = CountingKernel()
    pipeline = TurnMemoryPipeline(
        kernel, living_user=object(),
        config=TurnMemoryPipelineConfig(enabled=True, curator_enabled=False),
    )
    try:
        receipt = pipeline.enqueue_user_turn("Tên tôi là Minh", source="ai_post_turn_observer", turn_id="observer:1")
        assert not receipt.accepted
        assert receipt.reason == "non_user_source_for_user_turn_pipeline"
        assert kernel.calls == 0
    finally:
        pipeline.stop()
