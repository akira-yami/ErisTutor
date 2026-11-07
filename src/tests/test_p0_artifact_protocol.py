"""P0: contract + real legacy adapter paths. Does not claim UI rendering."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from runtime.workspace.artifact_protocol import (
    ARTIFACT_SCHEMA, ArtifactContractError,
    adapter_math_workspace, adapter_document_source,
    build_artifact, build_bundle, protocol_enabled,
    validate_artifact, validate_bundle, validate_feedback, validate_intent,
    write_bundle_sidecar,
)
from runtime.workspace.structured_output import prepare_document


def math_payload():
    return {
        'parse_mode': 'workspace_artifact', 'workspace_version': 1,
        'job_id': 'job12', 'title': 'Tích phân',
        'artifacts': [
            {'type': 'text', 'text': 'Tính diện tích'},
            {'type': 'formula', 'latex': r'\int_0^1 x\,dx=\frac12'},
            {'type': 'image', 'path': 'jobs/job12/out/plot.png', 'sha256': '0' * 64},
            {'type': 'table', 'headers': ['x', 'f(x)'], 'rows': [['0', '0'], ['1', '1']]},
            {'type': 'code', 'language': 'python', 'text': 'print(1 / 2)'},
        ],
    }


def test_math_adapter_preserves_order_and_content():
    bundle = adapter_math_workspace(math_payload())
    assert validate_bundle(bundle) == bundle
    assert bundle['turn_id'] == 'job:job12'
    assert [a['kind'] for a in bundle['artifacts']] == ['document', 'math', 'document', 'table', 'code']
    assert [a['presentation']['order'] for a in bundle['artifacts']] == [0, 10, 20, 30, 40]
    assert bundle['artifacts'][1]['content']['latex'] == r'\int_0^1 x\,dx=\frac12'
    assert bundle['artifacts'][2]['content']['blocks'][0]['path'] == 'jobs/job12/out/plot.png'
    assert bundle['artifacts'][3]['content']['rows'][1] == ['1', '1']
    assert all(a['metadata']['turn_binding'] == 'job_provisional' for a in bundle['artifacts'])


def test_math_adapter_bound_turn_and_stable_id():
    p = math_payload()
    a = adapter_math_workspace(p, turn_id='chat:123')
    b = adapter_math_workspace(p, turn_id='chat:123')
    assert a == b
    assert a['turn_id'] == 'chat:123'
    assert a['artifacts'][0]['metadata']['turn_binding'] == 'bound'
    assert adapter_math_workspace(p)['artifacts'][0]['artifact_id'] != a['artifacts'][0]['artifact_id']


def test_math_adapter_rejects_unknown_legacy_block():
    p = math_payload()
    p['artifacts'].append({'type': 'random_agent_html', 'html': '<script>'})
    with pytest.raises(ArtifactContractError):
        adapter_math_workspace(p)


def test_document_adapter_keeps_text_and_committed_hash():
    text = 'Cách giải: $\\int_0^1 x\\,dx=\\frac12$.\n\n```python\nprint(1)\n```'
    doc = prepare_document(text=text, session_id='s', generation=3, run_id='r',
                           delivery_id='del', turn_id='chat:13', render_math=False)
    result = adapter_document_source(doc, source_text=text)
    assert validate_bundle(result) == result
    artifact = result['artifacts'][0]
    assert artifact['kind'] == 'document'
    assert artifact['content']['source'] == text
    assert artifact['content']['text_hash'] == doc['text_hash']
    assert artifact['content']['blocks'] == doc['blocks']
    assert artifact['metadata']['turn_binding'] == 'bound'


def test_document_adapter_fails_on_wrong_source():
    doc = prepare_document(text='đúng', session_id='s', generation=0, run_id='r',
                           delivery_id='del', render_math=False)
    with pytest.raises(ArtifactContractError, match='hash mismatch'):
        adapter_document_source(doc, source_text='sai')


def test_document_adapter_provisional_turn_when_absent():
    doc = prepare_document(text='hello', session_id='s', generation=1, run_id='r',
                           delivery_id='del', render_math=False)
    b = adapter_document_source(doc, source_text='hello')
    assert b['turn_id'] == 'document:' + doc['document_id']
    assert b['artifacts'][0]['metadata']['turn_binding'] == 'document_provisional'


def test_bundle_deduplicates_repeated_revision():
    art = build_artifact(turn_id='chat:1', source='root', kind='math',
                         stable_key='x', content={'latex': r'\frac12'})
    with pytest.raises(ArtifactContractError, match='repeated'):
        build_bundle('chat:1', [art, art], stable_key='test')


def test_bundle_rejects_other_turn():
    art = build_artifact(turn_id='chat:1', source='root', kind='math',
                         stable_key='x', content={'latex': 'x'})
    with pytest.raises(ArtifactContractError, match='another turn'):
        build_bundle('chat:2', [art], stable_key='test')


def test_table_width_must_match():
    with pytest.raises(ArtifactContractError, match='row width mismatch'):
        build_artifact(turn_id='chat:1', source='root', kind='table', stable_key='x',
                       content={'columns': ['a', 'b'], 'rows': [['1']]})


def test_strict_revision_cannot_be_bool():
    a = adapter_math_workspace(math_payload())['artifacts'][1]
    a['revision'] = True
    with pytest.raises(ArtifactContractError, match='revision'):
        validate_artifact(a)


def test_unknown_kind_and_target_rejected():
    a = adapter_math_workspace(math_payload())['artifacts'][1]
    a['kind'] = 'html'
    with pytest.raises(ArtifactContractError):
        validate_artifact(a)
    a['kind'] = 'math'
    a['presentation']['target'] = ['internet']
    with pytest.raises(ArtifactContractError):
        validate_artifact(a)


def test_intent_contract():
    intent = {'type': 'presentation.intent.v1', 'intent_id': 'pi_test',
              'turn_id': 'chat:1', 'mode': 'speech_and_artifacts',
              'targets': ['bubble', 'rich_chat'], 'artifact_refs': ['art_xyz'],
              'feedback': {'required': 'rendered'}}
    assert validate_intent(intent) == intent
    with pytest.raises(ArtifactContractError):
        validate_intent({**intent, 'targets': ['alien']})


def test_feedback_code_status_contract():
    fb = {'type': 'renderer.feedback.v1', 'renderer_id': 'richchat-main',
          'intent_id': 'pi_test', 'artifact_id': 'art_xyz', 'revision': 1,
          'status': 'rendered', 'code': 'OK_RENDERED', 'retryable': False}
    assert validate_feedback(fb) == fb
    with pytest.raises(ArtifactContractError, match='mismatch'):
        validate_feedback({**fb, 'status': 'visible'})


def test_sidecar_atomic_idempotence_and_conflict(tmp_path):
    bundle = adapter_math_workspace(math_payload())
    target = write_bundle_sidecar(bundle, tmp_path)
    assert target.is_file()
    assert json.loads(target.read_text('utf-8')) == bundle
    assert write_bundle_sidecar(bundle, tmp_path) == target
    conflicting = json.loads(json.dumps(bundle))
    conflicting['artifacts'][0]['content']['source'] = 'changed text'
    with pytest.raises(ArtifactContractError, match='changed bytes'):
        write_bundle_sidecar(conflicting, tmp_path)


def test_sidecar_rejects_filename_traversal(tmp_path):
    bundle = adapter_math_workspace(math_payload())
    bundle['intent_id'] = '../evil'
    with pytest.raises(ArtifactContractError, match='unsafe intent'):
        write_bundle_sidecar(bundle, tmp_path)
    assert not (tmp_path / 'evil.json').exists()


def test_invalid_nan_cannot_enter_protocol():
    with pytest.raises(ArtifactContractError, match='non-JSON'):
        build_artifact(turn_id='chat:1', source='root', kind='chart', stable_key='plot',
                       content={'chart_type': 'line', 'series': [{'values': [float('nan')]}]})


def test_protocol_is_on_without_configuration_and_allows_emergency_rollback(monkeypatch):
    monkeypatch.delenv('ERIS_ARTIFACT_PROTOCOL_V1', raising=False)
    assert protocol_enabled()
    monkeypatch.setenv('ERIS_ARTIFACT_PROTOCOL_V1', '0')
    assert not protocol_enabled()
    monkeypatch.setenv('ERIS_ARTIFACT_PROTOCOL_V1', 'off')
    assert not protocol_enabled()
    monkeypatch.setenv('ERIS_ARTIFACT_PROTOCOL_V1', '1')
    assert protocol_enabled()


def test_legacy_math_payload_is_not_modified():
    original = math_payload()
    snapshot = json.loads(json.dumps(original))
    adapter_math_workspace(original)
    assert original == snapshot


def test_protocol_adapter_output_does_not_confuse_udp_ack():
    payload = math_payload()
    with patch('runtime.workspace.math_artifacts.send_artifacts_udp', return_value=False) as send:
        send(payload)
        assert send.call_args.args[0]['parse_mode'] == 'workspace_artifact'


def test_document_adapter_rejects_oversized_document():
    doc = prepare_document(text='small', session_id='s', generation=1, run_id='r',
                           delivery_id='del', render_math=False)
    with pytest.raises(ArtifactContractError):
        adapter_document_source(doc, source_text='x' * 48001)


def test_reject_image_path_traversal_and_untrusted_type():
    p = math_payload()
    p['artifacts'][2]['path'] = '../../private.txt'
    with pytest.raises(ArtifactContractError, match='unsafe relative path'):
        adapter_math_workspace(p)
    p = math_payload()
    p['artifacts'][2]['sha256'] = 'not-a-hash'
    with pytest.raises(ArtifactContractError, match='sha256'):
        adapter_math_workspace(p)


def test_executor_real_math_boundary_auto_enabled(monkeypatch, tmp_path):
    """The existing UDP transport must receive exactly the pre-P0 payload."""
    from actions.executor import ActionExecutor
    import runtime.workspace as workspace

    payload = math_payload()
    class FakeMathService:
        def __init__(self, root): self.workspace = Path(root)
        def execute_sync(self, request): return json.loads(json.dumps(payload))

    captured = []
    monkeypatch.delenv('ERIS_ARTIFACT_PROTOCOL_V1', raising=False)
    monkeypatch.setenv('ERIS_ARTIFACT_WORKSPACE', str(tmp_path))
    monkeypatch.setattr(workspace, 'MathWorkspaceService', FakeMathService)
    monkeypatch.setattr(workspace, 'send_artifacts_udp', lambda p: captured.append(p) or False)
    res = ActionExecutor()._math_workspace('{"kind":"integrate","expr":"x"}')
    assert res.success and res.observed
    assert captured == [payload]
    assert res.data['panel_rendered'] is False
    assert json.loads(res.data['artifact_refs'])
    assert res.data['artifact_turn_binding'] == 'job_provisional'
    bundles = list((tmp_path / 'presentation_v1').glob('*.json'))
    assert len(bundles) == 1
    assert json.loads(bundles[0].read_text('utf-8'))['intent_id'] == res.data['artifact_bundle_id']


def test_executor_math_protocol_disabled_does_not_write(monkeypatch, tmp_path):
    from actions.executor import ActionExecutor
    import runtime.workspace as workspace
    class FakeMathService:
        def __init__(self, root): self.workspace = Path(root)
        def execute_sync(self, request): return math_payload()
    monkeypatch.setenv('ERIS_ARTIFACT_PROTOCOL_V1', '0')
    monkeypatch.setenv('ERIS_ARTIFACT_WORKSPACE', str(tmp_path))
    monkeypatch.setattr(workspace, 'MathWorkspaceService', FakeMathService)
    monkeypatch.setattr(workspace, 'send_artifacts_udp', lambda p: True)
    res = ActionExecutor()._math_workspace('{"kind":"integrate","expr":"x"}')
    assert res.success and res.data['panel_rendered'] is True
    assert 'artifact_refs' not in res.data
    assert not (tmp_path / 'presentation_v1').exists()


def test_document_dispatch_auto_sidecar_and_legacy_udp(monkeypatch, tmp_path):
    from runtime.workspace import structured_output as docmod
    original_prepare = docmod.prepare_document
    monkeypatch.delenv('ERIS_ARTIFACT_PROTOCOL_V1', raising=False)
    monkeypatch.setenv('ERIS_ARTIFACT_WORKSPACE', str(tmp_path))
    sent = []
    monkeypatch.setattr(docmod._POOL, 'submit', lambda callback: callback())
    monkeypatch.setattr(docmod, 'send_document_acknowledged',
                        lambda doc, **kw: sent.append(doc) or True)
    monkeypatch.setattr(docmod, 'prepare_document', lambda **kw: original_prepare(**{**kw, 'render_math': False}))
    docmod.dispatch_committed_document(text='Một lời giải $x^2$.', session_id='s', generation=2,
                                       run_id='r', delivery_id='delivery', turn_id='chat:10')
    assert len(sent) == 1 and sent[0]['parse_mode'] == 'workspace_document'
    bundles = list((tmp_path / 'presentation_v1').glob('*.json'))
    assert len(bundles) == 1
    loaded = json.loads(bundles[0].read_text('utf-8'))
    assert loaded['artifacts'][0]['kind'] == 'document'
    assert loaded['turn_id'] == 'chat:10'


def test_json_schema_files_accept_real_adapter_output():
    jsonschema = pytest.importorskip('jsonschema')
    root = Path(__file__).resolve().parents[2] / 'contracts/presentation_v1'
    bundle = adapter_math_workspace(math_payload())
    doc = prepare_document(text='Đúng', session_id='s', generation=3,
                           run_id='r', delivery_id='delivery', render_math=False)
    second = adapter_document_source(doc, source_text='Đúng')
    for filename, examples in {
        'artifact.schema.json': [*bundle['artifacts'], *second['artifacts']],
        'bundle.schema.json': [bundle, second],
        'intent.schema.json': [{'type': 'presentation.intent.v1', 'intent_id': 'pi1',
                                'turn_id': 'chat:1', 'mode': 'artifacts_only',
                                'targets': ['rich_chat'], 'artifact_refs': []}],
        'feedback.schema.json': [{'type': 'renderer.feedback.v1', 'renderer_id': 'richchat',
                                  'intent_id': 'pi1', 'artifact_id': 'art1', 'revision': 1,
                                  'status': 'rendered', 'code': 'OK_RENDERED', 'retryable': False}],
    }.items():
        schema = json.loads((root / filename).read_text('utf-8'))
        jsonschema.Draft202012Validator.check_schema(schema)
        for example in examples:
            jsonschema.validate(example, schema)
