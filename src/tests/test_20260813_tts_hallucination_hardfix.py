from __future__ import annotations

from types import SimpleNamespace


def _task(order: int, text: str):
    from runtime.tts_task_manager import TtsSegmentTask

    frame = SimpleNamespace(
        turn_id="turn:1",
        segment_id=order,
        interruptible=True,
        audio_asset="",
        prosody_style="neutral",
        prosody_notes="",
    )
    return TtsSegmentTask(
        frame=frame,
        text=text,
        order=order,
        valence=0.0,
        arousal=0.3,
        stance="neutral",
    )


def test_segmented_turn_never_falls_back_to_inline_when_prefetch_threshold_is_smaller():
    from runtime.tts_task_manager import _dispatch_policy

    tasks = [_task(i, "đoạn hợp lệ " * 5) for i in range(5)]
    policy = _dispatch_policy(tasks, prefetch_workers=3, max_prefetch_segments=4)

    assert policy.strategy == "prefetch"
    assert 1 <= policy.workers <= 3


def test_forced_inline_is_ignored_for_multi_segment_turn(monkeypatch):
    from runtime.tts_task_manager import _dispatch_policy

    monkeypatch.setenv("ERIS_TTS_TASK_STRATEGY", "inline")
    tasks = [_task(i, "đoạn hợp lệ " * 4) for i in range(3)]
    policy = _dispatch_policy(tasks, prefetch_workers=3, max_prefetch_segments=8)

    assert policy.strategy == "prefetch"
    assert "forced_inline_ignored" in policy.reason


def test_conversation_profile_can_prefetch_every_segment_it_can_create():
    from runtime.stage_profiles import stage_profile

    profile = stage_profile("conversation")
    assert profile.max_prefetch_segments >= profile.max_tts_segments


def test_multi_segment_policy_promotes_single_configured_worker_to_parallel_prefetch():
    from runtime.tts_task_manager import _dispatch_policy

    tasks = [_task(i, "đoạn hợp lệ " * 4) for i in range(5)]
    policy = _dispatch_policy(tasks, prefetch_workers=1, max_prefetch_segments=8)
    assert policy.strategy == "prefetch"
    assert policy.workers >= 2


def test_legacy_queue_has_headroom_for_full_segmented_turn(monkeypatch):
    from runtime.tts_engine import _queue_size

    monkeypatch.delenv("ERIS_TTS_QUEUE_SIZE", raising=False)
    assert _queue_size() >= 8


def test_conversation_history_and_memory_claims_require_real_evidence():
    from runtime.claim_domain_contract import validate_claim_manifest

    for domain in ("conversation_history", "memory"):
        verdict = validate_claim_manifest(
            [{"domain": domain, "claim": "Bạn từng kể về cà phê", "evidence_refs": []}],
            allowed_domains=[domain],
            allowed_evidence_refs=[],
            text_present=True,
        )
        assert verdict.allowed is False
        assert any(item.endswith(":missing") for item in verdict.unknown_evidence_refs)


def test_self_preference_does_not_need_embodied_history():
    from runtime.claim_domain_contract import validate_claim_manifest

    verdict = validate_claim_manifest(
        [{"domain": "self_preference", "claim": "Tôi nghiêng về vị đắng", "evidence_refs": []}],
        allowed_domains=["self_preference"],
        text_present=True,
    )
    assert verdict.allowed is True


def test_user_probe_cannot_use_history_or_user_statement_as_bridge(tmp_path):
    from cortex.subject.expression_commitment import commitment_payload
    from runtime.ai_orchestrated.cognitive_blackboard import TypedCognitiveBlackboard
    from runtime.ai_orchestrated.initiative import InitiativeController, InitiativeAction
    from runtime.ai_orchestrated.social_candidate import SocialSpeechCandidate

    candidate = SocialSpeechCandidate(
        decision="candidate",
        kind="relational_initiative",
        topic="Ask one real unknown preference.",
        speech_act="user_probe",
        anchor_type="internal",
        anchor_id="probe:1",
        evidence_refs=("subject-initiative:7",),
        confidence=0.9,
        relevance=0.9,
        urgency=0.7,
        source="subject_initiative",
        expression_commitment=commitment_payload(
            origin="subject_initiative",
            source_id="probe:1",
            semantic_target="Ask one real unknown preference.",
            motive="understand_user",
            pressure=0.9,
            freshness=1.0,
            persistence=0.8,
        ),
    )
    board = TypedCognitiveBlackboard.from_snapshot({}, now=1000.0)
    decision = InitiativeController(state_path=tmp_path / "initiative.json").decide(board, candidate)

    assert decision.action is InitiativeAction.SPEECH
    domains = set(decision.speech_intent.allowed_claim_domains)
    assert "conversation_history" not in domains
    assert "memory" not in domains
    assert "user_statement" not in domains


def test_fake_memory_bridge_is_blocked_without_provenance():
    from runtime.self_biography_guard import user_probe_fake_memory_premise

    assert user_probe_fake_memory_premise(
        "À mà, lúc trước bạn có kể là đang thử vài kiểu cà phê pha ở nhà, giờ sao rồi?",
        claim_manifest=[],
        allowed_evidence_refs=[],
    ) is True
    assert user_probe_fake_memory_premise(
        "À mà, bình thường bạn thích cà phê kiểu nào?",
        claim_manifest=[],
        allowed_evidence_refs=[],
    ) is False


def test_embodied_self_history_is_blocked_but_preference_is_allowed():
    from runtime.self_biography_guard import embodied_self_history_without_evidence

    assert embodied_self_history_without_evidence(
        "Tôi thì hay uống đen không đường, quen rồi thấy ổn.",
        claim_manifest=[],
        allowed_evidence_refs=[],
    ) is True
    assert embodied_self_history_without_evidence(
        "Nếu nói về gu thì tôi nghiêng về vị đắng hơn.",
        claim_manifest=[{"domain": "self_preference", "claim": "nghiêng về vị đắng", "evidence_refs": []}],
        allowed_evidence_refs=[],
    ) is False


def test_embodied_self_history_can_pass_with_explicit_history_evidence():
    from runtime.self_biography_guard import embodied_self_history_without_evidence

    assert embodied_self_history_without_evidence(
        "Tôi thường uống loại này.",
        claim_manifest=[{
            "domain": "self_history",
            "claim": "Tôi thường uống loại này",
            "evidence_refs": ["self-action:coffee:1"],
        }],
        allowed_evidence_refs=["self-action:coffee:1"],
    ) is False


def test_user_statement_claim_requires_current_turn_evidence():
    from runtime.claim_domain_contract import validate_claim_manifest

    missing = validate_claim_manifest(
        [{"domain": "user_statement", "claim": "Bạn vừa nói cà phê bình thường", "evidence_refs": []}],
        allowed_domains=["user_statement"],
        allowed_evidence_refs=["user-statement:current-turn"],
        text_present=True,
    )
    assert missing.allowed is False

    grounded = validate_claim_manifest(
        [{
            "domain": "user_statement",
            "claim": "Bạn vừa nói cà phê bình thường",
            "evidence_refs": ["user-statement:current-turn"],
        }],
        allowed_domains=["user_statement"],
        allowed_evidence_refs=["user-statement:current-turn"],
        text_present=True,
    )
    assert grounded.allowed is True


def test_task_manager_plays_five_segments_without_cancel_or_drop():
    import threading
    import time
    from runtime.tts_task_manager import TtsTaskManager

    class Backend:
        def synthesize(self, text, **kwargs):
            time.sleep(0.02)
            # fake RIFF-ish bytes are enough for the mocked player/lipsync path
            return b"RIFF" + b"\x00" * 80

        def play_audio(self, wav_bytes, stop_event, on_started=None):
            if on_started:
                on_started()
            time.sleep(0.01)

        def is_speech_turn_current(self, *args, **kwargs):
            return True

    frames = [
        SimpleNamespace(
            turn_id="turn:five",
            segment_id=i,
            tts_text=f"segment {i}",
            interruptible=True,
            audio_asset="",
            prosody_style="neutral",
            prosody_notes="",
            delivery={},
        )
        for i in range(5)
    ]
    events = []
    manager = TtsTaskManager(prefetch_workers=1, max_prefetch_segments=4)
    assert manager.dispatch_frames(
        frames,
        backend=Backend(),
        valence=0.0,
        arousal=0.3,
        stance="neutral",
        on_event=lambda event, frame: events.append((event.type, event.segment_id)),
    ) is True
    assert manager.wait_until_idle(timeout=2.0) is True

    starts = [seg for typ, seg in events if typ == "audio_start"]
    dones = [seg for typ, seg in events if typ == "audio_done"]
    cancelled = [seg for typ, seg in events if typ == "audio_cancelled"]
    assert starts == [0, 1, 2, 3, 4]
    assert dones == [0, 1, 2, 3, 4]
    assert cancelled == []


def test_three_segment_turn_prefetches_all_three_by_default():
    from runtime.tts_task_manager import _dispatch_policy

    tasks = [_task(i, "đoạn cần phát liền mạch " * 8) for i in range(3)]
    policy = _dispatch_policy(tasks, prefetch_workers=1, max_prefetch_segments=8)

    assert policy.strategy == "prefetch"
    assert policy.workers == 3



def test_three_segment_turn_is_prebuffered_before_first_playback():
    import threading
    import time
    from runtime.tts_task_manager import TtsTaskManager

    synth_done = {}
    playback_started = []
    lock = threading.Lock()

    class Backend:
        def synthesize(self, text, **kwargs):
            index = int(text.rsplit(" ", 1)[-1])
            time.sleep((index + 1) * 0.02)
            with lock:
                synth_done[index] = time.monotonic()
            return b"RIFF" + b"\x00" * 80

        def play_audio(self, wav_bytes, stop_event, on_started=None):
            with lock:
                playback_started.append(time.monotonic())
            if on_started:
                on_started()
            time.sleep(0.005)

    frames = [
        SimpleNamespace(
            turn_id="turn:prebuffer",
            segment_id=i,
            tts_text=f"segment {i}",
            interruptible=True,
            audio_asset="",
            prosody_style="neutral",
            prosody_notes="",
            delivery={},
        )
        for i in range(3)
    ]
    manager = TtsTaskManager(prefetch_workers=1, max_prefetch_segments=8)
    assert manager.dispatch_frames(
        frames,
        backend=Backend(),
        valence=0.0,
        arousal=0.3,
        stance="neutral",
    ) is True
    assert manager.wait_until_idle(timeout=2.0) is True

    assert set(synth_done) == {0, 1, 2}
    assert playback_started
    # Clip 0 chỉ được mở loa sau khi cả cửa sổ 0..2 đã sẵn sàng, nên không còn
    # cảnh clip 0 đọc xong rồi đợi clip 1/2 synth như log lỗi trước đó.
    assert playback_started[0] >= max(synth_done.values())

def test_elevenlabs_budget_keeps_normal_long_reply_in_one_clip():
    from runtime.stage_contracts import StageFrame
    from runtime.stage_output import StageOutputPipeline
    from runtime.stage_profiles import stage_profile
    from runtime.tts_budget import GLOBAL_TTS_REQUEST_BUDGET

    # Xấp xỉ kích thước câu trong log 17:41:48: dài hơn limit Gemini cũ nhưng
    # vẫn là một utterance rất bình thường đối với ElevenLabs.
    text = (
        "À, ý em là việc đứng ngoài quan sát vòng lặp vẫn chưa đủ. "
        "Cái quyết định nằm ở chỗ cách nhìn đó có đủ sức khiến vòng thực sự xê dịch hay vỡ ra không, "
        "chứ không chỉ là một góc nhìn khác. Nếu nó chỉ là khung cửa mới mà ta vẫn đứng yên, "
        "thì vòng lặp vẫn quay. Nói ngắn gọn, đứng ngoài mà không đổi được cơ chế thì vẫn chỉ là một vòng khác."
    )
    assert len(text) > GLOBAL_TTS_REQUEST_BUDGET.max_transcript_chars
    assert len(text) <= GLOBAL_TTS_REQUEST_BUDGET.transcript_limit("elevenlabs")

    frames = StageOutputPipeline(profile=stage_profile("conversation")).frames_for_tts(
        StageFrame(turn_id="chat:regression", segment_id=0, tts_text=text)
    )
    assert len(frames) == 1
    assert frames[0].tts_text == text
