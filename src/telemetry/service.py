import base64
import io

import mss
from PIL import Image

from cortex.se import SeLens
from cortex.contracts import TelemetrySnapshot

from .system import TelemetryMixin


class TelemetryService(TelemetryMixin):
    def __init__(self):
        self.se_lens = SeLens()

    def capture_snapshot(self, *, mood_score: int, inferred_pos_x: float) -> TelemetrySnapshot:
        raw = self.capture_raw_telemetry(
            mood_score=mood_score,
            inferred_pos_x=inferred_pos_x,
        )
        return self.se_lens.build_snapshot(raw)

    def format_snapshot(self, snapshot: TelemetrySnapshot) -> str:
        return self.se_lens.format_snapshot(snapshot)

    def format_directive_snapshot(self, snapshot: TelemetrySnapshot) -> str:
        return self.se_lens.format_directive_snapshot(snapshot)

    def capture_machine_context(self, *, mood_score: int, inferred_pos_x: float) -> dict[str, object]:
        snapshot = self.capture_snapshot(
            mood_score=mood_score,
            inferred_pos_x=inferred_pos_x,
        )
        return snapshot.to_machine_context()

    def get_system_telemetry(self, *, mood_score: int, inferred_pos_x: float):
        snapshot = self.capture_snapshot(
            mood_score=mood_score,
            inferred_pos_x=inferred_pos_x,
        )
        return self.format_snapshot(snapshot)

    def capture_screen_frame_data_uri(self, *, max_side: int = 960, quality: int = 82) -> str:
        with mss.mss() as sct:
            monitor = sct.monitors[0]
            raw = sct.grab(monitor)

        image = Image.frombytes("RGB", raw.size, raw.rgb)
        width, height = image.size
        longest = max(width, height)
        if longest > max_side:
            scale = max_side / float(longest)
            image = image.resize(
                (max(1, int(width * scale)), max(1, int(height * scale))),
                Image.Resampling.LANCZOS,
            )

        buffer = io.BytesIO()
        image.save(buffer, format="JPEG", quality=quality, optimize=True)
        encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
        return f"data:image/jpeg;base64,{encoded}"
