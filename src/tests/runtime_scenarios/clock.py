from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class FakeClock:
    value: float = 1_000.0

    def now(self) -> float:
        return float(self.value)

    def advance(self, seconds: float) -> float:
        self.value += max(0.0, float(seconds))
        return self.now()

    def set(self, timestamp: float) -> float:
        self.value = max(0.0, float(timestamp))
        return self.now()
