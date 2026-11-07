from __future__ import annotations

import ast
from concurrent.futures import ThreadPoolExecutor
import copy
import json
from pathlib import Path
import queue
import socket
import threading
import time
from types import SimpleNamespace
import uuid

import pytest

from runtime.incoming_dispatcher import IncomingMessageDispatcher
from runtime.incoming_protocol_runtime import IncomingProtocolRuntimeMixin
from runtime.learning_canvas.focus import CanvasFocusStore, META_KEY
from runtime.learning_canvas.inbox import CanvasControlInbox
from runtime.learning_canvas.pack import load_pack, validate_pack
from runtime.learning_canvas.service import LearningCanvasService
from runtime.learning_canvas.snapshot import build_snapshot
from runtime.learning_core import LearningService, LearningStore
from runtime.learning_core.contracts import LearningPath, KnowledgePoint
from runtime.learning_core.records import AssessmentAttempt
from transport.udp import UdpTransport

ROOT = Path(__file__).resolve().parents[2]
PACK_PATH = ROOT / "src/runtime/learning_canvas/packs/java-di.json"
CONSTRUCTOR = "javadi:v1:constructor"


@pytest.fixture
def canvas(tmp_path):
    store = LearningStore(tmp_path / "learning.sqlite3")
    learning = LearningService(store)
    service = LearningCanvasService(learning, store=store)
    yield service
    store.close()


def request(service, action="open", **extra):
    return service.handle({"type": "learning_canvas_request", "request_id": f"test-{uuid.uuid4().hex}",
                           "action": action, **extra})


def raw_pack():
    return json.loads(PACK_PATH.read_text(encoding="utf-8"))


def test_pack_registers_idempotently_without_activating_or_erasing_other_paths(canvas):
    learning = canvas.learning
    other = LearningPath("existing", "Existing subject", "Keep it", (KnowledgePoint("other:one", "One"),))
    learning.register_path(other)
    learning.activate_path(other.path_id)
    LearningCanvasService(learning, store=canvas.focus.store)
    assert learning.active_path().path_id == "existing"
    assert len(canvas.pack.nodes) == 5
    stored = canvas.focus.store.load_path("javadi:v1:path")
    assert stored == canvas.pack.path
    assert all(key.startswith("javadi:v1:") for key in stored.kp_ids)
    assert not canvas.focus.store.load_attempts()


@pytest.mark.parametrize("problem,reason", [
    ("cycle", "prerequisite_cycle"), ("unknown_prerequisite", "unknown_prerequisite"),
    ("duplicate_block", "duplicate_pack_id"), ("duplicate_node", "duplicate_pack_id"),
    ("duplicate_exercise", "duplicate_pack_id"), ("answer_mismatch", "invalid_choice_answer"),
    ("duplicate_option", "invalid_choice_answer"), ("missing_java", "pack_requires_explanation_and_java"),
    ("wrong_namespace", "pack_namespace_mismatch"), ("wrong_schema", "invalid_pack_schema"),
    ("wrong_version", "unsupported_pack_version"), ("bad_block_kind", "invalid_pack_block_kind"),
])
def test_invalid_pack_rejected(problem, reason):
    raw = raw_pack()
    nodes = raw["nodes"]
    if problem == "cycle":
        nodes[0]["prerequisites"] = [nodes[1]["id"]]
    elif problem == "unknown_prerequisite":
        nodes[0]["prerequisites"] = ["unknown"]
    elif problem == "duplicate_block":
        nodes[1]["blocks"][0]["id"] = nodes[0]["blocks"][0]["id"]
    elif problem == "duplicate_node":
        nodes[1]["id"] = nodes[0]["id"]
    elif problem == "duplicate_exercise":
        nodes[1]["exercises"][0]["id"] = nodes[0]["exercises"][0]["id"]
    elif problem == "answer_mismatch":
        nodes[0]["exercises"][0]["answer"] = "not-an-option"
    elif problem == "duplicate_option":
        nodes[0]["exercises"][0]["options"][1]["id"] = "a"
    elif problem == "missing_java":
        nodes[0]["blocks"].pop()
    elif problem == "wrong_namespace":
        nodes[0]["id"] = "other:v1:dependency"
    elif problem == "wrong_schema":
        raw["schema"] = "unknown"
    elif problem == "wrong_version":
        raw["version"] = "2"
    elif problem == "bad_block_kind":
        nodes[0]["blocks"][0]["kind"] = "html"
    with pytest.raises(ValueError, match=reason):
        validate_pack(raw)


def test_pack_is_defensively_copied_and_private_answers_are_not_in_details():
    pack = load_pack()
    node = pack.node(CONSTRUCTOR)
    node["blocks"].clear()
    assert pack.node(CONSTRUCTOR)["blocks"]
    for key in pack.path.kp_ids:
        detail = pack.detail(key)
        for exercise in detail["exercises"]:
            assert set(exercise) == {"id", "type", "prompt"}
        assert len(json.dumps(detail, ensure_ascii=False).encode()) < 24000
    with pytest.raises(ValueError, match="unknown_canvas_node"):
        pack.node("../../secret")


@pytest.mark.parametrize("stage,state", [("unknown", "available"), ("learning", "learning"),
    ("practicing", "practicing"), ("mastered", "mastered"), ("retained", "mastered")])
def test_snapshot_maps_learning_insights_stage_and_due(stage, state):
    pack = load_pack()
    learning = SimpleNamespace(learning_insights=lambda **_: {"knowledge": [
        {"kp_id": key, "mastery_stage": stage, "review_due_at": 100 if i == 0 else None}
        for i, key in enumerate(pack.path.kp_ids)
    ]})
    snapshot = build_snapshot(learning, pack, {})
    assert {n["state"] for n in snapshot["nodes"]} == {state}
    assert [n["due"] for n in snapshot["nodes"]] == [True, False, False, False, False]
    assert [n["level"] for n in snapshot["nodes"]] == [0, 1, 1, 2, 2]
    warning = next(n for n in snapshot["nodes"] if n["id"] == CONSTRUCTOR)["prerequisite_warnings"]
    assert bool(warning) == (state != "mastered")


def test_attempt_and_focus_pin_survive_restart(canvas):
    ack = request(canvas, "navigate", node_id=CONSTRUCTOR, base_revision=0)
    assert ack["status"] == "ok"
    before = next(n for n in ack["learning_canvas"]["nodes"] if n["id"] == CONSTRUCTOR)
    assert before["state"] == "available"
    canvas.learning.record_attempt(AssessmentAttempt(
        "synthetic-independent", CONSTRUCTOR, "correct", "quiz_correct", strength=1,
        path_id=canvas.pack.path.path_id, item_family_id="constructor-choice", recorded_at=time.time(),
    ))
    path = canvas.focus.store._path
    canvas.focus.store.close()
    store = LearningStore(path)
    try:
        reopened = LearningCanvasService(LearningService(store), store=store)
        snapshot = request(reopened)["learning_canvas"]
        assert snapshot["focus"] == ack["learning_canvas"]["focus"]
        assert snapshot["focus"]["pinned"] and snapshot["focus"]["set_by"] == "user"
        replay = request(reopened, "navigate", node_id="javadi:v1:testing", base_revision=0)
        assert replay["status"] == "conflict" and replay["learning_canvas"]["focus"] == snapshot["focus"]
        assert snapshot["detail"]["id"] == CONSTRUCTOR
        assert next(n for n in snapshot["nodes"] if n["id"] == CONSTRUCTOR)["state"] != "available"
        assert store.load_attempt("synthetic-independent")
    finally:
        store.close()


def test_stale_request_rejected_and_returns_authoritative_state(canvas):
    first = request(canvas, "navigate", node_id=CONSTRUCTOR, base_revision=0)
    rejected = request(canvas, "navigate", node_id="javadi:v1:testing", base_revision=0)
    assert rejected["status"] == "conflict"
    assert rejected["message"] == "stale_canvas_revision"
    assert rejected["learning_canvas"]["focus"] == first["learning_canvas"]["focus"]


@pytest.mark.parametrize("payload", [
    {"action": "navigate", "node_id": "missing", "base_revision": 0},
    {"action": "navigate", "node_id": CONSTRUCTOR, "base_revision": True},
    {"action": "navigate", "node_id": CONSTRUCTOR, "base_revision": "0"},
    {"action": "navigate", "node_id": CONSTRUCTOR},
    {"action": "submit"}, {"group_id": "other"}, {"node_id": "missing"},
    {"action": "ack_eris_focus", "cause_id": "made-up", "base_revision": 0},
])
def test_invalid_request_never_moves_focus(canvas, payload):
    before = canvas.focus.snapshot()
    assert request(canvas, **payload)["status"] == "error"
    assert canvas.focus.snapshot() == before


def test_udp_retries_are_idempotent_and_request_id_reuse_rejected(canvas):
    data = {"request_id": "same", "action": "navigate", "node_id": CONSTRUCTOR, "base_revision": 0}
    first = canvas.handle(data)
    assert canvas.handle(copy.deepcopy(data)) == first
    assert canvas.focus.snapshot()["revision"] == 1
    rejected = canvas.handle({**data, "node_id": "javadi:v1:testing"})
    assert rejected["status"] == "error"
    assert rejected["message"] == "canvas_request_id_reused"
    assert canvas.focus.snapshot()["node_id"] == CONSTRUCTOR


def test_compare_and_set_across_connections_has_exactly_one_winner(canvas):
    other_store = LearningStore(canvas.focus.store._path)
    other_focus = CanvasFocusStore(other_store, canvas.pack)
    barrier = threading.Barrier(2)

    def navigate(focus, key):
        barrier.wait()
        try:
            return focus.navigate(key, base_revision=0)
        except ValueError as exc:
            return str(exc)

    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            a = executor.submit(navigate, canvas.focus, CONSTRUCTOR)
            b = executor.submit(navigate, other_focus, "javadi:v1:testing")
            results = [a.result(), b.result()]
        assert sum(isinstance(r, dict) for r in results) == 1
        assert "stale_canvas_revision" in results
        assert canvas.focus.snapshot() == other_focus.snapshot()
    finally:
        other_store.close()


def test_eris_focus_cause_ack_does_not_pin_or_echo_user_navigation(canvas):
    focus = canvas.focus.eris_focus(CONSTRUCTOR, base_revision=0, cause_id="root-cause")
    assert focus["set_by"] == "eris" and not focus["pinned"]
    ack = request(canvas, "ack_eris_focus", base_revision=1, cause_id="root-cause")
    assert ack["status"] == "ok"
    assert ack["learning_canvas"]["focus"]["acknowledged_cause_id"] == "root-cause"
    assert not ack["learning_canvas"]["focus"]["pinned"]
    canvas.focus.navigate(CONSTRUCTOR, base_revision=2)
    suggested = canvas.focus.eris_focus("javadi:v1:testing", base_revision=3, cause_id="next-cause")
    assert suggested["node_id"] == CONSTRUCTOR and suggested["pinned"]
    assert suggested["suggested_node_id"] == "javadi:v1:testing"


def test_server_state_has_origin_cause_and_can_be_requested_without_detail(canvas):
    canvas.focus.eris_focus(CONSTRUCTOR, base_revision=0, cause_id="root-state")
    full = canvas.state_packet()
    assert full["type"] == "learning_canvas_state" and not full["should_speak"]
    assert full["learning_canvas"]["focus"]["set_by"] == "eris"
    assert full["learning_canvas"]["focus"]["cause_id"] == "root-state"
    assert full["learning_canvas"]["detail"]["id"] == CONSTRUCTOR
    assert "detail" not in canvas.state_packet(include_detail=False)["learning_canvas"]


def test_cached_ack_cannot_be_mutated_by_callers(canvas):
    data = {"request_id": "defensive-copy", "action": "open"}
    reply = canvas.handle(data)
    reply["learning_canvas"]["focus"]["revision"] = 100
    assert canvas.handle(data)["learning_canvas"]["focus"]["revision"] == 0


def test_invalid_saved_focus_is_preserved(canvas):
    canvas.focus.store.set_meta(META_KEY, {"revision": 1, "node_id": "deleted-node"})
    with pytest.raises(ValueError, match="invalid_saved_canvas_focus"):
        canvas.focus.snapshot()
    assert canvas.focus.store.get_meta(META_KEY)["node_id"] == "deleted-node"


def test_all_public_packets_fit_udp_and_never_include_hidden_grading_data(canvas):
    for node_id in canvas.pack.path.kp_ids:
        ack = request(canvas, node_id=node_id)
        assert len(json.dumps(ack, ensure_ascii=False).encode()) < 16000
        assert ack["should_speak"] is False
        assert ack["learning_canvas"]["detail"]["id"] == node_id
        encoded = json.dumps(ack)
        for key in ("answer", "harness", "starter_code", "tests", "options"):
            assert f'"{key}":' not in encoded


class WireHost(IncomingProtocolRuntimeMixin):
    _maintenance_in_progress = False

    def __init__(self, canvas, transport):
        self.learning_canvas_service = canvas
        self.transport = transport

    def __getattr__(self, name):
        if name in {"_handle_panel_state_ack", "_handle_phase7_validation_control", "_handle_anchor_control",
                    "_handle_depth_control", "_handle_settings_update", "_handle_settings_health",
                    "_handle_settings_connection_test", "_handle_maintenance_catalog",
                    "_handle_maintenance_request", "_handle_debug_command"}:
            return lambda *_: pytest.fail("Canvas must not enter another route")
        raise AttributeError(name)


def test_dispatcher_route_does_not_require_conversation_or_audio(canvas):
    sent = []
    host = WireHost(canvas, SimpleNamespace(send_json=lambda payload: sent.append(payload)))
    result = IncomingMessageDispatcher(host).dispatch({"type": "learning_canvas_request", "action": "navigate",
        "request_id": "route-test", "base_revision": 0, "node_id": CONSTRUCTOR})
    assert result.handled and sent[0]["status"] == "ok"
    assert sent[0]["should_speak"] is False


def test_real_udp_ack_under_200ms_while_foreground_root_is_blocked(canvas):
    release, entered = threading.Event(), threading.Event()
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as client:
        client.bind(("127.0.0.1", 0))
        client.settimeout(1)
        transport = UdpTransport("127.0.0.1", client.getsockname()[1], "127.0.0.1", 0)
        host = WireHost(canvas, transport)
        inbox = CanvasControlInbox(transport, host._handle_learning_canvas_request)
        inbox.start()

        def fake_foreground_root():
            deadline = time.monotonic() + 1
            while time.monotonic() < deadline:
                message = inbox.poll_json()
                if message is not None:
                    assert message["type"] == "chat"
                    entered.set()
                    release.wait(2)
                    return
                time.sleep(0.001)

        root = threading.Thread(target=fake_foreground_root)
        root.start()
        try:
            target = transport._recv_sock.getsockname()
            client.sendto(b'{"type":"chat","message":"Root run"}', target)
            assert entered.wait(1)
            for revision in range(5):
                message = {"type": "learning_canvas_request", "request_id": f"latency-{revision}",
                           "action": "navigate", "node_id": CONSTRUCTOR, "base_revision": revision}
                start = time.perf_counter()
                client.sendto(json.dumps(message).encode(), target)
                ack = json.loads(client.recv(65507))
                elapsed = time.perf_counter() - start
                assert elapsed < 0.2, f"Canvas ack took {elapsed * 1000:.1f} ms"
                assert ack["status"] == "ok"
                assert root.is_alive() and not release.is_set()
        finally:
            release.set()
            root.join(timeout=1)
            inbox.close()
        assert not inbox._thread.is_alive()


def test_inbox_preserves_other_packets_in_order_and_proxies_send():
    incoming, sent = queue.Queue(), []
    for message in ({"type": "chat", "message": "one"}, {"type": "chat_end"}, {"type": "settings_health"}):
        incoming.put(message)
    transport = SimpleNamespace(poll_json=lambda: incoming.get_nowait() if not incoming.empty() else None,
                                send_json=sent.append, close=lambda: None)
    inbox = CanvasControlInbox(transport, lambda _: pytest.fail("Unexpected Canvas request"))
    inbox.start()
    try:
        deadline = time.monotonic() + 1
        while inbox._pending.qsize() != 3 and time.monotonic() < deadline:
            time.sleep(0.005)
        assert [inbox.poll_json()["type"] for _ in range(3)] == ["chat", "chat_end", "settings_health"]
        assert inbox.poll_json() is None
        inbox.send_json({"type": "test"})
        assert sent == [{"type": "test"}]
    finally:
        inbox.close()


def test_canvas_package_has_no_conversation_or_tts_authority():
    for source in (ROOT / "src/runtime/learning_canvas").glob("*.py"):
        tree = ast.parse(source.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                module = getattr(node, "module", "") or ""
                assert not any(part in module for part in ("conversation", "tts", "domain")), source
            if isinstance(node, ast.Call):
                name = getattr(node.func, "attr", getattr(node.func, "id", ""))
                assert name not in {"Decision", "speak", "synthesize", "cancel_generation", "_run_chat_cycle"}, source
