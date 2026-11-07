# -*- coding: utf-8 -*-
"""V6 regression tests for the remaining page 7-9 admission details.

No test performs real network I/O. These cases lock adaptive required-lane
budget degradation, truthful payload rewriting, model-family concurrency and
quota-domain-aware in-flight preemption from the architecture PDF.
"""

from __future__ import annotations

import threading
import time

from llm.token_budget import estimate_tokens
from runtime.inference_broker import (
    GLOBAL_INFERENCE_BROKER,
    InferenceBroker,
    InferenceRejected,
    inference_context,
)


def _make_request(
    broker: InferenceBroker,
    *,
    provider: str = "openai",
    model: str = "llama-3.1-8b-instant",
    lane: str = "chat_main",
    optional: bool = False,
    timeout: float = 2.0,
    input_tokens: int = 10,
    output_tokens: int = 10,
    metadata: dict | None = None,
):
    scope = "background:v6" if optional else "user_turn:v6"
    with inference_context(
        lane,
        optional=optional,
        cancel_scope=scope,
        timeout_seconds=timeout,
    ):
        return broker.request_from_context(
            provider=provider,
            model=model,
            estimated_input_tokens=input_tokens,
            reserved_output_tokens=output_tokens,
            timeout_seconds=timeout,
            metadata=metadata or {},
        )


def test_required_chat_degrades_reservation_before_waiting(monkeypatch) -> None:
    monkeypatch.setenv("ERIS_BROKER_TPM_OPENAI", "800")
    monkeypatch.setenv("ERIS_BROKER_ITPM_OPENAI", "700")
    monkeypatch.setenv("ERIS_BROKER_OTPM_OPENAI", "200")
    broker = InferenceBroker()
    request = _make_request(
        broker,
        input_tokens=900,
        output_tokens=300,
        metadata={
            "input_degrade_supported": True,
            "output_degrade_supported": True,
        },
    )
    observed: dict[str, int | bool] = {}

    def adapter(*, inference_request=None):
        assert inference_request is request
        observed["input"] = inference_request.estimated_input_tokens
        observed["output"] = inference_request.reserved_output_tokens
        observed["degraded"] = bool(inference_request.metadata.get("broker_degraded"))
        return {"choices": []}

    broker.run(request, adapter)

    assert observed == {"input": 600, "output": 200, "degraded": True}
    assert request.metadata["broker_original_input_tokens"] == 900
    assert request.metadata["broker_original_output_tokens"] == 300
    snapshot = broker.snapshot("openai")
    assert snapshot["tpm_used"] == 800
    assert snapshot["itpm_used"] == 600
    assert snapshot["otpm_used"] == 200


def test_broker_never_degrades_without_adapter_rewrite_contract(monkeypatch) -> None:
    monkeypatch.setenv("ERIS_BROKER_TPM_OPENAI", "800")
    monkeypatch.setenv("ERIS_BROKER_ITPM_OPENAI", "700")
    monkeypatch.setenv("ERIS_BROKER_OTPM_OPENAI", "200")
    broker = InferenceBroker()
    request = _make_request(broker, input_tokens=900, output_tokens=300)

    preview = broker.submit(request)

    assert preview.state == "queued"
    assert preview.reason == "quota_unavailable"
    assert request.estimated_input_tokens == 900
    assert request.reserved_output_tokens == 300
    assert "broker_degraded" not in request.metadata


def test_transport_rewrites_openai_payload_to_effective_budget(monkeypatch) -> None:
    import llm.client as client_module

    monkeypatch.setenv("ERIS_BROKER_MODE", "enforce")
    monkeypatch.setenv("ERIS_BROKER_TPM_OPENAI", "800")
    monkeypatch.setenv("ERIS_BROKER_ITPM_OPENAI", "700")
    monkeypatch.setenv("ERIS_BROKER_OTPM_OPENAI", "200")
    GLOBAL_INFERENCE_BROKER.reset_for_tests()
    captured: dict = {}

    class FakeResponse:
        headers = {}
        status_code = 200

    def fake_post(url, *, timeout, **kwargs):
        captured["url"] = url
        captured["timeout"] = timeout
        captured["json"] = kwargs["json"]
        return FakeResponse()

    monkeypatch.setattr(client_module.requests, "post", fake_post)
    messages = [
        {"role": "system", "content": "system contract"},
        {"role": "user", "content": "x" * 2700},
    ]
    original_tokens = estimate_tokens(messages)
    payload = {"model": "unit", "messages": messages, "max_tokens": 300}

    client_module._brokered_post(
        base_url="https://api.openai.com/v1",
        model_name="llama-3.1-8b-instant",
        messages=messages,
        max_tokens=300,
        timeout_seconds=3.0,
        url="https://api.openai.com/v1/chat/completions",
        json=payload,
    )

    sent = captured["json"]
    assert sent["max_tokens"] <= 200
    assert estimate_tokens(sent["messages"]) < original_tokens
    assert estimate_tokens(sent["messages"]) + sent["max_tokens"] <= 800
    GLOBAL_INFERENCE_BROKER.reset_for_tests()


def test_model_family_shares_concurrency_gate(monkeypatch) -> None:
    monkeypatch.setenv("ERIS_BROKER_CONCURRENCY_OPENAI", "1")
    broker = InferenceBroker()
    first = _make_request(broker, model="meta-llama/llama-3.3-70b-versatile", timeout=4.0)
    second = _make_request(broker, model="meta-llama/llama-3.3-70b-versatile-preview", timeout=4.0)
    first_started = threading.Event()
    release_first = threading.Event()
    second_started = threading.Event()

    def first_worker() -> None:
        broker.run(first, lambda: (first_started.set(), release_first.wait(2.0), "first")[-1])

    def second_worker() -> None:
        broker.run(second, lambda: (second_started.set(), "second")[-1])

    t1 = threading.Thread(target=first_worker)
    t2 = threading.Thread(target=second_worker)
    t1.start()
    assert first_started.wait(1.0)
    t2.start()
    time.sleep(0.08)

    assert not second_started.is_set()
    assert any(item["request_id"] == second.request_id for item in broker.list_queued())
    release_first.set()
    t1.join(2.0)
    t2.join(2.0)
    assert not t1.is_alive() and not t2.is_alive()
    assert second_started.is_set()


def test_unrelated_provider_optional_work_is_not_globally_cancelled(monkeypatch) -> None:
    monkeypatch.setenv("ERIS_BROKER_CONCURRENCY_OPENAI", "1")
    broker = InferenceBroker()
    optional = _make_request(
        broker,
        provider="openai",
        model="llama-3.1-8b-instant",
        lane="vision_ambient",
        optional=True,
        timeout=3.0,
    )
    started = threading.Event()
    release = threading.Event()
    outcome: dict[str, str] = {}

    def optional_adapter(*, cancel_token=None):
        started.set()
        while not release.is_set() and not cancel_token.cancelled:
            time.sleep(0.01)
        cancel_token.raise_if_cancelled(optional)
        return "vision"

    def optional_worker() -> None:
        try:
            outcome["result"] = broker.run(optional, optional_adapter)
        except InferenceRejected as exc:
            outcome["reason"] = exc.reason

    thread = threading.Thread(target=optional_worker)
    thread.start()
    assert started.wait(1.0)

    broker.begin_user_turn("user_turn:gemini")
    chat = _make_request(
        broker,
        provider="gemini",
        model="gemini-2.5-flash",
        lane="chat_main",
        optional=False,
        timeout=2.0,
    )
    assert broker.run(chat, lambda: "chat") == "chat"
    broker.end_user_turn("user_turn:gemini")
    time.sleep(0.05)
    assert thread.is_alive()

    release.set()
    thread.join(2.0)
    assert outcome == {"result": "vision"}
