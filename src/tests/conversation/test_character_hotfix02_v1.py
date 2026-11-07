# -*- coding: utf-8 -*-
"""HOTFIX 02: test DS2API reasoning dài + localize TRƯỚC verifier.

Tất cả provider/localizer đều là fake, không gọi HTTP hoặc TTS thật.
"""
from __future__ import annotations

import math
from types import SimpleNamespace

import pytest

from runtime.conversation.claim_verifier import (
    CharacterClaimVerifier, _verifier_timeout_seconds,
)
from runtime.conversation.runtime_writer import _prelocalize_v03_speech
from runtime.claim_domain_contract import inspect_claim_manifest
from test_character_output_artifact_v1 import prepared, turn
from test_character_verifier_v1 import registry


def test_verifier_no_arbitrary_ten_second_timeout(monkeypatch):
    monkeypatch.delenv('ERIS_CHARACTER_VERIFIER_TIMEOUT_SEC', raising=False)
    assert _verifier_timeout_seconds() is None
    monkeypatch.setenv('ERIS_CHARACTER_VERIFIER_TIMEOUT_SEC', '240')
    assert _verifier_timeout_seconds() == 240
    monkeypatch.setenv('ERIS_CHARACTER_VERIFIER_TIMEOUT_SEC', '0')
    assert _verifier_timeout_seconds() is None
    monkeypatch.delenv('ERIS_CHARACTER_VERIFIER_TIMEOUT_SEC', raising=False)

    class Router:
        observer_client = object()
        surface_client = None
        def invoke_structured_worker(self, client, **kwargs):
            assert kwargs['timeout_seconds'] is None
            assert kwargs['cognitive_lane'] == 'public_claim_verifier'
            return {'status': 'pass', 'surface_equivalence': True,
                    'memory_equivalence': True, 'findings': []}

    _, artifact = prepared()
    checked = CharacterClaimVerifier(Router()).verify(
        prepared=artifact, manifest=inspect_claim_manifest({'claim_manifest': []}),
        move=turn().move, registry=registry(), portrait=None,
    )
    assert checked.status == 'pass'


@pytest.mark.parametrize('provider_error, expected', [
    ('VERIFIER_TIMEOUT', 'VERIFIER_TIMEOUT'),
    ('VERIFIER_PROVIDER_ERROR', 'VERIFIER_PROVIDER_ERROR'),
])
def test_provider_failures_not_disguised_as_schema_error(provider_error, expected):
    class Router:
        observer_client = object()
        surface_client = None
        def invoke_structured_worker(self, *_args, **_kwargs):
            return {'_runtime_verifier_error': provider_error}
    _, artifact = prepared()
    checked = CharacterClaimVerifier(Router()).verify(
        prepared=artifact, manifest=inspect_claim_manifest({'claim_manifest': []}),
        move=turn().move, registry=registry(), portrait=None,
    )
    assert checked.status == 'unknown'
    assert checked.reason_codes == (expected,)


def test_broker_verifier_lane_has_no_wall_clock_deadline():
    from runtime.inference_broker import lane_policy, inference_context, GLOBAL_INFERENCE_BROKER
    assert lane_policy('public_claim_verifier').optional is False
    assert lane_policy('public_claim_verifier').deadline_seconds == 0
    with inference_context('public_claim_verifier', optional=False):
        request = GLOBAL_INFERENCE_BROKER.request_from_context(
            provider='test', model='test', estimated_input_tokens=50,
            reserved_output_tokens=100, timeout_seconds=0.0,
        )
    assert math.isinf(request.deadline_at)


def test_localizer_pins_flat_and_nested_speech_before_verification():
    calls = []
    class Localizer:
        def localize(self, text, *, language_hint, strict):
            calls.append((text, language_hint, strict))
            return 'こんにちは。'
    host = SimpleNamespace(character_speech_localizer=Localizer())
    candidate = {'text': 'Chào cậu.', 'speech_text': 'Xin chào.',
                 'utterance': {'speech': {'text': 'Okaeri.', 'language': 'ja'},
                               'display': {'text': 'Chào cậu.', 'language': 'vi'}},
                 'claim_manifest': []}
    revised = _prelocalize_v03_speech(candidate, host=host, language_hint='ja')
    assert calls == [('Okaeri.', 'ja', True)]
    assert candidate['speech_text'] == 'Xin chào.'
    assert candidate['utterance']['speech']['text'] == 'Okaeri.'
    assert revised['speech_text'] == 'こんにちは。'
    assert revised['utterance']['speech']['text'] == 'こんにちは。'
    assert revised['utterance']['display']['text'] == 'Chào cậu.'
    assert revised['claim_manifest'] == []


def test_localizer_non_japanese_output_cannot_masquerade_as_verified_japanese():
    # Rescue Change 3: khi localizer trả bản KHÔNG phải tiếng Nhật ('Okaeri'),
    # nó KHÔNG được chốt làm speech_text giả-Nhật. Trước đây nhánh này raise
    # v03_speech_localizer_unavailable (-> mute). Giờ fail-SOFT: thử lại 1 lần,
    # rồi phát câu đệm Nhật AN TOÀN (fallback (b)) để vẫn CÓ TIẾNG, không câm.
    # Invariant giữ nguyên: 'Okaeri' (non-JP) KHÔNG bao giờ bị coi là JP đã verify.
    from runtime.conversation.runtime_writer import _V03_SPEECH_JA_FILLER
    from runtime.stage_contracts import _contains_japanese_script

    calls = []
    class BrokenLocalizer:
        def localize(self, *_args, **_kwargs):
            calls.append(1)
            return 'Okaeri'
    host = SimpleNamespace(character_speech_localizer=BrokenLocalizer())
    revised = _prelocalize_v03_speech({'text': 'Chào', 'speech_text': 'Okaeri'},
                                      host=host, language_hint='ja')
    assert revised['speech_text'] != 'Okaeri'
    assert revised['speech_text'] == _V03_SPEECH_JA_FILLER
    assert _contains_japanese_script(revised['speech_text'])
    assert len(calls) == 2  # thử localize 2 lần trước khi dùng filler


def test_verifier_timeout_does_not_request_second_writer_candidate():
    from test_character_hotfix_v1 import _run_with_router
    from test_character_claim_integrity_v1 import Router
    router = Router()
    # Timeout chỉ phải chặn lượt có claim/ngữ nghĩa cần model kiểm.
    router.payloads = [{'text':'Tớ chưa chắc điều đó.', 'speech_text':'それはわからない。', 'claim_manifest': []}]
    router.invoke_structured_worker = lambda *_a, **_k: {
        '_runtime_verifier_error': 'VERIFIER_TIMEOUT',
    }
    outcome, _, delivery, router = _run_with_router(router)
    assert outcome.status == 'delivering'
    assert len(router.writer_calls) == 1
    assert len(delivery.calls) == 1


@pytest.mark.parametrize('kind', ['user_turn', 'autonomous_signal'])
def test_production_writer_certifies_prelocalized_phatic_speech(kind):
    from runtime.conversation.claim_evidence import build_evidence_registry
    from runtime.conversation.contracts import ConversationEvent, NextConversationMove
    from runtime.conversation.kernel import ConversationKernel
    from runtime.conversation.runtime_writer import RuntimeConversationWriter
    from test_character_claim_integrity_v1 import Parser, Router

    class Agent:
        def decide(self, **_kwargs):
            return NextConversationMove(intent='mở lời', say='chào tự nhiên')
    class Delivery:
        def __init__(self): self.calls = []
        def deliver(self, **kwargs): self.calls.append(kwargs)
    class Localizer:
        def localize(self, text, *, language_hint, strict):
            assert text == 'Xin chào.' and language_hint == 'ja' and strict
            return 'こんにちは。'

    router = Router()
    router.payloads = [{'text': 'Chào cậu.', 'speech_text': 'Xin chào.',
                        'claim_manifest': []}]
    host = SimpleNamespace(ai_model_router=router, parser=Parser(),
                           character_speech_localizer=Localizer(),
                           ai_stage_planner=None, current_stage_user_text='',
                           chat_history=[], sent=[], last_text='')
    delivery = Delivery()
    kernel = ConversationKernel(Agent(), writer=RuntimeConversationWriter(host),
                                delivery=delivery)
    event = ConversationEvent(event_id=f'{kind}:hello', session_id='hotfix02',
                              kind=kind, text='xin chào' if kind == 'user_turn' else '')
    registry = build_evidence_registry(event=event, state=None, affect=None,
                                       report=None, ledger_turns=(), memory_records=(),
                                       sampled_at=1.)
    outcome = kernel.handle_event(event, runtime_context={'__evidence_registry': registry})
    assert outcome.status == 'delivering'
    assert len(router.writer_calls) == 1
    # Mẫu chào ngắn đã được chứng minh cơ học, không cần gọi DeepSeek.
    assert router.worker_calls == []
    assert len(delivery.calls) == 1
    assert 'claim_verification_receipt' not in delivery.calls[0]['rendered'].metadata
    artifact = delivery.calls[0]['rendered'].metadata['prepared_public_output']
    assert artifact.speech_text == 'こんにちは。'
    assert artifact.stage_frame.tts_text == 'こんにちは。'
    assert host.sent == [] and host.chat_history == []


def test_router_transports_verifier_without_default_30_second_http_timeout():
    from runtime.ai_orchestrated.model_router import AIRuntimeModelRouter
    class Client:
        base_url = 'http://127.0.0.1:6011/v1'
        model_name = 'deepseek-v4-flash'
        def create_chat_completion(self, **kwargs):
            assert kwargs['timeout'] == 0.0
            return {'choices': [{'message': {'content': '{"status":"pass"}'}}]}
    router = AIRuntimeModelRouter(object())
    result = router.invoke_structured_worker(
        Client(), system='test', payload={'prompt':'a'},
        schema={'type':'object', 'properties': {'status': {'type':'string'}}},
        name='eris_character_claim_verifier_v1', max_tokens=50,
        cognitive_lane='public_claim_verifier', timeout_seconds=None,
    )
    assert result['status'] == 'pass'


def test_router_provider_timeout_keeps_separate_reason():
    from runtime.ai_orchestrated.model_router import AIRuntimeModelRouter
    class Client:
        base_url = 'http://127.0.0.1:6011/v1'
        model_name = 'deepseek-v4-flash'
        def create_chat_completion(self, **kwargs):
            raise TimeoutError('Bearer sensitive-token')
    router = AIRuntimeModelRouter(object())
    result = router.invoke_structured_worker(
        Client(), system='test', payload={}, schema={'type':'object'},
        name='eris_character_claim_verifier_v1', max_tokens=50,
        cognitive_lane='public_claim_verifier', timeout_seconds=None,
    )
    assert result == {'_runtime_verifier_error': 'VERIFIER_TIMEOUT'}


def test_ds2api_fallback_chain_does_not_impose_90s_verifier_failfast():
    from llm.client import ChainedFallbackClient
    from runtime.inference_broker import inference_context
    from runtime.provider_health import ProviderHealthRegistry

    seen = []
    class DS2API:
        base_url = 'http://127.0.0.1:6011/v1'
        model_name = 'deepseek-v4-flash'
        key_pool = None
        worker_role = 'observer'
        def create_chat_completion(self, **kwargs):
            seen.append(kwargs['timeout'])
            return {'ok': True}
    chain = ChainedFallbackClient([DS2API()],
                                   health_registry=ProviderHealthRegistry(),
                                   health_key='unit:hotfix02')
    with inference_context('public_claim_verifier', optional=False):
        assert chain.create_chat_completion(timeout=0.0) == {'ok': True}
    assert seen == [0.0]


def test_normal_ds2api_chat_keeps_bounded_timeout():
    from llm.client import ChainedFallbackClient
    from runtime.inference_broker import inference_context
    from runtime.provider_health import ProviderHealthRegistry

    seen = []
    class DS2API:
        base_url = 'http://127.0.0.1:6011/v1'
        model_name = 'deepseek-v4-flash'
        key_pool = None
        worker_role = 'companion'
        def create_chat_completion(self, **kwargs):
            seen.append(kwargs['timeout'])
            return {'ok': True}
    chain = ChainedFallbackClient([DS2API()],
                                   health_registry=ProviderHealthRegistry(),
                                   health_key='unit:hotfix02-normal')
    with inference_context('chat_main', optional=False):
        assert chain.create_chat_completion(timeout=0.0) == {'ok': True}
    assert seen and 1 <= seen[0] <= 90


def test_foreground_parent_deadline_does_not_cut_required_verifier():
    from runtime.inference_broker import inference_context, current_inference_context
    import time
    with inference_context('chat_main', deadline_at=time.time()+3.0,
                           cancel_scope='user_turn:unit-test'):
        with inference_context('public_claim_verifier', optional=False,
                               timeout_seconds=0.0):
            child = current_inference_context()
            assert math.isinf(child.deadline_at)
            assert child.cancel_scope == 'user_turn:unit-test'
        with inference_context('chat_tool_required', optional=False,
                               timeout_seconds=0.0):
            # Không mở quyền trốn deadline cho bất kỳ lane nào khác.
            assert not math.isinf(current_inference_context().deadline_at)
