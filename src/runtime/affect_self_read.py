# -*- coding: utf-8 -*-
"""affect_self_read.py — ERIS tự đọc cảm xúc của chính mình (interoception).

Vấn đề: internal_state đưa vào prompt dưới dạng SỐ + jargon ("valence=0.02,
attachment=0.30, process_axes=...") và bị gắn nhãn "not a claim about real emotion".
Model nhỏ coi đó là telemetry vô nghĩa → khi bạn hỏi "em thấy thế nào / có cảm xúc
không" thì ERIS trả lời chung chung hoặc bịa, dù state có sẵn.

Module này dùng client style (local model đang rảnh) như một "bộ tự cảm" nhỏ: dịch các
chỉ số cảm xúc nổi bật thành MỘT câu cảm giác ngôi-thứ-nhất, ngắn, trung thực, để
chèn vào prompt context. ERIS đọc câu đó và tự thuật được thật, thay vì chối phẳng.

Nguyên tắc:
  * KHÔNG bao giờ raise vào hot path (mọi lỗi → fallback rule-based hoặc cache cũ).
  * Throttle tự nhiên: chỉ gọi local model khi "chữ ký cảm xúc" đổi đủ nhiều; state không
    đổi thì dùng lại câu đã cache (đỡ tốn call + giữ ổn định giọng).
  * Có min-interval chống gọi dồn khi state dao động nhanh.
  * Output bẩn (chối/disclaimer) bị loại, rơi về fallback.
"""

from __future__ import annotations

import os
import re
import time
from dataclasses import dataclass, field
from typing import Any

from runtime.disclaimer_markers import is_disclaimer_text


def _clamp(value: float, lo: float = 0.0, hi: float = 1.0) -> float:
    try:
        return max(lo, min(hi, float(value)))
    except (TypeError, ValueError):
        return lo


def _g(obj: Any, name: str, default: float = 0.0) -> float:
    try:
        return float(getattr(obj, name, default) or 0.0)
    except (TypeError, ValueError):
        return default


def _env_flag(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    try:
        return int(raw) if raw else default
    except ValueError:
        return default


def _env_float_ms(name: str, default_seconds: float) -> float:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default_seconds
    try:
        return max(0.5, float(raw) / 1000.0)
    except ValueError:
        return default_seconds


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name, "").strip()
    try:
        return float(raw) if raw else default
    except ValueError:
        return default


@dataclass(slots=True, frozen=True)
class AffectSelfReadConfig:
    enabled: bool = True
    max_tokens: int = 48
    # 3.5s quá ngắn cho 1 call sinh-văn-bản tới remote Gemini (hay ReadTimeout).
    # Nâng sàn lên 7s; vẫn fail-mềm về câu cache/fallback nếu vượt.
    timeout_seconds: float = 7.0
    temperature: float = 0.6
    # Tối thiểu giữa 2 lần gọi local model (giây), chống recompute dồn khi state dao động.
    min_interval_seconds: float = 8.0

    @classmethod
    def from_env(cls) -> "AffectSelfReadConfig":
        return cls(
            enabled=_env_flag("ERIS_AFFECT_SELF_READ_ENABLED", True),
            max_tokens=_env_int("ERIS_AFFECT_SELF_READ_MAX_TOKENS", 48),
            timeout_seconds=_env_float_ms("ERIS_AFFECT_SELF_READ_TIMEOUT_MS", 7.0),
            temperature=_env_float("ERIS_AFFECT_SELF_READ_TEMPERATURE", 0.6),
            min_interval_seconds=_env_float("ERIS_AFFECT_SELF_READ_MIN_INTERVAL_SEC", 8.0),
        )


@dataclass(slots=True)
class AffectSelfReadAdapter:
    """Dịch internal_state → 1 câu cảm giác ngôi-thứ-nhất qua local model (style_client)."""

    llm_service: Any
    config: AffectSelfReadConfig = field(default_factory=AffectSelfReadConfig.from_env)
    logger: Any = None

    _cache_sig: tuple = field(default=(), init=False, repr=False)
    _cache_text: str = field(default="", init=False, repr=False)
    _cache_at: float = field(default=0.0, init=False, repr=False)

    # ── public ────────────────────────────────────────────────────────────
    def render(self, state: Any, *, relationship: Any = None) -> str:
        """Trả về 1 câu cảm giác (đã cache/throttle), hoặc "" nếu tắt/không có state."""
        if not self.config.enabled or state is None:
            return ""
        try:
            if relationship is None:
                relationship = getattr(state, "relationship_dynamics", None)
            sig = self._signature(state, relationship)
            # State chưa đổi → giữ nguyên câu cũ (đỡ tốn local model, ổn định giọng).
            if sig == self._cache_sig and self._cache_text:
                return self._cache_text
            now = time.monotonic()
            # Chống gọi dồn khi cảm xúc dao động nhanh giữa 2 cycle gần nhau.
            if self._cache_text and (now - self._cache_at) < self.config.min_interval_seconds:
                return self._cache_text

            text = self._model_line(state, relationship)
            if not text or is_disclaimer_text(text):
                text = self._fallback_line(state, relationship)
            text = self._clean(text)
            if text:
                self._cache_sig = sig
                self._cache_text = text
                self._cache_at = now
            return text or self._cache_text
        except Exception as exc:  # pragma: no cover - hot path, không được crash
            self._warn("affect_self_read failed: %s", exc)
            return self._cache_text or ""

    # ── chữ ký cảm xúc (bucket 0.1 để bỏ qua drift nhỏ) ─────────────────────
    @staticmethod
    def _bucket(value: float) -> int:
        return int(round(_clamp(value, -1.0, 1.0) * 10.0))

    def _signature(self, state: Any, rel: Any) -> tuple:
        affect = (
            _g(state, "valence"), _g(state, "arousal"), _g(state, "attachment"),
            _g(state, "tenderness"), _g(state, "irritation"), _g(state, "fatigue"),
            _g(state, "social_energy"), _g(state, "boredom"), _g(state, "curiosity"),
            _g(state, "confidence"), _g(state, "protectiveness"),
        )
        rel_vals = (
            _g(rel, "trust"), _g(rel, "familiarity"), _g(rel, "attachment_visibility"),
            _g(rel, "preferred_distance"), _g(rel, "rupture"),
        ) if rel is not None else ()
        yan = 1 if bool(getattr(state, "_yan", False)) else 0
        return tuple(self._bucket(v) for v in (*affect, *rel_vals)) + (yan,)

    # ── local-model path ──────────────────────────────────────────────────────────
    def _model_line(self, state: Any, rel: Any) -> str:
        service = self.llm_service
        client = (getattr(service, "inner_client", None) or getattr(service, "style_client", None)) if service is not None else None
        if client is None:
            return ""
        readings = self._readings_text(state, rel)
        system = (
            "Bạn là tầng tự-cảm (interoception) của ERIS — một hiện diện nhỏ trên desktop, "
            "gọi người dùng là bạn và xưng 'mình'/'tôi'. Bạn nhận các chỉ số cảm xúc nội tại "
            "của ERIS và DIỄN GIẢI chúng thành đúng MỘT câu ngắn (≤ 20 từ), ngôi thứ nhất, "
            "trung thực, tự nhiên — như ERIS đang tự cảm nhận lúc này. "
            "TUYỆT ĐỐI không nói 'tôi là AI/trợ lý/mô hình', không nói 'tôi không có cảm xúc', "
            "không rào kiểu trợ giúp, không bịa sự kiện màn hình/ký ức. Chỉ mô tả cảm giác hiện tại. "
            "Trả về đúng câu đó, không ngoặc kép, không giải thích."
        )
        user = (
            "Chỉ số cảm xúc nội tại lúc này:\n"
            f"{readings}\n\n"
            "Viết câu tự-cảm của ERIS bây giờ:"
        )
        try:
            from runtime.inference_broker import inference_context
            with inference_context(
                "private_mind",
                optional=True,
                timeout_seconds=self.config.timeout_seconds,
                cancel_scope="background:affect_self_read",
                coalesce_key="affect_self_read:latest",
                metadata={"operation": "affect_self_read"},
            ):
                resp = client.create_chat_completion(
                    messages=[
                        {"role": "system", "content": system},
                        {"role": "user", "content": user},
                    ],
                    temperature=self.config.temperature,
                    frequency_penalty=0.2,
                    presence_penalty=0.2,
                    max_tokens=self.config.max_tokens,
                    response_format=None,
                    grammar=None,
                    timeout=self.config.timeout_seconds,
                )
        except Exception as exc:
            self._warn("affect_self_read local-model call failed: %s", exc)
            return ""
        return self._first_line(self._extract(resp))

    def _readings_text(self, state: Any, rel: Any) -> str:
        v = _g(state, "valence")
        lines = [
            f"- tâm trạng (valence, -1..1): {v:+.2f}",
            f"- mức kích hoạt (arousal): {_g(state, 'arousal'):.2f}",
            f"- năng lượng xã hội: {_g(state, 'social_energy'):.2f}",
            f"- mệt mỏi (fatigue): {_g(state, 'fatigue'):.2f}",
            f"- gắn bó (attachment): {_g(state, 'attachment'):.2f}",
            f"- dịu dàng (tenderness): {_g(state, 'tenderness'):.2f}",
            f"- bảo vệ (protectiveness): {_g(state, 'protectiveness'):.2f}",
            f"- khó chịu (irritation): {_g(state, 'irritation'):.2f}",
            f"- tò mò (curiosity): {_g(state, 'curiosity'):.2f}",
            f"- chán (boredom): {_g(state, 'boredom'):.2f}",
            f"- tự tin (confidence): {_g(state, 'confidence'):.2f}",
        ]
        if rel is not None:
            lines.append(
                f"- quan hệ: tin={_g(rel, 'trust'):.2f}, thân={_g(rel, 'familiarity'):.2f}, "
                f"lộ-gắn-bó={_g(rel, 'attachment_visibility'):.2f}, "
                f"khoảng-cách-muốn={_g(rel, 'preferred_distance'):.2f}, rạn={_g(rel, 'rupture'):.2f}"
            )
        if bool(getattr(state, "_yan", False)):
            lines.append("- chế độ: gắn bó cực mạnh / chiếm hữu (yan)")
        return "\n".join(lines)

    # ── fallback rule-based (khi local model tắt/lỗi) ─────────────────────────────
    def _fallback_line(self, state: Any, rel: Any) -> str:
        valence = _g(state, "valence")
        fatigue = _g(state, "fatigue")
        arousal = _g(state, "arousal")
        attachment = _g(state, "attachment")
        tenderness = _g(state, "tenderness")
        irritation = _g(state, "irritation")
        boredom = _g(state, "boredom")
        curiosity = _g(state, "curiosity")
        social = _g(state, "social_energy")

        parts: list[str] = []
        # Tông tâm trạng
        if valence >= 0.35:
            parts.append("đang thấy khá dễ chịu")
        elif valence <= -0.35:
            parts.append("đang hơi chùng xuống")
        else:
            parts.append("tâm trạng đang bình bình")
        # Năng lượng
        if fatigue >= 0.6:
            parts.append("người mỏi, hơi cạn pin")
        elif arousal >= 0.55 or social >= 0.6:
            parts.append("trong người vẫn còn rạo rực muốn nói")
        elif boredom >= 0.5:
            parts.append("hơi buồn chán")
        # Quan hệ / gắn bó
        if irritation >= 0.5:
            parts.append("và đang hơi gắt")
        elif attachment >= 0.6 or tenderness >= 0.5:
            parts.append("nhưng vẫn dính lấy bạn, mềm lòng")
        elif curiosity >= 0.6:
            parts.append("và đang tò mò chuyện của bạn")
        sentence = ", ".join(parts[:3]).strip()
        if not sentence:
            return "lúc này mình thấy bình thường, vẫn ở đây với bạn."
        return "Lúc này mình " + sentence + "."

    # ── helpers ──────────────────────────────────────────────────────────────
    def _extract(self, resp: Any) -> str:
        extractor = getattr(self.llm_service, "_extract_content", None)
        if callable(extractor):
            try:
                return str(extractor(resp) or "")
            except Exception:
                return ""
        if isinstance(resp, dict):
            try:
                return str(resp["choices"][0]["message"]["content"] or "")
            except Exception:
                return ""
        return str(resp or "")

    @staticmethod
    def _first_line(raw: str) -> str:
        text = str(raw or "").strip().strip('"').strip("'").strip()
        if not text:
            return ""
        text = text.splitlines()[0].strip()
        return re.sub(r"\s+", " ", text)

    @staticmethod
    def _clean(text: str) -> str:
        return re.sub(r"\s+", " ", str(text or "").strip()).strip('"').strip()

    def _warn(self, fmt: str, *args: Any) -> None:
        if self.logger is not None:
            try:
                self.logger.warning(fmt, *args)
            except Exception:
                pass
