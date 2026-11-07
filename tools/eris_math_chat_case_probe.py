# -*- coding: utf-8 -*-
"""Tái hiện đúng bài toán trong ảnh chat và tạo bài giải có cấu trúc.

Mặc định chỉ chạy tool tạo artifact trên đĩa. Dùng --send khi Godot V4.1
đã chạy và cùng dùng thư mục ERIS_ARTIFACT_WORKSPACE.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))

from runtime.workspace import MathWorkspaceService, send_artifacts_udp


def main() -> None:
    parser = argparse.ArgumentParser(description='Kiểm tra bài toán ma trận 0/1 cấp 3 qua Workspace.')
    parser.add_argument('--send', action='store_true', help='Gửi artifact đến Godot qua UDP 4242')
    args = parser.parse_args()
    workspace = Path(os.getenv('ERIS_ARTIFACT_WORKSPACE') or str(ROOT / 'logs' / 'eris_artifacts'))
    result = MathWorkspaceService(workspace).execute_sync({
        'kind': 'binary_determinant_max', 'size': 3,
        'title': 'Bài toán: định thức lớn nhất của ma trận 0–1 cấp 3',
    })
    print(json.dumps({
        'job_id': result['job_id'],
        'title': result['title'],
        'steps': [a['text'] for a in result['artifacts'] if a['type'] == 'section'],
        'images': [a['path'] for a in result['artifacts'] if a['type'] == 'image'],
    }, ensure_ascii=False, indent=2))
    if args.send:
        send_artifacts_udp(result)
        print('Đã gửi gói workspace_artifact qua UDP 4242.')


if __name__ == '__main__':
    main()
