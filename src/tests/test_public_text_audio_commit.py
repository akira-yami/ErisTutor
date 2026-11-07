"""Public speech text waits for real playback, including slow and failed TTS."""

import logging
from types import SimpleNamespace

import pytest

from domain import Decision
from runtime.coordinator import RuntimeCoordinator
from runtime.stage_contracts import StageFrameBuilder


@pytest.fixture
def speech_output(monkeypatch):
    monkeypatch.setattr("runtime.ai_orchestrated.autonomous_trace.append_public_turn_trace", lambda *a, **kw: None)
    monkeypatch.setenv("ERIS_TTS_SYNC_TEXT", "1")
    monkeypatch.setenv("ERIS_TTS_TEXT_SYNC_TIMEOUT", "0.2")
    timers = []

    class Timer:
        def __init__(self, interval, callback):
            self.callback = callback

        def start(self):
            timers.append(self)

        def cancel(self):
            if self in timers:
                timers.remove(self)

    monkeypatch.setattr("runtime.public_output_runtime.threading.Timer", Timer)

    def build(language="vi", channel="chat", *, tts_ok=True, scheduled=True, sealed=True, backend=None):
        monkeypatch.setattr(
            "runtime.runtime_settings_store.runtime_text",
            lambda section, key, default=None: language if (section, key) == ("tts", "language") else default,
        )
        messages, panels, events, visible_turns = [], [], [], []
        callbacks = {}
        owner = object.__new__(RuntimeCoordinator)
        owner.logger = logging.getLogger("test-public-text-audio")
        owner.transport = SimpleNamespace(
            send_json=messages.append,
            send_panel_state=lambda payload: panels.append(payload),
        )
        owner.ai_runtime_enabled = False
        owner._stage_channel_blocked = lambda _channel: False
        owner._build_body_bias_payload = lambda: {}
        owner._private_mind_digest_for_payload = lambda: {}
        owner._private_life_digest_for_payload = lambda: {}
        owner._build_emotion_debug_payload = lambda: {}
        owner._build_depth_v2_block = lambda **_kwargs: {}
        owner._record_public_turn = lambda *args, **kwargs: visible_turns.append(kwargs["payload"])
        owner._get_runtime_provider = lambda _name: SimpleNamespace(
            engine=backend if backend is not None else SimpleNamespace(_ok=tts_ok))
        owner._handle_tts_playback_event = lambda *args, **kwargs: None
        owner._emit_stage_protocol_state = lambda *args, **kwargs: None
        owner.subconscious_runtime_enabled = False
        from live2d_body.stage_state import Live2DStageState
        from runtime.interaction_clock import InteractionClock

        live2d = Live2DStageState()
        owner.interaction_clock = InteractionClock()
        owner.stage_event_bus = SimpleNamespace(
            emit=lambda event: events.append(event.to_payload()),
            emit_live2d=lambda event: live2d.handle_payload(event.to_payload()),
        )
        owner.stage_frame_builder = StageFrameBuilder(language_hint=language)

        def dispatch(frames, **kwargs):
            callbacks.update(kwargs)
            callbacks["frame"] = frames[0]
            return scheduled

        owner.stage_dispatcher = SimpleNamespace(dispatch_tts=dispatch)
        if backend is not None:
            from runtime.stage_dispatcher import StageDispatcher

            owner.stage_dispatcher = StageDispatcher()
        owner.current_stage_user_text = "xin chào"
        owner.memory_state = None
        decision = Decision(
            text="Tớ nghe đây.", animation="stand", speech_text="聞いているよ。" if language == "ja" else "Tớ nghe đây.",
            speech_language=language, utterance_turn_id="test-audio-commit",
        )
        prepared = None
        state = SimpleNamespace(generation=1, active_run_id="run:audio", active_delivery_id="test-audio-commit")
        if sealed:
            from runtime.conversation.public_output_artifact import prepare_public_output

            turn = SimpleNamespace(session_id="test-session", generation=1, run_id="run:audio",
                                   delivery_id="test-audio-commit", move=SimpleNamespace(move_id="move:audio"))
            prepared = prepare_public_output(event_id="user:audio", turn=turn, channel=channel,
                                             decision=decision, payload=decision.to_payload())
            owner.conversation_kernel = SimpleNamespace(store=SimpleNamespace(load=lambda _sid: state))
        assert owner._send_decision_to_body(decision, channel=channel, prepared=prepared)

        def event(kind, segment_id=0, **kwargs):
            callbacks["on_event"](SimpleNamespace(
                type=kind, turn_id=callbacks["frame"].turn_id, segment_id=segment_id,
                **kwargs,
            ), callbacks["frame"])

        def start_hint():
            callback = callbacks.get("on_first_start")
            if callable(callback):
                callback()

        return SimpleNamespace(owner=owner, messages=messages, panels=panels, events=events,
                               visible_turns=visible_turns, callbacks=callbacks, event=event, timers=timers,
                               state=state, prepared=prepared, start_hint=start_hint, live2d=live2d)

    return build


def _speech_packets(output):
    return [packet for packet in output.messages if packet.get("text") == "Tớ nghe đây."]


@pytest.mark.parametrize("language", ["vi", "ja"])
@pytest.mark.parametrize("channel", ["chat", "autonomous"])
@pytest.mark.parametrize("start_signal", ["callback_then_event", "event"])
@pytest.mark.parametrize("sealed", [False, True])
def test_public_text_commits_once_at_playback_start(speech_output, language, channel, start_signal, sealed):
    output = speech_output(language, channel, sealed=sealed)
    assert not _speech_packets(output)
    assert not [event for event in output.events if event["type"] == "stage_segment"]
    assert not output.visible_turns
    assert output.panels
    for kind in ("audio_prepare", "audio_queued", "audio_buffer_ready"):
        output.event(kind)
        assert not _speech_packets(output)

    if start_signal == "callback_then_event":
        output.start_hint()
        assert not _speech_packets(output)
    output.event("audio_start")
    assert len(_speech_packets(output)) == 1
    if sealed:
        assert _speech_packets(output)[0]["text"] == output.prepared.display_text
        assert output.callbacks["frame"].tts_text == output.prepared.segments[0].tts_text
    assert len(output.visible_turns) == 1
    assert len([event for event in output.events if event["type"] == "stage_segment"]) == 1
    assert output.owner.public_lifecycle.snapshot().state == "SPEAKING"
    output.event("audio_start")
    output.start_hint()
    assert len(_speech_packets(output)) == 1
    assert len(output.visible_turns) == 1


def test_slow_synthesis_cannot_release_text_on_sync_timeout(speech_output):
    output = speech_output()
    for timer in list(output.timers):
        timer.callback()
    assert not _speech_packets(output)
    assert not output.visible_turns
    output.event("audio_start")
    assert len(_speech_packets(output)) == 1
    assert output.owner.public_lifecycle.snapshot().state == "SPEAKING"


@pytest.mark.parametrize("terminal", ["audio_failed", "audio_localization_failed", "audio_cancelled"])
def test_failure_before_playback_discards_deferred_text(speech_output, terminal):
    output = speech_output()
    output.event(terminal)
    assert not _speech_packets(output)
    assert not output.visible_turns
    assert output.owner.public_lifecycle.snapshot().state == (
        "INTERRUPTED" if terminal == "audio_cancelled" else "DEGRADED"
    )
    for timer in list(output.timers):
        timer.callback()
    output.start_hint()
    output.event("audio_start")
    assert not _speech_packets(output)


@pytest.mark.parametrize("tts_ok,scheduled", [(False, True), (True, False)])
def test_unavailable_audio_does_not_commit_speech_text(speech_output, tts_ok, scheduled):
    output = speech_output(tts_ok=tts_ok, scheduled=scheduled)
    assert not _speech_packets(output)
    assert not output.visible_turns
    assert output.owner.public_lifecycle.snapshot().state == "DEGRADED"
    assert output.owner._v12_public_delivery_outcomes["test-audio-commit"] == "failed"


def test_user_preemption_during_synthesis_cannot_commit_old_text(speech_output):
    output = speech_output()
    output.state.generation += 1
    output.start_hint()
    output.event("audio_start")
    output.event("audio_cancelled")
    assert not _speech_packets(output)
    assert not output.visible_turns
    assert not [event for event in output.events if event["type"] == "stage_segment"]
    assert output.owner._v12_public_delivery_outcomes["test-audio-commit"] == "cancelled"


def test_later_segment_playback_keeps_lifecycle_after_discarding_text(speech_output):
    output = speech_output()
    output.event("audio_failed")
    output.event("audio_start", segment_id=1, audio_envelope=(.6,) * 25,
                 audio_frame_ms=20, audio_duration_ms=500, audio_mouth_shape=(.65,) * 25)
    assert not _speech_packets(output)
    assert output.owner.interaction_clock.audio_active
    snapshot = output.live2d.advance(0)
    assert snapshot.speaking
    assert snapshot.mouth_shape == pytest.approx(.65)
    output.event("audio_done", segment_id=1)
    assert not output.owner.interaction_clock.audio_active
    snapshot = output.live2d.advance(0)
    assert not snapshot.speaking and snapshot.mouth_shape == 0


def test_legacy_opt_out_still_shows_text_immediately(speech_output, monkeypatch):
    monkeypatch.setenv("ERIS_TTS_SYNC_TEXT", "0")
    output = speech_output()
    assert len(_speech_packets(output)) == 1
    assert len(output.visible_turns) == 1


def test_real_task_manager_synth_failure_cannot_fake_text_commit(speech_output, monkeypatch):
    monkeypatch.setenv("ERIS_TTS_TASK_MANAGER", "1")

    class FailingBackend:
        _ok = True

        def synthesize(self, text, **kwargs):
            raise RuntimeError("controlled synthesis failure")

        def play_audio(self, *args, **kwargs):
            raise AssertionError("failed synthesis cannot reach playback")

    backend = FailingBackend()
    try:
        output = speech_output(backend=backend)
        assert backend.task_manager.wait_until_idle(2)
        assert not _speech_packets(output)
        assert not output.visible_turns
        assert output.owner.public_lifecycle.snapshot().state == "DEGRADED"
    finally:
        if getattr(backend, "task_manager", None) is not None:
            backend.task_manager.shutdown()


def test_audio_start_carries_duration_ms(speech_output):
    output = speech_output()
    from runtime.public_output_runtime import PublicOutputRuntimeMixin
    PublicOutputRuntimeMixin._handle_tts_playback_event(
        output.owner,
        SimpleNamespace(type="audio_start", turn_id="turn:test", audio_duration_ms=6200),
    )
    assert output.owner.last_tts_event.get("audio_duration_ms") == 6200
    tts_packets = [msg for msg in output.messages if msg.get("type") == "tts_event"]
    assert tts_packets
    assert tts_packets[-1].get("audio_duration_ms") == 6200


@pytest.mark.parametrize("preempt", [False, True])
def test_revision_retires_voice_and_text_together_with_real_dispatcher(speech_output, monkeypatch, preempt):
    import io
    import threading
    import wave
    from runtime.tts_engine import TtsEngine

    monkeypatch.setenv("ERIS_TTS_TASK_MANAGER", "1")
    entered, release = threading.Event(), threading.Event()
    played = []
    wav = io.BytesIO()
    with wave.open(wav, "wb") as stream:
        stream.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
        stream.writeframes(b"\0\0" * 1600)

    class Backend(TtsEngine):
        def __init__(self):
            self._ok = True
            self._worker = None
            self._speech_lock = threading.RLock()
            self._speech_epoch = 0
            self._speech_turn_id = ""
        def refresh_configuration(self): pass
        def synthesize(self, text, **kwargs):
            entered.set()
            assert release.wait(3)
            return wav.getvalue()
        def lip_sync_audio(self, data): return data
        def play_prepared_audio(self, data, stop, *, on_started=None):
            played.append(data)
            on_started()

    backend = Backend()
    try:
        output = speech_output(backend=backend)
        assert entered.wait(2)
        output.owner.tts_engine = backend
        def invalidate(_sid):
            output.state.generation += 1
        output.owner.conversation_kernel.preempt_for_user = invalidate
        if preempt:
            output.owner._mark_active_root_run_stale_for_revision(1, 2)
        release.set()
        assert backend.task_manager.wait_until_idle(3)
        assert (bool(played), bool(_speech_packets(output))) == (not preempt, not preempt)
    finally:
        release.set()
        backend.task_manager.shutdown()



def test_p2_artifact_publish_only_after_real_audio_start(speech_output, monkeypatch):
    """End-to-end public-text hook, not merely a sidecar helper fixture."""
    from dataclasses import replace
    from runtime.conversation import public_output_artifact as output_module
    from runtime.workspace import presentation_p2
    original = output_module.prepare_public_output
    calls = []
    blocks = ({'kind': 'math', 'title': 'Giải thích', 'content': {'latex': r'\\frac{1}{2}'}},)
    monkeypatch.setattr(output_module, 'prepare_public_output',
                        lambda **kw: replace(original(**kw), presentation_blocks=blocks))
    monkeypatch.setattr(presentation_p2, 'publish_committed_presentation',
                        lambda **kw: calls.append(kw))
    result = speech_output(sealed=True)
    assert not calls
    result.event('audio_queued')
    result.start_hint()
    assert not calls  # on_first_start is NOT evidence that audio actually played
    result.event('audio_start')
    assert len(calls) == 1
    assert calls[0]['turn_id'] == result.callbacks['frame'].turn_id
    assert calls[0]['delivery_id'] == result.prepared.delivery_id
    assert calls[0]['blocks'] == blocks
    result.event('audio_start')
    assert len(calls) == 1  # idempotent: send text and artifact only once


def test_p2_preempted_or_failed_audio_never_publishes_artifact(speech_output, monkeypatch):
    from dataclasses import replace
    from runtime.conversation import public_output_artifact as output_module
    from runtime.workspace import presentation_p2
    original = output_module.prepare_public_output
    calls = []
    monkeypatch.setattr(output_module, 'prepare_public_output',
                        lambda **kw: replace(original(**kw),
                            presentation_blocks=({'kind':'math','title':'T','content':{'latex':'x'}},)))
    monkeypatch.setattr(presentation_p2, 'publish_committed_presentation',
                        lambda **kw: calls.append(kw))
    failed = speech_output(sealed=True)
    failed.event('audio_failed')
    failed.event('audio_start')
    assert not calls
    preempted = speech_output(sealed=True)
    preempted.state.generation += 1
    preempted.event('audio_start')
    assert not calls
