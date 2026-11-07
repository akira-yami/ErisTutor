"""Playback lifecycle follows PCM submission, drain, cancellation and WAV energy."""

import io
import queue
import struct
import sys
import threading
import wave
from types import SimpleNamespace

import pytest

from runtime import tts_engine
from runtime.prosody_director import ProsodyDirector


def _wav():
    output = io.BytesIO()
    with wave.open(output, "wb") as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(1000)
        stream.writeframes(struct.pack("<120h", *([0] * 40 + [16384] * 40 + [0] * 40)))
    return output.getvalue()


def _sounddevice(monkeypatch, trace, *, on_enter=None, on_write=None, submitted=None):
    # Only hardware I/O is replaced: production WAV decoding and playback remain real.
    class Stream:
        def __init__(self, *, samplerate, channels, dtype):
            assert (samplerate, channels, dtype) == (1000, 1, "float32")
            self.aborted = False

        def __enter__(self):
            if on_enter:
                on_enter()
            return self

        def write(self, frames):
            assert len(frames) > 0
            if submitted is not None:
                submitted.extend(float(value) for value in frames)
            trace.append("pcm")
            if on_write:
                on_write()

        def abort(self):
            self.aborted = True
            trace.append("aborted")

        def __exit__(self, *_args):
            if not self.aborted:
                trace.append("drained")

    monkeypatch.setitem(sys.modules, "sounddevice", SimpleNamespace(OutputStream=Stream))
    monkeypatch.setattr(tts_engine, "_output_volume", lambda: 1.0)


def test_start_is_emitted_after_first_pcm_and_completion_after_drain(monkeypatch):
    trace = []
    _sounddevice(monkeypatch, trace)
    tts_engine._play_wav_bytes(_wav(), threading.Event(), on_started=lambda: trace.append("started"))
    trace.append("returned")

    assert trace[0:2] == ["pcm", "started"]
    assert trace.count("started") == 1
    assert trace[-2:] == ["drained", "returned"]


def test_cancel_before_first_pcm_does_not_emit_start(monkeypatch):
    stop = threading.Event()
    trace = []
    _sounddevice(monkeypatch, trace, on_enter=stop.set)

    tts_engine._play_wav_bytes(_wav(), stop, on_started=lambda: trace.append("started"))

    assert "started" not in trace
    assert "pcm" not in trace


@pytest.mark.parametrize("backend", ["winsound", "miniaudio"])
def test_fallback_cancel_before_start_does_not_touch_device(monkeypatch, backend):
    stop = threading.Event()
    stop.set()
    trace = []
    if backend == "winsound":
        monkeypatch.setitem(sys.modules, "winsound", SimpleNamespace(
            PlaySound=lambda *_args: trace.append("device"), SND_MEMORY=1, SND_ASYNC=2, SND_NODEFAULT=4,
        ))
        tts_engine._play_wav_winsound(_wav(), stop, on_started=lambda: trace.append("started"))
    else:
        class Device:
            def __init__(self, **_kwargs):
                trace.append("device")

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return None

            def start(self, _stream):
                trace.append("device-start")

            def is_active(self):
                return False

        monkeypatch.setitem(sys.modules, "miniaudio", SimpleNamespace(
            decode=lambda *_args: trace.append("device") or SimpleNamespace(
                nchannels=1, sample_rate=1000, samples=bytes(240),
            ),
            PlaybackDevice=Device, SampleFormat=SimpleNamespace(SIGNED16=1),
            stream_with_callbacks=lambda *_args, **_kwargs: iter(()),
        ))
        tts_engine._play_wav_miniaudio(_wav(), stop, on_started=lambda: trace.append("started"))

    assert trace == []


def test_cancellation_aborts_pending_audio_instead_of_draining(monkeypatch):
    stop = threading.Event()
    trace = []
    _sounddevice(monkeypatch, trace)

    def started():
        trace.append("started")
        stop.set()

    tts_engine._play_wav_bytes(_wav(), stop, on_started=started)

    assert trace == ["pcm", "started", "aborted"]


def _legacy_worker(monkeypatch, events, *, volume=1.0, play=None):
    worker = tts_engine._TtsWorker.__new__(tts_engine._TtsWorker)
    worker._queue = queue.Queue()
    worker._stop_event = threading.Event()
    worker._current_stop = threading.Event()
    worker._current_lock = threading.RLock()
    worker._current_req = None
    worker._epoch_validator = None
    worker._voice = "test-voice"
    worker._model = "test-model"
    worker._language_hint = "ja"
    worker._prosody = ProsodyDirector()
    monkeypatch.setattr(worker, "_resolve_main_transcript", lambda *_args, **_kwargs: ("hello", True, "ja"))
    monkeypatch.setattr(tts_engine, "_synthesize_active_provider_wav", lambda *_args, **_kwargs: _wav())
    monkeypatch.setattr(tts_engine, "_output_volume", lambda: volume)
    if play:
        monkeypatch.setattr(tts_engine, "_play_wav_bytes", play)
    worker._queue.put(tts_engine._SpeakRequest("hello", prelocalized=True, on_event=events.append))
    worker._queue.put(None)
    return worker


@pytest.mark.parametrize("volume", [1.0, 0.0])
def test_legacy_audio_start_carries_actual_wav_energy_and_duration(monkeypatch, volume):
    events = []

    def play(_wav_bytes, _stop, on_started, *, volume_applied=False):
        on_started()

    worker = _legacy_worker(monkeypatch, events, volume=volume, play=play)
    worker._run()
    start = next(event for event in events if event.type == "audio_start")

    assert getattr(start, "audio_frame_ms", None) == 20
    assert getattr(start, "audio_duration_ms", None) == 120
    envelope = getattr(start, "audio_envelope", ())
    assert isinstance(envelope, tuple)
    assert len(envelope) == 6
    assert envelope[:2] == envelope[-2:] == (0.0, 0.0)
    if volume:
        assert min(envelope[2:4]) > 0.5
    else:
        assert envelope == (0.0,) * 6
    assert [event.type for event in events] == ["audio_start", "audio_done"]


def test_legacy_start_callback_is_idempotent(monkeypatch):
    events = []

    def play(_wav_bytes, _stop, on_started, *, volume_applied=False):
        on_started()
        on_started()

    worker = _legacy_worker(monkeypatch, events, play=play)
    worker._run()

    assert [event.type for event in events] == ["audio_start", "audio_done"]


def test_legacy_cancelled_before_start_never_announces_playback(monkeypatch):
    events = []

    def play(_wav_bytes, stop, on_started, *, volume_applied=False):
        stop.set()
        on_started()

    worker = _legacy_worker(monkeypatch, events, play=play)
    worker._run()

    assert [event.type for event in events] == ["audio_cancelled"]


def test_legacy_without_device_start_reports_failure_and_unblocks_ui(monkeypatch):
    events = []
    visible = []
    worker = _legacy_worker(monkeypatch, events, play=lambda *_args, **_kwargs: None)
    request = worker._queue.queue[0]
    request.on_start_play = lambda: visible.append("visible")

    worker._run()

    assert [event.type for event in events] == ["audio_failed"]
    assert events[0].reason == "playback_not_started"
    assert visible == ["visible"]


def test_legacy_superseded_during_playback_does_not_report_done(monkeypatch):
    events = []
    current = True

    def play(_wav_bytes, _stop, on_started, *, volume_applied=False):
        nonlocal current
        on_started()
        current = False

    worker = _legacy_worker(monkeypatch, events, play=play)
    worker._epoch_validator = lambda *_args: current
    worker._run()

    assert [event.type for event in events] == ["audio_start", "audio_cancelled"]


def test_backend_failure_after_start_does_not_replay_audio(monkeypatch):
    trace = []
    writes = 0

    def write():
        nonlocal writes
        writes += 1
        if writes == 2:
            raise RuntimeError("device disconnected")

    _sounddevice(monkeypatch, trace, on_write=write)
    monkeypatch.setattr(tts_engine, "_play_wav_winsound", lambda *_args, **_kwargs: trace.append("fallback") or True)
    with pytest.raises(RuntimeError, match="device disconnected"):
        tts_engine._play_wav_bytes(_wav(), threading.Event(), on_started=lambda: trace.append("started"))

    assert trace.count("started") == 1
    assert "fallback" not in trace


def test_winsound_failure_after_start_does_not_replay_audio(monkeypatch):
    trace = []
    # sounddevice unavailable, then winsound reports a failure after submission.
    monkeypatch.setitem(sys.modules, "sounddevice", None)
    monkeypatch.setattr(tts_engine, "_output_volume", lambda: 1.0)

    def winsound(_wav_bytes, _stop, on_started):
        on_started()
        return False

    monkeypatch.setattr(tts_engine, "_play_wav_winsound", winsound)
    monkeypatch.setattr(tts_engine, "_play_wav_miniaudio", lambda *_args, **_kwargs: trace.append("fallback"))

    with pytest.raises(RuntimeError, match="playback_failed"):
        tts_engine._play_wav_bytes(_wav(), threading.Event(), on_started=lambda: trace.append("started"))

    assert trace == ["started"]


def test_lip_sync_audio_reflects_muted_output(monkeypatch):
    monkeypatch.setattr(tts_engine, "_output_volume", lambda: 0.0)
    engine = tts_engine.TtsEngine.__new__(tts_engine.TtsEngine)
    prepare = getattr(engine, "lip_sync_audio", None)
    assert callable(prepare)

    with wave.open(io.BytesIO(prepare(_wav())), "rb") as stream:
        assert stream.getnframes() == 120
        assert stream.readframes(120) == bytes(240)


def test_legacy_volume_snapshot_is_shared_by_envelope_and_submitted_pcm(monkeypatch):
    events = []
    submitted = []
    volume_reads = []
    _sounddevice(monkeypatch, [], submitted=submitted)
    worker = _legacy_worker(monkeypatch, events)

    def changing_volume():
        volume_reads.append("read")
        return 1.0 if len(volume_reads) == 1 else 0.0

    monkeypatch.setattr(tts_engine, "_output_volume", changing_volume)
    worker._run()

    start = next(event for event in events if event.type == "audio_start")
    assert start.audio_envelope[2] > 0.5
    assert submitted[40:80] == [0.5] * 40
    assert volume_reads == ["read"]


def test_prepared_playback_keeps_single_volume_snapshot_when_setting_changes(monkeypatch):
    submitted = []
    volume_reads = []
    _sounddevice(monkeypatch, [], submitted=submitted)
    engine = tts_engine.TtsEngine.__new__(tts_engine.TtsEngine)

    def changing_volume():
        volume_reads.append("read")
        return 1.0 if len(volume_reads) == 1 else 0.0

    monkeypatch.setattr(tts_engine, "_output_volume", changing_volume)
    wav_bytes = engine.lip_sync_audio(_wav())
    play = getattr(engine, "play_prepared_audio", None)
    assert callable(play)
    play(wav_bytes, threading.Event())

    assert submitted[40:80] == [0.5] * 40
    assert volume_reads == ["read"]
