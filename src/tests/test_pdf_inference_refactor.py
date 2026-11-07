# -*- coding: utf-8 -*-
"""Regression tests for the one-shot refactor requested from the architecture PDF.

These tests intentionally perform no network I/O.  They lock the P0/P1
invariants from pages 6-19: central admission, optional shedding, coalescing,
provider-header adaptation, a real prompt cap, and one typed semantic authority.
"""

from __future__ import annotations

import threading
import time

import pytest

from llm import token_budget
from runtime.inference_broker import (
    InferenceBroker,
    InferenceRejected,
    inference_context,
)
from runtime.semantic_frame import validate_semantic_frame


def _request(
    broker: InferenceBroker,
    *,
    lane: str,
    optional: bool,
    scope: str,
    coalesce_key: str = "",
    idempotency_key: str = "",
    timeout: float = 2.0,
):
    with inference_context(
        lane,
        optional=optional,
        cancel_scope=scope,
        coalesce_key=coalesce_key,
        idempotency_key=idempotency_key,
        timeout_seconds=timeout,
    ):
        return broker.request_from_context(
            provider="openai",
            model="unit-test-model",
            estimated_input_tokens=10,
            reserved_output_tokens=10,
            timeout_seconds=timeout,
        )


def test_optional_remote_work_is_shed_while_user_turn_is_active() -> None:
    broker = InferenceBroker()
    called = False

    with broker.user_turn("user_turn:test"):
        request = _request(
            broker,
            lane="private_mind",
            optional=True,
            scope="background:private_mind",
        )

        def remote_call():
            nonlocal called
            called = True
            return "should-not-run"

        with pytest.raises(InferenceRejected) as exc:
            broker.run(request, remote_call)

    assert exc.value.reason == "shed_optional_user_active"
    assert called is False


def test_provider_headers_update_broker_limits_and_cooldown() -> None:
    broker = InferenceBroker()
    broker.record_response_headers(
        "openai",
        {
            "x-ratelimit-limit-requests": "17",
            "x-ratelimit-limit-tokens": "4321",
            "retry-after": "2",
        },
    )

    snapshot = broker.snapshot("openai")
    assert snapshot["rpm_limit"] == 17
    assert snapshot["tpm_limit"] == 4321
    assert 0.0 < snapshot["cooldown_remaining_s"] <= 2.0


def test_coalescing_keeps_only_the_newest_waiting_optional_job(monkeypatch) -> None:
    monkeypatch.setenv("ERIS_BROKER_CONCURRENCY_OPENAI", "1")
    broker = InferenceBroker()
    first_started = threading.Event()
    release_first = threading.Event()
    results: dict[str, str] = {}

    first = _request(
        broker,
        lane="vision_ambient",
        optional=True,
        scope="background:vision:first",
        coalesce_key="screen:main",
        timeout=4.0,
    )

    def run_first() -> None:
        def call():
            first_started.set()
            release_first.wait(2.0)
            return "first"

        results["first"] = broker.run(first, call)

    thread_first = threading.Thread(target=run_first)
    thread_first.start()
    assert first_started.wait(1.0)

    second = _request(
        broker,
        lane="vision_ambient",
        optional=True,
        scope="background:vision:second",
        coalesce_key="screen:main",
        timeout=4.0,
    )
    third = _request(
        broker,
        lane="vision_ambient",
        optional=True,
        scope="background:vision:third",
        coalesce_key="screen:main",
        timeout=4.0,
    )

    errors: dict[str, str] = {}

    def run_named(name: str, request) -> None:
        try:
            results[name] = broker.run(request, lambda: name)
        except InferenceRejected as exc:
            errors[name] = exc.reason

    thread_second = threading.Thread(target=run_named, args=("second", second))
    thread_second.start()
    deadline = time.time() + 1.0
    while time.time() < deadline and not broker.list_queued():
        time.sleep(0.01)
    assert broker.list_queued(), "second request never entered the bounded queue"

    thread_third = threading.Thread(target=run_named, args=("third", third))
    thread_third.start()
    time.sleep(0.05)
    release_first.set()

    for thread in (thread_first, thread_second, thread_third):
        thread.join(2.0)
        assert not thread.is_alive()

    assert errors["second"] == "coalesced_to_newer"
    assert results["third"] == "third"


def test_prompt_cap_defaults_to_provider_headroom_when_no_override(monkeypatch) -> None:
    for name in (
        "ERIS_DISABLE_PROMPT_HARD_CAP",
        "ERIS_LLM_PROMPT_HARD_CAP_TOKENS",
        "ERIS_LLM_PROMPT_HARD_CAP_TOKENS_GROQ",
    ):
        monkeypatch.delenv(name, raising=False)

    # Simulate a runtime store where the setting is truly absent.  This is the
    # exact control-flow bug described on PDF pages 4 and 15-16.
    import runtime.runtime_settings_store as settings_store

    monkeypatch.setattr(settings_store, "runtime_int", lambda *_args, **_kwargs: None)
    assert token_budget.hard_cap_for_provider("groq") == 5100


def test_explicit_zero_still_disables_prompt_cap(monkeypatch) -> None:
    monkeypatch.setenv("ERIS_LLM_PROMPT_HARD_CAP_TOKENS_GROQ", "0")
    assert token_budget.hard_cap_for_provider("groq") >= 1_000_000_000


def test_assistant_check_in_frame_is_repaired_by_logic_not_regex() -> None:
    result = validate_semantic_frame(
        {
            "discourse_relation": "continuation_of_assistant_self_disclosure",
            "user_act": "relational_check_in",
            "target": "assistant",
            "literal_question": True,
            "ambiguity_owner": "assistant",
            "tool_intent": "weather",
            "continuation_key": "",
            "reply_obligation": {
                "must_answer": False,
                "must_not_broad_clarify": False,
                "must_not_call_tool": False,
            },
        }
    )

    assert result.frame["version"] == "semantic_frame.v1"
    assert result.frame["tool_intent"] == "none"
    # V29: missing continuation identity fails closed; parser must not invent
    # a recent thread and accidentally revive stale conversation state.
    assert result.frame["continuation_key"] == ""
    assert result.frame["discourse_relation"] == ""
    assert result.frame["reply_obligation"] == {
        "must_answer": True,
        "must_not_broad_clarify": True,
        "must_not_call_tool": True,
    }
    assert "assistant_check_in_must_not_call_tool" in result.violations


def test_broker_settles_split_input_output_usage(monkeypatch) -> None:
    monkeypatch.setenv("ERIS_BROKER_TPM_OPENAI", "1000")
    monkeypatch.setenv("ERIS_BROKER_ITPM_OPENAI", "700")
    monkeypatch.setenv("ERIS_BROKER_OTPM_OPENAI", "500")
    broker = InferenceBroker()
    request = _request(
        broker,
        lane="chat_main",
        optional=False,
        scope="user_turn:usage",
    )
    request.estimated_input_tokens = 100
    request.reserved_output_tokens = 200

    result = broker.run(
        request,
        lambda: {
            "usage": {"prompt_tokens": 12, "completion_tokens": 8, "total_tokens": 20},
            "choices": [],
        },
    )

    assert result["usage"]["total_tokens"] == 20
    snapshot = broker.snapshot("openai")
    assert snapshot["tpm_used"] == 20
    assert snapshot["itpm_used"] == 12
    assert snapshot["otpm_used"] == 8
    assert snapshot["itpm_limit"] == 700
    assert snapshot["otpm_limit"] == 500


def test_provider_headers_update_split_token_limits() -> None:
    broker = InferenceBroker()
    broker.record_response_headers(
        "groq",
        {
            "x-ratelimit-limit-tokens": "6000",
            "x-ratelimit-limit-input-tokens": "5000",
            "x-ratelimit-limit-output-tokens": "1200",
        },
    )

    snapshot = broker.snapshot("groq")
    assert snapshot["tpm_limit"] == 6000
    assert snapshot["itpm_limit"] == 5000
    assert snapshot["otpm_limit"] == 1200


def test_inflight_optional_adapter_receives_cancellation_token(monkeypatch) -> None:
    monkeypatch.setenv("ERIS_BROKER_CONCURRENCY_OPENAI", "1")
    broker = InferenceBroker()
    request = _request(
        broker,
        lane="vision_ambient",
        optional=True,
        scope="background:vision:cancel",
        timeout=3.0,
    )
    started = threading.Event()
    outcome: dict[str, str] = {}

    def adapter(*, cancel_token=None):
        assert cancel_token is not None
        started.set()
        while not cancel_token.cancelled:
            time.sleep(0.01)
        cancel_token.raise_if_cancelled(request)
        return "late-result"

    def worker() -> None:
        try:
            broker.run(request, adapter)
        except InferenceRejected as exc:
            outcome["reason"] = exc.reason

    thread = threading.Thread(target=worker)
    thread.start()
    assert started.wait(1.0)

    # PDF page 7: an on-wire optional request is not cancelled merely because
    # some unrelated user turn exists. It is preempted when a required request
    # actually needs the same quota/concurrency domain.
    broker.begin_user_turn("user_turn:preempt")
    time.sleep(0.03)
    assert thread.is_alive()
    chat_request = _request(
        broker,
        lane="chat_main",
        optional=False,
        scope="user_turn:preempt",
        timeout=2.0,
    )
    chat_result = broker.run(chat_request, lambda: "chat-result")
    broker.end_user_turn("user_turn:preempt")
    thread.join(2.0)

    assert chat_result == "chat-result"
    assert not thread.is_alive()
    assert outcome["reason"] == "cancelled"


def test_wording_retry_reuses_locked_semantic_frame() -> None:
    import json
    from types import SimpleNamespace

    from runtime.ai_orchestrated.model_router import AIRuntimeModelRouter

    captured: dict = {}

    class FakeClient:
        model_name = "unit-test-model"

        def create_chat_completion(self, **kwargs):
            captured.update(kwargs)
            return {
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "text": "Câu đã viết lại.",
                                    "should_speak": True,
                                    "performance_hint": "neutral",
                                },
                                ensure_ascii=False,
                            )
                        }
                    }
                ]
            }

    locked = validate_semantic_frame(
        {
            "version": "semantic_frame.v1",
            "turn_kind": "chat_response",
            "discourse_relation": "continuation_of_assistant_self_disclosure",
            "user_act": "relational_check_in",
            "target": "assistant",
            "literal_question": True,
            "ambiguity_owner": "assistant",
            "boundary_action": "none",
            "continuation_key": "assistant_self_state",
            "evidence_scope": "user_message_plus_last_assistant_turn",
            "tool_intent": "none",
            "missing_evidence": [],
            "reply_obligation": {
                "must_answer": True,
                "must_not_broad_clarify": True,
                "must_not_call_tool": True,
            },
        }
    ).frame
    router = AIRuntimeModelRouter(SimpleNamespace(client=FakeClient(), fallback_client=None))
    payload = router.generate_public_reply(
        chat_history=[],
        user_text="hôm nay em sao",
        locked_semantic_frame=locked,
        retry_instruction="Bỏ claim không có grounding.",
    )

    schema = captured["response_format"]["json_schema"]["schema"]
    assert "semantic_frame" not in schema["properties"]
    assert captured["max_tokens"] == 520
    assert payload["semantic_frame"] == locked
    assert payload["text"] == "Câu đã viết lại."


def _request_for_provider(
    broker: InferenceBroker,
    *,
    provider: str,
    lane: str,
    optional: bool,
    scope: str,
    project: str = "",
    timeout: float = 2.0,
):
    with inference_context(
        lane,
        optional=optional,
        cancel_scope=scope,
        project=project,
        timeout_seconds=timeout,
    ):
        return broker.request_from_context(
            provider=provider,
            model="unit-test-model",
            estimated_input_tokens=10,
            reserved_output_tokens=10,
            timeout_seconds=timeout,
        )


def test_hot_provider_does_not_head_of_line_block_other_provider() -> None:
    """A cooling OpenAI request may not freeze an unrelated Gemini request."""

    broker = InferenceBroker()
    broker.record_rate_limit("openai", retry_after_s=0.6)
    openai_request = _request_for_provider(
        broker,
        provider="openai",
        lane="chat_main",
        optional=False,
        scope="user_turn:openai-wait",
        timeout=1.5,
    )
    gemini_request = _request_for_provider(
        broker,
        provider="gemini",
        lane="chat_main",
        optional=False,
        scope="user_turn:gemini-ready",
        timeout=1.0,
    )
    outcome: dict[str, str] = {}

    def wait_openai() -> None:
        outcome["openai"] = broker.run(openai_request, lambda: "openai")

    thread = threading.Thread(target=wait_openai)
    thread.start()
    deadline = time.time() + 0.5
    while time.time() < deadline and not broker.list_queued():
        time.sleep(0.01)
    assert broker.list_queued()

    started = time.perf_counter()
    assert broker.run(gemini_request, lambda: "gemini") == "gemini"
    assert time.perf_counter() - started < 0.35

    thread.join(2.0)
    assert not thread.is_alive()
    assert outcome["openai"] == "openai"


def test_project_budget_is_reserved_and_reported(monkeypatch) -> None:
    monkeypatch.setenv("ERIS_BROKER_PROJECT_RPM_OPENAI_ALPHA", "1")
    broker = InferenceBroker()
    first = _request_for_provider(
        broker,
        provider="openai",
        lane="chat_main",
        optional=False,
        scope="user_turn:alpha-1",
        project="alpha",
    )
    assert broker.run(first, lambda: {"usage": {"prompt_tokens": 3, "completion_tokens": 2}})

    snapshot = broker.snapshot("openai", project="alpha")
    assert snapshot["project_budget"]["rpm_used"] == 1
    assert snapshot["project_budget"]["rpm_limit"] == 1
    assert snapshot["project_budget"]["tpm_used"] == 5

    second = _request_for_provider(
        broker,
        provider="openai",
        lane="chat_main",
        optional=False,
        scope="user_turn:alpha-2",
        project="alpha",
        timeout=0.10,
    )
    with pytest.raises(InferenceRejected) as exc:
        broker.run(second, lambda: "must-not-run")
    assert exc.value.reason in {"deadline_before_admission", "deadline_too_close"}


def test_optional_lane_is_shed_proactively_at_hot_utilization(monkeypatch) -> None:
    monkeypatch.setenv("ERIS_BROKER_RPM_OPENAI", "2")
    monkeypatch.setenv("ERIS_BROKER_HOT_UTILIZATION", "0.5")
    broker = InferenceBroker()
    required = _request_for_provider(
        broker,
        provider="openai",
        lane="chat_main",
        optional=False,
        scope="user_turn:required",
    )
    assert broker.run(required, lambda: "ok") == "ok"

    advice = broker.optional_lane_advice("vision_ambient", provider="openai")
    assert advice["hot"] is True
    assert advice["allowed"] is False
    assert advice["threshold_multiplier"] > 1.0

    optional = _request_for_provider(
        broker,
        provider="openai",
        lane="vision_ambient",
        optional=True,
        scope="background:vision-hot",
    )
    with pytest.raises(InferenceRejected) as exc:
        broker.run(optional, lambda: "must-not-run")
    assert exc.value.reason == "provider_hot_optional"


def test_lane_slo_can_be_tuned_without_changing_callers(monkeypatch) -> None:
    from runtime.inference_broker import lane_policy

    monkeypatch.setenv("ERIS_BROKER_PRIORITY_VISION_AMBIENT", "77")
    monkeypatch.setenv("ERIS_BROKER_MAX_QUEUED_VISION_AMBIENT", "4")
    monkeypatch.setenv("ERIS_BROKER_DEADLINE_SEC_VISION_AMBIENT", "9.5")
    monkeypatch.setenv("ERIS_BROKER_LANE_CONCURRENCY_VISION_AMBIENT", "2")
    policy = lane_policy("vision_ambient")
    assert policy.priority == 77
    assert policy.max_queued == 4
    assert policy.deadline_seconds == 9.5
    assert policy.max_concurrency == 2
    assert policy.optional is True


def test_topic_budget_caps_repeated_background_work() -> None:
    from runtime.topic_budget import TopicBudget

    budget = TopicBudget(max_calls=2, window_seconds=60.0)
    assert budget.allow("same-topic", now=100.0)
    budget.record("same-topic", now=100.0)
    assert budget.remaining("same-topic", now=101.0) == 1
    budget.record("same-topic", now=102.0)
    assert budget.allow("same-topic", now=103.0) is False
    assert budget.allow("other-topic", now=103.0) is True
    assert budget.allow("same-topic", now=161.0) is True


def test_degraded_fastpath_only_handles_exact_closed_forms(monkeypatch) -> None:
    from runtime.degraded_chat_fastpath import build_degraded_reply

    monkeypatch.setenv("ERIS_DEGRADED_CHAT_FASTPATH", "1")
    greeting = build_degraded_reply("Xin chào!", language_hint="vi")
    assert greeting["degraded_fastpath"] is True
    assert greeting["text"] == "Chào. Tôi nhận được tin nhắn rồi."
    assert greeting["speech_text"] == "こんにちは。メッセージは受け取ったよ。"
    assert greeting["semantic_frame"]["user_act"] == "greeting"
    assert greeting["semantic_frame"]["tool_intent"] == "none"

    # Open relational meaning stays with the real semantic authority. The local
    # escape hatch is not allowed to rediscover regex discourse classification.
    assert build_degraded_reply("hôm nay em sao", language_hint="vi") == {}
    assert build_degraded_reply("giải thích kiến trúc broker", language_hint="vi") == {}


def test_router_uses_degraded_fastpath_only_after_remote_failure(monkeypatch) -> None:
    from types import SimpleNamespace

    from runtime.ai_orchestrated.model_router import AIRuntimeModelRouter

    monkeypatch.setenv("ERIS_DEGRADED_CHAT_FASTPATH", "1")
    router = AIRuntimeModelRouter(SimpleNamespace(client=None, fallback_client=None, style_client=None))
    payload = router.generate_public_reply(
        chat_history=[],
        user_text="xin chào",
        allow_fallback=True,
        language_hint="vi",
    )
    assert payload["text"]
    assert payload["semantic_frame"]["user_act"] == "greeting"
    assert payload["system_action"] == ""


def test_acceptance_report_computes_optional_429_reduction_from_baseline() -> None:
    from tools.report_pdf_acceptance import build_report

    baseline = [
        {"name": "rate_limit_429_total", "value": 1, "tags": {"lane": "vision_ambient"}}
        for _ in range(10)
    ]
    current = [
        {"name": "rate_limit_429_total", "value": 1, "tags": {"lane": "vision_ambient"}}
        for _ in range(2)
    ]
    report = build_report(current, baseline_events=baseline)
    assert report["comparison"]["optional_429_reduction_percent"] == 80.0
    assert report["acceptance"]["optional_429_reduction_ge_80_percent"] is True
    assert report["baseline"]["rate_limits"]["optional_lane_429"] == 10


def test_broker_shadow_mode_mirrors_rejection_but_does_not_block(monkeypatch) -> None:
    monkeypatch.setenv("ERIS_BROKER_MODE_VISION_AMBIENT", "shadow")
    broker = InferenceBroker()
    called = False

    with broker.user_turn("user_turn:shadow"):
        request = _request(
            broker,
            lane="vision_ambient",
            optional=True,
            scope="background:vision:shadow",
        )
        preview = broker.submit(request)
        assert preview.state == "rejected"
        assert preview.reason == "shed_optional_user_active"

        def remote_call(*, cancel_token=None):
            nonlocal called
            called = True
            assert cancel_token is not None
            return "shadow-result"

        # Shadow rollout records what enforcement would do, but intentionally
        # leaves behavior unchanged during the measurement phase.
        assert broker.run(request, remote_call) == "shadow-result"

    assert called is True


def test_broker_lane_kill_drops_optional_but_required_has_emergency_bypass(monkeypatch) -> None:
    monkeypatch.setenv("ERIS_BROKER_KILL_VISION_AMBIENT", "1")
    broker = InferenceBroker()
    optional = _request(
        broker,
        lane="vision_ambient",
        optional=True,
        scope="background:vision:killed",
    )
    with pytest.raises(InferenceRejected) as exc:
        broker.run(optional, lambda: "must-not-run")
    assert exc.value.reason == "lane_kill_switch"

    monkeypatch.setenv("ERIS_BROKER_KILL_CHAT_MAIN", "1")
    required = _request(
        broker,
        lane="chat_main",
        optional=False,
        scope="user_turn:required-kill",
    )
    assert broker.run(required, lambda: "emergency-chat") == "emergency-chat"


def test_submit_preview_checks_quota_and_concurrency(monkeypatch) -> None:
    monkeypatch.setenv("ERIS_BROKER_RPM_OPENAI", "1")
    broker = InferenceBroker()
    first = _request(
        broker,
        lane="chat_main",
        optional=False,
        scope="user_turn:first-preview",
    )
    assert broker.run(first, lambda: "ok") == "ok"
    second = _request(
        broker,
        lane="vision_ambient",
        optional=True,
        scope="background:preview-hot",
    )
    preview = broker.submit(second)
    assert preview.state == "rejected"
    assert preview.reason in {"provider_hot_optional", "quota_unavailable_optional"}


def test_semantic_shadow_lane_and_module_are_removed() -> None:
    import importlib.util

    from runtime.inference_broker import LANE_POLICIES

    assert "semantic_shadow" not in LANE_POLICIES
    assert importlib.util.find_spec("runtime.semantic_shadow") is None
def test_semantic_paraphrase_evaluator_scores_predictions() -> None:
    import json
    from pathlib import Path

    from tools.evaluate_semantic_paraphrases import evaluate

    dataset = json.loads(
        Path("tests/fixtures/semantic_paraphrases_vi.json").read_text(encoding="utf-8")
    )
    predictions = {}
    for case in dataset["cases"]:
        expected = case["expect"]
        predictions[case["id"]] = {
            field: values[0] if isinstance(values, list) else values
            for field, values in expected.items()
        }
    report = evaluate(dataset, predictions)
    assert report["accuracy"] == 1.0
    assert report["acceptance_ge_95_percent"] is True
    assert report["sample_count"] >= 20


def test_remote_inference_static_audit_is_clean() -> None:
    from pathlib import Path

    from tools.audit_remote_inference_paths import audit

    root = (Path(".") / "src").resolve() if (Path(".") / "src").is_dir() else Path(".").resolve()
    report = audit(root)
    assert report["parse_errors"] == []
    assert report["findings"] == []
    assert report["passed"] is True


def test_acceptance_report_v3_covers_full_pdf_metrics() -> None:
    from tools.report_pdf_acceptance import build_report

    events = [
        {"name": "user_turn_latency_ms", "value": 1000, "tags": {"turn_category": "small_talk"}},
        {"name": "ttft_ms", "value": 500, "tags": {"turn_category": "small_talk"}},
        {"name": "remote_calls_per_user_turn", "value": 1, "tags": {"reason": "small_talk"}},
        {"name": "input_tokens_by_lane", "value": 100, "tags": {"lane": "chat_main"}},
        {"name": "output_tokens_by_lane", "value": 20, "tags": {"lane": "chat_main"}},
        {"name": "input_tokens_by_lane", "value": 10, "tags": {"lane": "vision_ambient"}},
        {"name": "broker_queue_depth", "value": 1, "tags": {"lane": "chat_main"}},
        {"name": "broker_coalesced_total", "value": 2, "tags": {"lane": "vision_ambient"}},
        {"name": "broker_cancel_before_start_total", "value": 1, "tags": {}},
        {"name": "observer_discard_total", "value": 1, "tags": {"stage": "before_start"}},
        {"name": "prompt_hard_cap", "value": 5100, "tags": {}},
    ]
    report = build_report(
        events,
        semantic_evaluation={"accuracy": 0.96, "sample_count": 25},
    )
    assert report["schema"] == "eris.pdf_acceptance.v3"
    assert report["ttft_ms"]["p50"] == 500.0
    assert report["tokens"]["background_to_chat_ratio"] == 0.0833
    assert report["cancellation"]["coalesced_jobs"] == 2
    assert report["acceptance"]["discourse_paraphrase_accuracy_ge_95_percent"] is True
    assert report["acceptance"]["optional_background_on_critical_path_zero"] is True


def test_provider_remaining_and_reset_headers_gate_admission() -> None:
    """PDF pages 2 and 15-16: remaining/reset headers must affect admission."""

    broker = InferenceBroker()
    broker.record_response_headers(
        "openai",
        {
            "x-ratelimit-limit-requests": "10",
            "x-ratelimit-remaining-requests": "0",
            "x-ratelimit-reset-requests": "1.5s",
            "x-ratelimit-limit-tokens": "1000",
            "x-ratelimit-remaining-tokens": "900",
            "x-ratelimit-reset-tokens": "2m3s",
        },
    )
    request = _request(
        broker,
        lane="chat_main",
        optional=False,
        scope="user_turn:header-gate",
    )
    preview = broker.submit(request)
    assert preview.state == "queued"
    assert preview.reason == "quota_unavailable"

    snapshot = broker.snapshot("openai")
    assert snapshot["provider_header_remaining"]["rpm"] == 0
    assert snapshot["provider_header_remaining"]["tpm"] == 900
    assert 1.0 <= snapshot["provider_header_reset_remaining_s"]["rpm"] <= 1.5
    assert snapshot["provider_header_reset_remaining_s"]["tpm"] > 120.0


def test_async_broker_helper_settles_usage_without_blocking_event_loop() -> None:
    import asyncio

    broker = InferenceBroker()
    request = _request(
        broker,
        lane="chat_main",
        optional=False,
        scope="user_turn:async-success",
    )

    async def adapter(*, cancel_token=None):
        assert cancel_token is not None
        await asyncio.sleep(0.01)
        return {
            "usage": {"prompt_tokens": 7, "completion_tokens": 3},
            "choices": [],
        }

    result = asyncio.run(broker.run_async(request, adapter))
    assert result["usage"]["prompt_tokens"] == 7
    snapshot = broker.snapshot("openai")
    assert snapshot["itpm_used"] == 7
    assert snapshot["otpm_used"] == 3


def test_async_broker_cancels_native_coroutine_at_deadline() -> None:
    import asyncio

    broker = InferenceBroker()
    request = _request(
        broker,
        lane="post_observer",
        optional=True,
        scope="background:async-timeout",
        timeout=0.10,
    )
    cancelled = False

    async def adapter(*, cancel_token=None):
        nonlocal cancelled
        assert cancel_token is not None
        try:
            await asyncio.sleep(2.0)
        except asyncio.CancelledError:
            cancelled = True
            raise

    with pytest.raises(InferenceRejected) as exc:
        asyncio.run(broker.run_async(request, adapter))
    assert exc.value.reason == "deadline_exceeded"
    assert cancelled is True
    assert broker.list_inflight() == []


def test_observer_anchor_uses_typed_frame_and_can_disable_legacy_text_rules() -> None:
    from runtime.ai_orchestrated.contracts import CognitiveObservation
    from runtime.ai_orchestrated.model_router import AIRuntimeModelRouter

    unknown = CognitiveObservation.from_mapping(
        {}, turn_id="typed-anchor", turn_seq=1, source="test", latency_ms=0
    )
    frame = {
        "version": "semantic_frame.v1",
        "discourse_relation": "new_or_direct_turn",
        "user_act": "style_criticism",
        "target": "assistant",
    }
    anchored = AIRuntimeModelRouter._apply_deterministic_observation_anchor(
        unknown,
        user_text="một câu không chứa marker cũ",
        assistant_text="response",
        event_kind="chat",
        semantic_frame=frame,
    )
    assert anchored.user_act == "style_criticism"

    no_frame = AIRuntimeModelRouter._apply_deterministic_observation_anchor(
        unknown,
        user_text="bạn nói generic vl",
        assistant_text="response",
        event_kind="chat",
        semantic_frame={},
    )
    assert no_frame.user_act == "unknown"


def test_live_semantic_authority_audit_is_clean() -> None:
    from pathlib import Path

    from tools.audit_semantic_authority_paths import audit

    root = (Path(".") / "src").resolve() if (Path(".") / "src").is_dir() else Path(".").resolve()
    report = audit(root)
    assert report["parse_errors"] == []
    assert report["findings"] == []
    assert report["passed"] is True


def test_idempotency_key_blocks_duplicate_remote_attempt() -> None:
    broker = InferenceBroker()
    first = _request(
        broker,
        lane="post_observer",
        optional=True,
        scope="background:observer:one",
        idempotency_key="turn:42",
    )
    assert broker.run(first, lambda: "observed") == "observed"

    called = False
    duplicate = _request(
        broker,
        lane="post_observer",
        optional=True,
        scope="background:observer:duplicate",
        idempotency_key="turn:42",
    )

    def must_not_run():
        nonlocal called
        called = True
        return "duplicate"

    with pytest.raises(InferenceRejected) as exc:
        broker.run(duplicate, must_not_run)
    assert exc.value.reason == "duplicate_idempotency_recent"
    assert called is False
    assert broker.snapshot("openai")["idempotency_recent"] == 1


def test_rejected_before_provider_does_not_poison_idempotency_key() -> None:
    broker = InferenceBroker()
    with broker.user_turn("user_turn:block-optional"):
        blocked = _request(
            broker,
            lane="post_observer",
            optional=True,
            scope="background:observer:blocked",
            idempotency_key="turn:retryable",
        )
        with pytest.raises(InferenceRejected) as exc:
            broker.run(blocked, lambda: "must-not-run")
        assert exc.value.reason == "shed_optional_user_active"

    retry = _request(
        broker,
        lane="post_observer",
        optional=True,
        scope="background:observer:retry",
        idempotency_key="turn:retryable",
    )
    assert broker.run(retry, lambda: "now-runs") == "now-runs"


def test_durable_broker_state_survives_restart_without_user_content(monkeypatch, tmp_path) -> None:
    state_path = tmp_path / "broker_state.json"
    monkeypatch.setenv("ERIS_BROKER_STATE_PATH", str(state_path))
    monkeypatch.setenv("ERIS_BROKER_STATE_WRITE_INTERVAL_SEC", "0")

    first = InferenceBroker()
    first.record_response_headers(
        "openai",
        {
            "x-ratelimit-limit-requests": "17",
            "x-ratelimit-limit-tokens": "4321",
            "x-ratelimit-remaining-requests": "2",
            "x-ratelimit-reset-requests": "30s",
        },
    )
    first.record_rate_limit("openai", retry_after_s=5.0)
    request = _request(
        first,
        lane="post_observer",
        optional=True,
        scope="background:observer:persist",
        idempotency_key="turn:persisted",
    )
    # Cooldown would prevent this request; register a completed idempotency
    # tombstone directly through a separate provider so the test stays quick.
    request.provider = "gemini"
    assert first.run(request, lambda: "ok") == "ok"
    first.flush_durable_state()

    raw = state_path.read_text(encoding="utf-8")
    assert "prompt" not in raw.lower()
    assert "response" not in raw.lower()
    assert "turn:persisted" in raw

    second = InferenceBroker()
    snapshot = second.snapshot("openai")
    assert snapshot["rpm_limit"] == 17
    assert snapshot["tpm_limit"] == 4321
    assert snapshot["provider_header_remaining"]["rpm"] == 2
    assert snapshot["cooldown_remaining_s"] > 0.0

    duplicate = _request(
        second,
        lane="post_observer",
        optional=True,
        scope="background:observer:persist-duplicate",
        idempotency_key="turn:persisted",
    )
    duplicate.provider = "gemini"
    with pytest.raises(InferenceRejected) as exc:
        second.run(duplicate, lambda: "must-not-run")
    assert exc.value.reason == "duplicate_idempotency_recent"
