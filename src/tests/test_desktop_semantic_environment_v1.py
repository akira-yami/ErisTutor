from types import SimpleNamespace

from runtime.ai_orchestrated.ambient_screen_context import AmbientScreenContext
from runtime.ai_orchestrated.desktop_semantics import DesktopSemanticRuntime
from cortex.se import SeLens


def test_numeric_ocr_churn_is_mechanical_and_does_not_wake() -> None:
    runtime = DesktopSemanticRuntime()
    screen = AmbientScreenContext(
        revision=1,
        window_title="Task Manager",
        process_name="Taskmgr.exe",
        age_seconds=0.2,
        changed_ratio=0.05,
        content_changed=True,
        changed_text=("CPU 31%", "RAM 62%"),
        evidence_refs=("screen:cpu", "screen:ram"),
    )
    situation = runtime.observe(
        screen,
        telemetry={"active_process": "Taskmgr.exe", "idle_seconds": 1},
        now=1000.0,
    )
    assert situation.semantic_delta is False
    assert situation.wake_recommended is False
    assert situation.max_attention_level == 0
    assert situation.recent_events[-1].kind == "mechanical_screen_delta"


def test_error_fusion_becomes_cognition_candidate() -> None:
    runtime = DesktopSemanticRuntime()
    screen = AmbientScreenContext(
        revision=3,
        window_title="main.py - Visual Studio Code",
        process_name="Code.exe",
        age_seconds=0.1,
        changed_ratio=0.18,
        content_changed=True,
        changed_text=("ConnectionRefusedError: [WinError 10061]",),
        evidence_refs=("screen:terminal-error",),
    )
    observer = SimpleNamespace(activity_intent="debugging", possible_error=0.91, confidence=0.78)
    rolling = SimpleNamespace(busy_score=0.82, interrupt_cost=0.88)
    situation = runtime.observe(
        screen,
        telemetry={
            "active_process": "Code.exe",
            "active_window_title": "main.py - Visual Studio Code",
            "active_window_kind": "coding",
            "idle_seconds": 2,
        },
        observer_frame=observer,
        rolling_context=rolling,
        now=2000.0,
    )
    assert situation.activity == "debugging"
    assert situation.semantic_delta is True
    assert situation.wake_recommended is True
    assert situation.max_attention_level == 3
    assert situation.last_action == "error_became_visible"
    assert situation.last_outcome == "possible_problem"
    assert situation.attention_state == "focused"


def test_private_surface_never_copies_changed_text_into_timeline() -> None:
    runtime = DesktopSemanticRuntime()
    screen = AmbientScreenContext(
        revision=2,
        window_title="Discord",
        process_name="Discord.exe",
        age_seconds=0.1,
        privacy_blocked=True,
        content_changed=True,
        changed_text=("this must never survive",),
        evidence_refs=("screen:private",),
    )
    situation = runtime.observe(
        screen,
        telemetry={
            "active_process": "Discord.exe",
            "content_metadata": {"privacy_level": "private"},
            "idle_seconds": 4,
        },
        now=3000.0,
    )
    event = situation.recent_events[-1]
    assert event.kind == "private_surface_changed"
    assert event.detail == ""
    assert event.object_ref == ""
    assert situation.semantic_delta is False
    assert "this must never survive" not in str(situation.to_dict())


def test_situation_uses_process_identity_and_interruption_cost() -> None:
    runtime = DesktopSemanticRuntime()
    screen = AmbientScreenContext(
        revision=1,
        window_title="vision.py - eris - Visual Studio Code",
        process_name="Code.exe",
        age_seconds=0.1,
    )
    situation = runtime.observe(
        screen,
        telemetry={
            "active_process": "Code.exe",
            "active_window_kind": "coding",
            "active_file": "src/vision.py",
            "idle_seconds": 1,
        },
        observer_frame=SimpleNamespace(activity_intent="debugging", possible_error=0.12, confidence=0.76),
        rolling_context=SimpleNamespace(busy_score=0.84, interrupt_cost=0.87),
        now=4000.0,
    )
    assert situation.focused_process == "Code.exe"
    assert situation.focused_object == "src/vision.py"
    assert situation.activity == "debugging"
    assert situation.attention_state == "focused"
    assert situation.interruption_cost == 0.87


def test_telemetry_snapshot_preserves_structured_foreground_fields() -> None:
    snapshot = SeLens().build_snapshot({
        "captured_at": "16:20",
        "active_window": "vision.py - Visual Studio Code",
        "active_window_title": "vision.py - Visual Studio Code",
        "active_app": "vision.py - Visual Studio Code",
        "active_process": "Code.exe",
        "window_class": "Chrome_WidgetWin_1",
        "focus_changed": True,
        "interaction_state": "active",
        "foreground_hwnd": 123,
        "idle_seconds": 1,
        "open_windows": (),
        "browser_tabs": (),
    })
    machine = snapshot.to_machine_context()
    assert machine["active_process"] == "Code.exe"
    assert machine["active_window_title"] == "vision.py - Visual Studio Code"
    assert machine["window_class"] == "Chrome_WidgetWin_1"
    assert machine["focus_changed"] is True
    assert machine["interaction_state"] == "active"
