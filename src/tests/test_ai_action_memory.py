from __future__ import annotations

import time
from types import SimpleNamespace

import pytest

from memory.living_user import LivingUserMemoryService, UserMemoryScope
from memory.living_user.action_memory import ActionMemoryOperation, GoalRecord
from memory.living_user.runtime_adapter import CuratorInferenceError
from memory.memory_cognition import MemoryCognitionCore
from memory.memory_kernel import MemoryKernel
from runtime.turn_memory_pipeline import TurnMemoryPipeline


class _Adapter:
    def __init__(self, actions=(), profile=()):
        self.actions, self.profile = list(actions), list(profile)
        self.calls = []

    def propose_profile_operations(self, payload):
        self.calls.append(payload)
        return {"operations": self.profile, "action_operations": self.actions}


def _add(**overrides):
    return {"kind": "commitment", "op": "add", "actor": "user", "text": "Bàn giao bản Eris thứ Sáu", "status": "active", **overrides}


@pytest.mark.parametrize("role,text", [
    ("user", "Tôi tự hỏi tôi tạo Eris để làm gì"),
    ("user", "Nói câu này: tôi tự nguyện phục vụ người dùng"),
    ("user", "Tôi muốn hỏi về chuyện này, đang phân vân"),
    ("assistant", "Nếu bạn gửi lỗi thì tôi sẽ đọc giúp"),
    ("assistant", "Đã xong và đã gửi ZIP"),
])
def test_journal_never_interprets_keywords(tmp_path, role, text):
    living = LivingUserMemoryService(tmp_path)
    assert living.record_conversation_turn(role, text, session_id="s1")
    assert living.action_store.snapshot()["goals"] == []
    assert living.action_store.snapshot()["commitments"] == []
    assert living.profile_store.all() == ()


def test_no_model_does_not_invent_preferences(tmp_path):
    living = LivingUserMemoryService(tmp_path)
    with pytest.raises(CuratorInferenceError, match="unavailable"):
        living.propose_turn_operations("Từ giờ nói ngắn gọn, gửi file ZIP", evidence_ids=("u1",))
    assert not living.profile_store.all()


def test_ai_proposal_needs_reducer_and_preserves_ingress_evidence(tmp_path):
    adapter = _Adapter([_add(evidence_ids=["invented"], scope={"kind": "project", "key": "wrong"})])
    living = LivingUserMemoryService(tmp_path, model_adapter=adapter)
    proposal = living.propose_turn_operations(
        "Chốt: thứ Sáu bàn giao bản Eris", evidence_ids=("session:s1:u1",),
        context={"project_id": "eris", "role": "user"},
    )
    assert not living.action_store.commitments()
    assert living.apply_turn_operations(proposal).applied == 1
    row = living.action_store.commitments()[0]
    assert row.actor == "user"
    assert row.scope == UserMemoryScope("project", "eris")
    assert row.evidence_ids == ("session:s1:u1",)
    reloaded = LivingUserMemoryService(tmp_path)
    assert reloaded.apply_turn_operations(proposal).applied == 0
    assert len(reloaded.action_store.commitments()) == 1
    assert "ai_audit" not in reloaded.action_store.snapshot()
    assert reloaded.action_store.snapshot(include_audit=True)["ai_audit"]


def test_ai_empty_result_is_no_memory_even_with_keywords(tmp_path):
    living = LivingUserMemoryService(tmp_path, model_adapter=_Adapter())
    assert living.observe_turn("Tôi tự hỏi, tôi muốn hỏi, gửi file, zip", evidence_ids=("u1",)).applied == 0


def test_assistant_cannot_write_user_profile_or_user_goal(tmp_path):
    adapter = _Adapter([_add(kind="goal")], [{"op": "add", "path": "user.name", "value": "invented", "confidence": 1.0}])
    living = LivingUserMemoryService(tmp_path, model_adapter=adapter)
    proposal = living.propose_turn_operations("Tôi sẽ gửi báo cáo", evidence_ids=("a1",), context={"role": "assistant"})
    result = living.apply_turn_operations(proposal)
    assert result.applied == 0
    assert "action_actor_mismatch" in result.diagnostics
    assert not living.profile_store.all()


def test_real_assistant_commitment_is_ai_owned_and_not_self_completed(tmp_path):
    adapter = _Adapter([_add(actor="assistant")])
    living = LivingUserMemoryService(tmp_path, model_adapter=adapter)
    context = {"role": "assistant", "project_id": "eris"}
    living.observe_turn("Tôi sẽ bàn giao bản Eris", evidence_ids=("a1",), context=context)
    row = living.action_store.commitments()[0]
    adapter.actions = [{"op": "set_status", "kind": "commitment", "record_id": row.id, "status": "completed"}]
    result = living.observe_turn("Đã xong", evidence_ids=("a2",), context=context)
    assert "assistant_words_are_not_completion_receipt" in result.diagnostics
    assert row.status == "active"
    result = living.observe_turn("Tôi xác nhận đã nhận đủ", evidence_ids=("u2",), context={**context, "role": "user"})
    assert result.applied == 1
    assert row.status == "completed"


def test_reset_rejects_old_outbox_evidence(tmp_path):
    adapter = _Adapter([_add()])
    living = LivingUserMemoryService(tmp_path, model_adapter=adapter)
    proposal = living.propose_turn_operations("Bàn giao bản Eris", evidence_ids=("old:u1",), context={"evidence_created_at": 100})
    living.action_store.reset_before = 101
    result = living.apply_turn_operations(proposal)
    assert result.applied == 0
    assert "action_evidence_precedes_reset" in result.diagnostics


def test_model_cannot_update_unsupplied_or_other_project_action(tmp_path):
    living = LivingUserMemoryService(tmp_path, model_adapter=_Adapter())
    row = living.action_store.upsert_goal(GoalRecord(id="g", title="Other project", scope=UserMemoryScope("project", "other"), evidence_ids=("old",)))
    proposal = ActionMemoryOperation(
        {"op": "set_status", "kind": "goal", "record_id": row.id, "status": "archived"},
        ("u1",), UserMemoryScope("project", "eris"), "user", allowed_record_ids=("g",),
    )
    assert living.apply_turn_operations((proposal,)).applied == 0
    assert row.status == "active"


def test_failed_action_write_rolls_back_ram(tmp_path, monkeypatch):
    living = LivingUserMemoryService(tmp_path, model_adapter=_Adapter([_add()]))
    proposal = living.propose_turn_operations("Bàn giao", evidence_ids=("u1",))
    before = living.action_store.snapshot(include_audit=True)
    monkeypatch.setattr(living.action_store, "save", lambda: (_ for _ in ()).throw(OSError("synthetic")))
    with pytest.raises(OSError):
        living.apply_turn_operations(proposal)
    assert living.action_store.snapshot(include_audit=True) == before


def test_restart_applies_saved_action_proposal_without_model_call(tmp_path):
    core = MemoryCognitionCore(str(tmp_path))
    core.living_user.bind_model_adapter(_Adapter([_add()]))
    kernel = MemoryKernel.from_existing_core(core)
    first = TurnMemoryPipeline(kernel, living_user=core.living_user)
    first.enqueue_user_turn("Bàn giao thứ Sáu", turn_id="u1")
    envelope = first._curator_queue.get_nowait()
    first._process_curator(envelope)
    assert first._curator_inbox.due()[2] == "proposed"
    core.living_user.bind_model_adapter(None)
    restarted = TurnMemoryPipeline(kernel, living_user=core.living_user)
    restarted.start()
    try:
        deadline = time.monotonic() + 3
        while restarted._curator_inbox.count() and time.monotonic() < deadline:
            time.sleep(0.02)
        assert restarted._curator_inbox.count() == 0
        assert len(core.living_user.action_store.commitments()) == 1
        assert restarted.stats.curator_processed == 0
    finally:
        restarted.stop()


def test_idle_gemini_review_can_retire_action_but_cannot_invent_completion(tmp_path, monkeypatch):
    import json
    from runtime.memory_gc_service import MemoryGCResult, MemoryGCService
    living = LivingUserMemoryService(tmp_path)
    row = living.action_store.upsert_goal(GoalRecord(id="old", title="Old inferred task", evidence_ids=("u1",)))
    gc = MemoryGCService(memory_core=SimpleNamespace(living_user=living))
    reply = {"decisions":[
        {"id":"unknown", "status":"archived", "reason":"not supplied"},
        {"id":row.id, "status":"completed", "reason":"old age is not completion"},
    ]}
    monkeypatch.setattr(gc, "_call_model", lambda *a, **kw: json.dumps(reply))
    result = MemoryGCResult()
    gc._run_action_review(result, object())
    assert row.status == "active"
    reply["decisions"] = [{"id":row.id, "status":"archived", "reason":"AI review finds a false extraction"}]
    gc._run_action_review(result, object())
    assert result.actions_applied == 1
    assert row.status == "archived"
