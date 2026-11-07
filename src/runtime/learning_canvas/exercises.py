"""Server-owned exercises and deterministic assessment, on the learning ledger."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import secrets
import threading
import time

from runtime.learning_core.records import AssessmentAttempt, digest
from runtime.workspace.java_docker import DockerJavaWorkspace, MAX_CODE_BYTES
from runtime.workspace.python_docker import DockerPythonExerciseWorkspace


class CanvasExercises:
    def __init__(self, learning, store, pack, *, workspace=None, python_workspace=None, on_completed=None):
        self.learning, self.store, self.pack = learning, store, pack
        self.workspace = workspace or DockerJavaWorkspace(store._path.parent / "canvas-java")
        self.python_workspace = python_workspace or DockerPythonExerciseWorkspace(store._path.parent / "canvas-python")
        self.on_completed = on_completed
        self._lock = threading.RLock()
        self._pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="eris-code-grader")
        self._future = None

    def close(self):
        self._pool.shutdown(wait=False, cancel_futures=True)

    def definition(self, node_id, exercise_id):
        for exercise in self.pack.node(node_id)["exercises"]:
            if exercise["id"] == exercise_id:
                return exercise
        raise ValueError("unknown_canvas_exercise")

    def open(self, node_id, exercise_id, request_id):
        definition = self.definition(node_id, exercise_id)
        activity_id = "canvas:activity:" + digest(request_id)[:40]
        with self._lock:
            item = self.store.load_activity(activity_id)
            if item is not None and (item["node_id"], item["exercise_id"]) != (node_id, exercise_id):
                raise ValueError("canvas_request_id_reused")
            if item is None:
                item = {"activity_id": activity_id, "kind": "canvas", "node_id": node_id,
                        "focus_revision": self.store.get_meta("learning_canvas_v1", {}).get("focus_revision", 0),
                        "exercise_id": exercise_id, "definition_hash": digest(definition),
                        "assistance_id": "canvas:help-scope:" + digest([node_id, exercise_id, digest(definition)])[:40],
                        "nonce": secrets.token_hex(16), "submissions": {}, "latest_submission": "",
                        "created_at": time.time(), "help": ""}
                if definition["type"] in {"java", "python"}:
                    previous_id = self.store.get_meta("canvas:" + definition["type"] + ":" + node_id, "")
                    previous = self.store.load_activity(previous_id) if previous_id else None
                    if previous and previous["definition_hash"] == item["definition_hash"] and previous.get("code"):
                        item["code"] = previous["code"]
                self.store.save_activity(item)
                self.store.register_assistance(activity_id, node_id)
                self.store.register_assistance(item["assistance_id"], node_id)
                inherited = self._assistance(item)
                if inherited["assistance"] != "independent":
                    self.store.record_assistance(activity_id, event_id="canvas:inherit:" + digest(item["assistance_id"])[:40],
                        assistance=inherited["assistance"], hint_count=inherited.get("hint_count", 0))
            self.store.set_meta("canvas:activity:" + node_id, activity_id)
            if definition["type"] in {"java", "python"}:
                self.store.set_meta("canvas:" + definition["type"] + ":" + node_id, activity_id)
            return self.public(item)

    def fallback(self, activity):
        """Unavailable Java/Python switches to the authored quiz, preserving code/notice."""
        with self._lock:
            if activity.get("status") != "unavailable" or activity.get("type") not in {"java", "python"}:
                return activity
            current = self.store.get_meta("canvas:activity:" + activity["node_id"], "")
            if current != activity["activity_id"]:
                return activity
            choice = next((e for e in self.pack.node(activity["node_id"])["exercises"] if e["type"] == "multiple_choice"), None)
            if choice is None:
                return activity
            public = self.open(activity["node_id"], choice["id"],
                "fallback:" + digest([activity["activity_id"], activity.get("submission_id", "")])[:40])
            item = self.store.load_activity(public["activity_id"])
            item["unavailable_notice"] = activity.get("guidance", "Kiểm tra Docker Desktop và container runner.")
            self.store.save_activity(item)
            return self.public(item)

    def latest(self, node_id):
        key = self.store.get_meta("canvas:activity:" + node_id, "")
        with self._lock:
            item = self.store.load_activity(key) if key else None
            return self.public(item) if item else None

    def _load(self, request):
        activity_id, nonce = request.get("activity_id"), request.get("nonce")
        if not isinstance(activity_id, str) or not isinstance(nonce, str):
            raise ValueError("invalid_canvas_activity")
        item = self.store.load_activity(activity_id)
        if not item or item.get("kind") != "canvas" or not secrets.compare_digest(item["nonce"], nonce):
            raise ValueError("invalid_canvas_activity_nonce")
        definition = self.definition(item["node_id"], item["exercise_id"])
        if item["definition_hash"] != digest(definition):
            raise ValueError("canvas_exercise_version_changed")
        return item, definition

    def public(self, item):
        definition = self.definition(item["node_id"], item["exercise_id"])
        result = {key: item[key] for key in ("activity_id", "node_id", "exercise_id", "nonce", "created_at")}
        result.update(type=definition["type"], prompt=definition["prompt"], help=item["help"],
                      assistance=self._assistance(item)["assistance"])
        if item.get("unavailable_notice"):
            result["unavailable_notice"] = item["unavailable_notice"]
        if definition["type"] == "multiple_choice":
            result["options"] = definition["options"]
        else:
            result["code"] = item.get("code", definition["starter_code"])
        submission = item["submissions"].get(item["latest_submission"])
        if submission:
            result.update(submission_id=item["latest_submission"], submitted_at=submission["recorded_at"], **submission["receipt"])
        else:
            result["status"] = "ready"
        return result

    def handle(self, action, request):
        with self._lock:
            item, definition = self._load(request)
            if action == "exercise_status":
                # Reconcile a worker interrupted by a process restart. Never
                # pretend the old queued code actually ran.
                for submission in item["submissions"].values():
                    if submission["receipt"]["status"] == "running" and (self._future is None or self._future.done()):
                        if submission.get("attempt"):
                            self._commit(item, submission)
                        else:
                            submission["receipt"] = {"status": "unavailable", "reason": "java_run_interrupted",
                                                     "guidance": "Lần chạy trước bị gián đoạn. Hãy chạy lại."}
                self.store.save_activity(item)
                return self.public(item)
            if action == "submit":
                answer = request.get("answer") if definition["type"] == "multiple_choice" else request.get("code")
                if not isinstance(answer, str) or not answer.strip() or len(answer.encode()) > MAX_CODE_BYTES:
                    raise ValueError("invalid_canvas_submission")
                if definition["type"] == "multiple_choice" and answer not in {o["id"] for o in definition["options"]}:
                    raise ValueError("invalid_canvas_option")
                key = request["request_id"]
                if key in item["submissions"]:
                    submission = item["submissions"][key]
                    if submission["answer_hash"] != digest(answer):
                        raise ValueError("canvas_submission_id_reused")
                    if submission.get("attempt"):
                        self._commit(item, submission)
                    return self.public(item)
            current = item["submissions"].get(item["latest_submission"], {})
            if current.get("receipt", {}).get("status") == "running":
                raise ValueError("canvas_exercise_running")
            if action in {"hint", "reveal"}:
                assistance = "hinted" if action == "hint" else "answer_revealed"
                self.store.record_assistance(item["activity_id"], event_id="canvas:help:" + digest(request["request_id"])[:40],
                    assistance=assistance, hint_count=1 if action == "hint" else 0)
                scope = item.get("assistance_id", item["activity_id"])
                if scope != item["activity_id"]:
                    self.store.record_assistance(scope, event_id="canvas:help:" + digest(request["request_id"])[:40],
                        assistance=assistance, hint_count=1 if action == "hint" else 0)
                item["help"] = (definition["hint"] if action == "hint" else
                    definition["explanation"] + "\nĐáp án: " + next(o["text"] for o in definition["options"] if o["id"] == definition["answer"])
                    if definition["type"] == "multiple_choice" else definition["solution"])
                self.store.save_activity(item)
                return self.public(item)
            if action != "submit":
                raise ValueError("unknown_canvas_exercise_action")
            if len(item["submissions"]) >= 32:
                raise ValueError("canvas_submission_budget_exceeded")
            if definition["type"] in {"java", "python"} and self._future is not None and not self._future.done():
                raise ValueError(f"{definition['type']}_worker_busy")
            submission = {"answer_hash": digest(answer), "recorded_at": time.time(),
                          "focus_revision": (self.store.get_meta("learning_canvas_v1", {}).get("focus_revision", item["focus_revision"])
                              if self.store.get_meta("learning_canvas_v1", {}).get("node_id") == item["node_id"] else item["focus_revision"]),
                          "attempt_id": "canvas:attempt:" + digest([item["activity_id"], key])[:40],
                          "assistance": self._assistance(item),
                          "receipt": {"status": "running"}, "notified": False}
            item["submissions"][key] = submission
            item["latest_submission"] = key
            if definition["type"] in {"java", "python"}:
                item["code"] = answer
                self.store.save_activity(item)
                target_grader = self._grade_java if definition["type"] == "java" else self._grade_python
                self._future = self._pool.submit(target_grader, item["activity_id"], key, answer, definition)
                return self.public(item)
            receipt = {"status": "completed", "result": "correct" if answer == definition["answer"] else "incorrect"}
            self._prepare_attempt(item, submission, receipt)
            self._commit(item, submission)
            return self.public(item)

    def _assistance(self, item):
        # Reopening the exact same authored item is not a fresh independent
        # variant after its answer/hint has already been disclosed.
        return self.store.assistance_for(item.get("assistance_id", item["activity_id"]))

    def _prepare_attempt(self, item, submission, receipt):
        definition = self.definition(item["node_id"], item["exercise_id"])
        submission["completed_receipt"] = receipt
        assistance = submission["assistance"]
        tests = receipt.get("tests", [])
        strength = sum(test["passed"] for test in tests) / len(tests) if tests else 1.0
        attempt = AssessmentAttempt(
            submission["attempt_id"], item["node_id"], receipt["result"],
            "quiz_incorrect" if receipt["result"] == "incorrect" else "application" if tests else "quiz_correct",
            strength=strength, path_id=self.pack.path.path_id, interaction_id=item["activity_id"],
            activity_id=item["activity_id"], item_id=item["exercise_id"], item_family_id=item["exercise_id"],
            assistance=assistance["assistance"], hint_count=assistance.get("hint_count", 0),
            answer_revealed=assistance["assistance"] == "answer_revealed",
            rubric_version=f"{definition['type']}:v1" if definition["type"] in {"java", "python"} else "deterministic-v1",
            assessor_version="canvas-deterministic-v1", recorded_at=submission["recorded_at"],
            error_code=receipt.get("reason", "") if receipt["result"] != "correct" else "",
        )
        submission["attempt"] = attempt.to_dict()
        self.store.save_activity(item)  # replay-safe if the process stops at the ledger boundary

    def _commit(self, item, submission):
        self.learning.record_attempt(AssessmentAttempt.from_mapping(submission["attempt"]))
        if self.store.load_attempt(submission["attempt_id"]) is None:
            raise ValueError("canvas_attempt_unavailable")
        submission["receipt"] = submission["completed_receipt"]
        self.store.save_activity(item)

    def take_event(self, activity_id):
        with self._lock:
            item = self.store.load_activity(activity_id)
            submission = item["submissions"].get(item["latest_submission"]) if item else None
            if not submission or submission["notified"] or not submission.get("attempt"):
                return None
            submission["notified"] = True
            self.store.save_activity(item)
            attempt = submission["attempt"]
            return {"action": "assessment", "event_id": "canvas:assessment:" + digest(attempt["attempt_id"])[:40],
                    "node_id": item["node_id"], "exercise_id": item["exercise_id"],
                    "focus_revision": submission.get("focus_revision", item.get("focus_revision", 0)),
                    "result": attempt["result"], "assistance": attempt["assistance"],
                    "tests": submission["receipt"].get("tests", [])}

    def _grade_java(self, activity_id, key, code, definition):
        try:
            receipt = self.workspace.execute(code, definition)
        except Exception as exc:
            receipt = {"status": "unavailable", "reason": "java_grader_" + type(exc).__name__,
                       "guidance": "Kiểm tra Docker rồi chạy lại; bạn vẫn có thể làm bài trắc nghiệm."}
        with self._lock:
            item = self.store.load_activity(activity_id)
            submission = item["submissions"][key]
            if receipt.get("status") == "completed":
                self._prepare_attempt(item, submission, receipt)
                self._commit(item, submission)
            else:
                submission["receipt"] = receipt
                self.store.save_activity(item)
            public = self.public(item)
        if callable(self.on_completed):
            self.on_completed(public)

    def _grade_python(self, activity_id, key, code, definition):
        try:
            receipt = self.python_workspace.execute(code, definition)
        except Exception as exc:
            receipt = {"status": "unavailable", "reason": "python_grader_" + type(exc).__name__,
                       "guidance": "Kiểm tra Docker rồi chạy lại; bạn vẫn có thể làm bài trắc nghiệm."}
        with self._lock:
            item = self.store.load_activity(activity_id)
            submission = item["submissions"][key]
            if receipt.get("status") == "completed":
                self._prepare_attempt(item, submission, receipt)
                self._commit(item, submission)
            else:
                submission["receipt"] = receipt
                self.store.save_activity(item)
            public = self.public(item)
        if callable(self.on_completed):
            self.on_completed(public)
