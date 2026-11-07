# -*- coding: utf-8 -*-
"""Stable keys for coalescing and broker idempotency.

Python's built-in ``hash()`` is intentionally randomized between processes, so
it is unsuitable for durable work identity.  These helpers produce short,
non-reversible digests without storing the source text in broker state.
"""

from __future__ import annotations

import hashlib
from typing import Any


def stable_work_key(namespace: str, *parts: Any, digest_size: int = 16) -> str:
    prefix = str(namespace or "work").strip().lower().replace(" ", "_") or "work"
    payload = "\x1f".join(str(part or "") for part in parts).encode("utf-8", "surrogatepass")
    digest = hashlib.blake2b(payload, digest_size=max(8, min(32, int(digest_size)))).hexdigest()
    return f"{prefix}:{digest}"


__all__ = ["stable_work_key"]
