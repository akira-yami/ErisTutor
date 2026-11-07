from __future__ import annotations

from dataclasses import replace
import json
import sqlite3
from types import SimpleNamespace

import pytest

from runtime.learning_core import KnowledgePoint, LearningPath, LearningService, LearningStore
from runtime.learning_core.records import AssessmentAttempt, DomainSpec
from runtime.learning_core.evaluation import evaluate_attempts
from runtime.learning_core import retention
from runtime.tutor.activities import LearningActivities
from runtime.tutor.sources import text_spans, import_document
from runtime.capabilities.tutor import TutorCapability
from runtime.tutor.executive import TutorExecutive
from runtime.conversation.contracts import DeliveryReceipt
from runtime.workspace.artifact_protocol import validate_bundle
from runtime.workspace.presentation_p2 import verified_tool_bundles, publish_committed_presentation


def service(tmp_path):
    return LearningService(LearningStore(tmp_path / "learning.sqlite3"))


def attempt(index=1, **changes):
    value = AssessmentAttempt(
        f"attempt:{index}",
        "python:v1:range",
        "correct",
        "quiz_correct",
        strength=1.0,
        interaction_id=f"question:{index}",
        turn_id=f"turn:{index}",
        item_family_id="range-stop",
        recorded_at=1000 + index,
    )
    return replace(value, **changes)


def event(index=1, text="", generation=1):
    return SimpleNamespace(event_id=f"user:{index}", session_id="session:1", generation=generation, text=text)


def receipt(generation=1, successful=True):
    return DeliveryReceipt(
        delivery_id="delivery:1", session_id="session:1", generation=generation, move_id="move:1", successful=successful
    )

def bind_activity_receipt(capability, result, *, generation=1, event_id="user:1"):
    # Mô phỏng adapter đã commit một public move có bundle chính xác.
    capability.bind_pending_delivery(
        event_id=event_id, session_id="session:1", generation=generation,
        delivery_id="delivery:1", move_id="move:1",
        artifact_bundle_ids=[result["artifact_bundle_id"]],
    )


def test_assisted_partial_and_revealed_answers_do_not_claim_independent_ability(tmp_path):
    learning = service(tmp_path)
    for n, state in enumerate(("hinted", "worked_with_eris", "answer_revealed"), 1):
        assert learning.record_attempt(attempt(n, assistance=state, review_recall=True))
    assert learning.record_attempt(attempt(4, result="partial", evidence_type="paraphrase", review_recall=True))
    belief = learning.learner_belief("python:v1:range")
    assert belief.independent_count == 0 and not belief.transfer_observed
    assert learning.get_mastery("python:v1:range").correct_count == 0
    assert learning.get_mastery("python:v1:range").mastery == 0
    assert learning.retention_snapshot()["items"] == {}
    assert len(learning.attempts()) == 4


def test_idempotency_and_conflicting_verdict_require_explicit_correction(tmp_path):
    learning = service(tmp_path)
    assert learning.record_attempt(attempt())
    assert not learning.record_attempt(replace(attempt(), recorded_at=9999))
    with pytest.raises(ValueError, match="explicit_correction"):
        learning.record_attempt(attempt(result="incorrect", evidence_type="quiz_incorrect"))
    assert len(learning.attempts()) == 1


def test_uncertain_submission_is_retained_without_learning_evidence(tmp_path):
    learning = service(tmp_path)
    assert learning.record_attempt(attempt(result="ungraded", evidence_type="exposure"))
    assert learning.attempts()[0]["result"] == "ungraded"
    assert learning.get_mastery("python:v1:range").attempt_count == 0
    assert learning._store.load_evidence("python:v1:range") == []


def test_attempt_projection_failure_rolls_back_every_record(tmp_path, monkeypatch):
    learning = service(tmp_path)

    def crash(*args, **kwargs):
        raise RuntimeError("projection_failed")

    monkeypatch.setattr(learning, "_projection", lambda *args: crash)
    with pytest.raises(RuntimeError, match="projection_failed"):
        learning.record_attempt(attempt(review_recall=True))
    assert learning.attempts() == []
    assert not learning._store.load_evidence("python:v1:range")
    assert not learning.retention_snapshot()["items"]


def test_correction_retracts_old_mastery_and_fsrs_and_survives_restart(tmp_path):
    learning = service(tmp_path)
    assert learning.record_attempt(attempt(review_recall=True))
    assert learning.retention_snapshot()["items"]
    assert learning.correct_attempt("attempt:1", correction_id="correction:1", now=1100)
    assert learning.get_mastery("python:v1:range").mastery == 0
    assert not learning.retention_snapshot()["items"]
    assert learning.attempts()[0]["result"] == "ungraded"
    assert len(learning.attempts(include_superseded=True)) == 2
    learning._store.close()
    reopened = service(tmp_path)
    assert reopened.get_mastery("python:v1:range").correct_count == 0
    assert len(reopened._store.load_evidence("python:v1:range")) == 0


def test_forget_purges_correction_chain_and_blocks_replay_of_same_interaction(tmp_path):
    learning = service(tmp_path)
    learning.record_attempt(attempt(answer_ref="private-answer"))
    learning.correct_attempt("attempt:1", correction_id="correction:1", now=1100)
    assert learning.forget_attempt("correction:1", now=1200)
    assert not learning.attempts(include_superseded=True)
    assert not learning.record_attempt(attempt())
    # A replay with a different event ID still belongs to the forgotten question.
    assert not learning.record_attempt(attempt(2, interaction_id="question:1"))
    learning._store.close()
    learning = service(tmp_path)
    assert not learning.attempts()
    assert not learning._store.load_evidence("python:v1:range")


def test_fsrs_has_stable_card_id_utc_and_again_for_failed_recall(tmp_path):
    learning = service(tmp_path)
    assert retention.readiness()["available"]
    learning.record_attempt(attempt(review_recall=True, rating=4))
    first = learning.retention_snapshot()["items"]
    learning.record_attempt(
        attempt(2, review_recall=True, result="incorrect", evidence_type="quiz_incorrect", rating=2, recorded_at=200000)
    )
    updated = learning.retention_snapshot()["items"]
    assert first.keys() == updated.keys()
    key = next(iter(updated))
    assert first[key]["card"]["card_id"] == updated[key]["card"]["card_id"]
    assert updated[key]["logs"][-1]["rating"] == 1
    assert updated[key]["logs"][-1]["review_datetime"].endswith("+00:00")
    learning._store.close()
    assert service(tmp_path).retention_snapshot()["items"] == updated


def test_fsrs_replay_does_not_convert_legacy_or_assisted_history(tmp_path):
    from runtime.learning_core.contracts import LearningEvidence

    old = LearningEvidence(
        "old",
        "skill",
        "quiz_correct",
        strength=1,
        correct=True,
        recorded_at=1000,
        interaction_id="old-question",
        metadata='{"review_recall": true}',
    )
    assert not retention.replay([old])
    ev = LearningService._evidence_for_attempt(attempt(review_recall=True))
    assert retention.replay([ev]) == retention.replay([ev])
    assert not retention.replay([LearningService._evidence_for_attempt(attempt(review_recall=True, hint_count=1))])


def test_sources_are_scoped_before_limit_and_old_citations_become_stale(tmp_path):
    learning = service(tmp_path)
    for name in ("math", "physics"):
        learning.register_domain(DomainSpec(name, name, name + ":v1"))
    first = text_spans(domain_id="math", source_id="book", text="energy math", now=1000)
    learning.import_source_spans(first)
    learning.import_source_spans(text_spans(domain_id="physics", source_id="book", text="energy " * 100, now=1000))
    results = learning.search_sources("energy", domain_id="math", limit=1)
    assert len(results) == 1 and results[0]["domain_id"] == "math"
    learning.import_source_spans(text_spans(domain_id="math", source_id="book", text="new text", now=2000))
    assert learning._store.source_span(first[0].span_id)["stale"]
    assert learning.search_sources("energy", domain_id="math") == []
    with pytest.raises(ValueError, match="stale"):
        learning.record_attempt(attempt(source_refs=(first[0].span_id,)))


def test_text_import_keeps_exact_locator_and_document_adapter_is_lazy(tmp_path):
    text = "Vòng lặp và điều kiện.\n" * 300
    filename = tmp_path / "lesson.txt"
    filename.write_text(text, encoding="utf-8")
    spans = import_document(filename, domain_id="python", source_id="lesson", now=1000)
    for span in spans:
        _, start, end = span.locator.split(":")
        assert text[int(start) : int(end)] == span.excerpt
    assert all(span.source_kind == "original" for span in spans)


def domain_path(learning):
    learning.register_domain(DomainSpec("math", "Toán", "math:v1"))
    path = LearningPath(
        "path:math",
        "Toán",
        "Mục tiêu",
        (KnowledgePoint("math:v1:a", "A"), KnowledgePoint("math:v1:b", "B")),
        domain_id="math",
    )
    learning.register_path(path, now=1000)
    learning.activate_path(path.path_id)
    return path


def test_curriculum_compare_and_swap_preserves_skill_evidence(tmp_path):
    learning = service(tmp_path)
    path = domain_path(learning)
    learning.record_attempt(attempt(kp_id="math:v1:a", path_id=path.path_id))
    base = learning.curriculum_snapshot()["revision"]
    proposed = replace(path, knowledge_points=tuple(reversed(path.knowledge_points)))
    preview = learning.preview_curriculum(proposed)
    assert preview["changes"]["added"] == []
    revision = learning.revise_path(proposed, base_revision=base, reason="reorder", now=1100)
    assert revision != base
    assert learning.get_mastery("math:v1:a").correct_count == 1
    with pytest.raises(ValueError, match="stale"):
        learning.revise_path(path, base_revision=base, reason="stale job", now=1200)
    assert learning.curriculum_snapshot()["revision"] == revision


def test_curriculum_new_objectives_need_real_current_sources(tmp_path):
    learning = service(tmp_path)
    path = domain_path(learning)
    proposed = replace(path, knowledge_points=path.knowledge_points + (KnowledgePoint("math:v1:c", "C"),))
    with pytest.raises(ValueError, match="require_sources"):
        learning.preview_curriculum(proposed)
    spans = text_spans(domain_id="math", source_id="book", text="C", now=1000)
    learning.import_source_spans(spans)
    preview = learning.preview_curriculum(proposed, source_refs=(spans[0].span_id,))
    learning.import_source_spans(text_spans(domain_id="math", source_id="book", text="replacement", now=2000))
    with pytest.raises(ValueError, match="stale"):
        learning.revise_path(
            proposed, base_revision=preview["base_revision"], reason="new", source_refs=preview["source_refs"]
        )
    assert learning.active_path().kp_ids == path.kp_ids


def test_namespace_and_cycle_validation_do_not_overwrite_existing_paths(tmp_path):
    learning = service(tmp_path)
    path = domain_path(learning)
    bad = replace(path, path_id="other", knowledge_points=(KnowledgePoint("a", "A"),))
    with pytest.raises(ValueError, match="namespace"):
        learning.register_path(bad)
    cyclic = replace(
        path,
        knowledge_points=(
            replace(path.knowledge_points[0], prerequisites=("math:v1:b",)),
            replace(path.knowledge_points[1], prerequisites=("math:v1:a",)),
        ),
    )
    with pytest.raises(ValueError, match="cycle"):
        learning.revise_path(cyclic, base_revision=learning.curriculum_snapshot()["revision"], reason="cycle")
    assert learning.active_path() == path


def test_python_activity_requires_delivery_and_actual_user_event(tmp_path, monkeypatch):
    monkeypatch.setenv("ERIS_ARTIFACT_WORKSPACE", str(tmp_path / "artifacts"))
    learning = service(tmp_path)
    capability = TutorCapability(TutorExecutive(learning))
    result = capability.invoke_for_root(arguments={"operation": "activity"}, event=event(), recent_history=[])
    assert result["status"] == "completed"
    workspace = capability._activity_workspace
    item = learning._store.load_activity(result["activity_id"])
    # No answer key, hints or explanation is present in its public sidecar.
    sidecar = tmp_path / "artifacts" / "presentation_v1" / (result["artifact_bundle_id"] + ".json")
    bundle = validate_bundle(json.loads(sidecar.read_text(encoding="utf-8")))
    public = bundle["artifacts"][0]["content"]["activity"]
    assert set(public) == {"activity_id", "nonce", "revision", "options"}
    command = workspace.command(item, "answer", item["correct"])
    answer = event(2, command, 2)
    before = capability.invoke_for_root(
        arguments={"operation": "activity_event", "correct": True}, event=answer, recent_history=[]
    )
    assert before["status"] == "failed" and not learning.attempts()
    bind_activity_receipt(capability, result)
    capability.observe_delivery_receipt(receipt())
    graded = capability.invoke_for_root(
        arguments={"operation": "activity_event", "answer": 99}, event=answer, recent_history=[]
    )
    assert graded["attempt"]["result"] == "correct"
    assert learning.get_mastery(item["kp_id"]).correct_count == 1
    duplicate = capability.invoke_for_root(arguments={"operation": "activity_event"}, event=answer, recent_history=[])
    assert duplicate["duplicate"] and len(learning.attempts()) == 1
    assert (
        capability.invoke_for_root(
            arguments={"operation": "activity_event", "answer": item["correct"]},
            event=event(3, "Tôi chưa làm bài", 3),
            recent_history=[],
        )["status"]
        == "failed"
    )


def test_hint_or_reveal_commits_only_after_receipt_then_blocks_independent_success(tmp_path, monkeypatch):
    monkeypatch.setenv("ERIS_ARTIFACT_WORKSPACE", str(tmp_path / "artifacts"))
    learning = service(tmp_path)
    capability = TutorCapability(TutorExecutive(learning))
    made = capability.invoke_for_root(arguments={"operation": "activity"}, event=event(), recent_history=[])
    item = learning._store.load_activity(made["activity_id"])
    bind_activity_receipt(capability, made)
    capability.observe_delivery_receipt(receipt())
    workspace = capability._activity_workspace
    help_event = event(2, workspace.command(item, "reveal"), 2)
    helped = capability.invoke_for_root(arguments={"operation": "activity_event"}, event=help_event, recent_history=[])
    assert helped["help_kind"] == "answer_revealed"
    assert learning._store.assistance_for(item["activity_id"])["assistance"] == "independent"
    capability.bind_pending_delivery(
        event_id=help_event.event_id, session_id="session:1", generation=2,
        delivery_id="delivery:1", move_id="move:1",
    )
    capability.observe_delivery_receipt(receipt(2))
    command = workspace.command(item, "answer", item["correct"])
    result = capability.invoke_for_root(
        arguments={"operation": "activity_event"}, event=event(3, command, 3), recent_history=[]
    )
    assert result["attempt"]["assistance"] == "answer_revealed"
    assert result["next_step"] == "independent_variant"
    assert learning.get_mastery(item["kp_id"]).correct_count == 0
    assert learning.learner_belief(item["kp_id"]).independent_count == 0


def test_activity_stale_revision_and_cross_session_are_rejected(tmp_path):
    learning = service(tmp_path)
    workspace = LearningActivities(learning, workspace=tmp_path / "artifacts")
    item = workspace.create(event=event(), now=1000)
    workspace.mark_delivered(item["activity_id"], receipt=receipt())
    user = event(2, workspace.command(item, "answer", 0), 2)
    with pytest.raises(ValueError, match="session"):
        workspace.handle_event(replace_event(user, session_id="another"), now=1100)
    path = learning.active_path()
    learning.revise_path(
        replace(path, title="New title"), base_revision=item["base_revision"], reason="rename", now=1050
    )
    with pytest.raises(ValueError, match="stale"):
        workspace.handle_event(user, now=1100)
    assert not learning.attempts()


def replace_event(value, **kw):
    return SimpleNamespace(**{**vars(value), **kw})


def test_tutor_bundle_is_attached_only_from_real_tool_result(tmp_path):
    learning = service(tmp_path)
    workspace = LearningActivities(learning, workspace=tmp_path)
    item = workspace.create(event=event(), now=1000)
    bundle_id = workspace.bundle(item)
    assert not verified_tool_bundles([{"artifact_bundle_id": bundle_id}])
    refs = verified_tool_bundles(
        [
            {
                "kind": "capability_result",
                "capability": "tutor",
                "result": {"status": "completed", "operation": "activity", "artifact_bundle_id": bundle_id},
            }
        ]
    )
    assert refs == (bundle_id,)
    output = publish_committed_presentation(
        turn_id="turn:1", delivery_id="delivery:1", tool_bundles=refs, workspace=tmp_path
    )
    bound = json.loads(output.read_text(encoding="utf-8"))
    assert bound["artifacts"][0]["source"] == "tutor_activity"
    assert bound["artifacts"][0]["content"]["activity"]["activity_id"] == item["activity_id"]


def test_evaluation_is_observational_and_small_samples_do_not_claim_improvement():
    rows = [attempt(i, item_family_id=f"family:{i}", phase="transfer").to_dict() for i in range(1, 5)]
    report = evaluate_attempts(rows)
    assert report["design"] == "observational" and not report["causal_improvement_established"]
    assert report["policies"]["eris-tutor-v1"]["phases"]["transfer"]["success_rate"] is None
    rows.append(attempt(5, item_family_id="family:5", phase="transfer", assistance="hinted").to_dict())
    assert evaluate_attempts(rows)["policies"]["eris-tutor-v1"]["independent_families"] == 4


def test_old_database_is_extended_without_promoting_legacy_review_to_fsrs(tmp_path):
    from runtime.learning_core.store import _DDL

    filename = tmp_path / "learning.sqlite3"
    conn = sqlite3.connect(filename)
    conn.executescript(_DDL)
    conn.execute(
        "INSERT INTO learning_evidence VALUES (?,?,?,?,?,?,?,?,?,?)",
        ("legacy", "skill", "quiz_correct", 1, 1, "old-question", "old-turn", 1000, "", ""),
    )
    conn.commit()
    conn.close()
    learning = service(tmp_path)
    assert learning._store.load_evidence("skill")[0].evidence_id == "legacy"
    assert not learning.retention_snapshot()["items"]


def test_choice_status_and_evidence_roll_back_together_and_can_be_retried(tmp_path, monkeypatch):
    learning = service(tmp_path)
    interaction = learning.pose_interaction(
        "python:v1:range", "multiple_choice", "Q?", "yes", options=("yes", "no"), correct_index=0, now=1000
    )
    project = learning._projection

    def crash(*args):
        raise RuntimeError("projection_failed")

    monkeypatch.setattr(learning, "_projection", lambda *args: crash)
    with pytest.raises(RuntimeError, match="projection_failed"):
        learning.resolve_interaction(interaction.interaction_id, 0, turn_id="answer:1", now=1001)
    assert learning._store.load_interaction(interaction.interaction_id).status == "pending"
    assert not learning.attempts()
    monkeypatch.setattr(learning, "_projection", project)
    assert learning.resolve_interaction(interaction.interaction_id, 0, turn_id="answer:1", now=1001)
    assert learning._store.load_interaction(interaction.interaction_id).status == "answered"
    assert len(learning.attempts()) == 1


def test_help_does_not_reduce_existing_mastery_or_become_recall_on_retry(tmp_path):
    learning = service(tmp_path)
    learning.record_attempt(attempt())
    before = learning.get_mastery("python:v1:range").mastery
    learning.record_attempt(attempt(2, assistance="hinted", review_recall=True))
    learning.record_attempt(attempt(3, interaction_id="question:2", assistance="hinted", review_recall=True))
    assert learning.get_mastery("python:v1:range").mastery == before
    assert learning.review_memory("python:v1:range")["recall_count"] == 0


def test_correction_uses_original_observation_time_not_correction_time(tmp_path):
    learning = service(tmp_path)
    learning.record_attempt(attempt(result="incorrect", evidence_type="quiz_incorrect", review_recall=True))
    learning.correct_attempt("attempt:1", correction_id="correction:later", result="correct", now=900000)
    corrected = learning.attempts()[0]
    assert corrected["recorded_at"] == 1001
    assert corrected["corrected_at"] == 900000
    item = next(iter(learning.retention_snapshot()["items"].values()))
    assert item["logs"][0]["review_datetime"].startswith("1970-01-01")


def test_coalesced_button_messages_use_actual_last_answer_without_inventing_help(tmp_path):
    learning = service(tmp_path)
    workspace = LearningActivities(learning, workspace=tmp_path / "artifacts")
    item = workspace.create(event=event(), now=1000)
    workspace.mark_delivered(item["activity_id"], receipt=receipt())
    command = workspace.command(item, "hint") + "\n" + workspace.command(item, "answer", item["correct"])
    result = workspace.handle_event(event(2, command, 2), now=1100)
    assert result["attempt"]["result"] == "correct"
    assert result["attempt"]["assistance"] == "independent"


def test_real_root_capability_receipt_carries_activity_bundle(tmp_path, monkeypatch):
    from runtime.conversation.agent import RuntimeModelRootAgent
    from runtime.conversation.context import RootCapabilityRegistry
    from runtime.conversation.contracts import ConversationEvent, NextConversationMove

    monkeypatch.setenv("ERIS_ARTIFACT_WORKSPACE", str(tmp_path / "artifacts"))
    learning = service(tmp_path)
    cap = TutorCapability(TutorExecutive(learning))

    class Router:
        executive_client = object()

        def __init__(self):
            self.seen = []
            self.steps = [
                {
                    "kind": "use_capability",
                    "capability": "tutor",
                    "arguments": {"operation": "activity", "preset": "python.range"},
                },
                {
                    "kind": "move",
                    "intent": "Mời tự thử",
                    "say": "Thử bài trong bàn làm việc nhé.",
                    "after": "wait_user",
                },
            ]

        def invoke_structured_worker(self, client, **kwargs):
            self.seen.append(kwargs)
            return self.steps.pop(0)

    router = Router()
    result = RuntimeModelRootAgent(router).decide(
        event=ConversationEvent(
            event_id="root:activity", session_id="session:1", generation=1, kind="user_turn", text="Cho tôi bài Python"
        ),
        recent_history=(),
        note="",
        runtime_context={
            "__root_capabilities": RootCapabilityRegistry({"tutor": cap}),
            "__conversation_run_id": "root:run",
        },
    )
    assert isinstance(result, NextConversationMove)
    assert result.tool_artifact_bundles
    tool_result = router.seen[1]["payload"]["tool_results"][0]
    assert tool_result["capability"] == "tutor"
    assert tool_result["result"]["status"] == "completed"
    assert not learning.attempts()  # opening an exercise is not evidence of learning


def test_fsrs_can_own_due_time_without_relabeling_legacy_heuristic_state(tmp_path):
    from datetime import datetime

    learning = service(tmp_path)
    learning.record_attempt(attempt(review_recall=True))
    shadow = learning.retention_snapshot()["items"]
    due = datetime.fromisoformat(next(iter(shadow.values()))["card"]["due"]).timestamp()
    active = LearningService(LearningStore(tmp_path / "learning.sqlite3"), retention_mode="fsrs")
    assert active._scheduled_review_items(["python:v1:range"], now=1002)[0].due_at == due
    assert active.review_snapshot()["python:v1:range"]["scheduler_owner"] == "fsrs"
    assert active.retention_snapshot()["items"] == shadow  # switching does not synthesize new reviews
    assert active.due_review_items(now=due + 1)[0].kp_id == "python:v1:range"


def test_missing_fsrs_keeps_learning_available_with_explicit_readiness(tmp_path, monkeypatch):
    monkeypatch.setattr(retention, "readiness", lambda: {"available": False, "reason": "not_installed"})
    learning = LearningService(LearningStore(tmp_path / "learning.sqlite3"), retention_mode="fsrs")
    assert learning.record_attempt(attempt(review_recall=True))
    assert learning.review_snapshot()["python:v1:range"]["scheduler_owner"] == "heuristic"
    assert not learning.retention_snapshot()["readiness"]["available"]
    assert learning.get_mastery("python:v1:range").correct_count == 1


def test_closed_python_presets_and_all_variants_have_correct_answer_keys():
    from contextlib import redirect_stdout
    from io import StringIO
    from runtime.tutor.activities import PRESETS, tracing_variant

    for preset in PRESETS:
        for seed in range(100):
            item = tracing_variant(preset, seed)
            output = StringIO()
            # Only fixed, authored preset templates with numeric substitutions;
            # this test never executes learner/model supplied Python.
            with redirect_stdout(output):
                exec(compile(item["code"], "authored-preset", "exec"), {})
            assert output.getvalue().strip() == item["options"][item["correct"]]
            assert len(set(item["options"])) == 3


def test_same_exercise_family_cannot_manufacture_mastery_by_reopening(tmp_path):
    learning = service(tmp_path)
    for n in range(1, 5):
        learning.record_attempt(attempt(n, item_family_id="same-template"))
    assert learning.get_mastery("python:v1:range").correct_count == 1
    assert learning.get_mastery("python:v1:range").stage == "practicing"
    assert learning.learner_belief("python:v1:range").independent_count == 1


def test_repeated_template_cannot_drown_out_independent_failure(tmp_path):
    learning = service(tmp_path)
    learning.record_attempt(attempt(result="incorrect", evidence_type="quiz_incorrect", item_family_id="other"))
    learning.record_attempt(attempt(2, item_family_id="reopened"))
    before = learning.get_mastery("python:v1:range").mastery
    for index in range(3, 15):
        learning.record_attempt(attempt(index, item_family_id="reopened"))
    record = learning.get_mastery("python:v1:range")
    assert record.mastery == pytest.approx(before, abs=0.001)
    assert record.stage == "practicing" and record.attempt_count == 14


def test_fresh_activity_changes_last_seen_problem_after_help(tmp_path):
    learning = service(tmp_path)
    workspace = LearningActivities(learning, workspace=tmp_path / "artifacts")
    first = workspace.create(event=event(), now=1000)
    second = workspace.create(event=event(2), now=1001)
    assert first["code"] != second["code"]
    assert first["kp_id"] == second["kp_id"]


def test_reimporting_same_named_document_is_idempotent_and_new_bytes_stale_old_spans(tmp_path):
    learning = service(tmp_path)
    domain_path(learning)
    cap = TutorCapability(TutorExecutive(learning))
    filename = tmp_path / "lesson.txt"
    filename.write_text("original geometry", encoding="utf-8")
    text = "Học từ file " + str(filename)
    first = cap.invoke_for_root(
        arguments={"operation": "import_source", "path": str(filename)}, event=event(1, text), recent_history=[]
    )
    second = cap.invoke_for_root(
        arguments={"operation": "import_source", "path": str(filename)}, event=event(2, text), recent_history=[]
    )
    assert first["status"] == second["status"] == "completed"
    assert first["source_id"] == second["source_id"]
    filename.write_text("revised geometry", encoding="utf-8")
    third = cap.invoke_for_root(
        arguments={"operation": "import_source", "path": str(filename)}, event=event(3, text), recent_history=[]
    )
    assert third["status"] == "completed" and third["source_id"] == first["source_id"]
    assert learning._store.source_span(first["source_refs"][0])["stale"]


def test_explicit_new_subject_uses_original_event_and_resumes_without_overwriting(tmp_path, monkeypatch):
    from runtime.tutor import auto_path

    learning = service(tmp_path)
    domain_path(learning)
    learning.record_attempt(attempt())
    executive = TutorExecutive(learning)
    executive.reasoner.router = object()
    capability = TutorCapability(executive)
    proposed = LearningPath(
        "path:fractions",
        "Phân số",
        "Học phân số",
        (KnowledgePoint("math:v1:fractions", "Phân số"),),
        domain_id="math:v1",
    )
    seen = []

    def propose(router, *, topic_text):
        seen.append(topic_text)
        return proposed

    monkeypatch.setattr(auto_path, "propose_learning_path", propose)
    result = capability.invoke_for_root(
        arguments={"operation": "start_path", "user_text": "fabricated topic"},
        event=event(1, "Tôi muốn học phân số"),
        recent_history=[],
    )
    assert result["status"] == "completed" and not result["resumed"]
    assert seen == ["Tôi muốn học phân số"]
    assert learning.active_path().path_id == "path:fractions"
    assert len(learning.attempts()) == 1
    proposed = replace(proposed, title="Unreviewed model rewrite")
    resumed = capability.invoke_for_root(
        arguments={"operation": "start_path"},
        event=event(2, "Tiếp tục phân số"),
        recent_history=[],
    )
    assert resumed["resumed"]
    assert learning.active_path().title == "Phân số"


def test_new_subject_without_model_preserves_current_path(tmp_path):
    learning = service(tmp_path)
    domain_path(learning)
    before = learning.active_path()
    result = TutorCapability(TutorExecutive(learning)).invoke_for_root(
        arguments={"operation": "start_path"},
        event=event(1, "Học hóa học"),
        recent_history=[],
    )
    assert result == {"status": "unavailable", "reason": "curriculum_model_unavailable"}
    assert learning.active_path() == before
