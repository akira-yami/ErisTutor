# -*- coding: utf-8 -*-
"""Word-based presets with the authorized 05/11 expression rescue overrides."""

from pathlib import Path
import json
from types import SimpleNamespace

import pytest

from live2d_body.asset_playback import EXPRESSION_ALIASES, NativeAssetPlayback
from live2d_body.layer_visibility import Live2DLayerVisibility
from live2d_body.stage_state import Live2DStageState

ASSETS = Path(__file__).resolve().parents[1] / "assets/live2d/infj"
EXPECTED = {
    "smirk_minor": {"ParamMouthForm": -0.3, "ParamErisMouthCornerL": 1.0},
    "smirk_disdain": {"ParamEyeLOpen": 0.56, "ParamEyeROpen": 0.78, "ParamMouthForm": -0.3, "ParamErisMouthCornerL": 1.0},
    "blush_affection": {"ParamCheek": 1.0},
    "cat_mouth": {"ParamMouthForm": -1.0},
    "sad_empathy": {"ParamMouthForm": -0.3, "ParamBrowLAngle": 1.0, "ParamBrowRAngle": 1.0},
    "wink_correct": {"ParamEyeLOpen": 0.0, "ParamEyeROpen": 1.0, "ParamMouthForm": 0.0, "ParamBrowLAngle": -1.0, "ParamBrowRAngle": -1.0},
    "teehee": {"ParamEyeLOpen": 0.0, "ParamEyeROpen": 1.0, "ParamMouthForm": 0.0, "ParamBrowLAngle": -1.0, "ParamBrowRAngle": -1.0, "ParamErisTongue": 0.6},
    "yan_warning": {"ParamMouthForm": -0.3, "ParamEyeHighlight": 0.0},
    "lazy_deadpan": {"ParamEyeLOpen": 0.48, "ParamEyeROpen": 0.52,
                     "ParamMouthForm": -0.15, "ParamBrowLAngle": 0.0, "ParamBrowRAngle": 0.10},
    "wink_correct_right": {"ParamEyeLOpen": 1.0, "ParamEyeROpen": 0.0, "ParamMouthForm": 0.0, "ParamBrowLAngle": -1.0, "ParamBrowRAngle": -1.0},
    "teehee_right": {"ParamEyeLOpen": 1.0, "ParamEyeROpen": 0.0, "ParamMouthForm": 0.0, "ParamBrowLAngle": -1.0, "ParamBrowRAngle": -1.0, "ParamErisTongue": 0.6},
}


@pytest.mark.parametrize("name,expected", EXPECTED.items())
def test_rescued_preset_parameters_and_model_registration(name, expected):
    refs = json.loads((ASSETS / "infj.model3.json").read_text(encoding="utf-8"))["FileReferences"]["Expressions"]
    assert {item["Name"]: item["File"] for item in refs}[name] == f"expressions/{name}.exp3.json"
    asset = json.loads((ASSETS / "expressions" / f"{name}.exp3.json").read_text(encoding="utf-8"))
    assert {item["Id"]: item["Value"] for item in asset["Parameters"]} == expected
    for param in asset["Parameters"]:
        # Open eyelids must preserve the native blink; Overwrite is reserved
        # for a deliberately closed wink eye and the authored non-eye channels.
        blend = "Multiply" if param["Id"] in {"ParamEyeLOpen", "ParamEyeROpen"} and param["Value"] > 0 else "Overwrite"
        assert param["Blend"] == blend
    assert "ParamMouthOpenY" not in expected  # TTS nắm quyền mouth-open khi nói.
    assert EXPRESSION_ALIASES[name] == name


@pytest.mark.parametrize("name", list(EXPECTED))
def test_stage_accepts_new_emotion_and_idle_mouth_stays_closed(monkeypatch, name):
    monkeypatch.setattr("live2d_body.stage_state.time.monotonic", lambda: 50.0)
    stage = Live2DStageState(idle_seed=0)
    assert stage.handle_payload({"protocol":"eris.stage", "type":"stage_segment", "emotion":name})
    result = stage.advance(0, now=50.0)
    assert result.emotion == name
    assert result.parameters["ParamMouthOpenY"] == 0
    assert result.explicit_emotion_weight > 0.9


@pytest.mark.parametrize("name,closed", [("wink_correct", "left"),("teehee", "left"),
                                           ("wink_correct_right", "right"),("teehee_right", "right")])
def test_wink_targets_correct_eye(monkeypatch, name, closed):
    monkeypatch.setattr("live2d_body.stage_state.time.monotonic", lambda: 50.0)
    stage = Live2DStageState(idle_seed=0)
    stage.handle_payload({"protocol":"eris.stage", "type":"stage_segment", "emotion":name})
    result = stage.advance(0, now=50.0)
    assert (result.eye_open_left if closed=="left" else result.eye_open_right) == 0.0


def test_native_playback_uses_asset_for_every_new_emotion():
    class FakeModel:
        def __init__(self): self.expression = None
        def SetExpression(self, value): self.expression = value
        def StopAllMotions(self): pass
        def StartMotion(self, *args): pass
    p = NativeAssetPlayback(ASSETS / "infj.model3.json")
    m = FakeModel()
    for index, name in enumerate(EXPECTED):
        p.advance(m, SimpleNamespace(emotion=name, speaking=True, motion="still",
                                     idle_action="settle", last_event_type="stage_segment",
                                     last_event_at=index+1))
        assert m.expression == name


def test_visibility_toggles_only_verified_parts_and_restores():
    class FakeModel:
        def __init__(self): self.writes = []
        def GetPartIds(self): return ["Part", "Mado", "ArtMesh7", "ArtMesh16"]
        def SetPartOpacity(self, index, opacity): self.writes.append((index, opacity))
    adapter=Live2DLayerVisibility()
    model=FakeModel()
    adapter.apply(model,emotion="blush_affection",weight=1)
    assert model.writes[-3:] == [(1,1),(2,1),(3,1)]
    adapter.apply(model,emotion="yan_warning",weight=1)
    assert model.writes[-3:] == [(1,0),(2,0),(3,0)]
    adapter.apply(model,emotion="neutral",weight=0)
    assert model.writes[-3:] == [(1,0),(2,1),(3,1)]
    adapter.apply(model,emotion="blush_affection",weight=1,terminal=True)
    assert model.writes[-3:] == [(1,0),(2,1),(3,1)]


def test_layer_adapter_never_hides_unrelated_parts_when_mesh_has_no_part():
    class FakeModel:
        def GetPartIds(self): return ["Part", "Part2", "Part5"]
        def SetPartOpacity(self, *args): raise AssertionError("Không được ẩn nguyên mặt")
    Live2DLayerVisibility().apply(FakeModel(),emotion="lazy_deadpan",weight=1)
