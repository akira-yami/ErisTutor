# -*- coding: utf-8 -*-
"""P2: kiểm thử bất biến giữa FSRS, bằng chứng, mở rộng lộ trình và Web report."""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from runtime.capabilities.tutor import TutorCapability
from runtime.learning_core import KnowledgePoint, LearningPath, LearningService, LearningStore
from runtime.learning_core import retention
from runtime.learning_core.insights import evidence_signals
from runtime.learning_core.insights_html import render_dashboard
from runtime.learning_core.records import AssessmentAttempt, DomainSpec
from runtime.tutor.executive import TutorExecutive
from runtime.tutor.sources import text_spans


def make_service(tmp_path, *, mode="shadow"):
    store = LearningStore(tmp_path / "learning.sqlite3")
    learning = LearningService(store, retention_mode=mode)
    learning.register_domain(DomainSpec("math", "Toán", "math"))
    if not store.load_path("math:path"):
        learning.register_path(LearningPath("math:path", "Toán <b>đại số</b>", "Học đại số", (
            KnowledgePoint("math:a", "Phép cộng"), KnowledgePoint("math:b", "Phép nhân"),
        ), domain_id="math"))
    learning.activate_path("math:path")
    return learning


def attempt(n, *, kp="math:a", result="correct", assistance="independent", family=None, review=False):
    return AssessmentAttempt(
        attempt_id=f"attempt:{n}", kp_id=kp, path_id="math:path", result=result,
        evidence_type="quiz_correct" if result == "correct" else "quiz_incorrect",
        strength=1.0, interaction_id=f"q:{n}", turn_id=f"turn:{n}",
        item_family_id=family or f"family:{n}", assistance=assistance,
        recorded_at=1000.0 + n, review_recall=review,
        error_code="confused_sign" if result == "incorrect" else "",
    )


def event(text, n=1):
    return SimpleNamespace(event_id=f"user:{n}", session_id="s:p2", generation=1, text=text)


def test_fsrs_opt_in_rejects_missing_library_and_reports_degraded(tmp_path, monkeypatch):
    monkeypatch.setattr(retention, "readiness", lambda: {"available": False, "reason": "no_fsrs"})
    learning = make_service(tmp_path, mode="fsrs")
    info = learning.retention_snapshot()
    assert info["selected_mode"] == "shadow" and info["requested_mode"] == "fsrs"
    assert info["readiness"]["degraded"] is True
    with pytest.raises(RuntimeError, match="fsrs_not_ready"):
        learning.set_retention_mode("fsrs")
    assert learning._store.get_meta("learning_retention_mode_v1") is None
    assert learning.set_retention_mode("heuristic")["selected_mode"] == "heuristic"
    learning._store.close()
    restored = make_service(tmp_path)
    assert restored.retention_snapshot()["selected_mode"] == "heuristic"


def test_fsrs_opt_in_when_available_persists_and_restores(tmp_path, monkeypatch):
    monkeypatch.setattr(retention, "readiness", lambda: {"available": True, "reason": ""})
    learning = make_service(tmp_path)
    assert learning.set_retention_mode("fsrs")["selected_mode"] == "fsrs"
    learning._store.close()
    restored = make_service(tmp_path)
    assert restored.retention_snapshot()["selected_mode"] == "fsrs"


def test_gaps_use_unique_independent_families_not_assisted_success(tmp_path):
    learning = make_service(tmp_path)
    assert learning.record_attempt(attempt(1, assistance="hinted"))
    summary = learning.learning_insights(now=1200)
    row = summary["knowledge"][0]
    assert row["signals"]["status"] == "needs_independent_probe"
    assert row["signals"]["independent_correct"] == 0
    assert row["signals"]["assisted_families"] == 1
    assert learning.record_attempt(attempt(2, result="incorrect", family="one"))
    assert learning.record_attempt(attempt(3, result="incorrect", family="two"))
    rows = learning.learning_insights(now=1200)["knowledge"]
    assert rows[0]["signals"]["status"] == "possible_repeated_gap"
    assert rows[0]["signals"]["independent_incorrect"] == 2
    assert rows[1]["signals"]["status"] == "unassessed"
    assert learning.record_attempt(attempt(4, family="two"))
    # Một họ bài chỉ được tính một lần theo câu trả lời mới nhất.
    assert learning.learning_insights(now=1200)["knowledge"][0]["signals"]["independent_families"] == 2


def test_correction_and_forget_recompute_insights(tmp_path):
    learning = make_service(tmp_path)
    assert learning.record_attempt(attempt(1, result="incorrect"))
    assert learning.learning_insights(now=1100)["knowledge"][0]["signals"]["status"] == "needs_repair"
    assert learning.correct_attempt("attempt:1", correction_id="correction:1", result="ungraded", now=1100)
    assert learning.learning_insights(now=1100)["knowledge"][0]["signals"]["status"] == "unassessed"
    assert learning.forget_attempt("correction:1", now=1101)
    assert learning.learning_insights(now=1102)["knowledge"][0]["signals"]["independent_families"] == 0


def test_expansion_requires_source_and_cas_and_does_not_reset_position(tmp_path):
    learning = make_service(tmp_path)
    learning.advance_kp("math:b")
    additions = [{"kp_id": "math:c", "title": "Phương trình", "prerequisites": ["math:b"]}]
    with pytest.raises(ValueError, match="sources"):
        learning.preview_expansion(additions, reason="Học thêm")
    span = text_spans(text="Từ phép cộng và phép nhân, có thể dùng phương trình để tìm ẩn số.",
                      domain_id="math", source_id="src:math", now=2000)[0]
    learning.import_source_spans([span])
    preview = learning.preview_expansion(additions, reason="Học thêm", source_refs=[span.span_id])
    assert preview["changes"]["added"] == ["math:c"]
    with pytest.raises(ValueError, match="stale_curriculum_revision"):
        learning.apply_expansion(additions, base_revision="wrong", reason="Học thêm", source_refs=[span.span_id])
    assert learning.active_path().kp_ids == ("math:a", "math:b")
    result = learning.apply_expansion(additions, base_revision=preview["base_revision"],
                                      reason="Học thêm", source_refs=[span.span_id])
    assert result["changes"]["added"] == ["math:c"]
    assert learning.active_path().kp_ids == ("math:a", "math:b", "math:c")
    assert learning.snapshot()["active_kp_id"] == "math:b"
    with pytest.raises(ValueError):
        learning.apply_expansion(additions, base_revision=preview["base_revision"],
                                 reason="Học thêm", source_refs=[span.span_id])


def test_capability_does_not_switch_fsrs_or_commit_expansion_from_model_guess(tmp_path, monkeypatch):
    learning = make_service(tmp_path)
    cap = TutorCapability(TutorExecutive(learning))
    blocked = cap.invoke_for_root(arguments={"operation": "set_retention_mode", "mode": "fsrs"},
                                  event=event("Tôi muốn học toán"), recent_history=[])
    assert blocked["status"] == "failed" and learning.retention_snapshot()["selected_mode"] == "shadow"
    blocked = cap.invoke_for_root(arguments={"operation": "apply_expansion", "additions": [{"kp_id": "math:c", "title": "C"}],
                                            "reason": "thêm", "source_refs": [], "base_revision": "abc"},
                                  event=event("Hãy học tiếp"), recent_history=[])
    assert blocked["status"] == "failed" and learning.active_path().kp_ids == ("math:a", "math:b")
    snapshot = cap.invoke_for_root(arguments={"operation": "insights"}, event=event("Xem tiến độ"), recent_history=[])
    assert snapshot["status"] == "completed" and len(snapshot["knowledge"]) == 2


def test_dashboard_escapes_user_text_and_does_not_run_script(tmp_path, monkeypatch):
    learning = make_service(tmp_path)
    html = render_dashboard(learning.learning_insights())
    assert "Toán &lt;b&gt;đại số&lt;/b&gt;" in html and "Toán <b>đại số</b>" not in html
    assert "http://" not in html and "<script" not in html
    import runtime.workspace_manager as workspaces
    monkeypatch.setattr(workspaces, "default_eris_home", lambda: tmp_path)
    cap = TutorCapability(TutorExecutive(learning))
    denied = cap.invoke_for_root(arguments={"operation": "dashboard"}, event=event("Học tiếp"), recent_history=[])
    assert denied["status"] == "failed"
    saved = cap.invoke_for_root(arguments={"operation": "dashboard"}, event=event("Mở bảng tiến độ"), recent_history=[])
    assert saved["status"] == "completed"
    path = Path(saved["path"])
    assert path.is_file() and path.read_text(encoding="utf-8").startswith("<!doctype html>")
    assert str(path).startswith(str(tmp_path))


def test_empty_learning_insights_no_subject(tmp_path):
    learning = LearningService(LearningStore(tmp_path / "empty.sqlite3"))
    assert learning.learning_insights()["assessment"] == "no_active_learning_path"
    with pytest.raises(ValueError):
        learning.learning_insights(max_kps=10000)


def test_pure_evidence_signal_does_not_count_ungraded_as_correct():
    signals = evidence_signals([
        {"attempt_id": "a", "result": "ungraded", "assistance": "independent", "item_family_id": "one"},
        {"attempt_id": "b", "result": "correct", "assistance": "answer_revealed", "item_family_id": "two"},
    ])
    assert signals["independent_correct"] == 0
    assert signals["status"] == "needs_independent_probe"


def test_due_review_is_explicit_action_not_passed_as_mastery(tmp_path):
    learning = make_service(tmp_path)
    assert learning.record_attempt(attempt(9, review=True))
    future = learning.learning_insights(now=1000 + 365 * 86400)
    row = next(item for item in future["knowledge"] if item["kp_id"] == "math:a")
    assert row["signals"]["status"] == "review_due"
    assert row["signals"]["next_action"] == "independent_recall"
    assert row["kp_id"] in [x["kp_id"] for x in future["due_review"]]
    assert future["assessment"] == "observational_not_calibrated"


def test_insights_other_path_cannot_change_active_learning_space(tmp_path):
    learning = make_service(tmp_path)
    learning.register_domain(DomainSpec("code", "Lập trình", "code"))
    learning.register_path(LearningPath("code:path", "Python", "Lập trình",
                                       (KnowledgePoint("code:one", "Biến"),), domain_id="code"))
    before = learning.snapshot()
    other = learning.learning_insights(path_id="code:path")
    assert other["path"]["path_id"] == "code:path"
    assert learning.snapshot() == before


def test_stale_source_blocks_curriculum_expansion(tmp_path):
    learning = make_service(tmp_path)
    node = [{"kp_id": "math:c", "title": "Bài mở rộng"}]
    old = text_spans(text="Dữ liệu chuẩn 1", domain_id="math", source_id="src:math")[0]
    learning.import_source_spans([old])
    assert learning.preview_expansion(node, source_refs=[old.span_id], reason="Mở rộng")["changes"]["added"]
    new = text_spans(text="Dữ liệu chuẩn đã chỉnh sửa", domain_id="math", source_id="src:math")[0]
    learning.import_source_spans([new])
    with pytest.raises(ValueError, match="stale_or_out_of_scope"):
        learning.preview_expansion(node, source_refs=[old.span_id], reason="Mở rộng")


def test_root_capability_can_approve_verified_expansion_without_reset(tmp_path):
    learning = make_service(tmp_path)
    cap = TutorCapability(TutorExecutive(learning))
    learning.advance_kp("math:b")
    source = text_spans(text="Kiến thức về phương trình ẩn số", domain_id="math", source_id="src:confirmed")[0]
    learning.import_source_spans([source])
    nodes = [{"kp_id": "math:c", "title": "Phương trình", "prerequisites": ["math:b"]}]
    request = {"additions": nodes, "source_refs": [source.span_id], "reason": "Học sâu hơn"}
    preview = cap.invoke_for_root(arguments={"operation": "preview_expansion", **request},
                                  event=event("Mở rộng Toán"), recent_history=[])
    assert preview["status"] == "completed"
    approved = cap.invoke_for_root(
        arguments={"operation": "apply_expansion", **request, "base_revision": preview["base_revision"]},
        event=event("Xác nhận thêm vào lộ trình", 2), recent_history=[],
    )
    assert approved["status"] == "completed"
    assert learning.snapshot()["active_kp_id"] == "math:b"
    assert learning.active_path().kp_ids[-1] == "math:c"


def test_root_retention_opt_in_exact_user_request(tmp_path, monkeypatch):
    monkeypatch.setattr(retention, "readiness", lambda: {"available": True, "reason": ""})
    learning = make_service(tmp_path)
    cap = TutorCapability(TutorExecutive(learning))
    result = cap.invoke_for_root(arguments={"operation": "set_retention_mode", "mode": "fsrs"},
                                 event=event("Bật FSRS cho lịch ôn"), recent_history=[])
    assert result["status"] == "completed" and result["selected_mode"] == "fsrs"
    result = cap.invoke_for_root(arguments={"operation": "set_retention_mode", "mode": "shadow"},
                                 event=event("Chuyển sang shadow", 2), recent_history=[])
    assert result["status"] == "completed" and result["selected_mode"] == "shadow"
