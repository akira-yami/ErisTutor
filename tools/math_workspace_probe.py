# -*- coding: utf-8 -*-
"""Probe không cần LLM: chạy toán và dựng artifact thật; thêm --send để gửi Godot."""
import argparse
import json
import os
from pathlib import Path
import sys

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root / 'src'))
from runtime.workspace import MathWorkspaceService, send_artifacts_udp

parser = argparse.ArgumentParser()
parser.add_argument('--send', action='store_true', help='Gửi artifact tới Godot đang chạy')
parser.add_argument('--workspace', default=str(root / 'logs' / 'eris_artifacts'))
args = parser.parse_args()
service = MathWorkspaceService(args.workspace)
for request in (
    {'kind':'matrix','rows':[[1,2],[3,4]],'title':'Ma trận 2×2'},
    {'kind':'differentiate','expression':'sin(x) * x^2','title':'Đạo hàm'},
    {'kind':'plot','expression':'x^2-2','xmin':-5,'xmax':5,'title':'Đồ thị y = x² - 2'},
):
    data = service.execute_sync(request)
    print(json.dumps({'job_id':data['job_id'],'title':data['title'],
                      'artifacts':[art['type'] for art in data['artifacts']]}, ensure_ascii=False))
    if args.send: send_artifacts_udp(data)
