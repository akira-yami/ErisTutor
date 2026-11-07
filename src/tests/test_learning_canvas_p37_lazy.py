"""P3.7 — learning context gọn & Gemini lazy; không gọi mạng thật.

Test đường Root -> lưu môn -> panel mở -> Gemini; session browser đóng/hết hạn
và việc Root không nhận toàn bộ graph khi chỉ hỏi tóm tắt.
"""
from __future__ import annotations

import json
import time
from types import SimpleNamespace
import uuid

import pytest

from runtime.capabilities.canvas import CanvasCapability
from runtime.learning_canvas.service import LearningCanvasService
from runtime.learning_canvas.prefetch import ProgressiveGraphPrefetch
from runtime.learning_canvas.continuity import LearningContinuity
from runtime.learning_core import LearningStore, LearningService


class FakeFastGemini:
    def __init__(self):
        self.calls = []

    def create_chat_completion(self, *, messages, **_):
        query = json.loads(messages[-1]["content"])
        self.calls.append(query)
        if query["mode"] == "expand":
            output = {"branches": [{"title": "Kiến thức nền", "summary": "Bản nháp", "next": [
                {"title": "Ví dụ", "summary": "Mở sau"}]}]}
        else:
            output = {"targets": [{"id": x["id"], "next": [{"title": "Ví dụ", "summary": "Mở sau"}]}
                                  for x in query["targets"]]}
        return {"choices": [{"message": {"content": json.dumps(output, ensure_ascii=False)}}]}


@pytest.fixture
def active_system(tmp_path):
    store = LearningStore(tmp_path / 'learning.sqlite3')
    canvas = LearningCanvasService(LearningService(store), store=store)
    canvas.prefetch.close()
    fake = FakeFastGemini()
    canvas.prefetch = ProgressiveGraphPrefetch(canvas.graphs, adapter=fake,
                        may_run=canvas.is_active, on_change=canvas._prefetch_changed,
                        on_result=canvas._prefetch_result)
    sent = []
    host = SimpleNamespace(learning_canvas_service=canvas, _send_runtime_json=lambda packet, **_: sent.append(packet) or True)
    yield canvas, CanvasCapability(host), fake, sent
    canvas.close()
    store.close()


def request(canvas, action, **fields):
    return canvas.handle({"request_id": "p37-" + uuid.uuid4().hex, "action": action, **fields})


def settled(canvas):
    for _ in range(90):
        thread = canvas.prefetch._thread
        if thread is None:
            return
        thread.join(0.05)
    raise AssertionError('prefetch worker not finished')


def root_tool(tool, op, **fields):
    return tool.invoke_for_root(arguments={"operation": op, **fields},
              event=SimpleNamespace(kind="user_turn", text="Yêu cầu học", session_id="test"))


def test_closed_panel_root_creates_only_short_brief_and_deferred_job(active_system):
    canvas, tool, gemini, sent = active_system
    assert not canvas.is_active()
    result = root_tool(tool, "graph_create", title="Mạng máy tính")
    assert result["status"] == "completed"
    assert result["learning_job"]["status"] == "deferred"
    assert result["panel_active"] is False
    assert "learning_canvas" not in result
    assert sent == [] and gemini.calls == []
    brief = canvas.brief()
    assert brief == {"subjects": [{"title": "Mạng máy tính", "at": "Mạng máy tính"}],
                     "other_subjects": 0}
    assert 'nodes' not in json.dumps(root_tool(tool, 'learning_brief'), ensure_ascii=False)
    assert canvas.continuity.snapshot()["jobs"][0]["status"] == "deferred"


def test_open_only_starts_job_when_its_subject_selected(active_system):
    canvas, tool, gemini, sent = active_system
    created = root_tool(tool, "graph_create", title="Spring Boot")
    group_id = created["graph_update"]["group_id"]
    assert not gemini.calls
    opened = request(canvas, "open", client_id="web-browser-1")
    assert opened["status"] == "ok" and canvas.is_active()
    settled(canvas)
    assert not gemini.calls  # Đang xem Java, không prefetch môn Spring mới.
    selected = request(canvas, "graph_select", group_id=group_id,
                       base_graph_revision=canvas.graphs.snapshot()["revision"])
    assert selected["status"] == "ok"
    settled(canvas)
    assert len(gemini.calls) == 1 and gemini.calls[0]["mode"] == "expand"
    assert canvas.continuity.snapshot()["jobs"][0]["status"] == "ready"
    assert len(canvas.graphs.snapshot()["graph"]["nodes"]) > 1


def test_hidden_tab_and_expired_lease_block_new_calls(active_system):
    canvas, tool, gemini, _ = active_system
    request(canvas, "open", client_id="test-tab-unique")
    root_tool(tool, "graph_create", title="Kỹ thuật số")
    settled(canvas)
    old_calls = len(gemini.calls)
    assert request(canvas, "session_close", client_id="test-tab-unique")["status"] == "ok"
    assert not canvas.is_active()
    assert not canvas.prefetch.request('ignored', 'ignored')
    assert root_tool(tool, "graph_create", title="Cấu trúc dữ liệu")["learning_job"]["status"] == "deferred"
    canvas._view_session("open", "test-tab-unique")
    canvas._viewers["test-tab-unique"] = time.monotonic() - 1
    assert not canvas.is_active()
    assert not canvas.prefetch.request('ignored', 'ignored')
    settled(canvas)
    assert len(gemini.calls) == old_calls


def test_multiple_tabs_keep_lease_until_last_one_closes(active_system):
    canvas, _, _, _ = active_system
    for client in ('tab-11111111', 'tab-22222222'):
        assert request(canvas, 'open', client_id=client)['status'] == 'ok'
    assert canvas.is_active()
    request(canvas, 'session_close', client_id='tab-11111111')
    assert canvas.is_active()
    request(canvas, 'session_close', client_id='tab-22222222')
    assert not canvas.is_active()
    assert request(canvas, 'session_ping', client_id='x')['status'] == 'error'


def test_compact_cache_invalidates_on_progress_and_survives_restart(active_system):
    canvas, tool, _, _ = active_system
    assert canvas.brief()["subjects"] == []
    ids = []
    for title in ("C++", "Spring Boot", "Mạng máy tính", "Toán", "AI"):
        ids.append(root_tool(tool, "graph_create", title=title)["graph_update"]["group_id"])
    b = canvas.brief()
    assert [s["title"] for s in b["subjects"]] == ["AI", "Toán", "Mạng máy tính", "Spring Boot"]
    assert b["other_subjects"] == 1
    assert len(json.dumps(b, ensure_ascii=False)) < 350
    assert LearningContinuity(canvas.focus.store).brief() == b
    assert canvas.brief() == b
    group = canvas.graphs._load()["groups"][0]
    canvas.record_graph_visit(group["group_id"], group["selected_node_id"], action="resumed")
    assert canvas.brief()["subjects"][0]["title"] == "C++"


def test_deferred_job_not_mistaken_for_interrupted_after_restart(active_system):
    canvas, tool, _, _ = active_system
    root_tool(tool, "graph_create", title="Giải tích")
    canvas.continuity.recover_interrupted_jobs()
    assert canvas.continuity.snapshot()["jobs"][0]["status"] == "deferred"
