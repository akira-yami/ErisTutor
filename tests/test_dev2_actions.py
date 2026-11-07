import json
import time
from types import SimpleNamespace

import pytest

from actions import ActionExecutor
from cortex.subject.contracts import ActionRequest
from domain import Decision
from native import desktop_input
from parser_grounding import DecisionParser
from runtime.world import CapabilityAuthority, CapabilityPolicy, CommandPolicy, SandboxWorld


def test_parser_keeps_typed_desktop_action():
    parser = DecisionParser(Decision("fallback", "stand"))
    result = parser.parse(json.dumps({
        "text": "Đã nhận yêu cầu.", "animation": "stand", "system_action": "click_mouse",
        "capability_arguments": {"hwnd": 25, "x": -20, "y": 100},
    }))
    assert result.system_action == "click_mouse"
    assert result.capability_arguments == {"hwnd": 25, "x": -20, "y": 100}
    assert not ActionExecutor().execute_capability("click_mouse", result.capability_arguments).success


def test_desktop_input_runs_only_with_signed_window_scope(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(desktop_input, "type_text", lambda **args: calls.append(args))
    authority = CapabilityAuthority(issuer="test", audience="sandbox", secret=b"a" * 32)
    policy = CapabilityPolicy(CommandPolicy(frozenset(), str(tmp_path)), token_verifier=authority.verify)
    world = SandboxWorld(policy=policy)
    executor = ActionExecutor()

    class Subject:
        config = SimpleNamespace(subject_id="test")

        async def deliberate(self, observation):
            return observation

        def to_action_request(self, decision, *, requested_capabilities, expected_outcomes, idempotency_key):
            return ActionRequest(
                action_id="action", decision_id=decision.observation_id, kind="type_text",
                arguments={}, expected_outcomes=expected_outcomes, requested_capabilities=requested_capabilities,
                idempotency_key=idempotency_key, authorship_draft_id="draft", created_at=time.time(),
            )

        async def observe_outcome(self, outcome):
            self.outcome = outcome

    subject = Subject()
    executor.bind_subject_service(subject)
    allowed_hwnd = "26"
    executor.bind_sandbox_world(world, lambda request, _: authority.issue(
        subject_id="test", capabilities=request.requested_capabilities, policy_version="v1",
        scope={"desktop_window_handles": [allowed_hwnd]},
    ))
    arguments = {"hwnd": 25, "text": "Tiếng Việt"}
    assert not executor.execute_capability("type_text", arguments).success
    assert subject.outcome.denial_reason == "desktop_window_outside_token_scope"
    assert not calls
    allowed_hwnd = "25"
    result = executor.execute_capability("type_text", arguments)
    assert result.success and subject.outcome.status.value == "succeeded"
    assert calls == [{"hwnd": 25, "text": "Tiếng Việt"}]
    assert result.dispatched and not result.observed
    assert result.to_public_meta()["action_input"] == ""


def test_native_input_rechecks_foreground_and_sends_unicode(monkeypatch):
    sent = []

    def send(count, inputs, _size):
        sent.extend((item.data.keyboard.wScan, item.data.keyboard.dwFlags) for item in inputs)
        return count

    api = SimpleNamespace(GetForegroundWindow=lambda: 25, GetAsyncKeyState=lambda _: 0, SendInput=send)
    monkeypatch.setattr(desktop_input, "_user32", lambda: api)
    with pytest.raises(PermissionError, match="foreground_window_changed"):
        desktop_input.type_text(26, "A")
    assert not sent
    desktop_input.type_text(25, "😀")
    assert sent == [(0xD83D, 4), (0xD83D, 6), (0xDE00, 4), (0xDE00, 6)]


def test_mouse_click_cannot_target_another_window(monkeypatch):
    moved = []
    sent = []

    def send(count, inputs, _size):
        sent.extend(item.data.mouse.dwFlags for item in inputs)
        return count

    api = SimpleNamespace(
        GetForegroundWindow=lambda: 25, GetAsyncKeyState=lambda _: 0,
        WindowFromPoint=lambda _: 26, GetAncestor=lambda *_: 26,
        SetCursorPos=lambda x, y: moved.append((x, y)) or True, SendInput=send,
    )
    monkeypatch.setattr(desktop_input, "_user32", lambda: api)
    with pytest.raises(PermissionError, match="point_outside_foreground_window"):
        desktop_input.click_mouse(25, 10, 20)
    assert not moved and not sent
    api.GetAncestor = lambda *_: 25
    desktop_input.click_mouse(25, 10, 20)
    assert moved == [(10, 20)] and sent == [2, 4]
