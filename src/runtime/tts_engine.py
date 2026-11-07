# -*- coding: utf-8 -*-
"""tts_engine.py — Emotional TTS cho ERIS, ElevenLabs-first.

DeepSeek STYLE lập VoicePlan side-channel; engine chuyển nhịp, điểm nhấn, năng
lượng và pitch thành request ElevenLabs. Provider local giữ vai trò fallback.

Transcript UI/memory luôn sạch. Audio tags và tiếng đệm Nhật chỉ tồn tại bên
trong đường TTS. Playback vẫn dùng WAV PCM mono 24 kHz qua backend hiện có.
"""

from __future__ import annotations

import io
import asyncio
import inspect
import json
import logging
import os
import queue
import random
import re
import struct
import subprocess
import tempfile
import threading
import time
import unicodedata
import wave
from dataclasses import dataclass
from typing import Any, Mapping, Optional

from runtime.prosody_director import ProsodyDirector
from runtime.audio_lip_sync import build_lip_sync_envelope
from runtime.speech_localizer import (
    SpeechLocalizationCancelled,
    SpeechLocalizationError,
    SpeechLocalizer,
    localizer_model_name,
)
from runtime.tts_audio_polish import audio_polish_enabled, polish_pcm16_mono
from runtime.tts_markup import render_tts_markup
from runtime.vocal_cache import VocalCache, VocalCacheKey, strip_audible_prefix, vocal_cache_enabled
from runtime.speech_contract import normalize_language
from runtime.addressing_policy import clean_public_addressing
from runtime.tts_circuit_breaker import GLOBAL_TTS_BREAKER

logger = logging.getLogger("TTS")


@dataclass(frozen=True, slots=True)
class TtsPlaybackEvent:
    """Lifecycle event của một đoạn TTS.

    Đây là phần "end đúng": caller biết đoạn nào đã được queue, bắt đầu phát,
    phát xong, bị hủy, hoặc lỗi. UI/interrupt ledger có thể bám vào event này
    mà không cần đoán từ log hay thread state.
    """

    type: str
    turn_id: str = ""
    segment_id: int = 0
    text: str = ""
    reason: str = ""
    error: str = ""
    audio_envelope: tuple[float, ...] = ()
    audio_frame_ms: int = 40
    audio_duration_ms: int = 0
    audio_mouth_shape: tuple[float, ...] = ()

# ── Feature flag (runtime language reload) ───────────────────────────────────

def _enabled() -> bool:
    from runtime.runtime_settings_store import runtime_bool
    return runtime_bool("tts", "enabled", default=True)

def _tts_provider() -> str:
    """ElevenLabs is the sole remote speech provider."""
    return "elevenlabs"






def _voice(language_hint: str = "") -> str:
    from runtime.runtime_settings_store import runtime_text

    if _tts_provider() == "elevenlabs":
        language = _normalize_language_hint(language_hint or _language_hint())
        if language in {"vi", "ja"}:
            selected = runtime_text("tts", f"elevenlabs_voice_id_{language}", default="").strip()
            if selected:
                return selected
        return (
            os.environ.get("ERIS_ELEVENLABS_VOICE_ID", "").strip()
            or runtime_text("tts", "elevenlabs_voice_id", default="fUjY9K2nAIwlALOwSiwc").strip()
            or "fUjY9K2nAIwlALOwSiwc"
        )
    return ""


def _model() -> str:
    if _tts_provider() == "elevenlabs":
        from runtime.runtime_settings_store import runtime_text

        return (
            os.environ.get("ERIS_ELEVENLABS_MODEL", "").strip()
            or runtime_text("tts", "elevenlabs_model", default="eleven_v3").strip()
            or "eleven_v3"
        )
    return "eleven_v3"

def _normalize_language_hint(value: str) -> str:
    """Chuẩn hoá value từ .env/UI thành mã ngôn ngữ nội bộ ổn định."""
    raw = str(value or "").strip().lower()
    if raw in {"", "auto", "vi", "vietnamese", "vi-vn"}:
        return "vi"
    if raw in {"ja", "jp", "japanese", "ja-jp", "jp-jp", "日本語", "日本语"}:
        return "ja"
    return raw


def _language_hint() -> str:
    # Voice contract defaults to Japanese; Vietnamese text is localized first.
    from runtime.runtime_settings_store import runtime_text
    return _normalize_language_hint(runtime_text("tts", "language", default="ja"))


def _quality_mode() -> str:
    """balanced | high.

    ``high`` không gọi thêm API; nó chỉ đổi prompt/markup để Gemini diễn câu kỹ
    hơn. Nếu cần latency thấp hơn, set ERIS_TTS_QUALITY=balanced.
    """
    from runtime.runtime_settings_store import runtime_text
    value = runtime_text("tts", "quality", default="high").strip().lower()
    return value if value in {"balanced", "high"} else "high"



def _output_volume() -> float:
    raw = os.environ.get("ERIS_TTS_VOLUME", "").strip()
    if raw:
        try:
            return max(0.0, min(1.0, float(raw)))
        except ValueError:
            pass
    from runtime.runtime_settings_store import runtime_float
    value = runtime_float("tts", "volume", default=1.0)
    return max(0.0, min(1.0, value))


def _speech_speed() -> float:
    raw = os.environ.get("ERIS_TTS_SPEED", "").strip()
    if raw:
        try:
            return max(0.85, min(1.15, float(raw)))
        except ValueError:
            pass
    from runtime.runtime_settings_store import runtime_float
    value = runtime_float("tts", "speed", default=1.0)
    return max(0.85, min(1.15, value))


def _pace_directive() -> str:
    speed = _speech_speed()
    if speed <= 0.90:
        return "Pacing target: noticeably slower and more deliberate than normal, without stretching vowels unnaturally.\n"
    if speed >= 1.10:
        return "Pacing target: brisk and responsive, about ten percent faster than normal, while keeping articulation clear.\n"
    if speed < 0.98:
        return "Pacing target: slightly slower than normal.\n"
    if speed > 1.02:
        return "Pacing target: slightly faster than normal.\n"
    return ""

def _queue_size() -> int:
    # Legacy fallback only. Keep enough headroom for the full conversation
    # StageOutput budget (8 segments) plus control traffic. A default of 4 was
    # able to delete seq=1 from a five-segment turn.
    raw = os.environ.get("ERIS_TTS_QUEUE_SIZE", "12").strip()
    try:
        return max(8, min(24, int(raw)))
    except ValueError:
        return 12




def _prelocalize_japanese_enabled() -> bool:
    raw = os.environ.get("ERIS_TTS_PRELOCALIZE_JA")
    if raw is not None and str(raw).strip():
        return str(raw).strip().lower() in {"1", "true", "yes", "on"}
    from runtime.runtime_settings_store import runtime_bool
    return runtime_bool("tts", "stage_localize", default=True)


def _speech_contract_fail_open_enabled() -> bool:
    return os.environ.get("ERIS_TTS_FAIL_OPEN_ON_LOCALIZATION_ERROR", "0").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _fallback_language_for_transcript(text: str) -> str:
    return "ja" if _contains_japanese_chars(text) else "vi"


def _local_tts_fallback_enabled() -> bool:
    return os.environ.get("ERIS_TTS_LOCAL_FALLBACK", "1").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _edge_tts_fallback_enabled() -> bool:
    return os.environ.get("ERIS_TTS_EDGE_FALLBACK", "1").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _sapi_tts_fallback_enabled() -> bool:
    # SAPI keeps Eris from going silent, but the stock Windows voices are not
    # suitable for the anime/Japanese persona, so only use it when requested.
    return os.environ.get("ERIS_TTS_SAPI_FALLBACK", "0").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


_CACHED_BEARER_TOKEN: str = ""
_CACHED_BEARER_EXPIRES_AT: float = 0.0
_ELEVENLABS_SESSION: Any = None
_ELEVENLABS_SESSION_LOCK = threading.Lock()


def _get_elevenlabs_http_session():
    global _ELEVENLABS_SESSION
    if _ELEVENLABS_SESSION is None:
        with _ELEVENLABS_SESSION_LOCK:
            if _ELEVENLABS_SESSION is None:
                import requests

                session = requests.Session()
                adapter = requests.adapters.HTTPAdapter(pool_connections=5, pool_maxsize=10)
                session.mount("https://", adapter)
                _ELEVENLABS_SESSION = session
    return _ELEVENLABS_SESSION


def _get_elevenlabs_token(
    *,
    required_characters: int = 0,
    excluded_account_ids: set[int] | None = None,
) -> tuple[str, str, int]:
    """Trả về (auth_type, token, account_id).

    Tải token ưu tiên từ SQLite Pool (GLOBAL_ELEVEN_POOL) để xoay tài khoản tự động.
    """
    global _CACHED_BEARER_TOKEN, _CACHED_BEARER_EXPIRES_AT
    from eris_secrets import get_secret
    import time

    now = time.time()
    pool_configured = False
    try:
        from runtime.elevenlabs_pool_db import GLOBAL_ELEVEN_POOL
        pool_configured = GLOBAL_ELEVEN_POOL.has_accounts()
        excluded = set(excluded_account_ids or set())
        # Thử lần lượt các account đủ quota. Account dưới 100 quota bị pool
        # loại trước khi runtime kịp gửi request.
        for _ in range(64):
            acc = GLOBAL_ELEVEN_POOL.get_active_account(
                required_characters=required_characters,
                exclude_account_ids=excluded,
            )
            if acc is None:
                break
            if acc.bearer_token and now < acc.bearer_expires_at:
                return "bearer", acc.bearer_token, acc.id
            ok, msg = GLOBAL_ELEVEN_POOL.sync_subscription(acc.id)
            updated_acc = GLOBAL_ELEVEN_POOL.get_account_by_id(acc.id)
            if (
                ok
                and updated_acc is not None
                and updated_acc.status == "active"
                and updated_acc.bearer_token
            ):
                return "bearer", updated_acc.bearer_token, updated_acc.id
            excluded.add(acc.id)
            logger.warning("[TTS/ElevenLabsPool] Bỏ account %d: %s", acc.id, msg)
    except Exception as exc:
        logger.debug("[TTS/ElevenLabsPool] Pool check error: %s", exc)

    if pool_configured:
        # Pool có account nhưng không account nào đạt ngưỡng 100/đủ độ dài.
        # Không quay lại key đơn vì rất có thể đó chính là account vừa bị loại.
        return "", "", 0

    api_key = str(_direct_elevenlabs_api_key() or "").strip()
    if api_key and not api_key.startswith("eyJ"):
        return "xi-api-key", api_key, 0

    if _CACHED_BEARER_TOKEN and now < _CACHED_BEARER_EXPIRES_AT:
        return "bearer", _CACHED_BEARER_TOKEN, 0

    refresh_token = str(get_secret("ELEVENLABS_REFRESH_TOKEN") or "").strip()
    firebase_key = str(get_secret("FIREBASE_API_KEY") or "").strip()
    bearer_token = str(get_secret("ELEVENLABS_BEARER_TOKEN") or "").strip()

    if refresh_token and firebase_key:
        try:
            session = _get_elevenlabs_http_session()
            url = f"https://securetoken.googleapis.com/v1/token?key={firebase_key}"
            headers = {
                "Referer": "https://elevenlabs.io/",
                "Origin": "https://elevenlabs.io",
                "Content-Type": "application/x-www-form-urlencoded",
            }
            data = {"grant_type": "refresh_token", "refresh_token": refresh_token}
            r = session.post(url, headers=headers, data=data, timeout=10)
            if r.status_code == 200:
                new_token = str(r.json().get("id_token") or "").strip()
                if new_token:
                    _CACHED_BEARER_TOKEN = new_token
                    _CACHED_BEARER_EXPIRES_AT = now + 3000.0
                    return "bearer", new_token, 0
        except Exception as exc:
            logger.debug("[TTS/ElevenLabs] refresh token failed: %s", exc)

    if bearer_token:
        _CACHED_BEARER_TOKEN = bearer_token
        _CACHED_BEARER_EXPIRES_AT = now + 1800.0
        return "bearer", bearer_token, 0

    if api_key:
        return "xi-api-key", api_key, 0

    return "", "", 0


def warmup_elevenlabs() -> None:
    """Pre-fetch token, start Web UI server, and warm up HTTP Keep-Alive pool in background."""
    def _do_warmup():
        try:
            try:
                from runtime.elevenlabs_pool_server import start_elevenlabs_pool_server
                start_elevenlabs_pool_server()
            except Exception as exc:
                logger.debug("[TTS/ElevenLabsPool] Web UI server start error: %s", exc)
            try:
                from runtime.elevenlabs_pool_db import GLOBAL_ELEVEN_POOL
                GLOBAL_ELEVEN_POOL.start_background_sync()
            except Exception as exc:
                logger.debug("[TTS/ElevenLabsPool] Auto-sync thread start error: %s", exc)
            _get_elevenlabs_token()
            _get_elevenlabs_http_session()
            logger.info("[TTS/ElevenLabs] Background token and connection pool warm-up complete.")
        except Exception as exc:
            logger.debug("[TTS/ElevenLabs] Warmup exception: %s", exc)

    threading.Thread(target=_do_warmup, daemon=True, name="ElevenLabs-Warmup").start()


def _direct_elevenlabs_api_key() -> str:
    from eris_secrets import get_secret

    return str(
        os.environ.get("ELEVENLABS_API_KEY", "").strip()
        or os.environ.get("ERIS_ELEVENLABS_API_KEY", "").strip()
        or get_secret("ELEVENLABS_API_KEY", "ERIS_ELEVENLABS_API_KEY")
        or ""
    ).strip()


def _elevenlabs_api_key() -> str:
    _, token, _ = _get_elevenlabs_token()
    return token


def _elevenlabs_auth_configured_fast() -> bool:
    """Return whether ElevenLabs auth exists without network/quota sync.

    Startup must not block the chat loop on Firebase/ElevenLabs checks. The
    real token refresh still happens in the warm-up thread or on first speech.
    """
    from eris_secrets import get_secret

    if _CACHED_BEARER_TOKEN and time.time() < _CACHED_BEARER_EXPIRES_AT:
        return True
    if _direct_elevenlabs_api_key():
        return True
    if str(get_secret("ELEVENLABS_BEARER_TOKEN") or "").strip():
        return True
    if (
        str(get_secret("ELEVENLABS_REFRESH_TOKEN") or "").strip()
        and str(get_secret("FIREBASE_API_KEY") or "").strip()
    ):
        return True
    try:
        from runtime.elevenlabs_pool_db import GLOBAL_ELEVEN_POOL

        return bool(GLOBAL_ELEVEN_POOL.has_accounts())
    except Exception as exc:
        logger.debug("[TTS/ElevenLabsPool] Fast auth check error: %s", exc)
        return False


def _elevenlabs_base_url() -> str:
    return str(os.environ.get("ERIS_ELEVENLABS_BASE_URL", "https://api.elevenlabs.io") or "").strip().rstrip("/")


def _elevenlabs_timeout_seconds() -> float:
    raw = str(os.environ.get("ERIS_ELEVENLABS_TIMEOUT_S", "120") or "120").strip()
    try:
        return max(20.0, min(300.0, float(raw)))
    except ValueError:
        return 120.0


def _elevenlabs_gemini_fallback_enabled() -> bool:
    return False


def _elevenlabs_float(name: str, default: float, low: float, high: float) -> float:
    raw = str(os.environ.get(name, default) or default).strip()
    try:
        return max(low, min(high, float(raw)))
    except (TypeError, ValueError):
        return default


def _effective_elevenlabs_speed(segment_speed: float, configured_speed: float) -> float:
    """Provider-specific clamp documented by ElevenLabs: 0.7-1.2."""

    try:
        raw = float(segment_speed) * float(configured_speed) * 1.05
    except (TypeError, ValueError):
        raw = 1.0
    return max(0.70, min(1.20, raw))


# ── Emotion → Audio Tag mapping ───────────────────────────────────────────────
# valence: [-1, 1]  arousal: [0, 1]  stance: string từ appraisal
# Gemini audio tags: inline trong text, phải là tiếng Anh cho best results

_STANCE_TO_TAG: dict[str, str] = {
    # tích cực
    "warm":              "warmly",
    "playful":           "playfully",
    "curious":           "curiously",
    "amused":            "with a slight laugh",
    "tender":            "gently",
    "affectionate":      "warmly, soft",
    "hopeful":           "hopefully",
    "content":           "contentedly",
    # trung tính / tập trung
    "focused":           "clearly",
    "composed":          "evenly",
    "neutral":           "",
    "detached":          "flatly",
    "guarded":           "carefully",
    "guarded_tender":    "gently but carefully",
    "observant":         "with calm curiosity",
    # tiêu cực / căng thẳng
    "melancholy":        "softly, with a hint of sadness",
    "sad":               "sadly",
    "withdrawn":         "quietly, distant",
    "anxious":           "nervously",
    "irritated":         "with slight irritation",
    "frustrated":        "with frustration",
    "tired":             "tiredly",
    "overwhelmed":       "trembling, overwhelmed",
    # cảm xúc mạnh
    "excited":           "excitedly",
    "surprised":         "surprised",
    "fearful":           "with a trembling voice",
    "panicked":          "panicked",
}

def _emotion_to_tag(
    valence: float,
    arousal: float,
    stance: str = "",
) -> str:
    """Chuyển valence/arousal/stance thành audio tag Gemini."""
    stance_clean = (stance or "").strip().lower()

    # Ưu tiên stance nếu có mapping rõ
    if stance_clean in _STANCE_TO_TAG:
        tag = _STANCE_TO_TAG[stance_clean]
        if tag:
            return f"[{tag}]"

    # Fallback từ valence × arousal
    # Vui + kích thích cao → excited
    if valence > 0.4 and arousal > 0.55:
        return "[excitedly]"
    # Vui nhẹ + arousal trung = playful/warm
    if valence > 0.25 and arousal > 0.3:
        return "[warmly]"
    # Vui nhẹ, bình tĩnh = content
    if valence > 0.1:
        return "[contentedly]"
    # Buồn + low arousal = softly/sadly
    if valence < -0.3 and arousal < 0.35:
        return "[softly, with sadness]"
    # Buồn + arousal cao = trembling/anxious
    if valence < -0.3 and arousal > 0.5:
        return "[trembling]"
    # Tiêu cực nhẹ
    if valence < -0.1:
        return "[quietly]"
    # Arousal cao, valence trung = tập trung/nghiêm túc
    if arousal > 0.65:
        return "[seriously]"
    # Default
    return ""


# ── System prompt cho Gemini TTS (character profile) ─────────────────────────

def _build_tts_prompt(
    text: str,
    audio_tag: str,
    language_hint: str,
    *,
    prosody_notes: str = "",
    prelocalized: bool = False,
    quality_mode: str = "high",
) -> str:
    """
    Xây dựng prompt TTS theo format advanced prompting của Gemini.

    language_hint:
      "auto" hoặc ""  = để Gemini tự detect ngôn ngữ từ transcript (khuyên dùng).
      "ja"            = ép Japanese (dịch transcript sang tiếng Nhật trước khi đọc).
      bất kỳ string   = truyền thẳng vào director notes.

    Style anime girl giữ nguyên bất kể ngôn ngữ — Gemini sẽ apply accent/breathiness
    lên ngôn ngữ tự detect được.
    """
    # Language instruction — chỉ thêm nếu được chỉ định rõ, không phải "auto"
    lang_line = ""
    if language_hint and language_hint.lower() not in ("auto", ""):
        if language_hint == "ja":
            if prelocalized:
                lang_line = (
                    "The transcript is already in Japanese. Read it aloud in Japanese. "
                    "Do not translate it again and do not read any hidden notes.\n"
                )
            else:
                lang_line = (
                    "The transcript must already be Japanese. Read Japanese only. "
                    "Do not translate, infer, or switch to Vietnamese or English.\n"
                )
        elif language_hint == "vi":
            lang_line = (
                "The transcript is in Vietnamese. Read it in Vietnamese only. "
                "Do not translate to Japanese or English, and do not switch language mid-line.\n"
            )
        else:
            lang_line = f"The transcript language is {language_hint}. Read it naturally.\n"

    # Inject audio tag vào đầu transcript
    tagged_text = f"{audio_tag} {text}".strip() if audio_tag else text
    quality = str(quality_mode or "high").strip().lower()
    if quality not in {"balanced", "high"}:
        quality = "high"

    prosody_line = ""
    if prosody_notes:
        prosody_line = f"Line performance: {prosody_notes}\n"

    signature_line = ""
    if language_hint == "ja" and re.search(r"(?:ですね|だね|よね)[。！？…]*\s*$", str(text or "")):
        signature_line = (
            "Signature ending performance: let the final ね land with a small 140–220ms linger, "
            "a tiny lift into the marker, then a gentle downward contour. Make it memorable at the "
            "warmth/brightness actually supported by the current performance state; do not turn it into a theatrical anime catchphrase.\n"
        )

    performance_rules = ""
    transcript_title = "## TRANSCRIPT"
    if quality == "high":
        transcript_title = "## PERFORMANCE SCRIPT"
        performance_rules = (
            "Performance rules:\n"
            "- Bracketed audio tags are acting directions. Never speak the tag text aloud.\n"
            "- Line breaks are short speech beats, usually under 350ms; avoid long silence between lines.\n"
            "- For dry teasing or deadpan lines, press the final phrase lightly, as if amused but unimpressed.\n"
            "- Keep the anime quality understated: close, crisp, and alive, not theatrical or childish.\n"
            "- Do not read ellipses as words; use them only as tiny pauses.\n"
            "- Never use old Vietnamese honorifics or the word 'Master'; say 'bạn' if a direct address is needed.\n\n"
        )

    # Phase 7: prompt compact. Style/persona dài được nén thành các trục có
    # tác dụng âm học; không gửi lại một bài luận cho từng segment ngắn.
    prompt = (
        "ERIS VOICE\n"
        "Character: youthful, close, crisp desktop companion; emotional temperature follows the current line/performance state.\n"
        "Delivery: emotionally nuanced and state-responsive; do not flatten joy, irritation, sadness, playfulness, or curiosity into a fixed restrained/warm baseline. Never become theatrical.\n"
        "Pacing: responsive and natural; tiny rhetorical pauses only, no long stalls.\n"
        "Rules: bracketed tags are silent acting directions; never read them aloud. "
        "Do not add, omit, paraphrase, or explain words.\n"
        f"{_pace_directive()}\n"
        f"{prosody_line}"
        f"{signature_line}"
        f"{lang_line}"
        f"{transcript_title}\n"
        f"{tagged_text}\n"
    )
    return prompt


# ── ElevenLabs TTS ────────────────────────────────────────────────────────────

_ELEVEN_VOCALIZATION_TAGS: dict[str, str] = {
    "small_laugh": "[laughs]",
    "soft_sigh": "[sighs]",
}


def _voice_director_cue_token(kind: object, *, language_hint: str) -> str:
    """Render đúng một cue cho transcript TTS, không chạm public text."""
    cue = str(kind or "none").strip().lower()
    tagged = _ELEVEN_VOCALIZATION_TAGS.get(cue, "")
    if tagged:
        return tagged
    try:
        from runtime.voice_director import vocalization_text

        return vocalization_text(cue, language_hint)
    except Exception:
        return ""


def _apply_voice_director_cue(
    body: str,
    segment: Mapping[str, object],
    *,
    language_hint: str,
) -> str:
    cue = _voice_director_cue_token(segment.get("vocalization", "none"), language_hint=language_hint)
    if not cue:
        return str(body or "").strip()
    position = str(segment.get("vocalization_position", "prefix") or "prefix").strip().lower()
    if position == "suffix":
        return f"{str(body or '').strip()} {cue}".strip()
    return f"{cue} {str(body or '').strip()}".strip()


_ELEVEN_STYLE_TAGS: dict[str, tuple[str, ...]] = {
    "neutral": (),
    "focused": ("[calm and clear]",),
    "warm_soft": ("[warmly]", "[softly]"),
    "curious_soft": ("[curious]", "[softly]"),
    "soft_tease": ("[mischievously]", "[softly]"),
    "dry_tease": ("[dryly]", "[mischievously]"),
    "self_aware_dry": ("[quietly]", "[dryly]"),
    "annoyed_soft": ("[slightly annoyed]", "[controlled]"),
    "sharp_deadpan": ("[deadpan]", "[sharply]"),
    "cold_boundary": ("[serious]", "[controlled]"),
    "deadpan": ("[deadpan]",),
    "low_quiet": ("[quietly]", "[low energy]"),
}


def _eleven_emphasize(text: str, phrases: object, *, language_hint: str) -> str:
    """Đặt quote nhẹ quanh tối đa hai phrase để ElevenLabs ưu tiên điểm rơi."""
    if not isinstance(phrases, (list, tuple)):
        return text
    rendered = str(text or "")
    japanese = normalize_language(language_hint) == "ja"
    left, right = ("「", "」") if japanese else ('"', '"')
    for item in list(phrases)[:2]:
        phrase = str(item or "").strip()
        if phrase and phrase in rendered:
            rendered = rendered.replace(phrase, f"{left}{phrase}{right}", 1)
    return rendered


def _render_elevenlabs_text(
    text: str,
    prosody_plan: object,
    *,
    language_hint: str,
    voice_director_segment: Mapping[str, object] | None = None,
) -> str:
    """Render text và style tag cho ElevenLabs V3 mà không bị thì thầm kéo dài."""
    segment = voice_director_segment or {}
    style = str(
        segment.get("style")
        or getattr(prosody_plan, "style", "")
        or "neutral"
    ).strip().lower()
    body = _eleven_emphasize(
        str(text or "").strip(),
        segment.get("emphasis", ()),
        language_hint=language_hint,
    )
    body = _apply_voice_director_cue(body, segment, language_hint=language_hint)
    tags = [tag for tag in _ELEVEN_STYLE_TAGS.get(style, ()) if tag not in {"[quietly]", "[low energy]", "[controlled]"}]
    try:
        warmth = max(0.0, min(1.0, float(segment.get("warmth", 0.0) or 0.0)))
    except (TypeError, ValueError):
        warmth = 0.0
    # Với style trung tính, warmth cao được chuyển thành một chỉ dẫn duy nhất.
    # Không xếp nhiều tag vì ElevenLabs có thể diễn quá tay rồi lại thành giọng bóp.
    if warmth >= 0.56 and style in {"neutral", "focused"}:
        return f"[warmly] {body}".strip()
    if tags:
        return f"{tags[0]} {body}".strip()
    return body.strip()


def _pcm24_to_wav_bytes(pcm_data: bytes) -> bytes:
    if not pcm_data:
        return b""
    pcm = pcm_data
    if audio_polish_enabled():
        try:
            polished = polish_pcm16_mono(pcm, sample_rate=24000)
            pcm = polished.pcm or pcm
            stats = polished.stats
            if stats.trimmed_head_ms or stats.trimmed_tail_ms or stats.compressed_pause_ms:
                logger.debug(
                    "[TTS/ElevenLabs] polish %dms→%dms trim=%d/%d pause=%d",
                    stats.original_ms,
                    stats.polished_ms,
                    stats.trimmed_head_ms,
                    stats.trimmed_tail_ms,
                    stats.compressed_pause_ms,
                )
        except Exception as exc:
            logger.warning("[TTS/ElevenLabs] audio polish lỗi, dùng PCM gốc: %s", _short_error(exc))

    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(24000)
        wf.writeframes(pcm)
    return buf.getvalue()


def _mp3_bytes_to_wav_bytes(mp3_bytes: bytes) -> bytes:
    import miniaudio

    if not mp3_bytes:
        return b""
    decoded = miniaudio.decode(mp3_bytes)
    samples = getattr(decoded, "samples", b"")
    pcm = samples.tobytes() if hasattr(samples, "tobytes") else bytes(samples)
    if not pcm:
        return b""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(int(getattr(decoded, "nchannels", 2) or 2))
        wf.setsampwidth(int(getattr(decoded, "sample_width", 2) or 2))
        wf.setframerate(int(getattr(decoded, "sample_rate", 44100) or 44100))
        wf.writeframes(pcm)
    return buf.getvalue()



def _is_elevenlabs_quota_error(exc: BaseException) -> bool:
    """Nhận diện quota failure để xoay account ngay trong cùng lượt TTS."""
    text = str(exc or "").lower()
    return any(
        marker in text
        for marker in (
            "quota_exceeded",
            "exceeds your quota",
            "credits remaining",
            "character limit",
            "elevenlabs http 429",
        )
    )


def _synthesize_elevenlabs_wav(
    text: str,
    voice: str,
    model: str,
    *,
    prosody_plan: object,
    language_hint: str = "",
    voice_director_segment: Mapping[str, object] | None = None,
    cancel_event=None,
    authority_check=None,
    _excluded_pool_account_ids: set[int] | None = None,
    _rotation_depth: int = 0,
) -> bytes:
    """Gọi ElevenLabs TTS stream."""

    _raise_if_tts_cancelled(cancel_event, authority_check)

    v_str = str(voice or "").strip()
    if not v_str or v_str in {"Aoede", "Leda", "Achernar", "Zephyr", "Puck", "Fenrir", "Kore", "Charon"} or len(v_str) < 10:
        voice = _voice()
    if not str(voice or "").strip():
        raise RuntimeError("missing_elevenlabs_voice_id")

    segment = dict(voice_director_segment or {})
    rendered_text = _render_elevenlabs_text(
        text,
        prosody_plan,
        language_hint=language_hint,
        voice_director_segment=segment,
    )
    from runtime.tts_budget import GLOBAL_TTS_REQUEST_BUDGET

    GLOBAL_TTS_REQUEST_BUDGET.validate(
        transcript=text,
        prompt=rendered_text,
        style_text=json.dumps(segment, ensure_ascii=False, separators=(",", ":")),
        provider="elevenlabs",
    )

    auth_type, token, pool_account_id = _get_elevenlabs_token(
        required_characters=len(rendered_text),
        excluded_account_ids=set(_excluded_pool_account_ids or set()),
    )
    if not token:
        raise RuntimeError("Không có ElevenLabs account nào còn từ 100 quota trở lên")
    if token.startswith("eyJ"):
        auth_type = "bearer"
    elif not auth_type:
        auth_type = "xi-api-key"

    energy = _elevenlabs_float("ERIS_ELEVENLABS_DEFAULT_ENERGY", 0.42, 0.0, 1.0)
    try:
        energy = max(0.0, min(1.0, float(segment.get("energy", energy))))
    except (TypeError, ValueError):
        pass
    segment_speed = 1.0
    try:
        segment_speed = float(segment.get("speed", 1.0) or 1.0)
    except (TypeError, ValueError):
        segment_speed = 1.0
    configured_speed = _speech_speed()
    speed = _effective_elevenlabs_speed(segment_speed, configured_speed)
    logger.debug(
        "[TTS ElevenLabs] configured_speed=%.3f segment_speed=%.3f effective_speed=%.3f",
        configured_speed,
        segment_speed,
        speed,
    )

    stability_base = _elevenlabs_float("ERIS_ELEVENLABS_STABILITY", 0.45, 0.0, 1.0)
    stability = max(0.30, min(0.65, stability_base))
    similarity = _elevenlabs_float("ERIS_ELEVENLABS_SIMILARITY_BOOST", 0.78, 0.0, 1.0)
    try:
        warmth = max(0.0, min(1.0, float(segment.get("warmth", 0.62) or 0.62)))
    except (TypeError, ValueError):
        warmth = 0.62
    style_boost = _elevenlabs_float("ERIS_ELEVENLABS_STYLE", 0.08, 0.0, 1.0)
    style_boost = max(0.0, min(0.28, style_boost + energy * 0.04 + warmth * 0.10))
    # Hơi tăng stability khi cần ấm để contour mượt hơn, nhưng không khóa cứng
    # đến mức giọng phẳng và bí.
    stability = max(0.30, min(0.68, stability + max(0.0, warmth - 0.5) * 0.10))
    speaker_boost = str(os.environ.get("ERIS_ELEVENLABS_SPEAKER_BOOST", "1") or "1").strip().lower() not in {
        "0",
        "false",
        "off",
        "no",
    }

    lang = normalize_language(language_hint, text=text)
    breaker_key = (
        "elevenlabs",
        str(pool_account_id or "direct"),
        str(model or ""),
        str(voice or ""),
        _same_language_locale(lang),
    )
    permit = None
    if _tts_breaker_enabled():
        permit = GLOBAL_TTS_BREAKER.allow(breaker_key)
        if not permit.allowed:
            raise RuntimeError(f"TTS circuit breaker {permit.state.value}: {permit.reason}")

    if auth_type == "bearer":
        url = "https://api.us.elevenlabs.io/v1/text-to-dialogue/stream"
        # PCM 24 kHz đi thẳng vào pipeline hiện tại, tránh decode MP3 trung gian
        # và chuẩn bị sẵn đường cho playback streaming sau này.
        params = {"output_format": "pcm_24000"}
        headers = {
            "Accept": "application/octet-stream",
            "Content-Type": "application/json",
            "Origin": "https://elevenlabs.io",
            "Referer": "https://elevenlabs.io/",
            "User-Agent": "Mozilla/5.0",
            "authorization": f"Bearer {token}",
        }
        payload = {
            "inputs": [
                {
                    "text": rendered_text,
                    "voice_id": str(voice).strip(),
                }
            ],
            "model_id": str(model or "eleven_v3"),
            "settings": {
                "stability": round(stability, 3),
                "use_speaker_boost": speaker_boost,
            },
        }
    else:
        payload = {
            "text": rendered_text,
            "model_id": str(model or "eleven_v3"),
            "voice_settings": {
                "stability": round(stability, 3),
                "similarity_boost": round(similarity, 3),
                "style": round(style_boost, 3),
                "use_speaker_boost": speaker_boost,
                "speed": round(speed, 3),
            },
            "apply_text_normalization": "auto",
        }
        if lang in {"vi", "ja", "en"}:
            payload["language_code"] = lang
        if lang == "ja":
            payload["apply_language_text_normalization"] = str(
                os.environ.get("ERIS_ELEVENLABS_JA_TEXT_NORMALIZATION", "0") or "0"
            ).strip().lower() in {"1", "true", "yes", "on"}
        previous_text = str(segment.get("previous_text", "") or "").strip()
        next_text = str(segment.get("next_text", "") or "").strip()
        if previous_text:
            payload["previous_text"] = previous_text
        if next_text:
            payload["next_text"] = next_text

        url = f"{_elevenlabs_base_url()}/v1/text-to-speech/{str(voice).strip()}/stream"
        params = {"output_format": "pcm_24000"}
        headers = {
            "Accept": "application/octet-stream",
            "Content-Type": "application/json",
            "xi-api-key": token,
        }

    try:
        import requests
        from llm.token_budget import estimate_tokens
        from runtime.inference_broker import GLOBAL_INFERENCE_BROKER, inference_context

        timeout = _elevenlabs_timeout_seconds()
        broker_provider = (
            f"elevenlabs_account_{pool_account_id}"
            if pool_account_id > 0
            else "elevenlabs"
        )
        with inference_context(
            "tts_current_turn",
            optional=False,
            timeout_seconds=timeout,
            cancel_scope=f"tts:elevenlabs:{threading.get_ident()}",
            metadata={"voice": voice, "tts_model": model, "transport": "elevenlabs.http"},
        ):
            broker_request = GLOBAL_INFERENCE_BROKER.request_from_context(
                # Quota/cooldown phải tách theo account. Một account 429 không
                # được đóng circuit của account kế tiếp trong pool.
                provider=broker_provider,
                model=str(model or "eleven_v3"),
                estimated_input_tokens=estimate_tokens([{"role": "user", "content": rendered_text}]),
                reserved_output_tokens=768,
                timeout_seconds=timeout,
                metadata={"voice_id": str(voice), "output_format": "pcm_24000" if auth_type != "bearer" else "mp3"},
            )

            def call_tts_provider(*, cancel_token=None):
                if cancel_token is not None:
                    cancel_token.raise_if_cancelled(broker_request)
                session = _get_elevenlabs_http_session()
                post_fn = session.post
                if getattr(requests.post, "__module__", "") != "requests.api":
                    post_fn = requests.post
                with post_fn(
                    url,
                    params=params,
                    headers=headers,
                    json=payload,
                    stream=True,
                    timeout=(15.0, timeout),
                ) as response:
                    if response.status_code >= 400:
                        detail = " ".join(str(response.text or "").split())[:500]
                        quota_failure = _is_elevenlabs_quota_error(
                            RuntimeError(f"ElevenLabs HTTP {response.status_code}: {detail}")
                        )
                        if pool_account_id > 0 and quota_failure:
                            try:
                                from runtime.elevenlabs_pool_db import GLOBAL_ELEVEN_POOL
                                GLOBAL_ELEVEN_POOL.mark_exhausted(pool_account_id, 86400.0)
                                logger.warning("[TTS/ElevenLabsPool] Account %d bị hết quota/hạn ngạch; đã đánh dấu và xoay pool", pool_account_id)
                            except Exception:
                                pass
                        raise RuntimeError(f"ElevenLabs HTTP {response.status_code}: {detail}")
                    chunks: list[bytes] = []
                    started_at = time.monotonic()
                    first_chunk_ms: float | None = None
                    response_headers = getattr(response, "headers", {}) or {}
                    content_type = str(getattr(response_headers, "get", lambda *_: "")("Content-Type", "") or "").lower()
                    for chunk in response.iter_content(chunk_size=16 * 1024):
                        _raise_if_tts_cancelled(cancel_event, authority_check)
                        if cancel_token is not None:
                            cancel_token.raise_if_cancelled(broker_request)
                        if chunk:
                            if first_chunk_ms is None:
                                first_chunk_ms = (time.monotonic() - started_at) * 1000.0
                            chunks.append(chunk)
                    total_ms = (time.monotonic() - started_at) * 1000.0
                    logger.debug(
                        "[TTS/ElevenLabs] stream buffered first_byte=%.0fms total=%.0fms bytes=%d type=%s",
                        float(first_chunk_ms or total_ms), total_ms, sum(len(item) for item in chunks), content_type or "unknown",
                    )
                return b"".join(chunks), content_type

            audio_result = GLOBAL_INFERENCE_BROKER.run(broker_request, call_tts_provider)
        if isinstance(audio_result, tuple) and len(audio_result) == 2:
            pcm, response_content_type = audio_result
        else:
            pcm, response_content_type = audio_result, ""
        if not pcm:
            raise RuntimeError("ElevenLabs returned empty audio")
        if _tts_breaker_enabled():
            GLOBAL_TTS_BREAKER.record_success(breaker_key, probe_id=getattr(permit, "probe_id", ""))

        if pool_account_id > 0:
            try:
                from runtime.elevenlabs_pool_db import GLOBAL_ELEVEN_POOL
                GLOBAL_ELEVEN_POOL.record_usage(pool_account_id, len(rendered_text))
            except Exception:
                pass

        if auth_type == "bearer" and ("mpeg" in response_content_type or "mp3" in response_content_type):
            # Một số gateway bearer cũ có thể bỏ qua output_format và vẫn trả MP3.
            return _mp3_bytes_to_wav_bytes(pcm)

        return _pcm24_to_wav_bytes(pcm)
    except Exception as exc:
        if _tts_request_cancelled(cancel_event, authority_check) or str(exc) == "tts_cancelled":
            raise TtsCancelledError() from None
        if (
            pool_account_id > 0
            and _is_elevenlabs_quota_error(exc)
            and _rotation_depth < 32
        ):
            # Đây là lỗi quota của account cũ thật, không phải cancel.
            # Ghi đúng provider failure trước khi thử account kế tiếp.
            if _tts_breaker_enabled():
                GLOBAL_TTS_BREAKER.record_failure(
                    breaker_key, kind="provider_rate_limited",
                    probe_id=getattr(permit, "probe_id", ""),
                )
            excluded = set(_excluded_pool_account_ids or set())
            excluded.add(pool_account_id)
            logger.warning(
                "[TTS/ElevenLabsPool] Xoay account ngay trong cùng request; bỏ account=%d depth=%d",
                pool_account_id,
                _rotation_depth + 1,
            )
            return _synthesize_elevenlabs_wav(
                text,
                voice,
                model,
                prosody_plan=prosody_plan,
                language_hint=language_hint,
                voice_director_segment=voice_director_segment,
                cancel_event=cancel_event,
                authority_check=authority_check,
                _excluded_pool_account_ids=excluded,
                _rotation_depth=_rotation_depth + 1,
            )
        if _tts_breaker_enabled():
            GLOBAL_TTS_BREAKER.record_failure(
                breaker_key,
                kind=_tts_failure_reason(exc),
                probe_id=getattr(permit, "probe_id", ""),
            )
        raise
    finally:
        # Mọi đường return/exception/quota rotation đều kết thúc lease này.
        # Nếu đã record_success/failure, slot đã là 0 hoặc breaker đã OPEN.
        if _tts_breaker_enabled() and getattr(getattr(permit, "state", None), "value", "") == "half_open":
            GLOBAL_TTS_BREAKER.record_cancelled(breaker_key, probe_id=getattr(permit, "probe_id", ""))


# ── Gemini TTS call ───────────────────────────────────────────────────────────



def _short_error(exc: Exception, *, limit: int = 220) -> str:
    text = " ".join(str(exc or "").split())
    if len(text) > limit:
        return text[: max(0, limit - 3)].rstrip() + "..."
    return text


def _edge_voice_candidates(language_hint: str = "") -> list[str]:
    lang = normalize_language(language_hint)
    expected_prefix = {"ja": "ja-JP-", "vi": "vi-VN-", "en": "en-US-"}.get(lang, "")
    configured = {
        "ja": os.environ.get("ERIS_TTS_EDGE_JA_VOICE", "ja-JP-NanamiNeural").strip(),
        "vi": os.environ.get("ERIS_TTS_EDGE_VI_VOICE", "vi-VN-HoaiMyNeural").strip(),
        "en": os.environ.get("ERIS_TTS_EDGE_EN_VOICE", "en-US-JennyNeural").strip(),
    }.get(lang, "")
    # Config sai locale bị reject thay vì đọc tiếng Việt bằng voice Nhật rồi gọi đó
    # là fallback. Một lỗi cấu hình không cần được biến thành tính cách mới của Eris.
    if not configured or (expected_prefix and not configured.lower().startswith(expected_prefix.lower())):
        return []
    return [configured]


def _edge_romaji_enabled() -> bool:
    """Legacy romaji chỉ được bật qua authority config.

    Phase 8 khóa đường fallback này ở profile stable. Env vẫn được store đọc như
    compatibility input, nhưng TTS không tự tạo một sự thật cấu hình thứ hai.
    """
    try:
        from runtime.hardening_config import HardeningConfig

        config = HardeningConfig.load()
        return bool(config.legacy_romaji_tts and not config.stable)
    except Exception:
        return False


def _contains_japanese_chars(text: str) -> bool:
    return bool(re.search(r"[\u3040-\u30ff\u3400-\u9fff]", str(text or "")))


def _strip_tts_honorifics(text: str) -> str:
    return clean_public_addressing(str(text or ""), surface="tts")


def _ascii_for_edge(text: str) -> str:
    source = str(text or "").replace("đ", "d").replace("Đ", "D")
    normalized = unicodedata.normalize("NFKD", source)
    ascii_text = normalized.encode("ascii", "ignore").decode("ascii", "ignore")
    return " ".join(ascii_text.split()).strip()


def _clean_edge_romaji_output(text: str) -> str:
    raw = str(text or "").replace("\r\n", "\n").replace("\r", "\n")
    raw = raw.strip().strip('"').strip("'").strip()
    lines = [" ".join(line.split()).strip() for line in raw.split("\n")]
    out = " ".join(line for line in lines if line).strip()
    out = re.sub(r"^(romaji|output)\s*:\s*", "", out, flags=re.IGNORECASE).strip()
    out = re.sub(r"\[[^\]]+\]", "", out).strip()
    return out




def _edge_japanese_romaji_text(text: str) -> str:
    return ""


def _edge_text_candidates(text: str, language_hint: str = "") -> list[tuple[str, str]]:
    clean = _clean_text(text)
    if not clean:
        return []

    lang = normalize_language(language_hint)
    if lang == "ja":
        # Contract Nhật chỉ được fallback nếu transcript thật sự đã là Nhật. Nếu
        # Gemini chính định dịch trong prompt nhưng chết trước khi trả audio, Edge
        # không được gọi một LLM phụ để dịch lại. Khi đó im lặng an toàn còn hơn
        # đổi tiếng/giọng lén lút.
        if not _contains_japanese_chars(clean):
            return []
        candidates: list[tuple[str, str]] = [("raw", clean)]
        romaji = _edge_japanese_romaji_text(clean)
        if romaji:
            candidates.append(("romaji", romaji))
    else:
        candidates = [("raw", clean)]

    result: list[tuple[str, str]] = []
    seen: set[str] = set()
    for mode, candidate in candidates:
        value = str(candidate or "").strip()
        if value and value not in seen:
            seen.add(value)
            result.append((mode, value))
    return result


def _mp3_file_to_wav_bytes(path: str) -> bytes:
    import miniaudio

    with open(path, "rb") as fh:
        mp3_bytes = fh.read()
    decoded = miniaudio.decode(mp3_bytes)
    samples = getattr(decoded, "samples", b"")
    if hasattr(samples, "tobytes"):
        pcm = samples.tobytes()
    else:
        pcm = bytes(samples)

    sample_width = int(getattr(decoded, "sample_width", 2) or 2)
    nchannels = int(getattr(decoded, "nchannels", 1) or 1)
    sample_rate = int(getattr(decoded, "sample_rate", 24000) or 24000)
    if not pcm:
        return b""

    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(nchannels)
        wf.setsampwidth(sample_width)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm)
    return buf.getvalue()


async def _edge_tts_save_mp3(text: str, voice: str, path: str) -> None:
    import edge_tts

    communicate = edge_tts.Communicate(text=text, voice=voice)
    await communicate.save(path)


def _synthesize_edge_tts_wav(text: str, *, language_hint: str = "") -> tuple[bytes, str]:
    text_candidates = _edge_text_candidates(text, language_hint)
    if not text_candidates or not _edge_tts_fallback_enabled():
        return b"", ""

    errors: list[str] = []
    with tempfile.TemporaryDirectory(prefix="eris_edge_tts_") as tmpdir:
        for voice in _edge_voice_candidates(language_hint):
            for mode, candidate_text in text_candidates:
                mp3_path = os.path.join(tmpdir, f"{voice}_{mode}.mp3")
                try:
                    asyncio.run(_edge_tts_save_mp3(candidate_text, voice, mp3_path))
                    if not os.path.exists(mp3_path) or os.path.getsize(mp3_path) <= 0:
                        raise RuntimeError("Edge TTS returned an empty MP3")
                    wav_bytes = _mp3_file_to_wav_bytes(mp3_path)
                    if wav_bytes:
                        return wav_bytes, f"{voice}/{mode}"
                    raise RuntimeError("Edge TTS MP3 decode returned empty WAV")
                except Exception as exc:
                    errors.append(f"{voice}/{mode}: {_short_error(exc, limit=120)}")

    if errors:
        raise RuntimeError("; ".join(errors))
    return b"", ""


def _synthesize_windows_sapi_wav(text: str, *, language_hint: str = "") -> bytes:
    """Render WAV with built-in Windows SAPI when remote TTS quota is exhausted."""
    clean = _clean_text(text)
    if os.name != "nt" or not clean:
        return b""

    script = r"""
$ErrorActionPreference = "Stop"
Add-Type -AssemblyName System.Speech
$textPath = $env:ERIS_SAPI_TEXT_PATH
$wavPath = $env:ERIS_SAPI_WAV_PATH
$lang = ($env:ERIS_SAPI_LANGUAGE_HINT -as [string]).ToLowerInvariant()
$text = [System.IO.File]::ReadAllText($textPath, [System.Text.Encoding]::UTF8)
$speaker = New-Object System.Speech.Synthesis.SpeechSynthesizer
try {
    $target = ""
    if ($lang.StartsWith("ja") -or $lang -eq "jp" -or $lang -eq "japanese") { $target = "ja" }
    elseif ($lang.StartsWith("vi")) { $target = "vi" }
    elseif ($lang.StartsWith("en")) { $target = "en" }
    if (-not $target) { throw "unsupported_language_contract" }
    $voice = $speaker.GetInstalledVoices() |
        Where-Object { $_.Enabled -and $_.VoiceInfo.Culture.Name.ToLowerInvariant().StartsWith($target) } |
        Select-Object -First 1
    if (-not $voice) { throw "no_compatible_sapi_voice" }
    $speaker.SelectVoice($voice.VoiceInfo.Name)
    $speaker.Rate = -1
    $speaker.Volume = 100
    $speaker.SetOutputToWaveFile($wavPath)
    $speaker.Speak($text) | Out-Null
}
finally {
    $speaker.Dispose()
}
"""
    with tempfile.TemporaryDirectory(prefix="eris_tts_") as tmpdir:
        text_path = os.path.join(tmpdir, "input.txt")
        wav_path = os.path.join(tmpdir, "fallback.wav")
        with open(text_path, "w", encoding="utf-8") as fh:
            fh.write(clean)
        env = dict(os.environ)
        env["ERIS_SAPI_TEXT_PATH"] = text_path
        env["ERIS_SAPI_WAV_PATH"] = wav_path
        env["ERIS_SAPI_LANGUAGE_HINT"] = str(language_hint or "")
        completed = subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
            env=env,
            timeout=20,
        )
        if completed.returncode != 0:
            raise RuntimeError((completed.stderr or "Windows SAPI fallback failed").strip())
        with open(wav_path, "rb") as fh:
            return fh.read()


def _build_local_fallback_chain():
    """Dựng provider chain Edge/SAPI mà chưa gọi network hay PowerShell."""
    from runtime.speech_provider_chain import (
        CallableSpeechProvider,
        SameLanguageSpeechFallbackChain,
        SpeechFallbackRequest,
    )

    def edge_synthesize(request: SpeechFallbackRequest):
        wav_bytes, voice = _synthesize_edge_tts_wav(request.text, language_hint=request.language)
        voice_name = str(voice or "").strip()
        voice_parts = voice_name.split("-", 2)
        actual_locale = "-".join(voice_parts[:2]) if len(voice_parts) >= 2 else request.locale
        return wav_bytes, {"voice": voice_name, "locale": actual_locale}

    def sapi_synthesize(request: SpeechFallbackRequest):
        wav_bytes = _synthesize_windows_sapi_wav(request.text, language_hint=request.language)
        return wav_bytes, {"voice": "windows-sapi", "locale": request.locale}

    return SameLanguageSpeechFallbackChain((
        CallableSpeechProvider("edge", edge_synthesize, enabled_fn=_edge_tts_fallback_enabled),
        CallableSpeechProvider("sapi", sapi_synthesize, enabled_fn=_sapi_tts_fallback_enabled),
    ))


def _try_local_tts_fallback(exc: Exception, *, fallback_text: str = "", language_hint: str = "") -> bytes:
    """Fallback qua provider chain, luôn giữ nguyên ngôn ngữ của turn.

    Edge và SAPI giờ là hai provider tách biệt. Router gọi mỗi provider tối đa một
    lần, kiểm locale trước khi nhận audio và tự ghi metric từng attempt. Engine
    không còn giữ hai khối retry lồng nhau, vốn là cách rất hiệu quả để giấu lỗi
    contract dưới một núi ``try/except``.
    """
    if not _local_tts_fallback_enabled() or not str(fallback_text or "").strip():
        return b""
    try:
        from runtime.rollout_control import feature_enabled
        pin_enabled = feature_enabled("tts_language_pin")
    except Exception:
        pin_enabled = str(os.environ.get("ERIS_TTS_LANGUAGE_PIN", "1") or "1").strip().lower() not in {"0", "false", "off", "no"}
    if not pin_enabled:
        language_hint = normalize_language("", text=fallback_text)
    language = normalize_language(language_hint, text=fallback_text)
    expected_locale = _same_language_locale(language)

    from runtime.speech_provider_chain import SpeechFallbackRequest

    chain = _build_local_fallback_chain()
    outcome = chain.synthesize(SpeechFallbackRequest(
        text=fallback_text,
        language=language,
        locale=expected_locale,
        voice_family=str(os.environ.get("ERIS_TTS_VOICE_FAMILY", "") or ""),
        cause=type(exc).__name__,
    ))
    if outcome.success:
        logger.warning(
            "[TTS] Provider chinh loi/quota (%s). Dung fallback=%s cung ngon ngu locale=%s voice=%s.",
            _short_error(exc), outcome.provider, outcome.locale, outcome.voice or "<default>",
        )
        return outcome.audio
    attempts = ", ".join(
        f"{item.provider}:{item.error_kind or 'failed'}" for item in outcome.attempts
    ) or "no_provider"
    logger.warning("[TTS] Khong co fallback cung ngon ngu kha dung: %s", attempts)
    return b""




def _tts_breaker_enabled() -> bool:
    try:
        from runtime.rollout_control import feature_enabled
        return feature_enabled("tts_breaker")
    except Exception:
        return os.environ.get("ERIS_TTS_BREAKER_ENABLED", "1").strip().lower() not in {"0", "false", "off", "no"}


def _tts_max_attempts() -> int:
    # TTS là lane public của lượt hiện tại nên được xoay qua vài key khác nhau khi
    # một key dính 429. Vẫn không mặc định quét sạch toàn pool; nếu cần probe mạnh,
    # đặt ERIS_TTS_MAX_ATTEMPTS=pool/all/full.
    raw = str(os.environ.get("ERIS_TTS_MAX_ATTEMPTS", "4") or "4").strip().lower()
    if raw in {"", "full", "all", "pool", "max"}:
        return 10_000
    try:
        return max(1, int(raw))
    except (TypeError, ValueError):
        return 10_000


def _tts_broker_timeout_seconds() -> float:
    raw = str(os.environ.get("ERIS_TTS_BROKER_TIMEOUT_SEC", "45") or "45").strip()
    try:
        return max(5.0, min(120.0, float(raw)))
    except ValueError:
        return 45.0


def _tts_broker_provider_name(lease=None) -> str:
    index = getattr(lease, "index", None)
    if index is None:
        return "gemini_tts"
    try:
        return f"gemini_tts_key_{int(index)}"
    except (TypeError, ValueError):
        return "gemini_tts"


def _same_language_locale(language_hint: str, locale: str = "") -> str:
    if str(locale or "").strip():
        return str(locale).strip()
    lang = normalize_language(language_hint)
    return {"vi": "vi-VN", "ja": "ja-JP", "en": "en-US"}.get(lang, lang)


def _tts_failure_reason(exc: Exception) -> str:
    """Chuẩn hóa lý do lỗi để log không đổ oan cho độ dài transcript."""

    text = " ".join(str(exc or "").lower().split())
    if "provider_hot_optional" in text:
        return "provider_hot"
    if "429" in text or "resource_exhausted" in text or "rate limit" in text:
        return "provider_rate_limited"
    if "transcript quá dài" in text or "transcript too long" in text:
        return "transcript_too_long"
    if "prompt vượt hard limit" in text or "prompt" in text and "hard limit" in text:
        return "prompt_too_long"
    if "fallback" in text:
        return "fallback_unavailable"
    return "provider_error"

class TtsCancelledError(RuntimeError):
    """Request bị mất quyền phát, KHÔNG phải lỗi nhà cung cấp TTS.

    Giữ message cũ để tương thích các backend/TtsTaskManager vẫn so str(exc),
    đồng thời cung cấp loại exception riêng cho code và regression mới.
    """
    def __init__(self) -> None:
        super().__init__("tts_cancelled")


def _tts_request_cancelled(cancel_event=None, authority_check=None) -> bool:
    if cancel_event is not None:
        try:
            if cancel_event.is_set():
                return True
        except (AttributeError, TypeError) as exc:
            logger.debug("[TTS] cancel event check failed: %s", _short_error(exc))
    if callable(authority_check):
        try:
            if not bool(authority_check()):
                return True
        except Exception:
            return True
    return False


def _raise_if_tts_cancelled(cancel_event=None, authority_check=None) -> None:
    if _tts_request_cancelled(cancel_event, authority_check):
        raise TtsCancelledError()


def _cancel_aware_sleep(seconds: float, cancel_event=None, authority_check=None) -> None:
    delay = max(0.0, float(seconds or 0.0))
    if delay <= 0:
        _raise_if_tts_cancelled(cancel_event, authority_check)
        return
    if cancel_event is not None and callable(getattr(cancel_event, "wait", None)):
        if cancel_event.wait(delay):
            raise TtsCancelledError()
    else:
        time.sleep(delay)
    _raise_if_tts_cancelled(cancel_event, authority_check)


def _synthesize_wav(prompt: str, voice: str, model: str, **kwargs) -> bytes:
    raise RuntimeError("Gemini audio provider is retired from Eris runtime.")


def _invoke_synthesize_wav(
    prompt: str,
    voice: str,
    model: str,
    **kwargs,
) -> bytes:
    """Gọi synth với contract mới nhưng vẫn tương thích adapter/mock cũ.

    Kiểm tra chữ ký trước khi gọi, không bắt bừa ``TypeError`` phát sinh bên
    trong provider. Nhờ vậy backend cũ ba tham số vẫn sống trong giai đoạn migration.
    """
    try:
        params = tuple(inspect.signature(_synthesize_wav).parameters.values())
        accepts_kwargs = any(p.kind == inspect.Parameter.VAR_KEYWORD for p in params)
        names = {p.name for p in params}
    except (TypeError, ValueError):
        accepts_kwargs = True
        names = set(kwargs)
    supported = kwargs if accepts_kwargs else {key: value for key, value in kwargs.items() if key in names}
    return _synthesize_wav(prompt, voice, model, **supported)


def _synthesize_active_provider_wav(
    text: str, *, voice: str, model: str, language_hint: str, locale: str,
    prosody_plan: object, prelocalized: bool,
    voice_director_segment: Mapping[str, object] | None = None,
    cancel_event=None, authority_check=None,
) -> bytes:
    """Synthesize with ElevenLabs, then the existing local fallback if available."""
    try:
        return _synthesize_elevenlabs_wav(
            text, voice, model, prosody_plan=prosody_plan, language_hint=language_hint,
            voice_director_segment=voice_director_segment,
            cancel_event=cancel_event, authority_check=authority_check,
        )
    except Exception as exc:
        if str(exc) == "tts_cancelled":
            raise
        logger.warning("[TTS/ElevenLabs] provider error: %s", _short_error(exc))
        local = _try_local_tts_fallback(exc, fallback_text=text, language_hint=language_hint)
        if local:
            return local
        raise


def _concat_wav_bytes(*items: bytes) -> bytes:
    """Ghép nhiều WAV PCM cùng format thành một WAV.

    Dùng cho vocal prefix cache: prefix và câu chính được synth riêng, nhưng phát
    ra như một audio duy nhất để tránh gap do mở playback hai lần.
    """
    chunks = [item for item in items if item]
    if not chunks:
        return b""
    if len(chunks) == 1:
        return chunks[0]

    params = None
    frames: list[bytes] = []
    for wav_bytes in chunks:
        try:
            with wave.open(io.BytesIO(wav_bytes), "rb") as wf:
                current = wf.getparams()
                if params is None:
                    params = current
                elif (
                    current.nchannels != params.nchannels
                    or current.sampwidth != params.sampwidth
                    or current.framerate != params.framerate
                ):
                    return chunks[-1]
                frames.append(wf.readframes(wf.getnframes()))
        except Exception:
            return chunks[-1]
    if params is None:
        return b""
    out = io.BytesIO()
    with wave.open(out, "wb") as wf:
        wf.setnchannels(params.nchannels)
        wf.setsampwidth(params.sampwidth)
        wf.setframerate(params.framerate)
        wf.writeframes(b"".join(frames))
    return out.getvalue()


def _wav_duration_seconds(wav_bytes: bytes) -> float:
    try:
        with wave.open(io.BytesIO(wav_bytes), "rb") as wf:
            framerate = max(1, int(wf.getframerate() or 24000))
            frames = max(0, int(wf.getnframes() or 0))
            return frames / float(framerate)
    except Exception:
        return 0.0


def _apply_output_volume(wav_bytes: bytes) -> bytes:
    """Scale PCM16 volume before playback, without depending on audioop/numpy."""

    volume = _output_volume()
    if not wav_bytes or abs(volume - 1.0) < 0.001:
        return wav_bytes
    try:
        with wave.open(io.BytesIO(wav_bytes), "rb") as wf:
            params = wf.getparams()
            frames = wf.readframes(wf.getnframes())
        if params.sampwidth != 2:
            return wav_bytes

        sample_count = len(frames) // 2
        samples = struct.unpack("<" + "h" * sample_count, frames)
        scaled = bytearray(len(frames))
        for index, sample in enumerate(samples):
            value = max(-32768, min(32767, int(sample * volume)))
            struct.pack_into("<h", scaled, index * 2, value)

        out = io.BytesIO()
        with wave.open(out, "wb") as wf:
            wf.setparams(params)
            wf.writeframes(bytes(scaled))
        return out.getvalue()
    except Exception as exc:
        logger.debug("[TTS] volume transform skipped: %s", exc)
        return wav_bytes


def _notify_playback_started(callback) -> None:
    if callback is None:
        return
    try:
        callback()
    except Exception as exc:
        logger.debug("[TTS] playback start callback failed: %s", exc)


def _play_wav_winsound(
    wav_bytes: bytes,
    stop_event: threading.Event,
    on_started=None,
) -> bool:
    if stop_event.is_set() or not wav_bytes:
        return True
    if os.name != "nt":
        return False
    try:
        import winsound
        import time

        duration = max(0.05, _wav_duration_seconds(wav_bytes))
        winsound.PlaySound(wav_bytes, winsound.SND_MEMORY | winsound.SND_ASYNC | winsound.SND_NODEFAULT)
        _notify_playback_started(on_started)
        started_at = time.time()
        while (time.time() - started_at) < duration:
            if stop_event.is_set():
                winsound.PlaySound(None, 0)
                return True
            time.sleep(0.02)
        return True
    except Exception as exc:
        logger.debug("[TTS] winsound playback unavailable: %s", exc)
        return False


def _play_wav_bytes(
    wav_bytes: bytes, stop_event: threading.Event, on_started=None, *, volume_applied: bool = False,
) -> None:
    """Phát WAV bytes qua backend phù hợp với đúng sample_rate và channel count."""
    if not volume_applied:
        wav_bytes = _apply_output_volume(wav_bytes)
    if not wav_bytes or stop_event.is_set():
        return

    started = False

    def _mark_started() -> None:
        nonlocal started
        if started or stop_event.is_set():
            return
        started = True
        _notify_playback_started(on_started)

    try:
        import sounddevice as sd
        import numpy as np

        buf = io.BytesIO(wav_bytes)
        with wave.open(buf, "rb") as wf:
            sr = wf.getframerate()
            channels = wf.getnchannels()
            sampwidth = wf.getsampwidth()
            frames = wf.readframes(wf.getnframes())

        dtype = np.int16 if sampwidth == 2 else np.int32
        arr = np.frombuffer(frames, dtype=dtype).astype(np.float32)
        arr /= float(2 ** (sampwidth * 8 - 1))
        if channels > 1:
            arr = arr.reshape(-1, channels)

        chunk_size = max(1, sr // 50)  # 20ms keeps submission/cancellation responsive.
        idx = 0
        with sd.OutputStream(samplerate=sr, channels=channels, dtype="float32") as stream:
            while idx < len(arr) and not stop_event.is_set():
                end = min(idx + chunk_size, len(arr))
                stream.write(arr[idx:end])
                idx = end
                if not started and not stop_event.is_set():
                    _mark_started()
            if stop_event.is_set():
                stream.abort()
        return
    except Exception as exc:
        if started:
            # A device failure after submission must not replay the clip elsewhere.
            raise
        logger.debug("[TTS] sounddevice playback unavailable: %s", exc)

    if stop_event.is_set():
        return
    if _play_wav_winsound(wav_bytes, stop_event, on_started=_mark_started):
        return
    if started:
        raise RuntimeError("playback_failed_after_start")

    _play_wav_miniaudio(wav_bytes, stop_event, on_started=_mark_started)


def _play_wav_miniaudio(wav_bytes: bytes, stop_event: threading.Event, on_started=None) -> None:
    """Phát WAV qua miniaudio với đúng sample_rate và nchannels từ file."""
    import miniaudio

    if not wav_bytes or stop_event.is_set():
        return

    try:
        decoded = miniaudio.decode(wav_bytes)
        nchannels = int(getattr(decoded, "nchannels", 2) or 2)
        sample_rate = int(getattr(decoded, "sample_rate", 44100) or 44100)
        data = bytes(getattr(decoded, "samples", b""))
    except Exception as exc:
        logger.warning("[TTS] miniaudio decode failed: %s", exc)
        return

    if not data:
        return

    chunk = 4096

    def _pcm_stream():
        step = chunk * nchannels * 2
        for i in range(0, len(data), step):
            if stop_event.is_set():
                return
            yield data[i : i + step]

    device = miniaudio.PlaybackDevice(
        output_format=miniaudio.SampleFormat.SIGNED16,
        nchannels=nchannels,
        sample_rate=sample_rate,
    )
    stream = miniaudio.stream_with_callbacks(
        _pcm_stream(),
        playback_format=miniaudio.SampleFormat.SIGNED16,
        nchannels=nchannels,
        sample_rate=sample_rate,
    )
    with device:
        if stop_event.is_set():
            return
        device.start(stream)
        if not stop_event.is_set():
            _notify_playback_started(on_started)
        import time
        while device.is_active() and not stop_event.is_set():
            time.sleep(0.02)


def _translate_to_japanese(text: str, api_key: str) -> str:
    """Legacy entry point delegates to the DeepSeek transcript localizer."""
    return SpeechLocalizer().localize(text, language_hint="ja", strict=True)


# ── Worker thread ─────────────────────────────────────────────────────────────

class _SpeakRequest:
    __slots__ = (
        "text",
        "valence",
        "arousal",
        "stance",
        "on_start_play",
        "prelocalized",
        "verified_transcript_hash",
        "prosody_style",
        "prosody_notes",
        "voice_director_segment",
        "turn_id",
        "segment_id",
        "interruptible",
        "vocal_cache_kind",
        "vocal_prefix",
        "language_hint",
        "voice_family",
        "locale",
        "on_event",
        "speech_epoch",
    )

    def __init__(
        self,
        text: str,
        valence: float = 0.0,
        arousal: float = 0.3,
        stance: str = "",
        on_start_play = None,
        prelocalized: bool = False,
        verified_transcript_hash: str = "",
        prosody_style: str = "",
        prosody_notes: str = "",
        voice_director_segment: Mapping[str, object] | None = None,
        turn_id: str = "",
        segment_id: int = 0,
        interruptible: bool = True,
        vocal_cache_kind: str = "",
        vocal_prefix: str = "",
        language_hint: str = "",
        voice_family: str = "",
        locale: str = "",
        on_event = None,
        speech_epoch: int = 0,
    ):
        self.text = text
        self.valence = valence
        self.arousal = arousal
        self.stance = stance
        self.on_start_play = on_start_play
        # True khi StageFrame đã chuẩn bị sẵn tts_text theo ngôn ngữ cần đọc.
        # Khi đó engine không dịch lại, tránh "dịch một câu Nhật sang Nhật" làm mất tự nhiên.
        self.prelocalized = bool(prelocalized)
        self.verified_transcript_hash = str(verified_transcript_hash or '')
        self.prosody_style = str(prosody_style or "")
        self.prosody_notes = str(prosody_notes or "")
        self.voice_director_segment = dict(voice_director_segment or {})
        self.turn_id = str(turn_id or "")
        try:
            self.segment_id = int(segment_id)
        except (TypeError, ValueError):
            self.segment_id = 0
        self.interruptible = bool(interruptible)
        self.vocal_cache_kind = str(vocal_cache_kind or "")
        self.vocal_prefix = str(vocal_prefix or "")
        self.language_hint = str(language_hint or "")
        self.voice_family = str(voice_family or "")
        self.locale = str(locale or "")
        self.on_event = on_event
        try:
            self.speech_epoch = int(speech_epoch)
        except (TypeError, ValueError):
            self.speech_epoch = 0


class _TtsWorker:
    def __init__(
        self,
        voice: str,
        model: str,
        language_hint: str,
        *,
        epoch_validator = None,
    ) -> None:
        self._voice = voice
        self._model = model
        self._language_hint = language_hint
        self._localizer = SpeechLocalizer()
        self._prosody = ProsodyDirector()
        self._vocal_cache = VocalCache()
        self._epoch_validator = epoch_validator
        if _tts_provider() == "elevenlabs":
            warmup_elevenlabs()
        self._queue: queue.Queue[Optional[_SpeakRequest]] = queue.Queue(maxsize=_queue_size())
        self._stop_event = threading.Event()
        self._current_stop = threading.Event()
        self._current_req: Optional[_SpeakRequest] = None
        self._current_lock = threading.RLock()
        self._thread = threading.Thread(target=self._run, daemon=True, name="TTS-Worker")
        self._thread.start()


    def _request_is_current(self, req: _SpeakRequest) -> bool:
        stopped = getattr(self, "_stop_event", None)
        if stopped is not None and stopped.is_set():
            return False
        if req.verified_transcript_hash and req.language_hint:
            configured = _normalize_language_hint(self._language_hint)
            if _normalize_language_hint(req.language_hint) != configured:
                return False
        validator = self._epoch_validator
        if not callable(validator):
            return True
        try:
            return bool(validator(req.speech_epoch, req.turn_id))
        except Exception:
            # Validator là lớp hardening. Nếu chính nó hỏng, fail closed để audio
            # cũ không bò về phát sau khi hội thoại đã chuyển lượt.
            return False

    def speak(self, req: _SpeakRequest, *, replace: bool = True) -> None:
        """Enqueue một đoạn TTS.

        ``replace=True`` giữ hành vi cũ: câu mới cắt câu đang nói. Khi sau này
        StageDispatcher tách một lượt thành nhiều segment, caller có thể dùng
        ``replace=False`` để phát tuần tự segment 0, 1, 2.
        """
        if replace:
            self._current_stop.set()
            self._drain_pending(reason="replaced")
        try:
            self._queue.put_nowait(req)
        except queue.Full:
            if replace:
                # A genuinely newer turn may replace stale pending speech.
                self._drop_one_pending(reason="queue_full_replaced")
                try:
                    self._queue.put_nowait(req)
                except queue.Full:
                    self._emit(req, "audio_failed", reason="queue_full", error="TTS queue is full")
                    return
            else:
                # Never evict an earlier segment from the SAME turn to make room
                # for a later segment. Preserve ordering and fail the newest
                # append instead; normal conversation should never reach this
                # branch because TaskManager owns segmented turns.
                self._emit(req, "audio_failed", reason="queue_full_append", error="TTS queue is full")
                return
        self._emit(req, "audio_queued")

    def shutdown(self) -> None:
        self._stop_event.set()
        self._current_stop.set()
        try:
            self._queue.put_nowait(None)
        except queue.Full:
            pass

    def _emit(
        self, req: Optional[_SpeakRequest], event_type: str, *, reason: str = "", error: str = "",
        audio_envelope: tuple[float, ...] = (), audio_frame_ms: int = 40, audio_duration_ms: int = 0,
        audio_mouth_shape: tuple[float, ...] = (),
    ) -> None:
        if req is None:
            return
        event = TtsPlaybackEvent(
            type=event_type,
            turn_id=req.turn_id,
            segment_id=req.segment_id,
            text=req.text,
            reason=reason,
            error=error,
            audio_envelope=audio_envelope,
            audio_frame_ms=audio_frame_ms,
            audio_duration_ms=audio_duration_ms,
            audio_mouth_shape=audio_mouth_shape,
        )
        callback = getattr(req, "on_event", None)
        if callback is not None:
            try:
                callback(event)
            except Exception as cb_exc:
                logger.error("[TTS] Error in on_event callback: %s", cb_exc)

    def _drop_one_pending(self, *, reason: str) -> None:
        try:
            old = self._queue.get_nowait()
        except queue.Empty:
            return
        if old is not None:
            self._emit(old, "audio_cancelled", reason=reason)

    def _drain_pending(self, *, reason: str) -> None:
        while True:
            try:
                old = self._queue.get_nowait()
            except queue.Empty:
                return
            if old is not None:
                self._emit(old, "audio_cancelled", reason=reason)

    def cancel_current(self, *, reason: str = "cancelled", only_interruptible: bool = True) -> bool:
        """Dừng đoạn TTS đang chạy và bỏ các đoạn đang chờ.

        Coordinator gọi hàm này khi user chen vào. Worker vẫn tự emit
        ``audio_cancelled`` ở vòng phát hiện tại; hàm này chỉ bật cờ dừng thật
        nhanh, để loa không tiếp tục đọc như máy sau khi lượt đã đổi sang user.
        """
        with self._current_lock:
            current = self._current_req
            if current is None:
                self._drain_pending(reason=reason)
                return False
            if only_interruptible and not bool(getattr(current, "interruptible", True)):
                return False
            self._current_stop.set()
        self._drain_pending(reason=reason)
        return True

    def _request_language(self, req: _SpeakRequest) -> str:
        configured = _normalize_language_hint(self._language_hint)
        # Contract của từng turn có quyền cao hơn cấu hình worker. Voice/provider
        # không được tự dịch transcript sang ngôn ngữ khác. ``configured`` chỉ là
        # fallback cho preview hoặc request legacy không mang speech contract.
        return _normalize_language_hint(req.language_hint or configured or "vi")

    def _request_voice(self, req: _SpeakRequest) -> str:
        if _tts_provider() == "elevenlabs":
            return _voice(self._request_language(req))
        requested = str(req.voice_family or "").strip()
        if not requested or requested.lower() in {"eris_primary", "primary", "default"}:
            return self._voice
        return requested

    def _request_locale(self, req: _SpeakRequest) -> str:
        language = self._request_language(req)
        return _same_language_locale(language, req.locale)

    def _resolve_main_transcript(
        self,
        req: _SpeakRequest,
        main_text_raw: str,
        *,
        cancel_event=None,
        authority_check=None,
    ) -> tuple[str, bool, str]:
        """Khóa transcript theo language contract trước khi gọi provider audio.

        ``prelocalized=True`` nghĩa là canonical UtteranceSpec *tuyên bố* đã chốt
        speech surface. TTS tin cờ đó chỉ khi transcript thực sự khớp language
        contract. Nếu contract là Nhật nhưng transcript không có kana/kanji, đây
        là lỗi invariant upstream: engine phải repair bằng localizer (hoặc câm khi
        repair thất bại), tuyệt đối không được hạ ``ja -> vi`` rồi phát sai giọng.
        """

        language_hint = self._request_language(req)
        text_to_speak = str(main_text_raw or "").strip()
        if req.verified_transcript_hash:
            import hashlib
            if hashlib.sha256(_clean_text(text_to_speak).encode('utf-8')).hexdigest() != req.verified_transcript_hash:
                raise SpeechLocalizationError('v03_verified_tts_transcript_mismatch')
            if language_hint == 'ja' and not _contains_japanese_chars(text_to_speak):
                raise SpeechLocalizationError('v03_verified_ja_transcript_invalid')
            # Không một localizer hay language repair nào được sửa bản đã pass.
            return text_to_speak, True, language_hint
        cleaned_main = _clean_text(text_to_speak)
        if not cleaned_main and text_to_speak:
            text_to_speak = "Mình gửi code ở đây nhé." if language_hint in ("vi", "vi-vn") else "コードを書いておいたよ。"
        elif cleaned_main:
            text_to_speak = cleaned_main
        if language_hint != "ja" or not text_to_speak:
            return text_to_speak, bool(req.prelocalized), language_hint
        if _contains_japanese_chars(text_to_speak):
            return text_to_speak, True, "ja"
        repairing_invalid_prelocalized = bool(req.prelocalized)
        if repairing_invalid_prelocalized:
            # Đây là lớp chặn cuối cho bug "Vietnamese transcript wearing a ja
            # label". Không fail-open sang tiếng nguồn ở nhánh này: nếu upstream
            # đã tuyên bố canonical Japanese mà nội dung không phải Japanese thì
            # đọc nguyên văn sẽ vi phạm chính voice contract người dùng đang nghe.
            logger.warning(
                "[TTS] prelocalized transcript không khớp contract ja; "
                "repair bằng Japanese localizer trước khi phát."
            )
        if not _prelocalize_japanese_enabled():
            if _speech_contract_fail_open_enabled() and not repairing_invalid_prelocalized:
                logger.warning("[TTS] JP localization disabled; audio degraded to source transcript.")
                return text_to_speak, bool(req.prelocalized), _fallback_language_for_transcript(text_to_speak)
            logger.warning(
                "[TTS] JP localization disabled; audio fail-closed for Japanese contract%s.",
                " (invalid prelocalized surface)" if repairing_invalid_prelocalized else "",
            )
            raise SpeechLocalizationError(
                "japanese_speech_contract_unavailable:invalid_prelocalized_surface"
                if repairing_invalid_prelocalized
                else "japanese_speech_contract_unavailable:localizer_disabled"
            )

        source_text = text_to_speak
        try:
            text_to_speak = self._localizer.localize(
                source_text,
                language_hint="ja",
                prosody_style=req.prosody_style,
                strict=True,
                cancel_event=cancel_event,
                authority_check=authority_check,
            )
        except SpeechLocalizationCancelled:
            raise
        except Exception as exc:
            if _speech_contract_fail_open_enabled() and not repairing_invalid_prelocalized:
                logger.warning(
                    "[TTS] JP localization lỗi (%s); audio degraded to source transcript.",
                    _short_error(exc),
                )
                return source_text, False, _fallback_language_for_transcript(source_text)
            logger.warning(
                "[TTS] JP localization lỗi (%s); audio fail-closed, giữ display transcript.",
                _short_error(exc),
            )
            if isinstance(exc, SpeechLocalizationError):
                raise
            raise SpeechLocalizationError(
                f"japanese_speech_contract_unavailable:{type(exc).__name__}"
            ) from exc
        if not _contains_japanese_chars(text_to_speak):
            if _speech_contract_fail_open_enabled() and not repairing_invalid_prelocalized:
                logger.warning(
                    "[TTS] JP localizer trả transcript không phải tiếng Nhật; audio degraded to source transcript."
                )
                return source_text, False, _fallback_language_for_transcript(source_text)
            logger.warning(
                "[TTS] JP localizer trả transcript không phải tiếng Nhật; audio fail-closed."
            )
            raise SpeechLocalizationError("japanese_speech_contract_unavailable:non_japanese_output")
        return text_to_speak, True, "ja"

    def _vocal_prefix_wav(
        self, req: _SpeakRequest, prefix: str, prosody_plan,
        *, cancel_event=None, authority_check=None,
    ) -> bytes:
        """Lấy/sinh WAV cho prefix nghe được như "Hah... haha.".

        Prefix cache dùng prompt riêng, rất ngắn. Nếu cache miss thì sinh một lần
        rồi lưu. Nếu lỗi cache/synth, trả b"" để câu chính vẫn được đọc bình thường.
        """
        prefix = str(prefix or "").strip()
        if not prefix or not vocal_cache_enabled():
            return b""
        language_hint = self._request_language(req)
        # A Japanese embellishment is not permission to switch a Vietnamese
        # utterance to Japanese, including when the old clip is already cached.
        if language_hint != "ja" and _contains_japanese_chars(prefix):
            return b""
        kind = str(getattr(req, "vocal_cache_kind", "") or "vocal_prefix")
        if authority_check is None:
            authority_check = lambda: self._request_is_current(req)
        _raise_if_tts_cancelled(cancel_event, authority_check)
        voice = self._request_voice(req)
        locale = self._request_locale(req)
        if _contains_japanese_chars(prefix):
            language_hint = "ja"
            locale = "ja-JP"
        key = VocalCacheKey(
            voice=voice,
            model=self._model,
            kind=kind,
            style=req.prosody_style or getattr(prosody_plan, "style", "") or "neutral",
            text=prefix,
            language_hint=language_hint,
        )
        cached = self._vocal_cache.get(key)
        if cached:
            logger.debug("[TTS] Vocal cache hit kind=%s text=%r", kind, prefix)
            return cached

        try:
            prefix_plan = self._prosody.plan(
                prefix,
                stance=req.stance,
                valence=req.valence,
                arousal=req.arousal,
                style_hint=req.prosody_style or getattr(prosody_plan, "style", ""),
                notes_hint=(
                    "Render only this short nonverbal vocalization. "
                    "Do not add words before or after it."
                ),
            )
            # Prefix Nhật/tiếng đệm đi qua cùng provider để giữ cùng timbre.
            # Không gắn emphasis của câu chính vào tiếng đệm, vì "ふふ" không cần
            # một cuộc họp chiến lược về trọng âm.
            prefix_segment = {
                "style": req.prosody_style or getattr(prefix_plan, "style", "warm_soft"),
                "energy": min(0.52, float(getattr(req, "arousal", 0.3) or 0.3)),
                "speed": 0.96,
                "pitch": "slightly_low",
                "emphasis": [],
            }
            wav_bytes = _synthesize_active_provider_wav(
                prefix,
                voice=voice,
                model=self._model,
                language_hint=language_hint,
                locale=locale,
                prosody_plan=prefix_plan,
                prelocalized=True,
                voice_director_segment=prefix_segment,
                cancel_event=cancel_event,
                authority_check=authority_check,
            )
            self._vocal_cache.put(key, wav_bytes)
            logger.debug("[TTS] Vocal cache stored kind=%s text=%r", kind, prefix)
            return wav_bytes
        except Exception as exc:
            logger.debug("[TTS] Vocal cache synth miss failed: %s", exc)
            return b""

    def synthesize(self, req: _SpeakRequest, *, cancel_event=None) -> bytes:
        """Sinh WAV cho một request nhưng chưa phát ra loa.

        Đây là cửa backend cho ``TtsTaskManager``: nhiều segment có thể synth song
        song, còn manager chịu trách nhiệm phát theo đúng ``segment_id``. Worker cũ
        vẫn dùng đường ``speak`` tuần tự để tương thích với code hiện có.
        """

        authority_check = lambda: self._request_is_current(req)
        _raise_if_tts_cancelled(cancel_event, authority_check)
        language_hint = self._request_language(req)
        voice = self._request_voice(req)
        locale = self._request_locale(req)
        audio_tag = _emotion_to_tag(req.valence, req.arousal, req.stance)
        vocal_prefix = str(getattr(req, "vocal_prefix", "") or "").strip()
        main_text_raw = strip_audible_prefix(req.text, vocal_prefix) if vocal_prefix else req.text
        prefix_is_separate = bool(vocal_prefix and (main_text_raw != req.text or req.voice_director_segment))
        text_to_speak, localized, effective_language = self._resolve_main_transcript(
            req,
            main_text_raw,
            cancel_event=cancel_event,
            authority_check=authority_check,
        )
        if effective_language != language_hint:
            language_hint = effective_language
            locale = _same_language_locale(language_hint)
        prosody_plan = self._prosody.plan(
            text_to_speak,
            stance=req.stance,
            valence=req.valence,
            arousal=req.arousal,
            fallback_tag=audio_tag,
            notes_hint=req.prosody_notes,
            style_hint=req.prosody_style,
        )
        main_wav = b""
        if str(text_to_speak or "").strip():
            main_wav = _synthesize_active_provider_wav(
                text_to_speak,
                voice=voice,
                model=self._model,
                language_hint=language_hint,
                locale=locale,
                prosody_plan=prosody_plan,
                prelocalized=localized or req.prelocalized,
                voice_director_segment=req.voice_director_segment,
                cancel_event=cancel_event,
                authority_check=authority_check,
            )

        logger.debug(
            "[TTS] valence=%.2f arousal=%.2f stance=%r → style=%r tag=%r",
            req.valence, req.arousal, req.stance, prosody_plan.style, prosody_plan.opening_tag,
        )

        prefix_wav = b""
        if prefix_is_separate:
            prefix_wav = self._vocal_prefix_wav(
                req, vocal_prefix, prosody_plan,
                cancel_event=cancel_event, authority_check=authority_check,
            )
        _raise_if_tts_cancelled(cancel_event, authority_check)
        return _concat_wav_bytes(prefix_wav, main_wav) or main_wav or prefix_wav

    def _run(self) -> None:
        while not self._stop_event.is_set():
            try:
                req = self._queue.get(timeout=1.0)
            except queue.Empty:
                continue
            if req is None:
                break

            self._current_stop.clear()
            with self._current_lock:
                self._current_req = req
            callback_called = False
            speech_contract_failed = False
            try:
                try:
                    if not self._request_is_current(req):
                        self._emit(req, "audio_cancelled", reason="stale_turn_before_synthesis")
                        continue
                    language_hint = self._request_language(req)
                    voice = self._request_voice(req)
                    locale = self._request_locale(req)
                    audio_tag = _emotion_to_tag(req.valence, req.arousal, req.stance)
                    vocal_prefix = str(getattr(req, "vocal_prefix", "") or "").strip()
                    main_text_raw = strip_audible_prefix(req.text, vocal_prefix) if vocal_prefix else req.text
                    prefix_is_separate = bool(vocal_prefix and (main_text_raw != req.text or req.voice_director_segment))
                    text_to_speak, localized, effective_language = self._resolve_main_transcript(
                        req,
                        main_text_raw,
                        cancel_event=self._current_stop,
                        authority_check=lambda: self._request_is_current(req),
                    )
                    if effective_language != language_hint:
                        language_hint = effective_language
                        locale = _same_language_locale(language_hint)
                    prosody_plan = self._prosody.plan(
                        text_to_speak,
                        stance=req.stance,
                        valence=req.valence,
                        arousal=req.arousal,
                        fallback_tag=audio_tag,
                        notes_hint=req.prosody_notes,
                        style_hint=req.prosody_style,
                    )
                    main_wav = b""
                    if str(text_to_speak or "").strip():
                        main_wav = _synthesize_active_provider_wav(
                            text_to_speak,
                            voice=voice,
                            model=self._model,
                            language_hint=language_hint,
                            locale=locale,
                            prosody_plan=prosody_plan,
                            prelocalized=localized or req.prelocalized,
                            voice_director_segment=req.voice_director_segment,
                            cancel_event=self._current_stop,
                            authority_check=lambda: self._request_is_current(req),
                        )

                    logger.debug(
                        "[TTS] valence=%.2f arousal=%.2f stance=%r → style=%r tag=%r",
                        req.valence, req.arousal, req.stance, prosody_plan.style, prosody_plan.opening_tag,
                    )

                    prefix_wav = b""
                    if prefix_is_separate:
                        prefix_wav = self._vocal_prefix_wav(
                            req, vocal_prefix, prosody_plan,
                            cancel_event=self._current_stop,
                            authority_check=lambda: self._request_is_current(req),
                        )
                    wav_bytes = _concat_wav_bytes(prefix_wav, main_wav) or main_wav or prefix_wav

                    if not self._request_is_current(req):
                        self._emit(req, "audio_cancelled", reason="stale_turn_before_playback")
                    elif self._current_stop.is_set():
                        self._emit(req, "audio_cancelled", reason="cancelled_before_playback")
                    else:
                        wav_bytes = _apply_output_volume(wav_bytes)
                        lip_sync = build_lip_sync_envelope(wav_bytes)
                        def _mark_audio_started() -> None:
                            nonlocal callback_called
                            if callback_called or self._current_stop.is_set() or not self._request_is_current(req):
                                return
                            callback_called = True
                            self._emit(
                                req, "audio_start", audio_envelope=lip_sync.values,
                                audio_frame_ms=lip_sync.frame_ms, audio_duration_ms=lip_sync.duration_ms,
                                audio_mouth_shape=lip_sync.shape,
                            )
                            if req.on_start_play is not None:
                                try:
                                    req.on_start_play()
                                except Exception as cb_exc:
                                    logger.error("[TTS] Error in on_start_play callback: %s", cb_exc)

                        _play_wav_bytes(
                            wav_bytes, self._current_stop, on_started=_mark_audio_started, volume_applied=True,
                        )
                        if self._current_stop.is_set() or not self._request_is_current(req):
                            self._emit(req, "audio_cancelled", reason="cancelled_during_playback")
                        elif not callback_called:
                            self._emit(req, "audio_failed", reason="playback_not_started")
                        else:
                            self._emit(req, "audio_done")

                except Exception as exc:
                    cancelled = (
                        str(exc) == "tts_cancelled"
                        or self._current_stop.is_set()
                        or not self._request_is_current(req)
                    )
                    if cancelled:
                        self._emit(req, "audio_cancelled", reason="speech_superseded")
                    elif isinstance(exc, SpeechLocalizationError):
                        speech_contract_failed = True
                        self._emit(req, "audio_localization_failed", error=str(exc))
                        logger.warning("[TTS] Audio bị bỏ vì không giữ được language contract: %s", exc)
                    else:
                        failure_reason = _tts_failure_reason(exc)
                        self._emit(req, "audio_failed", reason=failure_reason, error=str(exc))
                        logger.warning(
                            "[TTS] Lỗi reason=%s chars=%d: %s",
                            failure_reason,
                            len(str(getattr(req, "text", "") or "")),
                            exc,
                        )
                    # _synthesize_wav đã đánh dấu đúng lease gây 429. Không được
                    # mark "key hiện tại" lần nữa ở đây vì thread khác có thể đã xoay pool.
            finally:
                if (
                    not callback_called
                    and req.on_start_play is not None
                    and self._request_is_current(req)
                    and not self._current_stop.is_set()
                    and not speech_contract_failed
                ):
                    try:
                        req.on_start_play()
                    except Exception as cb_exc:
                        logger.error("[TTS] Error in fallback on_start_play callback: %s", cb_exc)
                with self._current_lock:
                    if self._current_req is req:
                        self._current_req = None




def _build_worker(voice: str, model: str, language_hint: str, *, epoch_validator=None):
    """Dựng worker mới nhưng vẫn chịu được test double/adapter constructor cũ."""
    try:
        return _TtsWorker(
            voice, model, language_hint, epoch_validator=epoch_validator
        )
    except TypeError as exc:
        if "epoch_validator" not in str(exc):
            raise
        worker = _TtsWorker(voice, model, language_hint)
        try:
            setattr(worker, "_epoch_validator", epoch_validator)
        except (AttributeError, TypeError) as exc:
            logger.debug("[TTS] legacy worker epoch validator attach failed: %s", _short_error(exc))
        return worker

# ── Public API ────────────────────────────────────────────────────────────────

class TtsEngine:
    """Engine TTS chính. Gắn vào coordinator.tts_engine."""

    def __init__(self) -> None:
        self._worker: Optional[_TtsWorker] = None
        self._ok = False
        self._closed = False
        self._config_lock = threading.RLock()
        self._config_signature = ""
        # Mỗi lượt nói công khai có một epoch. Request TTS hoàn tất muộn chỉ
        # được phát nếu epoch của nó vẫn là epoch đang sở hữu loa.
        self._speech_lock = threading.RLock()
        self._speech_epoch = 0
        self._speech_turn_id = ""

        if not _enabled():
            logger.info("[TTS] Đã tắt bởi runtime settings.")
            return

        provider = _tts_provider()
        if provider == "elevenlabs":
            try:
                import requests  # noqa: F401
            except ImportError:
                logger.warning("[TTS] requests chưa cài. Chạy: pip install requests")
                return
            if not _elevenlabs_auth_configured_fast():
                logger.warning(
                    "[TTS] Missing ElevenLabs auth: account pool/Firebase refresh token "
                    "or ELEVENLABS_API_KEY/ERIS_ELEVENLABS_API_KEY is required."
                )
                return
            if not _voice():
                logger.warning(
                    "[TTS] Thiếu ERIS_ELEVENLABS_VOICE_ID hoặc tts.elevenlabs_voice_id."
                )
                return
        voice = _voice()
        model = _model()
        lang = _language_hint()
        self._worker = _build_worker(
            voice, model, lang, epoch_validator=self.is_speech_epoch_current
        )
        self._config_signature = f"{provider}|{voice}|{model}|{lang}"
        self._ok = True
        logger.info(
            "[TTS] Khởi động. Provider=%s Model=%s Voice=%s Language=%s",
            provider, model, voice, lang,
        )


    def _cancel_backends(self, *, reason: str, only_interruptible: bool) -> bool:
        """Hủy cả task-manager lẫn worker legacy.

        Trước đây hàm public trả về ngay khi manager báo đã hủy, nên queue legacy
        có thể vẫn sống sau khi đổi profile. Hai backend cùng tồn tại là chuyện
        hợp lệ khi setting được đổi live; vì thế phải quét cả hai.
        """
        cancelled = False
        manager = getattr(self, "task_manager", None)
        if manager is not None and callable(getattr(manager, "cancel_current", None)):
            try:
                cancelled = bool(
                    manager.cancel_current(
                        reason=reason, only_interruptible=only_interruptible
                    )
                ) or cancelled
            except Exception as exc:
                logger.debug("[TTS] task manager cancel failed: %s", exc)
        worker = self._worker
        if worker is not None:
            try:
                cancelled = bool(
                    worker.cancel_current(
                        reason=reason, only_interruptible=only_interruptible
                    )
                ) or cancelled
            except Exception as exc:
                logger.debug("[TTS] worker cancel failed: %s", exc)
        return cancelled

    def begin_speech_turn(self, turn_id: str) -> int:
        """Trao quyền phát loa cho một turn mới và hủy mọi turn cũ.

        Gọi nhiều lần với cùng ``turn_id`` là idempotent, cần cho nhánh dispatcher
        thử task-manager rồi fallback inline.
        """
        normalized = str(turn_id or "").strip()
        with self._speech_lock:
            if normalized and normalized == self._speech_turn_id:
                return self._speech_epoch
            self._speech_epoch += 1
            epoch = self._speech_epoch
            self._speech_turn_id = normalized
        self._cancel_backends(reason="new_speech_turn", only_interruptible=False)
        return epoch

    def supersede_all(self, *, reason: str = "superseded") -> bool:
        """Thu hồi quyền phát của mọi request cũ, kể cả synth remote đang treo."""
        with self._speech_lock:
            self._speech_epoch += 1
            self._speech_turn_id = ""
        return self._cancel_backends(reason=reason, only_interruptible=False)

    def is_speech_epoch_current(self, epoch: int, turn_id: str = "") -> bool:
        try:
            expected = int(epoch)
        except (TypeError, ValueError):
            return True
        if expected <= 0:
            return True
        normalized = str(turn_id or "").strip()
        with self._speech_lock:
            if normalized and self._speech_turn_id and normalized == self._speech_turn_id:
                return True
            return expected == self._speech_epoch

    def refresh_configuration(self) -> bool:
        """Nạp lại language setting và dựng worker mới nếu dropdown đã đổi."""
        # Không biến một engine đã tắt/khởi động lỗi thành enabled chỉ vì panel
        # đổi language. Chỉ hot-reload worker đã khởi động thành công.
        if self._closed or not self._ok or self._worker is None:
            return False
        provider = _tts_provider()
        voice = _voice()
        model = _model()
        lang = _language_hint()
        signature = f"{provider}|{voice}|{model}|{lang}"
        with self._config_lock:
            if signature == self._config_signature:
                return False
            old_signature = self._config_signature or "<none>"
            self.supersede_all(reason="tts_configuration_changed")
            manager = getattr(self, "task_manager", None)
            if manager is not None and callable(getattr(manager, "shutdown", None)):
                try:
                    manager.shutdown()
                except Exception as exc:
                    logger.debug("[TTS] task manager config refresh failed: %s", exc)
                self.task_manager = None
            old_worker = self._worker
            if old_worker is not None:
                old_worker.shutdown()
            self._worker = _build_worker(
                voice, model, lang, epoch_validator=self.is_speech_epoch_current
            )
            self._config_signature = signature
            self._ok = True
            logger.info("[TTS] Đã nạp lại setting. %s -> %s (Language=%s)", old_signature, signature, lang)
            return True

    def speak(
        self,
        text: str,
        *,
        valence: float = 0.0,
        arousal: float = 0.3,
        stance: str = "",
        on_start_play = None,
        prelocalized: bool = False,
        verified_transcript_hash: str = "",
        prosody_style: str = "",
        prosody_notes: str = "",
        voice_director_segment: Mapping[str, object] | None = None,
        turn_id: str = "",
        segment_id: int = 0,
        interruptible: bool = True,
        vocal_cache_kind: str = "",
        vocal_prefix: str = "",
        language_hint: str = "",
        voice_family: str = "",
        locale: str = "",
        replace: bool = True,
        on_event = None,
        speech_epoch: int | None = None,
    ) -> None:
        """Phát TTS cho text với cảm xúc cho trước. Non-blocking."""
        self.refresh_configuration()
        if not _enabled() or not self._ok or self._worker is None:
            if on_event is not None:
                try:
                    on_event(TtsPlaybackEvent(type="audio_failed", text=str(text or ""), reason="tts_disabled"))
                except Exception as cb_exc:
                    logger.error("[TTS] Error in on_event callback: %s", cb_exc)
            if on_start_play is not None:
                try:
                    on_start_play()
                except Exception as cb_exc:
                    logger.error("[TTS] Error in fallback on_start_play callback: %s", cb_exc)
            return
        clean = _clean_text(text)
        if not clean and str(text or "").strip() and not verified_transcript_hash:
            lang = str(language_hint or _language_hint()).strip().lower()
            clean = "Mình gửi code ở đây nhé." if lang in ("vi", "vi-vn") else "コードを書いておいたよ。"
        if verified_transcript_hash:
            import hashlib
            if hashlib.sha256(clean.encode('utf-8')).hexdigest() != verified_transcript_hash:
                raise SpeechLocalizationError('v03_tts_input_mutated')
        if not clean:
            if on_event is not None:
                try:
                    on_event(TtsPlaybackEvent(type="audio_cancelled", text=str(text or ""), reason="empty_text"))
                except Exception as cb_exc:
                    logger.error("[TTS] Error in on_event callback: %s", cb_exc)
            if on_start_play is not None:
                try:
                    on_start_play()
                except Exception as cb_exc:
                    logger.error("[TTS] Error in fallback on_start_play callback: %s", cb_exc)
            return
        epoch = (
            self.begin_speech_turn(turn_id)
            if speech_epoch is None
            else int(speech_epoch)
        )
        self._worker.speak(
            _SpeakRequest(
                clean,
                valence,
                arousal,
                stance,
                on_start_play,
                prelocalized=prelocalized,
                verified_transcript_hash=verified_transcript_hash,
                prosody_style=prosody_style,
                prosody_notes=prosody_notes,
                voice_director_segment=voice_director_segment,
                turn_id=turn_id,
                segment_id=segment_id,
                interruptible=interruptible,
                vocal_cache_kind=vocal_cache_kind,
                vocal_prefix=vocal_prefix,
                language_hint=language_hint,
                voice_family=voice_family,
                locale=locale,
                on_event=on_event,
                speech_epoch=epoch,
            ),
            replace=replace,
        )

    def shutdown(self) -> None:
        self._closed = True
        manager = getattr(self, "task_manager", None)
        if manager is not None and callable(getattr(manager, "shutdown", None)):
            try:
                manager.shutdown()
            except Exception as exc:
                logger.debug("[TTS] task manager shutdown failed: %s", exc)
        if self._worker is not None:
            self._worker.shutdown()
            self._worker = None

    def cancel_current(self, *, reason: str = "cancelled", only_interruptible: bool = True) -> bool:
        """Dừng mọi backend audio đang tồn tại, không trả sớm sau manager."""
        return self._cancel_backends(
            reason=reason, only_interruptible=only_interruptible
        )

    def synthesize(
        self,
        text: str,
        *,
        valence: float = 0.0,
        arousal: float = 0.3,
        stance: str = "",
        prelocalized: bool = False,
        verified_transcript_hash: str = "",
        prosody_style: str = "",
        prosody_notes: str = "",
        voice_director_segment: Mapping[str, object] | None = None,
        turn_id: str = "",
        segment_id: int = 0,
        interruptible: bool = True,
        vocal_cache_kind: str = "",
        vocal_prefix: str = "",
        language_hint: str = "",
        voice_family: str = "",
        locale: str = "",
        cancel_event = None,
        speech_epoch: int | None = None,
    ) -> bytes:
        """Backend API mới: text sạch -> WAV bytes, chưa phát ra loa."""
        self.refresh_configuration()
        if not _enabled() or not self._ok or self._worker is None:
            raise RuntimeError("tts_disabled")
        clean = _clean_text(text)
        if not clean and str(text or "").strip() and not verified_transcript_hash:
            lang = str(language_hint or getattr(self, "_language_hint", "") or _language_hint()).strip().lower()
            clean = "Mình gửi code ở đây nhé." if lang in ("vi", "vi-vn") else "コードを書いておいたよ。"
        if verified_transcript_hash:
            import hashlib
            if hashlib.sha256(clean.encode('utf-8')).hexdigest() != verified_transcript_hash:
                raise SpeechLocalizationError('v03_tts_input_mutated')
        if not clean:
            return b""
        if speech_epoch is None:
            with self._speech_lock:
                epoch = self._speech_epoch
        else:
            epoch = int(speech_epoch)
        req = _SpeakRequest(
            clean,
            valence,
            arousal,
            stance,
            None,
            prelocalized=prelocalized,
            verified_transcript_hash=verified_transcript_hash,
            prosody_style=prosody_style,
            prosody_notes=prosody_notes,
            voice_director_segment=voice_director_segment,
            turn_id=turn_id,
            segment_id=segment_id,
            interruptible=interruptible,
            vocal_cache_kind=vocal_cache_kind,
            vocal_prefix=vocal_prefix,
            language_hint=language_hint,
            voice_family=voice_family,
            locale=locale,
            on_event=None,
            speech_epoch=epoch,
        )
        return self._worker.synthesize(req, cancel_event=cancel_event)

    def play_audio(self, wav_bytes: bytes, stop_event: threading.Event, on_started=None) -> None:
        """Phát WAV và báo mốc thiết bị playback đã thật sự được khởi động."""
        if wav_bytes:
            _play_wav_bytes(wav_bytes, stop_event, on_started=on_started)

    def lip_sync_audio(self, wav_bytes: bytes) -> bytes:
        """Return PCM with the same volume transform used by playback."""
        return _apply_output_volume(wav_bytes)

    def play_prepared_audio(self, wav_bytes: bytes, stop_event: threading.Event, on_started=None) -> None:
        """Play the volume snapshot already used to derive the lip-sync envelope."""
        if wav_bytes:
            _play_wav_bytes(wav_bytes, stop_event, on_started=on_started, volume_applied=True)

    def fallback_health_snapshot(self) -> dict[str, dict]:
        """Health của từng fallback provider để canary không coi TTS là một cục đen."""
        output: dict[str, dict] = {}
        try:
            for name, item in _build_local_fallback_chain().health().items():
                output[str(name)] = {
                    "ready": bool(getattr(item, "ready", False)),
                    "provider": str(getattr(item, "provider", name) or name),
                    "reason": str(getattr(item, "reason", "") or ""),
                    "details": dict(getattr(item, "details", {}) or {}),
                }
        except Exception as exc:
            output["fallback_chain"] = {"ready": False, "provider": "fallback_chain", "reason": type(exc).__name__}
        return output

    def cache_signature(self) -> str:
        """Chữ ký cấu hình TTS để task manager cache đúng voice/model/style."""
        with self._config_lock:
            signature = self._config_signature or f"{_tts_provider()}|{_voice()}|{_model()}|{_language_hint()}"
        return f"{signature}|{_quality_mode()}"


def _clean_text(text: str) -> str:
    """Bỏ markdown/code/URL trước khi đọc, nhưng giữ line break có chủ ý."""
    t = str(text or "").replace("\r\n", "\n").replace("\r", "\n")
    t = re.sub(r"(?:```[\w.+-]*\n?[\s\S]*?(?:```|\Z)|~~~[\w.+-]*\n?[\s\S]*?(?:~~~|\Z))", "", t, flags=re.S)
    t = re.sub(r"\*{1,2}([^*]*)\*{1,2}", r"\1", t)
    t = re.sub(r"_{1,2}([^_]*)_{1,2}", r"\1", t)
    t = re.sub(r"`{1,3}[^`]*`{1,3}", "", t)
    t = re.sub(r"https?://\S+", "", t)
    t = _strip_tts_honorifics(t)
    # Space ngang thì gọn lại; newline giữ lại để stage/dispatcher truyền nhịp
    # sang Gemini như các breath group ngắn.
    t = re.sub(r"[ \t\f\v]+", " ", t)
    t = re.sub(r" *\n+ *", "\n", t)
    t = re.sub(r"\n{3,}", "\n\n", t)
    t = "\n".join(line.strip() for line in t.split("\n")).strip()
    return t


def build_tts_engine() -> TtsEngine:
    """Entry point — gọi một lần trong vision.py."""
    return TtsEngine()


__all__ = ["TtsEngine", "TtsPlaybackEvent", "build_tts_engine"]
