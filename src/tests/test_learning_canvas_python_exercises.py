from __future__ import annotations

import json
import threading
import time
import uuid
import pytest

from runtime.learning_canvas.pack import KnowledgePack, validate_pack
from runtime.learning_canvas.service import LearningCanvasService
from runtime.learning_core import LearningService, LearningStore
from runtime.workspace.python_docker import DockerPythonExerciseWorkspace


SAMPLE_PYTHON_PACK = {
    "schema": "eris.learning.pack.v1",
    "version": "1",
    "group_id": "python-algorithms",
    "skill_namespace": "pyalgo",
    "title": "Thuật toán Python",
    "path_id": "pyalgo:v1:algorithms",
    "goal": "Luyện tập thuật toán với Python sandbox",
    "nodes": [
        {
            "id": "pyalgo:v1:binary_search",
            "title": "Tìm kiếm nhị phân",
            "summary": "Tìm kiếm trong mảng đã sắp xếp với O(log n)",
            "prerequisites": [],
            "blocks": [
                {"id": "b-explain", "kind": "explanation", "title": "Khái niệm", "text": "Mỗi bước chia đôi khoảng tìm kiếm."},
                {"id": "b-code", "kind": "python", "title": "Minh họa", "text": "def binary_search(arr, x): pass"}
            ],
            "exercises": [
                {
                    "id": "ex-choice",
                    "type": "multiple_choice",
                    "prompt": "Độ phức tạp của binary search là gì?",
                    "hint": "Chia đôi mỗi bước",
                    "options": [
                        {"id": "opt-log", "text": "O(log n)"},
                        {"id": "opt-n", "text": "O(n)"}
                    ],
                    "answer": "opt-log",
                    "explanation": "Binary search chia đôi không gian tìm kiếm sau mỗi bước."
                },
                {
                    "id": "ex-code",
                    "type": "python",
                    "prompt": "Cài đặt hàm find_index(arr, target) tìm vị trí của target trong arr.",
                    "hint": "Dùng left, right và mid = (left + right) // 2",
                    "starter_code": "def find_index(arr, target):\n    # TODO\n    return -1\n",
                    "solution": "def find_index(arr, target):\n    left, right = 0, len(arr) - 1\n    while left <= right:\n        mid = (left + right) // 2\n        if arr[mid] == target:\n            return mid\n        elif arr[mid] < target:\n            left = mid + 1\n        else:\n            right = mid - 1\n    return -1\n",
                    "harness": "import solution\n\nassert solution.find_index([1, 3, 5, 7], 5) == 2\nprint('test_found:PASS')\nassert solution.find_index([1, 3, 5, 7], 2) == -1\nprint('test_not_found:PASS')\n",
                    "tests": [
                        {"id": "test_found", "description": "Tìm thấy phần tử"},
                        {"id": "test_not_found", "description": "Không tìm thấy phần tử"}
                    ]
                }
            ]
        }
    ]
}


class PythonStub:
    def __init__(self):
        self.started = threading.Event()
        self.release = threading.Event()
        self.release.set()
        self.calls = 0
        self.receipt = {
            "status": "completed", "result": "correct",
            "tests": [
                {"id": "test_found", "description": "Tìm thấy phần tử", "passed": True},
                {"id": "test_not_found", "description": "Không tìm thấy phần tử", "passed": True}
            ],
            "duration_ms": 120, "diagnostic": "", "reason": "graded"
        }

    def execute(self, code, definition):
        self.calls += 1
        self.started.set()
        assert self.release.wait(3)
        return self.receipt


@pytest.fixture
def python_canvas(tmp_path):
    store = LearningStore(tmp_path / "learning.sqlite3")
    pack = validate_pack(SAMPLE_PYTHON_PACK)
    stub = PythonStub()
    service = LearningCanvasService(
        LearningService(store), store=store, pack=pack, python_workspace=stub
    )
    service.test_events = []
    service.on_event = service.test_events.append
    yield service
    stub.release.set()
    if service.exercises._future:
        service.exercises._future.result(timeout=4)
    service.close()
    store.close()


def test_python_pack_validation_and_open_exercise(python_canvas):
    service = python_canvas
    node_id = "pyalgo:v1:binary_search"
    req_id = "req-" + uuid.uuid4().hex
    public = service.exercises.open(node_id, "ex-code", req_id)
    assert public["type"] == "python"
    assert "find_index" in public["code"]
    assert public["status"] == "ready"


def test_python_exercise_submit_and_grade(python_canvas):
    service = python_canvas
    node_id = "pyalgo:v1:binary_search"
    req_id = "req-" + uuid.uuid4().hex
    public = service.exercises.open(node_id, "ex-code", req_id)

    submit_id = "sub-" + uuid.uuid4().hex
    reply = service.exercises.handle("submit", {
        "request_id": submit_id,
        "activity_id": public["activity_id"],
        "nonce": public["nonce"],
        "code": "def find_index(arr, target): return 2 if target == 5 else -1"
    })
    assert reply["status"] == "running"
    # Wait for stub worker
    if service.exercises._future:
        service.exercises._future.result(timeout=2)

    status_reply = service.exercises.handle("exercise_status", {
        "request_id": "status-" + uuid.uuid4().hex,
        "activity_id": public["activity_id"],
        "nonce": public["nonce"]
    })
    assert status_reply["status"] == "completed"
    assert status_reply["result"] == "correct"
    assert len(status_reply["tests"]) == 2

    # Check that attempt is committed into learning store
    attempts = service.exercises.store.load_attempts(node_id)
    assert len(attempts) == 1
    assert attempts[0]["result"] == "correct"
    assert attempts[0]["rubric_version"] == "python:v1"
