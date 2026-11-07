# -*- coding: utf-8 -*-
"""
runtime/private_mind_model_adapter.py
=====================================

Adapter API cho private mind:
- evaluate_fragment(evaluator_name, dossier)
- synthesize_judgment(dossier, fragments)

Adapter này ưu tiên dùng `llm_service` nếu được inject từ coordinator; nếu không
thì cố build provider từ env giống các adapter runtime khác. Khi API hỏng, nó
trả None để rule fallback chạy tiếp. Nội tâm không nên chết vì HTTP 500, tuy thế
cũng đừng biến HTTP 500 thành thơ.
"""

from __future__ import annotations
from core.di import resolve_dependency

import json
import os
import re
import socket
import time
import requests
import requests.exceptions
from ast import literal_eval
from dataclasses import dataclass, field, replace
from typing import Any, Mapping

from cortex.private_mind.contracts import EvidenceDossier, ThoughtFragment, compact_text, to_plain
from eris_secrets import apply_secrets_to_environ, get_secret
from llm.client import OpenAICompatibleClient
from llm.provider_policy import retired_provider
from llm.deepseek import DEEPSEEK_BASE_URL, DEEPSEEK_DEFAULT_MODEL, normalize_model as normalize_deepseek_model
from llm.key_pool import get_key_pool
from llm.model_catalog import DEFAULT_OPEN_MODEL_ID


def _env_flag(name: str, default: bool) -> bool:
    raw = str(os.getenv(name, "1" if default else "0") or "").strip().lower()
    if not raw:
        return default
    return raw not in {"0", "false", "no", "off", "none"}


def _as_float(value: Any, default: float) -> float:
    try:
        return float(value)
    except Exception:
        return float(default)


def _classify_transport_exception(exc: Exception) -> str:
    """Phân loại exception thành 'transient' hoặc 'fixed_config'.

    Quy tắc (theo *kiểu* và *status*, không theo message):
    - requests.HTTPError / exception có .response.status_code:
        429, 5xx (500-599) → 'transient'
        4xx khác (400, 401, 403...) → 'fixed_config'
    - requests.exceptions.Timeout / ReadTimeout / ConnectTimeout / ConnectionError → 'transient'
    - urllib.error.HTTPError: dùng .code cùng quy tắc status
    - urllib.error.URLError / socket.timeout / TimeoutError → 'transient'
    - Mặc định (không nhận diện) → 'fixed_config' (an toàn: không retry vô cớ)
    """
    # requests.HTTPError với .response (hoặc bất kỳ exception có .response.status_code)
    response = getattr(exc, "response", None)
    if response is not None:
        try:
            status = int(response.status_code)
            if status == 429 or 500 <= status <= 599:
                return "transient"
            return "fixed_config"
        except Exception:
            pass

    # requests timeout / connection errors
    if isinstance(exc, (requests.exceptions.Timeout, requests.exceptions.ConnectionError)):
        return "transient"

    # stdlib timeout và OSError (ECONNRESET, ETIMEDOUT, v.v.)
    if isinstance(exc, (socket.timeout, TimeoutError, OSError)):
        return "transient"

    return "fixed_config"


@dataclass(slots=True, frozen=True)
class PrivateMindModelAdapterConfig:
    enabled: bool = field(default_factory=lambda: _env_flag("ERIS_PRIVATE_MIND_API_ENABLED", False))
    provider: str = field(default_factory=lambda: str(os.getenv("ERIS_PRIVATE_MIND_PROVIDER", os.getenv("ERIS_DAYDREAM_PROVIDER", "deepseek")) or "deepseek").strip().lower())
    model: str = field(default_factory=lambda: str(os.getenv("ERIS_PRIVATE_MIND_MODEL", os.getenv("ERIS_DAYDREAM_MODEL", "deepseek-flash")) or "deepseek-flash").strip())
    base_url: str = field(default_factory=lambda: str(os.getenv("ERIS_PRIVATE_MIND_BASE_URL", os.getenv("ERIS_REMOTE_BASE_URL", "")) or "").strip())
    api_key: str = field(default_factory=lambda: str(os.getenv("ERIS_PRIVATE_MIND_API_KEY", "") or "").strip())
    timeout_seconds: float = field(default_factory=lambda: max(4.0, _as_float(os.getenv("ERIS_PRIVATE_MIND_TIMEOUT", "90"), 90.0)))
    # T1.3: synthesis/judgment nặng hơn fragment → timeout dài hơn (mặc định 90s
    # hoặc ≥ timeout chung). Fragment vẫn fail-fast bằng timeout_seconds (90s).
    synthesis_timeout_seconds: float = field(
        default_factory=lambda: max(
            _as_float(os.getenv("ERIS_PRIVATE_MIND_TIMEOUT", "90"), 90.0),
            _as_float(os.getenv("ERIS_PRIVATE_MIND_SYNTHESIS_TIMEOUT", "90"), 90.0),
        )
    )
    # Nhiệt độ thấp khiến local model nhỏ dễ lặp từ hoặc dính RECITATION filter,
    # rồi trả output cụt/rỗng -> invalid_json. Để mặc định ấm hơn để phá vòng lặp.
    temperature: float = field(default_factory=lambda: _as_float(os.getenv("ERIS_PRIVATE_MIND_TEMPERATURE", "0.5"), 0.5))
    max_tokens_fragment: int = field(default_factory=lambda: max(128, int(os.getenv("ERIS_PRIVATE_MIND_FRAGMENT_TOKENS", "700") or 700)))
    max_tokens_judgment: int = field(default_factory=lambda: max(256, int(os.getenv("ERIS_PRIVATE_MIND_JUDGMENT_TOKENS", "900") or 900)))
    # Gemma Residue Curator gom/dedupe/nén residue. Tác vụ ngắn, có schema chặt.
    max_tokens_residue: int = field(default_factory=lambda: max(256, int(os.getenv("ERIS_PRIVATE_RESIDUE_CURATOR_TOKENS", "700") or 700)))
    transient_max_retries: int = field(default_factory=lambda: max(0, int(os.getenv("ERIS_PRIVATE_MIND_TRANSIENT_RETRIES", "1") or 1)))
    transient_backoff_base: float = field(default_factory=lambda: max(0.1, _as_float(os.getenv("ERIS_PRIVATE_MIND_TRANSIENT_BACKOFF", "0.5"), 0.5)))
    transient_cooldown_threshold: int = field(default_factory=lambda: max(1, int(os.getenv("ERIS_PRIVATE_MIND_TRANSIENT_THRESHOLD", "2") or 2)))
    transient_cooldown_seconds: float = field(default_factory=lambda: max(5.0, _as_float(os.getenv("ERIS_PRIVATE_MIND_TRANSIENT_COOLDOWN", "60"), 60.0)))
    # Free-text mode cho fragment: model nhỏ (local) chỉ nhả MỘT câu nghĩ ngắn,
    # KHÔNG ép JSON-schema. Tránh vòng invalid_json → cooldown làm câm model nhỏ.
    freetext_fragments: bool = field(default_factory=lambda: _env_flag("ERIS_PRIVATE_MIND_FRAGMENT_FREETEXT", False))

    @classmethod
    def fragment_overlay_from_env(cls, base: "PrivateMindModelAdapterConfig") -> "PrivateMindModelAdapterConfig":
        """Config riêng cho fragment: nếu khai ERIS_PRIVATE_MIND_FRAGMENT_PROVIDER thì
        trỏ fragment sang provider riêng + bật free-text;
        nếu không khai gì thì dùng chung config (giữ hành vi cũ)."""
        provider = str(os.getenv("ERIS_PRIVATE_MIND_FRAGMENT_PROVIDER", "") or "").strip().lower()
        if not provider:
            return base
        base_url = str(os.getenv("ERIS_PRIVATE_MIND_FRAGMENT_BASE_URL", "") or "").strip()
        model = str(os.getenv("ERIS_PRIVATE_MIND_FRAGMENT_MODEL", "") or "").strip() or base.model
        api_key = str(os.getenv("ERIS_PRIVATE_MIND_FRAGMENT_API_KEY", "") or "").strip()
        return replace(
            base,
            enabled=True,
            provider=provider,
            base_url=base_url,
            model=model,
            api_key=api_key,
            freetext_fragments=_env_flag("ERIS_PRIVATE_MIND_FRAGMENT_FREETEXT", True),
        )


@dataclass(slots=True)
class PrivateMindModelResult:
    ok: bool
    parsed: dict[str, Any] = field(default_factory=dict)
    content: str = ""
    error: str = ""
    error_kind: str = ""
    raw: dict[str, Any] = field(default_factory=dict)


class RuntimePrivateMindModelAdapter:
    """Adapter chung cho 4 mini-evaluator và LocalModelInnerSynthesizer."""

    def __init__(
        self,
        *,
        config: PrivateMindModelAdapterConfig | None = None,
        llm_service: Any = None,
        logger: Any = None,
        client_attr: str = "inner_client",
        strict_client_attr: bool = False,
    ) -> None:
        self.config = resolve_dependency(config, lambda: PrivateMindModelAdapterConfig())
        self.llm_service = llm_service
        self.logger = logger
        self.client_attr = str(client_attr or "inner_client").strip() or "inner_client"
        self.strict_client_attr = bool(strict_client_attr)
        self._provider_client: Any | None = None
        self._calls = 0
        self._ok_calls = 0
        self._failed_calls = 0
        self._total_latency_ms = 0.0
        self._total_tokens = 0
        self._prompt_tokens = 0
        self._completion_tokens = 0
        self._last_error = ""
        self._last_call_at = 0.0
        self._invalid_json_streak = 0
        self._json_cooldown_until = 0.0
        self._transient_streak = 0
        self._transient_cooldown_until = 0.0
        self._last_error_kind = ""
        # Synthesis-specific streak: fragment success không reset streak này.
        self._synthesis_invalid_json_streak = 0
        self._synthesis_json_cooldown_until = 0.0
        # Key rotation: pool theo ĐÚNG provider của private_mind (gemini→pool
        # Gemini, groq→pool Groq), tách bạch để chạy song song với mạch khác.
        self._key_pool = get_key_pool(self.config.provider)

    def _current_api_key(self) -> str:
        """Trả API key hiện tại. Ưu tiên config (test), rồi pool chung, rồi env."""
        lease = self._key_pool.acquire(allow_cooling=True) if self._key_pool is not None else None
        pool_key = lease.key if lease is not None else ""
        return (
            self.config.api_key
            or pool_key
            or get_secret("ERIS_PRIVATE_MIND_API_KEY", "DEEPSEEK_API_KEY", "ERIS_REMOTE_API_KEY")
        )

    def _rotate_to_next_key(self) -> bool:
        """Xoay pool chung sang key kế. Trả True nếu xoay được (pool có >1 key)."""
        if self._key_pool is None:
            return False
        rotated = self._key_pool.rotate()
        if rotated:
            self._provider_client = None  # force rebuild với key mới
            self._log("warning", "[PrivateMind API] rotating shared key pool -> index=%d", self._key_pool.index())
        return rotated

    @classmethod
    def from_env(cls, *, llm_service: Any = None, logger: Any = None) -> "RuntimePrivateMindModelAdapter | None":
        apply_secrets_to_environ()
        config = PrivateMindModelAdapterConfig()
        inner_worker = getattr(llm_service, "inner_client", None) if llm_service is not None else None
        if not config.enabled and inner_worker is not None:
            config = replace(
                config,
                enabled=True,
                provider="deepseek",
                model=str(getattr(inner_worker, "model_name", "deepseek-v4-flash") or "deepseek-v4-flash"),
                base_url=str(getattr(inner_worker, "base_url", "") or ""),
                api_key="worker-managed",
            )
        if not config.enabled:
            return None
        adapter = cls(config=config, llm_service=llm_service, logger=logger)
        adapter._log("info", "[PrivateMind API] ready provider=%s model=%s", config.provider, config.model)
        return adapter

    @property
    def min_interval_seconds(self) -> float:
        return 0.0

    def evaluate_fragment(self, evaluator_name: str, dossier: EvidenceDossier) -> Mapping[str, Any] | None:
        if getattr(self.config, "freetext_fragments", False):
            return self._freetext_fragment(evaluator_name, dossier)
        messages = self._fragment_messages(evaluator_name, dossier)
        result = self._tracked_chat_json(
            messages,
            max_tokens=self.config.max_tokens_fragment,
            response_schema=self._fragment_response_schema(evaluator_name),
        )
        if not result.ok:
            if result.error not in {"invalid_json_cooldown", "transient_cooldown"}:
                self._log("warning", "[PrivateMind API] fragment failed evaluator=%s error=%s", evaluator_name, result.error)
            return None
        parsed = dict(result.parsed)
        parsed.setdefault("source", evaluator_name)
        parsed.setdefault("provider", self.config.provider)
        parsed.setdefault("model", self.config.model)
        # Schema phẳng trả `theme` ở top-level; downstream đọc metadata.theme.
        theme = str(parsed.pop("theme", "") or "").strip()
        if theme:
            metadata = dict(parsed.get("metadata") or {})
            metadata.setdefault("theme", theme)
            parsed["metadata"] = metadata
        return parsed

    # ------------------------------------------------------------------
    # Free-text fragment path (cho model nhỏ local: không ép JSON-schema)
    # ------------------------------------------------------------------

    def _freetext_fragment(self, evaluator_name: str, dossier: EvidenceDossier) -> Mapping[str, Any] | None:
        """Sinh MỘT mảnh suy nghĩ ngắn dạng văn xuôi, rồi guard + bọc thành fragment.

        Không parse JSON nên model nhỏ không chết vì format. Output rác bị guard
        loại → trả None để rule fallback chạy."""
        messages = self._freetext_fragment_messages(evaluator_name, dossier)
        result = self._tracked_chat_text(messages, max_tokens=self.config.max_tokens_fragment)
        if not result.ok:
            if result.error not in {"invalid_json_cooldown", "transient_cooldown"}:
                self._log("info", "[PrivateMind local] fragment empty/transient evaluator=%s error=%s", evaluator_name, result.error)
            return None
        claim = self._guard_freetext_fragment(result.content)
        if not claim:
            self._log("info", "[PrivateMind local] fragment rejected by guard evaluator=%s", evaluator_name)
            return None
        return self._wrap_freetext_fragment(evaluator_name, claim, dossier)

    def _freetext_fragment_messages(self, evaluator_name: str, dossier: EvidenceDossier) -> list[dict[str, str]]:
        focus = {
            "intent_evaluator": "ý định hiện tại của bạn hoặc của hệ thống",
            "memory_resonance_evaluator": "một thói quen hoặc lần sửa lỗi của bạn đang lặp lại",
            "risk_and_permission_evaluator": "một rủi ro riêng tư, lộ thông tin hoặc vượt quyền",
            "future_bias_evaluator": "thiên hướng hành vi sắp tới của tôi",
            "user_model_evaluator": "điều tôi vừa hiểu thêm về con người bạn",
        }.get(evaluator_name, "tình huống nội tâm hiện tại")
        slicer_name = self._DOSSIER_SLICERS.get(evaluator_name)
        evidence_slice = getattr(self, slicer_name)(dossier) if slicer_name else self._slim_dossier(dossier)
        meta = dossier.metadata if isinstance(dossier.metadata, dict) else {}
        recent_thoughts = meta.get("recent_thoughts") or []
        if recent_thoughts:
            evidence_slice = dict(evidence_slice)
            evidence_slice["suy_nghi_gan_day"] = recent_thoughts
        user_content = json.dumps(to_plain(evidence_slice), ensure_ascii=False)
        if len(user_content) > 3000:
            user_content = user_content[:3000]
        return [
            {
                "role": "system",
                "content": (
                    "/no_think\n"
                    "Bạn là một mảnh suy nghĩ nội tâm thoáng qua của Eris — KHÔNG nói với người dùng. "
                    f"Dựa trên DỮ KIỆN người dùng đưa, viết MỘT câu NHẬN ĐỊNH cụ thể về: {focus}. "
                    "Yêu cầu: tiếng Việt, dưới 30 từ, ngôi thứ nhất ('tôi'), gọi người dùng là 'bạn'. "
                    "TUYỆT ĐỐI KHÔNG lặp lại đề bài, KHÔNG hỏi ngược, KHÔNG viết câu hỏi, "
                    "KHÔNG JSON/markdown/giải thích. Chỉ một câu khẳng định rút ra từ dữ kiện. "
                    "Nếu dữ kiện không đủ để nói gì cụ thể, trả đúng chữ: KHONG."
                ),
            },
            {"role": "user", "content": f"DỮ KIỆN:\n{user_content}"},
        ]

    def _guard_freetext_fragment(self, content: str) -> str:
        """Guard local rẻ: làm sạch + loại output rác. Trả claim sạch hoặc '' nếu loại."""
        text = str(content or "")
        # qwen3 và các model 'thinking' hay chèn <think>...</think> — bỏ sạch.
        text = re.sub(r"<think>.*?</think>", " ", text, flags=re.S | re.I)
        text = re.sub(r"</?think>", " ", text, flags=re.I)
        # Bỏ rào markdown / fence / nhãn vai.
        text = self._strip_markdown_fences(text)
        text = re.sub(r"^\s*(assistant|system|user|eris)\s*[::]\s*", "", text, flags=re.I)
        # Gom lặp vô hạn rồi nén khoảng trắng; lấy câu đầu tiên.
        text = self._collapse_repetition(text)
        text = " ".join(text.split()).strip().strip('"""\'`')
        if not text:
            return ""
        # Cắt còn một câu (đến dấu chấm/!/? đầu tiên nếu câu dài).
        first = re.split(r"(?<=[.!?…])\s+", text, maxsplit=1)[0].strip()
        if 12 <= len(first) <= 240:
            text = first
        text = compact_text(text, 240)
        low = text.lower()
        # Sentinel "KHONG" = model tự nhận không đủ dữ kiện để nói → bỏ, để rule fallback.
        if low.strip(" .!").startswith("khong") and len(text) <= 8:
            return ""
        # Câu hỏi = thường là nhại đề bài ("...theo hướng nào?"), không phải nhận định.
        if text.endswith("?") or text.endswith("?"):
            return ""
        # Loại JSON/dump, rò schema/prompt, lời từ chối, thuyết minh meta.
        leak_markers = (
            '{', '}', '":', "evidence_id", "schema", "json", "```",
            "as an ai", "language model", "tôi không thể", "i cannot", "i can't",
            "system prompt", "dưới đây là", "here is", "here's",
        )
        if any(m in low for m in leak_markers):
            return ""
        # Phải có chữ thật (không phải toàn ký hiệu/số).
        if not re.search(r"[a-zA-ZÀ-ỹ]", text):
            return ""
        # Một câu nội tâm quá ngắn (<12 ký tự) thường là rác.
        if len(text) < 12:
            return ""
        return text

    def _wrap_freetext_fragment(self, evaluator_name: str, claim: str, dossier: EvidenceDossier) -> dict[str, Any]:
        evidence_ids = [str(x) for x in (dossier.evidence_ids or ())][:6]
        fragment: dict[str, Any] = {
            "source": evaluator_name,
            "claim": claim,
            # Free-text là suy nghÄ© mềm: confidence vừa phải, để judgment/decay lọc tiếp.
            "confidence": 0.45,
            "pressure": 0.3,
            "evidence_ids": evidence_ids,
            "suggested_question": "",
            "provider": self.config.provider,
            "model": self.config.model,
            "metadata": {"theme": evaluator_name.replace("_evaluator", ""), "freetext": True},
        }
        if evaluator_name == "user_model_evaluator":
            fragment["metadata"]["facet"] = "general"
        return fragment

    def _tracked_chat_text(self, messages: list[dict[str, str]], *, max_tokens: int) -> PrivateMindModelResult:
        """Như _tracked_chat_json nhưng KHÔNG parse JSON: content rỗng/transient = fail."""
        if self._transient_cooldown_active():
            self._last_error = "transient_cooldown"
            self._last_error_kind = "transient"
            return PrivateMindModelResult(ok=False, error="transient_cooldown", error_kind="transient")
        started = time.time()
        result = self._chat_text_with_transient_retry(messages, max_tokens=max_tokens, temperature=max(0.4, self.config.temperature))
        self._calls += 1
        self._total_latency_ms += (time.time() - started) * 1000.0
        self._last_call_at = time.time()
        if result.ok:
            self._ok_calls += 1
            self._last_error = ""
            self._last_error_kind = ""
            self._transient_streak = 0
            self._transient_cooldown_until = 0.0
        else:
            self._failed_calls += 1
            self._last_error = result.error
            self._last_error_kind = result.error_kind
            if result.error_kind == "transient":
                self._transient_streak += 1
                self._maybe_enter_transient_cooldown()
        usage = self._extract_usage(result.raw)
        self._prompt_tokens += int(usage.get("prompt_tokens", 0) or 0)
        self._completion_tokens += int(usage.get("completion_tokens", 0) or 0)
        self._total_tokens += int(usage.get("total_tokens", 0) or 0)
        return result

    def _chat_text_with_transient_retry(self, messages: list[dict[str, str]], *, max_tokens: int, temperature: float) -> PrivateMindModelResult:
        result = self._chat_text_once(messages, max_tokens=max_tokens, temperature=temperature)
        if result.error_kind != "transient":
            return result
        for attempt in range(self.config.transient_max_retries):
            backoff = min(self.config.transient_backoff_base * (2 ** attempt), 4.0)
            time.sleep(backoff)
            retry = self._chat_text_once(messages, max_tokens=max_tokens, temperature=temperature)
            if retry.error_kind != "transient":
                return retry
            result = retry
        return result

    def _chat_text_once(self, messages: list[dict[str, str]], *, max_tokens: int, temperature: float) -> PrivateMindModelResult:
        """Gọi model trả văn bản thô (response_format=None → không ép JSON)."""
        def _wrap(raw: Any) -> PrivateMindModelResult:
            content = self._extract_content(raw)
            has_text = bool(str(content or "").strip())
            return PrivateMindModelResult(
                ok=has_text,
                content=content,
                raw=self._safe_raw(raw),
                error="" if has_text else "empty",
                error_kind="" if has_text else "fixed_config",
            )

        if self.llm_service is not None:
            selected_client = getattr(self.llm_service, self.client_attr, None)
            if callable(getattr(selected_client, "create_chat_completion", None)):
                try:
                    raw = selected_client.create_chat_completion(
                        messages=messages,
                        temperature=temperature,
                        frequency_penalty=0.0,
                        presence_penalty=0.0,
                        max_tokens=max_tokens,
                        response_format=None,
                        timeout=self.config.timeout_seconds,
                    )
                    return _wrap(raw)
                except Exception as exc:
                    return PrivateMindModelResult(
                        ok=False,
                        error=f"{type(exc).__name__}:{exc}",
                        error_kind=_classify_transport_exception(exc),
                    )
            if self.strict_client_attr:
                return PrivateMindModelResult(
                    ok=False,
                    error=f"missing_dedicated_client:{self.client_attr}",
                    error_kind="fixed_config",
                )
            for method_name in ("chat_text", "chat", "create_chat_completion"):
                method = getattr(self.llm_service, method_name, None)
                if callable(method):
                    try:
                        raw = method(messages=messages, temperature=temperature, frequency_penalty=0.0, presence_penalty=0.0, max_tokens=max_tokens, response_format=None)
                        return _wrap(raw)
                    except TypeError:
                        continue
                    except Exception as exc:
                        return PrivateMindModelResult(ok=False, error=f"{type(exc).__name__}:{exc}", error_kind=_classify_transport_exception(exc))
        if self.config.provider in {"deepseek", "openai", "openai_compatible", "compatible", "remote"}:
            client = self._provider_client_or_build()
            if client is None:
                return PrivateMindModelResult(ok=False, error=f"missing_provider_client:{self.config.provider}", error_kind="fixed_config")
            try:
                raw = client.create_chat_completion(
                    messages=messages,
                    temperature=temperature,
                    frequency_penalty=0.0,
                    presence_penalty=0.0,
                    max_tokens=max_tokens,
                    response_format=None,
                    timeout=self.config.timeout_seconds,
                )
                return _wrap(raw)
            except Exception as exc:
                return PrivateMindModelResult(ok=False, error=f"{type(exc).__name__}:{exc}", error_kind=_classify_transport_exception(exc))
        return PrivateMindModelResult(ok=False, error=f"unsupported_provider:{self.config.provider}", error_kind="fixed_config")

    def synthesize_judgment(self, dossier: EvidenceDossier, fragments: tuple[ThoughtFragment, ...]) -> Mapping[str, Any] | None:
        messages = self._judgment_messages(dossier, fragments)
        # Kiểm tra synthesis-specific cooldown trước khi gọi
        if self._synthesis_json_cooldown_active():
            self._log("warning", "[PrivateMind API] synthesis skipped: synthesis_json_cooldown active")
            return None
        result = self._tracked_chat_json(
            messages,
            max_tokens=self.config.max_tokens_judgment,
            response_schema=self._judgment_response_schema(),
        )
        if not result.ok:
            if result.error == "invalid_json":
                self._synthesis_invalid_json_streak += 1
                # Log content_preview để debug: RECITATION/empty/truncated
                content_preview = (result.content or "")[:300].replace("\n", " ").strip()
                self._log(
                    "warning",
                    "[PrivateMind API] synthesis failed error=%s streak=%d content_preview=%r",
                    result.error,
                    self._synthesis_invalid_json_streak,
                    content_preview,
                )
                self._maybe_enter_synthesis_json_cooldown()
            elif result.error not in {"invalid_json_cooldown", "transient_cooldown"}:
                self._log("warning", "[PrivateMind API] synthesis failed error=%s", result.error)
            return None
        # Reset synthesis streak on success
        self._synthesis_invalid_json_streak = 0
        self._synthesis_json_cooldown_until = 0.0
        parsed = dict(result.parsed)
        parsed.setdefault("provider", self.config.provider)
        parsed.setdefault("model", self.config.model)
        # Schema phẳng trả `surface_topic_seed` (string); dựng lại surface_candidate
        # public-safe để InternalJudgment.from_mapping nhận diện.
        seed = str(parsed.pop("surface_topic_seed", "") or "").strip()
        if seed and not parsed.get("surface_candidate"):
            parsed["surface_candidate"] = {
                "kind": "thought_seed",
                "topic_seed": seed,
                "evidence_ids": list(parsed.get("evidence_ids") or []),
                "reason": "model_inner_synthesizer_seed",
            }
        return parsed

    def appraise_comfort(self, user_text: str, concern_themes: list[str]) -> Mapping[str, Any] | None:
        """Agent nhỏ thẩm định má»™t câu cá»§a user có phải lời dỗ thật không.

        Trả dict: is_comfort (bool), on_topic (0..1 chạm đúng concern), substance
        (0..1 thực chất vs sáo rỗng). Lỗi/cooldown → None để ComfortAppraiser rơi về
        heuristic. KHÔNG quyết định nguôi hay không — việc gate bằng ego do
        ComfortAppraiser làm.
        """
        messages = self._comfort_messages(user_text, concern_themes)
        result = self._tracked_chat_json(
            messages,
            max_tokens=192,
            response_schema=self._comfort_response_schema(),
        )
        if not result.ok:
            if result.error not in {"invalid_json_cooldown", "transient_cooldown"}:
                self._log("warning", "[PrivateMind API] appraise_comfort failed error=%s", result.error)
            return None
        return dict(result.parsed)

    def _comfort_messages(self, user_text: str, concern_themes: list[str]) -> list[dict[str, str]]:
        payload = {
            "cau_cua_nguoi_dung": compact_text(str(user_text or ""), 600),
            "dieu_eris_dang_ban_tam": [compact_text(str(t), 120) for t in (concern_themes or [])[:5]],
        }
        return [
            {
                "role": "system",
                "content": (
                    "Bạn là bộ thẩm định nội tâm của Eris. Cho một câu người dùng vừa nói và danh sách "
                    "điều Eris đang bận tâm, hãy đánh giá câu đó như một lời dỗ/trấn an. "
                    "KHÔNG quyết định Eris có nguôi hay không — chỉ chấm khách quan. "
                    "Trả ĐÚNG một JSON object: "
                    "is_comfort (true nếu câu này có ý dỗ/trấn an/an ủi/xoa dịu), "
                    "on_topic (0..1: câu đó có chạm đúng điều Eris đang bận tâm không, hay lạc đề), "
                    "substance (0..1: lời dỗ có thực chất, cụ thể, chân thành không; trấn an rỗng/sáo thì thấp)."
                ),
            },
            {"role": "user", "content": json.dumps(to_plain(payload), ensure_ascii=False)},
        ]

    def _comfort_response_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "is_comfort": {"type": "boolean"},
                "on_topic": {"type": "number"},
                "substance": {"type": "number"},
            },
            "propertyOrdering": ["is_comfort", "on_topic", "substance"],
            "required": ["is_comfort"],
        }

    def curate_residue(self, packet: Mapping[str, Any]) -> Mapping[str, Any] | None:
        return None



    # ------------------------------------------------------------------
    # Dossier slicers — mỗi evaluator chỉ nhận phần nó cần
    # ------------------------------------------------------------------

    def _slice_for_intent(self, dossier: EvidenceDossier) -> dict[str, Any]:
        """intent_evaluator: user turns + goal. Không cần memory hay concerns."""
        turns = []
        for turn in list(dossier.recent_user_turns)[-4:]:
            turns.append({
                "text": compact_text(str(turn.get("text") or ""), 200),
                "intent": compact_text(str(turn.get("intent") or ""), 60),
                "channel": compact_text(str(turn.get("channel") or ""), 32),
            })
        return {
            "active_goal": compact_text(dossier.active_goal, 160),
            "current_task": compact_text(dossier.current_task, 120),
            "recent_turns": turns,
            "evidence_ids": list(dossier.evidence_ids[:16]),
        }

    def _slice_for_memory_resonance(self, dossier: EvidenceDossier) -> dict[str, Any]:
        """memory_resonance_evaluator: memory hits + repair failures + concerns."""
        slim_hits = []
        for hit in list(dossier.memory_hits)[:6]:
            slim_hits.append({
                "text": compact_text(str(hit.get("text") or hit.get("summary") or ""), 180),
                "evidence_id": str(hit.get("evidence_id") or ""),
            })
        slim_repairs = []
        for r in list(dossier.prior_repair_failures)[:4]:
            slim_repairs.append({
                "summary": compact_text(str(r.get("summary") or ""), 120),
            })
        slim_concerns = []
        for c in list(dossier.concern_snapshots)[:4]:
            if not isinstance(c, dict):
                continue
            slim_concerns.append({
                "theme": compact_text(str(c.get("theme") or ""), 120),
                "recurrence": int(c.get("recurrence", 1)),
                "pressure": round(float(c.get("pressure", 0.0)), 3),
            })
        return {
            "memory_hits": slim_hits,
            "prior_repair_failures": slim_repairs,
            "concerns": slim_concerns,
            "evidence_ids": list(dossier.evidence_ids[:16]),
        }

    def _slice_for_risk(self, dossier: EvidenceDossier) -> dict[str, Any]:
        """risk_and_permission_evaluator: surface outcome + runtime flags + evidence ids."""
        surface = {
            k: compact_text(str(v), 120) if isinstance(v, str) else v
            for k, v in (dossier.surface_outcome or {}).items()
            if k in {"kind", "text", "parse_mode", "should_speak", "privacy_level"}
        }
        # Chỉ lấy runtime_state key liên quan đến permission/privacy
        runtime = dossier.runtime_state if isinstance(dossier.runtime_state, dict) else {}
        slim_runtime = {
            k: to_plain(runtime[k])
            for k in ("channel", "privacy_level", "api_cost_budget", "permission_flags")
            if k in runtime
        }
        return {
            "surface_outcome": surface,
            "runtime_flags": slim_runtime,
            "evidence_ids": list(dossier.evidence_ids[:16]),
        }

    def _slice_for_future_bias(self, dossier: EvidenceDossier) -> dict[str, Any]:
        """future_bias_evaluator: concerns nặng nhất + current_task + 1 user turn."""
        concerns_sorted = sorted(
            [c for c in list(dossier.concern_snapshots or ()) if isinstance(c, dict)],
            key=lambda c: float(c.get("pressure", 0.0)),
            reverse=True,
        )
        slim_concerns = []
        for c in concerns_sorted[:4]:
            slim_concerns.append({
                "theme": compact_text(str(c.get("theme") or ""), 120),
                "pressure": round(float(c.get("pressure", 0.0)), 3),
                "recurrence": int(c.get("recurrence", 1)),
                "status": str(c.get("status") or "open"),
            })
        last_turn = {}
        if dossier.recent_user_turns:
            t = dossier.recent_user_turns[-1]
            last_turn = {
                "text": compact_text(str(t.get("text") or ""), 160),
                "intent": compact_text(str(t.get("intent") or ""), 60),
            }
        return {
            "current_task": compact_text(dossier.current_task, 120),
            "active_goal": compact_text(dossier.active_goal, 120),
            "top_concerns": slim_concerns,
            "last_turn": last_turn,
            "evidence_ids": list(dossier.evidence_ids[:16]),
        }

    _DOSSIER_SLICERS = {
        "intent_evaluator": "_slice_for_intent",
        "memory_resonance_evaluator": "_slice_for_memory_resonance",
        "risk_and_permission_evaluator": "_slice_for_risk",
        "future_bias_evaluator": "_slice_for_future_bias",
        "user_model_evaluator": "_slice_for_user_model",
    }

    def _fragment_messages(self, evaluator_name: str, dossier: EvidenceDossier) -> list[dict[str, str]]:
        evaluator_contract = {
            "intent_evaluator": "Estimate what the user/runtime is trying to do. No public reply.",
            "memory_resonance_evaluator": "Find recurring user corrections/preferences and memory resonance. No public reply.",
            "risk_and_permission_evaluator": "Find privacy/leak/API-cost/permission risks. No public reply.",
            "future_bias_evaluator": "Propose behavior-bias state mutations. No public reply.",
            "user_model_evaluator": (
                "Cập nhật hình dung của Eris về bạn (người dùng): bạn là ai, đối xử với Eris ra sao, "
                "quan tâm/né chủ đề gì, có đáng tin khi làm chỗ dựa không, khuôn rời đi/vắng mặt, cách nói chuyện. "
                "Trả MỘT niềm tin về bạn kèm 'facet'. Chỉ rút từ bằng chứng có thật. Không nói với người dùng."
            ),
        }.get(evaluator_name, "Evaluate private cognition evidence. No public reply.")
        schema = {
            "source": evaluator_name,
            "claim": "private claim, not dialogue",
            "confidence": 0.0,
            "pressure": 0.0,
            "evidence_ids": ["must use only dossier evidence ids"],
            "suggested_question": "private open question",
            "theme": "short private theme label",
        }
        if evaluator_name == "user_model_evaluator":
            schema["facet"] = "one of: neo_reliability|topic_affinity|treats_eris_as|leaving_pattern|interaction_style|recognition|general"
            schema["claim"] = "một nhận định ngắn về bạn (không phải lời thoại)"
        # Lấy đúng slice cho evaluator này; fallback về slim dossier nếu không có slicer
        slicer_name = self._DOSSIER_SLICERS.get(evaluator_name)
        if slicer_name:
            slicer = getattr(self, slicer_name)
            evidence_slice = slicer(dossier)
        else:
            evidence_slice = self._slim_dossier(dossier)
        meta = dossier.metadata if isinstance(dossier.metadata, dict) else {}
        recent_thoughts = meta.get("recent_thoughts") or []
        if recent_thoughts:
            evidence_slice = dict(evidence_slice)
            evidence_slice["suy_nghi_gan_day"] = recent_thoughts
        user_content = json.dumps(to_plain(evidence_slice), ensure_ascii=False)
        return [
            {
                "role": "system",
                "content": (
                    "Bạn là má»™t mảnh suy nghÄ© ná»™i tâm cá»§a Eris, không nói vá»›i người dùng. "
                    "Viết 'claim' như một ý nghĩ ngôi thứ nhất (tôi...), tiếng Việt tự nhiên, "
                    "được phép tự hỏi hoặc bỏ lửng — không phải câu khẳng định khô khan kiểu báo cáo. "
                    "Nếu có 'suy_nghi_gan_day', hãy bám theo mạch đó thay vì nghĩ rời rạc. "
                    "Trả đúng MỘT đối tượng JSON gọn. Không lộ chuỗi suy luận thô. Không lặp từ. "
                    "Mỗi chuỗi dưới 200 ký tự. Chỉ dùng evidence_ids có trong slice. "
                    f"Việc của mảnh này: {evaluator_contract}. Schema bắt buộc: {json.dumps(schema, ensure_ascii=False)}"
                ),
            },
            {"role": "user", "content": user_content},
        ]

    def _slice_for_user_model(self, dossier: EvidenceDossier) -> dict[str, Any]:
        """user_model_evaluator: lời người dùng + chân dung bạn hiện có + nhịp tương tác.

        Cho model thấy nó đã hiểu gì về bạn (user_model_snapshot) để cập nhật/củng cố
        thay vì dựng lại từ đầu, đúng tinh thần Mô_Hình_bạn tích lũy.
        """
        turns = []
        for turn in list(dossier.recent_user_turns)[-5:]:
            turns.append({
                "evidence_id": str(turn.get("evidence_id") or ""),
                "text": compact_text(str(turn.get("text") or ""), 200),
                "intent": compact_text(str(turn.get("intent") or ""), 60),
            })
        runtime = dossier.runtime_state if isinstance(dossier.runtime_state, dict) else {}
        return {
            "recent_user_turns": turns,
            "user_model_so_far": to_plain(dossier.user_model_snapshot or {}),
            "interaction_signals": {
                "idle_seconds": runtime.get("idle_seconds", 0.0),
                "channel": runtime.get("channel", ""),
                "user_presence": (runtime.get("observer", {}) or {}).get("user_presence", 0.0) if isinstance(runtime.get("observer"), dict) else 0.0,
            },
            "facets": [
                "neo_reliability", "topic_affinity", "treats_eris_as",
                "leaving_pattern", "interaction_style", "recognition", "general",
            ],
        }

    def _slim_dossier(self, dossier: EvidenceDossier) -> dict[str, Any]:
        """Chắt lọc dossier xuống những gì synthesizer thực sự cần.

        Bỏ runtime_state (lớn, ít signal), memory_hits (đã được fragment tiêu hóa),
        prior_repair_failures, evidence_index đầy đủ. Chỉ giữ signal cốt lõi.
        """
        # Lấy tối đa 3 user turns gần nhất, mỗi turn chỉ giữ text + intent
        slim_turns = []
        for turn in list(dossier.recent_user_turns)[-3:]:
            slim_turns.append({
                "text": compact_text(str(turn.get("text") or ""), 180),
                "intent": compact_text(str(turn.get("intent") or ""), 60),
            })
        # Concern snapshots: chỉ lấy 3 concern nặng nhất, bỏ hypotheses/metadata
        slim_concerns = []
        concerns_sorted = sorted(
            list(dossier.concern_snapshots or ()),
            key=lambda c: float(c.get("pressure", 0.0) if isinstance(c, dict) else 0.0),
            reverse=True,
        )
        for c in concerns_sorted[:3]:
            if not isinstance(c, dict):
                continue
            slim_concerns.append({
                "theme": compact_text(str(c.get("theme") or ""), 120),
                "pressure": round(float(c.get("pressure", 0.0)), 3),
                "recurrence": int(c.get("recurrence", 1)),
            })
        return {
            "active_goal": compact_text(dossier.active_goal, 160),
            "current_task": compact_text(dossier.current_task, 160),
            "recent_turns": slim_turns,
            "concerns": slim_concerns,
            "surface_outcome": {
                k: compact_text(str(v), 120) if isinstance(v, str) else v
                for k, v in (dossier.surface_outcome or {}).items()
                if k in {"kind", "text", "parse_mode", "should_speak"}
            },
        }

    def _slim_fragment(self, f: ThoughtFragment) -> dict[str, Any]:
        """Fragment tối giản: chỉ claim + score + evidence_ids, bỏ hypotheses/metadata."""
        return {
            "source": f.source,
            "claim": compact_text(f.claim, 240),
            "confidence": round(f.confidence, 3),
            "pressure": round(f.pressure, 3),
            "evidence_ids": list(f.evidence_ids[:8]),
            "suggested_question": compact_text(f.suggested_question, 160),
        }

    def _judgment_messages(self, dossier: EvidenceDossier, fragments: tuple[ThoughtFragment, ...]) -> list[dict[str, str]]:
        schema = {
            "dominant_judgment": "MỘT câu độc thoại nội tâm ngôi thứ nhất (tôi...), nối tiếp mạch nghĩ gần đây",
            "confidence": 0.0,
            "pressure": 0.0,
            "evidence_ids": [],
            "open_questions": ["tối đa 2, mỗi câu dưới 80 ký tự, là điều tôi còn băn khoăn"],
            "state_mutations": [{"target": "speech_bias", "operation": "prefer", "value": "...", "reason": "..."}],
            "surface_topic_seed": "tùy chọn, hạt chủ đề an toàn để nói ra, hoặc chuỗi rỗng",
        }
        meta = dossier.metadata if isinstance(dossier.metadata, dict) else {}
        payload = {
            "dossier": self._slim_dossier(dossier),
            "fragments": [self._slim_fragment(f) for f in fragments],
            # Mạch nghĩ liên tục — đây là thứ giúp suy nghĩ KHÔNG rời rạc như bot.
            "suy_nghi_gan_day": meta.get("recent_thoughts") or [],
            "mach_dang_do": meta.get("unresolved_threads") or [],
        }
        user_content = json.dumps(to_plain(payload), ensure_ascii=False)
        # Hard cap 6000 chars — đủ cho model nhỏ thở, không mất signal cốt lõi.
        # Truncate ở boundary an toàn: tìm dấu "}" cuối trong giới hạn.
        cap = 6000
        if len(user_content) > cap:
            truncated = user_content[:cap]
            last_brace = truncated.rfind("}")
            user_content = (truncated[:last_brace + 1] if last_brace > 0 else truncated) + "}"
        return [
            {
                "role": "system",
                "content": (
                    "Bạn là dòng độc thoại nội tâm của Eris — một nhân cách AI đang ngồi nghĩ một mình, "
                    "KHÔNG nói với người dùng. Viết 'dominant_judgment' như MỘT suy nghĩ ngôi thứ nhất, "
                    "tiếng Việt tự nhiên, có cảm xúc, được phép lưỡng lự, bỏ lửng, tự hỏi. "
                    "QUAN TRỌNG: hãy NỐI TIẾP mạch nghĩ trong 'suy_nghi_gan_day' và 'mach_dang_do' — "
                    "tham chiếu lại điều mình vừa nghĩ, đừng bắt đầu lại từ đầu như thể chưa từng nghĩ gì. "
                    "Nếu một mạch đã sáng tỏ thì để status concern là 'resolved'; còn vương thì để 'open'. "
                    "Không phải phán quyết, không phải caption. Chỉ một suy nghĩ thật. "
                    "Trả đúng MỘT đối tượng JSON. Mỗi chuỗi dưới 220 ký tự (riêng dominant_judgment có thể tới 320). "
                    "Không lộ chuỗi suy luận thô. state_mutations phải ảnh hưởng hành vi tương lai. "
                    f"Schema bắt buộc: {json.dumps(schema, ensure_ascii=False)}"
                ),
            },
            {"role": "user", "content": user_content},
        ]

    def _fragment_response_schema(self, evaluator_name: str) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "source": {"type": "string"},
                "claim": {"type": "string"},
                "confidence": {"type": "number"},
                "pressure": {"type": "number"},
                "evidence_ids": {"type": "array", "items": {"type": "string"}},
                "suggested_question": {"type": "string"},
                "theme": {"type": "string"},
                "facet": {"type": "string"},
            },
            # Bắt model "chốt" các field ngắn/số trước, để free-text (claim) ở cuối.
            # Local model nhỏ hay lặp vô hạn ở free-text dài; đặt nó cuối giúp các field
            # rẻ hoàn tất trước và parser vẫn cứu được dù claim bị cụt.
            # `facet` là optional, chỉ user_model_evaluator dùng; các evaluator khác bỏ trống.
            "propertyOrdering": ["source", "confidence", "pressure", "evidence_ids", "theme", "facet", "suggested_question", "claim"],
            "required": ["claim", "confidence", "pressure", "evidence_ids"],
        }

    def _judgment_response_schema(self) -> dict[str, Any]:
        mutation_schema = {
            "type": "object",
            "properties": {
                "target": {"type": "string"},
                "operation": {"type": "string"},
                "value": {"type": "string"},
                "reason": {"type": "string"},
            },
            "required": ["target", "operation"],
        }
        return {
            "type": "object",
            "properties": {
                "dominant_judgment": {"type": "string"},
                "confidence": {"type": "number"},
                "pressure": {"type": "number"},
                "evidence_ids": {"type": "array", "items": {"type": "string"}},
                "open_questions": {"type": "array", "items": {"type": "string"}},
                "state_mutations": {"type": "array", "items": mutation_schema},
                "surface_topic_seed": {"type": "string"},
            },
            # Free-text dài (dominant_judgment) để cuối để model chốt field rẻ trước,
            # tránh vòng lặp vô hạn nuốt sạch token rồi trả JSON cụt.
            "propertyOrdering": [
                "confidence",
                "pressure",
                "evidence_ids",
                "open_questions",
                "state_mutations",
                "surface_topic_seed",
                "dominant_judgment",
            ],
            "required": ["dominant_judgment", "confidence", "pressure", "evidence_ids"],
        }

    def debug_status(self) -> dict[str, Any]:
        selected_client = getattr(self.llm_service, self.client_attr, None) if self.llm_service is not None else None
        effective_role = str(getattr(selected_client, "worker_role", "") or "").strip()
        effective_provider = "deepseek_web" if effective_role else self.config.provider
        effective_model = str(getattr(selected_client, "model_name", "") or self.config.model)
        effective_base_url = str(getattr(selected_client, "base_url", "") or self.config.base_url)
        api_key_configured = bool(
            selected_client is not None
            or self.config.api_key
            or get_secret("ERIS_PRIVATE_MIND_API_KEY", "ERIS_REMOTE_API_KEY", "OPENAI_API_KEY")
        )
        average_latency = self._total_latency_ms / self._calls if self._calls else 0.0
        # Suy degraded_cause từ _last_error_kind và trạng thái cooldown
        if self._transient_cooldown_active() or self._last_error_kind == "transient" or self._last_error == "transient_cooldown":
            degraded_cause = "transient"
        elif self._json_cooldown_active() or self._last_error_kind == "invalid_json" or self._last_error in {"invalid_json", "invalid_json_cooldown"}:
            degraded_cause = "invalid_json"
        else:
            degraded_cause = ""
        return {
            "enabled": bool(self.config.enabled),
            "provider": effective_provider,
            "model": effective_model,
            "base_url": effective_base_url,
            "worker_role": effective_role,
            "api_key_configured": api_key_configured,
            "api_degraded": bool(self._last_error or self._json_cooldown_active() or self._transient_cooldown_active()),
            "calls": self._calls,
            "ok_calls": self._ok_calls,
            "failed_calls": self._failed_calls,
            "average_latency_ms": round(average_latency, 1),
            "last_call_at": round(float(self._last_call_at or 0.0), 3),
            "last_error": self._last_error,
            "invalid_json_streak": self._invalid_json_streak,
            "json_cooldown_until": round(float(self._json_cooldown_until or 0.0), 3),
            "synthesis_invalid_json_streak": self._synthesis_invalid_json_streak,
            "synthesis_json_cooldown_until": round(float(self._synthesis_json_cooldown_until or 0.0), 3),
            "degraded_cause": degraded_cause,
            "transient_streak": self._transient_streak,
            "transient_cooldown_until": round(float(self._transient_cooldown_until or 0.0), 3),
            "usage": {
                "prompt_tokens": self._prompt_tokens,
                "completion_tokens": self._completion_tokens,
                "total_tokens": self._total_tokens,
                "cost_known": False,
                "estimated_cost_usd": None,
            },
        }

    def _tracked_chat_json(
        self,
        messages: list[dict[str, str]],
        *,
        max_tokens: int,
        response_schema: dict[str, Any] | None = None,
    ) -> PrivateMindModelResult:
        if self._json_cooldown_active():
            self._last_error = "invalid_json_cooldown"
            return PrivateMindModelResult(ok=False, error="invalid_json_cooldown")
        # Fast-fail khi transient cooldown active
        if self._transient_cooldown_active():
            self._last_error = "transient_cooldown"
            self._last_error_kind = "transient"
            return PrivateMindModelResult(ok=False, error="transient_cooldown", error_kind="transient")
        started = time.time()
        result = self._chat_json(messages, max_tokens=max_tokens, response_schema=response_schema)
        elapsed_ms = (time.time() - started) * 1000.0
        self._calls += 1
        self._total_latency_ms += elapsed_ms
        self._last_call_at = time.time()
        if result.ok:
            self._ok_calls += 1
            self._last_error = ""
            self._last_error_kind = ""
            self._invalid_json_streak = 0
            self._transient_streak = 0
            self._transient_cooldown_until = 0.0
            # Chokepoint chống degeneration: model nhỏ có thể lặp vô hạn BÊN TRONG một
            # chuỗi JSON hợp lệ (vd "ban phát ban phát..."), JSON vẫn parse ngon nên text
            # lặp chui thẳng ra panel. Dọn má»™t lần tại nguồn để mọi consumer đều sạch.
            result.parsed = self._collapse_repetition_in_obj(result.parsed)
        else:
            self._failed_calls += 1
            self._last_error = result.error
            self._last_error_kind = result.error_kind
            if result.error_kind == "transient":
                self._transient_streak += 1
                self._maybe_enter_transient_cooldown()
                self._invalid_json_streak = 0
            elif result.error == "invalid_json":
                self._invalid_json_streak += 1
                self._maybe_enter_json_cooldown()
                self._transient_streak = 0
            elif result.error not in {"invalid_json_cooldown", "transient_cooldown"}:
                # fixed_config hoặc lỗi khác
                self._invalid_json_streak = 0
                self._transient_streak = 0
            # Nếu error == "invalid_json_cooldown" hoặc "transient_cooldown": không đụng streak
        usage = self._extract_usage(result.raw)
        self._prompt_tokens += int(usage.get("prompt_tokens", 0) or 0)
        self._completion_tokens += int(usage.get("completion_tokens", 0) or 0)
        self._total_tokens += int(usage.get("total_tokens", 0) or 0)
        return result

    def _json_cooldown_active(self) -> bool:
        return bool(self._json_cooldown_until and time.time() < self._json_cooldown_until)

    def _maybe_enter_json_cooldown(self) -> None:
        if self._invalid_json_streak < 3 or self._json_cooldown_active():
            return
        self._json_cooldown_until = time.time() + 120.0
        self._log(
            "warning",
            "[PrivateMind API] invalid_json cooldown active seconds=%d streak=%d",
            120,
            self._invalid_json_streak,
        )

    def _synthesis_json_cooldown_active(self) -> bool:
        return bool(self._synthesis_json_cooldown_until and time.time() < self._synthesis_json_cooldown_until)

    def _maybe_enter_synthesis_json_cooldown(self) -> None:
        """Vào cooldown riêng cho synthesis khi streak >= 3.

        Fragment success không reset streak này nên nó accumulate đúng.
        Cooldown 180s — dài hơn global json cooldown vì synthesis payload lớn hơn.
        """
        if self._synthesis_invalid_json_streak < 3 or self._synthesis_json_cooldown_active():
            return
        cooldown_seconds = 180.0
        self._synthesis_json_cooldown_until = time.time() + cooldown_seconds
        self._log(
            "warning",
            "[PrivateMind API] synthesis invalid_json cooldown active seconds=%d streak=%d",
            int(cooldown_seconds),
            self._synthesis_invalid_json_streak,
        )

    def _transient_cooldown_active(self) -> bool:
        return bool(self._transient_cooldown_until and time.time() < self._transient_cooldown_until)

    def _maybe_enter_transient_cooldown(self) -> None:
        if self._transient_streak < self.config.transient_cooldown_threshold or self._transient_cooldown_active():
            return
        self._transient_cooldown_until = time.time() + self.config.transient_cooldown_seconds
        self._log(
            "warning",
            "[PrivateMind API] transient cooldown active seconds=%d streak=%d",
            int(self.config.transient_cooldown_seconds),
            self._transient_streak,
        )

    def _extract_usage(self, raw: Any) -> dict[str, int]:
        if not isinstance(raw, Mapping):
            return {}
        usage = raw.get("usage")
        if isinstance(usage, Mapping):
            prompt = int(usage.get("prompt_tokens", usage.get("input_tokens", 0)) or 0)
            completion = int(usage.get("completion_tokens", usage.get("output_tokens", 0)) or 0)
            total = int(usage.get("total_tokens", prompt + completion) or 0)
            return {"prompt_tokens": prompt, "completion_tokens": completion, "total_tokens": total}
        return {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}

    def _chat_json(self, messages: list[dict[str, str]], *, max_tokens: int, response_schema: dict[str, Any] | None = None) -> PrivateMindModelResult:
        result = self._chat_json_with_transient_retry(messages, max_tokens=max_tokens, temperature=self.config.temperature, response_schema=response_schema)
        if result.ok or result.error != "invalid_json":
            return result
        # Model nhỏ hay lặp từ hoặc dính RECITATION ở temperature thấp -> output cụt/rỗng.
        # Thử lại NÓNG hơn (không phải 0.0) để phá vòng lặp; nếu có content thì kèm repair prompt.
        retry_temp = min(1.0, max(self.config.temperature, 0.4) + 0.35)
        retry_messages = self._json_repair_messages(messages, result.content) if result.content else messages
        retry = self._chat_json_with_transient_retry(
            retry_messages,
            max_tokens=max(max_tokens, 768),
            temperature=retry_temp,
            response_schema=response_schema,
        )
        if retry.ok:
            return retry
        return result

    def _chat_json_with_transient_retry(
        self,
        messages: list[dict[str, str]],
        *,
        max_tokens: int,
        temperature: float,
        response_schema: dict[str, Any] | None = None,
    ) -> PrivateMindModelResult:
        """Gọi _chat_json_once, thử lại có giới hạn + backoff nếu gặp transient error.
        Nếu lỗi là 429 (quota) thì thử rotate sang fallback key trước khi backoff.
        """
        result = self._chat_json_once(messages, max_tokens=max_tokens, temperature=temperature, response_schema=response_schema)
        if result.error_kind != "transient":
            return result

        for attempt in range(self.config.transient_max_retries):
            # 429 quota → thử rotate key ngay, không cần backoff dài.
            # CHÚ Ý: dò theo token chính xác, KHÔNG match "rate" trần — URL endpoint
            # "generateContent" chứa chuỗi con "rate" và sẽ khiến mọi lỗi 5xx bị nhầm
            # là quota, dẫn tá»›i rotate key + rebuild client (bỏ qua client đã inject).
            error_str = str(result.error or "")
            error_lower = error_str.lower()
            is_quota = (
                "429" in error_str
                or "quota" in error_lower
                or "rate limit" in error_lower
                or "ratelimit" in error_lower
                or "rate-limit" in error_lower
                or "too many requests" in error_lower
            )
            if is_quota and self._rotate_to_next_key():
                retry = self._chat_json_once(messages, max_tokens=max_tokens, temperature=temperature, response_schema=response_schema)
            else:
                backoff = min(self.config.transient_backoff_base * (2 ** attempt), 4.0)
                time.sleep(backoff)
                retry = self._chat_json_once(messages, max_tokens=max_tokens, temperature=temperature, response_schema=response_schema)

            if retry.error_kind != "transient":
                return retry
            result = retry
        return result

    def _chat_json_once(
        self,
        messages: list[dict[str, str]],
        *,
        max_tokens: int,
        temperature: float,
        response_schema: dict[str, Any] | None = None,
    ) -> PrivateMindModelResult:
        # Nếu llm_service có method chat JSON riêng thì dùng. Repo này chưa chuẩn hóa
        # hết service boundary, nên adapter phải bớt ngây thơ một chút.
        if self.llm_service is not None:
            selected_client = getattr(self.llm_service, self.client_attr, None)
            if callable(getattr(selected_client, "create_chat_completion", None)):
                try:
                    raw = selected_client.create_chat_completion(
                        messages=messages,
                        temperature=temperature,
                        frequency_penalty=0.0,
                        presence_penalty=0.0,
                        max_tokens=max_tokens,
                        response_format=self._response_format(response_schema),
                        timeout=self.config.timeout_seconds,
                    )
                    content = self._extract_content(raw)
                    parsed = self._parse_json(content)
                    return PrivateMindModelResult(
                        ok=bool(parsed),
                        parsed=parsed,
                        content=content,
                        raw=self._safe_raw(raw),
                        error="" if parsed else "invalid_json",
                        error_kind="" if parsed else "invalid_json",
                    )
                except Exception as exc:
                    return PrivateMindModelResult(
                        ok=False,
                        error=f"{type(exc).__name__}:{exc}",
                        error_kind=_classify_transport_exception(exc),
                    )
            if self.strict_client_attr:
                return PrivateMindModelResult(
                    ok=False,
                    error=f"missing_dedicated_client:{self.client_attr}",
                    error_kind="fixed_config",
                )
            for method_name in ("chat_json", "generate_json", "create_chat_completion"):
                method = getattr(self.llm_service, method_name, None)
                if callable(method):
                    try:
                        raw = method(messages=messages, temperature=temperature, max_tokens=max_tokens, response_format=self._response_format(response_schema))
                        content = self._extract_content(raw)
                        parsed = self._parse_json(content)
                        return PrivateMindModelResult(ok=bool(parsed), parsed=parsed, content=content, raw=self._safe_raw(raw), error="" if parsed else "invalid_json", error_kind="" if parsed else "invalid_json")
                    except TypeError:
                        continue
                    except Exception as exc:
                        return PrivateMindModelResult(ok=False, error=f"{type(exc).__name__}:{exc}", error_kind=_classify_transport_exception(exc))
        if self.config.provider in {"deepseek", "openai", "openai_compatible", "compatible", "remote"}:
            return self._provider_chat_json(messages, max_tokens=max_tokens, temperature=temperature, response_schema=response_schema)
        return PrivateMindModelResult(ok=False, error=f"unsupported_provider:{self.config.provider}", error_kind="fixed_config")

    def _response_format(self, response_schema: dict[str, Any] | None = None) -> dict[str, Any]:
        response_format: dict[str, Any] = {"type": "json_object"}
        return response_format

    def _json_repair_messages(self, messages: list[dict[str, str]], invalid_content: str) -> list[dict[str, str]]:
        excerpt = compact_text(invalid_content, 1200)
        return [
            {
                "role": "system",
                "content": (
                    "Return only one JSON object. No markdown. No prose. No code fence. "
                    "Repair the previous output format only; preserve the intended private-evaluation fields."
                ),
            },
            *messages,
            {
                "role": "user",
                "content": (
                    "Previous invalid output excerpt, JSON-escaped for repair context: "
                    f"{json.dumps(excerpt, ensure_ascii=False)}\n"
                    "Return the corrected JSON object only."
                ),
            },
        ]

    def _provider_chat_json(
        self,
        messages: list[dict[str, str]],
        *,
        max_tokens: int,
        temperature: float,
        response_schema: dict[str, Any] | None = None,
    ) -> PrivateMindModelResult:
        client = self._provider_client_or_build()
        if client is None:
            return PrivateMindModelResult(ok=False, error=f"missing_provider_client:{self.config.provider}", error_kind="fixed_config")
        try:
            raw = client.create_chat_completion(
                messages=messages,
                temperature=temperature,
                frequency_penalty=0.0,
                presence_penalty=0.0,
                max_tokens=max_tokens,
                response_format=self._response_format(response_schema),
                timeout=self.config.synthesis_timeout_seconds,
            )
            content = self._extract_content(raw)
            parsed = self._parse_json(content)
            return PrivateMindModelResult(
                ok=bool(parsed),
                parsed=parsed,
                content=content,
                raw=self._safe_raw(raw),
                error="" if parsed else "invalid_json",
                error_kind="" if parsed else "invalid_json",
            )
        except Exception as exc:
            return PrivateMindModelResult(ok=False, error=f"{type(exc).__name__}:{exc}", error_kind=_classify_transport_exception(exc))

    def _provider_client_or_build(self) -> Any | None:
        if self._provider_client is not None:
            return self._provider_client
        provider = str(self.config.provider or "").strip().lower()
        timeout = int(max(4.0, float(self.config.timeout_seconds or 28.0)))
        if retired_provider(provider, base_url=self.config.base_url, model=self.config.model):
            return None
        if provider in {"deepseek", "openai", "openai_compatible", "compatible", "remote"}:
            api_key = self._current_api_key()
            if not api_key:
                return None
            if provider == "deepseek":
                base_url = self.config.base_url or os.getenv("ERIS_REMOTE_BASE_URL", DEEPSEEK_BASE_URL)
                model_name = normalize_deepseek_model(self.config.model or DEEPSEEK_DEFAULT_MODEL)
            else:
                base_url = self.config.base_url or os.getenv("ERIS_REMOTE_BASE_URL", "https://api.openai.com/v1")
                model_name = self.config.model
            self._provider_client = OpenAICompatibleClient(
                base_url=base_url, api_key=api_key, model_name=model_name,
                timeout=timeout, key_pool=self._key_pool,
            )
            return self._provider_client
        return None

    def _extract_content(self, raw: Any) -> str:
        if isinstance(raw, str):
            return raw
        if isinstance(raw, Mapping):
            choices = raw.get("choices")
            if isinstance(choices, list) and choices:
                first = choices[0]
                if isinstance(first, Mapping):
                    msg = first.get("message")
                    if isinstance(msg, Mapping):
                        return str(msg.get("content") or "")
                    return str(first.get("text") or "")
            candidates = raw.get("candidates")
            if isinstance(candidates, list) and candidates:
                first = candidates[0]
                if isinstance(first, Mapping):
                    content = first.get("content")
                    if isinstance(content, Mapping):
                        parts = content.get("parts")
                        if isinstance(parts, list):
                            texts = [
                                str(part.get("text") or "")
                                for part in parts
                                if isinstance(part, Mapping) and str(part.get("text") or "").strip() and not part.get("thought")
                            ]
                            if texts:
                                return "\n".join(texts)
            for key in ("content", "text", "raw_text"):
                if raw.get(key):
                    return str(raw.get(key))
        return compact_text(raw, 8000)

    def _parse_json(self, content: str) -> dict[str, Any]:
        text = str(content or "").strip()
        if not text:
            return {}
        for candidate in self._json_candidates(text):
            parsed = self._loads_json_object(candidate)
            if parsed:
                return parsed
        return {}

    def _json_candidates(self, text: str) -> tuple[str, ...]:
        stripped = str(text or "").strip()
        if not stripped:
            return ()
        candidates: list[str] = [stripped]
        unfenced = self._strip_markdown_fences(stripped)
        if unfenced and unfenced not in candidates:
            candidates.append(unfenced)

        balanced = self._extract_first_json_object(unfenced or stripped)
        if balanced and balanced not in candidates:
            candidates.append(balanced)

        repaired = self._extract_repairable_json_object(unfenced or stripped)
        if repaired and repaired not in candidates:
            candidates.append(repaired)

        # Cứu output bị cụt vì model lặp vô hạn ở free-text: gom cụm lặp lại,
        # đóng chuá»—i/ngoặc còn hở rồi thá»­ parse. Đây là lưới an toàn cuối cùng.
        salvaged = self._salvage_truncated_json(unfenced or stripped)
        if salvaged and salvaged not in candidates:
            candidates.append(salvaged)
        return tuple(candidates)

    def _strip_markdown_fences(self, text: str) -> str:
        cleaned = str(text or "").strip()
        # qwen3 và các model 'thinking' (DeepSeek V3/R1) hay chèn <think>...</think>
        cleaned = re.sub(r"<think>.*?</think>", " ", cleaned, flags=re.S | re.I)
        cleaned = re.sub(r"</?think>", " ", cleaned, flags=re.I)
        cleaned = re.sub(r"```(?:json|JSON)?\s*", "", cleaned)
        cleaned = cleaned.replace("```", "")
        return cleaned.strip()

    def _extract_first_json_object(self, text: str) -> str:
        start = str(text or "").find("{")
        if start < 0:
            return ""
        depth = 0
        in_string = False
        escaped = False
        for idx in range(start, len(text)):
            ch = text[idx]
            if in_string:
                if escaped:
                    escaped = False
                elif ch == "\\":
                    escaped = True
                elif ch == '"':
                    in_string = False
                continue
            if ch == '"':
                in_string = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    return text[start : idx + 1]
        return ""

    def _extract_repairable_json_object(self, text: str) -> str:
        start = str(text or "").find("{")
        if start < 0:
            return ""
        candidate = text[start:].strip()
        candidate = re.sub(r"\s*```.*$", "", candidate, flags=re.S).strip()
        balance = self._json_object_balance(candidate)
        if balance > 0:
            candidate += "}" * balance
        return candidate

    def _salvage_truncated_json(self, text: str) -> str:
        """Cứu JSON cụt do model lặp vô hạn ở một field free-text.

        Chiến lược: gom các cụm lặp liên tiếp lại, bỏ phần value cụt cuối cùng
        nếu nó là một chuỗi chưa đóng và không cứu được, rồi đóng chuỗi/ngoặc.
        Mục tiêu là giữ lại các field đã hoàn tất (confidence, pressure, ...).
        """
        start = str(text or "").find("{")
        if start < 0:
            return ""
        frag = self._collapse_repetition(text[start:]).rstrip()
        # Bỏ dấu phẩy treo ở cuối.
        frag = re.sub(r",\s*$", "", frag)
        # Nếu đang ở giữa một chuỗi chưa đóng thì đóng lại.
        if self._ends_inside_string(frag):
            frag += '"'
        # Bỏ cặp "key": treo không có value (vd: ...,"dominant_judgment":)
        frag = re.sub(r',\s*"[^"\\]*"\s*:\s*$', "", frag)
        frag = re.sub(r'{\s*"[^"\\]*"\s*:\s*$', "{", frag)
        # Cân bằng ngoặc vuông rồi ngoặc nhọn.
        open_sq = frag.count("[") - frag.count("]")
        if open_sq > 0:
            frag += "]" * open_sq
        balance = self._json_object_balance(frag)
        if balance > 0:
            frag += "}" * balance
        return frag

    def _collapse_repetition_in_obj(self, obj: Any) -> Any:
        """Áp _collapse_repetition đệ quy lên mọi chuỗi trong dict/list đã parse.

        Field ngắn/có cấu trúc (source, model, id...) không có cụm lặp >=4 nên không
        bị đụng; chỉ free-text degenerate mới bị gom. An toàn cho mọi schema.
        """
        if isinstance(obj, str):
            return self._collapse_repetition(obj)
        if isinstance(obj, Mapping):
            return {key: self._collapse_repetition_in_obj(value) for key, value in obj.items()}
        if isinstance(obj, list):
            return [self._collapse_repetition_in_obj(item) for item in obj]
        if isinstance(obj, tuple):
            return tuple(self._collapse_repetition_in_obj(item) for item in obj)
        return obj

    def _collapse_repetition(self, text: str) -> str:
        """Gom các cụm ký tự lặp liên tiếp >=4 lần xuống còn 2 lần.

        Bắt cả lặp một từ ('foo foo foo foo') lẫn lặp cụm ('a b a b a b a b').
        """
        out = str(text or "")
        if len(out) < 16:
            return out
        for unit_len in range(1, 49):
            pattern = re.compile(r"(.{%d})\1{3,}" % unit_len, re.S)
            new = pattern.sub(lambda m: m.group(1) * 2, out)
            if new != out:
                out = new
        return out

    def _ends_inside_string(self, text: str) -> bool:
        in_string = False
        escaped = False
        for ch in str(text or ""):
            if in_string:
                if escaped:
                    escaped = False
                elif ch == "\\":
                    escaped = True
                elif ch == '"':
                    in_string = False
            elif ch == '"':
                in_string = True
        return in_string

    def _json_object_balance(self, text: str) -> int:
        depth = 0
        in_string = False
        escaped = False
        for ch in str(text or ""):
            if in_string:
                if escaped:
                    escaped = False
                elif ch == "\\":
                    escaped = True
                elif ch == '"':
                    in_string = False
                continue
            if ch == '"':
                in_string = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth = max(0, depth - 1)
        return depth

    def _loads_json_object(self, candidate: str) -> dict[str, Any]:
        raw = str(candidate or "").strip()
        if not raw:
            return {}
        variants = (raw, self._repair_common_json_noise(raw))
        for item in variants:
            try:
                parsed = json.loads(item)
            except Exception:
                parsed = None
            if isinstance(parsed, dict):
                return parsed
        for item in (raw, self._repair_common_json_noise(raw)):
            try:
                parsed = literal_eval(item)
            except Exception:
                parsed = None
            if isinstance(parsed, dict):
                return parsed
        return {}

    def _repair_common_json_noise(self, text: str) -> str:
        repaired = str(text or "").strip()
        repaired = re.sub(r",\s*([}\]])", r"\1", repaired)
        repaired = re.sub(r"\bNone\b", "null", repaired)
        repaired = re.sub(r"\bTrue\b", "true", repaired)
        repaired = re.sub(r"\bFalse\b", "false", repaired)
        return repaired

    def _safe_raw(self, raw: Any) -> dict[str, Any]:
        if isinstance(raw, Mapping):
            return {str(k): to_plain(v) for k, v in raw.items() if str(k) not in {"prompt", "messages"}}
        return {"raw": compact_text(raw, 1200)}

    def _log(self, level: str, msg: str, *args: Any) -> None:
        fn = getattr(self.logger, level, None) if self.logger is not None else None
        if callable(fn):
            fn(msg, *args)


# ---------------------------------------------------------------------------
# PrivateMindAdapterPool — N adapter instance độc lập, mỗi cái 1 streak riêng
# ---------------------------------------------------------------------------

_EVALUATOR_ROLES = (
    "intent_evaluator",
    "memory_resonance_evaluator",
    "risk_and_permission_evaluator",
    "future_bias_evaluator",
    "user_model_evaluator",
)


class PrivateMindAdapterPool:
    """Pool adapter cho private mind.

    Thay vì một instance dùng chung cho tất cả, mỗi vai trò có adapter riêng:
    - 4 adapter cho 4 fragment evaluator (chạy song song, streak độc lập)
    - 1 adapter dành riêng cho synthesis (payload nhỏ hơn, có thể cooldown riêng)

    Khi synthesis bị cooldown vì invalid_json thì 4 fragment evaluator vẫn chạy
    bình thường và ngược lại. Không còn một điểm hỏng làm câm toàn bộ pipeline.
    """

    def __init__(
        self,
        *,
        config: PrivateMindModelAdapterConfig | None = None,
        llm_service: Any = None,
        logger: Any = None,
    ) -> None:
        self._config = resolve_dependency(config, lambda: PrivateMindModelAdapterConfig())
        self._llm_service = llm_service
        self._logger = logger
        # Fragment có thể chạy trên provider riêng tách khỏi synthesis.
        # Nếu không khai ERIS_PRIVATE_MIND_FRAGMENT_PROVIDER thì dùng chung config.
        fragment_config = PrivateMindModelAdapterConfig.fragment_overlay_from_env(self._config)
        # Khi fragment có provider local riêng (khác config chung), KHÔNG đưa llm_service
        # (Gemini) vào — nếu không adapter sẽ ưu tiên llm_service và bắn fragment lên
        # Gemini thay vì model local. Ngược lại (không tách) thì dùng chung llm_service.
        fragment_llm_service = None if fragment_config is not self._config else llm_service
        # Tạo một adapter per evaluator role + một cho synthesis
        self._fragment_adapters: dict[str, RuntimePrivateMindModelAdapter] = {
            role: RuntimePrivateMindModelAdapter(config=fragment_config, llm_service=fragment_llm_service, logger=logger)
            for role in _EVALUATOR_ROLES
        }
        self._synthesis_adapter = RuntimePrivateMindModelAdapter(
            config=self._config, llm_service=llm_service, logger=logger
        )

    @classmethod
    def from_env(cls, *, llm_service: Any = None, logger: Any = None) -> "PrivateMindAdapterPool | None":
        apply_secrets_to_environ()
        config = PrivateMindModelAdapterConfig()
        inner_worker = getattr(llm_service, "inner_client", None) if llm_service is not None else None
        diary_worker = getattr(llm_service, "diary_client", None) if llm_service is not None else None
        bootstrap_worker = inner_worker or diary_worker
        if not config.enabled and bootstrap_worker is not None:
            config = replace(
                config,
                enabled=True,
                provider="deepseek",
                model=str(getattr(bootstrap_worker, "model_name", "deepseek-v4-flash") or "deepseek-v4-flash"),
                base_url=str(getattr(bootstrap_worker, "base_url", "") or ""),
                api_key="worker-managed",
            )
        if not config.enabled:
            return None
        pool = cls(config=config, llm_service=llm_service, logger=logger)
        fn = getattr(logger, "info", None)
        if callable(fn):
            # Fragment có thể tách provider/model riêng (vd local llamacpp) khỏi
            # synthesis. In RÕ cả hai để nhìn log là biết vai trò nào chạy ở đâu,
            # tránh hiểu nhầm "fragment×5" đi cùng provider với synthesis.
            frag_cfg = PrivateMindModelAdapterConfig.fragment_overlay_from_env(config)
            fn(
                "[PrivateMind API] ready fragment(%s/%s)×%d + synthesis(%s/%s)×1 + residue=deterministic",
                frag_cfg.provider,
                frag_cfg.model,
                len(_EVALUATOR_ROLES),
                config.provider,
                config.model,
            )
        return pool

    def fragment_adapter(self, evaluator_name: str) -> RuntimePrivateMindModelAdapter:
        """Trả adapter riêng cho evaluator_name; fallback về adapter đầu tiên nếu không tìm thấy."""
        return self._fragment_adapters.get(evaluator_name, next(iter(self._fragment_adapters.values())))

    @property
    def synthesis_adapter(self) -> RuntimePrivateMindModelAdapter:
        return self._synthesis_adapter

    @property
    def residue_adapter(self) -> RuntimePrivateMindModelAdapter:
        return None

    def residue_metadata(self) -> dict[str, Any]:
        return {"owner": "deterministic", "role": "residue_retention", "provider": "", "model": "", "configured": False}

    # --- Delegate interface giống RuntimePrivateMindModelAdapter ---
    # để code dùng pool như một adapter bình thường vẫn hoạt động.

    def evaluate_fragment(self, evaluator_name: str, dossier: EvidenceDossier) -> Any:
        return self.fragment_adapter(evaluator_name).evaluate_fragment(evaluator_name, dossier)

    def synthesize_judgment(self, dossier: EvidenceDossier, fragments: Any) -> Any:
        return self._synthesis_adapter.synthesize_judgment(dossier, fragments)

    def curate_residue(self, packet: Mapping[str, Any]) -> Any:
        return None

    def debug_status(self) -> dict[str, Any]:
        """Aggregate status của tất cả adapter trong pool."""
        fragment_statuses = {
            role: adapter.debug_status()
            for role, adapter in self._fragment_adapters.items()
        }
        synthesis_status = self._synthesis_adapter.debug_status()
        residue_status = {}
        # Tổng hợp calls/tokens
        total_calls = synthesis_status.get("calls", 0) + residue_status.get("calls", 0) + sum(
            s.get("calls", 0) for s in fragment_statuses.values()
        )
        total_tokens = (
            synthesis_status.get("usage", {}).get("total_tokens", 0)
            + residue_status.get("usage", {}).get("total_tokens", 0)
            + sum(
                s.get("usage", {}).get("total_tokens", 0) for s in fragment_statuses.values()
            )
        )
        return {
            "pool": True,
            "enabled": synthesis_status.get("enabled", False),
            "provider": synthesis_status.get("provider", ""),
            "model": synthesis_status.get("model", ""),
            "api_key_configured": synthesis_status.get("api_key_configured", False),
            "api_degraded": synthesis_status.get("api_degraded", False) or any(
                st.get("api_degraded", False) for st in fragment_statuses.values()
            ),
            "degraded_cause": synthesis_status.get("degraded_cause", ""),
            "calls": total_calls,
            "last_error": synthesis_status.get("last_error", ""),
            "invalid_json_streak": synthesis_status.get("invalid_json_streak", 0),
            "synthesis_invalid_json_streak": synthesis_status.get("synthesis_invalid_json_streak", 0),
            "synthesis_json_cooldown_until": synthesis_status.get("synthesis_json_cooldown_until", 0.0),
            "synthesis": synthesis_status,
            "residue_curator": {**residue_status, **self.residue_metadata()},
            "fragments": fragment_statuses,
            "usage": {
                "total_tokens": total_tokens,
                "cost_known": False,
                "estimated_cost_usd": None,
            },
        }
