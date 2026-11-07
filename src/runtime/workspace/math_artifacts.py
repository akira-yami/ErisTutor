# -*- coding: utf-8 -*-
"""Cầu nối tool toán học -> SandboxWorld -> artifact JSON -> Godot.

Có thể được gọi bởi ActionExecutor/Root sau khi đã chọn tool, nhưng lớp này
không quyết định nói hay thay thế Identity. Tác vụ dài chạy trong worker riêng.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
from pathlib import Path
import sys
import time
import uuid
from typing import Any, Mapping

from runtime.world import CapabilityAuthority, CapabilityPolicy, CommandPolicy, SandboxWorld
from runtime.world.contracts import SandboxCommand, SandboxCommandKind


class MathWorkspaceService:
    """Thực hiện toán thật bằng worker chỉ nhận JSON có cấu trúc."""
    def __init__(self, workspace: str | Path, *, timeout_seconds: int = 35, max_memory_mb: int = 2048):
        self.workspace = Path(workspace).resolve()
        self.workspace.mkdir(parents=True, exist_ok=True)
        self.worker = Path(__file__).with_name('math_worker.py').resolve()
        self.authority = CapabilityAuthority(secret=os.urandom(32), issuer='eris-math-workspace', audience='sandbox')
        policy = CommandPolicy(
            executable_allowlist=frozenset({Path(sys.executable).name}),
            workspace_root=str(self.workspace),
            allow_executable_paths=True,
            max_runtime_seconds=timeout_seconds,
            max_memory_mb=max_memory_mb,
            network_enabled=False,
            environment_allowlist=frozenset({'SYSTEMROOT','WINDIR','PATH','TEMP','TMP','HOME','USERPROFILE','MPLCONFIGDIR'}),
        )
        self.world = SandboxWorld(policy=CapabilityPolicy(policy, token_verifier=self.authority.verify))

    @staticmethod
    def _validate_request(request: Mapping[str, Any]) -> dict:
        data = dict(request)
        if data.get('kind') not in {'matrix','simplify','differentiate','integrate','solve','plot','binary_determinant_max'}:
            raise ValueError('Loại tác vụ toán chưa hỗ trợ')
        serialized = json.dumps(data, ensure_ascii=False, allow_nan=False)
        if len(serialized.encode('utf-8')) > 12_000:
            raise ValueError('Đề bài vượt giới hạn 12 KB')
        return data

    async def execute(self, request: Mapping[str, Any], *, job_id: str | None = None) -> dict:
        data = self._validate_request(request)
        job = job_id or uuid.uuid4().hex
        if not job.isascii() or not (1 <= len(job) <= 64) or not all(c.isalnum() or c in '-_' for c in job):
            raise ValueError('job_id không hợp lệ')
        folder = self.workspace / 'jobs' / job
        folder.mkdir(parents=True, exist_ok=True)
        output = folder / 'out'
        output.mkdir(exist_ok=True)
        input_path = folder / 'request.json'
        request_bytes = json.dumps(data, ensure_ascii=False, sort_keys=True).encode('utf-8')
        if input_path.exists() and input_path.read_bytes() != request_bytes:
            raise ValueError('job_id đã được dùng cho một yêu cầu khác')
        if not input_path.exists():
            input_path.write_bytes(request_bytes)
        result_path = output / 'result.json'
        if not result_path.exists():
            hash_key = hashlib.sha256(request_bytes).hexdigest()[:18]
            cmd = SandboxCommand(
                command_id=f'cmd:math:{job}', action_id=f'action:math:{job}',
                decision_id=f'decision:math:{job}',
                kind=SandboxCommandKind.RUN_COMMAND,
                arguments={'argv':[sys.executable,'-I',str(self.worker),str(input_path),str(output)]},
                requested_capability='run_command', idempotency_key=f'math:{job}:{hash_key}',
                created_at=time.time(), subject_id='eris', simulate=False,
            )
            token = self.authority.issue(
                subject_id='eris', capabilities=frozenset({'run_command'}), policy_version='v1',
                scope={'command_ids':[cmd.command_id], 'action_ids':[cmd.action_id],
                       'decision_ids':[cmd.decision_id], 'idempotency_keys':[cmd.idempotency_key]},
                ttl_seconds=120,
            )
            outcome = await self.world.apply(cmd, token)
            if str(getattr(getattr(outcome, 'status', ''), 'value', getattr(outcome, 'status', ''))).lower() != 'succeeded':
                raise RuntimeError('Tính toán chưa thành công: '+str(getattr(outcome,'denial_reason', '') or 'unknown'))
        if not result_path.is_file():
            raise RuntimeError('Worker không trả result.json')
        if result_path.stat().st_size > 60_000:
            raise RuntimeError('Artifact JSON vượt giới hạn')
        result = json.loads(result_path.read_text(encoding='utf-8'))
        # Kiểm tra mọi ảnh thuộc thư mục job, hash khớp bản gốc.
        for item in result.get('artifacts', []):
            if item.get('type') != 'image':
                continue
            filename = str(item.get('path',''))
            if filename not in {'plot.png','matrix.png','formula.png','formula_1.png','formula_2.png'}:
                raise ValueError('Tên asset chưa được cấp phép')
            asset = (output / filename).resolve()
            if not asset.is_file() or asset.stat().st_size > 3_000_000:
                raise ValueError('Asset thiếu hoặc vượt giới hạn')
            if hashlib.sha256(asset.read_bytes()).hexdigest() != item.get('sha256'):
                raise ValueError('Artifact đã thay đổi')
            # Chỉ gửi relative path để Godot dựng dưới cùng workspace cố định.
            item['path'] = f'jobs/{job}/out/{filename}'
        return {'parse_mode':'workspace_artifact', 'workspace_version':1,
                'job_id':job, 'title':str(result.get('title') or 'Toán học')[:140],
                'artifacts':result.get('artifacts', [])}

    def execute_sync(self, request: Mapping[str, Any], *, job_id: str | None = None) -> dict:
        """Tiện dùng CLI hoặc tool worker; KHÔNG gọi đồng bộ trên UI/main loop."""
        return asyncio.run(self.execute(request, job_id=job_id))


def send_artifacts_udp(payload: Mapping[str, Any], *, host: str = '127.0.0.1',
                       port: int = 4242, timeout: float = .35, retries: int = 2) -> bool:
    """Gửi artifact và đợi ACK hiển thị từ Godot; không nhập nhằng với tool success.

    Caller vẫn giữ artifact trên đĩa khi panel chưa mở hoặc Godot không chạy.
    """
    import socket
    packet = json.dumps(dict(payload), ensure_ascii=False).encode('utf-8')
    if len(packet) >= 48_000:
        raise ValueError('Gói UDP quá lớn; hãy chia trang artifact')
    job_id = str(payload.get('job_id',''))
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.settimeout(timeout)
        for _ in range(max(1,min(int(retries),4))):
            sock.sendto(packet, (host, port))
            try:
                ack,_=sock.recvfrom(2048)
                parsed=json.loads(ack)
                if (parsed.get('parse_mode') == 'workspace_artifact_ack' and
                        parsed.get('job_id') == job_id):
                    return parsed.get('status') == 'rendered'
            except (socket.timeout, ValueError, OSError):
                continue
    return False
