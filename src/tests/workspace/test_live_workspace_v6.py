# -*- coding: utf-8 -*-
"""V6: các tình huống mà V5 không làm được: thay khối, sửa sai, mất ACK, hủy."""
import json
import socket
import threading
import time

import pytest

from runtime.workspace.live_document import (
    LiveDocument, LiveWorkspaceService, RevisionConflict,
    send_live_operation, dispatch_live_document,
)


def _make_doc():
    return LiveDocument('s', 4, 'r', 'deliver-12', 'turn-12', 'Vì log cần điều kiện $x>0$.')


def test_revision_conflict_and_patch_lifecycle():
    doc = _make_doc()
    begin = doc.operate('begin')
    assert (begin['previous_revision'], begin['revision']) == (0, 1)
    first = doc.operate('insert_block', block_id='b0', block={'type':'formula','latex':r'x\geq0'}, owner='presenter')
    assert first['revision'] == 2
    edited = doc.operate('replace_block', block_id='b0', block={'type':'formula','latex':r'x>0'}, base_revision=2, owner='writer')
    assert doc.snapshot()['blocks'][0]['latex'] == r'x>0'
    assert edited['owner'] == 'writer'
    with pytest.raises(RevisionConflict):
        doc.operate('replace_block', block_id='b0', block={'type':'paragraph','text':'sai'}, base_revision=2, owner='presenter')
    assert doc.snapshot()['blocks'][0]['latex'] == r'x>0'
    doc.operate('finalize')
    doc.operate('remove_block', block_id='b0', owner='writer', base_revision=4)
    assert doc.snapshot()['blocks'] == []


def test_no_duplicate_or_missing_blocks():
    doc = _make_doc(); doc.operate('begin')
    doc.operate('insert_block', block_id='a', block={'type':'paragraph','text':'A'})
    with pytest.raises(ValueError):
        doc.operate('insert_block', block_id='a', block={'type':'paragraph','text':'B'})
    with pytest.raises(ValueError):
        doc.operate('replace_block', block_id='missing', block={'type':'paragraph','text':'B'})
    with pytest.raises(ValueError):
        doc.operate('insert_block', block_id='../escape', block={'type':'paragraph','text':'B'})
    assert doc.revision == 2


def test_live_presenter_streams_and_finalizes_without_touching_speech(monkeypatch, tmp_path):
    events = []
    current = True
    def send(event, **_): events.append(dict(event)); return True
    source = '## Lời giải\n\nBước đầu đặt $x>0$.\n\n**Kết luận:** x dương.'
    service = LiveWorkspaceService(sender=send, is_current=lambda: current, workspace=tmp_path)
    doc = service.open(text=source, session_id='x', generation=1, run_id='r',
                       delivery_id='did', turn_id='tid')
    assert doc is not None
    monkeypatch.setattr('runtime.workspace.live_document.compose_with_model', lambda router, text: None)
    assert service.present(doc)
    assert events[0]['operation'] == 'begin'
    assert events[-1]['operation'] == 'finalize'
    assert events[1]['operation'] == 'insert_block'
    assert [ev['revision'] for ev in events] == list(range(1, len(events)+1))
    assert source == doc.source_text
    assert len(doc.snapshot()['blocks']) >= 2


def test_second_agent_replaces_while_writer_can_correct(monkeypatch, tmp_path):
    events=[]
    service=LiveWorkspaceService(sender=lambda ev,**_: events.append(ev) or True, workspace=tmp_path)
    doc=service.open(text='x>=0',session_id='s',generation=1,run_id='r',delivery_id='d',turn_id='t')
    monkeypatch.setattr('runtime.workspace.live_document.compose_with_model', lambda *_: [
        {'type':'heading','text':'Điều kiện'}, {'type':'formula','latex':r'x\geq0'}
    ])
    assert service.present(doc, router=object())
    assert 'replace_block' in [e['operation'] for e in events]
    revision=doc.revision
    assert service.revise(doc, operation='replace_block', block_id='b001',
                          block={'type':'formula','latex':r'x>0'}, owner='writer', base_revision=revision)
    assert doc.snapshot()['blocks'][-1]['latex'] == 'x>0'


def test_preemption_stops_after_current_patch(tmp_path):
    calls=[]
    alive=True
    def send(ev,**_):
        nonlocal alive
        calls.append(ev['operation'])
        if ev['operation']=='insert_block': alive=False
        return True
    service=LiveWorkspaceService(sender=send,is_current=lambda:alive,workspace=tmp_path)
    doc=service.open(text='Một.\n\nHai.\n\nBa.',session_id='s',generation=3,run_id='r',delivery_id='d',turn_id='t')
    assert not service.present(doc)
    assert calls == ['begin','insert_block']


def test_retry_on_lost_ack_deduplicates_revision():
    sock=socket.socket(socket.AF_INET,socket.SOCK_DGRAM)
    sock.bind(('127.0.0.1',0));sock.settimeout(2)
    port=sock.getsockname()[1]
    received=[]
    def server():
        try:
            for _ in range(2):
                raw,addr=sock.recvfrom(42000)
                event=json.loads(raw)
                received.append((event['document_id'],event['revision']))
                if len(received)==2:
                    packet={'parse_mode':'workspace_live_ack', 'document_id':event['document_id'],
                            'revision':event['revision'],'status':'duplicate'}
                    sock.sendto(json.dumps(packet).encode(),addr)
        finally: sock.close()
    th=threading.Thread(target=server,daemon=True);th.start()
    ev=_make_doc().operate('begin')
    assert send_live_operation(ev,port=port,timeout=.06,retries=3)
    th.join(2)
    assert received[0] == received[1]


def test_legacy_document_path_is_untouched():
    from runtime.workspace.structured_output import prepare_document
    doc=prepare_document(text='Xin chào',session_id='s',generation=1,
                         run_id='r',delivery_id='x',render_math=False)
    assert doc['parse_mode']=='workspace_document'
    assert doc['workspace_version']==1


def test_async_dispatch_returns_before_panel_ack(monkeypatch):
    started=threading.Event()
    release=threading.Event()
    def slow(event,**_):
        started.set(); release.wait(2); return False
    monkeypatch.setattr('runtime.workspace.live_document.send_live_operation',slow)
    class Transport:
        _send_target=('127.0.0.1',4242)
    t0=time.monotonic()
    service=dispatch_live_document(text='Câu thoại.',session_id='s',generation=1,
                                   run_id='r',delivery_id='d',turn_id='t',transport=Transport())
    assert service is not None
    assert (time.monotonic()-t0)<.3
    assert started.wait(2)
    release.set()


def test_router_presentation_and_writer_review_both_called(monkeypatch,tmp_path):
    # Đây là router giả trong unit test: kiểm tra đường gọi, KHÔNG nhận là LLM live.
    class Router:
        surface_client=object()
        deep_client=None
        llm_service=None
        def __init__(self): self.names=[]
        def invoke_structured_worker(self, client, **kwargs):
            self.names.append(kwargs['name'])
            if kwargs['name']=='eris_v6_presentation_agent':
                return {'blocks': [{'type':'heading','text':'Bước 1'},
                                   {'type':'formula','latex':'x\\geq0'}]}
            if kwargs['name']=='eris_v6_writer_workspace_revision':
                return {'edits': [{'block_id':'b001','type':'formula','latex':'x>0'}]}
            return {}
    events=[]
    router=Router()
    service=LiveWorkspaceService(sender=lambda ev,**_: events.append(ev) or True,workspace=tmp_path)
    doc=service.open(text='x>0',session_id='s',generation=1,run_id='r',delivery_id='d',turn_id='t')
    monkeypatch.setenv('ERIS_V6_PRESENTATION_AGENT','1')
    monkeypatch.setenv('ERIS_V6_WRITER_REVIEW','1')
    assert service.present(doc,router=router)
    assert router.names==['eris_v6_presentation_agent','eris_v6_writer_workspace_revision']
    assert doc.blocks['b001']['latex']=='x>0'
    assert events[-2]['owner']=='writer'
    assert events[-1]['operation']=='finalize'


def test_writer_owns_edited_block_cannot_be_overwritten_by_presenter():
    doc=_make_doc();doc.operate('begin')
    doc.operate('insert_block',block_id='one',block={'type':'paragraph','text':'gốc'})
    doc.operate('replace_block',block_id='one',block={'type':'paragraph','text':'Writer sửa'},owner='writer')
    with pytest.raises(RevisionConflict):
        doc.operate('replace_block',block_id='one',block={'type':'paragraph','text':'Presenter cũ'},owner='presenter')
    assert doc.blocks['one']['text']=='Writer sửa'


def test_capability_contract_and_writer_direct_edit(tmp_path):
    from actions.capability_registry import validate_registry_contract_coverage, validate_capability_arguments
    from runtime.workspace.live_document import writer_edit_live_document,inspect_live_document
    assert validate_registry_contract_coverage()[0]
    assert validate_capability_arguments('live_workspace_edit',{'document_id':'a'*32,'operation':'inspect'})[0]
    service=LiveWorkspaceService(sender=lambda ev,**_:True,workspace=tmp_path)
    doc=service.open(text='Lời giải',session_id='s',generation=1,run_id='r',delivery_id='cap',turn_id='turn-cap')
    assert service.revise(doc,operation='insert_block',block_id='intro',block={'type':'heading','text':'Bài toán'})
    old=doc.revision
    result=writer_edit_live_document(doc.document_id,'replace_block',block_id='intro',
                                     block={'type':'heading','text':'Bài toán đã cập nhật'},base_revision=old)
    assert result['ok'] and result['revision']==old+1
    stale=writer_edit_live_document(doc.document_id,'replace_block',block_id='intro',
                                   block={'type':'heading','text':'Bản lỗi'},base_revision=old)
    assert not stale['ok']
    assert inspect_live_document(doc.document_id)['blocks'][0]['text']=='Bài toán đã cập nhật'
