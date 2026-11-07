# -*- coding: utf-8 -*-
"""tts_audio_polish.py — hậu xử lý PCM cho giọng TTS.

Gemini TTS đôi khi trả audio có im lặng đầu/cuối hơi dài, hoặc pause giữa các
line dài hơn ý định của stage runtime.  Prompt chỉ "xin" model đọc đúng; module
này là lớp chặn kỹ thuật sau cùng: cắt im lặng thừa và cân âm lượng nhẹ trước
khi wrap thành WAV.

Thiết kế cố ý dùng stdlib, không phụ thuộc ffmpeg/numpy/pydub. Nếu polish lỗi,
caller nên dùng lại PCM gốc để TTS không bị câm.
"""

from __future__ import annotations

import array
import os
import sys
from dataclasses import dataclass


PCM_MAX = 32767
PCM_MIN = -32768


@dataclass(frozen=True, slots=True)
class AudioPolishStats:
    """Số liệu nhỏ để debug TTS mà không phải mở file audio."""

    original_ms: int
    polished_ms: int
    trimmed_head_ms: int
    trimmed_tail_ms: int
    compressed_pause_ms: int
    peak_before: int
    peak_after: int


@dataclass(frozen=True, slots=True)
class AudioPolishResult:
    pcm: bytes
    stats: AudioPolishStats


def audio_polish_enabled() -> bool:
    from runtime.runtime_settings_store import runtime_bool
    return runtime_bool("tts", "audio_polish", default=False)


def polish_pcm16_mono(
    pcm: bytes,
    *,
    sample_rate: int = 24000,
    silence_threshold: int | None = None,
    edge_keep_ms: int | None = None,
    max_internal_silence_ms: int | None = None,
    target_peak: float | None = None,
    max_gain: float | None = None,
) -> AudioPolishResult:
    """Cắt/nén silence và normalize nhẹ PCM 16-bit mono.

    - ``edge_keep_ms`` giữ lại một ít im lặng đầu/cuối để giọng không bị cụt.
    - ``max_internal_silence_ms`` nén các pause dài bên trong audio, xử lý đúng
      bệnh "đang nói rồi im lâu".
    - ``target_peak`` chỉ nâng âm lượng khi audio quá nhỏ; không cố master audio
      như nhạc, vì giọng anime breathy cần giữ dynamic.
    """
    if not pcm:
        return _empty_result()

    samples = _bytes_to_samples(pcm)
    if not samples:
        return _empty_result()

    threshold = _int_env("ERIS_TTS_SILENCE_THRESHOLD", silence_threshold or 120)
    edge_keep = _int_env("ERIS_TTS_EDGE_KEEP_MS", edge_keep_ms or 160)
    max_silence = _int_env("ERIS_TTS_MAX_INTERNAL_SILENCE_MS", max_internal_silence_ms or 900)
    target = _float_env("ERIS_TTS_TARGET_PEAK", target_peak if target_peak is not None else 0.72)
    gain_cap = _float_env("ERIS_TTS_MAX_GAIN", max_gain if max_gain is not None else 1.25)

    original_len = len(samples)
    peak_before = max(abs(int(x)) for x in samples) if samples else 0
    trimmed, head_cut, tail_cut = _trim_edges(samples, threshold, edge_keep, sample_rate)
    compressed, compressed_count = _compress_internal_silence(trimmed, threshold, max_silence, sample_rate)
    normalized = _normalize_peak(compressed, target_peak=target, max_gain=gain_cap)
    peak_after = max(abs(int(x)) for x in normalized) if normalized else 0

    stats = AudioPolishStats(
        original_ms=_samples_to_ms(original_len, sample_rate),
        polished_ms=_samples_to_ms(len(normalized), sample_rate),
        trimmed_head_ms=_samples_to_ms(head_cut, sample_rate),
        trimmed_tail_ms=_samples_to_ms(tail_cut, sample_rate),
        compressed_pause_ms=_samples_to_ms(compressed_count, sample_rate),
        peak_before=peak_before,
        peak_after=peak_after,
    )
    return AudioPolishResult(pcm=_samples_to_bytes(normalized), stats=stats)


def _empty_result() -> AudioPolishResult:
    stats = AudioPolishStats(
        original_ms=0,
        polished_ms=0,
        trimmed_head_ms=0,
        trimmed_tail_ms=0,
        compressed_pause_ms=0,
        peak_before=0,
        peak_after=0,
    )
    return AudioPolishResult(pcm=b"", stats=stats)


def _trim_edges(
    samples: array.array,
    threshold: int,
    keep_ms: int,
    sample_rate: int,
) -> tuple[array.array, int, int]:
    first = _first_voiced_index(samples, threshold)
    if first is None:
        return samples, 0, 0
    last = _last_voiced_index(samples, threshold)
    keep = _ms_to_samples(keep_ms, sample_rate)
    start = max(0, first - keep)
    end = min(len(samples), (last or first) + keep + 1)
    return samples[start:end], start, len(samples) - end


def _compress_internal_silence(
    samples: array.array,
    threshold: int,
    max_silence_ms: int,
    sample_rate: int,
) -> tuple[array.array, int]:
    """Nén pause dài bên trong audio, nhưng không xoá hết nhịp thở."""
    limit = _ms_to_samples(max_silence_ms, sample_rate)
    if limit <= 0 or len(samples) <= limit:
        return samples, 0

    out = array.array("h")
    silence_run: list[int] = []
    dropped = 0
    seen_voice = False

    for sample in samples:
        value = int(sample)
        if abs(value) <= threshold:
            if seen_voice:
                silence_run.append(value)
            else:
                out.append(value)
            continue

        if silence_run:
            keep = silence_run[:limit]
            out.extend(keep)
            dropped += max(0, len(silence_run) - len(keep))
            silence_run.clear()
        out.append(value)
        seen_voice = True

    # Silence cuối đã được edge trim trước đó; vẫn giữ tối đa limit để không cụt.
    if silence_run:
        keep = silence_run[:limit]
        out.extend(keep)
        dropped += max(0, len(silence_run) - len(keep))
    return out, dropped


def _normalize_peak(samples: array.array, *, target_peak: float, max_gain: float) -> array.array:
    if not samples:
        return samples
    peak = max(abs(int(x)) for x in samples)
    if peak <= 0:
        return samples
    target_abs = max(1, min(PCM_MAX, int(PCM_MAX * max(0.1, min(0.98, target_peak)))))
    gain = min(max_gain, target_abs / float(peak))
    if gain <= 1.03:
        return samples

    out = array.array("h")
    for sample in samples:
        scaled = int(round(int(sample) * gain))
        out.append(max(PCM_MIN, min(PCM_MAX, scaled)))
    return out


def _first_voiced_index(samples: array.array, threshold: int) -> int | None:
    for index, sample in enumerate(samples):
        if abs(int(sample)) > threshold:
            return index
    return None


def _last_voiced_index(samples: array.array, threshold: int) -> int | None:
    for index in range(len(samples) - 1, -1, -1):
        if abs(int(samples[index])) > threshold:
            return index
    return None


def _bytes_to_samples(pcm: bytes) -> array.array:
    usable = pcm[: len(pcm) - (len(pcm) % 2)]
    samples = array.array("h")
    samples.frombytes(usable)
    if sys.byteorder != "little":
        samples.byteswap()
    return samples


def _samples_to_bytes(samples: array.array) -> bytes:
    out = array.array("h", samples)
    if sys.byteorder != "little":
        out.byteswap()
    return out.tobytes()


def _ms_to_samples(ms: int, sample_rate: int) -> int:
    return max(0, int(sample_rate * max(0, ms) / 1000))


def _samples_to_ms(count: int, sample_rate: int) -> int:
    if sample_rate <= 0:
        return 0
    return int(round((max(0, count) / float(sample_rate)) * 1000))


def _int_env(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return int(default)
    try:
        return int(raw)
    except ValueError:
        return int(default)


def _float_env(name: str, default: float) -> float:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return float(default)
    try:
        return float(raw)
    except ValueError:
        return float(default)


__all__ = [
    "AudioPolishResult",
    "AudioPolishStats",
    "audio_polish_enabled",
    "polish_pcm16_mono",
]
