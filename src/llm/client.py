from llm.provider_policy import require_active_provider
import requests
import re
import os
import logging
import hashlib
import threading
import math
import time

from llm.token_budget import (
    detect_provider as _detect_provider,
    estimate_tokens as _estimate_tokens,
    enforce_prompt_cap as _enforce_prompt_cap,
    preflight as _preflight,
)
from llm.token_pressure import GLOBAL_TOKEN_PRESSURE_GOVERNOR
from runtime.observability import GLOBAL_OBSERVABILITY
from runtime.diagnostic_logging import safe_log
from runtime.inference_broker import (
    GLOBAL_INFERENCE_BROKER,
    current_inference_context,
    inference_context,
)
from llm.key_pool import get_provider_circuit
from llm.web_session_guard import GLOBAL_WEB_SESSION_GUARD
from llm.raw_trace import active_worker_role, begin_http_attempt
from llm.model_catalog import DEFAULT_OPEN_MODEL_ID, requires_open_model_hygiene
from llm.deepseek import (
    is_deepseek,
    normalize_model as normalize_deepseek_model,
    reasoning_effort as deepseek_reasoning_effort,
    thinking_mode as deepseek_thinking_mode,
)

_LOGGER = logging.getLogger("LLM")


def _gemini_auth_headers(api_key: str) -> dict:
    """Chọn đúng auth header theo loại credential Gemini.

    Google AI API chấp nhận hai loại credential:
    - API key (bắt đầu ``AIzaSy``): gửi qua ``x-goog-api-key``.
    - OAuth access token (bắt đầu ``AQ.``): gửi qua ``Authorization: Bearer``.
    Dùng sai header → 401 dù key/token hoàn toàn hợp lệ.
    """
    key = (api_key or "").strip()
    if key.startswith("ya29."):
        return {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    return {"x-goog-api-key": key, "Content-Type": "application/json"}




# Deliberation lanes are intentionally absent. A decision worker must not
# acquire hidden conversational memory from Whale2API; continuity lives in Eris
# state and is passed explicitly in the bounded workspace payload.
_PERSISTENT_WEB_SESSION_LANES = frozenset({
    "chat_main",
    "chat",
    "chat_tool_required",
    "directive",
    "autonomous",
    "inner",
    "private_mind",
    "daydream",
})

# Chat-only account affinity: pin consecutive turns of one conversation to a
# single gateway account via X-Whale2-Affinity-Key (FEAT-001 contract). This is
# a deliberately NARROWER set than _PERSISTENT_WEB_SESSION_LANES — background
# lanes (autonomous/inner/private_mind/daydream/directive) must never be pinned
# to one account, only the user-facing chat lanes.
_CHAT_AFFINITY_LANES = frozenset({
    "chat_main",
    "chat",
    "chat_tool_required",
})


def _compact_session_component(value: object, *, limit: int = 36) -> str:
    text = re.sub(r"[^a-zA-Z0-9_.:-]+", "-", str(value or "").strip()).strip("-:._")
    return (text or "unknown")[: max(8, int(limit))]


def _response_contract_name(response_format) -> str:
    if not isinstance(response_format, dict):
        return ""
    schema = response_format.get("json_schema")
    if isinstance(schema, dict):
        name = str(schema.get("name", "") or "").strip()
        if name:
            return name
    return str(response_format.get("type", "") or "").strip()


def _system_contract_seed(messages) -> str:
    """Return a stable prompt-contract seed, not the whole dynamic prompt.

    The public writer appends memory, grounding and relationship context to its
    system message. Hashing the complete message would create a new remote
    session every turn. Taking only the first paragraph preserves one session
    per role/contract while still separating a summarizer from public chat.
    """

    for message in messages or ():
        if not isinstance(message, dict) or str(message.get("role", "")).lower() != "system":
            continue
        content = message.get("content", "")
        if not isinstance(content, str):
            return ""
        first_paragraph = content.split("\n\n", 1)[0]
        return re.sub(r"\s+", " ", first_paragraph).strip()[:1200]
    return ""




def _attach_deepseek_json_schema(messages, response_format):
    """Giữ schema cho model dù DeepSeek API chỉ chấp nhận ``json_object``.

    Hạ ``json_schema`` xuống ``json_object`` mà không chuyển nội dung schema
    thành instruction sẽ âm thầm làm mất ràng buộc field/type. Gắn schema vào
    system prompt trước khi xác minh session để mọi thay đổi schema đều khiến
    gateway mở phiên mới, không kế thừa chỉ thị sai từ phiên cũ.
    """
    if not isinstance(response_format, dict) or response_format.get("type") != "json_schema":
        return messages
    requested = response_format.get("json_schema")
    if not isinstance(requested, dict) or not isinstance(requested.get("schema"), dict):
        return messages

    import json
    schema_json = json.dumps(requested, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    instruction = (
        "\n\n[OUTPUT_JSON_SCHEMA_REQUIREMENT]\n"
        "Trả lời bằng đúng một JSON object hợp lệ theo JSON Schema dưới đây; "
        "không có Markdown hoặc lời giải thích ngoài JSON.\n"
        f"JSON Schema: {schema_json}\n"
        "[/OUTPUT_JSON_SCHEMA_REQUIREMENT]"
    )
    updated = [dict(message) for message in messages]
    for index, message in enumerate(updated):
        if str(message.get("role", "")).lower() == "system" and isinstance(message.get("content"), str):
            message["content"] += instruction
            return updated
    # Không có system message: thêm một chỉ thị hệ thống, không viết đè câu hỏi.
    updated.insert(0, {"role": "system", "content": instruction.strip()})
    return updated

def _web_session_header(messages, response_format=None) -> str:
    """Build an isolated persistent Whale2API session key for this inference lane.

    Only conversational lanes persist. Background observers, summarizers,
    memory digest and generic transport calls intentionally omit the header so
    Whale2API creates and auto-deletes a one-shot DeepSeek Web session. This
    prevents a JSON-only summarizer prompt from becoming the immortal ancestor
    of the next public greeting, a surprisingly effective way to make software
    look haunted.
    """

    runtime_session = str(os.environ.get("ERIS_RUNTIME_SESSION_ID", "") or "").strip()
    if not runtime_session:
        return ""

    mode = str(os.environ.get("ERIS_WEB_SESSION_MODE", "isolated") or "isolated").strip().lower()
    if mode in {"off", "disabled", "none", "0"}:
        return ""
    if mode == "legacy":
        return runtime_session

    context = current_inference_context()
    lane = str(getattr(context, "lane", "transport") or "transport").strip().lower()
    configured_lanes = str(os.environ.get("ERIS_WEB_SESSION_PERSISTENT_LANES", "") or "").strip()
    persistent_lanes = (
        frozenset(
            item.strip().lower()
            for item in configured_lanes.split(",")
            if item.strip()
        )
        if configured_lanes
        else _PERSISTENT_WEB_SESSION_LANES
    )
    if lane not in persistent_lanes:
        return ""

    metadata = dict(getattr(context, "metadata", {}) or {})
    purpose = str(
        metadata.get("session_scope")
        or metadata.get("router_reason")
        or _response_contract_name(response_format)
        or "conversation"
    ).strip()
    contract_seed = "|".join(
        part for part in (
            lane,
            purpose,
            _response_contract_name(response_format),
            _system_contract_seed(messages),
        ) if part
    )
    digest = hashlib.sha256(contract_seed.encode("utf-8", errors="ignore")).hexdigest()[:16]
    return ":".join((
        _compact_session_component(runtime_session, limit=48),
        _compact_session_component(lane, limit=24),
        _compact_session_component(purpose, limit=36),
        digest,
    ))


def _chat_affinity_key() -> str:
    """Return the opaque X-Whale2-Affinity-Key value for the current lane.

    Pins consecutive turns of one runtime conversation to a single gateway
    account (FEAT-001 contract). Only user-facing chat lanes are eligible;
    background lanes stay on normal round-robin. Returns "" to mean "do not
    send the header", preserving exact current behavior when the kill-switch
    ERIS_CHAT_ACCOUNT_AFFINITY is off (default on = "1"; "0"/false/off/no =
    never send). The chat-only lane set is overridable via
    ERIS_CHAT_AFFINITY_LANES (comma-separated), mirroring how _web_session_header
    handles ERIS_WEB_SESSION_PERSISTENT_LANES. The value is derived from the same
    ERIS_RUNTIME_SESSION_ID X-Session-ID uses and compacted the same way, so it
    is stable across turns of one conversation and distinct across conversations.
    """

    toggle = str(os.environ.get("ERIS_CHAT_ACCOUNT_AFFINITY", "1") or "1").strip().lower()
    if toggle in {"0", "false", "off", "no"}:
        return ""

    runtime_session = str(os.environ.get("ERIS_RUNTIME_SESSION_ID", "") or "").strip()
    if not runtime_session:
        return ""

    lane = str(getattr(current_inference_context(), "lane", "transport") or "transport").strip().lower()
    configured_lanes = str(os.environ.get("ERIS_CHAT_AFFINITY_LANES", "") or "").strip()
    affinity_lanes = (
        frozenset(
            item.strip().lower()
            for item in configured_lanes.split(",")
            if item.strip()
        )
        if configured_lanes
        else _CHAT_AFFINITY_LANES
    )
    if lane not in affinity_lanes:
        return ""

    return _compact_session_component(runtime_session, limit=48)


def _runtime_reasoning_effort() -> str:
    try:
        from runtime.runtime_settings_store import runtime_text
        return runtime_text("chat", "reasoning_effort", default="medium")
    except Exception:
        return str(os.environ.get("ERIS_REASONING_EFFORT", "medium") or "medium")


def _pressure_provider(base_url: str, model_name: str) -> str:
    provider = _detect_provider(base_url, model_name)
    # A role-specific gateway key is a distinct quota/cooldown domain even when
    # it reaches the same DS2API URL. Without this suffix, one curator 429 could
    # poison chat admission despite the user explicitly provisioning a separate
    # credential. Humans bought two lanes; the broker should not repaint them as
    # one lane because their asphalt ends at the same host.
    pool = str(getattr(current_inference_context(), "credential_pool", "") or "").strip().lower()
    if not pool:
        return provider
    safe_pool = re.sub(r"[^a-z0-9._-]+", "-", pool).strip("-") or "pool"
    return f"{provider}__{safe_pool}"


def _record_pressure_request(base_url: str, model_name: str, messages, max_tokens: int) -> str:
    """Ghi request ở tầng transport để mọi lane đều góp vào pressure window."""

    require_active_provider(base_url=base_url, model=model_name)
    provider = _pressure_provider(base_url, model_name)
    try:
        prompt_tokens = _estimate_tokens(messages)
        lane = current_inference_context().lane or "transport"
        GLOBAL_TOKEN_PRESSURE_GOVERNOR.record_request(
            provider,
            lane=lane,
            prompt_tokens=prompt_tokens,
            output_tokens=max_tokens,
        )
        metric_tags = {"provider": provider, "lane": lane, "model": str(model_name or "unknown")[:80]}
        GLOBAL_OBSERVABILITY.observe("input_tokens_by_lane", prompt_tokens, tags=metric_tags)
        GLOBAL_OBSERVABILITY.observe("output_tokens_by_lane", max_tokens, tags=metric_tags)
        snapshot = GLOBAL_TOKEN_PRESSURE_GOVERNOR.snapshot(provider, lane=lane)
        GLOBAL_OBSERVABILITY.gauge("token_pressure_ratio", snapshot.pressure, tags={"provider": provider})
    except Exception as exc:
        safe_log(_LOGGER, "debug", "Token pressure request telemetry failed: %s", exc)
    return provider


def _record_pressure_success(provider: str) -> None:
    try:
        GLOBAL_TOKEN_PRESSURE_GOVERNOR.record_success(provider)
    except Exception:
        pass


def _record_pressure_429(
    provider: str,
    response,
    default: float = 20.0,
    *,
    model_name: str = "",
) -> None:
    try:
        retry_after = _retry_after_seconds(response, default)
        lane = current_inference_context().lane or "transport"
        GLOBAL_TOKEN_PRESSURE_GOVERNOR.record_rate_limit(
            provider,
            retry_after_s=retry_after,
            lane=lane,
        )
        GLOBAL_INFERENCE_BROKER.record_rate_limit(provider, retry_after_s=retry_after)
        tags = {
            "provider": provider,
            "lane": lane,
            "model": str(model_name or "unknown")[:80],
        }
        GLOBAL_OBSERVABILITY.increment("rate_limit_429_total", tags=tags)
        GLOBAL_OBSERVABILITY.observe("retry_after_seconds", retry_after, tags=tags)
    except Exception:
        pass


def _brokered_post(
    *,
    base_url: str,
    model_name: str,
    messages,
    max_tokens: int,
    timeout_seconds: float | None,
    url: str,
    **request_kwargs,
):
    """Reserve quota before the HTTP request, then report provider headers.

    PDF annotation, pages 6-10: every OpenAI/Gemini compatible transport call
    enters the same broker.  This is intentionally below LlmService so direct
    users such as PrivateMind and vision adapters cannot bypass admission.
    """

    provider = _pressure_provider(base_url, model_name)
    prompt_tokens = _estimate_tokens(messages)
    original_payload = request_kwargs.get("json")
    # Lấy lane TẠI caller trước khi broker có thể chuyển sang thread khác.
    # Trace chỉ dùng field ngắn; toàn bộ prompt nằm ở request body thực tế.
    trace_context = current_inference_context()
    trace_metadata = dict(getattr(trace_context, "metadata", {}) or {})
    trace_lane = str(getattr(trace_context, "lane", "transport") or "transport")
    trace_deepseek = is_deepseek(base_url, model_name)
    trace_worker_role = active_worker_role()
    # Gateway có session giữ lịch sử riêng: trim message sau khi chọn session
    # sẽ làm transcript thực tế khác transcript guard vừa xác nhận. Không cho
    # broker âm thầm rút prompt của một phiên persistent.
    session_headers = request_kwargs.get("headers") or {}
    has_persistent_session = bool(session_headers.get("X-Session-ID")) if isinstance(session_headers, dict) else False
    supports_input_degrade = bool(
        not has_persistent_session
        and isinstance(original_payload, dict)
        and isinstance(original_payload.get("messages"), list)
    )
    supports_output_degrade = bool(
        isinstance(original_payload, dict)
        and (
            "max_tokens" in original_payload
            or "max_completion_tokens" in original_payload
            or isinstance(original_payload.get("generationConfig"), dict)
        )
    )
    request = GLOBAL_INFERENCE_BROKER.request_from_context(
        provider=provider,
        model=model_name,
        estimated_input_tokens=prompt_tokens,
        reserved_output_tokens=max_tokens,
        timeout_seconds=timeout_seconds,
        metadata={
            "transport": "requests.post",
            # PDF annotation, page 8: required-lane degradation is legal only
            # when the adapter can actually rewrite the payload to match the
            # smaller reservation. The broker is an accountant, not a novelist.
            "input_degrade_supported": supports_input_degrade,
            "output_degrade_supported": supports_output_degrade,
        },
    )

    configured_timeout_seconds = None
    if timeout_seconds is not None:
        raw_timeout = float(timeout_seconds)
        if raw_timeout > 0.0 and math.isfinite(raw_timeout):
            configured_timeout_seconds = raw_timeout

    def send(*, cancel_token=None, timeout_seconds=None, inference_request=None):
        # PDF annotation, pages 5, 7 and 10: cancellation/deadline must reach
        # the adapter, not merely discard a Future after quota is already gone.
        actual_request = inference_request or request
        if cancel_token is not None:
            cancel_token.raise_if_cancelled(actual_request)
        if configured_timeout_seconds is None:
            effective_timeout = None
        elif timeout_seconds is None:
            effective_timeout = max(0.05, configured_timeout_seconds)
        else:
            effective_timeout = max(0.05, min(float(timeout_seconds), configured_timeout_seconds))

        effective_messages = list(messages or [])
        if supports_input_degrade and actual_request.estimated_input_tokens < prompt_tokens:
            effective_messages, _trimmed = _enforce_prompt_cap(
                effective_messages, max(1, int(actual_request.estimated_input_tokens))
            )
        effective_max_tokens = max(1, min(int(max_tokens), int(actual_request.reserved_output_tokens)))

        effective_kwargs = dict(request_kwargs)
        payload = effective_kwargs.get("json")
        if isinstance(payload, dict):
            payload = dict(payload)
            if supports_input_degrade and "messages" in payload:
                payload["messages"] = effective_messages
            if "max_tokens" in payload:
                payload["max_tokens"] = effective_max_tokens
            if "max_completion_tokens" in payload:
                payload["max_completion_tokens"] = effective_max_tokens
            generation_config = payload.get("generationConfig")
            if isinstance(generation_config, dict):
                generation_config = dict(generation_config)
                generation_config["maxOutputTokens"] = effective_max_tokens
                payload["generationConfig"] = generation_config
            effective_kwargs["json"] = payload

        _record_pressure_request(
            base_url, model_name, effective_messages, effective_max_tokens
        )
        # Ghi trước HTTP, để cả request timeout/đột ngột chết cũng còn đầu vào.
        # Chỉ DeepSeek; Gemini/Groq không vô tình chui vào log này.
        trace = begin_http_attempt(
            url=url,
            model=model_name,
            body=effective_kwargs.get("json"),
            headers=effective_kwargs.get("headers"),
            lane=trace_lane,
            cognitive_lane=str(trace_metadata.get("cognitive_lane", "") or ""),
            router_reason=str(trace_metadata.get("router_reason", "") or ""),
            worker_role=trace_worker_role,
        ) if trace_deepseek else None
        # Correlate Eris -> Whale2API -> DeepSeek Web without leaking headers to
        # trace. This header is used only by a patched local gateway; ignored elsewhere.
        if trace is not None and "headers" in effective_kwargs:
            effective_kwargs["headers"] = dict(effective_kwargs["headers"] or {})
            effective_kwargs["headers"]["X-Eris-Trace-ID"] = trace.request_id
        try:
            response = requests.post(url, timeout=effective_timeout, **effective_kwargs)
        except BaseException as exc:
            if trace is not None:
                trace.error(exc)
            raise
        if trace is not None:
            trace.response(response)
        if cancel_token is not None:
            cancel_token.raise_if_cancelled(actual_request)
        # retry-after, RPM and TPM headers are consumed once here.  The client
        # error path records a header-less 429 fallback only when necessary.
        GLOBAL_INFERENCE_BROKER.record_response_headers(provider, getattr(response, "headers", None))
        return response

    return GLOBAL_INFERENCE_BROKER.run(request, send)


def _http_error_with_body(response: requests.Response) -> requests.HTTPError:
    message = f"{response.status_code} Client Error: {response.reason} for url: {response.url}"
    body = (response.text or "").strip()
    if body:
        message = f"{message} | body: {body[:1200]}"
    exc = requests.HTTPError(message, response=response)
    try:
        exc.response_json = response.json()
    except Exception:
        exc.response_json = {}
    return exc


def _retry_after_seconds(response, default: float = 60.0) -> float:
    """Đọc Retry-After/retryDelay từ response 429, fallback về ``default``."""

    try:
        raw = str((getattr(response, "headers", {}) or {}).get("Retry-After", "")).strip()
        if raw:
            return max(1.0, min(float(raw), 3600.0))
    except (TypeError, ValueError):
        pass
    try:
        body = str(getattr(response, "text", "") or "")
        match = re.search(r'"retryDelay"\s*:\s*"?([0-9]+(?:\.[0-9]+)?)s?', body, flags=re.I)
        if match:
            return max(1.0, min(float(match.group(1)), 3600.0))
    except (TypeError, ValueError, OverflowError):
        pass
    return max(1.0, float(default))


def _key_pool_attempt_limit(_model_name: str, pool_size: int) -> int:
    """Giới hạn số project một request được phép thử khi gặp 429.

    PDF annotation, pages 6-9 and 19-20: retry fan-out is governed by one
    provider-wide setting.  Removed-model compatibility must not silently
    create a second quota policy.
    """

    size = max(1, int(pool_size or 1))
    raw = str(os.environ.get("ERIS_KEY_POOL_MAX_ATTEMPTS", "") or "").strip()
    if not raw:
        return size
    try:
        return max(1, min(size, int(raw)))
    except (TypeError, ValueError):
        return size


def _is_empty_output_error(exc: requests.HTTPError) -> bool:
    """Detect lỗi Groq 'model output must contain either output text or tool calls'.

    Lỗi này xảy ra khi model thinking Qwen với reasoning_effort=none
    + response_format=json_object tạo ra empty output. Cần retry mà không có
    response_format hoặc reasoning_effort.
    """
    try:
        body = exc.response.text or ""
        return "model output" in body and "tool calls" in body
    except Exception:
        return False


def _chat_payload_variants(payload: dict) -> list[dict]:
    """Sinh các payload tương thích nhưng không vô tình tắt reasoning trước.

    Biến thể đầu luôn là payload mạnh nhất. Chỉ khi endpoint trả 4xx vì không hiểu
    field mới lần lượt bỏ grammar/response_format/reasoning metadata. Với endpoint
    OpenAI-compatible cũ, ``max_completion_tokens`` cũng có biến thể ``max_tokens``.
    """

    variants = [payload]

    if "max_completion_tokens" in payload:
        legacy_tokens = dict(payload)
        legacy_tokens["max_tokens"] = legacy_tokens.pop("max_completion_tokens")
        variants.append(legacy_tokens)

    base_variants = list(variants)
    for base in base_variants:
        if "grammar" in base:
            without_grammar = dict(base)
            without_grammar.pop("grammar", None)
            variants.append(without_grammar)
        if "response_format" in base:
            without_response_format = dict(base)
            without_response_format.pop("response_format", None)
            variants.append(without_response_format)
        if "grammar" in base and "response_format" in base:
            minimal = dict(base)
            minimal.pop("grammar", None)
            minimal.pop("response_format", None)
            variants.append(minimal)

    # Aggregator OpenAI-compatible thuần có thể không nhận các field reasoning.
    # Bỏ chúng ở cuối cùng, sau khi đã thử đường parsed reasoning chuẩn.
    for optional_key in ("reasoning_effort", "reasoning_format", "include_reasoning", "thinking"):
        if any(optional_key in variant for variant in variants):
            for variant in list(variants):
                if optional_key not in variant:
                    continue
                relaxed = dict(variant)
                relaxed.pop(optional_key, None)
                variants.append(relaxed)
    return variants


def _is_groq_endpoint(base_url: str) -> bool:
    return "api.groq.com" in (base_url or "").strip().lower()


def _is_qwen_model(model_name: str) -> bool:
    return "qwen" in (model_name or "").strip().lower()


def _model_supports_reasoning_format(model_name: str) -> bool:
    """Return whether the configured, supported model family exposes parsed reasoning."""

    return "qwen" in (model_name or "").strip().lower()


def _normalize_reasoning_effort(model_name: str, requested: str | None) -> str | None:
    """Ánh xạ effort cho các họ Qwen còn được ERIS hỗ trợ.

    PDF cleanup annotation: removed-model compatibility left the model stack, so this
    adapter no longer carries dormant payload rules for it.
    """

    name = (model_name or "").strip().lower()
    raw = (requested or _runtime_reasoning_effort() or "").strip().lower()
    if "qwen3-32b" in name:
        return None
    if "qwen3.6" in name:
        return "none" if raw in {"none", "off", "0", "false"} else "default"
    return None



class LlamaServerClient:
    def __init__(self, base_url: str, timeout: int = 30):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.model_name = self._detect_model_name()

    def _detect_model_name(self) -> str:
        try:
            response = requests.get(f"{self.base_url}/models", timeout=15)
            response.raise_for_status()
            payload = response.json()
            models = payload.get("data", [])
            if models:
                return models[0].get("id", "local-model")
        except (requests.RequestException, TypeError, ValueError, KeyError) as exc:
            safe_log(_LOGGER, "debug", "Local model discovery failed: %s", exc)
        return "local-model"

    def create_chat_completion(
        self,
        *,
        messages,
        temperature,
        frequency_penalty,
        presence_penalty,
        max_tokens,
        response_format=None,
        grammar=None,
        timeout=None,
        reasoning_effort=None,
        reasoning_format=None,
    ):
        messages = _preflight(messages, base_url=self.base_url, model=self.model_name)
        pressure_provider = _pressure_provider(self.base_url, self.model_name)
        payload = {
            "model": self.model_name,
            "messages": messages,
            "temperature": temperature,
            "frequency_penalty": frequency_penalty,
            "presence_penalty": presence_penalty,
            "max_tokens": max_tokens,
        }
        if response_format is not None:
            payload["response_format"] = response_format
        if grammar is not None:
            payload["grammar"] = grammar

        last_error = None
        seen_signatures = set()
        for candidate in _chat_payload_variants(payload):
            signature = tuple(sorted(candidate.keys()))
            if signature in seen_signatures:
                continue
            seen_signatures.add(signature)

            actual_timeout = self.timeout if timeout is None else timeout
            response = _brokered_post(
                base_url=self.base_url,
                model_name=self.model_name,
                messages=messages,
                max_tokens=max_tokens,
                timeout_seconds=actual_timeout,
                url=f"{self.base_url}/chat/completions",
                json=candidate,
            )
            try:
                response.raise_for_status()
                _record_pressure_success(pressure_provider)
                return response.json()
            except requests.HTTPError as exc:
                last_error = _http_error_with_body(response)
                status_code = exc.response.status_code if exc.response is not None else None
                if status_code == 429:
                    _record_pressure_429(pressure_provider, response, model_name=self.model_name)
                if status_code is not None and status_code < 500:
                    continue
                raise last_error

        if last_error is not None:
            raise last_error
        raise RuntimeError("LlamaServerClient khong tao duoc request hop le.")


class OpenAICompatibleClient:
    def __init__(
        self,
        base_url: str,
        api_key: str,
        model_name: str,
        timeout: int = 30,
        key_pool=None,
        provider_circuit_id: str | None = None,
    ):
        require_active_provider(base_url=base_url, model=model_name)
        self.base_url = base_url.rstrip("/")
        self.key_pool = key_pool
        if not (api_key or "").strip() and key_pool is not None:
            lease = key_pool.acquire(allow_cooling=True)
            api_key = lease.key if lease is not None else ""
        self.api_key = self._normalize_api_key(api_key)
        configured_model = model_name.strip() or "gpt-5.4-nano"
        self.configured_model_name = configured_model
        self.model_name = (
            normalize_deepseek_model(configured_model)
            if is_deepseek(self.base_url, configured_model)
            else configured_model
        )
        self.timeout = timeout
        # Dedicated DeepSeek Web workers share a gateway but not a request budget.
        # A slow optional lane (inner/observer/style) must never open the circuit
        # for the companion lane that produces public chat.
        self.provider_circuit_id = str(provider_circuit_id or self.base_url).strip() or self.base_url
        self._provider_circuit = get_provider_circuit(self.provider_circuit_id)

    def _normalize_api_key(self, raw_value: str) -> str:
        candidate = (raw_value or "").strip()
        if not candidate:
            return ""

        # Allow users to paste a whole code snippet like api_key="sk-..."
        quoted = re.search(r'api_key\s*=\s*["\']([^"\']+)["\']', candidate, flags=re.IGNORECASE)
        if quoted:
            candidate = quoted.group(1).strip()

        token_match = re.search(r"\bg?sk[-_][A-Za-z0-9._\-]+\b", candidate)
        if token_match:
            candidate = token_match.group(0)

        candidate = candidate.strip()
        try:
            candidate.encode("ascii")
        except UnicodeEncodeError as exc:
            raise ValueError(
                "API key khong hop le. Hay chi dan dung gia tri key ASCII, vi du bat dau bang 'sk-'."
            ) from exc

        return candidate

    def create_chat_completion(
        self,
        *,
        messages,
        temperature,
        frequency_penalty,
        presence_penalty,
        max_tokens,
        response_format=None,
        grammar=None,
        timeout=None,
        reasoning_effort=None,
        reasoning_format=None,
        thinking_mode=None,
    ):
        messages = _preflight(messages, base_url=self.base_url, model=self.model_name)
        if is_deepseek(self.base_url, self.model_name):
            messages = _attach_deepseek_json_schema(messages, response_format)
        pressure_provider = _pressure_provider(self.base_url, self.model_name)
        if response_format and response_format.get("type") == "json_object":
            last_msg_has_json = False
            if messages:
                last_msg = messages[-1]
                content = last_msg.get("content", "")
                if isinstance(content, str) and "json" in content.lower():
                    last_msg_has_json = True
                elif isinstance(content, list):
                    for part in content:
                        if isinstance(part, dict) and "json" in part.get("text", "").lower():
                            last_msg_has_json = True
                            break
            if not last_msg_has_json and messages:
                messages = list(messages)
                last_msg = dict(messages[-1])
                if isinstance(last_msg.get("content"), str):
                    last_msg["content"] = last_msg["content"] + "\n\nReturn the output in json format."
                elif isinstance(last_msg.get("content"), list):
                    last_msg["content"] = list(last_msg["content"]) + [{"type": "text", "text": "\n\nReturn the output in json format."}]
                messages[-1] = last_msg

        deepseek_request = is_deepseek(self.base_url, self.model_name)
        is_official_deepseek = "api.deepseek.com" in str(self.base_url or "").lower()
        groq_reasoning = _is_groq_endpoint(self.base_url) and _model_supports_reasoning_format(self.model_name)
        payload = {
            "model": self.model_name,
            "messages": messages,
        }
        if deepseek_request:
            is_ds2api = "api.deepseek.com" not in str(self.base_url or "").lower() and ("6011" in str(self.base_url or "") or "localhost" in str(self.base_url or "") or "127.0.0.1" in str(self.base_url or ""))
            if is_ds2api:
                mode = "enabled"
            else:
                mode = deepseek_thinking_mode(self.configured_model_name, requested=thinking_mode)
            
            worker_role = str(getattr(self, "worker_role", "") or "").strip().lower()
            effective_role = (worker_role or str(active_worker_role() or "")).strip().lower()
            # Executive / Root có thể tắt thinking độc lập với các worker khác.
            # Check NÀY phải sau DS2API mode (line trên) để override được cả
            # trường hợp endpoint là localhost/DS2API (vốn force mode="enabled").
            # Hỗ trợ cả direct client (worker_role attribute) và production ContextVar (effective_role).
            if (worker_role == "executive" or effective_role in {"executive", "root"}) and os.environ.get("ERIS_EXECUTIVE_THINKING_DISABLED", "0") in {"1", "true", "yes"}:
                mode = "disabled"
            # Surface (public writer) có thể tắt thinking để trả lời nhanh hơn.
            # Production truyền role qua ContextVar (active_worker_role), KHÔNG qua
            # attribute self.worker_role, nên phải đọc ContextVar để check mới fire
            # được trên live path. Ưu tiên attribute worker_role (direct client),
            # fallback về active_worker_role() (production). Default BẬT (thinking
            # tắt cho surface) — env=0 để opt lại thinking. Check này sau DS2API
            # mode để override được localhost-force (mode="enabled").
            if effective_role == "surface" and os.environ.get("ERIS_SURFACE_THINKING_DISABLED", "1") in {"1", "true", "yes"}:
                mode = "disabled"
            payload["thinking"] = {"type": mode}

            payload["max_tokens"] = max_tokens if mode == "disabled" else max(max_tokens, 2048)
            if mode == "enabled":
                payload["reasoning_effort"] = deepseek_reasoning_effort(reasoning_effort)
            else:
                # Sampling controls are meaningful only in non-thinking mode.
                payload["temperature"] = temperature
                payload["frequency_penalty"] = frequency_penalty
                payload["presence_penalty"] = presence_penalty
        else:
            payload.update({
                "temperature": temperature,
                "frequency_penalty": frequency_penalty,
                "presence_penalty": presence_penalty,
            })
            # Groq tính reasoning + final answer chung trong completion budget.
            if groq_reasoning:
                payload["max_completion_tokens"] = max_tokens
                payload["top_p"] = 0.95
            else:
                payload["max_tokens"] = max_tokens
        if response_format is not None:
            if deepseek_request and response_format.get("type") == "json_schema":
                payload["response_format"] = {"type": "json_object"}
            else:
                payload["response_format"] = response_format
        if grammar is not None:
            payload["grammar"] = grammar

        if groq_reasoning:
            # JSON mode không cho reasoning_format=raw. parsed giữ nguyên năng lực
            # suy luận nhưng tách nó sang message.reasoning, content chỉ còn JSON.
            payload["reasoning_format"] = reasoning_format or "parsed"
            effort = _normalize_reasoning_effort(self.model_name, reasoning_effort)
            if effort is not None:
                payload["reasoning_effort"] = effort

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        # Các gateway chưa sửa vẫn bỏ FinalPrompt khi tái sử dụng session.
        # Guard xác minh transcript trước mỗi HTTP attempt; với legacy, giữ
        # nguyên hành vi cũ để rollback. ``off`` gửi full request không session.
        #
        # Chat account affinity (see _chat_affinity_key): the X-Whale2-Affinity-Key
        # header is gated by this SAME web_session_mode check. Two extra envs tune it:
        #   ERIS_CHAT_ACCOUNT_AFFINITY (default "1" = on; "0"/false/off/no = never
        #     send X-Whale2-Affinity-Key = exact current behavior, a kill-switch).
        #   ERIS_CHAT_AFFINITY_LANES (comma-separated override for the chat-only
        #     affinity lane set; empty = default _CHAT_AFFINITY_LANES).
        web_session_mode = str(os.environ.get("ERIS_WEB_SESSION_MODE", "isolated") or "isolated").strip().lower()
        session_base = (
            _web_session_header(messages, response_format=response_format)
            if deepseek_request and not is_official_deepseek else ""
        )
        if session_base and web_session_mode == "legacy":
            headers["X-Session-ID"] = session_base
        url = f"{self.base_url}/chat/completions"

        # Có pool: gặp 429 thì xoay key kế và thử lại, tối đa bằng số key trong pool.
        pool_size = self.key_pool.size if (self.key_pool is not None and self.key_pool.size > 0) else 1
        max_attempts = _key_pool_attempt_limit(self.model_name, pool_size)
        last_error = None
        attempted_indices: set[int] = set()
        for attempt in range(max(1, max_attempts)):
            key_lease = None
            attempt_key = self.api_key
            if self.key_pool is not None:
                key_lease = self.key_pool.acquire(exclude_indices=attempted_indices)
                if key_lease is None:
                    wait_s = self.key_pool.next_available_in()
                    from llm.provider_errors import ProviderUnavailable, ProviderErrorKind
                    raise ProviderUnavailable(ProviderErrorKind.RATE_LIMIT, role="key_pool")
                attempted_indices.add(key_lease.index)
                attempt_key = self._normalize_api_key(key_lease.key)
            # Header thuộc riêng HTTP attempt, không chia sẻ trạng thái giữa thread.
            attempt_headers = {**headers, "Authorization": f"Bearer {attempt_key}"}

            rate_limited = False
            auth_failed = False
            server_error = False
            seen_signatures = set()
            for candidate in _chat_payload_variants(payload):
                signature = tuple(sorted(candidate.keys()))
                if signature in seen_signatures:
                    continue
                seen_signatures.add(signature)

                # Mỗi variant/retry là một HTTP attempt riêng. Một HTTP 400,
                # timeout hoặc 429 cũng có thể đã chạm remote session, vì thế
                # attempt tiếp theo không được tái sử dụng phiên chưa commit.
                session_lease = None
                request_headers = dict(attempt_headers)
                if session_base and web_session_mode not in {"legacy", "off", "disabled", "none", "0"}:
                    session_lease = GLOBAL_WEB_SESSION_GUARD.reserve(
                        base_session_id=session_base,
                        base_url=self.base_url,
                        model=self.model_name,
                        credential=attempt_key,
                        messages=messages,
                        request_contract={
                            **{key: value for key, value in candidate.items() if key != "messages"},
                            # Gateway/DeepSeek có thể chỉ nhìn thấy json_object;
                            # vẫn phân biệt schema gốc, kể cả cùng tên.
                            "original_response_format": response_format,
                        },
                    )
                    request_headers["X-Session-ID"] = session_lease.session_id
                    # Chat-only account affinity (FEAT-001 contract). Under the
                    # SAME web_session_mode gate as X-Session-ID, pin consecutive
                    # turns of one conversation to a single gateway account. Set
                    # on the per-attempt copy so it re-applies on every retry.
                    affinity = _chat_affinity_key()
                    if affinity:
                        request_headers["X-Whale2-Affinity-Key"] = affinity
                try:
                    self._provider_circuit.before_request(self.provider_circuit_id)
                    actual_timeout = self.timeout if timeout is None else timeout
                    response = _brokered_post(
                        base_url=self.base_url,
                        model_name=self.model_name,
                        messages=messages,
                        max_tokens=max_tokens,
                        timeout_seconds=actual_timeout,
                        url=url,
                        headers=request_headers,
                        json=candidate,
                    )
                    response.raise_for_status()
                    try:
                        payload_json = response.json()
                    except ValueError as exc:
                        if session_lease is not None:
                            GLOBAL_WEB_SESSION_GUARD.abort(session_lease)
                        last_error = exc
                        continue

                    if isinstance(payload_json, dict) and "error" in payload_json:
                        err_detail = payload_json["error"]
                        err_msg = str(err_detail.get("message", "") if isinstance(err_detail, dict) else err_detail)
                        err_code = (err_detail.get("status_code", err_detail.get("status", err_detail.get("code")))
                                    if isinstance(err_detail, dict) else None)
                        try:
                            code = int(err_code)
                        except (ValueError, TypeError):
                            from llm.provider_errors import classify_provider_error, ProviderErrorKind
                            kind = classify_provider_error(err_msg)
                            code = (429 if kind == ProviderErrorKind.RATE_LIMIT else
                                    401 if kind == ProviderErrorKind.KEY_REVOKED else
                                    403 if kind == ProviderErrorKind.PROJECT_PERMISSION else 0)
                        if code in (401, 403, 429, 500, 502, 503, 504):
                            # Gateway 200 + JSON error vẫn là HTTP failure logic.
                            # Đưa qua đúng nhánh đánh dấu key/Retry-After hiện có.
                            synthetic = requests.Response()
                            synthetic.status_code = code
                            synthetic.reason = "gateway_error"
                            synthetic.url = url
                            synthetic.headers = dict(getattr(response, "headers", {}) or {})
                            synthetic._content = b'{"error":"gateway_error"}'
                            raise requests.HTTPError("gateway_error_envelope", response=synthetic)
                        # Không trả raw message có thể chứa key/user text lên log.
                        from llm.provider_errors import ProviderUnavailable, ProviderErrorKind
                        raise ProviderUnavailable(ProviderErrorKind.INVALID_RESPONSE_SHAPE, role="gateway_envelope")

                    choices = payload_json.get("choices") if isinstance(payload_json, dict) else None
                    if not isinstance(choices, list) or not choices:
                        from llm.provider_errors import ProviderUnavailable, ProviderErrorKind
                        raise ProviderUnavailable(ProviderErrorKind.INVALID_RESPONSE_SHAPE, role="empty_choices")
                    if isinstance(choices, list) and choices:
                        first_choice = choices[0] if isinstance(choices[0], dict) else {}
                        message = first_choice.get("message") if isinstance(first_choice, dict) else {}
                        content = str(message.get("content", "") or "").strip()
                        if not content:
                            last_error = RuntimeError("Empty output content received from provider")
                            raise last_error

                    # Chỉ lời trả thành công mới là bằng chứng server đã thêm
                    # turn vào session. Không dùng response lỗi hoặc output rỗng.
                    if session_lease is not None:
                        first = choices[0] if isinstance(choices, list) and choices and isinstance(choices[0], dict) else {}
                        reply = first.get("message") if isinstance(first.get("message"), dict) else {}
                        # Khi output bị cắt (length) hoặc kết thúc không rõ,
                        # transcript không thể xem là đã đồng bộ hoàn chỉnh.
                        if first.get("finish_reason") == "stop":
                            GLOBAL_WEB_SESSION_GUARD.commit(session_lease, reply.get("content", ""))
                        else:
                            GLOBAL_WEB_SESSION_GUARD.abort(session_lease)
                    self._provider_circuit.record_success()
                    _record_pressure_success(pressure_provider)
                    if self.key_pool is not None and key_lease is not None:
                        self.key_pool.mark_success(lease=key_lease)
                    return payload_json
                except requests.HTTPError as exc:
                    if session_lease is not None:
                        GLOBAL_WEB_SESSION_GUARD.abort(session_lease)
                    last_error = _http_error_with_body(exc.response) if exc.response is not None else exc
                    status_code = exc.response.status_code if exc.response is not None else None
                    if status_code == 429:
                        rate_limited = True
                        _record_pressure_429(pressure_provider, exc.response, model_name=self.model_name)
                        break  # thoát vòng variant để xoay key
                    if status_code in (401, 403):
                        auth_failed = True
                        break
                    if status_code in (500, 502, 503, 504):
                        server_error = True
                        break  # lỗi tạm thời phía provider, không phải lỗi key
                    if status_code is not None and status_code < 500:
                        continue  # thử biến thể payload kế (vd bỏ grammar/response_format)
                    raise last_error
                except (requests.Timeout, requests.ConnectionError) as exc:
                    if session_lease is not None:
                        GLOBAL_WEB_SESSION_GUARD.abort(session_lease)
                    last_error = exc
                    self._provider_circuit.record_failure(provider=self.provider_circuit_id, status=None)
                    break
                except Exception as exc:
                    if session_lease is not None:
                        GLOBAL_WEB_SESSION_GUARD.abort(session_lease)
                    last_error = exc
                    raise

            if rate_limited and self.key_pool is not None:
                retry_s = _retry_after_seconds(getattr(last_error, "response", None), 60.0)
                may_rotate = self.key_pool.mark_rate_limited(
                    retry_s, lease=key_lease, reason=f"http_429:{self.model_name}"
                )
                if may_rotate and attempt < max_attempts - 1:
                    continue
            if auth_failed and self.key_pool is not None:
                try:
                    from llm.provider_errors import ProviderErrorKind, classify_provider_error
                    kind = classify_provider_error(last_error)
                except Exception:
                    kind = None
                if str(kind) == "key_revoked":
                    self.key_pool.mark_permanently_dead(lease=key_lease, reason="http_auth_key_revoked")
                else:
                    self.key_pool.mark_dead(3600.0, lease=key_lease, reason="http_auth_or_permission")
                if attempt < max_attempts - 1:
                    continue
            if server_error:
                # 5xx thuộc provider/model, không đầu độc API key. Mở circuit
                # và để fallback chain xử lý provider khác.
                status = getattr(getattr(last_error, "response", None), "status_code", None)
                self._provider_circuit.record_failure(provider=self.provider_circuit_id, status=status)
            break

        if last_error is not None:
            raise last_error
        raise RuntimeError("OpenAICompatibleClient khong tao duoc request hop le.")


class ChainedFallbackClient:
    """Gộp nhiều client dự phòng thành MỘT, thử lần lượt cho tới khi có cái chạy.

    V96 bổ sung hai invariant runtime:
    - toàn chain dùng chung health state để outage không biến thành retry storm;
    - mọi tier dùng phần deadline còn lại, tuyệt đối không reset đồng hồ foreground.
    """

    def __init__(self, clients, logger=None, *, health_registry=None, health_key: str = ""):
        self._clients = [c for c in (clients or []) if c is not None]
        if not self._clients:
            raise ValueError("ChainedFallbackClient can it nhat mot client.")
        self._logger = logger or logging.getLogger("LLM")
        first = self._clients[0]
        self.base_url = getattr(first, "base_url", "")
        self.model_name = getattr(first, "model_name", "")
        self.key_pool = getattr(first, "key_pool", None)
        if health_registry is None:
            from runtime.provider_health import GLOBAL_REMOTE_LLM_HEALTH
            health_registry = GLOBAL_REMOTE_LLM_HEALTH
        self._provider_health = health_registry
        # V96: health là global registry nhưng key phải theo role. Executive token
        # hỏng không được kéo companion/chat còn khỏe xuống OFFLINE theo.
        explicit_health_key = str(health_key or "").strip().lower()
        worker_role = str(getattr(first, "worker_role", "") or "").strip().lower()
        derived_health_key = f"remote_llm:{worker_role}" if worker_role else "remote_llm"
        self._provider_health_key = explicit_health_key or derived_health_key
        # Credential 401 bị thu hồi không được thử lại ở mỗi user turn.
        # Chỉ lưu fingerprint, không lưu key trong cache trạng thái.
        self._auth_disabled_tiers: dict[int, str] = {}
        self._auth_lock = threading.Lock()

    def create_chat_completion(self, **kwargs):
        from runtime.provider_health import ProviderUnavailableFast

        parent_ctx = current_inference_context()
        lane = str(getattr(parent_ctx, "lane", "transport") or "transport").strip().lower()
        permit = self._provider_health.acquire(self._provider_health_key, lane=lane)
        if not permit.allowed:
            raise ProviderUnavailableFast(
                f"provider_health:{self._provider_health_key}:{permit.state.value}:{permit.reason}"
            )

        requested_timeout = kwargs.get("timeout")
        try:
            requested_budget = float(requested_timeout) if requested_timeout is not None else math.inf
        except (TypeError, ValueError):
            requested_budget = math.inf
        if requested_budget <= 0.0:
            requested_budget = math.inf

        parent_deadline = getattr(parent_ctx, "deadline_at", None)
        if parent_deadline is not None and math.isfinite(float(parent_deadline)):
            parent_remaining = max(0.05, float(parent_deadline) - time.time())
            chain_budget = min(requested_budget, parent_remaining)
        else:
            chain_budget = requested_budget
        chain_started = time.monotonic()

        last_error: Exception | None = None
        for idx, client in enumerate(self._clients):
            elapsed = max(0.0, time.monotonic() - chain_started)
            remaining_budget = (
                max(0.05, chain_budget - elapsed)
                if math.isfinite(chain_budget)
                else None
            )
            if remaining_budget is not None and remaining_budget <= 0.051:
                last_error = TimeoutError("foreground provider chain deadline exhausted")
                break

            # Quarantine riêng tier không có pool (tier có pool tự cách ly
            # theo ApiKeyLease). Credential thay đổi => tier được thử trở lại.
            credential = str(getattr(client, "api_key", "") or "")
            credential_id = hashlib.sha256(credential.encode("utf-8")).hexdigest()[:16]
            with self._auth_lock:
                blocked_id = self._auth_disabled_tiers.get(idx)
                if blocked_id and blocked_id != credential_id:
                    self._auth_disabled_tiers.pop(idx, None)
                    blocked_id = None
            if blocked_id and getattr(client, "key_pool", None) is None:
                from llm.provider_errors import ProviderUnavailable, ProviderErrorKind
                last_error = ProviderUnavailable(ProviderErrorKind.KEY_REVOKED, role="credential")
                continue

            client_url = str(getattr(client, "base_url", "") or "")
            is_ds2api = "api.deepseek.com" not in client_url.lower() and (
                "6011" in client_url or "localhost" in client_url or "127.0.0.1" in client_url
            )
            call_kwargs = dict(kwargs)

            requested = call_kwargs.get("timeout")
            try:
                requested_value = float(requested) if requested is not None else math.inf
            except (TypeError, ValueError):
                requested_value = math.inf
            if requested_value <= 0.0:
                requested_value = math.inf

            # Character HOTFIX 02: DeepSeek Web có thể suy luận rất lâu trước
            # khi trả JSON. Chỉ request verifier foreground KHÔNG dùng ngưỡng
            # failfast 90s mặc định. Sentinel 0 đi xuyên tới _brokered_post
            # để requests dùng timeout=None, broker dùng deadline=infinity.
            # Request vẫn bị hủy nếu cancel scope của turn bị thu hồi.
            unbounded_verifier = (lane == 'public_claim_verifier' and is_ds2api
                                  and (requested_timeout is None or requested_value == math.inf))
            if unbounded_verifier:
                timeout_value = (remaining_budget if remaining_budget is not None else 0.0)
            elif is_ds2api:
                worker_role = str(getattr(client, "worker_role", "") or "").strip().lower()
                executive_lane = worker_role == "executive"
                timeout_env = (
                    "ERIS_EXECUTIVE_DS2API_FAILFAST_TIMEOUT_S"
                    if executive_lane else "ERIS_DS2API_FAILFAST_TIMEOUT_S"
                )
                default_failfast = 90.0
                try:
                    failfast = float(os.environ.get(timeout_env, str(default_failfast)) or default_failfast)
                except (TypeError, ValueError):
                    failfast = default_failfast
                failfast = max(1.0, min(300.0, failfast))
                timeout_value = min(requested_value, failfast)
            else:
                timeout_value = min(requested_value, 90.0)

            if remaining_budget is not None:
                timeout_value = min(timeout_value, remaining_budget)
            if not math.isfinite(timeout_value):
                timeout_value = 90.0
            call_kwargs["timeout"] = (0.0 if unbounded_verifier and remaining_budget is None
                                       else max(0.05, float(timeout_value)))

            try:
                if idx == 0:
                    result = client.create_chat_completion(**call_kwargs)
                    self._provider_health.record_success(self._provider_health_key)
                    return result

                fallback_client_url = str(getattr(client, "base_url", "") or "")
                fallback_is_official_deepseek = "api.deepseek.com" in fallback_client_url.lower()
                if fallback_is_official_deepseek:
                    orig_max = call_kwargs.get("max_tokens")
                    if orig_max is not None:
                        try:
                            call_kwargs["max_tokens"] = max(int(orig_max), 2048)
                        except (TypeError, ValueError):
                            call_kwargs["max_tokens"] = 2048

                # V96: nested context vẫn giữ metadata/priority của parent và
                # inference_context sẽ clamp deadline con vào deadline cha.
                with inference_context(
                    parent_ctx.lane,
                    timeout_seconds=call_kwargs["timeout"],
                    optional=parent_ctx.optional,
                    priority=parent_ctx.priority,
                    cancel_scope=parent_ctx.cancel_scope,
                    metadata=dict(getattr(parent_ctx, "metadata", {}) or {}),
                ):
                    result = client.create_chat_completion(**call_kwargs)
                self._provider_health.record_degraded_success(
                    self._provider_health_key,
                    reason=f"fallback_tier_{idx + 1}_used",
                )
                return result
            except Exception as exc:  # noqa: BLE001 - thử nhà kế tiếp
                # V96: policy/cancellation của broker KHÔNG phải provider failure.
                # Nếu user turn đang active hoặc deadline đã hết, dừng chain ngay
                # và giữ health nguyên trạng thay vì tự đầu độc thành OFFLINE.
                try:
                    from runtime.inference_broker import InferenceRejected
                    control_plane_reject = isinstance(exc, InferenceRejected)
                except Exception:
                    control_plane_reject = False
                if control_plane_reject:
                    raise

                last_error = exc
                if getattr(client, "key_pool", None) is None:
                    from llm.provider_errors import classify_provider_error, ProviderErrorKind
                    if isinstance(exc, requests.HTTPError) and classify_provider_error(exc) == ProviderErrorKind.KEY_REVOKED:
                        with self._auth_lock:
                            self._auth_disabled_tiers[idx] = credential_id
                if is_ds2api:
                    self._logger.warning(
                        "WARNING: local DeepSeek Web proxy (Whale2API/DS2API) failed (%s), falling back to Official API.",
                        exc,
                    )
                else:
                    self._logger.warning(
                        "[FALLBACK CHAIN] tier %d/%d (%s) loi: %s -> thu tier ke",
                        idx + 1,
                        len(self._clients),
                        getattr(client, "model_name", "?"),
                        exc,
                    )
                continue

        if last_error is not None:
            # Tất cả tier đều hỏng: đây mới là bằng chứng đủ mạnh để đặt chain
            # OFFLINE. Từ đây background fail-fast và foreground chờ half-open probe.
            self._provider_health.record_failure(
                self._provider_health_key,
                last_error,
                hard=True,
            )
            raise last_error
        error = RuntimeError("ChainedFallbackClient khong co client kha dung.")
        self._provider_health.record_failure(self._provider_health_key, error, hard=True)
        raise error


class GeminiClient:
    """Compatibility error for callers of the removed provider."""

    def __init__(self, *args, **kwargs):
        raise ValueError("Gemini/Gemma providers are retired from Eris runtime.")
