# -*- coding: utf-8 -*-
"""DeepSeek Web cognitive-worker foundation.

This module intentionally does not change Whale2API or the DeepSeek Web
transport.  It only gives ERIS stable worker roles, one gateway API key per
role, and a small JSONL boundary for structured tasks.

The gateway key is expected to select the account/account-pool in Whale2API.
ERIS never needs the Gmail address or the DeepSeek password.
"""

from __future__ import annotations

import copy
import json
import os
import re
import threading
from dataclasses import dataclass
from typing import Any, Mapping

from eris_secrets import get_secret
from llm.raw_trace import worker_role_context


WORKER_ROLES: tuple[str, ...] = (
    "companion",
    "seed",
    "surface",
    "observer",
    "critic",
    "inner",
    "executive",
    "curator",
    "research",
    "learner",
)

# Structured workers default to canonical one-line JSON whenever the caller
# requests json_object/json_schema.
_DEFAULT_JSONL_ROLES = frozenset({"companion", "seed", "surface", "observer", "critic", "inner", "executive", "curator", "learner"})


class JsonlContractError(RuntimeError):
    """Raised when a structured worker still returns invalid JSON after repair."""


class RoleWorkerBusyError(RuntimeError):
    """Raised when a role-scoped worker actor intentionally refuses queued work."""


@dataclass(frozen=True, slots=True)
class DeepSeekWorkerSpec:
    role: str
    api_key: str
    base_url: str
    model: str
    jsonl_enabled: bool
    jsonl_repair_attempts: int = 1

    @property
    def configured(self) -> bool:
        return bool(self.api_key and self.base_url and self.model)


def normalize_worker_role(role: object) -> str:
    value = str(role or "").strip().lower().replace("-", "_")
    aliases = {
        "main": "companion",
        "chat": "companion",
        "social": "observer",
        "self_surface": "surface",
        "autonomous_surface": "surface",
        "small_talk": "surface",
        "random_chat_seed": "seed",
        "social_seed": "seed",
        "seed_worker": "seed",
        "deliberator": "critic",
        "private_mind": "inner",
        "memory": "inner",
    }
    value = aliases.get(value, value)
    if value not in WORKER_ROLES:
        raise ValueError(f"Unknown DeepSeek worker role: {role!r}")
    return value


def worker_env_name(role: object, suffix: str = "API_KEY") -> str:
    normalized = normalize_worker_role(role)
    return f"ERIS_DS_{normalized.upper()}_{suffix.strip().upper()}"


def _env_flag(name: str, default: bool) -> bool:
    raw = str(os.environ.get(name, "1" if default else "0") or "").strip().lower()
    if not raw:
        return default
    return raw not in {"0", "false", "no", "off", "none", "disabled"}


def _clean_api_key(value: str) -> str:
    candidate = str(value or "").strip()
    marker = candidate.upper()
    if (
        not candidate
        or marker.startswith("PASTE_")
        or marker in {"GATEWAY_KEY_SURFACE", "DAN_GATEWAY_KEY_SURFACE_VAO_DAY"}
    ):
        return ""
    return candidate


def _env_float(name: str, default: float, *, minimum: float = 0.0, maximum: float = 120.0) -> float:
    raw = str(os.environ.get(name, "") or "").strip()
    if not raw:
        return float(default)
    try:
        return max(float(minimum), min(float(maximum), float(raw)))
    except (TypeError, ValueError):
        return float(default)


def load_worker_spec(role: object) -> DeepSeekWorkerSpec:
    normalized = normalize_worker_role(role)
    role_prefix = f"ERIS_DS_{normalized.upper()}"
    role_env_key = _clean_api_key(os.environ.get(f"{role_prefix}_API_KEY", ""))
    role_secret_key = _clean_api_key(get_secret(f"{role_prefix}_API_KEY"))
    # V18 mặc định cho Surface dùng chung DS2API gateway key vì user đã có
    # daemon/account pool chung. Có thể đặt ERIS_DS_SURFACE_ALLOW_SHARED_KEY=0
    # để cô lập account riêng; đừng bắt người dùng khai lại cùng một key chỉ để
    # module chịu thừa nhận worker đang tồn tại.
    # Role-specific key luôn được ưu tiên cao hơn shared key.
    # ERIS_DS2API_API_KEY (shared gateway key) chỉ là fallback cuối khi role
    # chưa có key riêng. Không để shared key override role-specific key đã khai.
    shared_env_key = _clean_api_key(os.environ.get("ERIS_DS2API_API_KEY", "") or os.environ.get("ERIS_DS_API_KEY", ""))
    if normalized == "surface" and not _env_flag("ERIS_DS_SURFACE_ALLOW_SHARED_KEY", True):
        api_key = role_env_key or role_secret_key
    elif normalized == "seed" and shared_env_key:
        api_key = shared_env_key
    else:
        api_key = _clean_api_key(
            role_env_key
            or shared_env_key
            or role_secret_key
            or get_secret("ERIS_DS2API_API_KEY")
            or get_secret("ERIS_DS_API_KEY")
        )
    default_base_url = "http://127.0.0.1:6015/v1" if normalized == "seed" else "http://127.0.0.1:6011/v1"
    shared_base_url = "" if normalized == "seed" else (
        get_secret("ERIS_DS2API_BASE_URL") or get_secret("ERIS_DS_BASE_URL")
    )
    base_url = str(
        get_secret(f"{role_prefix}_BASE_URL")
        or shared_base_url
        or default_base_url
    ).strip()
    model = str(
        get_secret(f"{role_prefix}_MODEL")
        or get_secret("ERIS_DS2API_MODEL")
        or get_secret("ERIS_DS_MODEL")
        or "deepseek-v4-flash"
    ).strip()
    jsonl_default = normalized in _DEFAULT_JSONL_ROLES
    jsonl_enabled = _env_flag(f"{role_prefix}_JSONL", jsonl_default)
    try:
        repair_attempts = max(
            0,
            min(
                2,
                int(
                    os.environ.get(
                        f"{role_prefix}_JSONL_REPAIR_ATTEMPTS",
                        os.environ.get("ERIS_DS_JSONL_REPAIR_ATTEMPTS", "1"),
                    )
                    or (0 if normalized == "surface" else 1)
                ),
            ),
        )
    except (TypeError, ValueError):
        repair_attempts = 1
    return DeepSeekWorkerSpec(
        role=normalized,
        api_key=api_key,
        base_url=base_url,
        model=model,
        jsonl_enabled=jsonl_enabled,
        jsonl_repair_attempts=repair_attempts,
    )


def configured_worker_roles() -> tuple[str, ...]:
    return tuple(role for role in WORKER_ROLES if load_worker_spec(role).configured)


def _structured_response_requested(response_format: Any) -> bool:
    if not isinstance(response_format, Mapping):
        return False
    return str(response_format.get("type", "") or "").strip().lower() in {
        "json_object",
        "json_schema",
    }


def _response_schema(response_format: Any) -> Mapping[str, Any]:
    if not isinstance(response_format, Mapping):
        return {}
    if str(response_format.get("type", "") or "").strip().lower() == "json_schema":
        envelope = response_format.get("json_schema")
        if isinstance(envelope, Mapping) and isinstance(envelope.get("schema"), Mapping):
            return envelope["schema"]
    schema = response_format.get("schema")
    return schema if isinstance(schema, Mapping) else {}


def _validate_schema_shape(value: Any, schema: Mapping[str, Any], *, path: str = "$") -> None:
    """Validate shape đệ quy cho JSONL worker.

    Trước V6 lớp này chỉ nhìn required key ở top-level, nên object kiểu
    ``semantic_frame=true`` vẫn lọt qua dù schema đòi object. Ta kiểm tra nested
    properties/items nhưng vẫn cố ý không biến đây thành full jsonschema engine.
    Schema rỗng ``{}`` là soft field và được bỏ qua.
    """

    if not isinstance(schema, Mapping) or not schema:
        return
    expected_type = str(schema.get("type", "") or "").strip().lower()
    if expected_type == "object":
        if not isinstance(value, Mapping):
            raise JsonlContractError(f"schema_type:{path}:object")
        missing = [str(name) for name in (schema.get("required") or ()) if str(name) not in value]
        required_count = len(schema.get("required") or ())
        allowed_missing = required_count // 10 if path == "$" and required_count >= 10 else 0
        if len(missing) > allowed_missing:
            raise JsonlContractError(f"schema_required_missing:{path}:{','.join(missing)}")
        properties = schema.get("properties")
        if isinstance(properties, Mapping):
            for name, child_schema in properties.items():
                if str(name) not in value or not isinstance(child_schema, Mapping):
                    continue
                _validate_schema_shape(value[str(name)], child_schema, path=f"{path}.{name}")
        return
    if expected_type == "array":
        if not isinstance(value, list):
            raise JsonlContractError(f"schema_type:{path}:array")
        item_schema = schema.get("items")
        if isinstance(item_schema, Mapping):
            for index, item in enumerate(value):
                _validate_schema_shape(item, item_schema, path=f"{path}[{index}]")
        return
    if expected_type == "string":
        if not isinstance(value, str):
            raise JsonlContractError(f"schema_type:{path}:string")
    elif expected_type == "boolean":
        if not isinstance(value, bool):
            raise JsonlContractError(f"schema_type:{path}:boolean")
    elif expected_type == "integer":
        if not isinstance(value, int) or isinstance(value, bool):
            raise JsonlContractError(f"schema_type:{path}:integer")
    elif expected_type == "number":
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            raise JsonlContractError(f"schema_type:{path}:number")

    enum = schema.get("enum")
    if isinstance(enum, list) and enum and value not in enum:
        raise JsonlContractError(f"schema_enum:{path}")


def _append_schema_instruction(messages: Any, *, role: str, response_format: Any) -> list[dict[str, Any]]:
    copied = [dict(item) for item in (messages or []) if isinstance(item, Mapping)]
    schema = _response_schema(response_format)
    required = [str(name) for name in (schema.get("required") or ())]
    if not required:
        return copied
    schema_dump = json.dumps(schema, ensure_ascii=False, separators=(",", ":"))
    copied.append({
        "role": "user",
        "content": (
            f"ERIS {role} structured-output contract: return the OUTPUT object only; "
            "do not echo the input envelope and do not invent a wrapper object. "
            f"Required top-level keys: {', '.join(required)}. JSON only.\n"
            f"EXPECTED SCHEMA: {schema_dump}"
        ),
    })
    return copied


def _strip_code_fence(text: str) -> str:
    clean = str(text or "").strip()
    clean = re.sub(r"<think>.*?</think>", "", clean, flags=re.DOTALL | re.IGNORECASE).strip()
    clean = re.sub(r"^```(?:json|jsonl)?\s*", "", clean, flags=re.IGNORECASE)
    clean = re.sub(r"\s*```$", "", clean)
    return clean.strip()


def canonical_json_line(text: str) -> str:
    """Parse one JSON value and return canonical UTF-8 JSONL content."""

    clean = _strip_code_fence(text)
    if not clean:
        raise JsonlContractError("empty_jsonl_output")

    try:
        value = json.loads(clean, strict=False)
    except json.JSONDecodeError:
        object_start = clean.find("{")
        array_start = clean.find("[")
        starts = [x for x in (object_start, array_start) if x >= 0]
        if not starts:
            raise JsonlContractError("missing_json_value")
        start = min(starts)
        decoder = json.JSONDecoder(strict=False)
        try:
            value, _ = decoder.raw_decode(clean[start:])
        except json.JSONDecodeError as exc:
            raise JsonlContractError(f"invalid_json:{exc.msg}") from exc

    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _extract_openai_content(payload: Any) -> str:
    if not isinstance(payload, Mapping):
        return ""
    choices = payload.get("choices")
    if isinstance(choices, list) and choices:
        first = choices[0] if isinstance(choices[0], Mapping) else {}
        message = first.get("message") if isinstance(first, Mapping) else {}
        if isinstance(message, Mapping):
            return str(message.get("content", "") or "")
    return ""


def _replace_openai_content(payload: Any, content: str) -> Any:
    if not isinstance(payload, Mapping):
        return payload
    result = copy.deepcopy(dict(payload))
    choices = result.get("choices")
    if not isinstance(choices, list) or not choices:
        return result
    first = choices[0]
    if not isinstance(first, dict):
        return result
    message = first.get("message")
    if not isinstance(message, dict):
        message = {}
        first["message"] = message
    message["content"] = content
    return result


def _append_jsonl_repair_instruction(
    messages: Any,
    *,
    role: str,
    response_format: Any = None,
    error: Exception | None = None,
) -> list[dict[str, Any]]:
    copied = [dict(item) for item in (messages or []) if isinstance(item, Mapping)]
    schema = _response_schema(response_format)
    required = [str(name) for name in (schema.get("required") or ())]
    required_clause = f" Required top-level keys: {', '.join(required)}." if required else ""
    error_clause = f" Validation failure: {error}." if error is not None else ""
    schema_dump = f"\nEXPECTED SCHEMA: {json.dumps(schema, ensure_ascii=False, separators=(',', ':'))}" if schema else ""
    copied.append(
        {
            "role": "user",
            "content": (
                "The previous response violated the structured-output contract. "
                f"You are the ERIS {role} worker. Return exactly ONE valid JSON value "
                "on ONE physical line. No markdown, no code fence, no explanation, "
                "and no text before or after the JSON. Do not echo the input envelope. "
                f"Preserve the requested schema.{required_clause}{error_clause}{schema_dump}"
            ),
        }
    )
    return copied


class JsonlWorkerClient:
    """Thin client decorator for role metadata and canonical JSONL output.

    Free-text requests pass through untouched.  For json_object/json_schema
    requests, selected worker roles normalize message.content to a single JSON
    line and can perform one strict repair retry.  This keeps existing ERIS call
    sites compatible because the outer OpenAI-compatible response shape is not
    changed.
    """

    def __init__(
        self,
        client: Any,
        *,
        role: str,
        jsonl_enabled: bool = True,
        repair_attempts: int = 1,
    ) -> None:
        self._client = client
        self.worker_role = normalize_worker_role(role)
        self.jsonl_enabled = bool(jsonl_enabled)
        self.repair_attempts = max(0, min(2, int(repair_attempts)))
        self.base_url = getattr(client, "base_url", "")
        self.model_name = getattr(client, "model_name", "")
        self.configured_model_name = getattr(client, "configured_model_name", self.model_name)
        self.key_pool = getattr(client, "key_pool", None)

        # V4 - khai báo profile inference một cách tường minh.
        #
        # Worker trong module này KHÔNG phải model local 4B. Nó là DeepSeek Web
        # worker đi qua DS2API/Whale2API gateway. ``base_url`` có thể là localhost
        # (vì gateway chạy trên máy người dùng), nhưng model thật vẫn nằm ở remote.
        # Gắn metadata ngay trên client giúp router/scheduler không phải đoán tài
        # nguyên từ tên tier legacy như ``subconscious_4b``.
        self.inference_backend = "ds2api_deepseek_web"
        self.inference_resource_class = "remote_api"
        self._actor_lock = threading.Lock()
        self._actor_enabled = _env_flag(
            f"ERIS_DS_{self.worker_role.upper()}_ACTOR",
            _env_flag("ERIS_DS_ROLE_ACTORS", True),
        )
        self._drop_if_busy = _env_flag(
            f"ERIS_DS_{self.worker_role.upper()}_DROP_IF_BUSY",
            self.worker_role in {"inner", "seed"},
        )

    def __getattr__(self, name: str) -> Any:
        return getattr(self._client, name)

    def _actor_acquire_timeout(self, request_timeout: Any) -> float:
        default_wait = _env_float("ERIS_DS_ACTOR_WAIT_TIMEOUT_S", 2.0, minimum=0.0, maximum=120.0)
        role_wait = _env_float(
            f"ERIS_DS_{self.worker_role.upper()}_ACTOR_WAIT_TIMEOUT_S",
            default_wait,
            minimum=0.0,
            maximum=120.0,
        )
        try:
            timeout = float(request_timeout)
        except (TypeError, ValueError):
            return role_wait
        if timeout <= 0:
            return role_wait
        return max(0.0, min(role_wait, timeout))

    def create_chat_completion(self, **kwargs: Any) -> Any:
        if self._actor_enabled:
            if self._drop_if_busy:
                acquired = self._actor_lock.acquire(blocking=False)
            else:
                wait_s = self._actor_acquire_timeout(kwargs.get("timeout"))
                acquired = self._actor_lock.acquire(timeout=wait_s)
            if not acquired:
                raise RoleWorkerBusyError(f"worker={self.worker_role} busy")
            try:
                return self._create_chat_completion_locked(**kwargs)
            finally:
                self._actor_lock.release()
        return self._create_chat_completion_locked(**kwargs)

    def _create_chat_completion_locked(self, **kwargs: Any) -> Any:
        structured = self.jsonl_enabled and _structured_response_requested(kwargs.get("response_format"))
        attempts = 1 + (self.repair_attempts if structured else 0)
        last_error: Exception | None = None
        request_kwargs = dict(kwargs)
        base_messages = kwargs.get("messages")
        if structured:
            base_messages = _append_schema_instruction(
                base_messages,
                role=self.worker_role,
                response_format=kwargs.get("response_format"),
            )
            request_kwargs["messages"] = base_messages

        for attempt in range(attempts):
            if attempt:
                request_kwargs["messages"] = _append_jsonl_repair_instruction(
                    base_messages,
                    role=self.worker_role,
                    response_format=kwargs.get("response_format"),
                    error=last_error,
                )
            # ContextVar thay vì gắn role lên client: nhiều worker có thể dùng
            # cùng transport mà không dẫm nhãn trace của nhau.
            with worker_role_context(self.worker_role):
                payload = self._client.create_chat_completion(**request_kwargs)
            if not structured:
                return payload
            # Dialogue survives a malformed envelope. The router owns immediate
            # delivery and critic recovery; do not spend another foreground call.
            from llm.dialogue_recovery import recover_dialogue
            if recover_dialogue(_extract_openai_content(payload), kwargs.get("response_format") or {}):
                # Preserve the original wire response and return success through
                # fallback chains; bad formatting is not a provider outage.
                return payload
            try:
                line = canonical_json_line(_extract_openai_content(payload))
                schema = _response_schema(kwargs.get("response_format"))
                if schema:
                    _validate_schema_shape(json.loads(line), schema)
                return _replace_openai_content(payload, line)
            except JsonlContractError as exc:
                last_error = exc
                continue

        raise JsonlContractError(
            f"worker={self.worker_role} failed JSONL contract: {last_error or 'unknown'}"
        )


__all__ = [
    "DeepSeekWorkerSpec",
    "JsonlContractError",
    "JsonlWorkerClient",
    "RoleWorkerBusyError",
    "WORKER_ROLES",
    "canonical_json_line",
    "configured_worker_roles",
    "load_worker_spec",
    "normalize_worker_role",
    "worker_env_name",
]
