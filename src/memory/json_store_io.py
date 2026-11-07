# -*- coding: utf-8 -*-
"""I/O JSON dùng chung cho các memory store.

Mục tiêu:
- một lock theo đường dẫn để nhiều thread trong cùng process không ghi đè nhau;
- temp file luôn duy nhất;
- flush + fsync trước os.replace;
- retry ngắn khi Windows/antivirus tạm giữ file đích;
- dọn temp file kể cả khi lỗi.
"""
from __future__ import annotations

import json
import os
import tempfile
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

_LOCKS: dict[str, threading.RLock] = {}
_LOCKS_GUARD = threading.Lock()


def _normalized_path(path: str | os.PathLike[str]) -> str:
    return os.path.normcase(os.path.abspath(os.fspath(path)))


def lock_for_path(path: str | os.PathLike[str]) -> threading.RLock:
    key = _normalized_path(path)
    with _LOCKS_GUARD:
        lock = _LOCKS.get(key)
        if lock is None:
            lock = threading.RLock()
            _LOCKS[key] = lock
        return lock


@contextmanager
def locked_path(path: str | os.PathLike[str]) -> Iterator[None]:
    lock = lock_for_path(path)
    with lock:
        yield


def atomic_write_json(
    path: str | os.PathLike[str],
    payload: Any,
    *,
    retries: int = 6,
    retry_delay_s: float = 0.03,
) -> None:
    """Ghi JSON nguyên tử và thân thiện hơn với Windows.

    ``os.replace`` có thể tạm thời nhận ``PermissionError`` khi antivirus,
    indexer hoặc thread khác đang chạm file. Ta retry ngắn với backoff thay vì
    làm mất cả lượt commit memory.
    """

    target = Path(path).expanduser().resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    temp_name = ""

    with locked_path(target):
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                newline="\n",
                prefix=f".{target.name}.",
                suffix=".tmp",
                dir=str(target.parent),
                delete=False,
            ) as handle:
                temp_name = handle.name
                json.dump(payload, handle, ensure_ascii=False, indent=2)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())

            last_error: OSError | None = None
            attempts = max(1, int(retries))
            for attempt in range(attempts):
                try:
                    os.replace(temp_name, target)
                    temp_name = ""
                    return
                except PermissionError as exc:
                    last_error = exc
                    if attempt >= attempts - 1:
                        break
                    time.sleep(max(0.0, retry_delay_s) * (attempt + 1))

            if last_error is not None:
                raise last_error
            raise OSError(f"Không thể thay file JSON: {target}")
        finally:
            if temp_name:
                try:
                    os.unlink(temp_name)
                except FileNotFoundError:
                    pass
                except OSError:
                    pass
