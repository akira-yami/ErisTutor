# -*- coding: utf-8 -*-
"""P1: kiểm thử đa môn, input mở và ghi bằng chứng qua Tutor/Artifact thật."""
import base64
import json
from types import SimpleNamespace

import pytest

from runtime.learning_core import KnowledgePoint, LearningPath, LearningService, LearningStore
from runtime.learning_core.records import DomainSpec
from runtime.capabilities.tutor import TutorCapability
from runtime.conversation.contracts import DeliveryReceipt
from runtime.tutor.activities import LearningActivities, activity_variant, decode_text_answer
from runtime.tutor.executive import TutorExecutive
from runtime.workspace.artifact_protocol import validate_bundle


def make_learning(tmp_path):
    return LearningService(LearningStore(tmp_path / "learning.sqlite3"))


def event(num, text="", *, generation=1):
    return SimpleNamespace(event_id=f"user:{num}", session_id="session:p1", generation=generation, text=text)


def typed(value):
    data = base64.urlsafe_b64encode(value.encode("utf-8")).decode("ascii").rstrip("=")
    return "t:" + data


def new_cap(tmp_path, monkeypatch):
    monkeypatch.setenv("ERIS_ARTIFACT_WORKSPACE", str(tmp_path / "artifacts"))
    learning = make_learning(tmp_path)
    return learning, TutorCapability(TutorExecutive(learning))


def publish(cap, item, *, num=1):
    """Mô phỏng Kernel phát bundle THÀNH CÔNG (không giả lập model/voice)."""
    cap.bind_pending_delivery(
        event_id=f"user:{num}", session_id="session:p1", generation=1,
        delivery_id=f"delivery:{num}", move_id=f"move:{num}",
        artifact_bundle_ids=[item["artifact_bundle_id"]],
    )
    cap.observe_delivery_receipt(DeliveryReceipt(
        delivery_id=f"delivery:{num}", session_id="session:p1", generation=1,
        move_id=f"move:{num}", successful=True,
    ))


def test_learning_spaces_across_domains_resume_and_restart(tmp_path):
    learning = make_learning(tmp_path)
    for name in ("math", "code"):
        learning.register_domain(DomainSpec(name, name, name + ":v1"))
        learning.register_path(LearningPath(
            "path:" + name, name.title(), "Học " + name,
            (KnowledgePoint(name + ":v1:a", "Đầu"), KnowledgePoint(name + ":v1:b", "Tiếp")),
            domain_id=name,
        ))
    assert learning.activate_path("path:math")
    learning.advance_kp("math:v1:b")
    assert learning.activate_path("path:code")
    learning.advance_kp("code:v1:b")
    spaces = learning.learning_spaces()
    assert {p["path_id"] for p in spaces["paths"]} == {"path:math", "path:code"}
    assert {d["domain_id"] for d in spaces["domains"]} == {"math", "code"}
    assert learning.switch_learning_space("path:math")["active_path_id"] == "path:math"
    assert learning.learning_spaces()["paths"][0]["current_kp_id"] in {"math:v1:b", "code:v1:b"}
    learning._store.close()
    reopened = make_learning(tmp_path)
    assert reopened.active_path().path_id == "path:math"
    assert reopened.learning_spaces()["active_path_id"] == "path:math"
    assert reopened.switch_learning_space("path:code")["active_path_id"] == "path:code"
    assert next(p for p in reopened.learning_spaces()["paths"] if p["path_id"] == "path:code")["current_kp_id"] == "code:v1:b"
    with pytest.raises(ValueError, match="unknown_learning_space"):
        reopened.switch_learning_space("path:unknown")


def test_switch_path_requires_named_subject_in_actual_user_turn(tmp_path):
    learning = make_learning(tmp_path)
    learning.register_domain(DomainSpec("math", "Toán", "math:v1"))
    learning.register_path(LearningPath("path:math", "Toán cơ bản", "Học toán",
                                        (KnowledgePoint("math:v1:a", "A"),), domain_id="math"))
    cap = TutorCapability(TutorExecutive(learning))
    wrong = cap.invoke_for_root(arguments={"operation": "switch_path", "path_id": "path:math"},
                                event=event(1, "Tôi muốn nghỉ"), recent_history=[])
    assert wrong["status"] == "failed" and learning.active_path() is None
    ok = cap.invoke_for_root(arguments={"operation": "switch_path", "path_id": "path:math"},
                             event=event(2, "Tôi muốn quay lại Toán cơ bản"), recent_history=[])
    assert ok["status"] == "completed" and ok["active_path_id"] == "path:math"
    spaces = cap.invoke_for_root(arguments={"operation": "spaces"}, event=event(3), recent_history=[])
    assert spaces["status"] == "completed" and len(spaces["paths"]) == 1


def test_math_variant_keys_are_computed_not_model_guessed():
    for seed in range(100):
        f = activity_variant("math.fractions", seed)
        assert f["options"][f["correct"]] == f["explanation"].split("= ")[-1].rstrip(".")
        m = activity_variant("math.linear", seed)
        x = int(m["expected_text"])
        expression = m["question"].split("x + ")[1].split(" = ")
        assert x + int(expression[0]) == int(expression[1].split(".")[0])


def test_math_numeric_delivery_artifact_backend_grade_and_idempotency(tmp_path, monkeypatch):
    learning, cap = new_cap(tmp_path, monkeypatch)
    made = cap.invoke_for_root(arguments={"operation": "activity", "preset": "math.linear"},
                               event=event(1, "Bài toán"), recent_history=[])
    assert made["status"] == "completed", made
    item = learning._store.load_activity(made["activity_id"])
    assert item["response_mode"] == "numeric"
    sidecar = tmp_path / "artifacts" / "presentation_v1" / (made["artifact_bundle_id"] + ".json")
    envelope = validate_bundle(json.loads(sidecar.read_text(encoding="utf-8")))
    public = envelope["artifacts"][0]["content"]["activity"]
    assert public["response_mode"] == "numeric" and public["options"] == []
    assert not ({"expected_text", "correct", "explanation", "hints"} & set(public))
    user_event = event(2, cap._activity_workspace.command(item, "answer", typed(item["expected_text"])), generation=2)
    rejected = cap.invoke_for_root(arguments={"operation": "activity_event"}, event=user_event, recent_history=[])
    assert rejected["status"] == "failed"  # Chưa phát activity.
    publish(cap, made)
    graded = cap.invoke_for_root(arguments={"operation": "activity_event"}, event=user_event, recent_history=[])
    assert graded["status"] == "completed" and graded["attempt"]["result"] == "correct"
    assert graded["attempt"]["assistance"] == "independent"
    assert graded["attempt"]["answer_ref"].startswith("sha256:")
    assert item["expected_text"] not in graded["attempt"]["answer_ref"]
    assert learning.get_mastery(item["kp_id"]).correct_count == 1
    duplicate = cap.invoke_for_root(arguments={"operation": "activity_event"}, event=user_event, recent_history=[])
    assert duplicate["duplicate"] and len(learning.attempts()) == 1


def test_reflection_is_ungraded_and_does_not_raise_mastery(tmp_path, monkeypatch):
    learning, cap = new_cap(tmp_path, monkeypatch)
    made = cap.invoke_for_root(arguments={"operation": "activity", "preset": "writing.explanation"},
                               event=event(1, "Tôi muốn tự viết giải thích"), recent_history=[])
    assert made["status"] == "completed", made
    item = learning._store.load_activity(made["activity_id"])
    assert item["response_mode"] == "reflection"
    publish(cap, made)
    response = "Khi tự thử, mình sẽ thấy chỗ nào hiểu sai và biết cần hỏi phần nào."
    cmd = cap._activity_workspace.command(item, "answer", typed(response))
    result = cap.invoke_for_root(arguments={"operation": "activity_event"},
                                 event=event(2, cmd, generation=2), recent_history=[])
    assert result["status"] == "completed" and result["attempt"]["result"] == "ungraded"
    assert result["grading_status"] == "ungraded"
    assert learning.get_mastery(item["kp_id"]).correct_count == 0
    assert not learning._store.load_evidence(item["kp_id"])
    assert response not in str(learning.attempts())


def test_invalid_text_payload_does_not_create_evidence(tmp_path, monkeypatch):
    learning, cap = new_cap(tmp_path, monkeypatch)
    made = cap.invoke_for_root(arguments={"operation": "activity", "preset": "math.linear"},
                               event=event(1), recent_history=[])
    item = learning._store.load_activity(made["activity_id"])
    publish(cap, made)
    for n, bad in enumerate(("t:__", typed("not a number"), typed("2" * 3010)), 2):
        reply = cap.invoke_for_root(arguments={"operation": "activity_event"},
                                    event=event(n, cap._activity_workspace.command(item, "answer", bad), generation=2),
                                    recent_history=[])
        assert reply["status"] == "failed"
    assert learning.attempts() == []
    assert decode_text_answer(typed("xin chào")) == "xin chào"


def test_root_tool_result_carries_math_bundle_without_tutor_speaking(tmp_path, monkeypatch):
    """Đường Root -> Tutor -> artifact thật, chưa mô phỏng model/TTS mạng."""
    from runtime.conversation.agent import RuntimeModelRootAgent
    from runtime.conversation.context import RootCapabilityRegistry
    from runtime.conversation.contracts import ConversationEvent, NextConversationMove

    learning, cap = new_cap(tmp_path, monkeypatch)

    class DeterministicRouter:
        executive_client = object()

        def __init__(self):
            self.steps = [
                {"kind": "use_capability", "capability": "tutor",
                 "arguments": {"operation": "activity", "preset": "math.linear"}},
                {"kind": "move", "intent": "Mời làm bài Toán", "say": "Mời bạn tự giải phương trình.",
                 "after": "wait_user"},
            ]
            self.received = []

        def invoke_structured_worker(self, client, **kwargs):
            self.received.append(kwargs)
            return self.steps.pop(0)

    router = DeterministicRouter()
    result = RuntimeModelRootAgent(router).decide(
        event=ConversationEvent(
            event_id="root:math", session_id="session:p1", generation=1,
            kind="user_turn", text="Cho tôi bài Toán phương trình",
        ),
        recent_history=(), note="",
        runtime_context={
            "__root_capabilities": RootCapabilityRegistry({"tutor": cap}),
            "__conversation_run_id": "run:math",
        },
    )
    assert isinstance(result, NextConversationMove)
    assert result.say == "Mời bạn tự giải phương trình."  # Root là chủ lời nói.
    assert result.tool_artifact_bundles
    tool = router.received[1]["payload"]["tool_results"][0]
    assert tool["result"]["status"] == "completed"
    assert tool["result"]["artifact_bundle_id"] in result.tool_artifact_bundles
    assert not learning.attempts()
