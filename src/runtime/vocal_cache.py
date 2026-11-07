# -*- coding: utf-8 -*-
"""vocal_cache.py — cache WAV cho các âm biểu diễn ngắn.

Các prefix như "Hah... haha.", "Hmph.", "Haa..." là âm thân thể, không phải
nội dung trí tuệ. Nếu lần nào cũng gọi Gemini để sinh lại, phản ứng sẽ chậm và
không ổn định. Cache này lưu WAV prefix sau lần sinh đầu:

    voice + model + act_kind + voice_style + prefix_text -> .wav

Cache chỉ là tối ưu. Miss hoặc lỗi đọc/ghi đều fail mềm; TTS vẫn sinh audio như
bình thường.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path


def vocal_cache_enabled() -> bool:
    return os.environ.get("ERIS_TTS_VOCAL_CACHE", "1").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _cache_root() -> Path:
    raw = os.environ.get("ERIS_TTS_VOCAL_CACHE_DIR", "").strip()
    if raw:
        return Path(raw)
    return Path("voice_presets") / "tts_vocal_cache"


@dataclass(frozen=True, slots=True)
class VocalCacheKey:
    voice: str
    model: str
    kind: str
    style: str
    text: str
    language_hint: str = "auto"

    def fingerprint(self) -> str:
        payload = {
            "voice": self.voice,
            "model": self.model,
            "kind": self.kind,
            "style": self.style,
            "text": self.text,
            "language_hint": self.language_hint,
            "format": "wav_24k_mono_pcm16",
        }
        blob = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
        return hashlib.sha256(blob).hexdigest()[:32]


class VocalCache:
    """File cache nhỏ cho WAV prefix.

    Không giữ bytes lâu trong RAM. Prefix ít, đọc file WAV trực tiếp đủ nhanh và
    tránh làm worker TTS phình bộ nhớ.
    """

    def __init__(self, root: Path | str | None = None) -> None:
        self.root = Path(root) if root is not None else _cache_root()

    def path_for(self, key: VocalCacheKey) -> Path:
        safe_kind = _safe_part(key.kind or "unknown")
        safe_style = _safe_part(key.style or "neutral")
        return self.root / safe_kind / f"{safe_style}_{key.fingerprint()}.wav"

    def get(self, key: VocalCacheKey) -> bytes | None:
        if not vocal_cache_enabled():
            return None
        try:
            path = self.path_for(key)
            if not path.is_file():
                return None
            data = path.read_bytes()
            return data or None
        except Exception:
            return None

    def put(self, key: VocalCacheKey, wav_bytes: bytes) -> None:
        if not vocal_cache_enabled() or not wav_bytes:
            return
        try:
            path = self.path_for(key)
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".tmp")
            tmp.write_bytes(wav_bytes)
            os.replace(tmp, path)
        except Exception:
            # Cache không được phép làm hỏng hot path TTS.
            return


def _safe_part(value: str) -> str:
    cleaned = "".join(ch if ch.isalnum() or ch in {"_", "-"} else "_" for ch in str(value or ""))
    cleaned = cleaned.strip("_")
    return cleaned[:48] or "unknown"


def strip_audible_prefix(text: str, prefix: str) -> str:
    """Tách prefix nghe được khỏi transcript nếu nó nằm ở đầu câu."""
    raw = str(text or "").strip()
    pfx = str(prefix or "").strip()
    if not raw or not pfx:
        return raw
    if raw.lower().startswith(pfx.lower()):
        return raw[len(pfx) :].lstrip()
    return raw


__all__ = [
    "VocalCache",
    "VocalCacheKey",
    "strip_audible_prefix",
    "vocal_cache_enabled",
]
