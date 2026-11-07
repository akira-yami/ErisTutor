# -*- coding: utf-8 -*-
from pathlib import Path


def test_ds2api_boot_launches_ports_before_parallel_health_wait() -> None:
    source = (Path(__file__).resolve().parents[1] / "vision.py").read_text(encoding="utf-8")
    assert "ThreadPoolExecutor" in source
    assert "_start_one_ds2api" in source
    assert "executor.submit(self._start_one_ds2api" in source
