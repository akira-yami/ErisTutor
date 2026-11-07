# -*- coding: utf-8 -*-
"""Unified Presentation P0: strict, renderer-agnostic artifact contract.

This module DOES NOT render content, call TTS, wake Root, or add a loop.
Legacy Workspace remains authoritative until the P1 PresentationRuntime bridge.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
from typing import Any, Mapping

ARTIFACT_SCHEMA = 'eris.artifact.v1'
BUNDLE_SCHEMA = 'eris.presentation.bundle.v1'
INTENT_SCHEMA = 'presentation.intent.v1'
FEEDBACK_SCHEMA = 'renderer.feedback.v1'
ARTIFACT_KINDS = frozenset({'math', 'chart', 'code', 'table', 'document', 'speech'})
TARGETS = frozenset({'rich_chat', 'workspace', 'bubble', 'tts'})
FEEDBACK_CODES = {
    'OK_ACCEPTED': 'accepted', 'OK_APPLIED': 'applied',
    'OK_RENDERED': 'rendered', 'OK_VISIBLE': 'visible',
    'E_SCHEMA': 'failed', 'E_UNSUPPORTED_KIND': 'failed',
    'E_STALE_REVISION': 'failed', 'E_RENDER': 'failed',
    'E_BUSY': 'failed', 'E_TIMEOUT': 'failed', 'E_TRANSPORT': 'failed',
}
MAX_BUNDLE_BYTES = 256_000
MAX_ARTIFACT_BYTES = 96_000
_ID_RE = re.compile(r'^[A-Za-z0-9_:\-./]{1,160}$')


class ArtifactContractError(ValueError):
    """Artifact/intent/feedback violates the v1 contract."""


def _obj(value: Any, label: str) -> dict:
    if not isinstance(value, Mapping):
        raise ArtifactContractError(f'{label}: expected object')
    return dict(value)


def _str(value: Any, label: str, *, maximum: int = 2000, empty: bool = False) -> str:
    if not isinstance(value, str) or len(value) > maximum or (not empty and not value):
        raise ArtifactContractError(f'{label}: invalid string')
    return value


def _id(value: Any, label: str) -> str:
    s = _str(value, label, maximum=160)
    if not _ID_RE.fullmatch(s):
        raise ArtifactContractError(f'{label}: invalid identifier')
    return s


def _int(value: Any, label: str, *, lower: int = 1, upper: int = 1_000_000) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not lower <= value <= upper:
        raise ArtifactContractError(f'{label}: invalid integer')
    return value


def _json_size(value: Any, label: str, maximum: int) -> bytes:
    try:
        raw = json.dumps(value, ensure_ascii=False, sort_keys=True,
                         separators=(',', ':'), allow_nan=False).encode('utf-8')
    except (TypeError, ValueError, OverflowError) as exc:
        raise ArtifactContractError(f'{label}: non-JSON data') from exc
    if len(raw) > maximum:
        raise ArtifactContractError(f'{label}: exceeds {maximum} bytes')
    return raw


def _content(kind: str, content: dict) -> None:
    if kind == 'math':
        _str(content.get('latex'), 'math.latex', maximum=8000)
        if 'plain_text' in content:
            _str(content['plain_text'], 'math.plain_text', maximum=12000, empty=True)
    elif kind == 'chart':
        _str(content.get('chart_type'), 'chart.chart_type', maximum=40)
        if not isinstance(content.get('series'), list) or not content['series']:
            raise ArtifactContractError('chart.series: expected nonempty array')
    elif kind == 'code':
        _str(content.get('language'), 'code.language', maximum=50, empty=True)
        _str(content.get('source'), 'code.source', maximum=40000, empty=True)
    elif kind == 'table':
        if not isinstance(content.get('columns'), list) or not isinstance(content.get('rows'), list):
            raise ArtifactContractError('table.columns/rows: expected arrays')
        if len(content['columns']) > 64 or len(content['rows']) > 1000:
            raise ArtifactContractError('table: too many columns/rows')
        for row in content['rows']:
            if not isinstance(row, list) or len(row) != len(content['columns']):
                raise ArtifactContractError('table: row width mismatch')
    elif kind == 'document':
        _str(content.get('format'), 'document.format', maximum=30)
        if content['format'] not in {'markdown', 'text', 'blocks'}:
            raise ArtifactContractError('document.format: unsupported')
        _str(content.get('source'), 'document.source', maximum=48000, empty=True)
        if 'blocks' in content:
            if not isinstance(content['blocks'], list) or len(content['blocks']) > 120:
                raise ArtifactContractError('document.blocks: invalid')
            allowed_blocks = {'paragraph', 'heading', 'formula', 'code', 'table',
                              'list', 'quote', 'image', 'text', 'section'}
            for block in content['blocks']:
                item = _obj(block, 'document.block')
                if item.get('type') not in allowed_blocks:
                    raise ArtifactContractError('document.block: unsupported type')
                if item.get('type') == 'image':
                    image_path = _str(item.get('path'), 'image.path', maximum=260)
                    segments = image_path.replace('\\', '/').split('/')
                    if image_path.startswith(('/', '\\')) or ':' in image_path or '..' in segments or not all(segments):
                        raise ArtifactContractError('image.path: unsafe relative path')
                    if not re.fullmatch(r'[a-fA-F0-9]{64}', str(item.get('sha256', ''))):
                        raise ArtifactContractError('image.sha256: invalid')
    elif kind == 'speech':
        _str(content.get('text'), 'speech.text', maximum=12000)
        _str(content.get('voice_text'), 'speech.voice_text', maximum=12000)
        _str(content.get('sync_group'), 'speech.sync_group', maximum=160)
        if type(content.get('interruptible')) is not bool:
            raise ArtifactContractError('speech.interruptible: expected boolean')


def validate_artifact(value: Mapping[str, Any]) -> dict:
    item = _obj(value, 'artifact')
    if item.get('schema') != ARTIFACT_SCHEMA:
        raise ArtifactContractError('artifact.schema: unsupported')
    for field in ('artifact_id', 'turn_id', 'source'):
        _id(item.get(field), field)
    kind = item.get('kind')
    if kind not in ARTIFACT_KINDS:
        raise ArtifactContractError('artifact.kind: unsupported')
    _int(item.get('revision'), 'revision')
    content = _obj(item.get('content'), 'content')
    if 'activity' in content:
        if item.get('source') != 'tutor_activity' or kind != 'document':
            raise ArtifactContractError('activity: trusted tutor source required')
        activity = _obj(content['activity'], 'activity')
        # Hợp đồng cũ (4 trường) vẫn hợp lệ. Typed activity có thêm
        # response_mode, nhưng KHÔNG được chứa expected_text/correct/đáp án.
        legacy = {'activity_id', 'nonce', 'revision', 'options'}
        if set(activity) not in (legacy, legacy | {'response_mode'}):
            raise ArtifactContractError('activity: invalid fields')
        mode = activity.get('response_mode', 'choice')
        if mode not in ('choice', 'numeric', 'reflection'):
            raise ArtifactContractError('activity: invalid response mode')
        if not re.fullmatch(r'activity:[a-f0-9]{32}', str(activity.get('activity_id', ''))):
            raise ArtifactContractError('activity: invalid id')
        for field, length in (('nonce', 32), ('revision', 64)):
            if not re.fullmatch(r'[a-f0-9]{' + str(length) + r'}', str(activity.get(field, ''))):
                raise ArtifactContractError('activity: invalid ' + field)
        if (not isinstance(activity.get('options'), list)
                or (not 2 <= len(activity['options']) <= 6 if mode == 'choice' else len(activity['options']) != 0)
                or any(not isinstance(x, str) or not x or len(x) > 500 for x in activity['options'])):
            raise ArtifactContractError('activity: invalid choices')
    _content(kind, content)
    presentation = _obj(item.get('presentation'), 'presentation')
    targets = presentation.get('target')
    if not isinstance(targets, list) or not targets or any(t not in TARGETS for t in targets) or len(set(targets)) != len(targets):
        raise ArtifactContractError('presentation.target: invalid')
    _int(presentation.get('order'), 'presentation.order', lower=0)
    if 'title' in presentation:
        _str(presentation['title'], 'presentation.title', maximum=200, empty=True)
    if 'depends_on' in item:
        if not isinstance(item['depends_on'], list):
            raise ArtifactContractError('depends_on: expected array')
        for dep in item['depends_on']:
            _id(dep, 'depends_on[]')
    if 'metadata' in item:
        _obj(item['metadata'], 'metadata')
    _json_size(item, 'artifact', MAX_ARTIFACT_BYTES)
    return item


def validate_bundle(value: Mapping[str, Any]) -> dict:
    bundle = _obj(value, 'bundle')
    if bundle.get('schema') != BUNDLE_SCHEMA:
        raise ArtifactContractError('bundle.schema: unsupported')
    _id(bundle.get('intent_id'), 'intent_id')
    turn_id = _id(bundle.get('turn_id'), 'turn_id')
    artifacts = bundle.get('artifacts')
    if not isinstance(artifacts, list) or len(artifacts) > 120:
        raise ArtifactContractError('bundle.artifacts: invalid')
    seen: set[tuple[str, int]] = set()
    for art in artifacts:
        current = validate_artifact(art)
        if current['turn_id'] != turn_id:
            raise ArtifactContractError('bundle: artifact belongs to another turn')
        key = (current['artifact_id'], current['revision'])
        if key in seen:
            raise ArtifactContractError('bundle: repeated artifact revision')
        seen.add(key)
    _json_size(bundle, 'bundle', MAX_BUNDLE_BYTES)
    return bundle


def validate_intent(value: Mapping[str, Any]) -> dict:
    intent = _obj(value, 'presentation_intent')
    if intent.get('type') != INTENT_SCHEMA:
        raise ArtifactContractError('intent.type: unsupported')
    _id(intent.get('intent_id'), 'intent_id')
    _id(intent.get('turn_id'), 'turn_id')
    if intent.get('mode') not in {'speech_only', 'artifacts_only', 'speech_and_artifacts'}:
        raise ArtifactContractError('intent.mode: unsupported')
    targets = intent.get('targets')
    if not isinstance(targets, list) or any(t not in TARGETS for t in targets):
        raise ArtifactContractError('intent.targets: invalid')
    refs = intent.get('artifact_refs', [])
    if not isinstance(refs, list) or len(refs) > 120:
        raise ArtifactContractError('artifact_refs: expected bounded array')
    for ref in refs:
        _id(ref, 'artifact_refs[]')
    _json_size(intent, 'intent', 16_000)
    return intent


def validate_feedback(value: Mapping[str, Any]) -> dict:
    fb = _obj(value, 'renderer.feedback')
    if fb.get('type') != FEEDBACK_SCHEMA:
        raise ArtifactContractError('feedback.type: unsupported')
    for field in ('renderer_id', 'intent_id', 'artifact_id'):
        _id(fb.get(field), field)
    _int(fb.get('revision'), 'revision')
    code = fb.get('code')
    if code not in FEEDBACK_CODES or fb.get('status') != FEEDBACK_CODES[code]:
        raise ArtifactContractError('feedback: code/status mismatch')
    if type(fb.get('retryable')) is not bool:
        raise ArtifactContractError('feedback.retryable: expected boolean')
    _json_size(fb, 'feedback', 8_000)
    return fb


def _stable_id(prefix: str, *parts: Any) -> str:
    raw = _json_size(parts, 'id-seed', 120_000)
    return prefix + '_' + hashlib.sha256(raw).hexdigest()[:24]


def build_artifact(*, turn_id: str, source: str, kind: str, content: Mapping[str, Any],
                   stable_key: str, revision: int = 1, target: tuple[str, ...] = ('rich_chat',),
                   order: int = 0, title: str = '', metadata: Mapping[str, Any] | None = None) -> dict:
    artifact = {
        'schema': ARTIFACT_SCHEMA, 'artifact_id': _stable_id('art', turn_id, source, stable_key),
        'turn_id': turn_id, 'kind': kind, 'revision': revision, 'source': source,
        'content': dict(content),
        'presentation': {'target': list(target), 'order': order, 'title': title[:200]},
    }
    if metadata:
        artifact['metadata'] = dict(metadata)
    return validate_artifact(artifact)


def build_bundle(turn_id: str, artifacts: list[dict], *, stable_key: str) -> dict:
    return validate_bundle({
        'schema': BUNDLE_SCHEMA, 'intent_id': _stable_id('pi', turn_id, stable_key),
        'turn_id': turn_id, 'artifacts': artifacts,
    })


def adapter_math_workspace(payload: Mapping[str, Any], *, turn_id: str | None = None) -> dict:
    """Convert real math_worker blocks, keeping original ordering and asset refs.

    Without a supplied Root turn ID use a stable job-local ID. P1 must attach
    the real conversation turn before publishing; it must not guess one.
    """
    raw = _obj(payload, 'math_workspace payload')
    if raw.get('parse_mode') != 'workspace_artifact':
        raise ArtifactContractError('math_workspace: unexpected legacy format')
    job = _id(raw.get('job_id'), 'job_id')
    turn = _id(turn_id or ('job:' + job), 'turn_id')
    items = raw.get('artifacts')
    if not isinstance(items, list) or len(items) > 120:
        raise ArtifactContractError('math_workspace.artifacts: invalid')
    title = str(raw.get('title') or 'Toán học')[:200]
    artifacts: list[dict] = []
    for index, element in enumerate(items):
        block = _obj(element, f'math.blocks[{index}]')
        legacy_kind = block.get('type')
        if legacy_kind == 'formula':
            kind, content = 'math', {'latex': block.get('latex', ''), 'plain_text': block.get('text', '')}
        elif legacy_kind == 'table':
            kind, content = 'table', {'columns': block.get('headers', []), 'rows': block.get('rows', [])}
        elif legacy_kind == 'code':
            kind, content = 'code', {'language': block.get('language', ''), 'source': block.get('text', '')}
        elif legacy_kind in {'text', 'section', 'image'}:
            kind = 'document'
            content = {'format': 'blocks', 'source': str(block.get('text', '')) if legacy_kind != 'image' else '',
                       'blocks': [block]}
        else:
            raise ArtifactContractError(f'math.blocks[{index}]: unsupported legacy kind {legacy_kind!r}')
        artifacts.append(build_artifact(
            turn_id=turn, source='math_workspace', kind=kind, content=content,
            stable_key=f'{job}:{index}', order=index * 10, title=title,
            metadata={'legacy_job_id': job, 'legacy_type': legacy_kind,
                      'turn_binding': 'bound' if turn_id else 'job_provisional'},
        ))
    return build_bundle(turn, artifacts, stable_key='math:' + job)


def adapter_document_source(doc: Mapping[str, Any], *, source_text: str = '') -> dict:
    """Convert already prepared committed document without modifying speech."""
    raw = _obj(doc, 'document_source payload')
    if raw.get('parse_mode') != 'workspace_document':
        raise ArtifactContractError('document_source: unexpected legacy format')
    document_id = _id(raw.get('document_id'), 'document_id')
    turn_raw = str(raw.get('turn_id') or '')
    turn = _id(turn_raw or ('document:' + document_id), 'turn_id')
    blocks = raw.get('blocks')
    if not isinstance(blocks, list):
        raise ArtifactContractError('document_source.blocks: expected array')
    src = _str(source_text, 'source_text', maximum=48000, empty=True)
    # The document hash is computed from normalized text; never silently bind
    # an unrelated source text to a committed document.
    if src:
        from .structured_output import text_digest
        if text_digest(src) != raw.get('text_hash'):
            raise ArtifactContractError('document_source: source/hash mismatch')
    artifact = build_artifact(
        turn_id=turn, source='document_source', kind='document', stable_key=document_id,
        content={'format': 'blocks', 'source': src, 'blocks': blocks, 'text_hash': raw.get('text_hash', '')},
        title='Tài liệu Eris', metadata={'legacy_document_id': document_id,
                                         'delivery_id': str(raw.get('delivery_id') or ''),
                                         'turn_binding': 'bound' if turn_raw else 'document_provisional'},
    )
    return build_bundle(turn, [artifact], stable_key='document:' + document_id)


def write_bundle_sidecar(bundle: Mapping[str, Any], workspace: str | Path) -> Path:
    """Atomically publish a JSON sidecar for P1 ingestion, no network/UI effects.

    Files are deterministic across replays. We reject changed content for an
    existing ID/revision so an old worker cannot overwrite newer data.
    """
    doc = validate_bundle(bundle)
    root = Path(workspace).resolve() / 'presentation_v1'
    root.mkdir(parents=True, exist_ok=True)
    # intent IDs are used as filenames: reject path separators/dot components.
    intent_id = _id(doc['intent_id'], 'intent_id')
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,160}', intent_id):
        raise ArtifactContractError('sidecar: unsafe intent ID')
    name = intent_id + '.json'
    target = root / name
    serialized = _json_size(doc, 'bundle', MAX_BUNDLE_BYTES)
    if target.is_file():
        if target.read_bytes() != serialized:
            raise ArtifactContractError('sidecar: same intent ID with changed bytes')
        return target
    import tempfile
    with tempfile.NamedTemporaryFile(dir=root, mode='wb', prefix='.artifact-', suffix='.tmp', delete=False) as f:
        f.write(serialized)
        temporary = Path(f.name)
    try:
        # Defend against races: both writers must produce identical payload.
        if target.is_file():
            if target.read_bytes() != serialized:
                raise ArtifactContractError('sidecar: conflicting writer')
            return target
        # Linking is atomic and refuses to clobber a concurrent writer.
        try:
            os.link(temporary, target)
        except FileExistsError as exc:
            if target.read_bytes() != serialized:
                raise ArtifactContractError('sidecar: conflicting writer') from exc
        return target
    finally:
        temporary.unlink(missing_ok=True)


def protocol_enabled() -> bool:
    """P0 runs automatically; explicit opt-out is only for emergency rollback."""
    return os.getenv('ERIS_ARTIFACT_PROTOCOL_V1', '1').strip().lower() not in {
        '0', 'false', 'no', 'off',
    }
