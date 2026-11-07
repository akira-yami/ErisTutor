"""Learner Portrait read model, Gemini validation and browser contract."""
from __future__ import annotations

import json
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from runtime.learner_portrait import GeminiPortraitNarrator, LearnerPortraitService


class FakeLearning:
    def __init__(self):
        self.activated = False
        self.insights_calls = []

    def learning_spaces(self):
        return {"paths": [
            {"path_id": "math", "domain_id": "math", "title": "Toán", "kp_count": 2,
             "goal": "Học toán", "active": True},
            {"path_id": "java", "domain_id": "dev", "title": "Java", "kp_count": 1,
             "goal": "Học Java", "active": False},
        ], "active_path_id": "math"}

    def learning_insights(self, *, path_id, max_kps):
        self.insights_calls.append((path_id, max_kps))
        return {"knowledge": [{"kp_id": f"{path_id}.k1", "title": "Khái niệm cơ sở",
                 "prerequisites": [], "signals": {"status": "needs_independent_probe",
                 "assisted_families": 4, "independent_correct": 0,
                 "independent_incorrect": 0}, "mastery_stage": "introduced"}]}

    def activate_path(self, *args, **kwargs):
        raise AssertionError("Read-only portrait must not mutate active path")


class FakeNarrator:
    def describe(self, evidence):
        self.evidence = evidence
        return {"state": "empty", "observations": []}


def test_multi_subject_read_only_and_no_fake_mastery():
    learning, narrator = FakeLearning(), FakeNarrator()
    service = LearnerPortraitService(narrator=narrator)
    one = service.build(learning=learning)
    assert one["schema"] == "eris.learner_portrait.v1"
    assert len(one["spaces"]) == 2
    assert one["selection"]["path_id"] == "math"
    assert one["knowledge"][0]["assisted_families"] == 4
    assert one["knowledge"][0]["independent_correct"] == 0
    assert "mastery_percentage" not in one["knowledge"][0]
    two = service.build(learning=learning, path_id="java")
    assert two["selection"]["path_id"] == "java"
    assert learning.insights_calls == [("math", 48), ("java", 48)]


def test_unknown_path_does_not_switch_tutor():
    output = LearnerPortraitService(narrator=FakeNarrator()).build(
        learning=FakeLearning(), path_id="not-present")
    assert output["selection"]["path_id"] == "math"


def test_empty_path_and_scrubs_secret_before_gemini():
    class Private(FakeLearning):
        def learning_spaces(self):
            value = super().learning_spaces()
            value["paths"][0]["title"] = "token=sk-proj-abcABCabcABCabcABCabcABCabcABCabc123"
            return value
    n = FakeNarrator()
    output = LearnerPortraitService(narrator=n).build(learning=Private())
    assert "sk-proj-" not in json.dumps(n.evidence)
    assert "sk-proj-" not in json.dumps(output)
    empty = LearnerPortraitService(narrator=n).build(learning=None)
    assert empty["spaces"] == [] and not empty["knowledge"]


def test_gemini_uses_only_known_refs_and_rejects_stale_result():
    class Adapter:
        started = threading.Event()
        release = threading.Event()
        def create_chat_completion(self, *, messages, **kwargs):
            self.started.set()
            self.release.wait(3)
            return {"choices": [{"message": {"content": json.dumps({"observations": [
                {"text": "Có bằng chứng học tập", "source_refs": ["path:a"]},
                {"text": "Không có nguồn", "source_refs": ["path:bad"]},
            ]})}, "finish_reason": "stop"}]}
    adapter = Adapter()
    speaker = GeminiPortraitNarrator(adapter=adapter)
    first = [{"ref":"path:a", "title":"A"}]
    assert speaker.describe(first)["state"] == "generating"
    assert adapter.started.wait(1)
    # Different fingerprint while the original worker is still active.
    assert speaker.describe([{"ref":"path:b","title":"B"}])["state"] == "generating"
    adapter.release.set()
    for _ in range(30):
        if not speaker._worker_active: break
        time.sleep(.01)
    assert speaker.describe([{"ref":"path:b","title":"B"}])["state"] == "generating"
    for _ in range(30):
        result = speaker.describe([{"ref":"path:b","title":"B"}])
        if result["state"] == "ready": break
        time.sleep(.01)
    assert result == {"state": "ready", "observations": []}  # A is not a valid ref for B.


def test_web_contract_static_files_and_token_protected_api():
    base = Path(__file__).resolve().parents[1] / "body"
    server = (base / "UdpListener.RichChatPortal.cs").read_text(encoding="utf-8")
    incoming = (Path(__file__).resolve().parents[1] / "runtime/incoming_protocol_runtime.py").read_text(encoding="utf-8")
    js = (base / "richchat/portrait/portrait.js").read_text(encoding="utf-8")
    cs = (base / "UdpListener.CapabilityPanel.cs").read_text(encoding="utf-8")
    assert '"portrait/index.html"' in server
    assert 'relative == "api/portrait/state"' in server
    assert 'Origin: http://' in server
    assert 'IPAddress.Loopback' in server
    assert '"action": "portrait_snapshot"' in incoming or 'action == "portrait_snapshot"' in incoming
    assert 'ReadJsonString(root, "action") == "portrait_snapshot"' in cs
    assert "textContent =" in js and "innerHTML" not in js
    assert 'action===\'select\'' in js
