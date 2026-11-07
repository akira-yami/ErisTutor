# -*- coding: utf-8 -*-
"""Hồi quy cho bài toán thực tế đã gây ra 'bức tường chữ' trong chat.

Không test suy đoán của LLM. Test đường năng lực xác định được, math worker
và artifact có cấu trúc; khả năng Root chọn tool cần đo bằng runtime live.
"""
from pathlib import Path
import hashlib

from runtime.workspace import MathWorkspaceService
from actions.capability_registry import validate_capability_arguments
from runtime.conversation.agent import RuntimeModelRootAgent


def test_binary_3x3_has_verified_steps_and_rendered_images(tmp_path):
    service = MathWorkspaceService(tmp_path / 'math')
    result = service.execute_sync({
        'kind': 'binary_determinant_max', 'size': 3,
        'title': 'Giá trị lớn nhất của định thức ma trận 0–1 cấp 3',
    })
    assert result['parse_mode'] == 'workspace_artifact'
    artifacts = result['artifacts']
    sections = [x['text'] for x in artifacts if x['type'] == 'section']
    assert len(sections) == 4
    texts = '\n'.join(x['text'] for x in artifacts if x['type'] == 'text')
    assert 'lớn nhất' in texts and '512' in texts and 'Hadamard' in texts
    assert 'Định thức: 2' in texts
    images = [x for x in artifacts if x['type'] == 'image']
    assert len(images) >= 2
    for item in images:
        image = service.workspace / item['path']
        assert image.exists()
        data = image.read_bytes()
        assert data.startswith(b'\x89PNG')
        assert hashlib.sha256(data).hexdigest() == item['sha256']


def test_binary_math_kind_is_in_capability_contract():
    assert validate_capability_arguments('math_workspace', {
        'kind': 'binary_determinant_max', 'size': 3,
    })[0]
    assert not validate_capability_arguments('math_workspace', {
        'kind': 'binary_determinant_max', 'size': 4,
    })[0]


def test_root_knows_math_tool_action_interface():
    from runtime.conversation.context import RootCapabilityRegistry
    from runtime.conversation.tool_guides import read_tool_guide
    prompt = RuntimeModelRootAgent._system_prompt(RootCapabilityRegistry({}))
    assert 'binary_determinant_max' not in prompt
    assert 'math_workspace' in prompt
    assert 'kind=use_capability' in prompt
    assert 'binary_determinant_max' in read_tool_guide('math_workspace')['documentation']


def test_root_action_math_then_short_bubble_receives_tool_receipt():
    """Đường Root → action → kết quả → nói ngắn; quyết định model giả lập.

    Không nhầm test contract này với bằng chứng rằng LLM thực đã chọn tool.
    """
    from types import SimpleNamespace
    from runtime.conversation.contracts import ConversationEvent, NextConversationMove
    from runtime.conversation.context import RootCapabilityRegistry
    from runtime.capabilities.action import ActionCapability

    class Executor:
        def __init__(self): self.calls = []
        def execute_capability(self, action_id, arguments):
            self.calls.append((action_id, arguments))
            return SimpleNamespace(
                status='succeeded', success=True, message='Đã dựng 4 bước vào Workspace',
                data={'job_id':'test-1','artifact_count':15}, evidence=('artifact-job:test-1',),
                error_kind='', observer_reason='', to_public_meta=lambda: {},
            )

    class Router:
        executive_client = object()
        def __init__(self):
            self.seen = []
            self.steps = [
                {'kind':'use_capability','capability':'action', 'arguments':{
                    'action_id':'math_workspace',
                    'arguments':{'kind':'binary_determinant_max','size':3}}},
                {'kind':'move','intent':'Mời xem lời giải có bước','say':
                    'Đáp số là 2. Tôi đã để ma trận và chứng minh từng bước trên panel.',
                 'delivery':'tự nhiên','after':'wait_user'},
            ]
        def invoke_structured_worker(self, client, **kwargs):
            self.seen.append(kwargs)
            return self.steps.pop(0)

    executor = Executor()
    router = Router()
    result = RuntimeModelRootAgent(router).decide(
        event=ConversationEvent(event_id='e',session_id='s',kind='user_turn',text='Ma trận 0 1 cấp 3 det max?'),
        recent_history=(), note='', runtime_context={
            '__root_capabilities':RootCapabilityRegistry({'action':ActionCapability(executor)}),
            '__conversation_run_id':'r',
        },
    )
    assert isinstance(result, NextConversationMove)
    assert len(executor.calls) == 1
    assert executor.calls[0] == ('math_workspace',{'kind':'binary_determinant_max','size':3})
    receipts = router.seen[1]['payload']['tool_results']
    assert receipts[0]['result']['success'] is True
    assert receipts[0]['result']['data']['job_id'] == 'test-1'
    assert len(result.say) < 150
