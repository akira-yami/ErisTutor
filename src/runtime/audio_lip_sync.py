# -*- coding: utf-8 -*-
"""Extract jaw timing and an optional spectral shape hint from playback PCM."""

from __future__ import annotations

import io
import math
import sys
import wave
from array import array
from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True, slots=True)
class LipSyncEnvelope:
    values: tuple[float, ...] = ()
    frame_ms: int = 20
    duration_ms: int = 0
    shape: tuple[float, ...] = ()

    def to_payload(self) -> dict[str, object]:
        if not self.values:
            return {}
        return {
            "audio_envelope": list(self.values),
            "audio_frame_ms": int(self.frame_ms),
            "audio_duration_ms": int(self.duration_ms),
            **({"audio_mouth_shape": list(self.shape)} if self.shape else {}),
        }


def _percentile(values: list[float], ratio: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    position = max(0.0, min(1.0, float(ratio))) * (len(ordered) - 1)
    lower = int(math.floor(position))
    upper = min(len(ordered) - 1, lower + 1)
    fraction = position - lower
    return ordered[lower] + (ordered[upper] - ordered[lower]) * fraction


def _pcm_samples(raw: bytes, *, sample_width: int) -> tuple[list[int], float]:
    if sample_width == 1:
        # WAV 8-bit PCM là unsigned.
        return ([int(value) - 128 for value in raw], 128.0)
    if sample_width == 2:
        samples = array("h")
        samples.frombytes(raw)
        if sys.byteorder != "little":
            samples.byteswap()
        return (list(samples), 32768.0)
    if sample_width == 4:
        samples = array("i")
        samples.frombytes(raw)
        if sys.byteorder != "little":
            samples.byteswap()
        return (list(samples), 2147483648.0)
    if sample_width == 3:
        result: list[int] = []
        for offset in range(0, len(raw) - 2, 3):
            value = raw[offset] | (raw[offset + 1] << 8) | (raw[offset + 2] << 16)
            if value & 0x800000:
                value -= 1 << 24
            result.append(value)
        return (result, 8388608.0)
    return ([], 1.0)


def build_lip_sync_envelope(
    wav_bytes: bytes,
    *,
    frame_ms: int = 20,
    max_frames: int = 1800,
) -> LipSyncEnvelope:
    """Trả envelope 0..0.86 từ WAV; dữ liệu lạ thì fail mềm về envelope rỗng."""

    if not wav_bytes:
        return LipSyncEnvelope(frame_ms=max(20, int(frame_ms)))
    try:
        with wave.open(io.BytesIO(wav_bytes), "rb") as stream:
            channels = max(1, int(stream.getnchannels()))
            sample_rate = max(1, int(stream.getframerate()))
            sample_width = int(stream.getsampwidth())
            frame_count = max(0, int(stream.getnframes()))
            compression = str(stream.getcomptype() or "NONE")
            if compression != "NONE":
                return LipSyncEnvelope(frame_ms=max(20, int(frame_ms)))
            raw = stream.readframes(frame_count)
    except (wave.Error, EOFError, OSError, ValueError):
        return LipSyncEnvelope(frame_ms=max(20, int(frame_ms)))

    pcm_frame_bytes = sample_width * channels
    raw = raw[:len(raw) - len(raw) % pcm_frame_bytes]
    samples, full_scale = _pcm_samples(raw, sample_width=sample_width)
    if not samples or full_scale <= 0.0:
        return LipSyncEnvelope(frame_ms=max(20, int(frame_ms)))

    frame_ms = max(20, min(80, int(frame_ms)))
    max_frames = max(1, min(1800, int(max_frames)))
    samples_per_window = max(1, int(round(sample_rate * frame_ms / 1000.0))) * channels
    stride = max(1, math.ceil(len(samples) / (samples_per_window * max_frames)))
    samples_per_window *= stride
    frame_ms *= stride
    rms_values: list[float] = []
    shape_values: list[float] = []
    # A spectral hint for narrower vowel shapes, not phoneme recognition.
    # Ignore the fundamental region and compare the lower/upper resonances.
    window_frames = samples_per_window // channels
    use_shape = sample_rate >= 8000 and frame_ms <= 80
    if use_shape:
        taper = np.hanning(window_frames)
        frequencies = np.fft.rfftfreq(window_frames, 1.0 / sample_rate)
        resonance = (frequencies >= 650) & (frequencies <= 3200)
        lower = resonance & (frequencies < 1450)
    for start in range(0, len(samples), samples_per_window):
        block = samples[start : start + samples_per_window]
        if not block:
            break
        # Stereo/multichannel đều có thể dùng RMS chung; miệng quan tâm năng lượng,
        # không cần tranh luận bên tai nào có quyền biểu quyết lớn hơn.
        square_sum = sum(float(value) * float(value) for value in block)
        rms_values.append(math.sqrt(square_sum / len(block)) / full_scale)
        if use_shape:
            mono = np.asarray(block, dtype=float).reshape(-1, channels).mean(axis=1)
            emphasized = mono - .94 * np.r_[mono[0], mono[:-1]]
            power = np.abs(np.fft.rfft(emphasized * taper[:len(mono)], n=window_frames)) ** 2
            total = float(power[resonance].sum())
            low_share = float(power[lower].sum()) / total if total > 0 else 0.0
            enough_resonance = total > .025 * float(power.sum())
            shape_values.append(max(0.0, min(1.0, (low_share - .50) / .30)) if enough_resonance else 0.0)

    if not rms_values:
        return LipSyncEnvelope(frame_ms=frame_ms)

    peak = max(_percentile(rms_values, 0.94), max(rms_values) * .30, .004)
    # A short clip may be voiced throughout. Its quietest syllable is not
    # necessarily background noise and must not flatten a sustained vowel.
    gate = max(.001, min(_percentile(rms_values, .16) * 1.18, peak * .26))
    span = max(.003, peak - gate)

    normalized: list[float] = []
    for value in rms_values:
        gated = max(0.0, (value - gate) / span)
        # Căn bậc làm lời nói nhỏ vẫn thấy được; cap giữ rig không há quá tay.
        mouth = min(0.86, (min(1.35, gated) ** 0.58) * 0.78)
        normalized.append(mouth if mouth >= 0.035 else 0.0)

    # Attack nhanh, release vừa phải. Phần đóng nhanh cuối cùng còn được xử lý ở
    # stage_state để khe giữa âm tiết không bị smoothing nuốt mất.
    smoothed: list[float] = []
    current = 0.0
    for target in normalized:
        if target == 0.0:
            current = 0.0
        else:
            coefficient = 0.80 if target > current else 0.62
            current += (target - current) * coefficient
        smoothed.append(round(max(0.0, min(0.86, current)), 3))

    duration_ms = int(round(len(samples) / channels / sample_rate * 1000.0))
    shape = tuple(
        round(value * .75, 2) if opening > 0 else 0.0
        for value, opening in zip(shape_values, smoothed, strict=False)
    )
    return LipSyncEnvelope(tuple(smoothed), frame_ms, duration_ms, shape if any(shape) else ())


__all__ = ["LipSyncEnvelope", "build_lip_sync_envelope"]
