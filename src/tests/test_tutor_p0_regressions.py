# -*- coding: utf-8 -*-
"""Kiểm thử hồi quy P0 Tutor: delivery identity, multi-path resume và portable FSRS."""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

from runtime.capabilities.tutor import TutorCapability
from runtime.conversation.contracts import DeliveryReceipt
from runtime.learning_core import KnowledgePoint, LearningPath, LearningService, LearningStore
from runtime.tutor.executive import TutorExecutive


def _learning(tmp_path):
    return LearningService(LearningStore(tmp_path / "tutor.sqlite3"))


def _path(code):
    return LearningPath(
        f"path:{code}", f"Lộ trình {code}", f"Học {code}",
        (KnowledgePoint(f"{code}:one", "Cơ bản", "procedure"),
         KnowledgePoint(f"{code}:two", "Nâng cao", "procedure")),
    )


def _event(index=1):
    return SimpleNamespace(event_id=f"user:{index}", session_id="session:1",
                           generation=index, text="Hãy cho tôi bài Python")


def _receipt(*, generation=1, delivery_id="delivery:1", move_id="move:1", successful=True):
    return DeliveryReceipt(delivery_id=delivery_id, move_id=move_id,
                           session_id="session:1", generation=generation,
                           successful=successful)


def test_reactivate_same_path_keeps_skill_and_session_progress(tmp_path):
    learning = _learning(tmp_path)
    path = _path("a")
    learning.register_path(path)
    assert learning.activate_path(path.path_id)
    learning.advance_kp("a:two")
    before = learning._active_kp_id
    assert learning.activate_path(path.path_id)
    assert learning._active_kp_id == before
    assert learning.activate_path(path.path_id, restart=True)
    assert learning._active_kp_id == "a:one"


def test_switch_paths_resume_after_restart_and_recover_stale_kp(tmp_path):
    learning = _learning(tmp_path)
    a, b = _path("a"), _path("b")
    learning.register_path(a)
    learning.register_path(b)
    assert learning.activate_path(a.path_id)
    learning.advance_kp("a:two")
    assert learning.activate_path(b.path_id)
    learning.advance_kp("b:two")
    assert learning.activate_path(a.path_id)
    assert learning._active_kp_id == "a:two"
    learning._store.close()

    restored = _learning(tmp_path)
    assert restored._active_path_id == a.path_id
    assert restored._active_kp_id == "a:two"
    assert restored.activate_path(b.path_id)
    assert restored._active_kp_id == "b:two"
    # Bị sửa curriculum: KP trước đó biến mất => fallback về bài hợp lệ.
    revised = LearningPath(a.path_id, a.title, a.goal,
                           (KnowledgePoint("a:three", "Mới", "procedure"),))
    # Curriculum sửa đổi cần CAS revision; không được lách guard này.
    restored._store.upsert_path(revised, expected_revision=restored._store.path_revision(a.path_id))
    assert restored.activate_path(a.path_id)
    assert restored._active_kp_id == "a:three"


def test_activity_receipt_requires_exact_bound_delivery_move_and_bundle(tmp_path, monkeypatch):
    monkeypatch.setenv("ERIS_ARTIFACT_WORKSPACE", str(tmp_path / "artifacts"))
    learning = _learning(tmp_path)
    cap = TutorCapability(TutorExecutive(learning))
    issued = cap.invoke_for_root(arguments={"operation": "activity"}, event=_event(), recent_history=[])
    assert issued["status"] == "completed"
    activity_id = issued["activity_id"]
    original = _receipt()
    assert learning._store.load_activity(activity_id)["delivered"] is False

    # Tuy cùng session+generation, receipt chưa được bind không có thẩm quyền.
    cap.observe_delivery_receipt(original)
    assert not learning._store.load_activity(activity_id)["delivered"]

    # Sai event hoặc bundle do một tool khác phát => không bind được.
    cap.bind_pending_delivery(event_id="some-other-event", session_id="session:1", generation=1,
                              delivery_id=original.delivery_id, move_id=original.move_id,
                              artifact_bundle_ids=[issued["artifact_bundle_id"]])
    cap.bind_pending_delivery(event_id="user:1", session_id="session:1", generation=1,
                              delivery_id=original.delivery_id, move_id=original.move_id,
                              artifact_bundle_ids=["bundle:not-the-activity"])
    cap.observe_delivery_receipt(original)
    assert not learning._store.load_activity(activity_id)["delivered"]

    # Đã bind hợp lệ, nhưng các receipt có move/delivery ID khác vẫn phải bị bỏ qua.
    cap.bind_pending_delivery(event_id="user:1", session_id="session:1", generation=1,
                              delivery_id=original.delivery_id, move_id=original.move_id,
                              artifact_bundle_ids=[issued["artifact_bundle_id"]])
    cap.observe_delivery_receipt(replace(original, delivery_id="delivery:foreign"))
    cap.observe_delivery_receipt(replace(original, move_id="move:foreign"))
    cap.observe_delivery_receipt(replace(original, generation=99))
    assert not learning._store.load_activity(activity_id)["delivered"]
    cap.observe_delivery_receipt(original)
    assert learning._store.load_activity(activity_id)["delivered"]
    cap.observe_delivery_receipt(original)
    assert not cap._pending_activity_receipts


def test_failed_receipt_does_not_mark_activity_delivered(tmp_path, monkeypatch):
    monkeypatch.setenv("ERIS_ARTIFACT_WORKSPACE", str(tmp_path / "artifacts"))
    learning = _learning(tmp_path)
    cap = TutorCapability(TutorExecutive(learning))
    issued = cap.invoke_for_root(arguments={"operation": "activity"}, event=_event(), recent_history=[])
    cap.bind_pending_delivery(event_id="user:1", session_id="session:1", generation=1,
                              delivery_id="delivery:1", move_id="move:1",
                              artifact_bundle_ids=[issued["artifact_bundle_id"]])
    cap.observe_delivery_receipt(_receipt(successful=False))
    cap.observe_delivery_receipt(_receipt(successful=True))  # Receipt muộn cùng identity không đảo kết quả.
    assert not learning._store.load_activity(issued["activity_id"])["delivered"]


def test_fsrs_is_pinned_and_portable_build_checks_real_interpreter():
    root = Path(__file__).resolve().parents[2]
    requirements = (root / "requirements.txt").read_text(encoding="utf-8")
    build = (root / "_fullbuild.py").read_text(encoding="utf-8-sig")
    setup = (root / "eris_setup.py").read_text(encoding="utf-8-sig")
    assert "fsrs==6.3.2" in requirements
    assert 'ensure_tutor_fsrs_dependency(portable_dst)' in build
    assert '[py, "-m", "pip", "install", "fsrs==6.3.2"]' in build
    assert 'pip", "install", "-r", str(req_file)' in setup
