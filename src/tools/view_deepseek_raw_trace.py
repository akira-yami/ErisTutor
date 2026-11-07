# -*- coding: utf-8 -*-
"""Xem/khôi phục RAW DeepSeek từ index nhỏ + object nén.

    python src/tools/view_deepseek_raw_trace.py logs/deepseek_raw
    python src/tools/view_deepseek_raw_trace.py logs/deepseek_raw --lane root_conversation --full
    python src/tools/view_deepseek_raw_trace.py logs/deepseek_raw --id <request_id> --export-request request.json

Hỗ trợ cả JSONL cũ (body inline). --full/--export mới đọc object lớn;
bảng thống kê không cần bung context. Không tự đọc/làm sạch nội dung nhạy cảm.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Khi chạy trực tiếp bằng python src/tools/..., cần thêm src vào sys.path.
_SRC = Path(__file__).resolve().parents[1]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from llm.raw_trace_store import restore_payload


def _read_events(path: Path):
    if path.is_dir():
        files = sorted(set(path.glob('events_*.jsonl')) | set(path.glob('deepseek_*.jsonl')))
    else:
        files = [path]
    for file in files:
        if not file.is_file():
            continue
        with file.open('r', encoding='utf-8') as stream:
            for line in stream:
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    # Đoạn cuối chưa ghi xong khi process bị ngắt.
                    continue
                if isinstance(event, dict):
                    yield event, file.parent


def _restore(event: dict, folder: Path, field: str):
    key = 'raw_request_ref' if field == 'request' else 'raw_response_ref'
    old_key = 'raw_request_json' if field == 'request' else 'raw_response_text'
    if key not in event:
        return event.get(old_key)
    raw = restore_payload(folder, event[key])
    if field == 'request':
        try:
            return json.loads(raw)
        except (ValueError, UnicodeError):
            return raw.decode('utf-8', errors='replace')
    encoding = str(event.get('response_encoding') or 'utf-8')
    try:
        return raw.decode(encoding, errors='replace')
    except LookupError:
        return raw.decode('utf-8', errors='replace')


def _full_event(event: dict, folder: Path, field: str) -> dict:
    output = dict(event)
    if field == 'request':
        output['raw_request_json'] = _restore(event, folder, 'request')
    elif field == 'response':
        output['raw_response_text'] = _restore(event, folder, 'response')
    return output


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description='Đọc raw HTTP DeepSeek (không truncation)')
    parser.add_argument('path', nargs='?', default='logs/deepseek_raw', type=Path)
    parser.add_argument('--role', default='', help='worker role: observer, executive, surface...')
    parser.add_argument('--lane', default='', help='cognitive_lane hoặc broker lane')
    parser.add_argument('--origin', default='', help='eris_client hoặc whale_gateway')
    parser.add_argument('--id', default='', help='một request ID cụ thể')
    parser.add_argument('--family', default='', help='hiển thị lượt Eris và tất cả upstream request có parent_request_id bằng ID này')
    parser.add_argument('--full', action='store_true', help='bung nguyên văn RAW có thể rất lớn')
    parser.add_argument('--stats', action='store_true', help='tính tổng dung lượng index, objects và RAW logic')
    parser.add_argument('--limit', type=int, default=100)
    parser.add_argument('--export-request', type=Path, help='xuất nguyên byte request ra file')
    parser.add_argument('--export-response', type=Path, help='xuất nguyên byte response ra file')
    args = parser.parse_args(argv)
    if not args.path.exists():
        parser.error(f'Không tìm thấy log: {args.path}')

    ordered = {}
    logical_bytes = 0
    for event, folder in _read_events(args.path):
        for field in ('raw_request_ref', 'raw_response_ref'):
            if isinstance(event.get(field), dict):
                logical_bytes += int(event[field].get('byte_length') or 0)
        request_id = event.get('request_id')
        if not request_id:
            continue
        # Không hợp nhất IDs giống nhau giữa process khác; request_id uuid phải duy nhất.
        record = ordered.setdefault(str(request_id), {'request': None, 'outcome': None})
        if event.get('event') == 'request':
            record['request'] = (event, folder)
        elif event.get('event') in {'response', 'transport_error'}:
            record['outcome'] = (event, folder)

    if args.stats:
        root = args.path if args.path.is_dir() else args.path.parent
        objects = list((root / 'objects').rglob('*.gz')) if (root / 'objects').exists() else []
        compressed = sum(p.stat().st_size for p in objects)
        indexes = list(root.glob('events_*.jsonl')) + list(root.glob('deepseek_*.jsonl'))
        index_bytes = sum(p.stat().st_size for p in indexes)
        ratio = logical_bytes / compressed if compressed else 0
        print(f'RAW logic: {logical_bytes:,} byte | object gzip: {compressed:,} byte | '
              f'index: {index_bytes:,} byte | unique objects: {len(objects):,} | '
              f'ratio RAW/object: {ratio:.2f}x')
        return 0

    selected = []
    for request_id, record in ordered.items():
        req = (record['request'] or ({}, args.path))[0]
        if args.id and request_id != args.id:
            continue
        if args.family and request_id != args.family and req.get('parent_request_id') != args.family:
            continue
        if args.role and args.role.lower() != str(req.get('worker_role', '')).lower():
            continue
        if args.origin and args.origin.lower() != str(req.get('origin', 'eris_client')).lower():
            continue
        if args.lane and args.lane.lower() not in {
            str(req.get('lane', '')).lower(), str(req.get('cognitive_lane', '')).lower()
        }:
            continue
        selected.append((request_id, record))

    if args.export_request or args.export_response:
        if len(selected) != 1:
            parser.error('--export-* yêu cầu --id trỏ tới đúng một request')
        record = selected[0][1]
        for target, part, name in ((args.export_request, 'request', 'raw_request_ref'),
                                    (args.export_response, 'outcome', 'raw_response_ref')):
            if target is None:
                continue
            entry = record[part]
            if entry is None:
                parser.error(f'Không có {part} cho request này')
            event, directory = entry
            if name in event:
                raw = restore_payload(directory, event[name])
            else:
                value = event.get('raw_request_json' if part == 'request' else 'raw_response_text')
                raw = (json.dumps(value, ensure_ascii=False, separators=(',', ':')) if part == 'request'
                       else str(value)).encode('utf-8')
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(raw)
            print(f'Đã xuất {len(raw)} byte -> {target}')
        return 0

    if args.full:
        for request_id, record in selected:
            req = record['request']
            outcome = record['outcome']
            result = {'request_id': request_id,
                      'request': _full_event(req[0], req[1], 'request') if req else None,
                      'outcome': _full_event(outcome[0], outcome[1], 'response') if outcome and outcome[0].get('event') == 'response'
                      else outcome[0] if outcome else None}
            print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0

    print(f'Số HTTP attempts: {len(selected)}')
    print('TIME (UTC)             ORIGIN        ROLE          COGNITIVE LANE             HTTP    MS      IN_BYTES   OUT_BYTES ID')
    for request_id, record in selected[:max(0, args.limit)]:
        req = (record['request'] or ({}, None))[0]
        out = (record['outcome'] or ({}, None))[0]
        incoming = (req.get('raw_request_ref') or {}).get('byte_length')
        outgoing = (out.get('raw_response_ref') or {}).get('byte_length')
        if incoming is None:
            incoming = len(json.dumps(req.get('raw_request_json'), ensure_ascii=False)) if 'raw_request_json' in req else 0
        if outgoing is None:
            outgoing = len(out.get('raw_response_text', ''))
        status = str(out.get('http_status') or out.get('exception_type') or 'PENDING')
        elapsed = out.get('elapsed_ms')
        elapsed_str = f'{elapsed:.0f}' if isinstance(elapsed, (int, float)) else '-'
        print(f"{str(req.get('at_utc', ''))[:20]:20} {str(req.get('origin','eris_client'))[:13]:13} "
              f"{str(req.get('worker_role','?'))[:13]:13} {str(req.get('cognitive_lane') or req.get('lane') or '?')[:26]:26} "
              f'{status[:7]:7} {elapsed_str:>7} {incoming:>11} {outgoing:>11} {request_id[:12]}')
    if selected:
        print('\nDùng --id ... --full / --export-request / --export-response để xem nội dung gốc.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
