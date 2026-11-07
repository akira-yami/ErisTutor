# -*- coding: utf-8 -*-
"""Small logging boundary that never hides why diagnostics could not be emitted."""
from __future__ import annotations

import sys
from typing import Any


def safe_log(logger: Any, level: str, message: str, *args: Any) -> bool:
    """Best-effort logging with a stderr fallback and no broad silent handler."""
    callback = getattr(logger, str(level or "debug"), None)
    if not callable(callback):
        return False
    try:
        callback(message, *args)
        return True
    except (AttributeError, TypeError, ValueError, OSError, RuntimeError) as exc:
        try:
            rendered = message % args if args else message
        except (TypeError, ValueError):
            rendered = str(message)
        try:
            sys.stderr.write(
                f"[diagnostic-log-failed] level={level} error={type(exc).__name__} message={rendered[:240]}\n"
            )
        except (AttributeError, TypeError, ValueError, OSError):
            return False
        return False


__all__ = ["safe_log"]
