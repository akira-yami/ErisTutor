# -*- coding: utf-8 -*-
"""P1 renderer receipts: passive read on Root decision, NEVER a polling agent loop.

A browser ACK says the UI rendered an artifact. It does not prove the content
correct, visible to the person, or processed by the AI.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from datetime import datetime, timedelta, timezone
from typing import Any

from .artifact_protocol import validate_bundle, validate_feedback


def recent_render_feedback(workspace: str | Path | None = None, *, limit: int = 6,
                           now: datetime | None = None) -> list[dict[str, Any]]:
    root = Path(workspace or os.getenv('ERIS_ARTIFACT_WORKSPACE', 'logs/eris_artifacts')) / 'presentation_v1'
    directory = root / 'feedback'
    if not directory.is_dir():
        return []
    current = now or datetime.now(timezone.utc)
    recent: list[dict[str, Any]] = []
    # Files are persisted by the UI; read a bounded window and verify against
    # the original immutable P0 bundle before telling Root about the receipt.
    try:
        files = sorted((p for p in directory.glob('art_*.r*.json') if p.is_file() and
                        p.stat().st_size <= 4096), key=lambda p: p.stat().st_mtime,
                       reverse=True)[:max(1, min(limit * 5, 50))]
    except OSError:
        return []
    for file in files:
        try:
            fb = validate_feedback(json.loads(file.read_text('utf-8')))
            if fb.get('renderer_id') != 'richchat-main' or fb.get('status') not in ('rendered', 'failed'):
                continue
            stamp = datetime.fromisoformat(str(fb['received_at']).replace('Z', '+00:00'))
            if stamp.tzinfo is None or stamp > current + timedelta(minutes=2) or current - stamp > timedelta(hours=1):
                continue
            if file.name != f"{fb['artifact_id']}.r{fb['revision']}.json":
                continue
            intent = fb['intent_id']
            if not intent.startswith('pi_') or not all(c in '0123456789abcdef' for c in intent[3:]):
                continue
            path = root / (intent + '.json')
            if not path.is_file() or path.stat().st_size > 256_000:
                continue
            bundle = validate_bundle(json.loads(path.read_text('utf-8')))
            if bundle['intent_id'] != intent:
                continue
            matches = [a for a in bundle['artifacts'] if
                       a['artifact_id'] == fb['artifact_id'] and a['revision'] == fb['revision']]
            if len(matches) != 1:
                continue
            artifact = matches[0]
            recent.append({
                'turn_id': bundle['turn_id'], 'artifact_id': artifact['artifact_id'],
                'kind': artifact['kind'], 'status': fb['status'], 'code': fb['code'],
                'retryable': bool(fb['retryable']), 'intent_id': intent,
                'renderer': 'richchat-main', 'rendered_at': stamp.isoformat(),
                'turn_binding': artifact.get('metadata', {}).get('turn_binding', 'unknown'),
            })
            if len(recent) >= limit:
                break
        except (ValueError, KeyError, OSError, TypeError, OverflowError):
            continue
    return recent
