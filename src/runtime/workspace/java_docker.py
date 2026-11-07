"""Compile/run learner Java in Docker only. No host Java fallback."""
from __future__ import annotations

from pathlib import Path
import re
import shutil
import subprocess
import threading
import time
import uuid

JDK_IMAGE = "eclipse-temurin:21.0.8_9-jdk-jammy"
MAX_CODE_BYTES = 12000
MAX_OUTPUT_BYTES = 16000  # per stream, bounded while reading, not afterwards


class DockerJavaWorkspace:
    def __init__(self, workspace: str | Path, *, image: str = JDK_IMAGE):
        self.workspace = Path(workspace).resolve()
        self.image = image
        if not re.fullmatch(r"[A-Za-z0-9_.:/@-]{1,240}", image):
            raise ValueError("invalid_java_image")

    @staticmethod
    def available() -> bool:
        return bool(shutil.which("docker"))

    def readiness(self) -> dict:
        guidance = f"Khởi động Docker Desktop rồi tải image: docker pull {self.image}"
        if not self.available():
            return {"available": False, "reason": "docker_not_installed", "guidance": guidance}
        for command, reason in ((["docker", "info", "--format", "{{.ServerVersion}}"], "docker_not_running"),
                                (["docker", "image", "inspect", self.image, "--format", "{{.Id}}"], "jdk_image_missing")):
            try:
                result = subprocess.run(command, capture_output=True, timeout=3, shell=False, check=False)
                if result.returncode:
                    return {"available": False, "reason": reason, "guidance": guidance}
            except (OSError, subprocess.TimeoutExpired):
                return {"available": False, "reason": reason, "guidance": guidance}
        return {"available": True, "image": self.image}

    def command_for(self, folder: Path, *, container_name: str, class_name="OrderService", timeout=15.0) -> list[str]:
        if not re.fullmatch(r"eris-java-[a-f0-9]{32}", container_name):
            raise ValueError("invalid_java_container_name")
        if not re.fullmatch(r"[A-Za-z_$][A-Za-z0-9_$]{0,79}", class_name) or class_name == "Harness":
            raise ValueError("invalid_java_class_name")
        # Bytecode in /tmp is read by the VM, not executed as an OS binary.
        script = ("mkdir -p /tmp/classes && "
                  "javac -J-Xmx128m -J-XX:ActiveProcessorCount=1 -J-XX:+UseSerialGC -encoding UTF-8 "
                  f"-d /tmp/classes '/code/{class_name}.java' /code/Harness.java || exit 101; "
                  "printf '__ERIS_COMPILED__\\n'; "
                  "exec java -Xmx128m -XX:MaxMetaspaceSize=96m -XX:ActiveProcessorCount=1 -XX:+UseSerialGC "
                  "-Djava.io.tmpdir=/tmp -cp /tmp/classes Harness")
        return ["docker", "run", "--rm", "--pull", "never", "--name", container_name,
                "--network", "none", "--read-only", "--cap-drop", "ALL",
                "--security-opt", "no-new-privileges", "--pids-limit", "64",
                "--memory", "512m", "--memory-swap", "512m", "--cpus", "1",
                "--user", "65534:65534", "--log-driver", "none",
                "--ulimit", "fsize=16777216:16777216", "--ulimit", "nofile=128:128",
                "--tmpfs", "/tmp:rw,noexec,nosuid,nodev,size=64m",
                "--mount", f"type=bind,source={folder / 'code'},target=/code,readonly",
                # In-container watchdog also survives a lost host/CLI process.
                "--workdir", "/tmp", self.image, "timeout", "-k", "1s",
                f"{min(30., max(.2, float(timeout))):g}s", "sh", "-c", script]

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

    def execute(self, code: str, exercise: dict, *, timeout=15.0) -> dict:
        if not isinstance(code, str) or not code.strip() or len(code.encode("utf-8")) > MAX_CODE_BYTES:
            raise ValueError("invalid_java_code_size")
        container_name = "eris-java-" + uuid.uuid4().hex
        folder = self.workspace / "jobs" / container_name
        bound = min(30., max(.2, float(timeout)))
        command = self.command_for(folder, container_name=container_name, class_name=exercise["class_name"], timeout=bound)
        ready = self.readiness()
        if not ready["available"]:
            return {"status": "unavailable", **ready}
        source = folder / "code"
        source.mkdir(parents=True, exist_ok=False)
        (source / (exercise["class_name"] + ".java")).write_text(code, encoding="utf-8")
        (source / "Harness.java").write_text(exercise["harness"], encoding="utf-8")
        start = time.monotonic()
        try:
            exit_code, output, errors, failure = self._bounded_run(command, timeout=bound + 2)
        except (OSError, subprocess.SubprocessError):
            return {"status": "unavailable", "reason": "docker_execution_failed",
                    "guidance": "Kiểm tra Docker Desktop và image JDK; không chạy Java trên host."}
        finally:
            # Killing the CLI alone leaves an unbounded container behind. This
            # name is generated here, never supplied by the learner or model.
            try:
                subprocess.run(["docker", "rm", "-f", container_name], capture_output=True, timeout=3, shell=False)
            except (OSError, subprocess.TimeoutExpired):
                pass
        if exit_code in {125, 126, 127} and "__ERIS_COMPILED__" not in output:
            return {"status": "unavailable", "reason": "docker_container_failed",
                    "guidance": "Kiểm tra Docker Desktop/image JDK và quyền mount thư mục bài tập."}
        if exit_code in {124, 137} and not failure:
            failure = "timeout" if exit_code == 124 else "resource_limit"
        tests = []
        for test in exercise["tests"]:
            matches = re.findall(r"^" + re.escape(test["id"]) + r":(PASS|FAIL)\r?$", output, re.MULTILINE)
            tests.append({**test, "passed": matches == ["PASS"] and not failure})
        passed = sum(test["passed"] for test in tests)
        verdict = "correct" if passed == len(tests) and exit_code == 0 else "partial" if passed else "incorrect"
        reason = failure or ("compile_error" if exit_code == 101 and "__ERIS_COMPILED__" not in output
                             else "runtime_error" if exit_code else "graded")
        # Do not return compiler source excerpts of the hidden harness.
        diagnostic = (errors if reason == "compile_error" else "")[:2000]
        if "Harness.java" in errors:
            diagnostic = "Kiểm tra signature constructor, checkout(int amount) và interface PaymentGateway trong đề bài."
        return {"status": "completed", "result": verdict, "tests": tests, "reason": reason,
                "diagnostic": diagnostic, "duration_ms": round((time.monotonic() - start) * 1000),
                "image": self.image, "exit_code": exit_code}
