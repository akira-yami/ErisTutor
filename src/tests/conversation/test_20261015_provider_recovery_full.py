# -*- coding: utf-8 -*-
"""Regression REPAIR-01..04; không gọi DeepSeek thật và không dùng mạng."""
from __future__ import annotations

import json
import sqlite3
import time
from types import SimpleNamespace

import pytest
import requests

from llm.key_pool import ApiKeyPool
from llm.provider_errors import ProviderErrorKind, ProviderUnavailable, classify_provider_error
from runtime.conversation import ConversationEvent, ConversationKernel, NextConversationMove
from runtime.conversation.delivery_adapter import RuntimeConversationDeliveryAdapter
from runtime.turn_memory_pipeline import TurnMemoryPipeline, TurnMemoryPipelineConfig
from memory.living_user.runtime_adapter import LlmServiceUserProfileAdapter, CuratorInferenceError


def _http_response(status: int, body: str = '{}') -> requests.Response:
    response = requests.Response()
    response.status_code = status
    response.reason = 'ERR'
    response.url = 'http://localhost:6011/v1/chat/completions'
    response._content = body.encode('utf-8')
    response.headers = {'Retry-After': '17'}
    return response


def _call(client):
    return client.create_chat_completion(
        messages=[{'role': 'system', 'content': 'Only JSON'},
                  {'role': 'user', 'content': 'Reply in JSON.'}],
        temperature=0.0, frequency_penalty=0.0, presence_penalty=0.0,
        max_tokens=40, response_format={'type': 'json_object'},
    )


def test_http429_session_lease_must_not_replace_key_lease(monkeypatch):
    """429 của key 0 không được dùng SessionLease để mark_rate_limited."""
    import llm.client as c
    monkeypatch.setenv('ERIS_WEB_SESSION_MODE', 'isolated')
    monkeypatch.setattr(c, '_web_session_header', lambda *args, **kwargs: 'turn-1')
    history = []

    class Guard:
        def reserve(self, **kwargs):
            return SimpleNamespace(session_id='session', marker='web', credential=kwargs['credential'])
        def abort(self, lease):
            assert lease.marker == 'web'
        def commit(self, lease, response):
            assert lease.marker == 'web'

    monkeypatch.setattr(c, 'GLOBAL_WEB_SESSION_GUARD', Guard())

    def post(**kwargs):
        key = kwargs['headers']['Authorization']
        history.append(key)
        if key == 'Bearer sk-key1':
            resp = _http_response(429, '{"error":{"message":"rate limited"}}')
            resp.raise_for_status()
        return SimpleNamespace(
            raise_for_status=lambda: None,
            json=lambda: {'choices': [{'message': {'content': '{"ok":true}'}, 'finish_reason': 'stop'}]},
        )

    monkeypatch.setattr(c, '_brokered_post', post)
    pool = ApiKeyPool(['sk-key1', 'sk-key2'], provider='test-web-session-429')
    client = c.OpenAICompatibleClient('http://localhost:6011/v1', 'sk-key1',
                                     'deepseek-v4-flash', key_pool=pool,
                                     provider_circuit_id='test-web-session-429')
    assert _call(client)['choices'][0]['message']['content'] == '{"ok":true}'
    assert history == ['Bearer sk-key1', 'Bearer sk-key2']
    assert pool._dead_until[0] > time.time()
    # Client field bất biến, request mới lấy key qua lease riêng.
    assert client.api_key == 'sk-key1'


def test_401_only_revokes_failed_credential_and_403_is_permission():
    assert classify_provider_error(requests.HTTPError(response=_http_response(401))) == ProviderErrorKind.KEY_REVOKED
    assert classify_provider_error(requests.HTTPError(response=_http_response(403))) == ProviderErrorKind.PROJECT_PERMISSION
    pool = ApiKeyPool(['sk-keyA', 'sk-keyB'], provider='test-permanent')
    first = pool.acquire()
    pool.mark_permanently_dead(lease=first)
    assert pool.acquire().index == 1
    assert pool._dead_until[0] > time.time() + 3600


def test_chain_skips_revoked_credential_until_it_changes():
    from llm.client import ChainedFallbackClient
    health = SimpleNamespace(
        acquire=lambda *args, **kwargs: SimpleNamespace(allowed=True),
        record_success=lambda *args, **kwargs: None,
        record_degraded_success=lambda *args, **kwargs: None,
        record_failure=lambda *args, **kwargs: None,
    )
    class Bad:
        base_url = 'https://api.deepseek.com'
        model_name = 'deepseek-chat'
        api_key = 'sk-bad'
        key_pool = None
        count = 0
        def create_chat_completion(self, **kwargs):
            self.count += 1
            if self.api_key == 'sk-bad':
                raise requests.HTTPError('401 Unauthorized', response=_http_response(401))
            return {'ok': 'restored'}
    class Good:
        base_url = 'https://api.deepseek.com'
        model_name = 'deepseek-chat'
        api_key = 'sk-good'
        key_pool = None
        def create_chat_completion(self, **kwargs):
            return {'ok': 'fallback'}
    first = Bad()
    chain = ChainedFallbackClient([first, Good()], health_registry=health)
    assert chain.create_chat_completion()['ok'] == 'fallback'
    assert chain.create_chat_completion()['ok'] == 'fallback'
    assert first.count == 1
    first.api_key = 'sk-renewed'
    assert chain.create_chat_completion()['ok'] == 'restored'
    assert first.count == 2


def test_kernel_provider_down_sends_offline_vi_ja_without_writer_or_memory():
    from domain import Decision
    class Agent:
        def decide(self, **kwargs):
            raise ProviderUnavailable(ProviderErrorKind.RATE_LIMIT, role='root')
    class Writer:
        def realize(self, **kwargs):
            raise AssertionError('Writer must not run after provider outage')
    class Parser:
        def parse(self, raw, **kwargs):
            payload = json.loads(raw)
            return Decision(text=payload['text'], speech_text=payload.get('speech_text', ''),
                            animation='stand', should_speak=True)
    class Host:
        parser = Parser()
        ai_stage_planner = SimpleNamespace(apply=lambda *args, **kwargs: None)
        chat_history = []
        sent = []
        last_text = ''
        last_chat_answer = ''
        def _send_decision_to_body(self, decision, *, channel=None):
            self.sent.append((decision, channel))
    host = Host()
    kernel = ConversationKernel(Agent(), writer=Writer(), delivery=RuntimeConversationDeliveryAdapter(host))
    host.conversation_kernel = kernel
    result = kernel.handle_event(ConversationEvent.user_turn(session_id='s', text='Alo'))
    assert result.status == 'runtime_status_delivering'
    assert result.failure_reason == 'root:rate_limit'
    assert len(host.sent) == 1
    decision, channel = host.sent[0]
    assert channel == 'chat'
    assert 'trục trặc' in decision.text
    assert '調子' in decision.speech_text
    assert host.chat_history == []
    assert kernel.continuation.peek('s') is None


def test_offline_writer_only_for_real_provider_error():
    from runtime.conversation.writer import RenderedConversationMove
    class Agent:
        def decide(self, **kwargs):
            return NextConversationMove(intent='reply', say='reply')
    class Writer:
        def realize(self, **kwargs):
            raise requests.Timeout('offline')
    class Delivery:
        def __init__(self):
            self.calls = []
        def deliver(self, **kwargs):
            self.calls.append(kwargs)
    delivery = Delivery()
    kernel = ConversationKernel(Agent(), writer=Writer(), delivery=delivery)
    result = kernel.handle_event(ConversationEvent.user_turn(session_id='s', text='hello'))
    assert result.status == 'runtime_status_delivering'
    assert len(delivery.calls) == 1
    assert delivery.calls[0]['rendered'].metadata['runtime_status_only'] is True


def test_curator_adapter_timeout_is_not_success_empty():
    class Client:
        def create_chat_completion(self, **kwargs):
            raise requests.Timeout('offline')
    with pytest.raises(CuratorInferenceError, match='timeout'):
        LlmServiceUserProfileAdapter(SimpleNamespace(client=Client())).propose_profile_operations({'x': 1})
    class Good:
        def create_chat_completion(self, **kwargs):
            return {'choices': [{'message': {'content': '{"operations":[]}'}}]}
    assert LlmServiceUserProfileAdapter(SimpleNamespace(client=Good())).propose_profile_operations({'x': 1}) == {'operations': []}


def test_curator_inbox_replays_turn_across_restart(tmp_path):
    from runtime.curator_inbox import CuratorInbox
    from runtime.turn_memory_pipeline import TurnMemoryEnvelope
    path = str(tmp_path / 'core' / 'curator_inbox.sqlite3')
    original = TurnMemoryEnvelope('Tôi thích mưa', 'turn_12', ('turn_12',), envelope_id='abc')
    db = CuratorInbox(path)
    db.put(original)
    assert db.count() == 1
    new_process = CuratorInbox(path)
    did, payload, status, _ = new_process.due()
    assert did == 'abc' and payload['text'] == original.text
    assert status == 'pending'
    new_process.retry(did)
    assert new_process.due() is None
    with sqlite3.connect(path) as conn:
        conn.execute('UPDATE curator_inbox SET next_at = 0 WHERE envelope_id=?', (did,))
    assert new_process.due()[0] == did
    new_process.done(did)
    assert CuratorInbox(path).count() == 0


def test_http200_gateway_error_json_treated_as_auth_error(monkeypatch):
    import llm.client as c
    monkeypatch.setenv('ERIS_WEB_SESSION_MODE', 'off')
    seen = []
    pool = ApiKeyPool(['sk-error', 'sk-ok'], provider='test-error-envelope')
    class Response:
        headers = {}
        def raise_for_status(self):
            pass
        def __init__(self, key):
            self.key = key
        def json(self):
            if self.key == 'sk-error':
                return {'error': {'code': 401, 'message': 'unauthorized'}}
            return {'choices': [{'message': {'content': '{"ok":1}'}, 'finish_reason': 'stop'}]}
    def post(**kwargs):
        key = kwargs['headers']['Authorization'].replace('Bearer ', '')
        seen.append(key)
        return Response(key)
    monkeypatch.setattr(c, '_brokered_post', post)
    client = c.OpenAICompatibleClient('http://localhost:6011/v1', 'sk-error',
                                     'deepseek-v4-flash', key_pool=pool,
                                     provider_circuit_id='test-error-envelope')
    assert _call(client)['choices'][0]['message']['content'] == '{"ok":1}'
    assert seen == ['sk-error', 'sk-ok']
    assert pool._dead_until[0] > time.time() + 3600


def test_curator_pipeline_replays_inbox_from_previous_process(tmp_path):
    import threading
    from runtime.turn_memory_pipeline import TurnMemoryEnvelope
    from memory.living_user.models import ProfileOperation
    class Kernel:
        core = SimpleNamespace(base_dir=str(tmp_path / 'core'))
    class Living:
        config = SimpleNamespace(auto_observe_turns=True)
        def __init__(self):
            self.done = threading.Event()
            self.calls = 0
        def propose_turn_operations(self, text, *, evidence_ids, context=None):
            self.calls += 1
            return (ProfileOperation(op='add', path='communication.preferred_language', value='vi',
                                     evidence_ids=tuple(evidence_ids)),)
        def apply_turn_operations(self, operations, *, trace_context=None):
            self.done.set()
            return SimpleNamespace(applied=1)
    config = TurnMemoryPipelineConfig(enabled=True, curator_enabled=True)
    # Process 1 ghi disk trước khi worker tiêu thụ, không cần API network.
    first = TurnMemoryPipeline(Kernel(), living_user=Living(), config=config)
    envelope = TurnMemoryEnvelope('Tôi nói tiếng Việt', 'turn7', ('turn7',), envelope_id='retry-e7')
    assert first._enqueue_curator(envelope)
    assert first._curator_inbox.count() == 1
    # Process 2 tạo queue RAM mới, restore pending từ SQLite.
    living = Living()
    second = TurnMemoryPipeline(Kernel(), living_user=living, config=config)
    second.start()
    try:
        assert living.done.wait(3.0)
        deadline = time.monotonic() + 2.0
        while second._curator_inbox.count() and time.monotonic() < deadline:
            time.sleep(0.02)
        assert second._curator_inbox.count() == 0
        assert living.calls == 1
    finally:
        second.stop()
