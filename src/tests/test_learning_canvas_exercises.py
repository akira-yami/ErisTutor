from __future__ import annotations

import json
import threading
import time
import uuid
from types import SimpleNamespace
import socket

import pytest

from runtime.learning_canvas.service import LearningCanvasService
from runtime.learning_core import LearningService, LearningStore
from runtime.incoming_protocol_runtime import IncomingProtocolRuntimeMixin
from runtime.learning_canvas.inbox import CanvasControlInbox
from transport.udp import UdpTransport

NODE = "javadi:v1:constructor"


class JavaStub:
    def __init__(self):
        self.started, self.release = threading.Event(), threading.Event()
        self.release.set()
        self.calls = 0
        self.receipt = {"status": "completed", "result": "partial", "tests": [
            {"id": "delegates", "description": "Delegation", "passed": True},
            {"id": "null_guard", "description": "Null guard", "passed": False}]}

    def execute(self, code, definition):
        self.calls += 1
        self.started.set()
        assert self.release.wait(3)
        return self.receipt


@pytest.fixture
def canvas(tmp_path):
    store = LearningStore(tmp_path / "learning.sqlite3")
    stub = JavaStub()
    service = LearningCanvasService(LearningService(store), store=store, java_workspace=stub)
    service.test_events = []
    service.on_event = service.test_events.append
    yield service
    stub.release.set()
    if service.exercises._future:
        service.exercises._future.result(timeout=4)
    service.close()
    store.close()


def send(canvas, action, **kwargs):
    return canvas.handle({"request_id": "test-" + uuid.uuid4().hex, "action": action, **kwargs})


def begin(canvas, kind="choice"):
    if canvas.focus.snapshot()["node_id"] != NODE:
        send(canvas, "navigate", node_id=NODE, base_revision=canvas.focus.snapshot()["revision"])
    response = send(canvas, "exercise", node_id=NODE, exercise_id="di.constructor." + kind)
    assert response["status"] == "ok", response
    return response["exercise_update"]


def action(canvas, activity, op, **kwargs):
    return send(canvas, op, activity_id=activity["activity_id"], nonce=activity["nonce"], **kwargs)


def test_mc_is_graded_by_key_and_updates_real_progress(canvas):
    activity = begin(canvas)
    assert "options" in activity and "answer" not in activity and "explanation" not in activity
    reply = action(canvas, activity, "submit", answer="a", correct=False, result="incorrect")
    assert reply["status"] == "ok"
    assert reply["exercise_update"]["result"] == "correct"
    assert next(n for n in reply["learning_canvas"]["nodes"] if n["id"] == NODE)["state"] == "practicing"
    attempts = canvas.learning.attempts()
    assert len(attempts) == 1 and attempts[0]["assistance"] == "independent"
    events = [e for e in canvas.test_events if e["action"] == "assessment"]
    assert len(events) == 1 and events[0]["result"] == "correct"


@pytest.mark.parametrize("op,expected", [("hint", "hinted"), ("reveal", "answer_revealed")])
def test_help_is_durable_before_ack_and_submit_is_assisted(canvas, op, expected):
    activity = begin(canvas)
    help_reply = action(canvas, activity, op)
    assert help_reply["exercise_update"]["help"]
    assert canvas.focus.store.assistance_for(activity["activity_id"])["assistance"] == expected
    reply = action(canvas, activity, "submit", answer="a")
    assert reply["exercise_update"]["assistance"] == expected
    assert canvas.learning.attempts()[0]["assistance"] == expected
    assert canvas.learning.get_mastery(NODE).mastery == 0
    assert canvas.learning.attempts()[0]["answer_revealed"] == (op == "reveal")


def test_reveal_cannot_be_downgraded_to_hint(canvas):
    activity = begin(canvas)
    action(canvas, activity, "reveal")
    reply = action(canvas, activity, "hint")
    assert reply["exercise_update"]["assistance"] == "answer_revealed"


def test_new_nonce_for_same_authored_item_cannot_erase_prior_assistance(canvas):
    first = begin(canvas)
    action(canvas, first, "reveal")
    second = begin(canvas)
    assert second["nonce"] != first["nonce"]
    assert second["assistance"] == "answer_revealed"
    action(canvas, second, "submit", answer="a")
    assert canvas.learning.attempts()[0]["assistance"] == "answer_revealed"
    assert canvas.learning.get_mastery(NODE).mastery == 0


def test_submit_retry_is_idempotent_after_service_restart(canvas):
    activity = begin(canvas)
    request = {"request_id": "durable-submit", "action": "submit", "activity_id": activity["activity_id"],
               "nonce": activity["nonce"], "answer": "a"}
    first = canvas.handle(request)
    assert canvas.handle(request) == first
    canvas.close()
    new = LearningCanvasService(canvas.learning, store=canvas.focus.store)
    try:
        assert new.handle(request)["exercise_update"]["result"] == "correct"
        assert len(new.learning.attempts()) == 1
        bad = new.handle({**request, "answer": "b"})
        assert bad["status"] == "error"
        assert len(new.learning.attempts()) == 1
    finally:
        new.close()


@pytest.mark.parametrize("changes", [{"nonce": "wrong"}, {"activity_id": "unknown"}, {"answer": "not-an-option"}, {"nonce": None}])
def test_invalid_activity_or_answer_is_never_graded(canvas, changes):
    activity = begin(canvas)
    reply = send(canvas, "submit", **{"activity_id": activity["activity_id"], "nonce": activity["nonce"], "answer": "a", **changes})
    assert reply["status"] == "error"
    assert not canvas.learning.attempts()


def test_activities_and_attempts_survive_reopening_sqlite(canvas):
    activity = begin(canvas)
    action(canvas, activity, "hint")
    action(canvas, activity, "submit", answer="b")
    store = LearningStore(canvas.focus.store._path)
    new = LearningCanvasService(LearningService(store), store=store)
    try:
        state = send(new, "open")["learning_canvas"]
        assert state["activity"]["activity_id"] == activity["activity_id"]
        assert state["activity"]["assistance"] == "hinted" and state["activity"]["result"] == "incorrect"
        assert len(new.learning.attempts()) == 1
    finally:
        new.close()
        store.close()


def test_java_is_background_and_navigation_stays_responsive(canvas):
    canvas._view_session("open", "java-active-test")  # P3.7: push UI chỉ khi panel đang mở.
    activity = begin(canvas, "code")
    workspace = canvas.exercises.workspace
    workspace.release.clear()
    updates = []
    canvas.on_state = updates.append
    reply = action(canvas, activity, "submit", code="class OrderService {}")
    assert reply["exercise_update"]["status"] == "running"
    assert workspace.started.wait(1)
    start = time.perf_counter()
    navigation = send(canvas, "navigate", node_id="javadi:v1:testing", base_revision=canvas.focus.snapshot()["revision"])
    assert navigation["status"] == "ok" and time.perf_counter() - start < .2
    workspace.release.set()
    canvas.exercises._future.result(timeout=3)
    assert canvas.learning.attempts()[0]["result"] == "partial"
    assert updates[0]["learning_canvas"]["focus"]["node_id"] == "javadi:v1:testing"
    assert not [e for e in canvas.test_events if e["action"] == "assessment"]


def test_java_verdict_is_persisted_and_emits_one_root_event(canvas):
    activity = begin(canvas, "code")
    action(canvas, activity, "submit", code="class OrderService {}")
    canvas.exercises._future.result(timeout=3)
    result = action(canvas, activity, "exercise_status")["exercise_update"]
    assert result["result"] == "partial" and len(result["tests"]) == 2
    assert len(canvas.learning.attempts()) == 1
    assert len([e for e in canvas.test_events if e["action"] == "assessment"]) == 1


def test_java_unavailable_does_not_create_a_fake_attempt_and_mc_still_works(canvas):
    canvas.exercises.workspace.receipt = {"status": "unavailable", "reason": "docker_not_running", "guidance": "Start Docker"}
    activity = begin(canvas, "code")
    action(canvas, activity, "submit", code="class OrderService {}")
    canvas.exercises._future.result(timeout=3)
    reply = action(canvas, activity, "exercise_status")
    assert reply["exercise_update"]["status"] == "unavailable"
    assert not canvas.learning.attempts()
    fallback = canvas.exercises.latest(NODE)
    assert fallback["type"] == "multiple_choice" and fallback["unavailable_notice"]
    again = begin(canvas, "code")
    assert again["code"] == "class OrderService {}"
    mc = begin(canvas)
    assert action(canvas, mc, "submit", answer="a")["exercise_update"]["result"] == "correct"


def test_help_and_second_worker_are_rejected_while_code_runs(canvas):
    activity = begin(canvas, "code")
    canvas.exercises.workspace.release.clear()
    action(canvas, activity, "submit", code="class OrderService {}")
    assert action(canvas, activity, "hint")["status"] == "error"
    another = begin(canvas, "code")
    assert action(canvas, another, "submit", code="class OrderService {}")["status"] == "error"
    canvas.exercises.workspace.release.set()


def test_large_java_submission_uses_one_bounded_datagram(canvas):
    activity = begin(canvas, "code")
    code = "//" + "x" * 8000 + "\nclass OrderService {}"
    payload = {"request_id": "large-code", "action": "submit", "code": code,
               "activity_id": activity["activity_id"], "nonce": activity["nonce"]}
    assert 4096 < len(json.dumps(payload).encode()) < 20000
    assert canvas.handle(payload)["status"] == "ok"
    canvas.exercises._future.result(timeout=3)


def test_repeated_families_do_not_inflate_mastery(canvas):
    activity = begin(canvas)
    for _ in range(4):
        action(canvas, activity, "submit", answer="a")
    assert len(canvas.learning.attempts()) == 4
    assert canvas.learning.get_mastery(NODE).correct_count == 1


def test_java_submit_retry_after_ack_cache_eviction_never_starts_a_second_worker(canvas):
    activity = begin(canvas, "code")
    canvas.exercises.workspace.release.clear()
    data = {"request_id": "durable-java", "action": "submit", "activity_id": activity["activity_id"],
            "nonce": activity["nonce"], "code": "class OrderService {}"}
    assert canvas.handle(data)["exercise_update"]["status"] == "running"
    assert canvas.exercises.workspace.started.wait(1)
    canvas._requests.clear()
    assert canvas.handle(data)["exercise_update"]["status"] == "running"
    assert canvas.exercises.workspace.calls == 1
    canvas.exercises.workspace.release.set()


def test_assessment_not_reported_completed_until_ledger_commit_succeeds(canvas, monkeypatch):
    activity = begin(canvas)
    original = canvas.learning.record_attempt
    monkeypatch.setattr(canvas.learning, "record_attempt", lambda *_: (_ for _ in ()).throw(RuntimeError("temporary-store-failure")))
    data = {"request_id": "commit-boundary", "action": "submit", "activity_id": activity["activity_id"],
            "nonce": activity["nonce"], "answer": "a"}
    with pytest.raises(RuntimeError, match="temporary-store-failure"):
        canvas.handle(data)
    assert canvas.exercises.latest(NODE)["status"] == "running"
    assert not canvas.learning.attempts()
    monkeypatch.setattr(canvas.learning, "record_attempt", original)
    assert canvas.handle(data)["exercise_update"]["result"] == "correct"
    assert len(canvas.learning.attempts()) == 1


def test_worker_interrupted_before_grading_is_unavailable_not_incorrect(canvas):
    activity = begin(canvas, "code")
    item = canvas.focus.store.load_activity(activity["activity_id"])
    item["latest_submission"] = "interrupted"
    item["submissions"]["interrupted"] = {"receipt": {"status": "running"}, "recorded_at": time.time()}
    canvas.focus.store.save_activity(item)
    result = action(canvas, activity, "exercise_status")["exercise_update"]
    assert result["status"] == "unavailable" and result["reason"] == "java_run_interrupted"
    assert not canvas.learning.attempts()


def test_java_code_limit_rejected_before_worker(canvas):
    activity = begin(canvas, "code")
    assert action(canvas, activity, "submit", code="x" * 12001)["status"] == "error"
    assert canvas.exercises.workspace.calls == 0


def test_real_udp_carries_java_source_over_4kb_without_blocking_handler(canvas):
    activity = begin(canvas, "code")
    canvas.exercises.workspace.release.clear()
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as client:
        client.bind(("127.0.0.1", 0))
        client.settimeout(1)
        transport = UdpTransport("127.0.0.1", client.getsockname()[1], "127.0.0.1", 0)
        host = SimpleNamespace(learning_canvas_service=canvas,
                              _send_runtime_json=lambda payload, **_: transport.send_json(payload))
        inbox = CanvasControlInbox(transport,
            lambda msg: IncomingProtocolRuntimeMixin._handle_learning_canvas_request(host, msg))
        inbox.start()
        try:
            data = {"type": "learning_canvas_request", "request_id": "udp-code-9000", "action": "submit",
                    "activity_id": activity["activity_id"], "nonce": activity["nonce"],
                    "code": "//" + "x" * 9000 + "\nclass OrderService {}"}
            start = time.perf_counter()
            client.sendto(json.dumps(data).encode(), transport._recv_sock.getsockname())
            reply = json.loads(client.recv(63000))
            assert time.perf_counter() - start < .2
            assert reply["status"] == "ok" and reply["exercise_update"]["status"] == "running"
        finally:
            inbox.close()
            canvas.exercises.workspace.release.set()
