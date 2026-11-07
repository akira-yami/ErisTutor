from __future__ import annotations

from runtime.diagnostic_logging import safe_log


class _GoodLogger:
    def __init__(self) -> None:
        self.calls = []

    def warning(self, message, *args):
        self.calls.append((message, args))


class _BrokenLogger:
    def debug(self, message, *args):
        raise ValueError("broken logger")


def test_safe_log_reports_success() -> None:
    logger = _GoodLogger()
    assert safe_log(logger, "warning", "hello %s", "eris") is True
    assert logger.calls == [("hello %s", ("eris",))]


def test_safe_log_absorbs_logging_boundary_failure(capsys) -> None:
    assert safe_log(_BrokenLogger(), "debug", "hello %s", "eris") is False
    assert "diagnostic-log-failed" in capsys.readouterr().err
