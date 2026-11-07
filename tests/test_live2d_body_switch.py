# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import socket
from pathlib import Path
from typing import Any

from live2d_body.parameter_profile import INFJ_PARAMETER_PROFILE
from live2d_body.stage_state import Live2DStageState
from live2d_body.udp_receiver import Live2DStageReceiver


def _get_project_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _get_body_dir() -> Path:
    root = _get_project_root()
    return (root / "src" / "body") if (root / "src" / "body").exists() else (root / "body")


def _get_model_path() -> Path:
    root = _get_project_root()
    p = root / "src" / "assets" / "live2d" / "infj" / "infj.model3.json"
    if p.exists():
        return p
    return root / "assets" / "live2d" / "infj" / "infj.model3.json"


def test_model_bundle_has_runtime_references() -> None:
    model = _get_model_path()
    payload = json.loads(model.read_text(encoding="utf-8"))
    refs = payload["FileReferences"]
    referenced = [refs["Moc"], *refs["Textures"], refs["Physics"], refs["DisplayInfo"]]
    assert all((model.parent / relative).is_file() for relative in referenced)
    groups = {item["Name"]: item["Ids"] for item in payload["Groups"]}
    assert groups["EyeBlink"] == ["ParamEyeLOpen", "ParamEyeROpen"]
    assert groups["LipSync"] == ["ParamMouthOpenY"]


def test_stage_state_tracks_speech_lifecycle() -> None:
    state = Live2DStageState()
    state.handle_payload({"protocol": "eris.stage", "type": "stage_segment", "emotion": "curious"})
    assert state.advance(1 / 60).emotion == "curious"
    state.handle_payload({"protocol": "eris.stage", "type": "audio_playback_started"})
    assert state.advance(1 / 60).speaking is True
    state.handle_payload({"protocol": "eris.stage", "type": "audio_playback_done"})
    assert state.advance(1 / 60).speaking is False


def test_godot_stage_control_events_never_drive_public_speech_bubble() -> None:
    body_dir = _get_body_dir()
    parser_source = (body_dir / "UdpDecisionParser.cs").read_text(encoding="utf-8")
    listener_source = (body_dir / "UdpListener.cs").read_text(encoding="utf-8")

    # eris.stage carries choreography/lifecycle metadata only. Public bubble text
    # is released by the normal body payload at audio_start (or timeout/failure).
    assert 'TryReadFirstString(root, out string stageText, "display_text")' not in parser_source
    assert "IsStageSpeechEnvelope" not in parser_source
    assert "TryApplyControlEnvelopeSpeech(payload)" not in listener_source
    assert "private bool TryApplyControlEnvelopeSpeech" not in listener_source


def test_right_click_request_targets_godot_context_menu() -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as listener:
        listener.bind(("127.0.0.1", 0))
        listener.settimeout(1.0)
        port = listener.getsockname()[1]
        receiver = Live2DStageReceiver(
            Live2DStageState(),
            port=port + 1,
            body_host="127.0.0.1",
            body_port=port,
        )
        receiver.send_context_menu_request(screen_x=320, screen_y=240)
        raw, _ = listener.recvfrom(4096)
        payload = json.loads(raw.decode("utf-8"))
        assert payload["protocol"] == "eris.body.control"
        assert payload["type"] == "open_context_menu"
        assert payload["screen_x"] == 320
        assert payload["screen_y"] == 240


def test_gaze_keeps_custom_eyes_coupled_without_extreme_crossing() -> None:
    from live2d_body.gaze import GazeController

    gaze = GazeController(strength_x=0.28, strength_y=0.20, right_x_sign=-1.0)
    sample = None
    for _ in range(30):
        sample = gaze.update(
            cursor_screen=(2000.0, 500.0),
            face_screen=(1000.0, 500.0),
            span_x=400.0,
            span_y=300.0,
            dt=1 / 60,
        )
    assert sample is not None
    assert 0.0 < sample.left_x <= 0.28
    assert -0.28 <= sample.right_x < 0.0
    # Mắt nhìn vào sống mũi có biên nhỏ hơn mắt nhìn ra ngoài.
    assert abs(sample.left_x) > abs(sample.right_x)
    assert 0.0 < sample.head_x_degrees <= 2.4
    assert 0.78 <= sample.left_eye_open_scale <= 1.0


def test_window_geometry_is_reported_to_godot_body() -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as listener:
        listener.bind(("127.0.0.1", 0))
        listener.settimeout(1.0)
        port = listener.getsockname()[1]
        receiver = Live2DStageReceiver(
            Live2DStageState(),
            port=port + 1,
            body_host="127.0.0.1",
            body_port=port,
        )
        receiver.send_window_geometry(screen_x=1200, screen_y=100, width=520, height=900)
        raw, _ = listener.recvfrom(4096)
        payload = json.loads(raw.decode("utf-8"))
        assert payload["type"] == "window_geometry"
        assert payload["screen_x"] == 1200
        assert payload["height"] == 900


def test_renderer_control_visibility_is_resident_state() -> None:
    receiver = Live2DStageReceiver(Live2DStageState(), port=0)
    assert receiver.desired_visible is True
    assert receiver._handle_renderer_control({
        "protocol": "eris.live2d.control",
        "type": "set_visibility",
        "visible": False,
    }) is True
    assert receiver.desired_visible is False
    receiver._handle_renderer_control({
        "protocol": "eris.live2d.control",
        "type": "set_visibility",
        "visible": True,
    })
    assert receiver.desired_visible is True


def test_renderer_control_window_geometry_is_consumed_once() -> None:
    receiver = Live2DStageReceiver(Live2DStageState(), port=0)
    assert receiver._handle_renderer_control({
        "protocol": "eris.live2d.control",
        "type": "set_window_geometry",
        "screen_x": 120,
        "screen_y": 80,
        "width": 520,
        "height": 900,
    }) is True
    assert receiver.consume_requested_window_geometry() == {
        "x": 120,
        "y": 80,
        "width": 520,
        "height": 900,
    }
    assert receiver.consume_requested_window_geometry() is None


def test_renderer_control_shutdown_is_recorded() -> None:
    receiver = Live2DStageReceiver(Live2DStageState(), port=0)
    receiver._handle_renderer_control({
        "protocol": "eris.live2d.control",
        "type": "shutdown",
    })
    assert receiver.shutdown_requested is True


def test_parameter_profile_matches_viewer_semantics() -> None:
    profile = INFJ_PARAMETER_PROFILE
    assert profile.clamp_angle_x(-99.0) == -30.0
    assert profile.clamp_angle_x(99.0) == 30.0
    assert profile.mouth_open_from_ratio(0.0) == 0.0
    assert profile.mouth_open_from_ratio(0.5) == 0.5
    assert profile.mouth_open_from_ratio(1.0) == 1.0


def test_stage_state_closes_native_mouth_at_zero() -> None:
    state = Live2DStageState()
    snapshot = state.advance(0.0, now=0.0)
    assert snapshot.parameters["ParamMouthOpenY"] == 0.0


def test_warm_expression_does_not_raise_both_brows() -> None:
    state = Live2DStageState()
    state.handle_payload({"protocol": "eris.stage", "type": "stage_segment", "emotion": "warm_soft"})
    snapshot = state.advance(0.0, now=0.0)
    assert snapshot.parameters["ParamBrowLAngle"] <= 0.0
    assert snapshot.parameters["ParamBrowRAngle"] <= 0.0
    assert snapshot.parameters["ParamMouthForm"] > 0.0


def test_gaze_left_uses_larger_viewer_left_head_range() -> None:
    from live2d_body.gaze import GazeController

    gaze = GazeController(
        strength_x=0.30,
        strength_y=0.18,
        head_follow_left_degrees=4.8,
        head_follow_right_degrees=2.4,
    )
    sample = None
    for _ in range(60):
        sample = gaze.update(
            cursor_screen=(0.0, 500.0),
            face_screen=(1000.0, 500.0),
            span_x=400.0,
            span_y=300.0,
            dt=1 / 60,
        )
    assert sample is not None
    assert -4.8 <= sample.head_x_degrees < 0.0
    assert sample.left_x < 0.0 < sample.right_x
    assert abs(sample.right_x) > abs(sample.left_x)


def test_eyelid_scaling_reads_current_frame_value_not_cached_snapshot() -> None:
    from live2d_body.renderer import Live2DRenderer

    class FakeParameter:
        id = "ParamEyeLOpen"
        value = 1.0  # stale load-time snapshot

    class FakeModel:
        def __init__(self) -> None:
            self.writes: list[tuple[str, float]] = []

        def GetParameterValue(self, index: int) -> float:
            assert index == 3
            return 0.20  # auto-blink value generated by model.Update() this frame

        def GetParameter(self, index: int) -> FakeParameter:
            return FakeParameter()

        def SetParameterValue(self, parameter_id: str, value: float) -> None:
            self.writes.append((parameter_id, value))

    renderer = object.__new__(Live2DRenderer)
    renderer._parameter_indices = {"ParamEyeLOpen": 3}
    model = FakeModel()

    renderer._scale_eye_parameter(model, "ParamEyeLOpen", 0.5)

    # Mood is an absolute opening cap. The current native blink (.20) must
    # remain smaller than the .50 mood cap without being multiplied twice.
    assert model.writes == [("ParamEyeLOpen", 0.20)]


def test_stage_state_deduplicates_direct_and_godot_mirrors() -> None:
    state = Live2DStageState()
    started = {
        "protocol": "eris.stage",
        "type": "audio_playback_started",
        "event_id": "evt_same_packet",
        "tts_text": "Xin chào.",
    }
    state.handle_payload(started)
    assert state.advance(0.0).speaking is True

    # A duplicate with the same event_id must not mutate state a second time.
    duplicate = dict(started)
    duplicate["type"] = "audio_playback_done"
    state.handle_payload(duplicate)
    assert state.advance(0.0).speaking is True


def test_stage_bus_directly_mirrors_events_to_live2d(monkeypatch) -> None:
    from runtime.stage_event_bus import StageEventBus
    from runtime.stage_protocol import StageProtocolEvent

    class FakeTransport:
        def __init__(self) -> None:
            self.payloads: list[dict] = []

        def send_json(self, payload: dict) -> None:
            self.payloads.append(payload)

    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as listener:
        listener.bind(("127.0.0.1", 0))
        listener.settimeout(1.0)
        port = listener.getsockname()[1]
        monkeypatch.setenv("ERIS_LIVE2D_DIRECT_MIRROR", "1")
        monkeypatch.setenv("ERIS_LIVE2D_PORT", str(port))
        monkeypatch.setattr("runtime.stage_event_bus.stage_protocol_enabled", lambda: True)

        transport = FakeTransport()
        bus = StageEventBus(transport=transport)
        assert bus.emit(StageProtocolEvent(type="audio_playback_started", tts_text="Chào.")) is True

        raw, _ = listener.recvfrom(65507)
        mirrored = json.loads(raw.decode("utf-8"))
        assert mirrored["protocol"] == "eris.stage"
        assert mirrored["type"] == "audio_playback_started"
        assert mirrored["event_id"]
        assert transport.payloads[0]["event_id"] == mirrored["event_id"]


def test_duplicate_stage_event_reports_rejected():
    state = Live2DStageState()
    payload = {
        "protocol": "eris.stage",
        "type": "audio_playback_started",
        "event_id": "evt_duplicate_acceptance_contract",
        "tts_text": "Xin chào.",
    }
    assert state.handle_payload(payload) is True
    assert state.handle_payload(dict(payload)) is False


def test_live2d_model_hit_test_uses_precise_parts() -> None:
    from live2d_body.renderer import Live2DRenderer

    class FakeModel:
        def HitPart(self, x: float, y: float):
            assert (x, y) == (120.0, 240.0)
            return ["PartHair"]

        def HitTest(self, name: str, x: float, y: float) -> bool:
            raise AssertionError("HitTest fallback should not run when HitPart succeeds")

    assert Live2DRenderer._model_hit_test(FakeModel(), 120.0, 240.0) is True


def test_live2d_model_hit_test_passes_through_empty_alpha_area() -> None:
    from live2d_body.renderer import Live2DRenderer

    class FakeModel:
        def HitPart(self, _x: float, _y: float):
            return []

        def HitTest(self, _name: str, _x: float, _y: float) -> bool:
            return False

    assert Live2DRenderer._model_hit_test(FakeModel(), 15.0, 20.0) is False


def test_live2d_model_hit_test_ignores_generic_skinning_parts() -> None:
    from live2d_body.renderer import Live2DRenderer

    class FakeModel:
        def HitPart(self, _x: float, _y: float):
            return ["ArtMesh_Skinning", "ArtMesh0_Skinning", "trong"]

        def HitTest(self, _name: str, _x: float, _y: float) -> bool:
            return False

    assert Live2DRenderer._model_hit_test(FakeModel(), 40.0, 60.0) is False


def test_live2d_model_hit_test_uses_body_fallback_when_sdk_returns_no_parts() -> None:
    from live2d_body.renderer import Live2DRenderer

    class FakeModel:
        def HitPart(self, _x: float, _y: float):
            return []

        def HitTest(self, _name: str, _x: float, _y: float) -> bool:
            return False

    assert Live2DRenderer._model_hit_test(
        FakeModel(),
        260.0,
        477.0,
        window_width=520,
        window_height=900,
    ) is True
    assert Live2DRenderer._model_hit_test(
        FakeModel(),
        30.0,
        30.0,
        window_width=520,
        window_height=900,
    ) is False


def test_live2d_glfw_passthrough_toggles_only_on_state_change() -> None:
    from live2d_body.renderer import Live2DRenderer

    class FakeGlfw:
        MOUSE_PASSTHROUGH = 0x0002000D
        TRUE = 1
        FALSE = 0

        def __init__(self) -> None:
            self.calls = []

        def set_window_attrib(self, window, attrib, value) -> None:
            self.calls.append((window, attrib, value))

    renderer = object.__new__(Live2DRenderer)
    renderer._mouse_passthrough_active = False
    renderer._mouse_passthrough_supported = True
    glfw = FakeGlfw()

    assert renderer._set_mouse_passthrough(glfw, "window", True) is True
    assert renderer._set_mouse_passthrough(glfw, "window", True) is True
    assert renderer._set_mouse_passthrough(glfw, "window", False) is True
    assert glfw.calls == [
        ("window", glfw.MOUSE_PASSTHROUGH, glfw.TRUE),
        ("window", glfw.MOUSE_PASSTHROUGH, glfw.FALSE),
    ]


def test_godot_overlay_uses_region_passthrough_not_full_window_capture() -> None:
    body_dir = _get_body_dir()
    source = (body_dir / "UdpListener.cs").read_text(encoding="utf-8")
    live2d_source = (body_dir / "UdpListener.Live2D.cs").read_text(encoding="utf-8")
    assert "[Export] public bool EnableDesktopOverlayWindow = true;" in source
    assert "[Export] public bool KeepWindowAlwaysOnTop = true;" in source
    assert "[Export] public bool UseBorderlessWindow = true;" in source
    assert "[Export] public bool UseTransparentWindow = true;" in source
    assert "[Export] public bool FillScreenOverlay = true;" in source
    assert "DisplayServer.MouseGetPosition()" in source
    assert "ApplyMouseCaptureRect" in source
    assert "TryGetInteractivePanelCaptureRect" in source
    assert "TryBuildCharacterMouseCaptureRect" in source
    assert "MergeVisibleControlRect" in source
    assert "ApplyLive2DMousePassthrough" in live2d_source
    assert "DisplayServer.ScreenGetUsableRect" in source
    scene = (body_dir / "udpListener.tscn").read_text(encoding="utf-8")
    root_block = scene.split('[node name="Control" type="Control" parent="CanvasLayer"', 1)[1].split("[node", 1)[0]
    assert "mouse_filter = 2" in root_block
    assert "NativeOverlayMousePassthrough" not in source
    assert "ConfigureSafeWindowedMode" not in source


def test_windows_overlay_uses_native_global_clickthrough_without_clipping_rendering() -> None:
    body_dir = _get_body_dir()
    source = (body_dir / "UdpListener.cs").read_text(encoding="utf-8")
    update_body = source.split("private void UpdateMousePassthrough()", 1)[1].split("private void ApplyFullMouseCapture()", 1)[0]

    assert "WS_EX_LAYERED" in source
    assert "WS_EX_TRANSPARENT" in source
    assert "WindowGetNativeHandle(DisplayServer.HandleType.WindowHandle)" in source
    assert "RuntimeInformation.IsOSPlatform(OSPlatform.Windows)" in update_body
    assert "ApplyWindowsGlobalMousePassthrough" in update_body
    # Windows must leave the polygon path before ApplyMouseCaptureRect, otherwise
    # Godot clips the transparent overlay to the input polygon.
    assert update_body.index("ApplyWindowsGlobalMousePassthrough") < update_body.index("ApplyMouseCaptureRect")


def test_godot_overlay_does_not_use_offscreen_polygon_passthrough() -> None:
    body_dir = _get_body_dir()
    source = (body_dir / "UdpListener.cs").read_text(encoding="utf-8")
    assert "DisplayServer.WindowSetMousePassthrough(Array.Empty<Vector2>())" in source
    assert "OverlayClickThroughPolygon" not in source
    assert "RectToPassthroughPolygon" not in source


def test_godot_overlay_defaults_to_desktop_overlay_mode() -> None:
    body_dir = _get_body_dir()
    source = (body_dir / "UdpListener.cs").read_text(encoding="utf-8")
    assert "[Export] public bool EnableDesktopOverlayWindow = true;" in source
    assert "[Export] public bool KeepWindowAlwaysOnTop = true;" in source
    assert "[Export] public bool UseBorderlessWindow = true;" in source
    assert "[Export] public bool UseTransparentWindow = true;" in source
    assert "[Export] public bool FillScreenOverlay = true;" in source
    assert "DisplayServer.WindowSetFlag(DisplayServer.WindowFlags.MousePassthrough, false)" in source


def test_opening_chat_panel_hides_speech_bubbles_first() -> None:
    body_dir = _get_body_dir()
    source = (body_dir / "UdpListener.cs").read_text(encoding="utf-8")
    body = source.split("private void ToggleChatPanel()", 1)[1].split("private void CloseChatPanel()", 1)[0]
    assert "HideAllSpeechBubbles();" in body
    assert "_bubbleHideAtMs = 0;" in body
    assert body.index("HideAllSpeechBubbles();") < body.index("_chatPanel.Visible = true;")


def test_transient_overlays_do_not_expand_mouse_capture_regions() -> None:
    body_dir = _get_body_dir()
    body_source = (body_dir / "UdpListener.cs").read_text(encoding="utf-8")
    live2d_source = (body_dir / "UdpListener.Live2D.cs").read_text(encoding="utf-8")

    character_capture = body_source.split("private bool TryBuildCharacterMouseCaptureRect", 1)[1].split(
        "private static bool IsVisibleControlUnderMouse",
        1,
    )[0]
    live2d_capture = live2d_source.split("private bool ApplyLive2DMousePassthrough()", 1)[1].split(
        "private void ShutdownLive2DMode()",
        1,
    )[0]

    for decorative_overlay in ("ActiveSpeechBubble()", "_runtimePresenceIndicator", "_tutorPanel"):
        assert decorative_overlay not in character_capture
    for decorative_overlay in ("Live2DSpeechBubbleControl()", "_runtimePresenceIndicator", "_tutorPanel"):
        assert decorative_overlay not in live2d_capture


def test_live2d_stale_mouse_press_is_replayed_below_window() -> None:
    from types import SimpleNamespace

    from live2d_body.renderer import Live2DRenderer
    from live2d_body.window_bridge import WindowGeometry

    class FakeGlfw:
        MOUSE_BUTTON_LEFT = 0
        MOUSE_BUTTON_RIGHT = 1
        MOUSE_BUTTON_MIDDLE = 2

    class FakeBridge:
        def __init__(self) -> None:
            self.replayed = []

        @staticmethod
        def cursor_screen():
            return (180.0, 160.0)

        @staticmethod
        def geometry(_glfw, _window):
            return WindowGeometry(10, 10, 100, 100)

        def replay_mouse_button_down(self, button: str) -> bool:
            self.replayed.append(button)
            return True

    renderer = object.__new__(Live2DRenderer)
    renderer.config = SimpleNamespace(transparent=True)
    renderer._mouse_passthrough_supported = True
    renderer._mouse_replay_guard_until = 0.0
    renderer._dragging_window = False
    renderer._bridge = FakeBridge()
    passthrough = []
    renderer._set_mouse_passthrough = lambda _glfw, _window, enabled: passthrough.append(enabled) or True

    assert renderer._forward_stale_mouse_press(FakeGlfw(), "window", object(), 0) is True
    assert passthrough == [True]
    assert renderer._bridge.replayed == ["left"]


def test_position_window_selects_monitor_covering_xy_coordinates() -> None:
    from types import SimpleNamespace
    from live2d_body.renderer import Live2DRenderer

    class FakeMonitor:
        def __init__(self, name: str, pos: tuple[int, int], size: tuple[int, int]) -> None:
            self.name = name
            self.pos = pos
            self.size = size

    class FakeMode:
        def __init__(self, width: int, height: int) -> None:
            self.size = SimpleNamespace(width=width, height=height)

    class FakeGlfw:
        def __init__(self, monitors: list[FakeMonitor], primary: FakeMonitor) -> None:
            self.monitors = monitors
            self.primary = primary
            self.set_pos_calls: list[tuple[int, int]] = []

        def get_monitors(self) -> list[FakeMonitor]:
            return self.monitors

        def get_primary_monitor(self) -> FakeMonitor:
            return self.primary

        def get_monitor_pos(self, monitor: FakeMonitor) -> tuple[int, int]:
            return monitor.pos

        def get_video_mode(self, monitor: FakeMonitor) -> FakeMode:
            return FakeMode(monitor.size[0], monitor.size[1])

        def set_window_pos(self, _window: Any, x: int, y: int) -> None:
            self.set_pos_calls.append((x, y))

    m0 = FakeMonitor("primary", (0, 0), (1920, 1080))
    m1 = FakeMonitor("secondary", (1920, 0), (2560, 1440))
    glfw = FakeGlfw([m0, m1], m0)

    renderer = object.__new__(Live2DRenderer)
    renderer.config = SimpleNamespace(x=2200, y=100, width=520, height=900)

    screen_size, window_pos = renderer._position_window(glfw, "dummy_window")

    assert screen_size == (2560, 1440)
    assert window_pos == (2200, 100)
    assert glfw.set_pos_calls == [(2200, 100)]


def test_position_window_falls_back_to_primary_when_no_coords() -> None:
    from types import SimpleNamespace
    from live2d_body.renderer import Live2DRenderer

    class FakeMonitor:
        def __init__(self, name: str, pos: tuple[int, int], size: tuple[int, int]) -> None:
            self.name = name
            self.pos = pos
            self.size = size

    class FakeMode:
        def __init__(self, width: int, height: int) -> None:
            self.size = SimpleNamespace(width=width, height=height)

    class FakeGlfw:
        def __init__(self, monitors: list[FakeMonitor], primary: FakeMonitor) -> None:
            self.monitors = monitors
            self.primary = primary
            self.set_pos_calls: list[tuple[int, int]] = []

        def get_monitors(self) -> list[FakeMonitor]:
            return self.monitors

        def get_primary_monitor(self) -> FakeMonitor:
            return self.primary

        def get_monitor_pos(self, monitor: FakeMonitor) -> tuple[int, int]:
            return monitor.pos

        def get_video_mode(self, monitor: FakeMonitor) -> FakeMode:
            return FakeMode(monitor.size[0], monitor.size[1])

        def set_window_pos(self, _window: Any, x: int, y: int) -> None:
            self.set_pos_calls.append((x, y))

    m0 = FakeMonitor("primary", (0, 0), (1920, 1080))
    m1 = FakeMonitor("secondary", (1920, 0), (2560, 1440))
    glfw = FakeGlfw([m0, m1], m0)

    renderer = object.__new__(Live2DRenderer)
    renderer.config = SimpleNamespace(x=None, y=None, width=520, height=900)

    screen_size, window_pos = renderer._position_window(glfw, "dummy_window")

    assert screen_size == (1920, 1080)
    assert window_pos == (1370, 110)
    assert glfw.set_pos_calls == [(1370, 110)]


def test_position_window_handles_negative_monitor_coordinates() -> None:
    from types import SimpleNamespace
    from live2d_body.renderer import Live2DRenderer

    class FakeMonitor:
        def __init__(self, name: str, pos: tuple[int, int], size: tuple[int, int]) -> None:
            self.name = name
            self.pos = pos
            self.size = size

    class FakeMode:
        def __init__(self, width: int, height: int) -> None:
            self.size = SimpleNamespace(width=width, height=height)

    class FakeGlfw:
        def __init__(self, monitors: list[FakeMonitor], primary: FakeMonitor) -> None:
            self.monitors = monitors
            self.primary = primary
            self.set_pos_calls: list[tuple[int, int]] = []

        def get_monitors(self) -> list[FakeMonitor]:
            return self.monitors

        def get_primary_monitor(self) -> FakeMonitor:
            return self.primary

        def get_monitor_pos(self, monitor: FakeMonitor) -> tuple[int, int]:
            return monitor.pos

        def get_video_mode(self, monitor: FakeMonitor) -> FakeMode:
            return FakeMode(monitor.size[0], monitor.size[1])

        def set_window_pos(self, _window: Any, x: int, y: int) -> None:
            self.set_pos_calls.append((x, y))

    m0 = FakeMonitor("primary", (0, 0), (1920, 1080))
    m1 = FakeMonitor("secondary_left", (-1920, -1), (1920, 1080))
    glfw = FakeGlfw([m0, m1], m0)

    renderer = object.__new__(Live2DRenderer)
    renderer.config = SimpleNamespace(x=-1500, y=100, width=520, height=900)

    screen_size, window_pos = renderer._position_window(glfw, "dummy_window")

    assert screen_size == (1920, 1080)
    assert window_pos == (-1500, 100)
    assert glfw.set_pos_calls == [(-1500, 100)]


def test_position_window_clamps_offscreen_coordinates_to_monitor() -> None:
    from types import SimpleNamespace
    from live2d_body.renderer import Live2DRenderer

    class FakeMonitor:
        def __init__(self, name: str, pos: tuple[int, int], size: tuple[int, int]) -> None:
            self.name = name
            self.pos = pos
            self.size = size

    class FakeMode:
        def __init__(self, width: int, height: int) -> None:
            self.size = SimpleNamespace(width=width, height=height)

    class FakeGlfw:
        def __init__(self, monitors: list[FakeMonitor], primary: FakeMonitor) -> None:
            self.monitors = monitors
            self.primary = primary
            self.set_pos_calls: list[tuple[int, int]] = []

        def get_monitors(self) -> list[FakeMonitor]:
            return self.monitors

        def get_primary_monitor(self) -> FakeMonitor:
            return self.primary

        def get_monitor_pos(self, monitor: FakeMonitor) -> tuple[int, int]:
            return monitor.pos

        def get_video_mode(self, monitor: FakeMonitor) -> FakeMode:
            return FakeMode(monitor.size[0], monitor.size[1])

        def set_window_pos(self, _window: Any, x: int, y: int) -> None:
            self.set_pos_calls.append((x, y))

    m0 = FakeMonitor("primary", (0, 0), (1920, 1080))
    m1 = FakeMonitor("secondary", (1920, 0), (2560, 1440))
    glfw = FakeGlfw([m0, m1], m0)

    renderer = object.__new__(Live2DRenderer)
    renderer.config = SimpleNamespace(x=5000, y=5000, width=520, height=900)

    screen_size, window_pos = renderer._position_window(glfw, "dummy_window")

    # Clamped within m1 bounds: max_x = 1920 + 2560 - 520 = 3960, max_y = 0 + 1440 - 900 = 540
    assert screen_size == (2560, 1440)
    assert window_pos == (3960, 540)
    assert glfw.set_pos_calls == [(3960, 540)]

