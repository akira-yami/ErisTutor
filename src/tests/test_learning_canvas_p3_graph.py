"""P3: đồ thị mở nhiều chủ đề, vẫn bảo toàn Java pack và evidence."""
from __future__ import annotations

from types import SimpleNamespace
import time
import uuid

import pytest

from runtime.capabilities.canvas import CanvasCapability
from runtime.learning_canvas.service import LearningCanvasService
from runtime.learning_canvas.graph import GraphWorkspace, MAX_NODES
from runtime.learning_core import LearningService, LearningStore


@pytest.fixture
def service(tmp_path):
    store = LearningStore(tmp_path / "learning.sqlite3")
    canvas = LearningCanvasService(LearningService(store), store=store)
    yield canvas
    canvas.close()
    store.close()


def action(service, name, **kwargs):
    return service.handle({"request_id": f"p3-{uuid.uuid4().hex}",
                           "action": name, **kwargs})


def graph_workspace(service):
    return service.state_packet()["learning_canvas"]["graph_workspace"]


def test_p3_create_topics_expand_multiple_levels_without_touching_mastery(service):
    before = service.focus.snapshot()
    first = action(service, "graph_create", title="Spring Boot", base_graph_revision=0)
    assert first["status"] == "ok"
    graph = first["learning_canvas"]["graph_workspace"]["graph"]
    topic_id = graph["group_id"]
    root_id = graph["selected_node_id"]
    assert graph["title"] == "Spring Boot" and len(graph["nodes"]) == 1
    second = action(service, "graph_expand", group_id=topic_id, parent_id=root_id,
                    title="Spring Security", summary="Authentication và Authorization",
                    base_graph_revision=1)
    assert second["status"] == "ok"
    child_id = second["graph_update"]["node_id"]
    third = action(service, "graph_expand", group_id=topic_id, parent_id=child_id,
                   title="OAuth2", base_graph_revision=2)
    assert third["status"] == "ok"
    expanded = graph_workspace(service)["graph"]
    assert expanded["nodes"][-1]["parent_id"] == child_id
    assert expanded["nodes"][-1]["title"] == "OAuth2"
    assert all(n["origin"] == "user" for n in expanded["nodes"])
    # Đồ thị khám phá không phải chứng cứ đánh giá; bài Java vẫn không bị chạm.
    assert service.focus.snapshot() == before
    assert not service.focus.store.load_attempts()
    assert len(service.pack.path.kp_ids) == 5
    assert graph_workspace(service)["revision"] == 3


def test_p3_topic_switch_and_reopen_after_restart(service):
    first = action(service, "graph_create", title="Đại số tuyến tính", base_graph_revision=0)
    first_id = first["graph_update"]["group_id"]
    second = action(service, "graph_create", title="Lập trình mạng", base_graph_revision=1)
    second_id = second["graph_update"]["group_id"]
    assert first_id != second_id
    assert action(service, "graph_select", group_id=first_id, base_graph_revision=2)["status"] == "ok"
    assert action(service, "graph_select", group_id="java-di", base_graph_revision=3)["status"] == "ok"
    assert graph_workspace(service)["graph"] is None
    again = LearningCanvasService(service.learning, store=service.focus.store)
    persisted = graph_workspace(again)
    assert persisted["revision"] == 4 and persisted["selected_group_id"] == "java-di"
    assert {g["group_id"] for g in persisted["groups"]} == {"java-di", first_id, second_id}
    assert action(again, "graph_select", group_id=second_id, base_graph_revision=4)["status"] == "ok"
    assert graph_workspace(again)["graph"]["title"] == "Lập trình mạng"
    again.close()


def test_p3_conflicts_and_invalid_inputs_are_atomic(service):
    item = action(service, "graph_create", title="Database", base_graph_revision=0)
    group_id = item["graph_update"]["group_id"]
    root_id = item["graph_update"]["node_id"]
    valid = {"group_id": group_id, "parent_id": root_id, "base_graph_revision": 1}
    ok = action(service, "graph_expand", title="Indexes", **valid)
    assert ok["status"] == "ok"
    before = graph_workspace(service)
    assert action(service, "graph_expand", title="Transactions", **valid)["status"] == "conflict"
    assert action(service, "graph_expand", title="Indexes", **{**valid, "base_graph_revision": 2})["message"] == "duplicate_graph_child"
    assert action(service, "graph_expand", title="\x00bad", **{**valid, "base_graph_revision": 2})["status"] == "error"
    assert action(service, "graph_expand", title="Unknown", parent_id="bogus", group_id=group_id, base_graph_revision=2)["status"] == "error"
    assert graph_workspace(service) == before
    with pytest.raises(ValueError, match="invalid_graph_nodes"):
        service.graphs.expand(group_id, root_id, [{"title": "ok"}] * 9, base_revision=2)
    assert graph_workspace(service) == before


def test_p3_root_can_propose_without_stealing_user_selection(service):
    service._view_session("open", "test-p3-browser")  # Root test cũ cần panel đang xem.
    sent = []
    host = SimpleNamespace(learning_canvas_service=service,
                           _send_runtime_json=lambda packet, **_: sent.append(packet) or True)
    root = CanvasCapability(host)
    event = SimpleNamespace(kind="user_turn", session_id="primary", text="Học Spring")
    result = root.invoke_for_root(arguments={"operation": "graph_create", "title": "Spring Boot"}, event=event)
    assert result["status"] == "completed" and result["graph_update"]
    group_id = result["graph_update"]["group_id"]
    node_id = result["graph_update"]["node_id"]
    assert graph_workspace(service)["selected_group_id"] == "java-di"
    proposal = root.invoke_for_root(arguments={"operation": "graph_expand", "group_id": group_id,
        "parent_id": node_id, "nodes": [{"title": "IoC"}, {"title": "DI", "summary": "Phân biệt DI với IoC"}]}, event=event)
    assert proposal["status"] == "completed"
    assert len(proposal["graph_update"]["nodes"]) == 2
    assert graph_workspace(service)["selected_group_id"] == "java-di"
    group = action(service, "graph_select", group_id=group_id, base_graph_revision=2)
    assert group["status"] == "ok"
    assert all(n["origin"] == "eris_proposed" for n in graph_workspace(service)["graph"]["nodes"])
    assert len(sent) == 2


def test_p3_concurrent_connections_cas(tmp_path):
    path = tmp_path / "learning.sqlite3"
    first = LearningStore(path)
    second = LearningStore(path)
    try:
        a, b = GraphWorkspace(first), GraphWorkspace(second)
        a.create("Toán", base_revision=0)
        with pytest.raises(ValueError, match="stale_graph_revision"):
            b.create("Vật lý", base_revision=0)
        assert len(a.snapshot()["groups"]) == 2  # gồm Java mặc định
    finally:
        first.close()
        second.close()


def test_p3_bounded_payload_and_stable_ids(service):
    seed = action(service, "graph_create", title="Deep learning", base_graph_revision=0)
    group_id, parent_id = seed["graph_update"]["group_id"], seed["graph_update"]["node_id"]
    # Các cụm nhiều nhánh mô phỏng đồ thị khá lớn, không đẩy vượt UDP 60 KB.
    for i in range(0, MAX_NODES - 1, 7):
        count = min(7, MAX_NODES - 1 - i)
        service.graphs.expand(group_id, parent_id,
            [{"title": f"Chapter {i+j}", "summary": "Khái niệm kỹ thuật " * 10} for j in range(count)],
            base_revision=1 + i // 7)
    assert len(graph_workspace(service)["graph"]["nodes"]) == MAX_NODES
    import json
    envelope = action(service, "open")
    assert envelope["status"] == "ok"
    assert len(json.dumps(envelope, ensure_ascii=False).encode()) < 60000
    assert len({n["id"] for n in graph_workspace(service)["graph"]["nodes"]}) == MAX_NODES


def test_p3_never_commits_graph_too_large_for_transport(service):
    from runtime.learning_canvas.graph import MAX_GRAPH_BYTES
    seed = action(service, "graph_create", title="Kiến trúc phần mềm", base_graph_revision=0)
    gid, parent = seed["graph_update"]["group_id"], seed["graph_update"]["node_id"]
    revision = 1
    failed = False
    for batch in range(20):
        before = graph_workspace(service)
        result = action(service, "graph_expand", group_id=gid, parent_id=parent,
            title=f"Mô hình {batch} " + "x" * 99, summary="D" * 240,
            base_graph_revision=revision)
        if result["status"] == "error":
            assert result["message"] in {"graph_payload_limit", "graph_node_limit"}
            assert graph_workspace(service) == before
            failed = True
            break
        revision += 1
    assert len(str(graph_workspace(service))) > 0
    # Bản đồ gần đầy vẫn trả về trong ngân sách của giao thức.
    assert len(__import__("json").dumps(graph_workspace(service), ensure_ascii=False).encode()) < MAX_GRAPH_BYTES + 8000
