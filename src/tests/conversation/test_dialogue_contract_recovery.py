from __future__ import annotations

import json
import threading
from types import SimpleNamespace

import pytest

from llm.deepseek_workers import JsonlWorkerClient
from llm.dialogue_recovery import recover_dialogue
from runtime.ai_orchestrated.model_router import AIRuntimeModelRouter
from runtime.conversation.agent import ROOT_STEP_SCHEMA, RuntimeModelRootAgent
from runtime.conversation.contracts import ConversationEvent, NextConversationMove, NoReply
from runtime.conversation.kernel import KernelTurn
from runtime.conversation.runtime_writer import RuntimeConversationWriter


DIALOGUE = 'Alo alo, tôi nghe đây. Bạn gọi tôi à?'


def response(text):
    return {"choices": [{"message": {"content": text, "reasoning_content": "PRIVATE_SENTINEL"},
                         "finish_reason": "stop"}]}


class RawClient:
    model_name = "deepseek-flash"
    base_url = "http://localhost/v1"

    def __init__(self, text):
        self.text = text
        self.calls = []

    def create_chat_completion(self, **kwargs):
        self.calls.append(kwargs)
        return response(self.text)


class BlockingCritic(RawClient):
    def __init__(self):
        super().__init__(json.dumps({"intent": "Đáp lời chào", "delivery": "thân mật",
                                   "after": "may_continue", "notes": "Khôi phục metadata",
                                   "say": "MUST_NOT_REPLACE", "actions": ["MUST_NOT_EXECUTE"]}))
        self.started = threading.Event()
        self.release = threading.Event()

    def create_chat_completion(self, **kwargs):
        self.started.set()
        assert self.release.wait(3)
        return super().create_chat_completion(**kwargs)


def make_router(text, *, role="executive", critic=None):
    raw = RawClient(text)
    worker = JsonlWorkerClient(raw, role=role, repair_attempts=1)
    service = SimpleNamespace(executive_client=worker, surface_client=worker,
                              critic_client=critic, client=worker)
    router = AIRuntimeModelRouter(service)
    router._run_inference = lambda _tier, func, **kwargs: func()
    return router, raw


def ask(router, *, raw_sink=None):
    return router.invoke_structured_worker(
        router.executive_client, system="Return action JSON", payload={"user_text": "alo"},
        schema=ROOT_STEP_SCHEMA, name="eris_root_conversation_step_v12", max_tokens=1200,
        raw_reply_sink=raw_sink,
    )


def test_root_prose_returns_before_critic_finishes_and_keeps_wire():
    critic = BlockingCritic()
    router, raw = make_router(DIALOGUE, critic=critic)
    try:
        wire = []
        result = ask(router, raw_sink=wire)
        assert result["say"] == DIALOGUE
        assert result["_runtime_literal_dialogue"] is True
        assert result["after"] == "wait_user"
        assert len(raw.calls) == 1  # no executive format retry
        assert wire == [DIALOGUE]  # never fabricate a JSON wire transcript
        assert critic.started.wait(1)
        assert router.contract_repair_context() == ""
        critic.release.set()
        router._contract_repair._queue.join()
        context = router.contract_repair_context()
        assert "Đáp lời chào" in context
        assert "PRIVATE_SENTINEL" not in context
        assert "MUST_NOT" not in context
        assert result["after"] == "wait_user"  # background cannot change live lifecycle
        assert result["say"] == DIALOGUE
        assert critic.calls[0]["timeout"] == 20.0
        assert critic.calls[0]["messages"][1]["role"] == "user"
    finally:
        critic.release.set()
        router.close_contract_repair()


def test_public_surface_prose_is_kept_without_json_retry():
    router, raw = make_router(DIALOGUE, role="surface")
    try:
        result = router.generate_public_reply(chat_history=[], user_text="alo", allow_language_repair=False)
        assert result["text"] == DIALOGUE
        assert result["should_speak"] is True
        assert len(raw.calls) == 1
    finally:
        router.close_contract_repair()


def test_root_agent_and_writer_preserve_dialogue_and_localize(monkeypatch):
    monkeypatch.setenv("ERIS_ROOT_SESSION_BOOTSTRAP", "0")
    monkeypatch.setenv("ERIS_TTS_LANGUAGE", "ja")
    router, raw = make_router(DIALOGUE)
    event = ConversationEvent(event_id="e", session_id="s", kind="user_turn", text="alo")
    localized = []
    try:
        move = RuntimeModelRootAgent(router).decide(event=event, recent_history=(), note="", runtime_context={})
        assert isinstance(move, NextConversationMove)
        assert move.literal_dialogue
        from domain import Decision

        class Parser:
            def parse(self, raw, **kwargs):
                obj = json.loads(raw)
                return Decision(text=obj["text"], speech_text=obj.get("speech_text", ""),
                                animation="stand", should_speak=True)

        class Localizer:
            def localize(self, text, **kwargs):
                localized.append(text)
                return "もしもし、聞こえてるよ。"

        host = SimpleNamespace(ai_model_router=router, parser=Parser(), logger=None,
                               ai_stage_planner=None, character_speech_localizer=Localizer(),
                               current_stage_user_text="alo", chat_history=[])
        turn = KernelTurn(session_id="s", generation=1, run_id="r", move=move, note="", delivery_id="d")
        rendered = RuntimeConversationWriter(host).realize(
            turn=turn, event=event, recent_history=(), runtime_context={},
        )
        assert rendered.surface.display_text == DIALOGUE
        assert rendered.surface.speech_text == "もしもし、聞こえてるよ。"
        assert localized == [DIALOGUE]
        assert len(raw.calls) == 1  # surface does not rewrite recovered Root dialogue
    finally:
        router.close_contract_repair()


def test_preemption_still_discards_recovered_old_turn(monkeypatch):
    monkeypatch.setenv("ERIS_ROOT_SESSION_BOOTSTRAP", "0")
    router, _ = make_router(DIALOGUE)
    checks = iter([True, False])
    try:
        decision = RuntimeModelRootAgent(router).decide(
            event=ConversationEvent(event_id="e", session_id="s", kind="user_turn", text="alo"),
            recent_history=(), note="", runtime_context={"__root_run_active": lambda: next(checks)},
        )
        assert isinstance(decision, NoReply)
        assert decision.reason == "root_turn_preempted"
    finally:
        router.close_contract_repair()


@pytest.mark.parametrize("raw", ["", '<think>private</think>', '{"kind":"recall_memory","query":"x"}',
                                 '["private"]', '{"kind":"move","say":false}'])
def test_no_dialogue_is_invented_from_internal_output(raw):
    assert recover_dialogue(raw, {"json_schema": {"name": "eris_root_conversation_step_v12"}}) is None


def test_partial_root_json_salvages_only_complete_say_string():
    raw = '{"kind":"move","say":"Alo, tôi đây.","intent":'
    result = recover_dialogue(raw, {"json_schema": {"name": "eris_root_conversation_step_v12"}})
    assert result["say"] == "Alo, tôi đây."


def test_valid_internal_actions_keep_their_original_contract():
    raw = '{"kind":"recall_memory","query":"x"}'
    router, client = make_router(raw)
    try:
        assert ask(router) == {"kind": "recall_memory", "query": "x"}
        assert len(client.calls) == 1
        assert router._contract_repair is None
    finally:
        router.close_contract_repair()


def test_broken_tool_action_is_not_promoted_to_dialogue():
    raw = '{"kind":"use_capability","say":"run this", "arguments":'
    assert recover_dialogue(raw, {"json_schema": {"name": "eris_root_conversation_step_v12"}}) is None


def test_failed_critic_does_not_remove_dialogue():
    critic = RawClient("invalid metadata")
    router, raw = make_router(DIALOGUE, critic=critic)
    try:
        result = ask(router)
        router._contract_repair._queue.join()
        assert result["say"] == DIALOGUE
        assert len(raw.calls) == 1
        assert router.contract_repair_context() == ""
    finally:
        router.close_contract_repair()


def test_background_start_failure_does_not_drop_dialogue(monkeypatch):
    from runtime.ai_orchestrated import contract_repair
    def fail(*args):
        raise RuntimeError("cannot start thread")
    monkeypatch.setattr(contract_repair, "BackgroundContractRepair", fail)
    router, raw = make_router(DIALOGUE)
    assert ask(router)["say"] == DIALOGUE
    assert len(raw.calls) == 1


def test_repair_feedback_has_a_runtime_context_consumer(monkeypatch):
    from runtime.conversation.context_assembler import ConversationContextAssembler
    import runtime.conversation.context_assembler as assembler
    monkeypatch.setattr(assembler, "ensure_world_state_service", lambda host: SimpleNamespace(
        prompt_context=lambda *a, **k: "world"))
    host = SimpleNamespace(ai_model_router=SimpleNamespace(contract_repair_context=lambda: "REPAIR_FEEDBACK"))
    context = ConversationContextAssembler(host)._world_context("chat")
    assert "REPAIR_FEEDBACK" in context


def test_recovered_dialogue_does_not_trigger_official_provider_fallback():
    from llm.client import ChainedFallbackClient
    from runtime.provider_health import ProviderHealthRegistry
    router, raw = make_router(DIALOGUE)
    fallback = RawClient("MUST_NOT_CALL")
    router.llm_service.executive_client = ChainedFallbackClient(
        [router.llm_service.executive_client, fallback], health_registry=ProviderHealthRegistry(),
    )
    try:
        assert ask(router)["say"] == DIALOGUE
        assert len(raw.calls) == 1
        assert fallback.calls == []
    finally:
        router.close_contract_repair()
