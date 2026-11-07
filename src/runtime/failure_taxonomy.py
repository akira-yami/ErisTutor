# -*- coding: utf-8 -*-
"""Context-aware failure classification for runtime process boundaries.

The classifier is deliberately conservative.  Domain code should still catch
specific exceptions where it can recover locally; this module exists for the
outer lifecycle/provider/persistence boundaries where an exception must be
recorded before it is allowed to propagate or degrade the subsystem.
"""
from __future__ import annotations

import json
from enum import Enum
from typing import Any

from runtime.subsystem_health import FailureKind


class FailureContext(str, Enum):
    STARTUP = "startup"
    SHUTDOWN = "shutdown"
    PROVIDER = "provider"
    PERSISTENCE = "persistence"
    EXTERNAL_DATA = "external_data"
    INVARIANT = "invariant"


def classify_failure(
    exc: BaseException,
    *,
    context: FailureContext = FailureContext.STARTUP,
    fallback: FailureKind = FailureKind.PROGRAMMER_ERROR,
) -> FailureKind:
    """Return a stable health failure kind for a boundary exception.

    Context wins over broad Python inheritance.  An ``OSError`` while writing a
    state file is a persistence failure, while the same exception around a
    provider socket is transient provider trouble.  Without context those two
    cases become one useless bucket, a small triumph of technically correct
    telemetry over actual diagnosis.
    """

    if context == FailureContext.PERSISTENCE:
        return FailureKind.PERSISTENCE_FAILURE
    if context == FailureContext.PROVIDER:
        if isinstance(exc, (TimeoutError, ConnectionError, OSError)):
            return FailureKind.PROVIDER_TRANSIENT
        return fallback
    if context == FailureContext.EXTERNAL_DATA:
        if isinstance(
            exc,
            (
                ValueError,
                UnicodeError,
                json.JSONDecodeError,
                FileNotFoundError,
                NotADirectoryError,
                PermissionError,
            ),
        ):
            return FailureKind.INVALID_EXTERNAL_DATA
        if isinstance(exc, OSError):
            return FailureKind.EXPECTED_DEGRADATION
        return fallback
    if context == FailureContext.INVARIANT:
        if isinstance(exc, (AssertionError, RuntimeError)):
            return FailureKind.INVARIANT_VIOLATION
        return fallback
    if context == FailureContext.SHUTDOWN:
        if isinstance(exc, (TimeoutError, OSError)):
            return FailureKind.EXPECTED_DEGRADATION
        return fallback
    if isinstance(exc, (TimeoutError, ConnectionError)):
        return FailureKind.PROVIDER_TRANSIENT
    if isinstance(exc, (AssertionError, TypeError, AttributeError, NameError)):
        return FailureKind.PROGRAMMER_ERROR
    return fallback


def failure_details(exc: BaseException, *, context: FailureContext) -> dict[str, Any]:
    return {
        "phase": context.value,
        "exception_type": type(exc).__name__,
    }


__all__ = ["FailureContext", "classify_failure", "failure_details"]
