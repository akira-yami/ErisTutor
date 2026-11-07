import sys
from types import SimpleNamespace

import pytest

from native.screen_capture import capture_screen


def test_capture_clips_region_without_desktop_fallback(monkeypatch):
    calls = []

    class Grabber:
        monitors = [{"left": -100, "top": 0, "width": 300, "height": 200}]

        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

        def grab(self, region):
            calls.append(region)
            return SimpleNamespace(size=(region["width"], region["height"]), rgb=b"fake")

    monkeypatch.setitem(sys.modules, "mss", SimpleNamespace(mss=Grabber))
    monkeypatch.setitem(sys.modules, "PIL", SimpleNamespace(
        Image=SimpleNamespace(frombytes=lambda mode, size, rgb: (mode, size, rgb)),
    ))
    assert capture_screen((-120, 10, 70, 40)) == ("RGB", (50, 40), b"fake")
    with pytest.raises(ValueError, match="capture_bounds_outside_desktop"):
        capture_screen((400, 20, 80, 80))
    assert calls == [{"left": -100, "top": 10, "width": 50, "height": 40}]
