# -*- coding: utf-8 -*-
"""Chạy thử V6: public text → cập nhật từng block → sửa công thức → ACK.

Ví dụ:
  $env:PYTHONPATH="src;."
  py tools/v6_live_workspace_probe.py
  py tools/v6_live_workspace_probe.py --send

Không dùng LLM giả làm live: --send yêu cầu Godot đang chạy ở UDP 4242.
"""
from __future__ import annotations
import argparse
import json
import os
import socket
import time
import uuid

from runtime.workspace.live_document import (
    LiveWorkspaceService, inspect_live_document, writer_edit_live_document,
)

SAMPLE = r'''# Miền xác định của hàm số

Điều kiện xác định của $\ln(1-2\cos x)$ là biểu thức trong logarit dương.

## Bước 1 — Đặt điều kiện

$$1-2\cos x>0$$

## Bước 2 — Biến đổi

$$\cos x<\frac12$$

## Kết luận

Hai đầu mút không được lấy vì biểu thức trong logarit bằng 0.

$$D=\bigcup_{k\in\mathbb Z}\left(\frac{\pi}3+2k\pi,\frac{5\pi}3+2k\pi\right)$$'''


def main():
    cli = argparse.ArgumentParser(description='V6 — thử live patch trong Panel')
    cli.add_argument('--send', action='store_true', help='Gửi thực đến Godot đang chạy')
    cli.add_argument('--port', type=int, default=4242)
    cli.add_argument('--no-writer-correction', action='store_true')
    args = cli.parse_args()
    turn_id = 'v6-probe-' + uuid.uuid4().hex[:10]
    ops = []

    def capture(packet, **kwargs):
        ops.append((packet['revision'], packet['operation'], packet.get('block_id','')))
        print('PATCH', packet['revision'], packet['operation'], packet.get('block_id',''))
        return True

    if args.send:
        # Tạo một turn cùng text/hash để Godot gắn tài liệu vào đúng message.
        public_packet={'text':SAMPLE, 'should_speak':False, 'stage':{'turn_id':turn_id}}
        with socket.socket(socket.AF_INET,socket.SOCK_DGRAM) as sock:
            sock.sendto(json.dumps(public_packet, ensure_ascii=False).encode('utf-8'),('127.0.0.1',args.port))
        time.sleep(.2)
        from runtime.workspace.live_document import send_live_operation
        sender=send_live_operation
    else:
        sender=capture

    service=LiveWorkspaceService(sender=sender, workspace=os.getenv('ERIS_ARTIFACT_WORKSPACE','logs/eris_artifacts'))
    doc=service.open(text=SAMPLE,session_id='v6-probe',generation=1,run_id='probe',
                     delivery_id=turn_id,turn_id=turn_id,port=args.port)
    if doc is None:
        print('Chưa có ACK từ Godot; chưa thể xác nhận UI hoạt động.'); return 2
    if not service.present(doc,port=args.port):
        print('Gửi tài liệu thất bại hoặc bị preempt.'); return 3
    state=inspect_live_document(doc.document_id)
    print('DOCUMENT',doc.document_id,'REVISION',doc.revision,'BLOCKS',len(doc.order))
    # Cho Writer sửa một ký hiệu; thao tác này có revision và hiện ngay trên Panel.
    if not args.no_writer_correction:
        heading_id=next((k for k in doc.order if doc.blocks[k]['type']=='heading'), '')
        if heading_id:
            result=writer_edit_live_document(doc.document_id,'replace_block',block_id=heading_id,
                                             block={'type':'heading','level':1,'text':'Miền xác định — hai đầu mút không lấy'},
                                             base_revision=doc.revision)
            print('WRITER EDIT',result)
    print('KẾT QUẢ:', 'đã gửi các patch đến Godot' if args.send else 'đã mô phỏng patch cục bộ')
    return 0


if __name__=='__main__':
    raise SystemExit(main())
