# -*- coding: utf-8 -*-
"""P3 passive presentation health for Root, based on committed bundles + verified ACKs.

A missing browser receipt means *not observed*, not failed.  No loops, network,
LLM inference, automatic public response, or tool execution is started here.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
from typing import Any

from .artifact_protocol import validate_bundle
from .presentation_feedback import recent_render_feedback


def recent_presentation_health(workspace: str | Path | None = None, *, limit: int = 5,
                               now: datetime | None = None) -> list[dict[str, Any]]:
    root = Path(workspace or os.getenv('ERIS_ARTIFACT_WORKSPACE', 'logs/eris_artifacts')) / 'presentation_v1'
    if not root.is_dir():
        return []
    current = now or datetime.now(timezone.utc)
    recent = recent_render_feedback(workspace, limit=48, now=current)
    by_artifact = {(r['artifact_id'], r['intent_id']): r for r in recent}
    try:
        files = sorted((f for f in root.glob('pi_*.json')
                        if f.is_file() and not f.is_symlink() and f.stat().st_size <= 256_000),
                       key=lambda f: f.stat().st_mtime, reverse=True)[:40]
    except OSError:
        return []
    output = []
    for file in files:
        try:
            age = (current - datetime.fromtimestamp(file.stat().st_mtime, timezone.utc)).total_seconds()
            # The UI has a bounded 36-bundle window: do not ask Root to repair
            # unbounded historical work or a future-dated bundle.
            if age < -120 or age > 1800:
                continue
            bundle = validate_bundle(json.loads(file.read_text('utf8')))
            if file.name != bundle['intent_id'] + '.json' or bundle['turn_id'].startswith(('job:', 'document:')):
                continue
            for art in bundle['artifacts']:
                if 'rich_chat' not in art['presentation']['target']:
                    continue
                if art.get('metadata', {}).get('turn_binding') != 'bound':
                    continue
                ack = by_artifact.get((art['artifact_id'], bundle['intent_id']))
                if ack is not None:
                    state = 'rendered' if ack['status'] == 'rendered' else 'failed'
                    next_action = ('none' if state == 'rendered' else
                                   'retry_ui_or_revise_if_current_request' if ack.get('retryable') else
                                   'revise_if_current_request')
                else:
                    state, next_action = 'awaiting_viewer', 'wait_for_rich_chat'
                output.append({
                    'turn_id': bundle['turn_id'],
                    'artifact_id': art['artifact_id'],
                    'intent_id': bundle['intent_id'],
                    'kind': art['kind'], 'revision': art['revision'],
                    'status': state,
                    'code': ack['code'] if ack else 'NO_RENDER_RECEIPT',
                    'next_action': next_action,
                    'age_seconds': max(0, int(age)),
                })
                if len(output) >= max(1, min(10, limit)):
                    return output
        except (OSError, ValueError, TypeError, KeyError, OverflowError, UnicodeError):
            continue
    return output
