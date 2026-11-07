# -*- coding: utf-8 -*-
"""Hồi quy theo design: speculative không bao giờ phát trước lease/đè user."""
from __future__ import annotations

from types import SimpleNamespace

from runtime.conversation import (
    ConversationAfter, ConversationKernel, ContinuationLeaseScheduler,
    NextConversationMove, RenderedConversationMove, SurfaceEnvelope,
)
from runtime.tutor.auto_path import propose_learning_path, maybe_autostart_path


class Agent:
    def __init__(self):
        self.calls = 0

    def decide(self, **kwargs):
        self.calls += 1
        return NextConversationMove(intent="tiếp mạch", say="Một ý mới", after=ConversationAfter.WAIT_USER)


class Writer:
    def __init__(self):
        self.calls = 0

    def realize(self, **kwargs):
        self.calls += 1
        return RenderedConversationMove(
            surface=SurfaceEnvelope(display_text="câu đã chuẩn bị"),
            decision_payload=kwargs["turn"].move.to_dict(),
        )


class Delivery:
    def __init__(self):
        self.calls = 0

    def deliver(self, **kwargs):
        self.calls += 1


def test_speculative_prepare_is_not_public_until_matching_due():
    agent, writer, delivery = Agent(), Writer(), Delivery()
    kernel = ConversationKernel(agent, writer=writer, delivery=delivery)
    generation = kernel.preempt_for_user("s").generation
    lease = kernel.continuation.schedule(
        session_id="s", generation=generation, move_id="source",
        after=ConversationAfter.MAY_CONTINUE, now=10, may_continue_delay=1.2,
    )
    assert lease is not None
    prepared = kernel.prepare_continuation_candidate(lease=lease)
    assert prepared is not None and prepared.status == "speculative_ready"
    assert agent.calls == 1 and writer.calls == 1 and delivery.calls == 0
    assert kernel.handle_due_continuation(session_id="s", now=10.5) is None
    delivered = kernel.handle_due_continuation(session_id="s", now=11.5)
    assert delivered is not None and delivered.status == "delivering"
    assert agent.calls == 1 and writer.calls == 1 and delivery.calls == 1
    assert kernel.continuation.peek("s") is None


def test_user_preempts_prepared_candidate_and_keeps_voice_silent():
    agent, writer, delivery = Agent(), Writer(), Delivery()
    kernel = ConversationKernel(agent, writer=writer, delivery=delivery)
    generation = kernel.preempt_for_user("s").generation
    lease = kernel.continuation.schedule(
        session_id="s", generation=generation, move_id="source",
        after=ConversationAfter.CONTINUE_SELF, now=10, continue_self_delay=.15,
    )
    assert lease is not None
    kernel.prepare_continuation_candidate(lease=lease)
    assert delivery.calls == 0
    kernel.preempt_for_user("s")
    assert kernel.continuation.peek("s") is None
    assert kernel.handle_due_continuation(session_id="s", now=11) is None
    assert delivery.calls == 0


def test_lease_mismatch_cannot_replay_old_candidate():
    scheduler = ContinuationLeaseScheduler()
    first = scheduler.schedule(session_id="s", generation=1, move_id="1",
                               after=ConversationAfter.MAY_CONTINUE, now=10)
    assert scheduler.offer_speculative(first, "old")
    second = scheduler.schedule(session_id="s", generation=2, move_id="2",
                                after=ConversationAfter.MAY_CONTINUE, now=10)
    assert scheduler.take_speculative(second) is None
    assert scheduler.offer_speculative(first, "old") is False


class PlanRouter:
    def __init__(self, *, value=True):
        self.value = value
        self.calls = 0

    def plan_teaching_episode(self, *, request_context):
        self.calls += 1
        if not self.value:
            return None
        return {
            "global_goal": "Hiểu lập trình Python",
            "success_evidence": ["Tự viết được hàm"],
            "concepts": [
                {"id": "a", "objective": "Biến", "description": "Khởi tạo biến", "prerequisite_ids": []},
                {"id": "b", "objective": "Hàm", "description": "Khai báo hàm", "prerequisite_ids": ["a"]},
                {"id": "c", "objective": "Ứng dụng", "description": "Viết chương trình", "prerequisite_ids": ["b"]},
            ],
        }


class Learning:
    def __init__(self):
        self.path = None

    def active_path(self):
        return self.path

    def register_domain(self, domain):
        self.domain = domain

    def register_path(self, path, *, now=None):
        self.path = path

    def activate_path(self, path_id):
        return self.path.path_id == path_id


def test_auto_path_uses_model_concepts_and_durable_source():
    router = PlanRouter()
    path = propose_learning_path(router, topic_text="Dạy tôi Python")
    assert path is not None and path.source == "eris_auto"
    assert len(path.knowledge_points) == 3
    assert path.domain_id
    assert all(kp.kp_id.startswith(path.domain_id + ":") for kp in path.knowledge_points)
    assert path.knowledge_points[1].prerequisites == (path.knowledge_points[0].kp_id,)
    assert propose_learning_path(PlanRouter(value=False), topic_text="Dạy tôi Python") is None


def test_autostart_is_idempotent_and_not_triggered_by_single_trivia():
    router, learning = PlanRouter(), Learning()
    host = SimpleNamespace(ai_model_router=router, learning_service=learning)
    assert maybe_autostart_path(host, user_text="Pikachu là gì?") is False
    assert router.calls == 0
    assert maybe_autostart_path(host, user_text="Dạy tôi Python từ gốc") is True
    assert maybe_autostart_path(host, user_text="Dạy tôi lại") is False
    assert router.calls == 1


def test_autopath_survives_learning_service_restart(tmp_path):
    from runtime.learning_core import LearningService, LearningStore
    db = tmp_path / "learning.sqlite3"
    learning = LearningService(LearningStore(db))
    host = SimpleNamespace(ai_model_router=PlanRouter(), learning_service=learning)
    assert maybe_autostart_path(host, user_text="Dạy tôi Python từ gốc")
    before = learning.active_path()
    assert before is not None
    restarted = LearningService(LearningStore(db))
    after = restarted.active_path()
    assert after is not None and after.path_id == before.path_id
    assert after.source == "eris_auto"
    assert after.knowledge_points[2].prerequisites == (after.knowledge_points[1].kp_id,)


def test_b_confusion_opens_path_with_substantive_context():
    router, learning = PlanRouter(), Learning()
    host = SimpleNamespace(
        ai_model_router=router, learning_service=learning,
        conversation_handoff_runtime=SimpleNamespace(snapshot=lambda: SimpleNamespace(
            repair_needed=True, user_stance="confused", topic_closed=False,
            last_assistant_text="Tại sao vòng lặp for trong Python lại thay đổi giá trị biến đếm, "
                                "và làm sao để ứng dụng cơ chế này trong một ví dụ thực tế?"
                                " Hãy thử nghĩ xem ta cần lặp bao nhiêu lần để giải quyết bài toán.",
        )),
    )
    history = ({"role": "assistant", "content": "Đầu tiên ta định nghĩa vòng lặp."},
               {"role": "user", "content": "Tôi muốn xem ví dụ."})
    assert maybe_autostart_path(host, user_text="Tôi chưa hiểu đoạn này", recent_history=history)
    assert router.calls == 1


def test_preempt_during_speculative_model_call_cannot_deliver():
    import threading
    started, release = threading.Event(), threading.Event()

    class BlockAgent(Agent):
        def decide(self, **kwargs):
            started.set()
            assert release.wait(timeout=2)
            return super().decide(**kwargs)

    agent, writer, delivery = BlockAgent(), Writer(), Delivery()
    kernel = ConversationKernel(agent, writer=writer, delivery=delivery)
    generation = kernel.preempt_for_user("s").generation
    lease = kernel.continuation.schedule(
        session_id="s", generation=generation, move_id="source",
        after=ConversationAfter.MAY_CONTINUE, now=10,
    )
    holder = []
    thread = threading.Thread(
        target=lambda: holder.append(kernel.prepare_continuation_candidate(lease=lease)),
    )
    thread.start()
    assert started.wait(timeout=2)
    kernel.preempt_for_user("s")
    release.set()
    thread.join(timeout=2)
    assert not thread.is_alive()
    assert holder[0].status == "stale"
    assert writer.calls == 0 and delivery.calls == 0


def test_prepared_no_reply_does_not_call_root_twice():
    from runtime.conversation import NoReply

    class Quiet(Agent):
        def decide(self, **kwargs):
            self.calls += 1
            return NoReply(reason="mạch đã trọn")

    agent, writer, delivery = Quiet(), Writer(), Delivery()
    kernel = ConversationKernel(agent, writer=writer, delivery=delivery)
    generation = kernel.preempt_for_user("s").generation
    lease = kernel.continuation.schedule(
        session_id="s", generation=generation, move_id="source",
        after=ConversationAfter.MAY_CONTINUE, now=10,
    )
    assert kernel.prepare_continuation_candidate(lease=lease).status == "no_reply"
    result = kernel.handle_due_continuation(session_id="s", now=12)
    assert result is not None and result.status == "no_reply"
    assert agent.calls == 1 and writer.calls == 0 and delivery.calls == 0
