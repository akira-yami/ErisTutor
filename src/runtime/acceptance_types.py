from __future__ import annotations

"""Kiểu verdict dùng chung cho critique, gate và observability."""

from dataclasses import dataclass
from enum import StrEnum


class GateVerdict(StrEnum):
    PASS = "pass"
    CONSTRAINED_RERUN = "constrained_rerun"
    DOMAIN_FALLBACK = "domain_fallback"
    BLOCK = "block"


@dataclass(frozen=True, slots=True)
class AcceptanceResult:
    verdict: GateVerdict
    reasons: tuple[str, ...] = ()
    advisories: tuple[str, ...] = ()
    fallback_text: str = ""
    rerun_notes: str = ""

    @property
    def accepted(self) -> bool:
        """Compat với code cũ; chỉ PASS mới là accepted."""
        return self.verdict == GateVerdict.PASS


__all__ = ["GateVerdict", "AcceptanceResult"]
