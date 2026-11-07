from __future__ import annotations

import json
import time
from types import SimpleNamespace

from memory.living_user import (
    GoalRecord,
    LivingUserMemoryBenchmark,
    LivingUserMemoryService,
    ProfileOperation,
    UserMemoryScope,
    WorkflowRecord,
)


def _op(path, value, evidence, **kwargs):
    return ProfileOperation(
        op=kwargs.pop("op", "add"),
        path=path,
        value=value,
        confidence=kwargs.pop("confidence", 0.9),
        explicitness=kwargs.pop("explicitness", "explicit"),
        evidence_ids=(evidence,),
        reason="test",
        source="test",
        **kwargs,
    )


def test_goal_and_workflow_memory_are_injected_into_context(tmp_path):
    service = LivingUserMemoryService(tmp_path)
    service.add_goal(
        "Chuyển ERIS sang Qwen3 embedding",
        description="Đổi model sau khi hoàn thiện memory",
        next_actions=("benchmark threshold",),
        evidence_ids=("turn_1",),
        project_id="eris",
    )
    service.add_workflow(
        "sửa source ERIS",
        ("giữ tương thích ngược", "chú thích tiếng Việt", "chạy regression", "gửi ZIP và patch"),
        evidence_ids=("turn_2",),
        project_id="eris",
    )
    packet = service.compile_context("tiếp tục refactor ERIS", project_id="eris")
    rendered = packet.render_prompt_context()
    assert "Chuyển ERIS sang Qwen3 embedding" in rendered
    assert "chú thích tiếng Việt" in rendered
    assert "ACTIVE USER WORK" in rendered


def test_ai_extracts_goal_and_workflow_only_at_apply_boundary(tmp_path):
    adapter = SimpleNamespace(propose_profile_operations=lambda payload: {"operations": [], "action_operations": [
        {"op":"add", "kind":"goal", "text":"Hoàn thiện memory", "status":"active"},
        {"op":"add", "kind":"workflow", "text":"Bàn giao source", "steps":["chạy kiểm thử hồi quy", "gửi file hoàn chỉnh"]},
    ]})
    service = LivingUserMemoryService(tmp_path, model_adapter=adapter)
    text = "Tôi muốn hoàn thiện memory, giữ tương thích ngược, chạy test và gửi file hoàn chỉnh."
    service.record_conversation_turn("user", text, session_id="s1", turn_id="u1", context={"project_id":"eris"})
    assert not service.action_store.goals()
    proposal = service.propose_turn_operations(text, evidence_ids=("u1",), context={"project_id":"eris"})
    assert not service.action_store.goals()
    service.apply_turn_operations(proposal)
    assert service.action_store.goals(scope=UserMemoryScope("project", "eris"))
    assert "chạy kiểm thử hồi quy" in service.action_store.workflows()[0].steps


def test_assistant_journal_does_not_interpret_promises_or_completion(tmp_path):
    service = LivingUserMemoryService(tmp_path)
    for i, text in enumerate(("Tôi sẽ refactor source và gửi ZIP.", "Đã xong và đã gửi ZIP.")):
        service.record_conversation_turn("assistant", text, session_id="s1", turn_id=f"a{i}", context={"project_id":"eris"})
    assert not service.action_store.commitments(include_inactive=True)


def test_uncertain_inference_is_marked_for_clarification(tmp_path):
    service = LivingUserMemoryService(tmp_path)
    service.profile_store.apply((
        _op(
            "technical.experience_level", "advanced", "turn_1",
            explicitness="inferred", confidence=0.62,
        ),
    ))
    packet = service.compile_context("giải thích kỹ thuật cho tôi")
    item = next(x for x in packet.items if x.path == "technical.experience_level")
    assert item.use_mode == "clarify"
    assert packet.clarifications
    assert "không coi là sự thật" in packet.render_prompt_context()


def test_conflicting_memory_is_never_silently_selected(tmp_path):
    service = LivingUserMemoryService(tmp_path)
    service.profile_store.apply((_op("user.name", "Khang", "turn_1", confidence=0.96),))
    service.profile_store.apply((
        _op("user.name", "Minh", "turn_2", explicitness="inferred", confidence=0.99),
    ))
    packet = service.compile_context("tên của tôi là gì")
    item = next(x for x in packet.items if x.path == "user.name")
    assert item.use_mode == "conflict"
    rendered = packet.render_prompt_context()
    assert "đang mâu thuẫn" in rendered
    assert "không tự chọn" in rendered


def test_trace_and_replay_explain_pipeline(tmp_path):
    adapter = SimpleNamespace(propose_profile_operations=lambda payload: {"operations": [
        {"op":"add", "path":"technical.code_comment_language", "value":"vi", "confidence":0.9, "explicitness":"explicit"}
    ]})
    service = LivingUserMemoryService(tmp_path, model_adapter=adapter)
    service.observe_turn(
        "Làm source có chú thích tiếng Việt và gửi file hoàn chỉnh.",
        evidence_ids=("turn_1",),
    )
    service.compile_context("sửa source")
    report = service.replay_report()
    assert report.event_count >= 2
    assert report.operation_count >= 1
    assert "technical.code_comment_language" in report.selected_paths
    assert any(line.startswith("observe:") for line in report.timeline)


def test_hard_forget_scrubs_matching_trace(tmp_path):
    service = LivingUserMemoryService(tmp_path)
    service.correct("user.name", "Khang", evidence_ids=("turn_1",))
    service.compile_context("tên tôi")
    assert "user.name" in json.dumps(service.replay_report().to_dict(), ensure_ascii=False)
    service.forget("user.name", hard=True)
    assert "user.name" not in json.dumps(service.replay_report().to_dict(), ensure_ascii=False)


def test_profile_compaction_removes_only_old_inactive_records(tmp_path):
    service = LivingUserMemoryService(tmp_path)
    service.profile_store.apply((_op("user.name", "Minh", "turn_1"),))
    service.profile_store.apply((_op("user.name", "Khang", "turn_2", op="update"),))
    old = next(x for x in service.profile_store.get("user.name", include_inactive=True) if x.status == "superseded")
    old.updated_at = time.time() - 120 * 86400
    removed = service.profile_store.compact(inactive_after_days=90)
    assert removed == 1
    assert service.profile_store.get("user.name")[0].value == "Khang"


def test_action_store_archives_stale_candidates_but_keeps_active_goals(tmp_path):
    service = LivingUserMemoryService(tmp_path)
    active = service.action_store.upsert_goal(GoalRecord(
        id="", title="Hoàn thiện ERIS", status="active", scope=UserMemoryScope(), evidence_ids=("turn_1",),
    ))
    candidate = service.action_store.upsert_goal(GoalRecord(
        id="", title="Có thể thử một UI khác", status="candidate", scope=UserMemoryScope(), evidence_ids=("turn_2",),
    ))
    candidate.updated_at = time.time() - 30 * 86400
    changed = service.action_store.archive_stale(candidate_after_days=14)
    assert changed == 1
    assert next(x for x in service.action_store.goals(include_inactive=True) if x.id == active.id).status == "active"
    assert next(x for x in service.action_store.goals(include_inactive=True) if x.id == candidate.id).status == "archived"


def test_memory_benchmark_measures_recall_and_false_memory():
    cases = ({
        "query": "hãy sửa source",
        "profile": [
            {"path": "technical.code_comment_language", "value": "vi", "evidence_id": "t1"},
            {"path": "health.private_note", "value": "không liên quan", "evidence_id": "t2", "sensitivity": "private"},
        ],
        "expected_paths": ["technical.code_comment_language"],
        "excluded_paths": ["health.private_note"],
    },)
    metrics = LivingUserMemoryBenchmark().run(cases)
    assert metrics.cases == 1
    assert metrics.recall_at_k == 1.0
    assert metrics.false_memory_rate == 0.0
    assert metrics.passed == 1


def test_action_memory_round_trip(tmp_path):
    service = LivingUserMemoryService(tmp_path)
    service.action_store.upsert_workflow(WorkflowRecord(
        id="", trigger="bàn giao source", steps=("chạy test", "gửi ZIP"),
        scope=UserMemoryScope("project", "eris"), evidence_ids=("turn_1",),
    ))
    second = LivingUserMemoryService(tmp_path)
    workflows = second.action_store.workflows(scope=UserMemoryScope("project", "eris"))
    assert workflows and workflows[0].steps == ("chạy test", "gửi ZIP")
