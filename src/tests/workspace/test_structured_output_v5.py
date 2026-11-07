# -*- coding: utf-8 -*-
"""V5: kiểm thử tài liệu công khai, không nhầm test với Godot live."""
import hashlib
import json
from pathlib import Path
import socket
import threading

import pytest

from runtime.workspace.structured_output import (
    parse_markdown, prepare_document, text_digest,
    send_document_acknowledged, dispatch_committed_document,
)
from runtime.workspace.math_artifacts import send_artifacts_udp


def _local_ack_server(expected_mode, response_mode, identity_name):
    server = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    server.bind(('127.0.0.1', 0)); server.settimeout(3)
    port=server.getsockname()[1]
    packets=[]
    def serve():
        try:
            raw, addr = server.recvfrom(60_000)
            doc=json.loads(raw)
            packets.append(doc)
            if doc['parse_mode'] == expected_mode:
                reply={'parse_mode':response_mode,identity_name:doc[identity_name],'status':'rendered'}
                server.sendto(json.dumps(reply).encode(),addr)
        finally:
            server.close()
    thread=threading.Thread(target=serve,daemon=True);thread.start()
    return port, packets, thread


def test_markdown_blocks_preserve_reasoning_and_code():
    raw=("# Lời giải\n\nĐầu tiên dùng $x^2-1$, rồi xét tiếp.\n\n"
         "```python\nprint('hello')\n```\n\n"
         "| Bước | Kết quả |\n| --- | --- |\n| 1 | 2 |\n\n"
         "- Một\n- Hai\n\n$$\\frac{a}{b}$$")
    blocks = parse_markdown(raw)
    assert [b['type'] for b in blocks] == [
        'heading', 'paragraph','formula','paragraph','code','table','list','formula'
    ]
    assert blocks[4]['text'] == "print('hello')"
    assert blocks[5]['rows'] == [['1','2']]
    assert blocks[7]['latex'] == r'\frac{a}{b}'


def test_long_plain_answer_is_readable_without_forcing_math_tool():
    raw=' '.join(('Nếu có trường hợp thứ nhất, ta chứng minh điều này. '
                  'Sau đó kiểm tra trường hợp thứ hai và tiếp tục. ') for _ in range(12))
    blocks=parse_markdown(raw)
    assert len(blocks) >= 3
    assert all(b['type']=='paragraph' for b in blocks)
    assert 'Sau đó' in ''.join(b['text'] for b in blocks)


def test_math_image_has_correct_hash_and_is_inside_workspace(tmp_path):
    document = prepare_document(
        text=r'## Kết quả'+'\n\n'+r'$$\frac{a^2+b^2}{2}$$',
        session_id='s', generation=4, run_id='r', delivery_id='d1',
        workspace=tmp_path,
    )
    assert document['parse_mode'] == 'workspace_document'
    formula=next(block for block in document['blocks'] if block['type']=='formula')
    assert formula['latex']==r'\frac{a^2+b^2}{2}'
    image=tmp_path/formula['path']
    assert image.is_file()
    assert image.read_bytes().startswith(b'\x89PNG')
    assert hashlib.sha256(image.read_bytes()).hexdigest()==formula['sha256']


def test_no_shell_execution_from_latex(tmp_path):
    content=r'$$\input{/etc/passwd}$$'
    document=prepare_document(text=content,session_id='s',generation=1,run_id='r',
                              delivery_id='../bad',workspace=tmp_path)
    formula=document['blocks'][0]
    assert formula['latex']==r'\input{/etc/passwd}'
    # Không chạy LaTeX CLI; nếu MathText từ chối thì chỉ giữ text có thể copy.
    assert not any(p.name=='bad' for p in tmp_path.rglob('*'))


def test_document_receipt_from_fake_godot():
    port, packets, th = _local_ack_server('workspace_document','workspace_document_ack','document_id')
    doc=prepare_document(text='Hello $x^2$',session_id='s',generation=1,run_id='r',
                         delivery_id='d',render_math=False)
    assert send_document_acknowledged(doc,port=port)
    th.join(2)
    assert packets[0]['blocks'][1]['type']=='formula'


def test_artifact_receipt_from_fake_godot():
    port,packets,th=_local_ack_server('workspace_artifact','workspace_artifact_ack','job_id')
    assert send_artifacts_udp({'parse_mode':'workspace_artifact','workspace_version':1,
                               'job_id':'job1','artifacts':[]},port=port)
    th.join(2)
    assert packets[0]['job_id']=='job1'


def test_no_ack_is_distinct_from_success():
    port,packets,th=_local_ack_server('workspace_artifact','wrong_ack','job_id')
    assert not send_artifacts_udp({'parse_mode':'workspace_artifact',
                                   'job_id':'job2','artifacts':[]},port=port,timeout=.05,retries=1)
    th.join(2)


def test_async_dispatch_does_not_change_transcript(monkeypatch):
    import runtime.workspace.structured_output as module
    done=threading.Event()
    seen=[]
    def sender(doc, **kwargs):
        seen.append(doc);done.set();return True
    monkeypatch.setattr(module,'send_document_acknowledged',sender)
    class FakeTransport:
        _send_target=('127.0.0.1',4242)
    content='Tôi chọn tự giải trước. $$x^2=4$$'
    dispatch_committed_document(text=content,session_id='s',generation=5,
                                run_id='r',delivery_id='d',transport=FakeTransport())
    assert done.wait(6)
    assert seen[0]['generation']==5
    assert seen[0]['text_hash']==text_digest(content)
    assert content == 'Tôi chọn tự giải trước. $$x^2=4$$'


def test_reject_unbounded_payload():
    with pytest.raises(ValueError): parse_markdown('x'*25_000)
