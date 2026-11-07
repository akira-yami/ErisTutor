# -*- coding: utf-8 -*-
"""Regression: RAW không bị cắt, không lưu trùng khối, và khôi phục byte-for-byte."""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from llm import raw_trace, raw_trace_store
from requests.models import complexjson as requests_json
from tools.view_deepseek_raw_trace import main


def _events(root: Path):
    return [json.loads(line) for f in root.glob('events_*.jsonl')
            for line in f.read_text(encoding='utf-8').splitlines()]


def test_large_bootstrap_reused_with_inserted_prefix(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv('ERIS_DEEPSEEK_RAW_TRACE', '1')
    monkeypatch.setenv('ERIS_DEEPSEEK_RAW_TRACE_DIR', str(tmp_path))
    # Chuỗi giả lập dài và biến thiên, không phải một đoạn chỉ gồm một từ lặp lại.
    full_self = ''.join(f'belief_{i:05d}: ' + 'Xin chào世界'*20 + '\n' for i in range(12000))
    for i in range(3):
        body = {'model': 'deepseek-v4-flash', 'messages': [
            {'role': 'system', 'content': full_self},
            {'role': 'user', 'content': f'new turn {i}'}]}
        with raw_trace.worker_role_context('executive'):
            attempt = raw_trace.begin_http_attempt(url='http://localhost:6011/v1/chat/completions',
                model='deepseek-v4-flash', body=body, headers={}, lane='chat_main', cognitive_lane='root_conversation')
            attempt.response(SimpleNamespace(status_code=200, text='{"choices":[{"message":{"content":"ok"}}]}'))
    events = _events(tmp_path)
    assert len(events) == 6
    reqs = [e for e in events if e['event'] == 'request']
    assert 'raw_request_json' not in reqs[0]
    assert len(reqs[0]['raw_request_ref']['chunks']) > 3
    total_raw = sum(e['raw_request_ref']['byte_length'] for e in reqs)
    stored = sum(p.stat().st_size for p in (tmp_path/'objects').rglob('*.gz'))
    assert stored < total_raw / 3  # nén + dedup
    assert set(reqs[0]['raw_request_ref']['chunks']) & set(reqs[1]['raw_request_ref']['chunks'])
    assert raw_trace_store.restore_payload(tmp_path, reqs[2]['raw_request_ref']) == requests_json.dumps(body, allow_nan=False).encode('utf-8')
    assert main([str(tmp_path), '--lane', 'root_conversation']) == 0
    assert full_self not in capsys.readouterr().out
    destination = tmp_path/'exported.json'
    assert main([str(tmp_path), '--id', reqs[2]['request_id'], '--export-request', str(destination)]) == 0
    assert destination.read_bytes() == requests_json.dumps(body, allow_nan=False).encode('utf-8')


def test_single_character_edit_in_front_deduplicates_suffix(tmp_path):
    root = tmp_path
    content = b''.join((str(i)*9).encode() + bytes([i%256])*100 for i in range(20000))
    first = raw_trace_store.store_payload(root, content, kind='bytes')
    second = raw_trace_store.store_payload(root, b'head-' + content, kind='bytes')
    assert raw_trace_store.restore_payload(root, first) == content
    assert raw_trace_store.restore_payload(root, second) == b'head-' + content
    assert len(set(first['chunks']) & set(second['chunks'])) > 0


def test_missing_or_corrupt_object_is_detected(tmp_path):
    manifest = raw_trace_store.store_payload(tmp_path, b'raw response', kind='utf8_text')
    location = next((tmp_path/'objects').rglob('*.gz'))
    location.write_bytes(b'corrupted')
    with pytest.raises((OSError, ValueError, EOFError)):
        raw_trace_store.restore_payload(tmp_path, manifest)
    manifest['chunks'] = ['../../secret']
    with pytest.raises(ValueError):
        raw_trace_store.restore_payload(tmp_path, manifest)


def test_no_sensitive_headers_or_inline_prompt(monkeypatch, tmp_path):
    monkeypatch.setenv('ERIS_DEEPSEEK_RAW_TRACE', '1')
    monkeypatch.setenv('ERIS_DEEPSEEK_RAW_TRACE_DIR', str(tmp_path))
    attempt = raw_trace.begin_http_attempt(url='https://localhost/api?access_token=secret',
       model='deepseek-v4-flash', body={'messages':[{'role':'user','content':'long-private-text'}]},
       headers={'Authorization':'Bearer DO-NOT-STORE', 'X-Session-ID':'SECRET-SESSION'}, lane='chat')
    attempt.error(TimeoutError('DO-NOT-STORE'))
    index = next(tmp_path.glob('events_*.jsonl')).read_text(encoding='utf-8')
    assert 'long-private-text' not in index
    assert 'DO-NOT-STORE' not in index
    assert 'SECRET-SESSION' not in index
    assert 'access_token' not in index
