# -*- coding: utf-8 -*-
"""P2 contract tests: Root chooses presentation; executable receipts are trusted, model IDs are not."""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from runtime.conversation.agent import RuntimeModelRootAgent
from runtime.conversation.context import RootCapabilityRegistry
from runtime.conversation.contracts import ConversationEvent, NextConversationMove, NoReply
from runtime.capabilities.action import ActionCapability
from runtime.workspace.artifact_protocol import adapter_math_workspace, validate_bundle, write_bundle_sidecar
from runtime.workspace.presentation_p2 import (
    normalize_presentation_blocks, publish_committed_presentation, verified_math_bundles,
)


class FakeRouter:
    executive_client = object()
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []
    def invoke_structured_worker(self, _client, **kw):
        self.calls.append(kw)
        return self.responses.pop(0)


def _decide(router, context=None):
    return RuntimeModelRootAgent(router).decide(
        event=ConversationEvent(event_id='e-p2', session_id='s', kind='user_turn',
                                text='Giải bài toán, vẽ bảng và công thức'),
        recent_history=(), note='', runtime_context=context or {},
    )


def _math_blocks():
    return [
        {'kind':'math','title':'Tích phân', 'content':{'latex':r'\\int_0^1 x\\,dx=\\frac12'}},
        {'kind':'table','title':'Bảng', 'content':{'columns':['x','y'],'rows':[[0,0],[1,1]]}},
    ]


def test_root_selects_typed_blocks_without_extra_loop():
    router = FakeRouter({'kind':'move','intent':'trình bày trực quan', 'say':'Xem công thức và bảng nhé',
                         'delivery':'nhẹ','after':'wait_user','presentation_blocks':_math_blocks()})
    move = _decide(router)
    assert isinstance(move, NextConversationMove)
    assert len(move.presentation_blocks) == 2
    assert move.tool_artifact_bundles == ()
    assert len(router.calls) == 1
    assert 'presentation_blocks' in router.calls[0]['schema']['oneOf'][0]['properties']
    assert 'presentation_blocks' not in {'say':move.say}


def test_invalid_llm_blocks_fail_soft_and_cannot_escape_schema():
    raw = _math_blocks() + [
        {'kind':'speech','content':{'text':'fake','voice_text':'fake','sync_group':'x','interruptible':True}},
        {'kind':'document','content':{'format':'html','source':'<script>alert(1)</script>'}},
        {'kind':'table','content':{'columns':['x','y'],'rows':[[1]]}},
        {'kind':'code','content':{'language':'python','source':'print(1)'}},
    ]
    assert [x['kind'] for x in normalize_presentation_blocks(raw)] == ['math','table','code']


def test_direct_blocks_bind_to_actual_committed_delivery_and_replay_idempotent(tmp_path):
    assert not list(tmp_path.glob('**/pi_*.json'))
    file = publish_committed_presentation(turn_id='delivery:real-1',delivery_id='delivery:real-1',
        blocks=_math_blocks(), workspace=tmp_path)
    assert file and file.exists()
    bundle = validate_bundle(json.loads(file.read_text('utf-8')))
    assert bundle['turn_id']=='delivery:real-1'
    assert len(bundle['artifacts'])==2
    assert {x['kind'] for x in bundle['artifacts']}=={'math','table'}
    assert all(x['metadata']['turn_binding']=='bound' for x in bundle['artifacts'])
    again = publish_committed_presentation(turn_id='delivery:real-1',delivery_id='delivery:real-1',
        blocks=_math_blocks(),workspace=tmp_path)
    assert again==file and again.read_bytes()==file.read_bytes()


def test_successful_math_receipt_attaches_original_tool_content(tmp_path):
    provisional = adapter_math_workspace({
        'parse_mode':'workspace_artifact', 'job_id':'p2-job-1', 'title':'Tool result',
        'artifacts':[{'type':'formula','latex':'x^2', 'text':'x squared'},
                     {'type':'code','language':'python','text':'print(1)'}],
    })
    write_bundle_sidecar(provisional,tmp_path)
    receipt = {'kind':'capability_result','capability':'action',
               'result':{'action_id':'math_workspace','success':True,
                         'data':{'artifact_bundle_id':provisional['intent_id']}}}
    ids = verified_math_bundles([receipt])
    assert ids==(provisional['intent_id'],)
    file=publish_committed_presentation(turn_id='delivery:real-tool', delivery_id='delivery:real-tool',
                                        tool_bundles=ids, workspace=tmp_path)
    assert file and file.exists()
    data=validate_bundle(json.loads(file.read_text('utf8')))
    assert [a['kind'] for a in data['artifacts']]==['math','code']
    assert all(a['turn_id']=='delivery:real-tool' for a in data['artifacts'])
    assert all(a['metadata']['source_bundle_id']==ids[0] for a in data['artifacts'])
    assert validate_bundle(json.loads((tmp_path/'presentation_v1'/f'{ids[0]}.json').read_text()))['turn_id']=='job:p2-job-1'


def test_failed_tool_and_model_supplied_bundle_id_are_ignored(tmp_path):
    bogus='pi_' + 'a'*24
    router=FakeRouter({'kind':'use_capability', 'capability':'action',
                       'arguments':{'action_id':'math_workspace','arguments':{'kind':'integrate','expression':'x'}}},
                      {'kind':'move','intent':'text','say':'Không chạy được tool', 'delivery':'tự nhiên',
                       'after':'wait_user','tool_artifact_bundles':[bogus]})
    class Failed:
        def execute_capability(self, *_):
            return SimpleNamespace(status='failed',success=False,message='error', data={'artifact_bundle_id':bogus},
                                   evidence=(),error_kind='',observer_reason='',to_public_meta=lambda:{})
    move=_decide(router, {'__root_capabilities':RootCapabilityRegistry({'action':ActionCapability(Failed())}),
                          '__conversation_run_id':'run-p2'})
    assert isinstance(move,NextConversationMove)
    assert move.tool_artifact_bundles==()
    assert publish_committed_presentation(turn_id='delivery:noop',delivery_id='delivery:noop',
                                          tool_bundles=move.tool_artifact_bundles,workspace=tmp_path) is None
    assert not list(tmp_path.glob('**/pi_*.json'))


def test_ignored_cross_turn_or_forged_bundle_ids(tmp_path):
    assert publish_committed_presentation(turn_id='../../evil',delivery_id='delivery:x',blocks=_math_blocks(),workspace=tmp_path) is None
    assert publish_committed_presentation(turn_id='delivery:safe',delivery_id='delivery:safe',
                                          tool_bundles=['pi_'+'a'*24],workspace=tmp_path) is None


def test_preempt_has_no_final_move_to_publish():
    active=[True]
    class Preempt(FakeRouter):
        def invoke_structured_worker(self, client, **kw):
            raw=super().invoke_structured_worker(client, **kw)
            active[0]=False
            return raw
    move=_decide(Preempt({'kind':'move','intent':'x','say':'y','delivery':'z',
                          'after':'wait_user','presentation_blocks':_math_blocks()}),
                 {'__root_run_active':lambda:active[0]})
    assert isinstance(move, NoReply)
    assert move.reason=='root_turn_preempted'


def test_p2_chart_matches_browser_contract():
    valid={'kind':'chart','title':'Tiến độ', 'content':{'chart_type':'bar','series':[{'labels':['S1','S2'], 'values':[3,8]}]}}
    invalid={'kind':'chart','content':{'chart_type':'line','series':[{'labels':['S1','S2'],'values':['1','2']}]}}
    forged={'kind':'document','content':{'format':'blocks','source':'','blocks':[{'type':'image','path':'x.png','sha256':'0'*64}]}}
    assert [x['kind'] for x in normalize_presentation_blocks([valid, invalid, forged])] == ['chart']


def test_root_real_tool_result_binds_to_its_own_delivery(tmp_path):
    payload={'parse_mode':'workspace_artifact','job_id':'actual-root-job','title':'Đồ thị công cụ',
             'artifacts':[{'type':'formula','latex':r'\frac{1}{2}', 'text':'1/2'}]}
    bundle=adapter_math_workspace(payload)
    write_bundle_sidecar(bundle,tmp_path)
    class RealExecutor:
        def execute_capability(self, action_id, arguments):
            assert action_id=='math_workspace' and arguments['kind']=='integrate'
            return SimpleNamespace(status='succeeded',success=True,message='Đã tính bằng SymPy',
                                   data={'job_id':payload['job_id'],'artifact_bundle_id':bundle['intent_id']},
                                   evidence=('artifact-job:actual-root-job',),error_kind='',observer_reason='',
                                   to_public_meta=lambda:{})
    router=FakeRouter(
       {'kind':'use_capability','capability':'action','arguments':{
           'action_id':'math_workspace','arguments':{'kind':'integrate','expression':'x'}}},
       {'kind':'move','intent':'giải tích phân','say':'Kết quả nằm ngay trong chat.',
        'delivery':'nhẹ','after':'wait_user'},
    )
    move=_decide(router,{'__root_capabilities':RootCapabilityRegistry({'action':ActionCapability(RealExecutor())}),
                         '__conversation_run_id':'run:math-real'})
    assert isinstance(move,NextConversationMove)
    assert move.tool_artifact_bundles == (bundle['intent_id'],)
    assert len(router.calls)==2
    path=publish_committed_presentation(turn_id='delivery:math-real',delivery_id='delivery:math-real',
                                        tool_bundles=move.tool_artifact_bundles,workspace=tmp_path)
    assert path and json.loads(path.read_text())['artifacts'][0]['content']['latex']==r'\frac{1}{2}'
