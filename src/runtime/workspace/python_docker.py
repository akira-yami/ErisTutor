# -*- coding: utf-8 -*-
"""Thực thi mã Python do Eris viết trong Docker thay vì host Python.

Khác với math_worker, mã ở đây là mã bất kỳ. Vì vậy tuyệt đối KHÔNG fallback
sang subprocess Python của máy thật khi Docker chưa được cài / chưa khởi động.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import time
import uuid


class DockerPythonWorkspace:
    """Một lần thực thi trong container mất mạng, không có capability root."""

    def __init__(self, workspace: str | Path, *, image: str = 'python:3.12-slim'):
        self.workspace = Path(workspace).resolve()
        self.workspace.mkdir(parents=True, exist_ok=True)
        self.image = str(image)
        if not self.image or len(self.image) > 180 or any(c in self.image for c in ' \t\n\r\0'):
            raise ValueError('Tên Docker image không hợp lệ')

    @staticmethod
    def available() -> bool:
        return bool(shutil.which('docker'))

    def command_for(self, folder: Path) -> list[str]:
        """Tách riêng để có thể test policy mà không có Docker thật."""
        code_folder = folder / 'code'
        output_folder = folder / 'out'
        return [
            'docker','run','--rm','--network','none',
            '--read-only','--cap-drop','ALL', '--security-opt','no-new-privileges',
            '--pids-limit','64','--memory','512m','--cpus','1',
            '--user','65534:65534',
            '--tmpfs','/tmp:rw,noexec,nosuid,size=64m',
            '--mount',f'type=bind,source={code_folder},target=/code,readonly',
            '--mount',f'type=bind,source={output_folder},target=/output',
            '--workdir','/output',self.image,'python','-B','/code/main.py'
        ]

    def execute(self, code: str, *, job_id: str | None = None, timeout: float = 25.0) -> dict:
        if not self.available():
            raise RuntimeError('Docker chưa có; không chạy mã tùy ý trên Python host')
        if not isinstance(code, str) or not code.strip() or len(code.encode('utf-8')) > 12_000:
            raise ValueError('Code Python không hợp lệ / quá 12 KB')
        job = job_id or uuid.uuid4().hex
        if not job.isascii() or not 1 <= len(job) <= 64 or not all(c.isalnum() or c in '-_' for c in job):
            raise ValueError('job_id không hợp lệ')
        folder = self.workspace / 'jobs' / job
        code_dir = folder / 'code'
        output = folder / 'out'
        code_dir.mkdir(parents=True, exist_ok=True)
        output.mkdir(parents=True, exist_ok=True)
        # Container dùng UID không đặc quyền; chỉ thư mục job này được mở ghi.
        try:
            output.chmod(0o777)
        except OSError:
            pass
        file = code_dir / 'main.py'
        if file.exists() and file.read_text(encoding='utf-8') != code:
            raise ValueError('job_id bị dùng cho code khác')
        if not file.exists():
            file.write_text(code, encoding='utf-8')
        # Không chạy lại job đã có kết quả, kể cả khi người dùng reload/restart.
        receipt = output / 'stdout.txt'
        if not receipt.exists():
            start = time.monotonic()
            run = subprocess.run(self.command_for(folder), capture_output=True,
                                 timeout=min(max(1., timeout), 120.), shell=False, check=False)
            if len(run.stdout) > 32_000 or len(run.stderr) > 32_000:
                raise RuntimeError('Đầu ra Python vượt giới hạn')
            text = run.stdout.decode('utf-8', errors='replace')
            error = run.stderr.decode('utf-8', errors='replace')
            receipt.write_text(f'exit_code={run.returncode}\n'+text+('\nSTDERR:\n'+error if error else ''),encoding='utf-8')
        if not receipt.read_text(encoding='utf-8').startswith('exit_code=0\n'):
            raise RuntimeError('Docker Python thất bại: '+receipt.read_text(encoding='utf-8')[:500])
        artifacts = [
            {'type':'code','language':'python','text':code},
            {'type':'text','text':receipt.read_text(encoding='utf-8')[:12_000]},
        ]
        for path in sorted(output.iterdir()):
            if path.is_symlink() or not path.is_file() or path.suffix.lower() != '.png':
                continue
            if path.stat().st_size > 3_000_000:
                continue
            data = path.read_bytes()
            if not data.startswith(b'\x89PNG'):
                continue
            artifacts.append({'type':'image','path':f'jobs/{job}/out/{path.name}',
                              'sha256':hashlib.sha256(data).hexdigest(), 'alt':'Ảnh do container Python tạo'})
            if len(artifacts)>=14:
                break
        return {'parse_mode':'workspace_artifact','workspace_version':1,
                'job_id':job, 'title':'Python sandbox · '+job[:8], 'artifacts':artifacts}
