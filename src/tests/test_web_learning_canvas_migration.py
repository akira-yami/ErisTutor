"""Regression guards for the Godot -> browser Learning Canvas migration."""
from pathlib import Path
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[2]
BODY = ROOT / 'src/body'
WEB = BODY / 'richchat'


def test_canvas_no_longer_creates_godot_surface():
    for name in ('UdpListener.LearningCanvas.cs', 'UdpListener.LearningCanvasExercises.cs',
                 'UdpListener.LearningCanvasPlayback.cs'):
        assert not (BODY / name).exists(), name
    assert not (ROOT / 'tools/ui-preview/LearningCanvasPreview.cs').exists()
    assert not (ROOT / 'tools/ui-preview/learning_canvas_preview.tscn').exists()
    source = (BODY / 'UdpListener.cs').read_text(encoding='utf-8')
    assert '_learningCanvasPanel' not in source
    assert 'InitializeWebLearningCanvas()' in source
    assert 'TickWebLearningCanvas()' in source
    assert 'TryHandleWebLearningCanvasPacket(raw)' in source


def test_local_portal_exposes_web_canvas_and_bounded_same_origin_commands():
    bridge = (BODY / 'UdpListener.WebLearningCanvas.cs').read_text(encoding='utf-8')
    portal = (BODY / 'UdpListener.RichChatPortal.cs').read_text(encoding='utf-8')
    assert 'OpenRichChatPortal("canvas.html")' in bridge
    assert 'ConcurrentQueue<string>' in bridge
    assert '"group_id"] = "java-di"' in bridge
    assert '"base_revision"' in bridge and '"request_id"' in bridge
    assert '"code" ? 12_000' in bridge
    assert 'api/canvas/state' in portal
    assert 'api/canvas/request' in portal
    assert 'sameOrigin' in portal
    for asset in ('canvas.html', 'canvas.js', 'canvas.css'):
        assert f'"{asset}"' in portal
        assert (WEB / asset).exists()
    assert 'Content-Security-Policy:' in portal


def test_canvas_navigation_and_backend_contract_are_not_reinvented_in_web_ui():
    html = (WEB / 'canvas.html').read_text(encoding='utf-8')
    js = (WEB / 'canvas.js').read_text(encoding='utf-8')
    chat = (WEB / 'index.html').read_text(encoding='utf-8')
    assert 'href="canvas.html"' in chat
    assert 'api/canvas/state' in js and 'api/canvas/request' in js
    for action in ('open', 'navigate', 'exercise', 'submit', 'hint', 'reveal', 'exercise_status', 'ack_eris_focus'):
        assert f"'{action}'" in js
    assert 'textContent' in js
    assert '.innerHTML' not in js  # No untrusted lesson/code rendered as HTML.
    assert 'id="map"' in html and 'id="detail"' in html
    assert '@media(max-width:710px)' in (WEB / 'canvas.css').read_text(encoding='utf-8')


def test_web_canvas_js_parses():
    node = shutil.which('node')
    if not node:
        pytest.skip('node is unavailable')
    subprocess.run([node, '--check', str(WEB / 'canvas.js')], check=True, timeout=10)
