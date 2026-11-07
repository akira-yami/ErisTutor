from types import SimpleNamespace

from live2d_body.renderer import Live2DRenderer
from live2d_body.stage_state import Live2DStageState
from live2d_body.udp_receiver import Live2DStageReceiver
from live2d_body.window_bridge import WindowGeometry


def test_ui_rects_replace_clear_and_keep_negative_desktop_coordinates():
    receiver = Live2DStageReceiver(Live2DStageState(), port=0)
    packet = {"protocol": "eris.live2d.control", "type": "set_input_exclusion_rects"}
    receiver._handle_renderer_control({**packet, "rects": [{"x": -1500, "y": 200, "width": 300, "height": 500}]})
    assert receiver.input_exclusion_rects == ((-1500, 200, 300, 500),)
    receiver._handle_renderer_control({**packet, "rects": []})
    assert receiver.input_exclusion_rects == ()


def test_ui_over_model_yields_mouse_and_restores_model_input_after_close():
    receiver = Live2DStageReceiver(Live2DStageState(), port=0)
    packet = {"protocol": "eris.live2d.control", "type": "set_input_exclusion_rects"}
    receiver._handle_renderer_control({**packet, "rects": [{"x": 100, "y": 100, "width": 300, "height": 500}]})
    renderer = object.__new__(Live2DRenderer)
    renderer.receiver = receiver
    renderer.config = SimpleNamespace(transparent=True)
    renderer._mouse_passthrough_supported = True
    renderer._dragging_window = False
    renderer._bridge = SimpleNamespace(cursor_screen=lambda: (200, 200))
    hit_calls = []
    renderer._model_hit_test = lambda *a, **kw: hit_calls.append(True) or True
    passthrough = []
    renderer._set_mouse_passthrough = lambda *a: passthrough.append(a[-1])
    geometry = WindowGeometry(0, 0, 520, 900)
    renderer._update_mouse_passthrough(None, None, None, geometry)
    assert passthrough == [True]
    assert hit_calls == []
    receiver._handle_renderer_control({**packet, "rects": []})
    renderer._update_mouse_passthrough(None, None, None, geometry)
    assert passthrough == [True, False]
    assert hit_calls == [True]


def test_stale_press_on_ui_replays_even_when_model_is_opaque():
    renderer = object.__new__(Live2DRenderer)
    renderer.receiver = SimpleNamespace(input_exclusion_rects=((100, 100, 300, 500),))
    renderer.config = SimpleNamespace(transparent=True)
    renderer._mouse_passthrough_supported = True
    renderer._dragging_window = False
    renderer._mouse_replay_guard_until = 0
    replayed = []
    renderer._bridge = SimpleNamespace(
        cursor_screen=lambda: (200, 200),
        geometry=lambda *a: WindowGeometry(0, 0, 520, 900),
        replay_mouse_button_down=lambda b: replayed.append(b) or True,
    )
    renderer._model_hit_test = lambda *a, **kw: True
    renderer._set_mouse_passthrough = lambda *a: None
    glfw = SimpleNamespace(MOUSE_BUTTON_LEFT=0, MOUSE_BUTTON_RIGHT=1, MOUSE_BUTTON_MIDDLE=2)
    assert renderer._forward_stale_mouse_press(glfw, None, None, 0)
    assert replayed == ["left"]


def test_live2d_apply_scale_delta_and_clamps(tmp_path, monkeypatch):
    monkeypatch.setenv("ERIS_STATE_DIR", str(tmp_path))
    renderer = object.__new__(Live2DRenderer)
    renderer._runtime_scale = 1.0
    scale_calls = []
    model = SimpleNamespace(SetScale=lambda s: scale_calls.append(s))

    # Zoom in
    renderer._apply_scale_delta(model, 0.05)
    assert renderer._runtime_scale == 1.05
    assert scale_calls[-1] == 1.05

    # Zoom beyond max (3.0)
    renderer._apply_scale_delta(model, 5.0)
    assert renderer._runtime_scale == 3.0
    assert scale_calls[-1] == 3.0

    # Zoom beyond min (0.2)
    renderer._apply_scale_delta(model, -5.0)
    assert renderer._runtime_scale == 0.2
    assert scale_calls[-1] == 0.2

    # Check persistence file
    saved_file = tmp_path / "live2d_scale.txt"
    assert saved_file.exists()
    assert float(saved_file.read_text().strip()) == 0.2


def test_udp_receiver_handles_scale_commands():
    receiver = Live2DStageReceiver(Live2DStageState(), port=0)
    # adjust_scale
    packet_adjust = {"protocol": "eris.live2d.control", "type": "adjust_scale", "delta": 0.05}
    assert receiver._handle_renderer_control(packet_adjust)
    assert receiver.consume_scale_delta() == 0.05
    assert receiver.consume_scale_delta() == 0.0

    # set_scale
    packet_set = {"protocol": "eris.live2d.control", "type": "set_scale", "scale": 1.5}
    assert receiver._handle_renderer_control(packet_set)
    assert receiver.consume_scale() == 1.5
    assert receiver.consume_scale() is None
