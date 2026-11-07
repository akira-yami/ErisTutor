"""P3.6: lịch sử học, Root -> Gemini -> Root event, không gian nhiều môn.

Chỉ fake Gemini; không gọi mạng, không chấm mastery dựa trên lượt đọc.
"""
from __future__ import annotations

import json
import time
from types import SimpleNamespace
import uuid

import pytest

from runtime.capabilities.canvas import CanvasCapability
from runtime.learning_canvas.continuity import LearningContinuity
from runtime.learning_canvas.prefetch import ProgressiveGraphPrefetch
from runtime.learning_canvas.service import LearningCanvasService
from runtime.learning_core import LearningService, LearningStore


class FakeGemini:
    def create_chat_completion(self, *, messages, **kwargs):
        request = json.loads(messages[-1]["content"])
        if request["mode"] == "expand":
            data = {"branches": [
                {"title": "Tổng quan", "summary": "Nháp", "next": [{"title": "Khái niệm", "summary": "Nháp"}]},
                {"title": "Thực hành", "summary": "Nháp", "next": [{"title": "Ví dụ", "summary": "Nháp"}]}]}
        else:
            data = {"targets": [{"id": x["id"], "next": [{"title": "Tầng sâu", "summary": "Nháp"}]}
                                for x in request["targets"]]}
        return {"choices": [{"message": {"content": json.dumps(data, ensure_ascii=False)}}]}


class BrokenGemini:
    def create_chat_completion(self, **kwargs):
        raise ConnectionError("offline")


@pytest.fixture
def system(tmp_path):
    store = LearningStore(tmp_path / "learning.sqlite3")
    canvas = LearningCanvasService(LearningService(store), store=store)
    canvas._view_session("open", "test-p36-browser")  # Test cũ giả lập panel đang mở.
    canvas.prefetch.close()
    canvas.prefetch = ProgressiveGraphPrefetch(canvas.graphs, adapter=FakeGemini(),
                  on_change=canvas._prefetch_changed, on_result=canvas._prefetch_result)
    yield canvas
    canvas.close()
    store.close()


def wait_done(canvas):
    until = time.monotonic() + 5
    while time.monotonic() < until:
        thread = canvas.prefetch._thread
        if thread is None:
            return
        thread.join(timeout=0.05)
    raise AssertionError("worker không kết thúc")


def request(canvas, action, **fields):
    return canvas.handle({"request_id": "p36-" + uuid.uuid4().hex, "action": action, **fields})


def test_ui_can_switch_subject_resume_and_store_history_after_restart(system):
    first = request(system, "graph_create", title="Mạng máy tính", base_graph_revision=0)
    wait_done(system)
    g1 = first["graph_update"]["group_id"]
    root = first["graph_update"]["node_id"]
    child = system.graphs.snapshot()["graph"]["nodes"][1]["id"]
    assert request(system, "graph_select", group_id=g1, node_id=child,
                   base_graph_revision=system.graphs.snapshot()["revision"])["status"] == "ok"
    wait_done(system)
    second = request(system, "graph_create", title="Đại số", base_graph_revision=system.graphs.snapshot()["revision"])
    wait_done(system)
    assert request(system, "graph_select", group_id=g1,
                   base_graph_revision=system.graphs.snapshot()["revision"])["status"] == "ok"
    ws = system.graphs.snapshot()
    assert ws["graph"]["selected_node_id"] == child
    assert request(system, "mark_viewed", group_id=g1, node_id=child)["status"] == "ok"
    history = system.continuity.snapshot(group_id=g1)
    assert history["nodes"][g1+"/"+child]["status"] == "viewed"
    assert history["subjects"][g1]["last_node_id"] == child
    assert second["graph_update"]["group_id"] in history["subjects"]
    assert not system.focus.store.load_attempts()  # Không biến lượt xem thành evidence.
    reloaded = LearningContinuity(system.focus.store).snapshot(group_id=g1)
    assert reloaded["nodes"][g1+"/"+child]["status"] == "viewed"
    assert any(event["action"] == "status_viewed" for event in reloaded["events"])
    assert root != child


def test_viewed_requires_selected_node_and_never_grants_mastery(system):
    r = request(system, "graph_create", title="An toàn thông tin", base_graph_revision=0)
    wait_done(system)
    g = r["graph_update"]["group_id"]
    root = r["graph_update"]["node_id"]
    sibling = system.graphs.snapshot()["graph"]["nodes"][1]["id"]
    assert request(system, "mark_viewed", group_id=g, node_id=sibling)["message"] == "canvas_focus_changed"
    assert request(system, "mark_viewed", group_id=g, node_id=root)["status"] == "ok"
    with pytest.raises(ValueError, match="unverified_mastery_forbidden"):
        system.continuity.status(g, root, "mastered")
    assert system.continuity.snapshot(group_id=g)["nodes"][g+"/"+root]["status"] == "viewed"


def test_root_graph_create_starts_gemini_and_queues_completion_without_auto_speaking(system):
    received = []
    system.on_event = received.append
    sent = []
    host = SimpleNamespace(learning_canvas_service=system,
                _send_runtime_json=lambda packet, **kwargs: sent.append(packet) or True)
    root = CanvasCapability(host)
    event = SimpleNamespace(kind="user_turn", text="Tạo bản đồ C++", session_id="s")
    before = system.graphs.snapshot()["selected_group_id"]
    output = root.invoke_for_root(arguments={"operation":"graph_create", "title":"C++"}, event=event)
    assert output["status"] == "completed" and output["graph_update"]["existing"] is False
    assert output["learning_job"]["job_id"]
    wait_done(system)
    assert system.graphs.snapshot()["selected_group_id"] == before  # Root không giật focus.
    assert any(x["action"] == "graph_ready" and x["job_source"] == "root" for x in received)
    assert system.continuity.snapshot()["jobs"][0]["status"] == "ready"
    assert not any("should_speak" in event for event in received)  # Root mới quyết định lời nói.
    again = root.invoke_for_root(arguments={"operation":"graph_create", "title":"c++"}, event=event)
    assert again["graph_update"]["existing"] is True and "learning_job" not in again
    assert len(system.graphs._load()["groups"]) == 1
    history = root.invoke_for_root(arguments={"operation":"learning_history"}, event=event)
    assert history["status"] == "completed" and history["learning_continuity"]["subjects"]


def test_failed_gemini_job_reports_failure_but_preserves_graph(system):
    system.prefetch.close()
    system.prefetch = ProgressiveGraphPrefetch(system.graphs, adapter=BrokenGemini(),
                     on_change=system._prefetch_changed, on_result=system._prefetch_result)
    received = []
    system.on_event = received.append
    host = SimpleNamespace(learning_canvas_service=system,
                           _send_runtime_json=lambda *_, **__: True)
    result = CanvasCapability(host).invoke_for_root(arguments={"operation":"graph_create","title":"Sinh học"},
                                                event=SimpleNamespace(kind="user_turn", text="Tạo môn Sinh",session_id="s"))
    wait_done(system)
    assert result["status"] == "completed"
    assert system.continuity.snapshot()["jobs"][0]["status"] == "unavailable"
    assert any(e["action"] == "graph_failed" for e in received)
    assert len(system.graphs._load()["groups"]) == 1
    assert not system.focus.store.load_attempts()


def test_legacy_java_practice_status_cannot_be_inferred_from_open(system):
    node = system.pack.path.kp_ids[1]
    result = request(system, "navigate", node_id=node, base_revision=0)
    assert result["status"] == "ok"
    progress = system.continuity.snapshot(group_id=system.pack.group_id)
    assert progress["nodes"][system.pack.group_id+"/"+node]["status"] == "exploring"
    assert progress["events"][0]["action"] == "navigated"


def test_end_to_end_canvas_job_reaches_root_event_queue(system):
    from runtime.learning_canvas.events import connect_canvas_host
    wakeups = []
    packets = []
    host = SimpleNamespace(learning_canvas_service=system, conversation_kernel=None,
        _send_runtime_json=lambda packet, **kwargs: packets.append(packet) or True,
        _wake_autonomous_loop=lambda reason: wakeups.append(reason))
    connect_canvas_host(host)
    tool = CanvasCapability(host)
    result = tool.invoke_for_root(arguments={"operation": "graph_create", "title": "Spring Security"},
        event=SimpleNamespace(kind="user_turn", text="Tạo môn Spring Security", session_id="s"))
    wait_done(system)
    assert result["status"] == "completed"
    pending = list(host._pending_conversation_capability_events)
    assert len(pending) == 1
    assert pending[0]["kind"] == "tutor_result"
    assert pending[0]["metadata"]["canvas_action"] == "graph_ready"
    assert pending[0]["metadata"]["canvas_capability"]["graph_group_id"] == result["graph_update"]["group_id"]
    assert wakeups == ["learning_canvas:graph_ready"]
    assert all(packet.get("should_speak") is False for packet in packets)


def test_completion_after_subject_switch_does_not_change_active_topic(system):
    from threading import Event
    class SlowFake(FakeGemini):
        started, release = Event(), Event()
        def create_chat_completion(self, **kwargs):
            self.started.set()
            assert self.release.wait(3)
            return super().create_chat_completion(**kwargs)
    system.prefetch.close()
    slow = SlowFake()
    system.prefetch = ProgressiveGraphPrefetch(system.graphs, adapter=slow,
        on_change=system._prefetch_changed, on_result=system._prefetch_result)
    host = SimpleNamespace(learning_canvas_service=system,
                           _send_runtime_json=lambda *_, **__: True)
    root = CanvasCapability(host)
    event = SimpleNamespace(kind="user_turn", text="Tạo môn", session_id="s")
    first = root.invoke_for_root(arguments={"operation":"graph_create", "title":"Hệ điều hành"}, event=event)
    assert slow.started.wait(2)
    selected_before = system.graphs.snapshot()["selected_group_id"]
    # Người dùng chưa chọn môn này; công việc Gemini đang chạy không được tự mở panel.
    assert selected_before == system.pack.group_id
    slow.release.set()
    wait_done(system)
    assert system.graphs.snapshot()["selected_group_id"] == selected_before
    assert first["learning_job"]["job_id"] == system.continuity.snapshot()["jobs"][0]["job_id"]


def test_ui_create_existing_topic_reopens_instead_of_duplicating(system):
    first = request(system, "graph_create", title="Spring Data JPA", base_graph_revision=0)
    wait_done(system)
    second = request(system, "graph_create", title="spring data jpa",
                     base_graph_revision=system.graphs.snapshot()["revision"])
    assert second["status"] == "ok"
    assert second["graph_update"]["existing"] is True
    assert "learning_job" not in second
    assert first["graph_update"]["group_id"] == second["graph_update"]["group_id"]
    assert len(system.graphs._load()["groups"]) == 1


def test_study_duration_is_backend_rate_limited_and_not_mastery(system):
    r = request(system, "graph_create", title="Thuật toán", base_graph_revision=0)
    wait_done(system)
    group_id, node_id = r["graph_update"]["group_id"], r["graph_update"]["node_id"]
    first = request(system, "study_heartbeat", group_id=group_id, node_id=node_id)
    second = request(system, "study_heartbeat", group_id=group_id, node_id=node_id)
    assert first["status"] == second["status"] == "ok"
    progress = system.continuity.snapshot(group_id=group_id)["nodes"][group_id+"/"+node_id]
    assert progress["estimated_seconds"] == 30  # Không cộng 2 lần trong một cửa sổ 25 giây.
    assert progress["status"] == "exploring"
    assert not system.focus.store.load_attempts()


def test_interrupted_job_is_recovered_on_restart(tmp_path):
    store = LearningStore(tmp_path / "restart.sqlite3")
    c = LearningContinuity(store)
    c.queue_job("lg:old", "topic-abc", "topic-abc:root", title="Môn cũ")
    assert c.snapshot()["jobs"][0]["status"] == "queued"
    LearningContinuity(store).recover_interrupted_jobs()
    assert c.snapshot()["jobs"][0]["status"] == "interrupted"
    store.close()
