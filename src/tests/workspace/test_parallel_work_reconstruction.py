# -*- coding: utf-8 -*-
"""Kiểm chứng luồng giải dài không được khóa luồng thoại."""
import threading
import time
from dataclasses import dataclass

from runtime.workspace.parallel_work import ParallelWorkService


@dataclass
class ToolResult:
    success: bool = True
    message: str = "Đã kiểm chứng bằng công cụ"
    evidence: tuple[str, ...] = ("math:verified",)
    data: dict = None


def _until(test, limit=3):
    end = time.monotonic() + limit
    while time.monotonic() < end:
        if test():
            return
        time.sleep(.01)
    raise AssertionError("Không đạt trạng thái mong đợi")


def test_chat_doc_trong_khi_work_tool_dang_cho(tmp_path):
    inside_tool = threading.Event()
    release_tool = threading.Event()
    tools = []
    seen = []

    def model(ctx):
        kinds = [e["kind"] for e in ctx["recent_events"]]
        if "tool_result" in kinds:
            return {"kind": "complete", "text": "Đã kiểm chứng nghiệm.",
                    "action_id": "", "arguments": {}}
        return {"kind": "tool", "text": "Đang kiểm chứng biểu thức",
                "action_id": "math_workspace", "arguments": {"kind": "solve", "expression": "x**2-4"}}

    def tool(name, args):
        tools.append((name, args))
        inside_tool.set()
        assert release_tool.wait(2)
        return ToolResult(data={"result": "-2, 2"})

    svc = ParallelWorkService(tmp_path / "jobs.sqlite", planner=model, tool=tool, publish=seen.append)
    start_at = time.monotonic()
    job = svc.start("Giải phương trình")
    assert time.monotonic() - start_at < .25
    assert inside_tool.wait(2)
    # Chat vẫn có thể đọc trạng thái hoặc chọn nói, không đợi toán xong.
    t = time.monotonic()
    current = svc.snapshot(job["job_id"])
    assert current["status"] == "running"
    assert time.monotonic() - t < .1
    release_tool.set()
    _until(lambda: svc.snapshot(job["job_id"])["status"] == "completed")
    assert tools[0][0] == "math_workspace"
    assert svc.snapshot(job["job_id"])["last_evidence"]
    assert any(x["kind"] == "tool_result" for x in seen)


def test_cancel_khong_commit_ket_qua_tool_muon(tmp_path):
    inside = threading.Event()
    release = threading.Event()
    def planner(ctx):
        return {"kind": "tool", "text": "test", "action_id": "math_workspace", "arguments": {"kind": "matrix"}}
    def tool(*_):
        inside.set()
        release.wait(2)
        return ToolResult()
    svc = ParallelWorkService(tmp_path / "c.sqlite", planner=planner, tool=tool)
    jid = svc.start("Kiểm chứng")["job_id"]
    assert inside.wait(2)
    svc.cancel(jid)
    release.set()
    _until(lambda: svc.snapshot(jid)["status"] == "cancelled")
    assert not any(e["kind"] == "tool_result" for e in svc.events(jid))
    assert any(e["kind"] == "late_tool_result_discarded" for e in svc.events(jid))


def test_restart_khong_tu_replay_side_effect(tmp_path):
    db = tmp_path / "restart.sqlite"
    gate = threading.Event()
    inside = threading.Event()
    def planner(ctx):
        return {"kind": "tool", "text": "", "action_id": "math_workspace", "arguments": {"kind": "matrix"}}
    def tool(*_):
        inside.set(); gate.wait(2)
        return ToolResult()
    svc = ParallelWorkService(db, planner=planner, tool=tool)
    jid = svc.start("Giải lâu")["job_id"]
    assert inside.wait(2)
    # Giả lập mở một runtime mới trước khi tiến trình cũ trả receipt.
    restarted = ParallelWorkService(db, planner=None)
    assert restarted.snapshot(jid)["status"] == "needs_review"
    gate.set()


def test_khong_co_model_khong_gia_hoan_thanh(tmp_path):
    svc = ParallelWorkService(tmp_path / "missing.sqlite", planner=None)
    jid = svc.start("Bài khó")["job_id"]
    _until(lambda: svc.snapshot(jid)["status"] == "needs_review")
    assert "Chưa nối model" in svc.snapshot(jid)["summary"]


def test_cam_tool_ngoai_pham_vi(tmp_path):
    svc = ParallelWorkService(tmp_path / "bad.sqlite", planner=lambda _: {
        "kind":"tool", "text":"", "action_id":"open_url", "arguments":{"url":"https://example.com"}},
        tool=lambda *_: (_ for _ in ()).throw(AssertionError("Không được chạy")))
    jid = svc.start("Kiểm tra")["job_id"]
    _until(lambda: svc.snapshot(jid)["status"] == "failed")
    assert any(e["kind"] == "error" for e in svc.events(jid))


def test_doc_khong_bi_dich_thanh_speech(tmp_path):
    from runtime.conversation.contracts import NextConversationMove
    from runtime.conversation.runtime_writer import _prelocalize_v03_speech
    from runtime.workspace.live_document import LiveDocument
    from runtime.workspace.structured_output import text_digest
    import pytest

    move = NextConversationMove.from_mapping({
        "kind": "move", "intent":"giải toán", "say":"Cậu xem phép biến đổi này đi.",
        "document_source": "# Giải toán\n```python\nprint(42)\n```\n$$x^2=4$$"
    })
    assert "```python\n" in move.document_source
    assert move.to_dict()["document_source"] == move.document_source
    with pytest.raises(RuntimeError, match="v03_document_speech_missing"):
        _prelocalize_v03_speech({"text": "Cậu xem bên panel", "speech_text": "",
                                "_separate_document": True}, host=object(), language_hint="vi")
    result = _prelocalize_v03_speech({"text": "Cậu xem bên panel", "speech_text": "Đọc câu này.",
                                      "_separate_document": True}, host=object(), language_hint="vi")
    assert result["speech_text"] == "Đọc câu này."
    doc = LiveDocument("s", 1, "r", "d", "t", move.document_source,
                       reference_text="Cậu xem bên panel")
    op = doc.operate("begin")
    assert op["text_hash"] == text_digest("Cậu xem bên panel")


def test_root_move_document_tach_khoi_prompt_writer():
    from types import SimpleNamespace
    from runtime.conversation.runtime_writer import RuntimeConversationWriter
    from runtime.conversation.kernel import KernelTurn
    from runtime.conversation.contracts import NextConversationMove, ConversationEvent
    class Router:
        def __init__(self): self.received = None
        def generate_public_reply(self, **kwargs):
            self.received = kwargs
            return {"text": "Cậu nhìn Panel nhé.", "speech_text": "Cậu nhìn Panel nhé."}
    router = Router()
    move = NextConversationMove(intent="Giảng toán", say="Nói ít để mở bảng",
                                document_source="```python\nsecret_value = 42\n```\n$$x^2=4$$")
    turn = KernelTurn(session_id="s", generation=1, run_id="r", move=move, note="")
    writer = RuntimeConversationWriter(SimpleNamespace(ai_model_router=router))
    result = writer.realize(turn=turn, event=ConversationEvent.user_turn(session_id="s", text="giải"),
                            recent_history=(), runtime_context={})
    assert "secret_value" not in router.received["discourse_context"]
    assert "Nói ít để mở bảng" in router.received["discourse_context"]
    assert result.decision_payload["document_source"] == move.document_source


def test_capability_executor_nhan_job_nhanh(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from actions.executor import ActionExecutor
    from runtime.workspace.parallel_work import ParallelWorkService
    executor = ActionExecutor()
    work = ParallelWorkService(tmp_path / "jobs.sqlite", planner=None)
    executor._parallel_work_service = work
    created = executor._parallel_work('{"operation":"start","task":"Kiểm chứng"}')
    assert created.success
    import json
    job = json.loads(created.data["job"])
    read = executor._parallel_work(json.dumps({"operation":"status","job_id":job["job_id"]}))
    assert read.success
    recent = executor._parallel_work('{"operation":"recent"}')
    assert job["job_id"] in recent.data["job"]


def test_hop_dong_document_dung_sidecar_khong_tinh_vao_hash_speech():
    from dataclasses import replace
    from runtime.conversation.public_output_artifact import PreparedPublicOutput
    from runtime.workspace.live_document import LiveWorkspaceService
    # Bản tài liệu thay đổi không được động đến chữ đã commit cho TTS.
    original = PreparedPublicOutput(event_id="e", session_id="s", generation=1,
                                    run_id="r", move_id="m", delivery_id="d",
                                    channel="chat", display_text="Mời xem Panel.",
                                    speech_text="Mời xem Panel.", memory_text="Mời xem Panel.",
                                    stage_frame=None, segments=(), payload={},
                                    source_fingerprint="test")
    changed = replace(original, document_source="$$x^2=4$$")
    assert changed.display_text == original.display_text
    assert changed.speech_text == original.speech_text


def test_capability_schema_parallel_work_tu_root():
    from actions.capability_registry import validate_capability_arguments, encode_capability_arguments
    ok, reason = validate_capability_arguments("parallel_work", {"operation":"start", "task":"Giải bài"})
    assert ok, reason
    encoded = encode_capability_arguments("parallel_work", {"operation":"recent"})
    assert '"recent"' in encoded
    ok, _ = validate_capability_arguments("parallel_work", {"operation":"delete_all"})
    assert not ok


def test_resume_chi_sau_khi_nguoi_dung_tra_loi(tmp_path):
    def planner(ctx):
        if "user_answer" in [x["kind"] for x in ctx["recent_events"]]:
            return {"kind":"complete", "text":"Đã nhận bổ sung và hoàn tất.",
                    "action_id":"", "arguments":{}}
        return {"kind":"wait_user", "text":"Bạn muốn dùng cách nào?", "action_id":"", "arguments":{}}
    svc = ParallelWorkService(tmp_path / "resume.sqlite", planner=planner)
    jid = svc.start("Dạy bài")["job_id"]
    _until(lambda: svc.snapshot(jid)["status"] == "waiting_user")
    assert svc.resume(jid, "Tôi muốn cách 2")["status"] == "queued"
    _until(lambda: svc.snapshot(jid)["status"] == "completed")
    assert svc.resume(jid, "Thử lại") == {"error":"job_not_waiting_user"}


def test_root_duoc_cap_nhat_tien_do_khong_can_cho_job(tmp_path):
    from types import SimpleNamespace
    from runtime.conversation.kernel import ConversationKernel
    from runtime.conversation.agent import _bounded_context
    gate = threading.Event()
    entered = threading.Event()
    svc = ParallelWorkService(tmp_path / "active.sqlite", planner=lambda ctx: (
        entered.set(), gate.wait(1),
        {"kind":"complete","text":"Hoàn tất","action_id":"","arguments":{}}
    )[-1])
    jid = svc.start("Giải bài dài")["job_id"]
    assert entered.wait(1)
    host = SimpleNamespace(action_executor=SimpleNamespace(_parallel_work_service=svc))
    kernel = ConversationKernel(agent=object(), writer=SimpleNamespace(host=host))
    ctx = kernel._agent_runtime_context("s", {})
    public = _bounded_context(ctx)
    assert public["work_lane_progress"][0]["job_id"] == jid
    assert public["work_lane_progress"][0]["status"] == "running"
    gate.set()
