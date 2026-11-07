"""Gemini Playwright Pool Client

Round-robin across multiple WebAI-to-API instances, each running
a separate Google account via Playwright.

Pool endpoints (default):
    http://127.0.0.1:8081  <- account 1
    http://127.0.0.1:8082  <- account 2
    http://127.0.0.1:8083  <- account 3

Usage (debug/compatibility only):
    from runtime.agent_harness.gemini_pool import GeminiPool
    pool = GeminiPool()
    base_url, model = pool.next()
    # -> ("http://127.0.0.1:8082/v1", "playwright/gemini/gemini-3-pro")
"""
from __future__ import annotations

import itertools
import logging
import os
from pathlib import Path
import threading
import time
import urllib.request

logger = logging.getLogger(__name__)

_DEFAULT_ENDPOINTS = [
    "http://127.0.0.1:8081",
    "http://127.0.0.1:8082",
    "http://127.0.0.1:8083",
]

_DEFAULT_MODEL = "playwright/gemini/gemini-3-pro"


class GeminiPool:
    """Round-robin pool of WebAI-to-API Playwright instances.

    Thread-safe. Skips unhealthy endpoints automatically.
    Falls back to single endpoint if pool not configured.
    """

    def __init__(
        self,
        endpoints: list[str] | None = None,
        model: str = _DEFAULT_MODEL,
        health_check: bool = True,
        health_timeout: float = 2.0,
    ) -> None:
        raw = endpoints or self._load_from_env()
        self._all = raw
        self._model = model
        self._health_timeout = health_timeout
        self._lock = threading.Lock()
        self._health: dict[str, bool] = {e: True for e in raw}
        self._last_check: dict[str, float] = {e: 0.0 for e in raw}
        self._recheck_interval = 30.0  # re-check failed endpoint after 30s

        if health_check:
            self._initial_health_check()

        # Build round-robin iterator from healthy endpoints
        self._cycle = itertools.cycle(self._healthy_endpoints())
        logger.info("[pool] GeminiPool initialized: %d endpoints, model=%s",
                    len(raw), model)

    # ── Public ────────────────────────────────────────────────────────

    def next(self) -> tuple[str, str]:
        """Get next (base_url, model) from pool.

        Returns:
            (base_url_with_v1, model_string)
            e.g. ("http://127.0.0.1:8082/v1", "playwright/gemini/gemini-3-pro")
        """
        with self._lock:
            healthy = self._healthy_endpoints()
            if not healthy:
                # All down — reset and try first
                logger.warning("[pool] All endpoints unhealthy, resetting health state")
                for ep in self._all:
                    self._health[ep] = True
                healthy = self._all

            # Advance cycle until we hit a healthy endpoint
            for _ in range(len(self._all) * 2):
                ep = next(self._cycle)
                if self._is_healthy(ep):
                    return f"{ep}/v1", self._model

            # Fallback
            ep = self._all[0]
            return f"{ep}/v1", self._model

    def mark_failed(self, base_url: str) -> None:
        """Mark an endpoint as unhealthy (call on HTTP error)."""
        ep = base_url.rstrip("/v1").rstrip("/")
        with self._lock:
            if ep in self._health:
                logger.warning("[pool] Marking endpoint unhealthy: %s", ep)
                self._health[ep] = False
                self._last_check[ep] = time.time()

    def status(self) -> list[dict]:
        """Return health status of all endpoints."""
        result = []
        for ep in self._all:
            result.append({
                "endpoint": ep,
                "healthy": self._health.get(ep, False),
                "last_check": self._last_check.get(ep, 0.0),
            })
        return result

    def size(self) -> int:
        return len(self._all)

    # ── Internal ──────────────────────────────────────────────────────

    def _load_from_env(self) -> list[str]:
        """Load endpoints tu env var, file state, hoac dung defaults."""
        # 1. Env var (cao nhat)
        raw = os.environ.get("GEMINI_POOL_ENDPOINTS", "")
        if raw.strip():
            eps = [e.strip() for e in raw.split(",") if e.strip()]
            logger.info("[pool] Loaded %d endpoints from GEMINI_POOL_ENDPOINTS", len(eps))
            return eps

        # 2. File duoc ghi boi _start_gemini_pool.bat
        state_file = self._find_state_file()
        if state_file:
            try:
                content = state_file.read_text(encoding="utf-8").strip()
                eps = [e.strip() for e in content.split(",") if e.strip()]
                if eps:
                    logger.info("[pool] Loaded %d endpoints from %s", len(eps), state_file)
                    return eps
            except Exception:
                pass

        # 3. Hardcode defaults (3 instances)
        return list(_DEFAULT_ENDPOINTS)

    def _find_state_file(self):
        """Tim file gemini_pool_endpoints.txt tu eris root."""
        try:
            # src/runtime/agent_harness/ -> eris root
            root = Path(__file__).resolve().parents[3]
            candidates = [
                root / ".eris_state" / "gemini_pool_endpoints.txt",
                root / "gemini_pool_endpoints.txt",
            ]
            for p in candidates:
                if p.exists():
                    return p
        except Exception:
            pass
        return None

    def _initial_health_check(self) -> None:
        """Quick HEAD check on each endpoint at startup."""
        for ep in self._all:
            healthy = self._ping(ep)
            self._health[ep] = healthy
            self._last_check[ep] = time.time()
            status = "OK" if healthy else "OFFLINE"
            logger.info("[pool] %s -> %s", ep, status)

    def _ping(self, endpoint: str) -> bool:
        """Ping /v1/models to check if instance is up."""
        try:
            url = f"{endpoint}/v1/models"
            req = urllib.request.Request(url, method="GET")
            with urllib.request.urlopen(req, timeout=self._health_timeout) as resp:
                return resp.status == 200
        except Exception:
            return False

    def _is_healthy(self, ep: str) -> bool:
        if self._health.get(ep, True):
            return True
        # Re-check if enough time has passed
        elapsed = time.time() - self._last_check.get(ep, 0.0)
        if elapsed >= self._recheck_interval:
            healthy = self._ping(ep)
            self._health[ep] = healthy
            self._last_check[ep] = time.time()
            if healthy:
                logger.info("[pool] Endpoint recovered: %s", ep)
            return healthy
        return False

    def _healthy_endpoints(self) -> list[str]:
        return [ep for ep in self._all if self._is_healthy(ep)]


# ── Singleton ──────────────────────────────────────────────────────────────────

_pool_instance: GeminiPool | None = None
_pool_lock = threading.Lock()


def get_pool(
    endpoints: list[str] | None = None,
    model: str = _DEFAULT_MODEL,
    health_check: bool = True,
) -> GeminiPool:
    """Get or create singleton GeminiPool."""
    global _pool_instance
    with _pool_lock:
        if _pool_instance is None:
            _pool_instance = GeminiPool(
                endpoints=endpoints,
                model=model,
                health_check=health_check,
            )
        return _pool_instance
