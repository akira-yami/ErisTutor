"""P3.5: nhánh ít nhưng mở sâu, Gemini chạy nền/không giành focus."""
from __future__ import annotations

import json
import threading
import time
from types import SimpleNamespace

import pytest

from runtime.learning_canvas.service import LearningCanvasService
from runtime.learning_canvas.prefetch import ProgressiveGraphPrefetch
from runtime.learning_core import LearningService, LearningStore
from runtime.capabilities.canvas import CanvasCapability


class FakeGemini:
    def __init__(self):
        self.calls = []

    def create_chat_completion(self, *, messages, **_):
        request = json.loads(messages[1]["content"])
        self.calls.append(request)
        if request["mode"] == "expand":
            payload = {"branches": [
                {"title": "Khái niệm cơ bản", "summary": "Khung để tìm hiểu thêm", "next": [
                    {"title": "Ví dụ thực tế", "summary": "Bài tập nhỏ"},
                    {"title": "Cơ chế", "summary": "Cách vận hành"}]},
                {"title": "Thực hành", "summary": "Thử áp dụng", "next": [
                    {"title": "Bước đầu tiên", "summary": "Học từng bước"}]}]}
        else:
            payload = {"targets": [{"id": n["id"], "next": [
                {"title": "Tầng tiếp theo", "summary": "Đề xuất mới"}]}
                 for n in request["targets"]]}
        return {"choices": [{"message": {"content": json.dumps(payload, ensure_ascii=False)}}]}


@pytest.fixture
def system(tmp_path):
    store = LearningStore(tmp_path / "learning.sqlite3")
    canvas = LearningCanvasService(LearningService(store), store=store)
    canvas._view_session("open", "test-p35-browser")  # Test P3.5 giả lập panel đang mở.
    canvas.prefetch.close()  # thay adapter thật bằng fake để test không dùng mạng
    fake = FakeGemini()
    canvas.prefetch = ProgressiveGraphPrefetch(canvas.graphs, adapter=fake)
    yield canvas, fake
    canvas.close()
    store.close()


def wait_done(prefetch, timeout=5):
    until = time.monotonic() + timeout
    while time.monotonic() < until:
        thread = prefetch._thread
        if thread is None:
            return
        thread.join(timeout=.1)
    raise AssertionError("Gemini worker did not settle")


def test_progressive_only_reveals_selected_layer_and_hides_next(system):
    canvas, fake = system
    result = canvas.handle({"request_id": "create-p35", "action": "graph_create",
                            "title": "Spring Boot", "base_graph_revision": 0})
    assert result["status"] == "ok"
    wait_done(canvas.prefetch)
    workspace = canvas.graphs.snapshot()
    group = workspace["graph"]
    assert len(group["nodes"]) == 3  # root và đúng 2 nhánh đầu
    assert "prepared" not in group
    assert len(group["prepared_counts"]) == 2
    assert "Ví dụ thực tế" not in json.dumps(group, ensure_ascii=False)
    assert fake.calls[0]["mode"] == "expand"
    first_child = group["nodes"][1]["id"]
    selection = canvas.handle({"request_id": "select-p35", "action": "graph_select",
                               "group_id": group["group_id"], "node_id": first_child,
                               "base_graph_revision": workspace["revision"]})
    assert selection["status"] == "ok"
    promoted = selection["learning_canvas"]["graph_workspace"]["graph"]
    assert promoted["selected_node_id"] == first_child
    assert [n["title"] for n in promoted["nodes"] if n["parent_id"] == first_child] == [
        "Ví dụ thực tế", "Cơ chế"]
    assert all(n["origin"] == "gemini_draft" for n in promoted["nodes"] if n["parent_id"] == first_child)
    wait_done(canvas.prefetch)
    assert fake.calls[-1]["mode"] == "prepare"
    assert canvas.focus.snapshot()["node_id"] == canvas.pack.path.kp_ids[0]
    assert not canvas.focus.store.load_attempts()


def test_progressive_persists_hidden_branches_and_p3_compatibility(system):
    canvas, _ = system
    result = canvas.handle({"request_id": "create-2", "action": "graph_create",
                            "title": "Đại số", "base_graph_revision": 0})
    wait_done(canvas.prefetch)
    original = canvas.graphs.snapshot()["graph"]
    new = LearningCanvasService(canvas.learning, store=canvas.focus.store)
    new.prefetch.close()  # không gọi mạng
    reloaded = new.graphs.snapshot()["graph"]
    assert reloaded["prepared_counts"] == original["prepared_counts"]
    first = reloaded["nodes"][1]
    new.graphs.select(reloaded["group_id"], node_id=first["id"],
                      base_revision=new.graphs.snapshot()["revision"])
    assert len(new.graphs.snapshot()["graph"]["nodes"]) > len(original["nodes"])
    new.close()


def test_old_gemini_response_cannot_steal_focus_or_overwrite_other_topic(system):
    canvas, _ = system
    class SlowGemini(FakeGemini):
        started = threading.Event()
        resume = threading.Event()
        def create_chat_completion(self, **kwargs):
            self.started.set()
            if not self.resume.wait(timeout=5):
                raise TimeoutError("test timed out")
            return super().create_chat_completion(**kwargs)
    canvas.prefetch.close()
    slow = SlowGemini()
    canvas.prefetch = ProgressiveGraphPrefetch(canvas.graphs, adapter=slow)
    first = canvas.handle({"request_id": "a1", "action": "graph_create", "title": "Toán",
                           "base_graph_revision": 0})
    assert slow.started.wait(timeout=2)
    first_graph = first["graph_update"]["group_id"]
    second = canvas.handle({"request_id": "a2", "action": "graph_create", "title": "Vật lý",
                            "base_graph_revision": 1})
    second_graph = second["graph_update"]["group_id"]
    slow.resume.set()
    wait_done(canvas.prefetch)
    ws = canvas.graphs.snapshot()
    assert ws["selected_group_id"] == second_graph
    assert not any(n["parent_id"] for n in canvas.graphs._load()["groups"][0]["nodes"])
    assert len(ws["graph"]["nodes"]) > 1
    assert ws["graph"]["group_id"] != first_graph


def test_manual_edit_survives_prepared_and_root_prefetch_has_no_navigation(system):
    canvas, _ = system
    create = canvas.handle({"request_id": "a3", "action": "graph_create", "title": "Mạng máy tính",
                            "base_graph_revision": 0})
    wait_done(canvas.prefetch)
    group = canvas.graphs.snapshot()["graph"]
    child = group["nodes"][1]["id"]
    # Thêm bằng tay một nhánh cũng không biến thành mastery.
    additions = canvas.graphs.expand(group["group_id"], child,
        [{"title": "Câu hỏi riêng", "summary": "Tự đặt"}],
        base_revision=canvas.graphs.snapshot()["revision"], follow=False)
    assert additions[0]["origin"] == "user"
    promoted = canvas.graphs.select(group["group_id"], node_id=child,
                                     base_revision=canvas.graphs.snapshot()["revision"])
    assert len(promoted) == 2
    assert any(n["title"] == "Câu hỏi riêng" for n in canvas.graphs.snapshot()["graph"]["nodes"])
    host = SimpleNamespace(learning_canvas_service=canvas,
                           _send_runtime_json=lambda packet, **_: True)
    root = CanvasCapability(host)
    event = SimpleNamespace(kind="user_turn", session_id="main", text="Học mạng")
    result = root.invoke_for_root(arguments={"operation": "graph_prefetch", "group_id": group["group_id"],
                                             "node_id": child}, event=event)
    assert result["status"] == "completed"
    assert canvas.graphs.snapshot()["graph"]["selected_node_id"] == child
    assert not canvas.focus.store.load_attempts()
    wait_done(canvas.prefetch)


def test_gemini_invalid_payload_is_atomic(system):
    canvas, _ = system
    create = canvas.graphs.create("Lý thuyết", base_revision=0)
    before = canvas.graphs.snapshot()
    with pytest.raises(ValueError):
        canvas.graphs.apply_gemini(create["group_id"], create["selected_node_id"],
            {"branches": [{"title": "X"}, {"title": "X"}]})
    assert canvas.graphs.snapshot() == before
