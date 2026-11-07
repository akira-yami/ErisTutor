# -*- coding: utf-8 -*-
"""V2: test đối kháng loại action, đích, số lượng, trạng thái và public path."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from runtime.conversation.action_claim_integrity import (
    detected_completed_actions, enforce_completed_action_claims,
)
from runtime.conversation.context import RootCapabilityRegistry
from runtime.conversation.contracts import ConversationEvent, NextConversationMove
from runtime.conversation.kernel import KernelTurn
from runtime.conversation.runtime_writer import RuntimeConversationWriter

EVENT = ConversationEvent(event_id='event:action-v2', session_id='session:v2',
                          generation=12, kind='user_turn', text='xin chào')
RUN = 'run:v2'


def receipts(*items):
    """Tạo receipt từ Registry thực, không mock dataclass từ lời Writer."""
    registry = RootCapabilityRegistry({})
    for idx, item in enumerate(items):
        registry.record_result(name=item.get('capability', 'action'),
                               invocation_id=item.get('invocation_id', f'inv:{idx}'),
                               event=item.get('event', EVENT), run_id=item.get('run_id', RUN),
                               result={k: v for k, v in item.items()
                                       if k not in ('capability', 'invocation_id', 'event', 'run_id')})
    return registry


def done(action_id, **extras):
    return dict(action_id=action_id, status='completed', success=True, **extras)


def observed(action_id, *, data=None, action_name=None):
    return dict(action_id=action_id, status='observed', success=True,
                data=data or {}, public_meta={
                    'action_name': action_name or action_id, 'success': True,
                    'observed': True, 'dispatched': True,
                    'public_mode': 'observed_success',
                })


def dispatched(action_id):
    return dict(action_id=action_id, status='dispatched', success=True,
                public_meta={'action_name': action_id, 'success': True,
                             'observed': False, 'dispatched': True,
                             'public_mode': 'request_sent'})


def check(text, *results, surfaces=()):
    reg = receipts(*results)
    enforce_completed_action_claims(
        surfaces=(text, *surfaces), receipts=reg.result_log,
        event_id=EVENT.event_id, session_id=EVENT.session_id,
        run_id=RUN, generation=EVENT.generation,
    )


@pytest.mark.parametrize(('claim', 'wrong_action'), [
    ('Tớ đã gửi email rồi.', 'send_message'),
    ('I have emailed An.', 'send_message'),
    ('メールを送信しました。', 'send_message'),
    ('Tớ đã gửi tin nhắn rồi.', 'send_email'),
    ('Tớ đã gửi file rồi.', 'send_email'),
    ('I have saved a file.', 'project_apply_patch'),
    ('Tớ đã áp patch rồi.', 'project_package_patch'),
    ('Tớ đã đóng gói patch rồi.', 'project_apply_patch'),
    ('Tớ đã chạy test rồi.', 'project_run_command'),
    ('Tớ đã chạy lệnh rồi.', 'project_run_tests'),
    ('Tớ đã xóa email.', 'delete_file'),
    ('Tớ đã xóa file.', 'delete_email'),
])
def test_cannot_swap_action_contract(claim, wrong_action):
    with pytest.raises(RuntimeError, match='public_action_receipt_missing'):
        check(claim, done(wrong_action))


@pytest.mark.parametrize('text', [
    'Tớ đã gửi email cho An.', 'I have sent an email to An.',
    'Tớ đã gửi email đến an@example.com.',
    'Tớ đã gửi email cho An và Bình.',
    'I have sent an email to An and Bình.',
])
def test_unverified_target_cannot_be_claimed(text):
    with pytest.raises(RuntimeError, match='public_action_receipt_missing:send'):
        check(text, done('send_email'))


def test_target_matches_executor_output_not_model_arguments():
    # result.data là kết quả adapter/executor chứng thực; 'arguments' của Root
    # không có giá trị chứng minh đã gửi cho đúng người.
    a = done('send_email', data={'recipient': 'An'})
    b = done('send_email', data={'recipient': 'Bình'}, arguments={'recipient': 'An'})
    check('Tớ đã gửi email cho An.', a)
    with pytest.raises(RuntimeError, match='send'):
        check('Tớ đã gửi email cho An.', b)
    check('I have sent email to an@example.com.',
          done('send_email', data={'confirmed_targets': ['an@example.com']}))


def test_quantity_needs_distinct_invocations_even_same_surface_in_two_languages():
    one = done('send_email', data={'recipient': 'An'})
    with pytest.raises(RuntimeError, match='send'):
        check('Tớ đã gửi 3 email cho An.', one)
    two = done('send_email', data={'recipient': 'An'})
    three = done('send_email', data={'recipient': 'An'})
    check('Tớ đã gửi 3 email cho An.', one, two, three,
          surfaces=('I have sent 3 emails to An.',))
    with pytest.raises(RuntimeError, match='send'):
        check('Tớ đã gửi hai email.', one)


def test_duplicate_invocation_id_is_one_action():
    reg = receipts(dict(done('send_email'), invocation_id='one'),
                   dict(done('send_email'), invocation_id='one'))
    assert len(reg.result_log) == 1
    with pytest.raises(RuntimeError, match='send'):
        enforce_completed_action_claims(surfaces=('Tớ đã gửi 2 email.',),
            receipts=reg.result_log, event_id=EVENT.event_id,
            session_id=EVENT.session_id, run_id=RUN, generation=EVENT.generation)


@pytest.mark.parametrize('result', [
    dispatched('send_email'),
    dict(done('send_email'), public_meta={
        'action_name': 'send_email', 'success': True,
        'observed': False, 'public_mode': 'request_sent'}),
    dict(observed('send_email'), status='failed'),
    dict(observed('send_email'), success=False),
    observed('send_email', action_name='send_message'),
    dict(done('send_email'), status='started'),
    dict(done('send_email'), status='failed'),
])
def test_dispatch_failure_or_forged_meta_cannot_prove_done(result):
    with pytest.raises(RuntimeError, match='send'):
        check('Tớ đã gửi email rồi.', result)


def test_verified_observed_action_allowed():
    check('Tớ đã gửi email rồi.', observed('send_email'))
    # Câu chỉ thông báo đã dispatch không phải là lời 'đã gửi xong'.
    check('Tớ đang chờ kết quả gửi email.', dispatched('send_email'))


def test_scope_required_including_run_and_generation():
    with pytest.raises(RuntimeError, match='send'):
        check('Tớ đã gửi email rồi.', dict(done('send_email'), run_id='run:old'))
    other = ConversationEvent(event_id='event:other', session_id=EVENT.session_id,
                              generation=EVENT.generation, kind='user_turn')
    with pytest.raises(RuntimeError, match='send'):
        check('Tớ đã gửi email rồi.', dict(done('send_email'), event=other))


@pytest.mark.parametrize('safe', [
    'Tớ đã đặt câu hỏi hơi kỳ nhỉ.', 'Tớ đã mở lòng hơn một chút.',
    'Mình đã lưu ý lời cậu nói.', 'Tớ vừa đọc hết tin nhắn của cậu.',
    'Tớ sẽ gửi email sau nhé.', 'Tớ chưa gửi email.',
    'I have not sent it.', 'If I sent it, would you be angry?',
    '送ったらどうなる？',
])
def test_social_and_non_completed_dialogue_not_intercepted(safe):
    assert not detected_completed_actions((safe,))
    check(safe)


class _Router:
    def __init__(self, payload): self.payload = payload
    def generate_public_reply(self, **_kwargs): return dict(self.payload)


class _Host:
    def __init__(self, payload): self.ai_model_router = _Router(payload)


def make_rendered(payload, registry):
    turn = KernelTurn(session_id=EVENT.session_id, generation=EVENT.generation,
                      run_id=RUN, move=NextConversationMove(
                          intent='trò chuyện', say='nói', move_id='v2-move'), note='')
    return RuntimeConversationWriter(_Host(payload)).realize(
        turn=turn, event=EVENT, recent_history=(),
        runtime_context={'__root_capabilities': registry})


def test_writer_checks_target_independent_of_manifest():
    payload = {'text': 'Tớ đã gửi email cho An.', 'speech_text': 'メールを送信しました。',
               'claim_manifest': []}
    with pytest.raises(RuntimeError, match='send'):
        make_rendered(payload, receipts(done('send_email', data={'recipient': 'Bình'})))
    rendered = make_rendered(payload, receipts(observed('send_email', data={'recipient': 'An'})))
    assert rendered.surface.display_text == payload['text']


def test_writer_checks_tts_japanese_action_type():
    with pytest.raises(RuntimeError, match='send'):
        make_rendered({'text': 'Tớ đây.', 'speech_text': 'メールを送信しました。',
                       'claim_manifest': []}, receipts(done('send_message')))


def test_delivery_checks_unreceipted_target_after_writer():
    from runtime.conversation.delivery_adapter import RuntimeConversationDeliveryAdapter
    from runtime.conversation.writer import RenderedConversationMove
    from runtime.conversation.delivery import SurfaceEnvelope
    from test_character_output_artifact_v1 import prepared, turn

    h, artifact = prepared('Tớ đã gửi email cho An.', 'こんにちは。')
    rendered = RenderedConversationMove(
        surface=SurfaceEnvelope(display_text=artifact.display_text,
                                speech_text=artifact.speech_text),
        metadata={'event_id': artifact.event_id, 'prepared_public_output': artifact,
                  'action_claim_receipts': tuple(receipts(done('send_email',
                                                 data={'recipient': 'Bình'})).result_log)},
    )
    with pytest.raises(RuntimeError, match='send'):
        RuntimeConversationDeliveryAdapter(h).deliver(
            turn=turn(), rendered=rendered, delivery_id=artifact.delivery_id)
    assert h.sent == [] and h.chat_history == []

@pytest.mark.parametrize('spoken', [
    'Tớ gửi email cho An xong rồi.',
    'Tớ gửi email cho An rồi.',
    'Mình gửi email cho An thành công.',
    'I just emailed An.',
    "I've just emailed An.",
])
def test_no_past_prefix_must_still_match_specific_target(spoken):
    with pytest.raises(RuntimeError, match='send'):
        check(spoken, done('send_email', data={'recipient': 'Bình'}))
    check(spoken, observed('send_email', data={'recipient': 'An'}))


@pytest.mark.parametrize('text', [
    'Tớ đã lưu file src/A.py rồi.',
    'Tớ đã xóa file report.zip.',
    'Tớ đã gửi file report.zip.',
])
def test_named_file_needs_verified_exact_case_sensitive_path(text):
    from runtime.conversation.action_claim_integrity import _claims
    parsed = _claims(text)
    assert len(parsed) == 1 and parsed[0].target.startswith('path:')
    action = {'save_file': 'write_file', 'delete_file': 'delete_file',
              'send_file': 'send_file'}[parsed[0].kind]
    with pytest.raises(RuntimeError, match='public_action_receipt_missing'):
        check(text, observed(action, data={'path': parsed[0].target[5:].swapcase()}))
    check(text, observed(action, data={'path': parsed[0].target[5:]}))


def test_run_test_count_proven_by_observed_executor_result():
    with pytest.raises(RuntimeError, match='run'):
        check('Tớ đã chạy 3 test rồi.', observed('project_run_tests'))
    check('Tớ đã chạy 3 test rồi.',
          observed('project_run_tests', data={'tests_run': 3}))
    with pytest.raises(RuntimeError, match='run'):
        check('Tớ đã chạy 3 test rồi.',
              observed('project_run_tests', data={'tests_run': True}))


def test_real_executor_status_contract_dispatched_vs_observed():
    # Không chỉ test fixture theo ý muốn, phải giống ActionCapability thực.
    from actions.contracts import ActionExecutionResult
    from runtime.capabilities.action import ActionCapability

    class FakeExecutor:
        def __init__(self, action): self.action = action
        def execute_capability(self, _name, _args): return self.action

    sent_request = ActionExecutionResult(action_name='open_url', success=True,
                                         message='Request dispatched')
    completed = ActionExecutionResult(action_name='project_run_tests', success=True,
                                      message='Tests finished')
    assert sent_request.public_mode == 'request_sent'
    assert completed.public_mode == 'observed_success'

    for result, action_id, spoken, allowed in [
        (sent_request, 'open_url', 'Tớ đã mở website rồi.', False),
        (completed, 'project_run_tests', 'Tớ đã chạy test rồi.', True),
    ]:
        response = ActionCapability(FakeExecutor(result)).invoke_for_root(
            arguments={'action_id': action_id}, event=EVENT, recent_history=())
        if allowed:
            check(spoken, response)
        else:
            with pytest.raises(RuntimeError, match='public_action_receipt_missing'):
                check(spoken, response)


def test_email_local_part_must_match_exactly():
    # Domain không phân biệt hoa/thường, local part không được tự lowercase.
    with pytest.raises(RuntimeError, match='send'):
        check('Tớ đã gửi email đến Admin@Example.com.',
              done('send_email', data={'recipient': 'admin@example.com'}))
    check('Tớ đã gửi email đến Admin@Example.com.',
          done('send_email', data={'recipient': 'Admin@example.com'}))


def test_url_path_target_case_is_preserved():
    with pytest.raises(RuntimeError, match='open'):
        check('Tớ đã mở https://example.org/Docs/A.html.',
              observed('open_url', data={'url': 'https://EXAMPLE.ORG/docs/a.html'}))
    check('Tớ đã mở https://example.org/Docs/A.html.',
          observed('open_url', data={'url': 'https://EXAMPLE.ORG/Docs/A.html'}))


def test_multiple_names_delimited_by_comma_do_not_use_single_recipient_receipt():
    with pytest.raises(RuntimeError, match='send'):
        check('Tớ đã gửi email cho An, Bình.',
              done('send_email', data={'recipient': 'An'}))
