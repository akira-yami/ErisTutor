# -*- coding: utf-8 -*-
"""stage_output.py — transformer chain nhỏ cho output sân khấu.

Sau StageFrame đầu tiên, câu vẫn có thể quá dài để TTS nghe tự nhiên.  Người
thật thường nói thành mấy nhịp ngắn: "Khoan." rồi mới tới câu chính.  Module
này tách ``StageFrame`` thành nhiều segment TTS, nhưng vẫn giữ cùng ``turn_id``.

Đây là transformer thuần dữ liệu: không gọi TTS, không gửi UI, không chạm memory.
"""

from __future__ import annotations

import os
import re
from dataclasses import replace

from runtime.prosody_director import ProsodyDirector
from runtime.stage_contracts import StageFrame
from runtime.voice_director import vocalization_text


_SENTENCE_BOUNDARY_RE = re.compile(r"(?<=[。！？])\s*(?=\S)|(?<=[.!?])\s+")


def _enabled() -> bool:
    from runtime.stage_profiles import active_stage_profile
    return active_stage_profile().logical_segment_tts


def _max_segments(profile: object | None = None) -> int:
    if profile is None:
        from runtime.stage_profiles import active_stage_profile
        profile = active_stage_profile()
    return max(1, min(16, int(getattr(profile, "max_tts_segments", 8) or 8)))


def _min_split_chars(profile: object | None = None) -> int:
    if profile is None:
        from runtime.stage_profiles import active_stage_profile
        profile = active_stage_profile()
    return max(24, min(120, int(getattr(profile, "min_split_chars", 58) or 58)))


def _provider_transcript_limit() -> int:
    from runtime.tts_budget import GLOBAL_TTS_REQUEST_BUDGET
    try:
        from runtime.tts_engine import _tts_provider
        provider = _tts_provider()
    except Exception:
        provider = ""
    return GLOBAL_TTS_REQUEST_BUDGET.transcript_limit(provider)


def _max_segment_chars() -> int:
    from runtime.runtime_settings_store import runtime_int
    try:
        from runtime.tts_engine import _tts_provider
        provider = _tts_provider()
    except Exception:
        provider = ""

    hard_limit = _provider_transcript_limit()
    configured = max(48, min(1200, runtime_int("stage", "max_tts_segment_chars", default=88)))

    # 88 ký tự hợp với Gemini nhưng quá vụn với ElevenLabs. Khi buộc phải chia
    # một turn > hard-limit, dùng chunk lớn hơn để giảm số request mạng và giữ
    # nhịp nói. Giá trị panel lớn hơn vẫn được tôn trọng.
    if str(provider).strip().lower() == "elevenlabs":
        configured = max(configured, min(420, hard_limit))
    return min(configured, hard_limit)


def _split_long_piece(text: str, max_chars: int) -> list[str]:
    """Tách câu dài không dấu chấm theo clause/word, không làm mất ký tự."""
    source = " ".join(str(text or "").split()).strip()
    if not source or len(source) <= max_chars:
        return [source] if source else []

    chunks: list[str] = []
    rest = source
    while len(rest) > max_chars:
        window = rest[: max_chars + 1]
        cut = -1
        # Ưu tiên dấu ngắt tự nhiên, sau đó mới tới khoảng trắng.
        for marker in ("; ", ": ", ", ", " — ", " - ", " "):
            pos = window.rfind(marker)
            if pos >= int(max_chars * 0.55):
                cut = pos + (len(marker.rstrip()) if marker.strip() else 0)
                break
        if cut <= 0:
            cut = max_chars
        piece = rest[:cut].strip()
        if not piece:
            piece = rest[:max_chars].strip()
            cut = max_chars
        chunks.append(piece)
        rest = rest[cut:].strip()
    if rest:
        chunks.append(rest)
    return chunks


def _rebalance_to_limit(chunks: list[str], max_segments: int) -> list[str]:
    """Gộp cân bằng khi quá nhiều chunk, tránh dồn toàn bộ phần dư vào câu cuối."""
    if len(chunks) <= max_segments:
        return chunks
    total_chars = sum(len(item) for item in chunks) + max(0, len(chunks) - 1)
    groups: list[str] = []
    cursor = 0
    remaining_chars = total_chars
    while cursor < len(chunks) and len(groups) < max_segments:
        groups_left = max_segments - len(groups)
        chunks_left = len(chunks) - cursor
        if groups_left == 1:
            groups.append(" ".join(chunks[cursor:]).strip())
            break
        target = max(1, remaining_chars // groups_left)
        current: list[str] = []
        current_len = 0
        # Luôn để lại ít nhất một chunk cho mỗi group còn lại.
        while cursor < len(chunks) - (groups_left - 1):
            candidate = chunks[cursor]
            added = len(candidate) + (1 if current else 0)
            if current and current_len + added > target:
                break
            current.append(candidate)
            current_len += added
            cursor += 1
        if not current:
            current.append(chunks[cursor])
            current_len = len(chunks[cursor])
            cursor += 1
        groups.append(" ".join(current).strip())
        remaining_chars = max(0, remaining_chars - current_len - 1)
    return groups


class SentenceSegmenter:
    """Chia text TTS thành các câu ngắn, không đụng vào text UI."""

    def __init__(self, *, profile: object | None = None) -> None:
        self.profile = profile

    def split(self, text: str) -> tuple[str, ...]:
        clean = " ".join(str(text or "").split()).strip()
        if not clean:
            return ()
        if len(clean) < _min_split_chars(self.profile):
            return (clean,)

        # Chỉ tách khi dấu kết câu được theo sau bởi whitespace. Vì vậy dấu
        # chấm trong 3.5, v1.2.3, URL hoặc tên file không bị coi là hết câu.
        parts = [part.strip() for part in _SENTENCE_BOUNDARY_RE.split(clean) if part.strip()]
        if not parts:
            return ()

        # Gộp câu quá ngắn vào câu trước để tránh TTS phát mẩu vụn.
        merged: list[str] = []
        for part in parts:
            if merged and len(part) < 12:
                merged[-1] = f"{merged[-1]} {part}".strip()
            else:
                merged.append(part)

        # Một câu không có dấu chấm vẫn có thể dài hàng nghìn ký tự. Tách tiếp
        # theo clause/word trước khi chạm provider, thay vì để TTS drift rồi đổ
        # cho model vì con người đã viết một đoạn văn như biên bản họp.
        hard_chunks: list[str] = []
        for part in merged:
            hard_chunks.extend(_split_long_piece(part, _max_segment_chars()))

        balanced = _rebalance_to_limit(hard_chunks, _max_segments(self.profile))
        # max_segments là preference sân khấu, không phải giấy phép vượt hard TTS
        # budget. Nếu rebalance gộp quá tay, tách lại và chấp nhận nhiều segment
        # hơn thay vì gửi một transcript provider chắc chắn từ chối.
        hard_limit = _max_segment_chars()
        safe: list[str] = []
        for item in balanced:
            safe.extend(_split_long_piece(item, hard_limit))
        return tuple(item for item in safe if item)


class StageOutputPipeline:
    """Base StageFrame -> tuple[StageFrame, ...]."""

    def __init__(self, *, profile: object | None = None) -> None:
        if isinstance(profile, str):
            from runtime.stage_profiles import stage_profile
            profile = stage_profile(profile)
        self.profile = profile
        self.segmenter = SentenceSegmenter(profile=profile)
        self.prosody = ProsodyDirector()

    def _profile(self):
        if self.profile is not None:
            return self.profile
        from runtime.stage_profiles import active_stage_profile
        return active_stage_profile()

    @staticmethod
    def _segment_delivery(delivery: dict[str, object], *, index: int, count: int) -> dict[str, object]:
        """Giữ pause ở cuối beat, không lặp pause sau từng câu con.

        ``SentenceSegmenter`` có thể tách một beat JSON thành nhiều TTS segment.
        Beat vẫn là đơn vị đạo diễn, vì thế pause/thinking chỉ thuộc segment cuối.
        Metadata của beat vẫn được giữ trên mọi segment để UI biết chúng
        thuộc cùng một nhịp autonomous.
        """

        copied = dict(delivery or {})
        voice_segment = copied.get("voice_director_segment")
        if isinstance(voice_segment, dict) and index > 0:
            voice_segment = dict(voice_segment)
            voice_segment["vocalization"] = "none"
            copied["voice_director_segment"] = voice_segment
        active = copied.get("active_beat")
        if isinstance(active, dict):
            active = dict(active)
            if index < count - 1:
                active["pause_after_ms"] = 0
                active["pause_jitter_ms"] = 0
            copied["active_beat"] = active
        return copied

    def _voice_director_frames(self, frame: StageFrame) -> tuple[StageFrame, ...]:
        """Áp Voice Director lên toàn bộ câu mà không tạo segment mới.

        Kế hoạch v2 chỉ mang delivery metadata. Transcript, display text và
        memory text giữ nguyên; TTS luôn nhận đúng một StageFrame/clip.
        """

        plan = frame.voice_plan if isinstance(getattr(frame, "voice_plan", None), dict) else {}
        if not plan:
            return ()
        delivery_raw = plan.get("delivery")
        if not isinstance(delivery_raw, dict):
            # Compatibility với kế hoạch cũ chỉ khi đúng một segment. Nhiều hơn
            # bị bỏ qua để không tái sinh lỗi ngắt/drop.
            legacy = plan.get("segments")
            if not isinstance(legacy, list) or len(legacy) != 1 or not isinstance(legacy[0], dict):
                return ()
            delivery_raw = legacy[0]

        voice_meta = dict(delivery_raw)
        voice_meta["pause_after_ms"] = 0
        cue = str(voice_meta.get("vocalization", "none") or "none").strip().lower()
        if cue not in {"none", "soft_hum", "gentle_ack", "small_laugh", "soft_sigh", "curious_hm", "soft_surprise"}:
            cue = "none"
        voice_meta["vocalization"] = cue
        cue_position = str(voice_meta.get("vocalization_position", "prefix") or "prefix").strip().lower()
        voice_meta["vocalization_position"] = cue_position if cue_position in {"prefix", "suffix"} else "prefix"
        voice_meta["previous_text"] = ""
        voice_meta["next_text"] = ""

        style = str(voice_meta.get("style", frame.prosody_style) or frame.prosody_style)
        emphasis = [
            str(item or "").strip()
            for item in (voice_meta.get("emphasis") or ())
            if str(item or "").strip() and str(item or "").strip() in str(frame.tts_text or "")
        ][:2]
        try:
            warmth = max(0.35, min(0.85, float(voice_meta.get("warmth", 0.62) or 0.62)))
        except (TypeError, ValueError):
            warmth = 0.62

        delivery = dict(frame.delivery) if isinstance(frame.delivery, dict) else {}
        delivery["voice_director_segment"] = voice_meta
        # Không tạo active_beat, pause hay performance_act. Đạo diễn chỉ chỉnh
        # chất giọng; timeline và câu chữ không thuộc quyền hạn của nó.

        notes = str(frame.prosody_notes or "").strip()
        notes = (
            f"{notes} Single continuous take. Warmth={warmth:.2f}; "
            f"energy={voice_meta.get('energy', 0.42)}; pitch={voice_meta.get('pitch', 'neutral')}; "
            f"speed={voice_meta.get('speed', 1.0)}. Keep it smooth, close, warm and natural; "
            "no dramatic pause and no whisper."
        ).strip()
        if cue != "none":
            notes = (
                f"{notes} Add exactly one subtle {cue} at the "
                f"{voice_meta.get('vocalization_position', 'prefix')} inside the same continuous take; "
                "do not create a separate clip or dead-air pause."
            ).strip()
        if emphasis:
            notes = f"{notes} Lightly emphasize: {', '.join(emphasis)}.".strip()
        prosody = self.prosody.plan(
            frame.tts_text,
            emotion=frame.emotion,
            style_hint=style,
            notes_hint=notes,
        )
        return (
            replace(
                frame,
                segment_id=0,
                prosody_style=prosody.style,
                prosody_notes=prosody.director_notes,
                delivery=delivery,
            ),
        )

    def frames_for_tts(self, frame: StageFrame) -> tuple[StageFrame, ...]:
        # Một audio preset đã render ứng với toàn bộ câu. Tách segment sẽ làm
        # metadata file WAV lệch khỏi transcript và phát cùng file nhiều lần.
        if str(getattr(frame, "audio_asset", "") or "").strip():
            return (frame,)

        directed = self._voice_director_frames(frame)
        if directed:
            hard_limit = _provider_transcript_limit()
            if all(len(str(item.tts_text or "")) <= hard_limit for item in directed):
                return directed
            # Voice Director chỉ đổi delivery metadata. Nếu transcript vượt hard
            # provider budget, giữ metadata đó rồi tiếp tục qua sentence splitter
            # thay vì trả sớm một frame mà ElevenLabs chắc chắn từ chối.
            frame = directed[0]

        # Autonomous/opening act đã có speech beat do Stage Director đặt. Ưu
        # tiên beat này hơn sentence splitter tổng quát để điểm rơi không bị chia
        # lại lần hai. Riêng ElevenLabs, chỉ opening typed mới tách segment; các
        # turn thường giữ một request để không làm tăng latency vô cớ.
        act = frame.stage_act if isinstance(getattr(frame, "stage_act", None), dict) else {}
        beats = act.get("beats") if isinstance(act, dict) else None
        speech_beats = [
            beat for beat in (beats or [])
            if isinstance(beat, dict) and str(beat.get("kind", "")) == "speech" and str(beat.get("text", "")).strip()
        ]
        from runtime.tts_engine import _tts_provider
        explicit_opening = bool(
            speech_beats
            and str(act.get("opportunity_kind", "")).strip().lower() == "startup"
            and str(act.get("cadence", "")).strip().lower() == "opening_directed"
        )
        if (
            _tts_provider() == "elevenlabs"
            and not explicit_opening
            and len(str(frame.tts_text or "")) <= _provider_transcript_limit()
        ):
            return (frame,)
        if speech_beats:
            frames: list[StageFrame] = []
            segment_index = 0
            for beat in speech_beats:
                beat_text = str(beat.get("text", "") or "").strip()
                beat_display = str(beat.get("display_text", "") or "").strip()
                beat_parts = self.segmenter.split(beat_text) or ((beat_text,) if beat_text else ())
                display_parts = self.segmenter.split(beat_display) if beat_display else ()
                for part_index, text in enumerate(beat_parts):
                    delivery = dict(frame.delivery) if isinstance(frame.delivery, dict) else {}
                    active_beat = dict(beat)
                    if part_index < len(beat_parts) - 1:
                        active_beat["pause_after_ms"] = 0
                        active_beat["pause_jitter_ms"] = 0
                    delivery["active_beat"] = active_beat
                    pause_after = int(beat.get("pause_after_ms", 0) or 0) if part_index == len(beat_parts) - 1 else 0
                    notes = frame.prosody_notes
                    if 0 < pause_after <= 600:
                        notes = f"{notes} End this beat with a short {pause_after}ms rhetorical pause.".strip()
                    elif pause_after > 600:
                        # Pause dài do StageContinuationRuntime thực thi sau audio_done;
                        # không bảo model TTS tự kéo im lặng trong cùng file audio.
                        notes = f"{notes} End this beat cleanly; do not synthesize a long trailing silence.".strip()
                    plan = self.prosody.plan(
                        text,
                        emotion=str(beat.get("emotion", frame.emotion) or frame.emotion),
                        style_hint=str(beat.get("tts_style", frame.prosody_style) or frame.prosody_style),
                        notes_hint=notes,
                    )
                    # Khi số mảnh display và speech không khớp (dịch Nhật có
                    # thể đổi nhịp), dùng nguyên bubble Việt cho mảnh đầu và để
                    # các mảnh sau không ghi đè UI/memory bằng transcript Nhật.
                    # Chỉ map theo index khi số phân đoạn khớp. Dịch Việt→Nhật
                    # có thể thêm/bớt câu: ghép index mù sẽ để mất phần chữ
                    # Việt ở event stage. Khi lệch, phát nguyên display một lần.
                    display_text = (
                        display_parts[part_index]
                        if len(display_parts) == len(beat_parts)
                        else beat_display if part_index == 0 else ""
                    )
                    frames.append(
                        replace(
                            frame,
                            segment_id=segment_index,
                            display_text=display_text,
                            tts_text=text,
                            memory_text=display_text,
                            emotion=str(beat.get("emotion", frame.emotion) or frame.emotion),
                            motion=str(beat.get("motion_intent", frame.motion) or frame.motion),
                            prosody_style=plan.style,
                            prosody_notes=plan.director_notes,
                            delivery=delivery,
                            body_timeline=frame.body_timeline if segment_index == 0 else {},
                        )
                    )
                    segment_index += 1
            return tuple(frames)

        profile = self._profile()
        if not bool(getattr(profile, "logical_segment_tts", True)):
            return (frame,)
        segments = self.segmenter.split(frame.tts_text)
        if len(segments) <= 1:
            return (frame,)

        # display_text / memory_text là surface riêng với TTS. Đặc biệt khi
        # bubble dùng tiếng Việt còn giọng dùng tiếng Nhật, tuyệt đối không lấy
        # transcript TTS để ghi đè UI/memory chỉ vì TTS vừa bị chia segment.
        display_segments = self.segmenter.split(frame.display_text)
        memory_segments = self.segmenter.split(frame.memory_text)

        frames: list[StageFrame] = []
        for index, segment in enumerate(segments):
            plan = self.prosody.plan(
                segment,
                emotion=frame.emotion,
                style_hint=frame.prosody_style,
                notes_hint=frame.prosody_notes,
            )
            # VI/JP khác số câu không có nghĩa tương đương theo index.
            # Đừng tự nhận cặp bất kỳ là aligned. Giữ toàn văn Việt/memory
            # ở segment đầu, còn playback vẫn chia đủ số segment Nhật.
            display_text = (
                display_segments[index]
                if len(display_segments) == len(segments)
                else frame.display_text if index == 0 else ""
            )
            memory_text = (
                memory_segments[index]
                if len(memory_segments) == len(segments)
                else frame.memory_text if index == 0 else ""
            )
            frames.append(
                replace(
                    frame,
                    segment_id=index,
                    display_text=display_text,
                    tts_text=segment,
                    memory_text=memory_text,
                    prosody_style=plan.style,
                    prosody_notes=plan.director_notes,
                    delivery=self._segment_delivery(frame.delivery, index=index, count=len(segments)),
                    body_timeline=frame.body_timeline if index == 0 else {},
                )
            )
        return tuple(frames)


__all__ = ["SentenceSegmenter", "StageOutputPipeline"]
