from __future__ import annotations

from pathlib import Path


def test_chat_cycle_resolves_executive_before_use() -> None:
    """Regression: V17-V24 từng đọc local ``executive`` trước khi binding."""
    root = Path(__file__).resolve().parents[1]
    source = (root / "runtime" / "ai_orchestrated" / "orchestrator.py").read_text(encoding="utf-8")
    bind = 'executive = getattr(host, "ai_executive_cortex", None)'
    use = 'if executive is not None and bool(getattr(executive, "enabled", False)):'
    assert bind in source
    assert use in source
    assert source.index(bind) < source.index(use)
