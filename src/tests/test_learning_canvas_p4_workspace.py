"""P3.8/P4: kiểm tra xuyên suốt Root/Web Canvas/Gemini giả, không cần mạng thật."""
from __future__ import annotations

import json
import threading
import time
import uuid
from pathlib import Path

import pytest

from runtime.learning_canvas.lesson import LearningLessonStore, OnDemandLessonWorker, validate_lesson
from runtime.learning_canvas.service import LearningCanvasService
from runtime.learning_core import LearningService, LearningStore


class FakeLessonGemini:
    def __init__(self, blocks=None, gate=None):
        self.calls = []
        self.gate = gate
        self.entered = threading.Event()
        self.blocks = blocks or [
            {"kind": "text", "title": "Khái niệm", "text": "Mô tả dễ hiểu và ngắn."},
            {"kind": "steps", "title": "Thử lần lượt", "items": ["Đọc", "Thực hành"]},
            {"kind": "check", "title": "Tự kiểm tra", "question": "Giải thích?", "answer": "Giải thích lại."},
        ]

    def create_chat_completion(self, *, messages, **kwargs):
        query = json.loads(messages[-1]["content"])
        self.calls.append((query, kwargs))
        self.entered.set()
        if self.gate:
            assert self.gate.wait(timeout=5), "fake Gemini not released"
        return {"choices": [{"message": {"content": json.dumps({"blocks": self.blocks}, ensure_ascii=False)}}]}


def request(canvas, action, **fields):
    return canvas.handle({"action": action, "request_id": "p4-" + uuid.uuid4().hex, **fields})


def finished(worker):
    for _ in range(100):
        thread = worker._thread
        if thread is None:
            return
        thread.join(0.05)
    raise AssertionError("lesson worker did not finish")


@pytest.fixture
def setup(tmp_path):
    db = LearningStore(tmp_path / "learning.sqlite3")
    service = LearningCanvasService(LearningService(db), store=db)
    service.prefetch.close()  # Chỉ mock Gemini bài học, không tạo request đồ thị thật.
    service.prefetch.request = lambda *args, **kwargs: False
    fake = FakeLessonGemini()
    service.lesson_worker.close()
    service.lesson_worker = OnDemandLessonWorker(service.lessons, service.graphs,
        may_run=service.is_active, on_change=service._lesson_changed, adapter=fake)
    sent = []
    service.on_state = sent.append
    yield service, fake, sent, db
    service.close()
    db.close()


def add_topic(service, title="Spring Boot"):
    graph = service.graphs.create(title, base_revision=service.graphs.snapshot()["revision"])
    return graph["group_id"], graph["selected_node_id"]


def test_closed_panel_does_not_create_lesson_or_call_gemini(setup):
    service, fake, _, _ = setup
    group_id, node_id = add_topic(service)
    assert not service.is_active()
    assert not service.lesson_worker.request(group_id, node_id)
    assert fake.calls == []
    assert service.brief()["subjects"] == []  # Root không nạp bài học khi chat thường.


def test_open_panel_on_demand_then_cache_restart(setup):
    service, fake, sent, db = setup
    group_id, node_id = add_topic(service)
    assert request(service, "open", client_id="tab-p4-11111111")["status"] == "ok"
    finished(service.lesson_worker)
    assert len(fake.calls) == 1
    assert list(fake.calls[0][0]) == ["topic", "trail"]  # Không gửi mastery/history/chat.
    assert all(key not in fake.calls[0][0] for key in ("mastery", "conversation", "history"))
    packet = service.state_packet()["learning_canvas"]["dynamic_lesson"]
    assert packet["status"] == "ready" and packet["content"]["node_id"] == node_id
    assert packet["content"]["verified"] is False
    assert sent and sent[-1]["learning_canvas"]["dynamic_lesson"]["status"] == "ready"
    assert not service.lesson_worker.request(group_id, node_id)
    assert len(fake.calls) == 1
    # Store per-node phục hồi trên DB, không cần Gemini khi Eris khởi động lại.
    assert LearningLessonStore(db).get(group_id, node_id) == packet["content"]
    request(service, "session_close", client_id="tab-p4-11111111")
    assert not service.lesson_worker.request(group_id, node_id)


def test_request_requires_current_node_and_active_panel(setup):
    service, fake, _, _ = setup
    group_id, node_id = add_topic(service)
    closed = request(service,"lesson_request",group_id=group_id,node_id=node_id)
    assert closed["status"] == "error"
    request(service, "open", client_id="tab-p4-22222222")
    wrong = request(service,"lesson_request",group_id=group_id,node_id="other")
    assert wrong["status"] == "error"
    finished(service.lesson_worker)
    done = request(service,"lesson_request",group_id=group_id,node_id=node_id)
    assert done["status"] == "ok"
    assert done["lesson_queued"] is False  # cache có rồi, không gọi lại.
    assert len(fake.calls) == 1


def test_switch_subject_during_generation_never_overwrites_selected_lesson(setup):
    service, _, _, _ = setup
    group_a, node_a = add_topic(service, "Mạng máy tính")
    group_b, node_b = add_topic(service, "Giải tích")
    gate = threading.Event()
    fake = FakeLessonGemini(gate=gate)
    service.lesson_worker.close()
    service.lesson_worker = OnDemandLessonWorker(service.lessons, service.graphs,
        may_run=service.is_active, on_change=service._lesson_changed, adapter=fake)
    # Mở B trước; chọn A (Gemini bắt đầu), sau đó chọn B lúc A chưa trả.
    request(service, "open", client_id="tab-p4-33333333")
    # Hiện selected B nên A không được phép gọi thẳng qua lesson_request.
    selected = request(service,"graph_select",group_id=group_a,
                       base_graph_revision=service.graphs.snapshot()["revision"])
    assert selected["status"] == "ok"
    assert fake.entered.wait(timeout=2)
    selected = request(service,"graph_select",group_id=group_b,
                       base_graph_revision=service.graphs.snapshot()["revision"])
    assert selected["status"] == "ok"
    gate.set()
    finished(service.lesson_worker)
    assert service.graphs.snapshot()["selected_group_id"] == group_b
    assert service.lessons.get(group_a, node_a) is not None
    current = service.state_packet()["learning_canvas"]["dynamic_lesson"]
    assert current["content"]["group_id"] == group_b
    assert current["content"]["node_id"] == node_b
    assert all("mastery" not in json.dumps(row,ensure_ascii=False) for row in fake.calls)


def test_close_panel_while_gemini_work_inflight_makes_no_ui_notification(setup):
    service, _, sent, _ = setup
    group, _ = add_topic(service)
    gate = threading.Event()
    fake = FakeLessonGemini(gate=gate)
    service.lesson_worker.close()
    service.lesson_worker = OnDemandLessonWorker(service.lessons, service.graphs,
        may_run=service.is_active, on_change=service._lesson_changed, adapter=fake)
    request(service, "open", client_id="tab-p4-44444444")
    assert fake.entered.wait(timeout=2)
    sent.clear()
    request(service, "session_close", client_id="tab-p4-44444444")
    gate.set()
    finished(service.lesson_worker)
    assert not sent
    assert service.lessons.get(group, service.graphs.snapshot()["graph"]["selected_node_id"])


@pytest.mark.parametrize("blocks", [
    [{"kind":"text","title":"Khái niệm","text":"Vật thể toán học"},
     {"kind":"formula","title":"Công thức","latex":r"2^{32-n}","explanation":"Số địa chỉ"}],
    [{"kind":"code","title":"Ví dụ","language":"python","text":"print(1 + 1)"},
     {"kind":"check","title":"Kiểm tra","question":"Output?","answer":"2"}],
    [{"kind":"text","title":"Khái niệm","text":"Spring Boot"},
     {"kind":"steps","title":"Quy trình","items":["Khởi tạo project","Tạo controller"]}],
])
def test_math_code_theory_blocks_are_independently_validated(blocks):
    lesson = validate_lesson({"blocks":blocks},group_id="topic-x",node_id="node-x",title="Demo")
    assert [entry["kind"] for entry in lesson["blocks"]] == [entry["kind"] for entry in blocks]
    assert lesson["verified"] is False


@pytest.mark.parametrize("block", [
    {"kind":"html","title":"Inject","text":"<script>alert(1)</script>"},
    {"kind":"formula","title":"X","latex":r"\href{https://evil.site}{Click}"},
    {"kind":"steps","title":"X","items":["too short"]},
    {"kind":"code","title":"X","text":""},
])
def test_bad_or_unsafe_blocks_rejected(block):
    with pytest.raises(ValueError):
        validate_lesson({"blocks":[block]},group_id="topic",node_id="node",title="X")


def test_bridge_and_web_rendering_are_whitelisted_and_text_only():
    root = Path(__file__).resolve().parents[1]
    cs = (root / "body/UdpListener.WebLearningCanvas.cs").read_text(encoding="utf-8")
    js = (root / "body/richchat/canvas.js").read_text(encoding="utf-8")
    assert '"lesson_request"' in cs
    assert "function renderDynamicLesson" in js
    assert "innerHTML" not in js
    assert "vendor/mathjax.js" in js
    assert "renderDynamicLesson(container,node)" in js


def test_validate_lesson_accepts_options_and_explanation_in_check():
    block = {
        "kind": "check",
        "title": "Trắc nghiệm nhanh",
        "question": "Dependency là gì?",
        "options": ["A. Đối tượng phụ thuộc", "B. Biến int", "C. Từ khóa return"],
        "answer": "A",
        "explanation": "Dependency là đối tượng mà đối tượng khác cần."
    }
    lesson = validate_lesson({"blocks": [block]}, group_id="topic-x", node_id="node-x", title="Demo")
    res = lesson["blocks"][0]
    assert res["kind"] == "check"
    assert len(res["options"]) == 3
    assert res["answer"] == "A"
    assert res["explanation"] == "Dependency là đối tượng mà đối tượng khác cần."
