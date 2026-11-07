"""Exercise manager lifecycle with controlled device boundaries, without audio I/O."""
import io
import struct
import threading
import wave
from types import SimpleNamespace

from runtime.tts_task_manager import TtsTaskManager


def _wav():
    output = io.BytesIO()
    with wave.open(output, "wb") as stream:
        stream.setparams((1, 2, 8000, 0, "NONE", "not compressed"))
        stream.writeframes(b"\x00\x00" * 800)
    return output.getvalue()


class Device:
    def __init__(self, *, start=True):
        self.entered = threading.Event()
        self.release = threading.Event()
        self.start = start

    def synthesize(self, text, **kwargs):
        return _wav()

    def play_audio(self, wav_bytes, stop_event, on_started=None):
        if self.start:
            on_started()
        self.entered.set()
        assert self.release.wait(2), "test did not release fake device"
        if not self.start:
            on_started()


def _dispatch(manager, device, events, turn="turn:test"):
    frame = SimpleNamespace(turn_id=turn, segment_id=0, tts_text="Hello",
                            interruptible=True, audio_asset="", delivery={},
                            prosody_style="neutral", prosody_notes="")
    assert manager.dispatch_frames([frame], backend=device, valence=0, arousal=.3,
                                   stance="neutral", on_event=lambda event, frame: events.append(event))
    assert device.entered.wait(2)


def _lifecycle(events, turn="turn:test"):
    return [event.type for event in events if event.turn_id == turn
            and event.type in {"audio_start", "audio_done", "audio_cancelled", "audio_failed"}]


def test_cancel_publishes_current_segment_before_blocked_device_returns():
    manager, device, events = TtsTaskManager(), Device(), []
    try:
        _dispatch(manager, device, events)
        manager.cancel_current()
        assert _lifecycle(events) == ["audio_start", "audio_cancelled"]
    finally:
        device.release.set()
        manager.wait_until_idle(2)
        manager.shutdown()
    assert _lifecycle(events) == ["audio_start", "audio_cancelled"]


def test_cancel_before_device_start_suppresses_late_start():
    manager, device, events = TtsTaskManager(), Device(start=False), []
    try:
        _dispatch(manager, device, events)
        manager.cancel_current()
        device.release.set()
        assert manager.wait_until_idle(2)
        assert _lifecycle(events) == ["audio_cancelled"]
    finally:
        device.release.set()
        manager.shutdown()


def test_replaced_playback_cannot_start_after_new_turn():
    manager, old, new, events = TtsTaskManager(), Device(start=False), Device(), []
    try:
        _dispatch(manager, old, events, "old")
        old_thread = manager._playback_thread
        _dispatch(manager, new, events, "new")
        old.release.set()
        old_thread.join(2)
        assert not old_thread.is_alive()
        assert _lifecycle(events, "old") == ["audio_cancelled"]
        assert _lifecycle(events, "new") == ["audio_start"]
    finally:
        old.release.set()
        new.release.set()
        manager.wait_until_idle(2)
        manager.shutdown()


def test_return_without_start_callback_is_failed_not_done():
    class SilentDevice(Device):
        def play_audio(self, wav_bytes, stop_event, on_started=None):
            self.entered.set()

    manager, device, events = TtsTaskManager(), SilentDevice(), []
    try:
        _dispatch(manager, device, events)
        assert manager.wait_until_idle(2)
        assert _lifecycle(events) == ["audio_failed"]
    finally:
        manager.shutdown()


def test_successful_playback_reports_start_envelope_then_done():
    manager, device, events = TtsTaskManager(), Device(), []
    try:
        _dispatch(manager, device, events)
        device.release.set()
        assert manager.wait_until_idle(2)
        assert _lifecycle(events) == ["audio_start", "audio_done"]
        started = next(event for event in events if event.type == "audio_start")
        assert started.audio_duration_ms == 100
        assert started.audio_frame_ms == 20
        assert len(started.audio_envelope) == 5
    finally:
        device.release.set()
        manager.shutdown()


def test_old_segment_callback_cannot_start_next_segment():
    class LateCallbackDevice(Device):
        def __init__(self):
            super().__init__()
            self.previous_start = None

        def play_audio(self, wav_bytes, stop_event, on_started=None):
            if self.previous_start is None:
                on_started()
                self.previous_start = on_started
            else:
                self.previous_start()
            self.entered.set()

    manager, device, events = TtsTaskManager(), LateCallbackDevice(), []
    frames = [SimpleNamespace(turn_id="turn:test", segment_id=index, tts_text=f"Hello {index}",
                              interruptible=True, audio_asset="", delivery={},
                              prosody_style="neutral", prosody_notes="") for index in range(2)]
    try:
        assert manager.dispatch_frames(frames, backend=device, valence=0, arousal=.3,
                                       stance="neutral", on_event=lambda event, frame: events.append(event))
        assert manager.wait_until_idle(2)
        assert [(event.segment_id, event.type) for event in events
                if event.type in {"audio_start", "audio_done", "audio_failed"}] == [
                    (0, "audio_start"), (0, "audio_done"), (1, "audio_failed")]
    finally:
        manager.shutdown()


def test_volume_change_after_preparation_keeps_envelope_and_played_pcm_identical():
    class ChangingVolumeDevice(Device):
        def __init__(self):
            super().__init__()
            self.volume = .5
            self.played_pcm = None

        def synthesize(self, text, **kwargs):
            output = io.BytesIO()
            with wave.open(output, 'wb') as stream:
                stream.setparams((1, 2, 8000, 0, 'NONE', 'not compressed'))
                stream.writeframes(b'\x00\x00' * 400 + struct.pack('<h', 1000) * 400)
            return output.getvalue()

        def lip_sync_audio(self, wav_bytes):
            output = io.BytesIO()
            with wave.open(io.BytesIO(wav_bytes), 'rb') as source:
                params = source.getparams()
                pcm = source.readframes(source.getnframes())
            with wave.open(output, 'wb') as target:
                target.setparams(params)
                samples = struct.unpack('<800h', pcm)
                target.writeframes(struct.pack('<800h', *(int(sample * self.volume) for sample in samples)))
            self.volume = 0  # User mutes immediately after the prepared PCM snapshot.
            return output.getvalue()

        def play_audio(self, wav_bytes, stop_event, on_started=None):
            # Ordinary playback reads the latest volume again.
            self.play_prepared_audio(self.lip_sync_audio(wav_bytes), stop_event, on_started)

        def play_prepared_audio(self, wav_bytes, stop_event, on_started=None):
            with wave.open(io.BytesIO(wav_bytes), 'rb') as stream:
                self.played_pcm = struct.unpack('<800h', stream.readframes(stream.getnframes()))
            on_started()
            self.entered.set()

    manager, device, events = TtsTaskManager(), ChangingVolumeDevice(), []
    try:
        _dispatch(manager, device, events)
        assert manager.wait_until_idle(2)
        assert device.played_pcm == (0,) * 400 + (500,) * 400
        started = next(event for event in events if event.type == 'audio_start')
        assert any(value > 0 for value in started.audio_envelope)
        assert started.audio_duration_ms == 100
    finally:
        manager.shutdown()
