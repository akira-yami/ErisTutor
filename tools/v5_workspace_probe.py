# -*- coding: utf-8 -*-
"""Kiểm tra đầu ra V5 bằng lời giải thật, không thay Root/Identity.

Ví dụ:
  PYTHONPATH=src:. python tools/v5_workspace_probe.py
  PYTHONPATH=src:. python tools/v5_workspace_probe.py --send

--send chỉ là đầu vào UI giả lập nhằm kiểm tra Godot, không phải live LLM.
"""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import socket
import time
import uuid

from runtime.workspace.structured_output import prepare_document, send_document_acknowledged

MATH_EXAMPLE = r"""# Bài toán ma trận 0–1 cấp 3

**Kết luận:** Giá trị lớn nhất của định thức là **2**.

$$\max_{A\in\{0,1\}^{3\times3}}\det(A)=2$$

## Bước 1. Xét một ma trận cụ thể

Chọn ma trận sau để chứng minh cận 2 đạt được:

$$\begin{pmatrix}1&1&0\\1&0&1\\0&1&1\end{pmatrix}$$

## Bước 2. Tính định thức

Tính theo hàng đầu tiên: định thức bằng -2. Đổi hai hàng để nhận định thức 2.

## Bước 3. Kiểm tra bằng Python (nếu cần)

```python
import sympy as sp
A = sp.Matrix([[1,1,0],[1,0,1],[0,1,1]])
assert A.det() == -2
print(A.det())
```

| Cách kiểm tra | Kết quả |
| --- | --- |
| Đại số | -2 |
| Ma trận đổi hàng | 2 |

Bài giải chi tiết chỉ nằm trong Panel; Bubble vẫn là lời thoại của Eris.
"""


def main():
    parser=argparse.ArgumentParser(description='Kiểm chứng Output V5 / Godot')
    parser.add_argument('--send',action='store_true',help='Gửi packet chẩn đoán đến Godot UDP 4242')
    parser.add_argument('--port',type=int,default=4242)
    parser.add_argument('--workspace',default=os.getenv('ERIS_ARTIFACT_WORKSPACE','logs/eris_artifacts'))
    args=parser.parse_args()
    turn='probe-v5-'+uuid.uuid4().hex[:12]
    doc=prepare_document(text=MATH_EXAMPLE,session_id='probe',generation=1,
                         run_id='probe',delivery_id=turn,turn_id=turn,workspace=args.workspace)
    print('V5 DOCUMENT:', json.dumps({'document_id':doc['document_id'],
                                      'turn_id':turn,'blocks':len(doc['blocks']),
                                      'formulas':sum(b['type']=='formula' for b in doc['blocks']),
                                      'images':sum(bool(b.get('path')) for b in doc['blocks']),
                                      'workspace':str(Path(args.workspace).resolve())},ensure_ascii=False,indent=2))
    if args.send:
        # Chỉ để test UI: tạo một public text packet với stage turn_id giống doc.
        # Trong ứng dụng thật, packet này phải do public_output_runtime gửi khi
        # audio_start đã xảy ra; probe không giả làm kết quả của LLM live.
        packet={'text':MATH_EXAMPLE,'should_speak':False,'stage':{'turn_id':turn},'parse_mode':''}
        with socket.socket(socket.AF_INET,socket.SOCK_DGRAM) as sock:
            sock.sendto(json.dumps(packet,ensure_ascii=False).encode('utf-8'),('127.0.0.1',args.port))
        time.sleep(.2)
        acknowledged=send_document_acknowledged(doc,port=args.port,retries=3)
        print('GODOT ACK:', 'Đã xác nhận render' if acknowledged else 'Chưa có xác nhận Godot')


if __name__=='__main__':
    main()
