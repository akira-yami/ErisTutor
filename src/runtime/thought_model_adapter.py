# -*- coding: utf-8 -*-
"""
runtime/thought_model_adapter.py
================================

Compatibility adapter cho ThoughtStream model review.

Một số bản coordinator đã import RuntimeThoughtModelAdapter nhưng zip hiện tại
không có file này. Thiếu file import làm toàn bộ runtime chết trước khi private
mind kịp thở. Rất tao nhã, theo kiểu cửa nhà cháy vì quên bản lề.

Adapter này dùng private_mind_model_adapter nếu bật env; nếu không thì trả None
và ThoughtStream chạy deterministic như cũ.
"""

from __future__ import annotations
from core.di import resolve_dependency

import os
from dataclasses import dataclass, field
from typing import Any, Mapping

from runtime.private_mind_model_adapter import RuntimePrivateMindModelAdapter


def _env_flag(name: str, default: bool) -> bool:
    raw = str(os.getenv(name, "1" if default else "0") or "").strip().lower()
    if not raw:
        return default
    return raw not in {"0", "false", "no", "off", "none"}


@dataclass(slots=True, frozen=True)
class ThoughtModelAdapterConfig:
    enabled: bool = field(default_factory=lambda: _env_flag("ERIS_THOUGHT_EVALUATOR_API_ENABLED", False))
    min_interval_seconds: float = field(default_factory=lambda: float(os.getenv("ERIS_THOUGHT_EVALUATOR_MIN_INTERVAL_SEC", "45") or 45.0))
    log_api_thoughts: bool = field(default_factory=lambda: _env_flag("ERIS_THOUGHT_EVALUATOR_LOG_API_THOUGHTS", True))
    provider: str = field(default_factory=lambda: os.getenv("ERIS_PRIVATE_MIND_PROVIDER", "private_mind"))
    model: str = field(default_factory=lambda: os.getenv("ERIS_PRIVATE_MIND_MODEL", "private_mind"))


class RuntimeThoughtModelAdapter:
    def __init__(self, *, config: ThoughtModelAdapterConfig | None = None, private_adapter: Any = None, logger: Any = None) -> None:
        self.config = resolve_dependency(config, lambda: ThoughtModelAdapterConfig())
        self.min_interval_seconds = self.config.min_interval_seconds
        self.log_api_thoughts = self.config.log_api_thoughts
        self.logger = logger
        self.private_adapter = private_adapter

    @classmethod
    def from_env(cls, *, logger: Any = None) -> "RuntimeThoughtModelAdapter | None":
        config = ThoughtModelAdapterConfig()
        if not config.enabled:
            return None
        private_adapter = RuntimePrivateMindModelAdapter.from_env(logger=logger)
        if private_adapter is None:
            return None
        return cls(config=config, private_adapter=private_adapter, logger=logger)

    def review(self, payload: Mapping[str, Any]) -> dict[str, Any] | None:
        """Trả schema ThoughtStream đang mong đợi.

        Không có prompt riêng phức tạp ở đây để tránh hai hệ private review đá nhau.
        PrivateMindLoop mới là đường chính; adapter này chỉ giữ import/runtime không gãy.
        """

        if self.private_adapter is None:
            return None
        # Không tái chế dossier ở đây vì payload ThoughtStream không cùng contract.
        # Trả deterministic minimal review đủ an toàn.
        evidence = tuple(str(x) for x in (payload.get("evidence_ids") or payload.get("evidence") or ()) if str(x).strip())[:8]
        topic = str(payload.get("topic") or payload.get("summary") or payload.get("seed") or "private thought")
        return {
            "recommended_route": "keep_private",
            "confidence": 0.42,
            "surface_pressure": 0.18,
            "memory_pressure": 0.36,
            "dominant_evaluation": f"Private thought should remain internal unless grounded by later SurfaceValve: {topic[:160]}",
            "judge_verdict": "Keep private; let PrivateMindLoop handle concern/mutation.",
            "risk_assessment": "Avoid raw private-chain surface.",
            "evidence_ids": list(evidence),
            "provider": self.config.provider,
            "model": self.config.model,
        }
