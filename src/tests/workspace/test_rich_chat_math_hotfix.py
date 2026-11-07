# -*- coding: utf-8 -*-
"""Đảm bảo dạng toán trong hội thoại thật không mất thành paragraph thô."""
from __future__ import annotations
import hashlib
from pathlib import Path

from runtime.workspace.structured_output import parse_markdown, _render_math
from runtime.workspace.live_document import LiveWorkspaceService


def test_integral_in_real_transcript_renders_as_formula():
    blocks = parse_markdown('∫₀¹ x dx = x²/2 |₀¹ = 1/2.')
    assert [b['type'] for b in blocks] == ['formula']
    assert blocks[0]['latex'].startswith(r'\int_{0}^{1} x \,dx')
    assert r'\frac{x^{2}}{2}' in blocks[0]['latex']
    assert r'\vert_{0}^{1}' in blocks[0]['latex']


def test_integral_inside_sentence_preserves_surrounding_prose():
    blocks = parse_markdown('Muốn tính ∫₀¹ x dx thì có hai việc: tìm nguyên hàm, rồi thay cận.')
    assert [b['type'] for b in blocks] == ['paragraph','formula','paragraph']
    assert blocks[0]['text'] == 'Muốn tính'
    assert blocks[2]['text'].startswith('thì có hai việc')


def test_non_math_prose_remains_prose_and_dollar_math_still_works():
    assert [b['type'] for b in parse_markdown('Hôm nay học cùng Eris nhé')] == ['paragraph']
    assert parse_markdown('Bước 1: $x^2+1$.')[1]['latex'] == 'x^2+1'


def test_distinct_math_images_remain_valid_when_sent_as_live_blocks(tmp_path, monkeypatch):
    # Chạy đường V6 thật (không mock hàm tạo ảnh); bắt packet mà không cần Godot.
    events = []
    service = LiveWorkspaceService(sender=lambda packet, **_: events.append(packet) or True,
                                   workspace=tmp_path)
    doc = service.open(text='Đầu $x^2$ rồi $x^3$.', session_id='s',generation=1,
                       run_id='r',delivery_id='d',turn_id='t')
    assert doc is not None
    monkeypatch.setattr('runtime.workspace.live_document.compose_with_model', lambda *_: None)
    assert service.present(doc)
    formulas = [ev['block'] for ev in events if ev['operation'] == 'insert_block'
                and ev['block']['type'] == 'formula']
    assert len(formulas) == 2
    paths = [f['path'] for f in formulas]
    assert paths[0] != paths[1], 'Các công thức không được ghi đè math_0.png của nhau'
    for b in formulas:
        file = tmp_path / b['path']
        assert file.exists()
        assert hashlib.sha256(file.read_bytes()).hexdigest() == b['sha256']
