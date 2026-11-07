# -*- coding: utf-8 -*-
"""Sổ kết quả thực thi move: độc lập với transcript DeepSeek và public history.

Mỗi record là một biên nhận MECHANICAL, không phải chỉ thị chọn câu tiếp theo.
Key nằm ở SQLite atomic state giống conversation note/continuation, không dùng
transcript model. Dữ liệu chỉ gồm chỉ dẫn move ngắn để Root nối đúng quyết định
cũ; không ghi speech audio, raw Writer reply hoặc chain-of-thought.
"""
from __future__ import annotations

import threading
import time
from typing import Any


class MoveOutcomeLedger:
    MAX_RECORDS = 16
    VALID_STATUSES = frozenset({
        'preparing', 'queued_for_audio', 'delivering', 'delivered', 'failed_before_delivery',
        'delivery_failed', 'cancelled', 'unconfirmed_after_restart',
    })
    TERMINAL = frozenset({'delivered', 'failed_before_delivery', 'delivery_failed',
                          'cancelled', 'unconfirmed_after_restart'})

    def __init__(self, *, atomic_store: Any = None, logger: Any = None) -> None:
        self.atomic_store = atomic_store
        self.logger = logger
        self._lock = threading.RLock()
        self._memory: dict[str, list[dict[str, Any]]] = {}

    @staticmethod
    def _sid(session_id: str) -> str:
        return str(session_id or 'default')[:180] or 'default'

    @classmethod
    def _key(cls, session_id: str) -> str:
        return f'eris.conversation_move_outcomes.v1:{cls._sid(session_id)}'

    @classmethod
    def _blank(cls, session_id: str) -> dict[str, Any]:
        return {'schema': 'eris.conversation_move_outcomes.v1',
                'session_id': cls._sid(session_id), 'records': []}

    def list(self, session_id: str) -> list[dict[str, Any]]:
        sid = self._sid(session_id)
        if self.atomic_store is not None:
            try:
                record = self.atomic_store.load(self._key(sid))
                if record is not None:
                    rows = record.payload.get('records', [])
                    if isinstance(rows, list):
                        safe = [dict(row) for row in rows if isinstance(row, dict)][-self.MAX_RECORDS:]
                        with self._lock:
                            self._memory[sid] = safe
                        return [dict(row) for row in safe]
            except Exception as exc:
                self._log('warning', '[move-outcome] durable read degraded: %s', type(exc).__name__)
        with self._lock:
            return [dict(row) for row in self._memory.get(sid, [])]

    def update(self, *, session_id: str, generation: int, run_id: str, move_id: str,
               status: str, delivery_id: str = '', event_id: str = '', intent: str = '',
               say: str = '', reason: str = '') -> None:
        """Upsert theo run+move, không cho callback muộn đảo terminal state."""
        if status not in self.VALID_STATUSES or not move_id or not run_id:
            return
        sid = self._sid(session_id)
        now = time.time()

        def apply(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
            result = [dict(row) for row in rows if isinstance(row, dict)]
            idx = next((i for i, row in enumerate(result)
                        if row.get('run_id') == run_id and row.get('move_id') == move_id), None)
            if idx is not None:
                existing = result[idx]
                if existing.get('status') in self.TERMINAL:
                    return result[-self.MAX_RECORDS:]
                existing.update({'status': status, 'updated_at': now})
                if reason:
                    existing['reason'] = str(reason)[:160]
                if delivery_id:
                    existing['delivery_id'] = str(delivery_id)[:180]
            else:
                result.append({
                    'run_id': str(run_id)[:180], 'move_id': str(move_id)[:180],
                    'generation': max(0, int(generation)), 'event_id': str(event_id)[:180],
                    'delivery_id': str(delivery_id)[:180], 'status': status,
                    'intent': str(intent)[:240], 'say_hint': str(say)[:360],
                    'reason': str(reason)[:160], 'updated_at': now,
                })
            return result[-self.MAX_RECORDS:]

        with self._lock:
            if self.atomic_store is not None:
                try:
                    with self.atomic_store.transaction(self._key(sid), self._blank(sid)) as tx:
                        rows = apply(list(tx.payload.get('records', [])))
                        tx.stage({**self._blank(sid), 'records': rows})
                    self._memory[sid] = rows
                    return
                except Exception as exc:
                    self._log('warning', '[move-outcome] durable update degraded: %s', type(exc).__name__)
            self._memory[sid] = apply(self._memory.get(sid, []))

    def cancel_inflight(self, session_id: str, *, reason: str) -> None:
        """User ngắt: không biến move chưa hoàn tất thành delivered."""
        for row in self.list(session_id):
            if row.get('status') in {'preparing', 'queued_for_audio', 'delivering'}:
                self.update(session_id=session_id, generation=int(row.get('generation', 0)),
                            run_id=row['run_id'], move_id=row['move_id'],
                            status='cancelled', reason=reason)

    def recover_inflight_on_restart(self, session_id: str) -> None:
        """Lần đầu kernel mới nhìn thấy phiên: không giả vờ âm thanh đã xong.

        SQLite biết move từng ở preparing/delivering nhưng không thể chứng minh
        TTS đã đến tai người dùng trước crash. Ghi UNKNOWN thay vì delivered,
        tuyệt đối không phát lại từ ledger.
        """
        for row in self.list(session_id):
            if row.get('status') in {'preparing', 'queued_for_audio', 'delivering'}:
                self.update(session_id=session_id, generation=int(row.get('generation', 0)),
                            run_id=row['run_id'], move_id=row['move_id'],
                            status='unconfirmed_after_restart',
                            reason='process_restart_delivery_unconfirmed')

    def feedback(self, session_id: str) -> list[dict[str, Any]]:
        """Tín hiệu cho Root kể cả khi public history rỗng / session reset."""
        reports = []
        for row in self.list(session_id)[-10:]:
            entry = {k: row[k] for k in (
                'run_id', 'move_id', 'generation', 'event_id', 'delivery_id',
                'status', 'intent', 'say_hint', 'reason', 'updated_at',
            ) if k in row}
            name = str(row.get('intent') or row.get('move_id') or 'unknown')[:240]
            status = row.get('status')
            reason = str(row.get('reason', ''))
            if status == 'failed_before_delivery':
                stage = 'prepare' if ':prepare:' in reason else 'writer/delivery preparation'
                summary = f'Move {name} thất bại ở {stage}; chưa gửi tới người dùng.'
            elif status == 'queued_for_audio':
                summary = f'Move {name} đã gửi tới hàng đợi phát; chưa có audio_start, chưa xác nhận user nghe.'
            elif status == 'delivering':
                summary = f'Move {name} đã bắt đầu phát âm thanh; chưa xác nhận phát xong.'
            elif status == 'delivered':
                summary = f'Move {name} đã phát xong.'
            elif status == 'cancelled':
                summary = f'Move {name} đã bị hủy; không tự phát lại.'
            elif status == 'unconfirmed_after_restart':
                summary = f'Move {name} không rõ đã phát xong trước restart hay chưa; không tự phát lại.'
            elif status == 'delivery_failed':
                summary = f'Move {name} gặp lỗi trong delivery; không tính là đã phát xong.'
            else:
                summary = f'Move {name} đang chuẩn bị; chưa gửi tới người dùng.'
            entry['outcome_summary'] = summary
            reports.append(entry)
        return reports

    def _log(self, level: str, message: str, *args: Any) -> None:
        method = getattr(self.logger, level, None)
        if callable(method):
            try:
                method(message, *args)
            except Exception:
                pass


__all__ = ['MoveOutcomeLedger']
