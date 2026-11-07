# -*- coding: utf-8 -*-
"""Durable checkpoint for provider admission state.

PDF annotation
--------------
Pages 6-10 and 15-19 describe the inference broker as a control plane rather
than a process-local helper.  A process restart must therefore not erase a
provider cooldown or a live ``remaining/reset`` window and immediately create
a fresh burst.  This module persists only quota/admission metadata.  It never
stores prompts, responses, API keys or user content.
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping


_SCHEMA = "eris.inference_broker_state.v1"
_ALLOWED_KINDS = ("rpm", "tpm", "itpm", "otpm")


@dataclass(frozen=True, slots=True)
class BrokerStateSnapshot:
    cooldown_until: dict[str, float]
    dynamic_limits: dict[str, dict[str, int]]
    header_remaining: dict[str, dict[str, int]]
    header_reset_at: dict[str, dict[str, float]]
    idempotency_tombstones: dict[str, float]
    saved_at: float = 0.0


class BrokerStateStore:
    """Atomic, schema-validated JSON storage with write debouncing."""

    def __init__(self, path: str | os.PathLike[str], *, min_write_interval_s: float = 0.5) -> None:
        self.path = Path(path).expanduser()
        self.min_write_interval_s = max(0.0, float(min_write_interval_s))
        self._lock = threading.RLock()
        self._last_write_at = 0.0
        self._pending: dict[str, Any] | None = None
        self._timer: threading.Timer | None = None

    @property
    def enabled(self) -> bool:
        return bool(str(self.path))

    def load(self, *, now: float | None = None) -> BrokerStateSnapshot:
        current = time.time() if now is None else float(now)
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            return BrokerStateSnapshot({}, {}, {}, {}, {}, 0.0)
        if not isinstance(raw, Mapping) or raw.get("schema") != _SCHEMA:
            return BrokerStateSnapshot({}, {}, {}, {}, {}, 0.0)

        cooldown = {
            str(provider): float(until)
            for provider, until in self._mapping(raw.get("cooldown_until")).items()
            if self._positive_float(until) > current
        }
        limits: dict[str, dict[str, int]] = {}
        for provider, values in self._mapping(raw.get("dynamic_limits")).items():
            clean = {
                kind: self._positive_int(self._mapping(values).get(kind))
                for kind in _ALLOWED_KINDS
                if self._positive_int(self._mapping(values).get(kind)) > 0
            }
            if clean:
                limits[str(provider)] = clean

        resets: dict[str, dict[str, float]] = {}
        remaining: dict[str, dict[str, int]] = {}
        raw_resets = self._mapping(raw.get("header_reset_at"))
        raw_remaining = self._mapping(raw.get("header_remaining"))
        providers = set(raw_resets) | set(raw_remaining)
        for provider in providers:
            provider_resets: dict[str, float] = {}
            provider_remaining: dict[str, int] = {}
            reset_values = self._mapping(raw_resets.get(provider))
            remaining_values = self._mapping(raw_remaining.get(provider))
            for kind in _ALLOWED_KINDS:
                reset_at = self._positive_float(reset_values.get(kind))
                if reset_at <= current:
                    continue
                provider_resets[kind] = reset_at
                rem = self._nonnegative_int(remaining_values.get(kind))
                if rem is not None:
                    provider_remaining[kind] = rem
            if provider_resets:
                resets[str(provider)] = provider_resets
            if provider_remaining:
                remaining[str(provider)] = provider_remaining

        tombstones = {
            str(key): float(expires_at)
            for key, expires_at in self._mapping(raw.get("idempotency_tombstones")).items()
            if self._positive_float(expires_at) > current
        }
        return BrokerStateSnapshot(
            cooldown_until=cooldown,
            dynamic_limits=limits,
            header_remaining=remaining,
            header_reset_at=resets,
            idempotency_tombstones=tombstones,
            saved_at=self._positive_float(raw.get("saved_at")),
        )

    def save(self, payload: Mapping[str, Any], *, force: bool = False, now: float | None = None) -> bool:
        current = time.time() if now is None else float(now)
        document = dict(payload)
        document["schema"] = _SCHEMA
        document["saved_at"] = current
        with self._lock:
            if not force and self.min_write_interval_s > 0.0:
                self._pending = document
                if self._timer is None or not self._timer.is_alive():
                    elapsed = max(0.0, current - self._last_write_at)
                    delay = max(0.01, self.min_write_interval_s - elapsed)
                    self._timer = threading.Timer(delay, self.flush)
                    self._timer.daemon = True
                    self._timer.start()
                return False
            self._write_atomic(document)
            self._last_write_at = current
            self._pending = None
            return True

    def flush(self) -> bool:
        with self._lock:
            if self._pending is None:
                return False
            document = self._pending
            self._write_atomic(document)
            self._last_write_at = time.time()
            self._pending = None
            self._timer = None
            return True

    def clear(self) -> None:
        with self._lock:
            self._pending = None
            timer = self._timer
            self._timer = None
            if timer is not None:
                timer.cancel()
            try:
                self.path.unlink()
            except FileNotFoundError:
                pass
            except OSError:
                pass

    def _write_atomic(self, document: Mapping[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = None
        temporary_path = ""
        try:
            handle = tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=str(self.path.parent),
                prefix=f".{self.path.name}.",
                suffix=".tmp",
                delete=False,
            )
            temporary_path = handle.name
            json.dump(document, handle, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
            handle.close()
            handle = None
            os.replace(temporary_path, self.path)
        finally:
            if handle is not None:
                handle.close()
            if temporary_path:
                try:
                    os.unlink(temporary_path)
                except FileNotFoundError:
                    pass
                except OSError:
                    pass

    @staticmethod
    def _mapping(value: Any) -> Mapping[str, Any]:
        return value if isinstance(value, Mapping) else {}

    @staticmethod
    def _positive_float(value: Any) -> float:
        try:
            return max(0.0, float(value))
        except (TypeError, ValueError):
            return 0.0

    @staticmethod
    def _positive_int(value: Any) -> int:
        try:
            return max(0, int(value))
        except (TypeError, ValueError):
            return 0

    @staticmethod
    def _nonnegative_int(value: Any) -> int | None:
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            return None
        return parsed if parsed >= 0 else None


__all__ = ["BrokerStateSnapshot", "BrokerStateStore"]
