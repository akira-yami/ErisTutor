# -*- coding: utf-8 -*-
"""P3: committed artifact -> real validated browser ACK -> Root health context."""
from __future__ import annotations
from datetime import datetime, timezone, timedelta
import json
import os
from runtime.workspace.presentation_p2 import publish_committed_presentation
from runtime.workspace.presentation_health import recent_presentation_health
from runtime.workspace.presentation_feedback import recent_render_feedback
from runtime.conversation.agent import _bounded_context


def make_bundle(tmp_path):
    f=publish_committed_presentation(
        turn_id='delivery:active', delivery_id='delivery:active',
        blocks=[{'kind':'math','title':'Tích phân','content':{'latex':r'\int_0^1 x\,dx=\frac12'}}],
        workspace=tmp_path,
    )
    assert f
    return f, json.loads(f.read_text('utf8'))


def write_ack(tmp_path,b, *, status='rendered',code='OK_RENDERED',retryable=False, intent=None, id=None,rev=None):
    a=b['artifacts'][0]
    fb={'type':'renderer.feedback.v1','renderer_id':'richchat-main',
        'intent_id':intent or b['intent_id'], 'artifact_id':id or a['artifact_id'],
        'revision':rev or a['revision'],'status':status,'code':code,
        'retryable':retryable,'received_at':datetime.now(timezone.utc).isoformat()}
    root=tmp_path/'presentation_v1'/'feedback';root.mkdir(parents=True,exist_ok=True)
    (root/f"{a['artifact_id']}.r{a['revision']}.json").write_text(json.dumps(fb),'utf8')


def test_pending_is_not_inferred_failure(tmp_path):
    _,b=make_bundle(tmp_path)
    health=recent_presentation_health(tmp_path)
    assert len(health)==1
    assert health[0]['status']=='awaiting_viewer'
    assert health[0]['next_action']=='wait_for_rich_chat'


def test_verified_failure_is_actionable_and_success_clears(tmp_path):
    _,b=make_bundle(tmp_path)
    write_ack(tmp_path,b,status='failed',code='E_RENDER',retryable=True)
    s=recent_presentation_health(tmp_path)[0]
    assert s['status']=='failed' and s['next_action']=='retry_ui_or_revise_if_current_request'
    write_ack(tmp_path,b)
    s=recent_presentation_health(tmp_path)[0]
    assert s['status']=='rendered' and s['next_action']=='none'


def test_no_ghost_on_uncommitted_or_tampered_ack(tmp_path):
    _,b=make_bundle(tmp_path)
    write_ack(tmp_path,b,intent='pi_'+'b'*24)
    assert recent_render_feedback(tmp_path)==[]
    assert recent_presentation_health(tmp_path)[0]['status']=='awaiting_viewer'


def test_expired_and_provisional_never_escalate(tmp_path):
    f,b=make_bundle(tmp_path)
    old=(datetime.now(timezone.utc)-timedelta(hours=2)).timestamp()
    os.utime(f,(old,old))
    assert recent_presentation_health(tmp_path)==[]


def test_agent_context_receives_verified_health_without_extra_loop(tmp_path,monkeypatch):
    _,b=make_bundle(tmp_path)
    write_ack(tmp_path,b,status='failed',code='E_RENDER',retryable=True)
    monkeypatch.setenv('ERIS_ARTIFACT_WORKSPACE',str(tmp_path))
    ctx=_bounded_context({})
    assert any(i['status']=='failed' for i in ctx['presentation_health'])
    assert ctx['renderer_feedback'][0]['code']=='E_RENDER'


def test_empty_workspace_does_not_create_false_failure(tmp_path):
    assert recent_presentation_health(tmp_path)==[]
