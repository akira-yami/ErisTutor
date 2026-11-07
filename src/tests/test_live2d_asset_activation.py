"""Asset contracts and actual native playback, without network or audio output."""
from dataclasses import replace
import json
from pathlib import Path

import pytest

from live2d_body.config import Live2DBodyConfig
from live2d_body.gaze import GazeSample
from live2d_body.renderer import Live2DRenderer
from live2d_body.stage_state import Live2DSnapshot


ASSETS = Path(__file__).resolve().parents[1] / "assets/live2d/infj"


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def test_config_defaults_drive_wider_coherent_cursor_gaze(monkeypatch):
    for key in ("X", "Y", "INNER_RATIO"):
        monkeypatch.delenv("ERIS_LIVE2D_EYE_" + ("STRENGTH_" + key if key != "INNER_RATIO" else key), raising=False)
    for config in (Live2DBodyConfig(ASSETS / "infj.model3.json"), Live2DBodyConfig.from_env()):
        renderer = Live2DRenderer(config, None, None)
        for _ in range(120):
            gaze = renderer._gaze.update(cursor_screen=(10000, -10000), face_screen=(0, 0), span_x=100, span_y=100, dt=1 / 60)
        assert gaze.left_x == pytest.approx(.44, abs=.002)
        assert gaze.right_x == pytest.approx(-.44 * .82, abs=.002)
        assert abs(gaze.left_y) == pytest.approx(.30, abs=.002)


@pytest.mark.parametrize("name", ["glance_left", "glance_right", "ponder_left", "ponder_right", "scan_short"])
def test_authored_horizontal_gaze_moves_both_eyes_in_one_screen_direction(name):
    curves = {c["Id"]: c["Segments"] for c in read_json(ASSETS / f"motions/{name}.motion3.json")["Curves"]}
    left, right = curves["eyeballxl"], curves["eyeballxr"]
    # Assets use linear segments: the native-verified per-eye X axes oppose.
    for offset in range(4, len(left), 3):
        assert left[offset] * right[offset] < 0 or left[offset] == right[offset] == 0
    if name.endswith("left"):
        assert left[4] < 0 < right[4]
    elif name.endswith("right"):
        assert right[4] < 0 < left[4]


def test_authored_head_targets_are_visible_parameter_units_with_neutral_endpoints():
    curves = {c["Id"]: c["Segments"] for c in read_json(ASSETS / "motions/glance_left.motion3.json")["Curves"]}
    assert curves["ParamAngleX"][7] == pytest.approx(-6.2)
    assert curves["ParamAngleZ"][7] == pytest.approx(-1.68)
    for path in (ASSETS / "motions").glob("*.json"):
        motion = read_json(path)
        assert motion["Meta"]["Loop"] is False
        for curve in motion["Curves"]:
            assert curve["Id"] != "ParamMouthOpenY"
            assert curve["Segments"][1] == curve["Segments"][-1] == 0
            if curve["Id"] in {"ParamAngleX", "ParamAngleZ"}:
                assert all(abs(v) <= 30 for v in curve["Segments"][4::3])


def test_physics_reads_each_hair_chain_vertex_and_normalizes_fifteen_units():
    setting = read_json(ASSETS / "infj.physics3.json")["PhysicsSettings"][0]
    outputs = {item["Destination"]["Id"]: item["VertexIndex"] for item in setting["Output"]}
    assert [outputs[f"Param_Angle_Rotation{i}"] for i in (5, 6, 7)] == [1, 2, 3]
    for domain in ("Position", "Angle"):
        assert setting["Normalization"][domain] == {"Minimum": -15, "Default": 0, "Maximum": 15}


def test_catalog_references_exist_and_never_animate_audio_mouth():
    refs = read_json(ASSETS / "infj.model3.json")["FileReferences"]
    for entry in refs["Expressions"]:
        assert (ASSETS / entry["File"]).is_file()
        assert "ParamMouthOpenY" not in {p["Id"] for p in read_json(ASSETS / entry["File"])["Parameters"]}
    for entries in refs["Motions"].values():
        for entry in entries:
            assert (ASSETS / entry["File"]).is_file()


@pytest.fixture
def native_model(monkeypatch):
    glfw = pytest.importorskip("glfw")
    live2d = pytest.importorskip("live2d.v3")
    assert glfw.init()
    glfw.window_hint(glfw.VISIBLE, glfw.FALSE)
    window = glfw.create_window(512, 768, "INFJ hidden contract", None, None)
    assert window
    glfw.make_context_current(window)
    live2d.init()
    live2d.glInit()
    model = live2d.LAppModel()
    model.LoadModelJson(str(ASSETS / "infj.model3.json"))
    model.Resize(512, 768)
    model.SetAutoBlinkEnable(False)
    model.SetAutoBreathEnable(False)
    clock = [100.0]
    model._lastFrame = clock[0]
    monkeypatch.setattr("live2d.v3.lapp_model.time.time", lambda: clock[0])
    renderer = Live2DRenderer(Live2DBodyConfig(ASSETS / "infj.model3.json"), None, None)
    renderer._parameter_indices = renderer._read_parameter_indices(model)
    renderer.available_parameters = set(renderer._parameter_indices)
    yield renderer, model, clock
    model.StopAllMotions()
    model.DestroyRenderer()
    live2d.dispose()
    glfw.destroy_window(window)
    glfw.terminate()


def snapshot(**kwargs):
    base = Live2DSnapshot(emotion="neutral", motion="stand", speaking=False, parameters={"ParamMouthOpenY": 0, "ParamAngleX": 0, "ParamAngleZ": 0})
    return replace(base, **kwargs)


def frames(native, pose, count=1, gaze=None):
    renderer, model, clock = native
    step = getattr(renderer, "_update_model_frame", None)
    assert callable(step), "renderer must compose baseline before native playback and audio mouth after it"
    for _ in range(count):
        clock[0] += 1 / 60
        step(model, pose, gaze or GazeSample(0, 0, 0, 0, 0), 1 / 60)
    return {p: model.GetParameterValue(i) for p, i in renderer._parameter_indices.items()}


def test_model_bundle_passes_native_consistency(native_model):
    refs = read_json(ASSETS / "infj.model3.json")["FileReferences"]
    assert refs["Textures"]
    assert all((ASSETS / texture).is_file() for texture in refs["Textures"])
    # The SDK resolves this name relative to the loaded model's directory.
    assert native_model[1].HasMocConsistencyFromFile(refs["Moc"])


def test_native_expression_survives_frame_composition_without_accumulating(native_model):
    pose = snapshot(emotion="warm_soft")
    values = frames(native_model, pose, 90)
    assert values["ParamMouthForm"] == pytest.approx(.1, abs=.025)
    values = frames(native_model, pose, 180)
    assert values["ParamMouthForm"] == pytest.approx(.1, abs=.025)
    assert values["ParamErisMouthCornerL"] == 0
    assert values["ParamMouthOpenY"] == 0


def test_native_motion_takes_temporary_gaze_ownership_and_finishes_once(native_model):
    pose = snapshot(motion="glance_left", last_event_type="stage_segment")
    gaze = GazeSample(.3, -.3, 0, 0, 0)
    values = frames(native_model, pose, 36, gaze)
    assert values["eyeballxl"] < -.2
    assert values["eyeballxr"] > .2
    assert values["ParamAngleX"] < -2
    values = frames(native_model, pose, 150, gaze)
    assert native_model[1].IsMotionFinished()
    assert values["eyeballxl"] == pytest.approx(.3, abs=.02)
    assert values["eyeballxr"] == pytest.approx(-.3, abs=.02)


def test_terminal_cancels_asset_expression_and_custom_mouth_but_not_audio_ownership(native_model):
    pose = snapshot(emotion="teasing", motion="tilt_head", speaking=True, last_event_type="audio_start", parameters={"ParamMouthOpenY": .57})
    values = frames(native_model, pose, 90)
    assert values["ParamErisTongue"] == pytest.approx(.876, abs=.001)
    assert values["ParamMouthOpenY"] == pytest.approx(.57)
    terminal = snapshot(emotion="teasing", motion="tilt_head", last_event_type="audio_done", parameters={"ParamMouthOpenY": .7, "ParamErisTongue": 1, "ParamErisLipPress": .8})
    values = frames(native_model, terminal, 1)
    assert values["ParamMouthOpenY"] == 0
    assert values["ParamErisTongue"] == 0
    assert values["ParamErisLipPress"] == 0
    values = frames(native_model, terminal, 120)
    assert values["ParamErisTongue"] == values["ParamErisLipPress"] == 0
    assert native_model[1].IsMotionFinished()


def test_same_motion_rearms_on_a_new_semantic_event_not_affect_updates(native_model):
    pose = snapshot(motion="glance_left", last_event_type="stage_segment", last_event_at=1)
    frames(native_model, pose, 150)
    assert native_model[1].IsMotionFinished()
    frames(native_model, replace(pose, last_event_at=2), 30)
    assert not native_model[1].IsMotionFinished()
    frames(native_model, replace(pose, last_event_at=2), 150)
    frames(native_model, replace(pose, last_event_type="affect_state", last_event_at=3), 1)
    assert native_model[1].IsMotionFinished()


def test_ignored_terminal_mirror_does_not_cancel_owned_audio_expression(native_model):
    pose = snapshot(emotion="teasing", motion="tilt_head", speaking=True, last_event_type="audio_start", last_event_at=1, parameters={"ParamMouthOpenY": .57})
    frames(native_model, pose, 60)
    ignored = replace(pose, last_event_type="audio_done", last_event_at=2)
    values = frames(native_model, ignored, 1)
    assert values["ParamErisTongue"] == pytest.approx(.876, abs=.001)
    assert values["ParamEyeLOpen"] < .74  # The expression was not cancelled.
    assert values["ParamMouthOpenY"] == pytest.approx(.57)


@pytest.mark.parametrize("event", ["audio_cancelled", "audio_failed", "stage_segment_cancelled", "degraded"])
def test_cancel_before_audio_resets_the_pending_native_expression(native_model, event):
    pose = snapshot(emotion="teasing", motion="tilt_head", last_event_type="stage_segment", last_event_at=1)
    frames(native_model, pose, 60)
    values = frames(native_model, replace(pose, last_event_type=event, last_event_at=2), 1)
    assert values["ParamErisTongue"] == 0


def test_runtime_thinking_small_activates_authored_ponder(native_model):
    pose = snapshot(emotion="thinking", motion="thinking_small", last_event_type="thinking_started")
    values = frames(native_model, pose, 40)
    assert values["eyeballxl"] < -.15
    assert values["eyeballxr"] > .15


def test_native_auto_blink_survives_baseline_and_eyelid_factors(native_model):
    native_model[1].SetAutoBlinkEnable(True)
    openings = []
    pose = snapshot(motion="still", eye_open_left=.8, eye_open_right=.8)
    for _ in range(900):
        openings.append(frames(native_model, pose)["ParamEyeLOpen"])
    assert max(openings) <= .801
    assert min(openings) < .2


@pytest.mark.parametrize("emotion,left,right", [
    ("smirk_disdain", .56, .78), ("lazy_deadpan", .48, .52),
    ("teasing", .62, .86), ("skeptical", .58, .90),
    ("amused", .72, .76), ("warm", .86, .88),
])
def test_expression_lids_keep_their_opening_and_native_blink(native_model, emotion, left, right):
    from live2d_body.stage_state import _EMOTION_TARGETS
    model = native_model[1]
    model.SetAutoBlinkEnable(True)
    target = _EMOTION_TARGETS[emotion]
    pose = snapshot(emotion=emotion, motion="still", parameters=target.parameters(),
                    eye_open_left=target.eye_open_left, eye_open_right=target.eye_open_right)
    values = []
    for _ in range(1080):
        result = frames(native_model, pose)
        values.append((result["ParamEyeLOpen"], result["ParamEyeROpen"]))
    for side, opening in enumerate((left, right)):
        samples = [value[side] for value in values[60:]]
        assert max(samples) == pytest.approx(opening, abs=.015)
        assert min(samples) < .1


@pytest.mark.parametrize("opening,tongue", [(0, 0), (.11, 0), (.405, .5), (.7, 1), (.84, 1)])
def test_v13_speech_owns_shape_and_tongue_while_retaining_expression(native_model, opening, tongue):
    pose = snapshot(emotion="teasing", motion="still", speaking=True,
                    parameters={"ParamMouthOpenY": opening}, mouth_shape=.65)
    values = frames(native_model, pose, 90)
    assert values["ParamMouthOpenY"] == pytest.approx(opening)
    assert values["ParamErisTongue"] == pytest.approx(tongue)
    assert values["ParamErisMouthCornerL"] == pytest.approx(.65 * min(1, opening / .20))
    assert values["ParamMouthForm"] > 0
