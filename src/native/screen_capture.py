"""Capture pixels locally with optional MSS and Pillow dependencies."""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from PIL.Image import Image


def capture_screen(bounds: tuple[int, int, int, int] | None = None) -> Image:
    """Capture a desktop region clipped to the virtual screen, or the whole screen."""
    import mss
    from PIL import Image

    with mss.mss() as grabber:
        desktop = grabber.monitors[0]
        region = desktop
        if bounds is not None:
            left, top, width, height = bounds
            right = min(left + width, desktop["left"] + desktop["width"])
            bottom = min(top + height, desktop["top"] + desktop["height"])
            left = max(left, desktop["left"])
            top = max(top, desktop["top"])
            if right <= left or bottom <= top:
                raise ValueError("capture_bounds_outside_desktop")
            region = {"left": left, "top": top, "width": right - left, "height": bottom - top}
        shot = grabber.grab(region)
        return Image.frombytes("RGB", shot.size, shot.rgb)
