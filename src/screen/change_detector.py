from __future__ import annotations

"""Change detector rẻ để OCR chỉ chạy khi màn hình thật sự đổi."""

from dataclasses import dataclass
from PIL import Image, ImageChops, ImageStat


@dataclass(slots=True, frozen=True)
class ScreenChange:
    changed: bool
    ratio: float
    roi: tuple[int, int, int, int] | None
    full_refresh: bool


class ScreenChangeDetector:
    def __init__(self, *, threshold: float = 0.025, thumbnail_size: tuple[int, int] = (96, 54)) -> None:
        self.threshold = max(0.001, min(1.0, float(threshold)))
        self.thumbnail_size = thumbnail_size
        self._last_key = ""
        self._last_thumb: Image.Image | None = None

    def inspect(self, image: Image.Image, *, key: str) -> ScreenChange:
        thumb = image.convert("L").resize(self.thumbnail_size, Image.Resampling.BILINEAR)
        if self._last_thumb is None or key != self._last_key:
            self._last_key = key
            self._last_thumb = thumb
            return ScreenChange(True, 1.0, None, True)

        diff = ImageChops.difference(self._last_thumb, thumb)
        mean = float(ImageStat.Stat(diff).mean[0]) / 255.0
        bbox = diff.point(lambda p: 255 if p > 12 else 0).getbbox()
        self._last_thumb = thumb
        if mean < self.threshold or bbox is None:
            return ScreenChange(False, mean, None, False)

        sx = image.width / float(self.thumbnail_size[0])
        sy = image.height / float(self.thumbnail_size[1])
        left, top, right, bottom = bbox
        pad = 20
        roi = (
            max(0, int(left * sx) - pad),
            max(0, int(top * sy) - pad),
            min(image.width, int(right * sx) + pad),
            min(image.height, int(bottom * sy) + pad),
        )
        return ScreenChange(True, mean, roi, False)
