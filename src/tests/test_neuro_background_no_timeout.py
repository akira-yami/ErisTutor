from __future__ import annotations

import math


def test_neuro_continuation_lane_has_no_deadline() -> None:
    from runtime.inference_broker import InferenceBroker, inference_context, lane_policy

    assert lane_policy("neuro_continuation").deadline_seconds == 0.0

    broker = InferenceBroker()
    with inference_context("neuro_continuation", optional=True, timeout_seconds=0.0):
        request = broker.request_from_context(
            provider="unit",
            model="unit-model",
            estimated_input_tokens=1,
            reserved_output_tokens=1,
            timeout_seconds=0.0,
        )

    assert math.isinf(request.deadline_at)
    assert request.optional is True


def test_zero_timeout_disables_http_timeout(monkeypatch) -> None:
    import llm.client as client_module

    captured: dict[str, object] = {}

    class Response:
        status_code = 200
        headers = {}

    def fake_post(url, *, timeout, **_kwargs):
        captured["url"] = url
        captured["timeout"] = timeout
        return Response()

    monkeypatch.setattr(client_module.requests, "post", fake_post)
    monkeypatch.setattr(client_module.GLOBAL_INFERENCE_BROKER, "enabled", False)

    client_module._brokered_post(
        base_url="http://localhost:6011/v1",
        model_name="deepseek-v4-flash",
        messages=[{"role": "user", "content": "x"}],
        max_tokens=8,
        timeout_seconds=0.0,
        url="http://localhost:6011/v1/chat/completions",
        json={"messages": [{"role": "user", "content": "x"}], "max_tokens": 8},
    )

    assert captured["timeout"] is None
