# -*- coding: utf-8 -*-
"""P2: Root-owned presentation, published only after the live public-text send.

No planner, watcher, second LLM or autonomous retry. Root chooses optional typed
blocks; tool bundle IDs originate ONLY from successful executor receipts. The
bridge is mechanical and uses the existing immutable P0/P1 sidecar protocol.
"""
from __future__ import annotations

import json
import math
import os
from pathlib import Path
import re
from typing import Any, Mapping, Sequence

from .artifact_protocol import (
    ArtifactContractError, build_artifact, build_bundle,
    validate_bundle, write_bundle_sidecar,
)

_BUNDLE_ID = re.compile(r'^pi_[a-f0-9]{24}$')
_ALLOWED = frozenset({'math', 'chart', 'table', 'code', 'document'})


def normalize_presentation_blocks(value: Any) -> tuple[dict[str, Any], ...]:
    """Fail-soft: invalid LLM data never leaks into a sidecar, TTS or history."""
    if not isinstance(value, list):
        return ()
    normalized: list[dict[str, Any]] = []
    for raw in value[:6]:
        if not isinstance(raw, Mapping) or raw.get('kind') not in _ALLOWED:
            continue
        content = raw.get('content')
        if not isinstance(content, Mapping):
            continue
        if raw.get('kind') == 'chart':
            # Require data the P1 chart renderer can actually draw; do not
            # create broken chart artifacts just because P0 shape is loose.
            series = content.get('series')
            if (content.get('chart_type') not in {'line', 'bar'} or
                    not isinstance(series, list) or len(series) != 1 or
                    not isinstance(series[0], Mapping)):
                continue
            values, labels = series[0].get('values'), series[0].get('labels')
            if (not isinstance(values, list) or not isinstance(labels, list) or
                    not 2 <= len(values) <= 180 or len(labels) != len(values) or
                    any(type(x) not in (float, int) or not math.isfinite(x) or abs(x) >= 1e9 for x in values) or
                    any(not isinstance(x, (str, int, float)) or len(str(x)) > 80 for x in labels)):
                continue
        if raw.get('kind') == 'document' and ('blocks' in content or content.get('format') not in {'markdown','text'}):
            # Model-originated documents may not forge image assets or source files.
            continue
        title = str(raw.get('title') or '')[:100]
        try:
            # Validate using the SAME P0 contract as real tool artifacts.
            candidate = build_artifact(
                turn_id='validation:root', source='root_presentation',
                kind=str(raw['kind']), content=content,
                stable_key='validation:' + str(len(normalized)), title=title,
            )
            # Tighten the per-move budget; P0 independently limits the bundle.
            if len(json.dumps(candidate, ensure_ascii=False).encode('utf-8')) > 24_000:
                continue
            normalized.append({'kind': candidate['kind'], 'content': candidate['content'], 'title': title})
        except (ArtifactContractError, TypeError, ValueError, OverflowError):
            continue
    return tuple(normalized)


def verified_tool_bundles(results: Sequence[Mapping[str, Any]]) -> tuple[str, ...]:
    """Discover only successful math/tutor executor results from this Root run.

    Never trust bundle identifiers emitted by the model in its final move.
    """
    identifiers: list[str] = []
    for item in results:
        if item.get('kind') != 'capability_result':
            continue
        response = item.get('result')
        if (item.get('capability') == 'tutor' and isinstance(response, Mapping)
                and response.get('status') == 'completed' and response.get('operation') == 'activity'):
            raw = response.get('artifact_bundle_id')
            if isinstance(raw, str) and _BUNDLE_ID.fullmatch(raw) and raw not in identifiers:
                identifiers.append(raw)
            continue
        if item.get('capability') != 'action':
            continue
        if not isinstance(response, Mapping) or response.get('action_id') != 'math_workspace' or response.get('success') is not True:
            continue
        data = response.get('data')
        raw = data.get('artifact_bundle_id') if isinstance(data, Mapping) else None
        if isinstance(raw, str) and _BUNDLE_ID.fullmatch(raw) and raw not in identifiers:
            identifiers.append(raw)
    return tuple(identifiers[:4])


# Compatibility for callers/tests that used the original math-only bridge.
verified_math_bundles = verified_tool_bundles


def publish_committed_presentation(*, turn_id: str, delivery_id: str,
                                  blocks: Sequence[Mapping[str, Any]] = (),
                                  tool_bundles: Sequence[str] = (),
                                  workspace: str | Path | None = None) -> Path | None:
    """Publish after the real transport send, not when Root merely prepares a turn.

    Tool sidecars must be job-provisional and match an executor-issued receipt.
    A bound copy is immutable; provisional gallery remains unchanged for audit.
    """
    if not isinstance(turn_id, str) or not re.fullmatch(r'[A-Za-z0-9_:.\-]{1,160}', turn_id) or '..' in turn_id:
        return None
    if not isinstance(delivery_id, str) or not re.fullmatch(r'[A-Za-z0-9_:.\-]{1,160}', delivery_id) or '..' in delivery_id:
        return None
    root = Path(workspace or os.getenv('ERIS_ARTIFACT_WORKSPACE', 'logs/eris_artifacts')).resolve()
    items: list[dict[str, Any]] = []
    # Freshly validate even when source was already normalized at Root boundary.
    for i, block in enumerate(normalize_presentation_blocks(list(blocks))):
        items.append(build_artifact(
            turn_id=turn_id, source='root_presentation', kind=block['kind'],
            content=block['content'], stable_key=f'{delivery_id}:{i}',
            order=10*i, title=block['title'],
            metadata={'turn_binding': 'bound', 'owner': 'root_agent'},
        ))
    for bundle_id in list(tool_bundles)[:4]:
        if not isinstance(bundle_id, str) or not _BUNDLE_ID.fullmatch(bundle_id):
            continue
        path = root / 'presentation_v1' / (bundle_id + '.json')
        try:
            if not path.is_file() or path.stat().st_size > 256_000 or path.is_symlink():
                continue
            bundle = validate_bundle(json.loads(path.read_text('utf-8')))
            if bundle['intent_id'] != bundle_id or not bundle['turn_id'].startswith('job:'):
                continue  # no cross-turn stealing; no rebinding a bound artifact
            for original in bundle['artifacts']:
                if len(items) >= 40:
                    break
                if original['source'] not in {'math_workspace', 'tutor_activity'} or original['kind'] not in _ALLOWED:
                    continue
                items.append(build_artifact(
                    turn_id=turn_id, source=original['source'], kind=original['kind'],
                    content=original['content'], stable_key=original['artifact_id'],
                    order=len(items)*10,
                    title=original['presentation'].get('title', '')[:200],
                    metadata={'turn_binding': 'bound', 'source_artifact_id': original['artifact_id'],
                              'source_bundle_id': bundle_id, 'owner': original['source']},
                ))
        except (OSError, ValueError, TypeError, KeyError, UnicodeError):
            continue
    if not items:
        return None
    return write_bundle_sidecar(build_bundle(turn_id, items, stable_key='root:' + delivery_id), root)
