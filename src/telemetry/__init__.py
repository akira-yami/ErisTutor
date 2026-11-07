try:
    from .system import TelemetryMixin
except Exception:  # pragma: no cover - dependency/platform fallback
    TelemetryMixin = None  # type: ignore

try:
    from .service import TelemetryService
except Exception:  # pragma: no cover - tests có thể thiếu mss/PIL trên Linux
    TelemetryService = None  # type: ignore

__all__ = ["TelemetryMixin", "TelemetryService"]
