# -*- coding: utf-8 -*-
"""P3 Chromium E2E with a tiny HTTP *fixture*, not a Godot.exe test.

Exercises the real bundled MathJax/JS and HTTP ACK path. Requires Playwright +
Chromium; otherwise skipped. No production server or model credentials needed.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import time

import pytest
from playwright.sync_api import Error as PlaywrightError

ROOT = Path(__file__).resolve().parents[1] / 'body' / 'richchat'


def browser():
    chromium = shutil.which('chromium') or shutil.which('chromium-browser')
    if not chromium and os.name == 'nt':
        candidates = [Path(os.environ.get(root, '')) / relative
                      for root, relative in (
                          ('PROGRAMFILES(X86)', 'Microsoft/Edge/Application/msedge.exe'),
                          ('PROGRAMFILES', 'Microsoft/Edge/Application/msedge.exe'),
                          ('PROGRAMFILES', 'Google/Chrome/Application/chrome.exe'),
                      )]
        chromium = next((str(path) for path in candidates if path.is_file()), None)
    if not chromium:
        pytest.skip('No Chromium executable for browser integration test')
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        pytest.skip('Playwright not installed')
    return chromium, sync_playwright


def make_state(kind='math'):
    id='art_' + 'a'*24
    content={'latex':r'\int_0^1 x\,dx=\frac12'} if kind=='math' else {
        'format':'blocks','source':'',
        'blocks':[{'type':'image','path':'missing.png','sha256':'0'*64}]}
    art={'schema':'eris.artifact.v1','artifact_id':id,'turn_id':'delivery:test',
         'kind':kind,'revision':1,'source':'root_presentation',
         'content':content, 'presentation':{'target':['rich_chat'], 'order':0,'title':'P3'}}
    return {'status':'Sẵn sàng',
            'messages':[{'id':1,'role':'assistant','date':'2026-10-29','time':'21:56',
                         'turnId':'delivery:test','text':'Kết quả tích phân'}],
            'bundles':[{'intent_id':'pi_'+'b'*24,'turn_id':'delivery:test',
                        'provisional':False,'artifacts':[art]}]}



def install_routes(page, state, receipts):
    # Playwright fulfills every request in-process; does not call localhost or
    # the network. This is a browser-layer E2E, not a production HTTP server.
    types={'.js':'application/javascript', '.html':'text/html', '.css':'text/css'}
    def handle(route):
        from urllib.parse import urlsplit, unquote
        parsed=urlsplit(route.request.url)
        path=unquote(parsed.path).lstrip('/')
        if path=='api/state':
            route.fulfill(status=200, content_type='application/json',body=json.dumps(state));return
        if path=='api/feedback':
            receipts.append(json.loads(route.request.post_data))
            route.fulfill(status=200,body='{}',content_type='application/json');return
        if path.startswith('api/image/'):
            route.fulfill(status=404,body='missing image');return
        file=(ROOT/path).resolve()
        if not file.is_relative_to(ROOT) or not file.is_file():
            route.fulfill(status=404,body='not found');return
        route.fulfill(status=200, body=file.read_bytes(), content_type=types.get(file.suffix,'application/octet-stream'))
    page.route('**/*',handle)

def test_browser_reports_success_only_after_mathjax_svg():
    exe, run = browser()
    with run() as p:
        b = p.chromium.launch(executable_path=exe, args=['--no-sandbox'], headless=True)
        page=b.new_page()
        receipts=[]
        install_routes(page, make_state(), receipts)
        try:
            page.goto('http://eris-test.invalid/index.html')
        except PlaywrightError as exc:
            b.close()
            if 'ERR_BLOCKED_BY_ADMINISTRATOR' in str(exc):
                pytest.skip('Chromium navigation is blocked in this container; run browser E2E on Windows')
            raise
        page.wait_for_function("document.querySelector('.artifact-card mjx-container svg') !== null", timeout=20000)
        page.wait_for_function("window.__unused || document.querySelector('.artifact-card mjx-container') !== null", timeout=3000)
        deadline=time.monotonic()+10
        while not receipts and time.monotonic()<deadline:
            page.wait_for_timeout(100)
        assert len(receipts)==1, receipts
        assert receipts[0]['status']=='rendered' and receipts[0]['code']=='OK_RENDERED'
        assert page.locator('.artifact-card').count()==1
        b.close()


def test_browser_missing_image_is_a_failure_not_success():
    exe, run = browser()
    with run() as p:
        b = p.chromium.launch(executable_path=exe, args=['--no-sandbox'], headless=True)
        page=b.new_page()
        receipts=[]
        install_routes(page, make_state('document'), receipts)
        try:
            page.goto('http://eris-test.invalid/index.html')
        except PlaywrightError as exc:
            b.close()
            if 'ERR_BLOCKED_BY_ADMINISTRATOR' in str(exc):
                pytest.skip('Chromium navigation is blocked in this container; run browser E2E on Windows')
            raise
        deadline=time.monotonic()+8
        while not receipts and time.monotonic()<deadline:
            page.wait_for_timeout(100)
        assert receipts, 'Broken image should not generate a success ACK'
        assert all(r['status']!='rendered' for r in receipts)
        assert receipts[0]['code']=='E_RENDER'
        b.close()


def test_browser_speech_highlights_exact_live_turn():
    exe, run = browser()
    with run() as p:
        b = p.chromium.launch(executable_path=exe, args=['--no-sandbox'], headless=True)
        page = b.new_page()
        data = make_state()
        data['speech'] = {'phase': 'speaking', 'turnId': 'delivery:test'}
        install_routes(page, data, [])
        try:
            page.goto('http://eris-test.invalid/index.html')
        except PlaywrightError as exc:
            b.close()
            if 'ERR_BLOCKED_BY_ADMINISTRATOR' in str(exc):
                pytest.skip('Chromium navigation blocked; run on Windows')
            raise
        page.wait_for_selector('.entry.assistant.speaking-now .speech-indicator', timeout=10000)
        assert page.locator('.entry.assistant.speaking-now').count()==1
        data['speech'] = {'phase': 'speaking', 'turnId': 'delivery:stale'}
        page.wait_for_function("document.querySelector('.entry.assistant.speaking-now') === null", timeout=5000)
        data['speech'] = {'phase': 'thinking', 'turnId': ''}
        page.wait_for_selector('.live-activity', timeout=5000)
        b.close()


def test_browser_loads_history_without_optional_workspace_button():
    exe, run = browser()
    with run() as p:
        b = p.chromium.launch(executable_path=exe, headless=True)
        page = b.new_page()
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        state = make_state()
        state['bundles'] = []
        install_routes(page, state, [])
        # A cached/older shell can omit optional workspace controls. History
        # initialization and ongoing updates must remain usable in that shell.
        import re
        shell = (ROOT / 'index.html').read_text(encoding='utf-8')
        shell = re.sub(r'<button\b[^>]*id="workspaceBtn"[^>]*>.*?</button>', '', shell)
        page.route('**/index.html', lambda route: route.fulfill(
            status=200, content_type='text/html', body=shell))
        page.goto('http://eris-test.invalid/index.html')
        page.wait_for_selector('.entry.assistant', timeout=10000)
        assert page.locator('#dates button').count() > 0
        assert 'Đang tải hội thoại' not in page.locator('#messages').inner_text()
        state['messages'].append({
            'id': 2, 'role': 'user', 'date': '2026-10-30', 'time': '22:00',
            'text': 'Tin mới sau khi tải lịch sử', 'turnId': '',
        })
        page.wait_for_selector('.entry.user', timeout=5000)
        assert not errors, errors
        b.close()
