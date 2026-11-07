# -*- coding: utf-8 -*-
"""speech_localizer.py — chuẩn hoá transcript trước khi đưa vào TTS.

Bubble có thể là tiếng Việt trong khi voice contract yêu cầu tiếng Nhật. Bước
localize vì thế là một phần của contract phát âm, không phải lớp trang trí có thể
bị bỏ qua tùy hứng. Nếu không tạo được transcript Nhật hợp lệ, đường strict sẽ
fail-closed thay vì đưa câu Việt cho TTS rồi cầu mong model tự dịch.
"""

from __future__ import annotations

import logging
import os
import re
import time
from typing import Callable, Iterable

from eris_secrets import get_secret
from runtime.diagnostic_logging import safe_log
from runtime.speech_persona_polish import (
    SpeechPersonaPolicy,
    resolve_speech_persona_policy,
    translation_respects_persona_boundaries,
)

logger = logging.getLogger("TTS.Localizer")

_JAPANESE_RE = re.compile(r"[\u3040-\u30ff\u3400-\u9fff]")
_JOIN_SIGNATURE_TAIL_RULES = (
    (re.compile(r"よ[。！？…]\s*(?:だね|よね)[。！？…]*\s*$"), "よね。"),
    (re.compile(r"だ[。！？…]\s*だね[。！？…]*\s*$"), "だね。"),
    (re.compile(r"です[。！？…]\s*ですね[。！？…]*\s*$"), "ですね。"),
)
_STANDALONE_SIGNATURE_TAIL_RE = re.compile(
    r"(?:[。！？…]\s*)(?:ですね|だね|よね)[。！？…]*\s*$"
)
_SIGNATURE_FAMILY_TAIL_RE = re.compile(r"(?:ですね|だね|よね)[。！？…]*\s*$")


class SpeechLocalizationError(RuntimeError):
    """Không tạo được transcript đúng ngôn ngữ đã pin cho turn."""


class SpeechLocalizationCancelled(SpeechLocalizationError):
    """Turn đã bị user chen ngang trong lúc localizer đang làm việc."""


def _enabled() -> bool:
    from runtime.runtime_settings_store import runtime_bool

    return runtime_bool("tts", "stage_localize", default=True)




def localizer_provider_name() -> str:
    return "deepseek"


def localizer_model_name() -> str:
    from llm.provider_policy import retired_provider
    value = os.environ.get("ERIS_TTS_LOCALIZER_MODEL", "").strip()
    if value and not value.startswith("PASTE_") and not retired_provider(model=value):
        return value
    remote = os.environ.get("ERIS_REMOTE_MODEL", "").strip()
    return remote if remote and not retired_provider(model=remote) else "deepseek-flash"


def _get_api_key_lease(*, provider: str = "deepseek", exclude_indices: Iterable[int] = ()):
    for env_name in (
        "ERIS_TTS_LOCALIZER_API_KEY", "ERIS_DS_CRITIC_API_KEY", "ERIS_STYLE_API_KEY",
        "DEEPSEEK_API_KEY", "ERIS_REMOTE_API_KEY",
    ):
        key = os.environ.get(env_name, "").strip() or get_secret(env_name).strip()
        if key and not key.startswith("PASTE_"):
            return key, None, None
    return "", None, None


def _style_rescue_api_key() -> str:
    key = get_secret("ERIS_DS_CRITIC_API_KEY", "ERIS_STYLE_API_KEY").strip()
    if key and not key.startswith("PASTE_"):
        return key
    return ""


def _already_japanese(text: str) -> bool:
    return bool(_JAPANESE_RE.search(str(text or "")))


def _valid_japanese_translation(
    source: str,
    translated: str,
    *,
    persona_policy: SpeechPersonaPolicy | None = None,
) -> bool:
    clean_source = _normalize_transcript(source)
    clean_out = _normalize_transcript(translated)
    if not (clean_out and clean_out != clean_source and _already_japanese(clean_out)):
        return False
    policy = persona_policy or resolve_speech_persona_policy(clean_source)
    return translation_respects_persona_boundaries(clean_source, clean_out, policy)


def _localizer_max_attempts() -> int:
    raw = str(os.environ.get("ERIS_TTS_LOCALIZER_MAX_ATTEMPTS", "2") or "2").strip()
    try:
        return max(1, min(4, int(raw)))
    except (TypeError, ValueError):
        return 2


def _localizer_timeout_seconds() -> float:
    # Open-model translation can be materially slower than flash models, so the
    # JP localization lane needs a longer default runway than the old 6s cap.
    raw = str(os.environ.get("ERIS_TTS_LOCALIZER_TIMEOUT_SEC", "60.0") or "60.0").strip()
    try:
        return max(1.0, min(90.0, float(raw)))
    except (TypeError, ValueError):
        return 60.0


def _cancel_requested(cancel_event=None, authority_check: Callable[[], bool] | None = None) -> bool:
    if cancel_event is not None and callable(getattr(cancel_event, "is_set", None)):
        try:
            if cancel_event.is_set():
                return True
        except Exception:
            return True
    if callable(authority_check):
        try:
            return not bool(authority_check())
        except Exception:
            return True
    return False


def _raise_if_cancelled(cancel_event=None, authority_check: Callable[[], bool] | None = None) -> None:
    if _cancel_requested(cancel_event, authority_check):
        raise SpeechLocalizationCancelled("speech_localization_cancelled")


def _localizer_output_tokens(text: str) -> int:
    """Budget output động cho bản dịch Nhật.

    Hard-code 128 token từng đủ cho mẩu 50–80 ký tự, nhưng khi ElevenLabs giữ
    cả utterance trong một clip thì localizer cũng phải được phép trả đủ câu.
    Chặn ở 1024 để một request lỗi không nuốt budget vô hạn.
    """
    clean = str(text or "").strip()
    return max(256, min(1024, len(clean) + 192))


def _split_localizer_chunks(text: str, *, max_chars: int = 540) -> tuple[str, ...]:
    """Chia bản VI thành mảnh đủ ngắn để dịch NHẬT mà không bỏ câu nào.

    Chỉ chia trên khoảng trắng nếu có thể, giữ nguyên thứ tự từ. Đây không
    phải segment TTS hay cơ chế ép Eris nói ngắn: chỉ là transport cho localizer.
    """
    source = _normalize_transcript(text)
    if not source:
        return ()
    if len(source) <= max_chars:
        return (source,)
    pieces: list[str] = []
    while len(source) > max_chars:
        candidate = source[: max_chars + 1]
        # Ưu tiên ranh giới câu, rồi mới đến ranh giới từ.
        boundaries = [match.end() for match in re.finditer(r"[.!?。！？]\s+", candidate)]
        valid = [pos for pos in boundaries if pos >= max_chars // 2]
        cut = valid[-1] if valid else candidate.rfind(" ")
        if cut < max_chars // 2:
            cut = max_chars
        pieces.append(source[:cut].strip())
        source = source[cut:].strip()
    if source:
        pieces.append(source)
    return tuple(p for p in pieces if p)


class SpeechLocalizer:
    """Dịch/style câu public thành transcript TTS đúng language contract."""

    def __init__(self) -> None:
        self._cache: dict[tuple[str, str, str], str] = {}
        self._failure_until: dict[tuple[str, str, str], float] = {}
        self._logged_persona_policies: set[str] = set()

    def localize(
        self,
        text: str,
        *,
        language_hint: str = "auto",
        prosody_style: str = "",
        strict: bool = False,
        cancel_event=None,
        authority_check: Callable[[], bool] | None = None,
    ) -> str:
        clean = _normalize_transcript(text)
        if not clean:
            if str(text or "").strip():
                return "コードを書いておいたよ。"
            return ""

        lang = str(language_hint or "auto").strip().lower()
        if lang not in {"ja", "jp", "japanese"}:
            return clean
        if _already_japanese(clean):
            return clean
        if not _enabled():
            if strict:
                raise SpeechLocalizationError("japanese_localizer_disabled")
            return clean

        style = str(prosody_style or "").strip().lower()
        persona_policy = resolve_speech_persona_policy(clean, prosody_style=style)
        log_key = persona_policy.cache_key
        if log_key not in self._logged_persona_policies:
            self._logged_persona_policies.add(log_key)
            logger.info(
                "[TTS.Localizer] speech persona profile=%s mode=%s warmth=%.2f cuteness=%.2f "
                "signature=%s surface=%s selected=%s rate=%.2f",
                persona_policy.profile,
                persona_policy.mode,
                persona_policy.warmth,
                persona_policy.cuteness,
                persona_policy.signature_ending,
                persona_policy.signature_surface,
                persona_policy.signature_ending_selected,
                persona_policy.signature_ending_rate,
            )
        key = (lang, f"{style}|{persona_policy.cache_key}", clean)
        cached = self._cache.get(key)
        if cached and _valid_japanese_translation(
            clean, cached, persona_policy=persona_policy
        ):
            return cached
        failed_until = float(self._failure_until.get(key, 0.0) or 0.0)
        if failed_until > time.monotonic():
            if strict:
                raise SpeechLocalizationError("japanese_localization_recently_failed")
            return clean

        try:
            # Với lời dài, dịch ĐỦ từng chunk rồi mới nối, tuyệt đối không
            # cắt transcript do token budget. Một chunk lỗi = lượt dịch lỗi;
            # không có chuyện đọc âm thanh Nhật 1/2 mà báo thành công.
            chunks = _split_localizer_chunks(clean)
            translated_parts: list[str] = []
            for chunk in chunks:
                _raise_if_cancelled(cancel_event, authority_check)
                chunk_policy = resolve_speech_persona_policy(chunk, prosody_style=style)
                translated_part = self._translate_to_japanese(
                    chunk,
                    prosody_style=style,
                    persona_policy=chunk_policy,
                    cancel_event=cancel_event,
                    authority_check=authority_check,
                )
                if not _valid_japanese_translation(chunk, translated_part, persona_policy=chunk_policy):
                    raise SpeechLocalizationError("japanese_localization_chunk_invalid")
                translated_parts.append(translated_part)
            translated = " ".join(translated_parts)
        except SpeechLocalizationCancelled:
            raise
        except SpeechLocalizationError:
            self._failure_until[key] = time.monotonic() + 15.0
            if strict:
                raise
            return clean

        if not _valid_japanese_translation(
            clean, translated, persona_policy=persona_policy
        ):
            self._failure_until[key] = time.monotonic() + 15.0
            if strict:
                raise SpeechLocalizationError("japanese_localization_unavailable")
            return clean
        self._cache[key] = translated
        self._failure_until.pop(key, None)
        return translated

    def _translate_to_japanese(
        self,
        text: str,
        *,
        prosody_style: str = "",
        persona_policy: SpeechPersonaPolicy | None = None,
        cancel_event=None,
        authority_check: Callable[[], bool] | None = None,
    ) -> str:
        _raise_if_cancelled(cancel_event, authority_check)
        provider_name = localizer_provider_name()

        # Dịch Nhật là required lane khi contract đã pin ja. Budget vẫn nhỏ, nhưng
        # không được optional-skip rồi lén trả transcript Việt xuống TTS.
        all_lane_budget = os.environ.get("ERIS_TOKEN_BUDGET_ALL_LANES", "1").strip().lower() not in {
            "0",
            "false",
            "off",
            "no",
        }
        plan = None
        budgeted_text = str(text or "")
        if all_lane_budget:
            try:
                from llm.token_pressure import GLOBAL_TOKEN_PRESSURE_GOVERNOR

                estimated_prompt = max(64, len(budgeted_text) // 3 + 180)
                plan = GLOBAL_TOKEN_PRESSURE_GOVERNOR.plan_request(
                    provider_name,
                    lane="speech_localizer",
                    prompt_tokens=estimated_prompt,
                    requested_output_tokens=_localizer_output_tokens(budgeted_text),
                    optional_pass=False,
                )
                if not plan.allowed:
                    raise SpeechLocalizationError(f"speech_localizer_budget_denied:{plan.reason}")
                # Chunking tại localize() giữ đủ transcript; không dùng prompt
                # estimate để chặn bản dịch và thay cả lời đọc bằng filler.
            except SpeechLocalizationError:
                raise
            except Exception:
                plan = None
                budgeted_text = str(text or "")

        policy = persona_policy or resolve_speech_persona_policy(
            text, prosody_style=prosody_style
        )
        persona_line = policy.prompt_directive()
        style_line = _style_line(prosody_style)
        prompt = (
            f"{persona_line}\n"
            f"{style_line}\n\n"
            "Transcript:\n"
            f"{budgeted_text}"
        )
        system_instruction = (
            "Translate Vietnamese or English into natural spoken Japanese for Eris. Preserve the exact meaning, "
            "current emotional temperature, energy, irritation/delight balance, and relationship distance. Do not invent questions, "
            "apologies, silence, dissatisfaction, commands, or hidden context. Do not "
            "turn satisfaction into dissatisfaction. Avoid ご主人様 and マスター. Output "
            "only Japanese, with no quotes, notes, explanations, or romanization."
        )

        attempts = _localizer_max_attempts()
        attempted_indices: set[int] = set()
        used_unpooled_key = False
        last_exc: Exception | None = None
        style_rescue_key = _style_rescue_api_key() if provider_name == "deepseek" else ""
        style_rescue_used = False

        def _try_style_rescue() -> str:
            nonlocal style_rescue_used
            if style_rescue_used or not style_rescue_key:
                return ""
            style_rescue_used = True
            try:
                rescue_response = _translate_with_deepseek_api(
                    api_key=style_rescue_key,
                    model_name=localizer_model_name(),
                    system_instruction=system_instruction,
                    prompt=prompt,
                    timeout_seconds=_localizer_timeout_seconds(),
                    max_output_tokens=_localizer_output_tokens(budgeted_text),
                )
                rescue_out = _clean_translation_output(_response_text(rescue_response))
                rescue_out = _apply_signature_surface(rescue_out, policy)
                if not _valid_japanese_translation(text, rescue_out, persona_policy=policy):
                    raise SpeechLocalizationError(
                        "style_key_rescue_invalid_or_persona_boundary_violation"
                    )
                try:
                    if plan is not None:
                        from llm.token_budget import estimate_tokens
                        from llm.token_pressure import GLOBAL_TOKEN_PRESSURE_GOVERNOR

                        GLOBAL_TOKEN_PRESSURE_GOVERNOR.record_request(
                            provider_name,
                            lane="speech_localizer",
                            prompt_tokens=estimate_tokens([{"role": "user", "content": prompt}]),
                            output_tokens=plan.output_tokens,
                        )
                    from llm.token_pressure import GLOBAL_TOKEN_PRESSURE_GOVERNOR

                    GLOBAL_TOKEN_PRESSURE_GOVERNOR.record_success(provider_name)
                except Exception as exc:
                    safe_log(logger, "debug", "TTS localizer rescue telemetry failed: %s", exc)
                logger.warning(
                    "[TTS.Localizer] critic key rescue succeeded model=%s",
                    localizer_model_name(),
                )
                return rescue_out
            except Exception as rescue_exc:
                logger.warning(
                    "[TTS.Localizer] critic key rescue failed model=%s: %s",
                    localizer_model_name(),
                    rescue_exc,
                )
                return ""

        for attempt in range(attempts):
            _raise_if_cancelled(cancel_event, authority_check)
            health = None
            reserved_index = None
            try:
                from runtime.tts_key_health import GLOBAL_TTS_KEY_LANE_HEALTH

                health = GLOBAL_TTS_KEY_LANE_HEALTH
                attempted_indices.update(health.excluded_indices())
            except Exception:
                health = None

            while True:
                api_key, pool, lease = _get_api_key_lease(
                    provider=provider_name,
                    exclude_indices=attempted_indices,
                )
                if lease is None:
                    break
                attempted_indices.add(int(lease.index))
                if health is None or health.try_reserve(lease.index):
                    reserved_index = int(lease.index) if health is not None else None
                    break
                lease = None
                api_key = ""

            # Khi không có pool, chỉ thử env key đúng một lần. Với pool, lease
            # khác sẽ được chọn ở vòng attempt kế tiếp. Không biến retry thành
            # việc gọi lại cùng một key đang 429 rồi tỏ vẻ bất ngờ.
            if lease is None and api_key:
                if used_unpooled_key:
                    break
                used_unpooled_key = True
            if not api_key:
                rescue = _try_style_rescue()
                if rescue:
                    return rescue
                last_exc = SpeechLocalizationError(f"missing_{provider_name}_key_for_japanese_localizer")
                break

            try:
                model_name = localizer_model_name()

                # PDF annotation, pages 6-9 and 15: SDK calls must not bypass
                # global admission merely because they do not use llm/client.py.
                from llm.token_budget import estimate_tokens
                from runtime.inference_broker import GLOBAL_INFERENCE_BROKER, inference_context
                from runtime.work_keys import stable_work_key

                output_reserve = int(getattr(plan, "output_tokens", 384) or 384)
                with inference_context(
                    "speech_localizer",
                    optional=False,
                    timeout_seconds=_localizer_timeout_seconds(),
                    cancel_scope="background:speech_localizer",
                    coalesce_key=stable_work_key("speech_localizer", text),
                    metadata={"attempt": attempt, "transport": f"{provider_name}.text"},
                ):
                    broker_request = GLOBAL_INFERENCE_BROKER.request_from_context(
                        provider=provider_name,
                        model=model_name,
                        estimated_input_tokens=estimate_tokens([{"role": "user", "content": prompt}]),
                        reserved_output_tokens=output_reserve,
                        timeout_seconds=_localizer_timeout_seconds(),
                    )
                    def call_localizer(*, cancel_token=None):
                        if cancel_token is not None:
                            cancel_token.raise_if_cancelled(broker_request)
                        result = _translate_with_deepseek_api(
                            api_key=api_key,
                            model_name=model_name,
                            system_instruction=system_instruction,
                            prompt=prompt,
                            timeout_seconds=_localizer_timeout_seconds(),
                            max_output_tokens=_localizer_output_tokens(budgeted_text),
                        )
                        if cancel_token is not None:
                            cancel_token.raise_if_cancelled(broker_request)
                        return result

                    response = GLOBAL_INFERENCE_BROKER.run(broker_request, call_localizer)
                _raise_if_cancelled(cancel_event, authority_check)
                out = _clean_translation_output(_response_text(response))
                out = _apply_signature_surface(out, policy)
                if not _valid_japanese_translation(
                    text, out, persona_policy=policy
                ):
                    rescue = _try_style_rescue()
                    if rescue:
                        return rescue
                    raise SpeechLocalizationError(
                        "localizer_returned_invalid_or_persona_boundary_violation"
                    )

                try:
                    if plan is not None:
                        from llm.token_budget import estimate_tokens
                        from llm.token_pressure import GLOBAL_TOKEN_PRESSURE_GOVERNOR

                        GLOBAL_TOKEN_PRESSURE_GOVERNOR.record_request(
                            provider_name,
                            lane="speech_localizer",
                            prompt_tokens=estimate_tokens([{"role": "user", "content": prompt}]),
                            output_tokens=plan.output_tokens,
                        )
                    if pool is not None and lease is not None:
                        pool.mark_success(lease=lease)
                    from llm.token_pressure import GLOBAL_TOKEN_PRESSURE_GOVERNOR

                    GLOBAL_TOKEN_PRESSURE_GOVERNOR.record_success(provider_name)
                except Exception as exc:
                    safe_log(logger, "debug", "TTS localizer success telemetry failed: %s", exc)
                return out
            except SpeechLocalizationCancelled:
                raise
            except Exception as exc:
                last_exc = exc
                may_retry = attempt < attempts - 1
                try:
                    from llm.provider_errors import classify_provider_error

                    kind = classify_provider_error(exc)
                    if pool is not None and lease is not None:
                        if str(kind) == "key_revoked":
                            pool.mark_permanently_dead(lease=lease, reason="localizer_key_reported_leaked")
                        elif str(kind) == "rate_limit":
                            may_retry = bool(
                                pool.mark_rate_limited(
                                    seconds=60.0,
                                    lease=lease,
                                    reason=f"localizer_http_429:{localizer_model_name()}",
                                )
                            ) and may_retry
                            try:
                                from llm.token_pressure import GLOBAL_TOKEN_PRESSURE_GOVERNOR

                                GLOBAL_TOKEN_PRESSURE_GOVERNOR.record_rate_limit(
                                    provider_name,
                                    retry_after_s=60.0,
                                    lane="speech_localizer",
                                )
                            except Exception as pressure_exc:
                                safe_log(logger, "debug", "TTS rate-limit telemetry failed: %s", pressure_exc)
                        elif str(kind) == "project_permission":
                            pool.mark_dead(3600.0, lease=lease, reason="localizer_project_permission")
                except Exception as classify_exc:
                    safe_log(logger, "debug", "TTS provider error classification failed: %s", classify_exc)

                logger.warning(
                    "[TTS.Localizer] Dịch sang JP lỗi model=%s lượt %d/%d: %s",
                    localizer_model_name(),
                    attempt + 1,
                    attempts,
                    exc,
                )
                if not may_retry:
                    break
                delay = min(0.18, 0.05 * (attempt + 1))
                if cancel_event is not None and callable(getattr(cancel_event, "wait", None)):
                    if cancel_event.wait(delay):
                        raise SpeechLocalizationCancelled("speech_localization_cancelled")
                else:
                    time.sleep(delay)
            finally:
                if health is not None:
                    health.release(reserved_index)

        if last_exc is not None:
            rescue = _try_style_rescue()
            if rescue:
                return rescue
            if isinstance(last_exc, SpeechLocalizationError):
                raise last_exc
            detail = str(last_exc or "").strip()
            suffix = type(last_exc).__name__ if last_exc else "unknown"
            if detail:
                suffix = f"{suffix}:{detail}"
            raise SpeechLocalizationError(f"japanese_localization_failed:{suffix}")


def _normalize_transcript(text: str) -> str:
    """Giữ line break như nhịp thở, chỉ dọn khoảng trắng nội dòng."""

    raw = str(text or "").replace("\r\n", "\n").replace("\r", "\n")
    raw = re.sub(r"(?:```[\w.+-]*\n?[\s\S]*?(?:```|\Z)|~~~[\w.+-]*\n?[\s\S]*?(?:~~~|\Z))", "", raw, flags=re.S)
    raw = re.sub(r"`[^`\n]*`", "", raw)
    lines = [" ".join(line.split()).strip() for line in raw.split("\n")]
    return "\n".join(line for line in lines if line).strip()


def _clean_translation_output(text: str) -> str:
    """Dọn output dịch nhưng không flatten nhiều dòng thành một dòng."""

    raw = str(text or "").replace("\r\n", "\n").replace("\r", "\n")
    raw = raw.strip().strip('"').strip("'").strip()
    for pattern, replacement in _JOIN_SIGNATURE_TAIL_RULES:
        raw = pattern.sub(replacement, raw)
    raw = _STANDALONE_SIGNATURE_TAIL_RE.sub("。", raw).strip()
    lines = [" ".join(line.split()).strip() for line in raw.split("\n")]
    return "\n".join(line for line in lines if line).strip()


def _response_text(response: object) -> str:
    if isinstance(response, str):
        return response
    if isinstance(response, dict):
        choices = response.get("choices")
        if isinstance(choices, list) and choices:
            first = choices[0] if isinstance(choices[0], dict) else {}
            message = first.get("message") if isinstance(first, dict) else {}
            if isinstance(message, dict):
                return str(message.get("content", "") or "")
        return str(response.get("text", "") or "")
    return str(getattr(response, "text", "") or "")


def _translate_with_deepseek_api(
    *,
    api_key: str,
    model_name: str,
    system_instruction: str,
    prompt: str,
    timeout_seconds: float,
    max_output_tokens: int = 384,
) -> dict[str, object]:
    import requests

    base_url = (
        os.environ.get("ERIS_TTS_LOCALIZER_BASE_URL", "").strip()
        or os.environ.get("ERIS_REMOTE_BASE_URL", "").strip()
        or get_secret("ERIS_TTS_LOCALIZER_BASE_URL", "ERIS_REMOTE_BASE_URL").strip()
        or "http://127.0.0.1:5103/v1"
    )
    from llm.provider_policy import require_active_provider
    require_active_provider(base_url=base_url, model=model_name)
    url = f"{base_url.rstrip('/')}/chat/completions"
    payload = {
        "model": model_name,
        "messages": [
            {"role": "system", "content": system_instruction},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.15,
        "max_tokens": max(128, min(1024, int(max_output_tokens or 384))),
        "thinking": {"type": "disabled"},
    }
    # Localizer gọi requests.post trực tiếp, KHÔNG đi qua llm/client.py.
    # Gắn cùng raw trace để danh sách DeepSeek lane không bị thiếu TTS localizer.
    from llm.raw_trace import begin_http_attempt

    trace = begin_http_attempt(
        url=url, model=model_name, body=payload, headers={},
        lane="tts_localizer", cognitive_lane="speech_localization",
        router_reason="tts_stage_localize", worker_role="tts_localizer",
    )
    try:
        response = requests.post(
            url,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=max(1.0, float(timeout_seconds or 4.0)),
        )
    except BaseException as exc:
        if trace is not None:
            trace.error(exc)
        raise
    if trace is not None:
        trace.response(response)
    response.raise_for_status()
    return response.json()


def _apply_signature_surface(text: str, persona_policy: SpeechPersonaPolicy | None) -> str:
    out = str(text or "").strip()
    if (
        not out
        or persona_policy is None
        or not getattr(persona_policy, "signature_ending_selected", False)
        or not getattr(persona_policy, "signature_surface", "")
        or _SIGNATURE_FAMILY_TAIL_RE.search(out)
    ):
        return out
    surface = str(getattr(persona_policy, "signature_surface", "") or "")
    if re.search(r"よ[。！？…]*\s*$", out):
        return re.sub(r"よ[。！？…]*\s*$", "よね。", out)
    if re.search(r"です[。！？…]*\s*$", out):
        return re.sub(r"です[。！？…]*\s*$", "ですね。", out)
    if re.search(r"だ[。！？…]*\s*$", out) and surface in {"だね", "よね"}:
        return re.sub(r"だ[。！？…]*\s*$", "だね。" if surface == "だね" else "だよね。", out)
    return out


def _style_line(style: str) -> str:
    """Hint dịch tự nhiên theo prosody, không biến nghĩa câu."""

    style = str(style or "").strip().lower()
    if style == "dry_tease":
        return "Style hint: preserve the dry teasing edge at the intensity already chosen by the live delivery state; slightly amused when supported, never force a warm base."
    if style == "soft_tease":
        return "Style hint: preserve soft teasing and current playfulness; do not automatically restrain brightness when the live state is lively."
    if style == "self_aware_dry":
        return "Style hint: self-aware and dry; let current affect decide whether the repair sounds relieved, annoyed, amused, low-energy, or neutral."
    if style == "annoyed_soft":
        return "Style hint: mild irritation is real here; keep it controlled and clear, but do not cover it with mandatory warmth."
    if style == "sharp_deadpan":
        return "Style hint: one sharp deadpan retort; intensity follows the current affect and boundary state, with no mandatory warm underlay."
    if style == "cold_boundary":
        return "Style hint: firm boundary, direct and non-threatening. Respect does not require sounding warm; follow the current affect."
    if style == "deadpan":
        return "Style hint: deadpan delivery; keep the current emotional temperature instead of adding a warm base or artificial coldness."
    if style == "focused":
        return "Style hint: technical and clear while preserving the current affect; technical mode must not reset emotional temperature."
    if style == "low_quiet":
        return "Style hint: quiet and understated because the live state is low-energy; do not add a mandatory warm pulse or dramatic wording."
    return "Style hint: natural delivery that follows the current affect and relationship state; do not normalize toward warm, cold, restrained, bubbly, or concise."


__all__ = [
    "SpeechLocalizationCancelled",
    "SpeechLocalizationError",
    "SpeechLocalizer",
    "localizer_model_name",
]
