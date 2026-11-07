from __future__ import annotations

from types import SimpleNamespace

import runtime.hardening_metrics as metrics


class FakeObservability:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.calls = []

    def _call(self, name, *args, **kwargs):
        if self.fail:
            raise RuntimeError("telemetry offline")
        self.calls.append((name, args, kwargs))

    def increment(self, *args, **kwargs):
        self._call("increment", *args, **kwargs)

    def observe(self, *args, **kwargs):
        self._call("observe", *args, **kwargs)

    def gauge(self, *args, **kwargs):
        self._call("gauge", *args, **kwargs)

    def log(self, *args, **kwargs):
        self._call("log", *args, **kwargs)


def test_hardening_metrics_use_one_working_boundary(monkeypatch) -> None:
    obs = FakeObservability()
    monkeypatch.setattr(metrics, "_obs", lambda: obs)

    metrics.record_provider_rate_limit(provider="gemini", lane="research", retry_after_s=2.5)
    metrics.record_repair_state(active=True, exhausted=False, turns_left=2, event="start")

    names = [item[0] for item in obs.calls]
    assert names.count("increment") == 2
    assert "observe" in names
    assert "gauge" in names


def test_hardening_metrics_never_escape_telemetry_failure(monkeypatch) -> None:
    monkeypatch.setattr(metrics, "_obs", lambda: FakeObservability(fail=True))

    metrics.record_action_confirmation(SimpleNamespace(public_mode="observed_success", action_name="open", success=True, observed=True))
    metrics.record_tts_breaker(state="open", event="failure", provider="gemini", locale="ja")
