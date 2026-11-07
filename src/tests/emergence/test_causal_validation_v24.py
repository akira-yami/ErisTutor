from __future__ import annotations

import pytest

from experiments.emergence.report import ExperimentReport
from experiments.emergence.validation import CausalValidationReport


def test_validation_report_fails_closed_when_any_gate_fails() -> None:
    good = ExperimentReport("good", "r", "a", {}, {}, True, 1)
    bad = ExperimentReport("bad", "r", "a", {}, {}, False, 1, notes=("x",))
    report = CausalValidationReport((good, bad), (), None, False, ("paired:bad",))
    assert report.passed is False
    assert report.failures == ("paired:bad",)
