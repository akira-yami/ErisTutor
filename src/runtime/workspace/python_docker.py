# -*- coding: utf-8 -*-
"""Thực thi mã Python do Eris viết trong Docker thay vì host Python.

Khác với math_worker, mã ở đây là mã bất kỳ. Vì vậy tuyệt đối KHÔNG fallback
sang subprocess Python của máy thật khi Docker chưa được cài / chưa khởi động.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import re
import shutil
import subprocess
import threading
import time
import uuid

PYTHON_IMAGE = 'python:3.12-slim'
MAX_CODE_BYTES = 12000
MAX_OUTPUT_BYTES = 16000



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
        if not file.exists(): file.write_text(code, encoding='utf-8')
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
            if path.stat().st_size > 3_000_000: continue
            data = path.read_bytes()
            if not data.startswith(b'\x89PNG'): continue
            artifacts.append({'type':'image','path':f'jobs/{job}/out/{path.name}',
                              'sha256':hashlib.sha256(data).hexdigest(), 'alt':'Ảnh do container Python tạo'})
            if len(artifacts)>=14: break
        return {'parse_mode':'workspace_artifact','workspace_version':1,
                'job_id':job, 'title':'Python sandbox · '+job[:8], 'artifacts':artifacts}


class DockerPythonExerciseWorkspace:
    """Thực thi và chấm bài tập Python của người học trong Docker cô lập."""

    def __init__(self, workspace: str | Path, *, image: str = PYTHON_IMAGE):
        self.workspace = Path(workspace).resolve()
        self.image = image
        if not re.fullmatch(r"[A-Za-z0-9_.:/@-]{1,240}", image):
            raise ValueError("invalid_python_image")

    @staticmethod
    def available() -> bool:
        return bool(shutil.which("docker"))

    def readiness(self) -> dict:
        guidance = f"Khởi động Docker Desktop rồi tải image: docker pull {self.image}"
        if not self.available():
            return {"available": False, "reason": "docker_not_installed", "guidance": guidance}
        for command, reason in (
            (["docker", "info", "--format", "{{.ServerVersion}}"], "docker_not_running"),
            (["docker", "image", "inspect", self.image, "--format", "{{.Id}}"], "python_image_missing"),
        ):
            try:
                result = subprocess.run(command, capture_output=True, timeout=3, shell=False, check=False)
                if result.returncode:
                    return {"available": False, "reason": reason, "guidance": guidance}
            except (OSError, subprocess.TimeoutExpired):
                return {"available": False, "reason": reason, "guidance": guidance}
        return {"available": True, "image": self.image}

    def command_for(self, folder: Path, *, container_name: str, timeout: float = 15.0) -> list[str]:
        if not re.fullmatch(r"eris-py-[a-f0-9]{32}", container_name):
            raise ValueError("invalid_python_container_name")
        script = (
            "python -B -u /code/harness.py || exit 101"
        )
        return [
            "docker", "run", "--rm", "--pull", "never", "--name", container_name,
            "--network", "none", "--read-only", "--cap-drop", "ALL",
            "--security-opt", "no-new-privileges", "--pids-limit", "64",
            "--memory", "512m", "--memory-swap", "512m", "--cpus", "1",
            "--user", "65534:65534", "--log-driver", "none",
            "--ulimit", "fsize=16777216:16777216", "--ulimit", "nofile=128:128",
            "--tmpfs", "/tmp:rw,noexec,nosuid,nodev,size=64m",
            "--mount", f"type=bind,source={folder / 'code'},target=/code,readonly",
            "--workdir", "/tmp", self.image, "timeout", "-k", "1s",
            f"{min(30., max(.2, float(timeout))):g}s", "sh", "-c", script,
        ]

    @staticmethod
    def _bounded_run(command, *, timeout):
        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, shell=False)
        buffers = [bytearray(), bytearray()]
        overflow = threading.Event()

        def read(stream, buffer):
            while chunk := stream.read(1024):
                room = MAX_OUTPUT_BYTES - len(buffer)
                buffer.extend(chunk[:max(0, room)])
                if len(chunk) > room:
                    overflow.set()

        readers = [threading.Thread(target=read, args=(stream, buffer), daemon=True)
                   for stream, buffer in zip((process.stdout, process.stderr), buffers, strict=True)]
        for reader in readers:
            reader.start()
        deadline = time.monotonic() + timeout
        failure = ""
        while process.poll() is None:
            if overflow.is_set() or time.monotonic() >= deadline:
                failure = "output_limit" if overflow.is_set() else "timeout"
                process.kill()
                break
            time.sleep(0.01)
        process.wait(timeout=3)
        for reader in readers:
            reader.join(timeout=0.5)
        if overflow.is_set():
            failure = "output_limit"
        for stream in (process.stdout, process.stderr):
            stream.close()
        return process.returncode, *(bytes(b).decode("utf-8", errors="replace") for b in buffers), failure

    def execute(self, code: str, exercise: dict, *, timeout: float = 15.0) -> dict:
        if not isinstance(code, str) or not code.strip() or len(code.encode("utf-8")) > MAX_CODE_BYTES:
            raise ValueError("invalid_python_code_size")
        container_name = "eris-py-" + uuid.uuid4().hex
        folder = self.workspace / "jobs" / container_name
        bound = min(30., max(.2, float(timeout)))
        command = self.command_for(folder, container_name=container_name, timeout=bound)
        ready = self.readiness()
        if not ready["available"]:
            return {"status": "unavailable", **ready}
        source = folder / "code"
        source.mkdir(parents=True, exist_ok=False)
        (source / "solution.py").write_text(code, encoding="utf-8")
        harness_code = exercise.get("harness") or (
            "import sys\nimport solution\n"
        )
        (source / "harness.py").write_text(harness_code, encoding="utf-8")
        start = time.monotonic()
        try:
            exit_code, output, errors, failure = self._bounded_run(command, timeout=bound + 2)
        except (OSError, subprocess.SubprocessError):
            return {
                "status": "unavailable", "reason": "docker_execution_failed",
                "guidance": "Kiểm tra Docker Desktop và image Python; không chạy Python tùy ý trên host.",
            }
        finally:
            try:
                subprocess.run(["docker", "rm", "-f", container_name], capture_output=True, timeout=3, shell=False)
            except (OSError, subprocess.TimeoutExpired):
                pass
        if exit_code in {125, 126, 127} and not output:
            return {
                "status": "unavailable", "reason": "docker_container_failed",
                "guidance": "Kiểm tra Docker Desktop/image Python và quyền mount thư mục bài tập.",
            }
        if exit_code in {124, 137} and not failure:
            failure = "timeout" if exit_code == 124 else "resource_limit"
        tests = []
        for test in exercise.get("tests", []):
            matches = re.findall(r"^" + re.escape(test["id"]) + r":(PASS|FAIL)\r?$", output, re.MULTILINE)
            tests.append({**test, "passed": matches == ["PASS"] and not failure})
        passed = sum(test["passed"] for test in tests) if tests else (1 if exit_code == 0 else 0)
        verdict = "correct" if (passed == len(tests) and exit_code == 0) else "partial" if passed else "incorrect"
        reason = failure or ("runtime_error" if exit_code else "graded")
        diagnostic = (errors or output)[:2000]
        return {
            "status": "completed", "result": verdict, "tests": tests, "reason": reason,
            "diagnostic": diagnostic, "duration_ms": round((time.monotonic() - start) * 1000),
            "image": self.image, "exit_code": exit_code,
        }

