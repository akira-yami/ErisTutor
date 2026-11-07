"""Opt-in, write-only authority tracing for the *existing* Eris decision path.

No raw user messages, prompts, screenshots, LLM text or tool args are stored.
Trace errors must never change the conversation decision. Disabled by default.
"""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
import json
import os
from pathlib import Path
import time
import uuid
from typing import Any, Iterator

_CORRELATION: ContextVar[str] = ContextVar('eris_autonomy_correlation', default='')
_ALLOWED = frozenset({
    'mode', 'source', 'status', 'reason_code', 'selection_source', 'frame_id',
    'session_id', 'turn_id', 'decision_id', 'event_id', 'source_revision',
    'channel', 'has_motor_selection', 'scope', 'coalition_count', 'elapsed_ms',
    'autonomous', 'has_continuation', 'result_status',
})


def enabled() -> bool:
    return str(os.getenv('ERIS_AUTONOMY_TRACE', '0')).strip().lower() in ('1', 'true', 'yes')


def correlation_id() -> str:
    return _CORRELATION.get()


def emit(event: str, **fields: Any) -> None:
    if not enabled():
        return
    try:
        record = {
            'schema': 'eris.autonomy.authority_trace.v1',
            'at': time.time(),
            'event': str(event)[:90],
            'correlation_id': _CORRELATION.get(),
        }
        for key in _ALLOWED.intersection(fields):
            value = fields[key]
            if isinstance(value, (str, int, float, bool)) or value is None:
                record[key] = value if not isinstance(value, str) else value[:160]
        path = Path(os.getenv('ERIS_AUTONOMY_TRACE_PATH') or 'logs/autonomy_authority.jsonl')
        path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(str(path), os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o600)
        try:
            os.write(fd, (json.dumps(record, ensure_ascii=False, sort_keys=True) + '\n').encode('utf-8'))
        finally:
            os.close(fd)
    except Exception:
        # Observability cannot become a second coordinator or break public turns.
        pass


@contextmanager
def span(source: str, *, correlation: str = '') -> Iterator[str]:
    if not enabled():
        yield ''
        return
    token = _CORRELATION.set(str(correlation or uuid.uuid4().hex))
    began = time.monotonic()
    emit('span.start', source=source)
    try:
        yield _CORRELATION.get()
    except BaseException:
        emit('span.error', source=source)
        raise
    finally:
        emit('span.end', source=source, elapsed_ms=round((time.monotonic() - began) * 1000, 2))
        _CORRELATION.reset(token)
