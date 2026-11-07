from __future__ import annotations

import subprocess
import sys

import pytest

from runtime.learning_canvas.pack import load_pack
from runtime.workspace.java_docker import DockerJavaWorkspace, JDK_IMAGE, MAX_OUTPUT_BYTES


def exercise():
    return load_pack().node("javadi:v1:constructor")["exercises"][1]


def test_java_policy_is_isolated_and_only_mounts_read_only_source(tmp_path):
    java = DockerJavaWorkspace(tmp_path)
    command = java.command_for(tmp_path / "job", container_name="eris-java-" + "a" * 32)
    text = " ".join(command)
    for flag in ("--network none", "--read-only", "--cap-drop ALL", "--security-opt no-new-privileges",
                 "--user 65534:65534", "--pids-limit 64", "--memory 512m", "--cpus 1",
                 "--pull never", "--log-driver none", "/tmp:rw,noexec,nosuid,nodev,size=64m"):
        assert flag in text
    assert command.count("--mount") == 1
    assert "target=/code,readonly" in text
    assert JDK_IMAGE in command and "javac" in command[-1] and "java -Xmx128m" in command[-1]
    assert command[command.index(JDK_IMAGE) + 1:command.index(JDK_IMAGE) + 5] == ["timeout", "-k", "1s", "15s"]


@pytest.mark.parametrize("class_name", ["../Escape", "Harness", "X; touch /host", ""])
def test_java_class_cannot_escape_source_path(tmp_path, class_name):
    with pytest.raises(ValueError, match="invalid_java_class_name"):
        DockerJavaWorkspace(tmp_path).command_for(tmp_path, container_name="eris-java-" + "a" * 32, class_name=class_name)


def test_no_docker_never_launches_host_java(tmp_path, monkeypatch):
    java = DockerJavaWorkspace(tmp_path)
    monkeypatch.setattr(java, "available", lambda: False)
    monkeypatch.setattr(subprocess, "Popen", lambda *_a, **_k: pytest.fail("No subprocess allowed without Docker"))
    result = java.execute("class OrderService {}", exercise())
    assert result["status"] == "unavailable" and result["reason"] == "docker_not_installed"


@pytest.mark.parametrize("stdout,exit_code,reason,verdict", [
    ("__ERIS_COMPILED__\ndelegates:PASS\nreturns:PASS\nnull_guard:PASS\n", 0, "", "correct"),
    ("__ERIS_COMPILED__\ndelegates:PASS\nreturns:FAIL\nnull_guard:FAIL\n", 0, "", "partial"),
    ("", 101, "", "incorrect"),
    ("__ERIS_COMPILED__\ndelegates:PASS\nreturns:PASS\nnull_guard:PASS\n", -1, "timeout", "incorrect"),
    ("__ERIS_COMPILED__\ndelegates:PASS\ndelegates:PASS\n", 0, "", "incorrect"),
])
def test_java_uses_real_receipt_lines_and_always_removes_its_container(tmp_path, monkeypatch, stdout, exit_code, reason, verdict):
    java = DockerJavaWorkspace(tmp_path)
    monkeypatch.setattr(java, "readiness", lambda: {"available": True})
    monkeypatch.setattr(java, "_bounded_run", lambda *_a, **_k: (exit_code, stdout, "student compile error", reason))
    cleanup = []
    monkeypatch.setattr(subprocess, "run", lambda command, **_: cleanup.append(command))
    result = java.execute("class OrderService {}", exercise())
    assert result["result"] == verdict
    assert cleanup[0][:3] == ["docker", "rm", "-f"]
    assert cleanup[0][-1].startswith("eris-java-")
    assert (next((tmp_path / "jobs").iterdir()) / "code/Harness.java").exists()


def test_bounded_process_reader_stops_output_flood():
    code = "import sys;\nwhile True: sys.stdout.write('x'*4096); sys.stdout.flush()"
    _, output, error, failure = DockerJavaWorkspace._bounded_run([sys.executable, "-c", code], timeout=2)
    assert failure == "output_limit"
    assert len(output.encode()) <= MAX_OUTPUT_BYTES and len(error.encode()) <= MAX_OUTPUT_BYTES


def test_bounded_process_reader_times_out_infinite_loop():
    _, _, _, failure = DockerJavaWorkspace._bounded_run([sys.executable, "-c", "while True: pass"], timeout=.2)
    assert failure == "timeout"


@pytest.fixture(scope="module")
def real_java(tmp_path_factory):
    java = DockerJavaWorkspace(tmp_path_factory.mktemp("real-java"))
    ready = java.readiness()
    if not ready["available"]:
        pytest.skip("Real Docker/JVM spike unavailable: " + ready["reason"])
    return java


@pytest.mark.parametrize("case", ["correct", "partial", "compile_error", "timeout", "output_limit"])
def test_java_real_container_read_only_noexec_and_error_cases(real_java, case):
    item = exercise()
    code = item["solution"]
    if case == "partial":
        code = code.replace("java.util.Objects.requireNonNull(gateway)", "gateway")
    elif case == "compile_error":
        code = "class OrderService { invalid syntax }"
    elif case == "timeout":
        code = code.replace("return gateway.charge(amount);", "while (true) {}")
    elif case == "output_limit":
        code = code.replace("return gateway.charge(amount);", 'while (true) { System.out.println("x".repeat(1000)); }')
    result = real_java.execute(code, item, timeout=3 if case == "timeout" else 15)
    assert result["status"] == "completed", result
    if case in {"correct", "partial"}:
        assert result["result"] == case
    else:
        assert result["reason"] == case
        assert result["result"] == "incorrect"
