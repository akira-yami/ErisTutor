# -*- coding: utf-8 -*-
"""
cortex/tutor/config.py
======================

Cấu hình (Master_Switch mặc định TẮT), risk guard, logger và tiện ích lọc định danh.
"""

from __future__ import annotations

import os
import re
import time
from dataclasses import dataclass, field
from typing import Any, Optional

try:  # pragma: no cover - import mềm để module config vẫn sống khi chạy lẻ
    from cortex.tutor.secret_scrubber import scrub_secrets
except Exception:  # pragma: no cover
    try:
        from .secret_scrubber import scrub_secrets  # type: ignore
    except Exception:  # pragma: no cover
        def scrub_secrets(text: Any) -> str:  # type: ignore
            return str(text or "")


# ---------------------------------------------------------------------------
# Lọc định danh — không bao giờ để email/cookie lọt vào log/record/popup.
# ---------------------------------------------------------------------------
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
_TOKENISH_RE = re.compile(r"\b(?:cookie|token|password|passwd|secret|session|sid)\b\s*[:=]\s*\S+", re.IGNORECASE)


def scrub_identity(text: Any) -> str:
    """Bỏ email và secret/token/API key khỏi văn bản. An toàn với None.

    Đây là cổng cũ được nhiều module gọi, nên mở rộng nó thay vì tạo thêm một
    hàm đẹp để rồi không ai dùng. Phần mềm loài người vốn đã có đủ đồ trang trí.
    """

    value = scrub_secrets(text)
    value = _EMAIL_RE.sub("[email]", value)
    value = _TOKENISH_RE.sub("[redacted-secret]", value)
    return value


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, "").strip())
    except (TypeError, ValueError):
        return default


def _env_int(name: str, default: int) -> int:
    try:
        return int(float(os.environ.get(name, "").strip()))
    except (TypeError, ValueError):
        return default


def _env_str(name: str, default: str) -> str:
    raw = os.environ.get(name)
    return raw if raw is not None else default


# =============================================================================
# Config
# =============================================================================

@dataclass(slots=True)
class TutorConfig:
    """Cấu hình Curiosity Tutor. `enabled` (Master_Switch) mặc định TẮT."""

    enabled: bool = False
    # Curiosity gate
    curiosity_threshold: float = 0.5
    min_seconds_between_episodes: float = 120.0
    max_episodes_per_hour: int = 20
    observer_ambiguity_threshold: float = 0.6
    max_gap_queue: int = 50
    dedupe_similarity: float = 0.86
    # Web backend
    profile_dir: str = ""
    nav_url: str = "https://gemini.google.com"
    episode_timeout_s: float = 90.0
    answer_stable_window_s: float = 2.5
    type_delay_ms_min: int = 40
    type_delay_ms_max: int = 120
    window_width: int = 520
    window_height: int = 720
    window_x: int = 880
    window_y: int = 120
    app_mode: bool = True   # cửa sổ app (ẩn thanh địa chỉ/tab) cho gọn, "trang trí" hơn
    # Selector (chỉnh một chỗ khi Gemini đổi UI)
    sel_input: str = "div[contenteditable='true']"
    sel_send: str = "button[aria-label*='Send'], button[aria-label*='Gửi']"
    sel_response: str = "message-content, .model-response-text"
    sel_login_marker: str = "input[type='password'], #captcha, iframe[src*='recaptcha']"
    # Chống phát hiện automation (Google chặn "browser may not be secure")
    browser_channel: str = "chrome"   # "chrome" = Chrome thật; "" = Chromium bundled
    # Risk guard
    max_consecutive_failures: int = 3
    failure_cooldown_s: float = 1800.0
    captcha_pause_s: float = 3600.0
    # Tra cứu nhẹ theo yêu cầu user. Không dùng Deep Research API.
    research_answer_max_words: int = 280
    research_prompt_max_chars: int = 395
    research_persist_answers: bool = False
    # Khoe (share)
    share_window_s: float = 600.0
    share_novelty_threshold: float = 0.5

    def normalized(self) -> "TutorConfig":
        """Clamp giá trị về miền hợp lệ. Cờ không hợp lệ ⇒ coi như TẮT (xử lý ở from_env)."""

        def clamp01(v: float, d: float) -> float:
            try:
                n = float(v)
            except (TypeError, ValueError):
                n = d
            if n != n:
                n = d
            return max(0.0, min(1.0, n))

        lo = max(0, int(self.type_delay_ms_min))
        hi = max(lo, int(self.type_delay_ms_max))
        return TutorConfig(
            enabled=bool(self.enabled),
            curiosity_threshold=clamp01(self.curiosity_threshold, 0.5),
            min_seconds_between_episodes=max(0.0, float(self.min_seconds_between_episodes)),
            max_episodes_per_hour=max(0, int(self.max_episodes_per_hour)),
            observer_ambiguity_threshold=clamp01(self.observer_ambiguity_threshold, 0.6),
            max_gap_queue=max(1, int(self.max_gap_queue)),
            dedupe_similarity=clamp01(self.dedupe_similarity, 0.86),
            profile_dir=str(self.profile_dir or ""),
            nav_url=str(self.nav_url or "https://gemini.google.com"),
            episode_timeout_s=max(1.0, float(self.episode_timeout_s)),
            answer_stable_window_s=max(0.1, float(self.answer_stable_window_s)),
            type_delay_ms_min=lo,
            type_delay_ms_max=hi,
            window_width=max(200, int(self.window_width)),
            window_height=max(200, int(self.window_height)),
            window_x=int(self.window_x),
            window_y=int(self.window_y),
            app_mode=bool(self.app_mode),
            sel_input=str(self.sel_input or ""),
            sel_send=str(self.sel_send or ""),
            sel_response=str(self.sel_response or ""),
            sel_login_marker=str(self.sel_login_marker or ""),
            browser_channel=str(self.browser_channel or ""),
            max_consecutive_failures=max(1, int(self.max_consecutive_failures)),
            failure_cooldown_s=max(0.0, float(self.failure_cooldown_s)),
            captcha_pause_s=max(0.0, float(self.captcha_pause_s)),
            research_answer_max_words=max(80, min(600, int(self.research_answer_max_words))),
            research_prompt_max_chars=max(220, min(395, int(self.research_prompt_max_chars))),
            research_persist_answers=bool(self.research_persist_answers),
            share_window_s=max(0.0, float(self.share_window_s)),
            share_novelty_threshold=clamp01(self.share_novelty_threshold, 0.5),
        )

    @classmethod
    def from_env(cls, logger: "Optional[TutorLogger]" = None) -> "TutorConfig":
        """Đọc ERIS_TUTOR_* từ môi trường.

        Master_Switch (`ERIS_TUTOR_ENABLED`):
        - VẮNG (không khai báo) ⇒ BẬT (auto chạy) — đây là hành vi mặc định.
        - "off/false/0/no" ⇒ TẮT (kill switch vẫn còn cho ai muốn tắt hẳn).
        - giá trị không hợp lệ ⇒ TẮT + cảnh báo (Req 8.3).
        """

        raw_enabled = os.environ.get("ERIS_TUTOR_ENABLED")
        if raw_enabled is None:
            enabled = True
        elif raw_enabled.strip().lower() in {"1", "true", "yes", "on"}:
            enabled = True
        elif raw_enabled.strip().lower() in {"0", "false", "no", "off"}:
            enabled = False
        else:
            enabled = False
            if logger is not None:
                logger.warn(f"ERIS_TUTOR_ENABLED không hợp lệ ({raw_enabled!r}) → coi như TẮT")

        # LƯU Ý: TutorConfig dùng slots=True nên KHÔNG đọc default qua cls.<field>
        # (đó là slot descriptor). Dùng một instance default để lấy giá trị thật.
        d = cls()
        cfg = cls(
            enabled=enabled,
            curiosity_threshold=_env_float("ERIS_TUTOR_CURIOSITY_THRESHOLD", 0.5),
            min_seconds_between_episodes=_env_float("ERIS_TUTOR_MIN_GAP_SECONDS", 120.0),
            max_episodes_per_hour=_env_int("ERIS_TUTOR_MAX_PER_HOUR", 20),
            observer_ambiguity_threshold=_env_float("ERIS_TUTOR_OBSERVER_AMBIGUITY", 0.6),
            max_gap_queue=_env_int("ERIS_TUTOR_MAX_GAP_QUEUE", 50),
            profile_dir=_env_str("ERIS_TUTOR_PROFILE_DIR", ""),
            nav_url=_env_str("ERIS_TUTOR_NAV_URL", "https://gemini.google.com"),
            episode_timeout_s=_env_float("ERIS_TUTOR_EPISODE_TIMEOUT", 90.0),
            answer_stable_window_s=_env_float("ERIS_TUTOR_ANSWER_STABLE_WINDOW", 2.5),
            window_width=_env_int("ERIS_TUTOR_WINDOW_WIDTH", 520),
            window_height=_env_int("ERIS_TUTOR_WINDOW_HEIGHT", 720),
            window_x=_env_int("ERIS_TUTOR_WINDOW_X", 880),
            window_y=_env_int("ERIS_TUTOR_WINDOW_Y", 120),
            app_mode=_env_bool("ERIS_TUTOR_APP_MODE", True),
            sel_input=_env_str("ERIS_TUTOR_SEL_INPUT", d.sel_input),
            sel_send=_env_str("ERIS_TUTOR_SEL_SEND", d.sel_send),
            sel_response=_env_str("ERIS_TUTOR_SEL_RESPONSE", d.sel_response),
            sel_login_marker=_env_str("ERIS_TUTOR_SEL_LOGIN_MARKER", d.sel_login_marker),
            browser_channel=_env_str("ERIS_TUTOR_BROWSER_CHANNEL", d.browser_channel),
            research_answer_max_words=_env_int("ERIS_TUTOR_RESEARCH_MAX_WORDS", d.research_answer_max_words),
            research_prompt_max_chars=_env_int("ERIS_TUTOR_RESEARCH_PROMPT_MAX_CHARS", d.research_prompt_max_chars),
            research_persist_answers=_env_bool("ERIS_TUTOR_RESEARCH_PERSIST", d.research_persist_answers),
            share_window_s=_env_float("ERIS_TUTOR_SHARE_WINDOW", 600.0),
            share_novelty_threshold=_env_float("ERIS_TUTOR_SHARE_NOVELTY", 0.5),
        )
        return cfg.normalized()


# =============================================================================
# Risk guard
# =============================================================================

class TutorRiskGuard:
    """Tạm ngưng episode khi lỗi liên tiếp hoặc gặp captcha/hết phiên."""

    def __init__(self, config: TutorConfig) -> None:
        self.config = config
        self._consecutive_failures = 0
        self._paused_until: float = 0.0

    def is_paused(self, now: float) -> bool:
        return now < self._paused_until

    def paused_until(self) -> float:
        return self._paused_until

    def note_success(self, now: float) -> None:
        self._consecutive_failures = 0

    def note_failure(self, now: float, reason: Optional[str] = None) -> None:
        self._consecutive_failures += 1
        if self._consecutive_failures >= self.config.max_consecutive_failures:
            self._paused_until = max(self._paused_until, now + self.config.failure_cooldown_s)
            self._consecutive_failures = 0

    def note_captcha(self, now: float) -> None:
        self._paused_until = max(self._paused_until, now + self.config.captcha_pause_s)

    @property
    def consecutive_failures(self) -> int:
        return self._consecutive_failures


# =============================================================================
# Logger
# =============================================================================

def _scrub_payload(value: Any) -> Any:
    """Scrub secret đệ quy cho log/panel payload.

    Log cũ chỉ scrub top-level string; nested dict như research metrics/ledger có thể
    lùa token vào trong. Một cái rây mà chỉ rây mặt trên thì đúng là thời trang bảo mật.
    """

    try:
        if isinstance(value, str):
            return scrub_identity(value)
        if isinstance(value, dict):
            return {str(k): _scrub_payload(v) for k, v in value.items()}
        if isinstance(value, (list, tuple)):
            return [_scrub_payload(v) for v in value]
    except Exception:
        return "[unserializable]"
    return value

class TutorLogger:
    """Observability cho Tutor. KHÔNG bao giờ log định danh (email/cookie/HTML login)."""

    def __init__(self, sink: Optional[Any] = None, *, max_records: int = 256) -> None:
        self._sink = sink
        self.records: list[dict[str, Any]] = []
        self._max_records = max_records

    def _emit(self, payload: dict[str, Any]) -> None:
        try:
            safe = _scrub_payload(payload)
            self.records.append(safe)
            if len(self.records) > self._max_records:
                self.records = self.records[-self._max_records:]
            sink = self._sink
            if sink is not None:
                fn = getattr(sink, "info", None)
                if callable(fn):
                    fn("tutor %r", safe)
        except Exception:
            pass

    def warn(self, message: str) -> None:
        try:
            payload = {"kind": "warning", "message": scrub_identity(message)}
            self.records.append(payload)
            sink = self._sink
            if sink is not None:
                fn = getattr(sink, "warning", None) or getattr(sink, "info", None)
                if callable(fn):
                    fn("tutor %r", payload)
        except Exception:
            pass

    def log_stage(self, episode_id: str, stage: str) -> None:
        self._emit({"kind": "stage", "episode_id": episode_id, "stage": stage})

    def log_episode(self, episode_dict: dict[str, Any]) -> None:
        payload = {"kind": "episode", **episode_dict}
        self._emit(payload)


__all__ = ["TutorConfig", "TutorRiskGuard", "TutorLogger", "scrub_identity"]
