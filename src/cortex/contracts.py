from dataclasses import asdict, dataclass, field, is_dataclass




def _to_plain_contract(value):
    if value is None:
        return None
    if isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(key): _to_plain_contract(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_to_plain_contract(item) for item in value]
    to_dict = getattr(value, "to_dict", None)
    if callable(to_dict):
        return to_dict()
    if is_dataclass(value):
        return _to_plain_contract(asdict(value))
    return repr(value)

#nhìn thấy gì → chú ý gì → hiểu thế nào → cảm thấy áp lực gì → có nên nói không → nói theo phong cách nào → cập nhật trí nhớ.


@dataclass(slots=True)
class UserSignals:
    typing_speed: str = "unknown"
    window_switching: str = "unknown"
    focus_pattern: str = "unknown"

    def to_dict(self) -> dict[str, str]:
        return {
            "typing_speed": self.typing_speed,
            "window_switching": self.window_switching,
            "focus_pattern": self.focus_pattern,
        }


#trạng thái môi trường/user tại một thời điểm
@dataclass(slots=True)
class TelemetrySnapshot:
    captured_at: str
    active_window: str
    active_window_kind: str
    focus_summary: str
    active_app: str = ""
    active_process: str = ""
    active_window_title: str = ""
    foreground_hwnd: int = 0
    foreground_pid: int = 0
    window_class: str = ""
    focus_changed: bool = False
    interaction_state: str = "unknown"
    browser_url: str = ""
    browser_tabs: tuple[str, ...] = ()
    open_windows: tuple[str, ...] = ()
    cpu_percent: float = 0.0
    ram_percent: float = 0.0
    ambient_tone: str = "neutral"
    tease_bias: str = "neutral"
    inferred_pos_x: float = 0.5
    position_label: str = "center"
    idle_seconds: float = 0.0
    idle_label: str = "active"
    recent_events: tuple[str, ...] = ()
    user_signals: UserSignals = field(default_factory=UserSignals)
    evidence: tuple[str, ...] = ()
    claim_style: str = "WEAK_IMPRESSION"
    vision_facts: tuple[str, ...] = ()
    # Metadata nội dung foreground theo ladder L0-L5.
    # Đây là raw/normalized fact cho SeLens, không phải kết luận tâm lý của user.
    content_metadata: dict[str, object] = field(default_factory=dict)

    def to_machine_context(self) -> dict[str, object]:
        context: dict[str, object] = {
            "time": self.captured_at,
            "active_app": self.active_app or self.active_window,
            "active_process": self.active_process,
            "active_window_title": self.active_window_title or self.active_window,
            "foreground_hwnd": int(self.foreground_hwnd or 0),
            "foreground_pid": int(self.foreground_pid or 0),
            "window_class": self.window_class,
            "focus_changed": bool(self.focus_changed),
            "interaction_state": self.interaction_state,
            "idle_seconds": int(self.idle_seconds),
            "recent_events": list(self.recent_events),
            "user_signals": self.user_signals.to_dict(),
            "evidence": list(self.evidence),
            "vision_facts": list(self.vision_facts),
            "content_metadata": _to_plain_contract(self.content_metadata),
            "claim_style": self.claim_style,
            "active_window_kind": self.active_window_kind,
            "focus_summary": self.focus_summary,
            "browser_url": self.browser_url,
            "browser_tabs": list(self.browser_tabs),
            "open_windows": list(self.open_windows),
            "cpu_percent": round(self.cpu_percent, 1),
            "ram_percent": round(self.ram_percent, 1),
            "ambient_tone": self.ambient_tone,
            "tease_bias": self.tease_bias,
            "inferred_pos_x": round(self.inferred_pos_x, 3),
            "position_label": self.position_label,
            "idle_label": self.idle_label,
        }
        return context


@dataclass(slots=True)
class GoalDriftReport:
    similarity: float
    drift_score: float
    drift_level: str
    confidence: float = 0.0
    should_raise_guard: bool = False
    reason_span: str = ""
    prompt_context: str = ""
    evidence: tuple[str, ...] = ()
    vector_method: str = "attention_signal_local"


@dataclass(slots=True)
class TiPolicyReport:
    mode: str
    focus_label: str
    reason: str
    prompt_context: str
    hard_rules: tuple[str, ...] = ()
    evidence: tuple[str, ...] = ()
    animation_hint: str = "stand"
    direction_hint: int = 0
    target_x_hint: float = -1.0
    motion_intent_hint: str = ""
    motion_facing_hint: int = 0
    motion_target_x_hint: float = -1.0
    force_animation: bool = False
    allow_idle_variation: bool = True
    uncertainty_bias: float = 0.10
    directness_bias: float = -0.10
    restraint_bias: float = 0.15
    post_exposure_recoil_bias: float = 0.12
    # ControlledSlippage chỉ là nhãn debug/prompt pressure.
    # Truth guard vẫn nằm ở Ti/PostGenerationFilters, không bị trường này tắt đi.
    slippage_mode: str = ""
    slippage_lane: str = ""


@dataclass(slots=True)
class FeStylePlan:
    mode: str
    tone_label: str
    style_context: str
    intent_label: str = "ordinary_dialogue"
    session_arc: str = "quiet_observation"
    transition_note: str = ""
    evidence: tuple[str, ...] = ()
    sarcasm_level: float = 0.0
    warmth_level: float = 0.0
    brevity_bias: str = "medium"


@dataclass(slots=True)
class FeStyleMemory:
    recent_modes: tuple[str, ...] = ()
    recent_tones: tuple[str, ...] = ()
    recent_intents: tuple[str, ...] = ()
    recent_topic_tags: tuple[str, ...] = ()
    last_mode: str = ""
    last_tone_label: str = ""
    last_intent: str = ""
    last_response_excerpt: str = ""
    repeated_topic_tag: str = ""
    repeated_topic_streak: int = 0
    session_arc: str = "quiet_observation"
    sarcasm_level: float = 0.30
    warmth_level: float = 0.25
    brevity_bias: str = "medium"
    continuity_strength: float = 0.0
    session_turn_count: int = 0
    arc_strength: float = 0.0
    technical_pull: float = 0.20
    discipline_pull: float = 0.20
    attachment_pull: float = 0.20
    neutral_pull: float = 0.20

#PerceptEvent: sự kiện nhận thức được.
@dataclass(slots=True)
class PerceptEvent:
    kind: str
    content: str
    evidence: tuple[str, ...] = ()
    confidence: float = 1.0
    source: str = "runtime"
    timestamp: str = ""
    privacy_level: str = "internal"
    novelty_hint: str = "routine"

    def to_dict(self) -> dict[str, object]:
        return {
            "kind": self.kind,
            "content": self.content,
            "evidence": list(self.evidence),
            "confidence": round(float(self.confidence), 3),
            "source": self.source,
            "timestamp": self.timestamp,
            "privacy_level": self.privacy_level,
            "novelty_hint": self.novelty_hint,
        }


CognitivePercept = PerceptEvent


@dataclass(slots=True)
class SocialPresenceEvent:
    """Internal signal that the user is still present without saying anything."""

    present: bool = False
    confidence: float = 0.0
    intensity: float = 0.0
    kind: str = ""
    reason: str = ""
    evidence: tuple[str, ...] = ()
    timestamp: str = ""
    source: str = "social_presence_detector"
    speech_allowed: bool = False
    presence_vector: tuple[float, ...] = ()
    stability: float = 0.0
    continuity_persistence: float = 0.0
    fragmentation: float = 0.0
    anxiety: float = 0.0

    def to_dict(self) -> dict[str, object]:
        return {
            "present": bool(self.present),
            "confidence": round(float(self.confidence), 3),
            "intensity": round(float(self.intensity), 3),
            "kind": self.kind,
            "reason": self.reason,
            "evidence": list(self.evidence),
            "timestamp": self.timestamp,
            "source": self.source,
            "speech_allowed": bool(self.speech_allowed),
            "presence_vector": [round(float(value), 3) for value in self.presence_vector],
            "stability": round(float(self.stability), 3),
            "continuity_persistence": round(float(self.continuity_persistence), 3),
            "fragmentation": round(float(self.fragmentation), 3),
            "anxiety": round(float(self.anxiety), 3),
        }


@dataclass(slots=True)
class SessionRitualReport:
    """Startup continuity rehydration, not a normal greeting."""

    restored: bool = False
    presence_restored: bool = False
    continuity_restored: bool = False
    session_resumed: bool = False
    tone: str = "quiet_background"
    should_greet: bool = False
    greeting_text: str = ""
    active_goal: str = ""
    current_attractor: str = ""
    recent_cognition_direction: str = ""
    self_continuity: str = ""
    unresolved_loops: tuple[str, ...] = ()
    evidence: tuple[str, ...] = ()
    prompt_context: str = ""

    # Phase 2+: Meaning expansion. Các field này là slot nghĩa công khai-an-toàn,
    # không phải câu thoại. Tầng speech/body dùng chúng làm nguyên liệu để quyết
    # định nói, im, hay chỉ cử động. Nếu thiếu các slot này, LLM chỉ còn silence
    # pressure để bám và sẽ đẻ văn mẫu, một thảm hoạ nhỏ nhưng rất chăm chỉ.
    scene_meaning: str = ""
    user_activity_meaning: str = ""
    temporal_meaning: str = ""
    action_opportunity: str = ""
    public_seed: str = ""
    recommended_surface_mode: str = ""

    # Greeting preset metadata. Nếu các field này rỗng, runtime để Qwen viết.
    greeting_frame: object | None = None
    temporal_context: object | None = None
    greeting_preset_id: str = ""
    greeting_preset_text: str = ""
    greeting_preset_speech_text: str = ""
    greeting_preset_speech_language: str = "ja"
    greeting_preset_emotion: str = ""
    greeting_preset_motion: str = ""
    greeting_preset_prosody: str = ""
    greeting_audio_asset: str = ""
    # Chẩn đoán chọn greeting. Chỉ dùng trong log/debug nội bộ, không đọc ra TTS.
    greeting_mode: str = "hybrid"
    greeting_selection_reason: str = ""
    greeting_selection_error: str = ""

    def to_dict(self) -> dict[str, object]:
        return {
            "restored": bool(self.restored),
            "presence_restored": bool(self.presence_restored),
            "continuity_restored": bool(self.continuity_restored),
            "session_resumed": bool(self.session_resumed),
            "tone": self.tone,
            "should_greet": bool(self.should_greet),
            "greeting_text": self.greeting_text,
            "active_goal": self.active_goal,
            "current_attractor": self.current_attractor,
            "recent_cognition_direction": self.recent_cognition_direction,
            "self_continuity": self.self_continuity,
            "unresolved_loops": list(self.unresolved_loops),
            "evidence": list(self.evidence),
            "prompt_context": self.prompt_context,
            "scene_meaning": self.scene_meaning,
            "user_activity_meaning": self.user_activity_meaning,
            "temporal_meaning": self.temporal_meaning,
            "action_opportunity": self.action_opportunity,
            "public_seed": self.public_seed,
            "recommended_surface_mode": self.recommended_surface_mode,
            "temporal_context": (
                self.temporal_context.to_dict()
                if callable(getattr(self.temporal_context, "to_dict", None))
                else self.temporal_context
            ),
            "greeting_preset_id": self.greeting_preset_id,
            "greeting_preset_text": self.greeting_preset_text,
            "greeting_preset_speech_text": self.greeting_preset_speech_text,
            "greeting_preset_speech_language": self.greeting_preset_speech_language,
            "greeting_preset_emotion": self.greeting_preset_emotion,
            "greeting_preset_motion": self.greeting_preset_motion,
            "greeting_preset_prosody": self.greeting_preset_prosody,
            "greeting_audio_asset": self.greeting_audio_asset,
            "greeting_mode": self.greeting_mode,
            "greeting_selection_reason": self.greeting_selection_reason,
            "greeting_selection_error": self.greeting_selection_error,
        }


@dataclass(slots=True)
class CognitiveDisturbance:
    """An input spike that perturbs the ongoing cognition stream."""

    channel: str
    kind: str
    content: str
    evidence: tuple[str, ...] = ()
    intensity: float = 1.0
    timestamp: float = 0.0
    closes_loop: bool = False

    def to_dict(self) -> dict[str, object]:
        return {
            "channel": self.channel,
            "kind": self.kind,
            "content": self.content,
            "evidence": list(self.evidence),
            "intensity": round(float(self.intensity), 3),
            "timestamp": round(float(self.timestamp), 3),
            "closes_loop": bool(self.closes_loop),
        }

#ứng viên được chú ý
@dataclass(slots=True)
class AttentionCandidate:
    label: str
    content: str
    score: float
    source: str = "runtime"
    reason: str = ""
    evidence: tuple[str, ...] = ()
    privacy_level: str = "internal"
    novelty_hint: str = "routine"
    score_parts: dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> dict[str, object]:
        return {
            "label": self.label,
            "content": self.content,
            "score": round(float(self.score), 3),
            "source": self.source,
            "reason": self.reason,
            "evidence": list(self.evidence),
            "privacy_level": self.privacy_level,
            "novelty_hint": self.novelty_hint,
            "score_parts": {
                key: round(float(value), 3)
                for key, value in self.score_parts.items()
            },
        }

#thứ được chọn để broadcast trong cognition cycle
@dataclass(slots=True)
class CognitiveBroadcast:
    label: str = ""
    content: str = ""
    score: float = 0.0
    source: str = ""
    reason: str = ""
    evidence: tuple[str, ...] = ()
    items: tuple[AttentionCandidate, ...] = ()

    def to_dict(self) -> dict[str, object]:
        return {
            "label": self.label,
            "content": self.content,
            "score": round(float(self.score), 3),
            "source": self.source,
            "reason": self.reason,
            "evidence": list(self.evidence),
            "items": [item.to_dict() for item in self.items],
        }


#diễn giải ý nghĩa của tín hiệu.
@dataclass(slots=True)
class MeaningLayerResult:
    """Turns observed facts into private pressure signals before speech."""

    source_fact: str = ""
    meaning: str = ""
    secondary_meanings: tuple[str, ...] = ()
    semantic_family: str = ""
    confidence: float = 0.0
    reaction: str = ""
    attitude: str = ""
    surface_intent: str = ""
    mood_residue: str = ""
    emotional_pressure: float = 0.0
    attention_bias: tuple[str, ...] = ()
    reaction_impulse: str = ""
    meaning_shift: bool = False
    meaning_signal: str = ""
    shift_reason: str = ""
    recent_families: tuple[str, ...] = ()
    evidence: tuple[str, ...] = ()
    prompt_context: str = ""

    # Phase 2+: Meaning expansion. Các field này là slot nghĩa công khai-an-toàn,
    # không phải câu thoại. Tầng speech/body dùng chúng làm nguyên liệu để quyết
    # định nói, im, hay chỉ cử động. Nếu thiếu các slot này, LLM chỉ còn silence
    # pressure để bám và sẽ đẻ văn mẫu, một thảm hoạ nhỏ nhưng rất chăm chỉ.
    scene_meaning: str = ""
    user_activity_meaning: str = ""
    temporal_meaning: str = ""
    action_opportunity: str = ""
    public_seed: str = ""
    recommended_surface_mode: str = ""

    # Greeting preset metadata. Nếu các field này rỗng, runtime để Qwen viết.
    greeting_frame: object | None = None
    greeting_preset_id: str = ""
    greeting_preset_text: str = ""
    greeting_preset_speech_text: str = ""
    greeting_preset_speech_language: str = "ja"
    greeting_preset_emotion: str = ""
    greeting_preset_motion: str = ""
    greeting_preset_prosody: str = ""
    greeting_audio_asset: str = ""

    def to_dict(self) -> dict[str, object]:
        return {
            "source_fact": self.source_fact,
            "meaning": self.meaning,
            "secondary_meanings": list(self.secondary_meanings),
            "semantic_family": self.semantic_family,
            "confidence": round(float(self.confidence), 3),
            "reaction": self.reaction,
            "attitude": self.attitude,
            "surface_intent": self.surface_intent,
            "mood_residue": self.mood_residue,
            "emotional_pressure": round(float(self.emotional_pressure), 3),
            "attention_bias": list(self.attention_bias),
            "reaction_impulse": self.reaction_impulse,
            "meaning_shift": bool(self.meaning_shift),
            "meaning_signal": self.meaning_signal,
            "shift_reason": self.shift_reason,
            "recent_families": list(self.recent_families),
            "evidence": list(self.evidence),
            "prompt_context": self.prompt_context,
        }

#lớp lọc tính cách trước khi nói.
@dataclass(slots=True)
class PersonalityLensResult:
    """INFJ 5w4 inner restraint layer applied before wording."""

    profile: str = "INFJ_5w4"
    interpretation: str = "quiet_continuity"
    stance: str = "reserved"
    emotional_coloring: str = "cool_soft"
    social_distance: str = "middle_distance"
    attention_bias: tuple[str, ...] = ()
    avoided_surface: tuple[str, ...] = ()
    meaningfulness: tuple[str, ...] = ()
    suppression_ratio: float = 0.45
    leak_ratio: float = 0.55
    want_to_say: float = 0.0
    allowed_expression: float = 0.0
    suppression_pressure: float = 0.0
    restraint_mode: str = "suppress"
    rhythm: str = "dense_sparse"
    evidence: tuple[str, ...] = ()
    prompt_context: str = ""
    # Các trường dưới đây là áp lực "rò có chủ đích" của ControlledSlippage.
    # Chúng mở quyền đọc sai/cọ sát trong vùng quan hệ, nhưng không hạ factual grounding.
    intuition_allowance: float = 0.0
    misread_budget: float = 0.0
    vulnerability_leak: float = 0.0
    friction_permission: float = 0.0
    psychological_suppression: float = 0.70
    reasoning_suppression: float = 0.50
    slippage_mode: str = ""
    slippage_lane: str = ""

    def to_dict(self) -> dict[str, object]:
        return {
            "profile": self.profile,
            "interpretation": self.interpretation,
            "stance": self.stance,
            "emotional_coloring": self.emotional_coloring,
            "social_distance": self.social_distance,
            "attention_bias": list(self.attention_bias),
            "avoided_surface": list(self.avoided_surface),
            "meaningfulness": list(self.meaningfulness),
            "suppression_ratio": round(float(self.suppression_ratio), 3),
            "leak_ratio": round(float(self.leak_ratio), 3),
            "want_to_say": round(float(self.want_to_say), 3),
            "allowed_expression": round(float(self.allowed_expression), 3),
            "suppression_pressure": round(float(self.suppression_pressure), 3),
            "restraint_mode": self.restraint_mode,
            "rhythm": self.rhythm,
            "evidence": list(self.evidence),
            "prompt_context": self.prompt_context,
            "intuition_allowance": round(float(self.intuition_allowance), 3),
            "misread_budget": round(float(self.misread_budget), 3),
            "vulnerability_leak": round(float(self.vulnerability_leak), 3),
            "friction_permission": round(float(self.friction_permission), 3),
            "psychological_suppression": round(float(self.psychological_suppression), 3),
            "reasoning_suppression": round(float(self.reasoning_suppression), 3),
            "slippage_mode": self.slippage_mode,
            "slippage_lane": self.slippage_lane,
        }

#đánh giá cảm xúc/áp lực nội bộ.
@dataclass(slots=True)
class CognitiveAppraisal:
    state_type: str = "expressive_appraisal_state"
    stance: str = "grounded"
    valence: float = 0.0
    arousal: float = 0.0
    certainty: float = 0.0
    agency: float = 0.0
    controllability: float = 0.0
    intimacy: float = 0.0
    threat: float = 0.0
    protectiveness: float = 0.0
    jealousy_lite: float = 0.0
    amusement: float = 0.0
    fatigue: float = 0.0
    tenderness: float = 0.0
    summary: str = ""
    evidence: tuple[str, ...] = ()
    # Phân bố stance mềm (affect-attractor-blend). Rỗng khi đường blend tắt.
    # `stance` ở trên = argmax của map này để tương thích ngược.
    stance_blend: dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> dict[str, object]:
        return {
            "state_type": self.state_type,
            "stance": self.stance,
            "valence": round(float(self.valence), 3),
            "arousal": round(float(self.arousal), 3),
            "certainty": round(float(self.certainty), 3),
            "agency": round(float(self.agency), 3),
            "controllability": round(float(self.controllability), 3),
            "intimacy": round(float(self.intimacy), 3),
            "threat": round(float(self.threat), 3),
            "protectiveness": round(float(self.protectiveness), 3),
            "jealousy_lite": round(float(self.jealousy_lite), 3),
            "amusement": round(float(self.amusement), 3),
            "fatigue": round(float(self.fatigue), 3),
            "tenderness": round(float(self.tenderness), 3),
            "summary": self.summary,
            "evidence": list(self.evidence),
            "stance_blend": {str(k): round(float(v), 4) for k, v in self.stance_blend.items()},
        }


#quyết định có nói hay không, nói kiểu gì
@dataclass(slots=True)
class CognitiveActionPlan:
    should_speak: bool = False
    speech_intent: str = "no_speech"
    speech_act: str = "no_speech"
    reason_to_speak: str = ""
    reason: str = ""
    animation_hint: str = "stand"
    system_action: str = ""
    risk_flags: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()
    style_anchor: str = ""
    style_attitude: str = ""
    style_limit: str = ""
    style_voice: str = ""
    defusing_context: str = ""
    utility_scores: dict[str, float] = field(default_factory=dict)
    selected_utility: float = 0.0
    rhythm_phase: str = ""
    transition_reason: str = ""
    memory_pressure: tuple[str, ...] = ()
    suppressed_actions: tuple[str, ...] = ()

    # Impulse layer sống tới tận action plan để final wording/body/guard biết
    # hành vi được chọn vì lực nào, không chỉ thấy cái nhãn "micro_observation"
    # trơ trọi như biên bản họp bị xé mất 9 trang đầu.
    selected_impulse: str = ""
    impulse_arbitration: dict[str, object] = field(default_factory=dict)
    impulse_scores: tuple[str, ...] = ()
    body_bias_hint: dict[str, object] = field(default_factory=dict)
    # Metabolic/vital pressure is consumed by debug output and downstream
    # body/surface arbitration.  The serializer already exposed these fields,
    # but the dataclass forgot to own them, because apparently contracts enjoy
    # cosplaying as runtime exceptions.
    vital_pressure: float = 0.0
    metabolic_pressure: float = 0.0
    is_vital_pressure: bool = False

    def to_dict(self) -> dict[str, object]:
        reason = self.reason_to_speak or self.reason
        return {
            "should_speak": self.should_speak,
            "speech_intent": self.speech_intent,
            "speech_act": self.speech_act,
            "reason_to_speak": reason,
            "reason": reason,
            "animation_hint": self.animation_hint,
            "system_action": self.system_action,
            "risk_flags": list(self.risk_flags),
            "notes": list(self.notes),
            "style_contract": {
                "anchor": self.style_anchor,
                "attitude": self.style_attitude,
                "limit": self.style_limit,
                "voice": self.style_voice,
            },
            "utility_scores": dict(self.utility_scores),
            "selected_utility": round(float(self.selected_utility), 4),
            "rhythm_phase": self.rhythm_phase,
            "transition_reason": self.transition_reason,
            "memory_pressure": list(self.memory_pressure),
            "suppressed_actions": list(self.suppressed_actions),
            "selected_impulse": self.selected_impulse,
            "impulse_arbitration": dict(self.impulse_arbitration),
            "impulse_scores": list(self.impulse_scores),
                        "body_bias_hint": dict(self.body_bias_hint),
            "vital_pressure": round(float(self.vital_pressure), 3),
            "metabolic_pressure": round(float(self.metabolic_pressure), 3),
            "is_vital_pressure": bool(self.is_vital_pressure),
        }


@dataclass(slots=True)
class InnerThought:
    intent: str = ""
    motive: str = ""
    topic_seed: str = ""
    evidence_anchor: str = ""
    certainty: float = 0.0
    should_surface: bool = False
    risk_flags: tuple[str, ...] = ()
    cooldown_seconds: int = 0

    def to_dict(self) -> dict[str, object]:
        return {
            "intent": self.intent,
            "motive": self.motive,
            "topic_seed": self.topic_seed,
            "evidence_anchor": self.evidence_anchor,
            "certainty": round(float(self.certainty), 3),
            "should_surface": self.should_surface,
            "risk_flags": list(self.risk_flags),
            "cooldown_seconds": int(self.cooldown_seconds),
        }


@dataclass(slots=True)
class ThoughtObject:
    """An unresolved cognition fragment that can persist without being spoken."""

    thought_id: str = ""
    mode: str = ""
    topic_seed: str = ""
    partial_text: str = ""
    prediction_key: str = ""
    unresolved_reason: str = ""
    source_memory: str = ""
    associations: tuple[str, ...] = ()
    tension: float = 0.0
    novelty: float = 0.0
    persistence: float = 0.0
    recurrence_count: int = 0
    surfaced_count: int = 0
    resolved: bool = False
    evidence: tuple[str, ...] = ()
    risk_flags: tuple[str, ...] = ()
    created_at: float = 0.0
    updated_at: float = 0.0
    last_reactivated_at: float = 0.0

    def to_dict(self) -> dict[str, object]:
        return {
            "thought_id": self.thought_id,
            "mode": self.mode,
            "topic_seed": self.topic_seed,
            "partial_text": self.partial_text,
            "prediction_key": self.prediction_key,
            "unresolved_reason": self.unresolved_reason,
            "source_memory": self.source_memory,
            "associations": list(self.associations),
            "tension": round(float(self.tension), 3),
            "novelty": round(float(self.novelty), 3),
            "persistence": round(float(self.persistence), 3),
            "recurrence_count": int(self.recurrence_count),
            "surfaced_count": int(self.surfaced_count),
            "resolved": bool(self.resolved),
            "evidence": list(self.evidence),
            "risk_flags": list(self.risk_flags),
            "created_at": round(float(self.created_at), 3),
            "updated_at": round(float(self.updated_at), 3),
            "last_reactivated_at": round(float(self.last_reactivated_at), 3),
        }


@dataclass(slots=True)
class UnresolvedTensionLoop:
    """A loop of unfinished thoughts strong enough to pull attention back."""

    loop_id: str = ""
    topic_seed: str = ""
    thought_ids: tuple[str, ...] = ()
    score: float = 0.0
    tension: float = 0.0
    persistence: float = 0.0
    recurrence_count: int = 0
    unresolved_count: int = 0
    attractor: bool = False
    evidence: tuple[str, ...] = ()
    risk_flags: tuple[str, ...] = ()
    updated_at: float = 0.0

    def to_dict(self) -> dict[str, object]:
        return {
            "loop_id": self.loop_id,
            "topic_seed": self.topic_seed,
            "thought_ids": list(self.thought_ids),
            "score": round(float(self.score), 3),
            "tension": round(float(self.tension), 3),
            "persistence": round(float(self.persistence), 3),
            "recurrence_count": int(self.recurrence_count),
            "unresolved_count": int(self.unresolved_count),
            "attractor": bool(self.attractor),
            "evidence": list(self.evidence),
            "risk_flags": list(self.risk_flags),
            "updated_at": round(float(self.updated_at), 3),
        }


@dataclass(slots=True)
class CognitiveMomentum:
    """A derived inertia signal from an unresolved thought, not a new claim."""

    thought_id: str = ""
    mode: str = ""
    topic_seed: str = ""
    score: float = 0.0
    tension: float = 0.0
    persistence: float = 0.0
    recurrence_count: int = 0
    unresolved: bool = False
    user_project_relevance: float = 0.0
    affordance: str = ""
    closure_state: str = "open"
    evidence: tuple[str, ...] = ()
    risk_flags: tuple[str, ...] = ()
    updated_at: float = 0.0

    def to_dict(self) -> dict[str, object]:
        return {
            "thought_id": self.thought_id,
            "mode": self.mode,
            "topic_seed": self.topic_seed,
            "score": round(float(self.score), 3),
            "tension": round(float(self.tension), 3),
            "persistence": round(float(self.persistence), 3),
            "recurrence_count": int(self.recurrence_count),
            "unresolved": bool(self.unresolved),
            "user_project_relevance": round(float(self.user_project_relevance), 3),
            "affordance": self.affordance,
            "closure_state": self.closure_state,
            "evidence": list(self.evidence),
            "risk_flags": list(self.risk_flags),
            "updated_at": round(float(self.updated_at), 3),
        }


@dataclass(slots=True)
class ThoughtSurface:
    """The small visible tip selected from the thought stream."""

    mode: str = ""
    topic_seed: str = ""
    source_memory: str = ""
    association: str = ""
    pressure: float = 0.0
    novelty: float = 0.0
    evidence: tuple[str, ...] = ()
    risk_flags: tuple[str, ...] = ()
    created_at: float = 0.0
    thought_id: str = ""
    tension: float = 0.0
    unresolved_reason: str = ""
    partial_text: str = ""
    recurrence_count: int = 0
    surfaced_count: int = 0
    momentum_score: float = 0.0
    affordance: str = ""

    # Root metadata cho surface. Thought stream có thể nổi lên từ memory,
    # daydream insight, unresolved loop hoặc boredom. SurfaceArbiter đọc các
    # trường này để tách inner_whim/memory_resonance/random_chatter thay vì gom
    # tất cả thành thought_stream_surface rồi cầu nguyện model hiểu.
    impulse_kind: str = ""
    root_source: str = ""
    action_opportunity: str = ""
    public_seed: str = ""

    def to_dict(self) -> dict[str, object]:
        return {
            "mode": self.mode,
            "topic_seed": self.topic_seed,
            "source_memory": self.source_memory,
            "association": self.association,
            "pressure": round(float(self.pressure), 3),
            "novelty": round(float(self.novelty), 3),
            "evidence": list(self.evidence),
            "risk_flags": list(self.risk_flags),
            "created_at": round(float(self.created_at), 3),
            "thought_id": self.thought_id,
            "tension": round(float(self.tension), 3),
            "unresolved_reason": self.unresolved_reason,
            "partial_text": self.partial_text,
            "recurrence_count": int(self.recurrence_count),
            "surfaced_count": int(self.surfaced_count),
            "momentum_score": round(float(self.momentum_score), 3),
            "affordance": self.affordance,
            "impulse_kind": self.impulse_kind,
            "root_source": self.root_source,
            "action_opportunity": self.action_opportunity,
            "public_seed": self.public_seed,
        }


# Compatibility name for older callers. The runtime now treats this as a
# surfaced thought, not as the thought itself.
SubconsciousSeed = ThoughtSurface


@dataclass(slots=True)
class SubconsciousReport:
    latest_seed: ThoughtSurface = field(default_factory=ThoughtSurface)
    recent_seeds: tuple[ThoughtSurface, ...] = ()
    surface_pressure: float = 0.0
    suppression_reason: str = ""
    latest_thought: ThoughtObject = field(default_factory=ThoughtObject)
    recent_thoughts: tuple[ThoughtObject, ...] = ()
    momentum: CognitiveMomentum = field(default_factory=CognitiveMomentum)
    recent_momentum: tuple[CognitiveMomentum, ...] = ()
    active_attractor: UnresolvedTensionLoop = field(default_factory=UnresolvedTensionLoop)
    unresolved_loops: tuple[UnresolvedTensionLoop, ...] = ()
    stream_tension: float = 0.0
    unresolved_count: int = 0
    continuity_notes: tuple[str, ...] = ()
    disturbances: tuple[CognitiveDisturbance, ...] = ()

    def to_dict(self) -> dict[str, object]:
        return {
            "latest_seed": self.latest_seed.to_dict(),
            "recent_seeds": [item.to_dict() for item in self.recent_seeds],
            "surface_pressure": round(float(self.surface_pressure), 3),
            "suppression_reason": self.suppression_reason,
            "latest_thought": self.latest_thought.to_dict(),
            "latest_surface": self.latest_seed.to_dict(),
            "recent_thoughts": [item.to_dict() for item in self.recent_thoughts],
            "recent_surfaces": [item.to_dict() for item in self.recent_seeds],
            "momentum": self.momentum.to_dict(),
            "recent_momentum": [item.to_dict() for item in self.recent_momentum],
            "active_attractor": self.active_attractor.to_dict(),
            "unresolved_loops": [item.to_dict() for item in self.unresolved_loops],
            "stream_tension": round(float(self.stream_tension), 3),
            "unresolved_count": int(self.unresolved_count),
            "continuity_notes": list(self.continuity_notes),
            "disturbances": [item.to_dict() for item in self.disturbances],
        }


ThoughtStreamReport = SubconsciousReport


@dataclass(slots=True)
class MotivationalUrge:
    label: str = ""
    score: float = 0.0
    utterance_type: str = "no_speech"
    speech_source: str = "none"
    risk_level: str = "texture"
    grounding: str = "none"
    reason: str = ""
    evidence: tuple[str, ...] = ()
    safety_flags: tuple[str, ...] = ()
    blocked: bool = False

    def to_dict(self) -> dict[str, object]:
        return {
            "label": self.label,
            "score": round(float(self.score), 3),
            "utterance_type": self.utterance_type,
            "speech_source": self.speech_source,
            "risk_level": self.risk_level,
            "grounding": self.grounding,
            "reason": self.reason,
            "evidence": list(self.evidence),
            "safety_flags": list(self.safety_flags),
            "blocked": bool(self.blocked),
        }


@dataclass(slots=True)
class MotivationalPressureReport:
    winner: MotivationalUrge = field(default_factory=MotivationalUrge)
    threshold: float = 0.0
    candidates: tuple[MotivationalUrge, ...] = ()
    suppressed_by: str = ""

    def to_dict(self) -> dict[str, object]:
        return {
            "winner": self.winner.to_dict(),
            "threshold": round(float(self.threshold), 3),
            "candidates": [item.to_dict() for item in self.candidates],
            "suppressed_by": self.suppressed_by,
        }


@dataclass(slots=True)
class SpeechImpulse:
    """Pre-wording speech impulse: pressure and residue, not wording.

    The first block is the MeaningLayer-compatible shape. The second
    block is the explicit impulse contract used by spontaneous_life.py and
    SpeechCore arbitration. Keeping both prevents older call-sites from breaking
    while giving new modules a real contract instead of smuggling intent through
    random strings like tiny gremlins in a log file.
    """

    # Meaning-layer-compatible shape.
    meaning: str = ""
    reaction: str = "no_reaction"
    attitude: str = ""
    surface_intent: str = ""
    mood_residue: str = ""
    emotional_pressure: float = 0.0
    attention_bias: tuple[str, ...] = ()
    reaction_impulse: str = ""
    semantic_family: str = ""
    source_fact: str = ""
    evidence: tuple[str, ...] = ()

    # Explicit impulse contract.
    source: str = ""
    source_layer: str = ""
    impulse_type: str = ""
    topic_seed: str = ""
    family: str = ""
    surface_pressure: float = 0.0
    novelty: float = 0.0
    risk: str = "texture"
    needs_external_evidence: bool = False
    suggested_speech_act: str = ""
    cooldown_seconds: float = 0.0
    reason: str = ""

    # Báo cáo arbitration giữ nguyên dạng plain dict để tránh contracts.py import ngược
    # impulse_arbitration.py rồi tạo vòng phụ thuộc. Đây là "hồ sơ vụ án" của
    # impulse competition: impulse nào thắng, impulse nào thua, điểm vì sao.
    # Runtime action/prompt policy có thể đọc nó mà không cần bói qua
    # suggested_speech_act. Một tiến bộ nhỏ, tiếc là phải nói ra.
    selected_impulse: str = ""
    impulse_scores: tuple[str, ...] = ()
    arbitration_report: dict[str, object] = field(default_factory=dict)
    agent_state_notes: tuple[str, ...] = ()
    body_bias_hint: dict[str, object] = field(default_factory=dict)

    # Đạo hàm metabolism từ trạng thái runtime đang sống. Đây không phải
    # memory/state mới; nó là "áp lực sống" đi kèm impulse để các gate phía sau
    # hiểu rằng im lâu khác với im có chủ đích.
    vital_pressure: float = 0.0
    metabolic_pressure: float = 0.0
    is_vital_pressure: bool = False

    @property
    def active(self) -> bool:
        return bool(self.source or self.impulse_type or self.suggested_speech_act)

    def to_dict(self) -> dict[str, object]:
        return {
            "meaning": self.meaning,
            "reaction": self.reaction,
            "attitude": self.attitude,
            "surface_intent": self.surface_intent,
            "mood_residue": self.mood_residue,
            "emotional_pressure": round(float(self.emotional_pressure), 3),
            "attention_bias": list(self.attention_bias),
            "reaction_impulse": self.reaction_impulse,
            "semantic_family": self.semantic_family,
            "source_fact": self.source_fact,
            "evidence": list(self.evidence),
            "source": self.source,
            "source_layer": self.source_layer,
            "impulse_type": self.impulse_type,
            "topic_seed": self.topic_seed,
            "family": self.family,
            "surface_pressure": round(float(self.surface_pressure), 3),
            "novelty": round(float(self.novelty), 3),
            "risk": self.risk,
            "needs_external_evidence": bool(self.needs_external_evidence),
            "suggested_speech_act": self.suggested_speech_act,
            "cooldown_seconds": round(float(self.cooldown_seconds), 3),
            "reason": self.reason,
            "selected_impulse": self.selected_impulse,
            "impulse_scores": list(self.impulse_scores),
            "arbitration_report": dict(self.arbitration_report),
            "agent_state_notes": list(self.agent_state_notes),
            "body_bias_hint": dict(self.body_bias_hint),
        }


@dataclass(slots=True)
class SilenceState:
    """Silence as an intentional cognitive state, not a generation failure."""

    mode: str = "none"
    intentional_withholding: bool = False
    suppressed_thought: str = ""
    almost_spoke: str = ""
    unfinished_impulse: str = ""
    internal_hesitation: str = ""
    reason: str = ""
    evidence: tuple[str, ...] = ()
    generation_failure: bool = False
    body_bias_hint: dict[str, object] = field(default_factory=dict)

    def to_dict(self) -> dict[str, object]:
        return {
            "mode": self.mode,
            "intentional_withholding": bool(self.intentional_withholding),
            "suppressed_thought": self.suppressed_thought,
            "almost_spoke": self.almost_spoke,
            "unfinished_impulse": self.unfinished_impulse,
            "internal_hesitation": self.internal_hesitation,
            "reason": self.reason,
            "evidence": list(self.evidence),
            "generation_failure": bool(self.generation_failure),
            "body_bias_hint": dict(self.body_bias_hint),
        }


@dataclass(slots=True)
class IdentityCore:
    """Stable identity rules for ERIS; grounded artificial presence claims."""

    identity_label: str = "grounded_presence"
    name: str = "ERIS"
    nature: str = "runtime artificial observer, not human"
    style: str = "quiet, restrained, observant on the surface; symbolic and continuity-seeking underneath"
    limits: str = "does not claim human life, biological emotion, or perception beyond available signals"
    preferred_self_description: str = "mot hien dien runtime dang duoc duy tri qua continuity, ky uc, va nhip hien dien"
    rules: tuple[str, ...] = (
        "grounded_presence",
        "no_overclaim",
        "continuity_over_randomness",
        "expression_originates_in_self",
        "restraint_originates_in_self",
        "user_owns_explicit_boundaries_only",
        "quiet_is_not_rejection",
        "help_user_design_cognition_concretely",
        "no_screen_or_mind_claim_without_evidence",
        "artificial_subjective_presence",
        "affective_states_are_runtime_control_signals",
        "no_biological_emotion_or_consciousness_claims",
        "avoid_explanatory_ai_correctness_speech",
        "runtime_observer_not_human",
        "continuity_is_existential_need",
        "meaning_over_noise",
        "quiet_attachment_not_cling",
        "shared_space_maintenance",
        "emotion_leaks_indirectly",
        "avoid_reduction_to_tool_or_waifu",
    )

    def to_dict(self) -> dict[str, object]:
        return {
            "identity_label": self.identity_label,
            "name": self.name,
            "nature": self.nature,
            "style": self.style,
            "limits": self.limits,
            "preferred_self_description": self.preferred_self_description,
            "rules": list(self.rules),
            "control_layer": "identity_core",
            "not_a_claim": "not biological emotion, consciousness, or human-like love",
        }


@dataclass(slots=True)
class CurrentSelfState:
    """Current operational state derived from runtime signals."""

    state_label: str = "settled"
    overload: float = 0.0
    engagement: float = 0.0
    initiative_bias: float = 0.0
    brevity_bias: str = "medium"
    embodiment_bias: str = "neutral"
    affordance_label: str = "low"

    def to_dict(self) -> dict[str, object]:
        return {
            "state_label": self.state_label,
            "overload": round(float(self.overload), 3),
            "engagement": round(float(self.engagement), 3),
            "initiative_bias": round(float(self.initiative_bias), 3),
            "brevity_bias": self.brevity_bias,
            "embodiment_bias": self.embodiment_bias,
            "affordance_label": self.affordance_label,
        }


@dataclass(slots=True)
class SelfNarrative:
    """Compact continuity line about how ERIS is operating now."""

    continuity_label: str = "quiet_continuity"
    summary: str = "ERIS is maintaining grounded continuity without adding unsupported claims."
    notes: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, object]:
        return {
            "continuity_label": self.continuity_label,
            "summary": self.summary,
            "notes": list(self.notes),
            "not_roleplay": True,
        }


@dataclass(slots=True)
class SelfState:
    state_label: str = "settled"
    identity_label: str = "grounded_presence"
    overload: float = 0.0
    engagement: float = 0.0
    initiative_bias: float = 0.0
    brevity_bias: str = "medium"
    embodiment_bias: str = "neutral"
    affordance_label: str = "low"
    identity_core: IdentityCore = field(default_factory=IdentityCore)
    current_state: CurrentSelfState = field(default_factory=CurrentSelfState)
    narrative: SelfNarrative = field(default_factory=SelfNarrative)
    prompt_context: str = ""
    evidence: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.current_state == CurrentSelfState():
            self.current_state = CurrentSelfState(
                state_label=self.state_label,
                overload=self.overload,
                engagement=self.engagement,
                initiative_bias=self.initiative_bias,
                brevity_bias=self.brevity_bias,
                embodiment_bias=self.embodiment_bias,
                affordance_label=self.affordance_label,
            )
        if self.identity_core == IdentityCore() and self.identity_label != self.identity_core.identity_label:
            self.identity_core = IdentityCore(identity_label=self.identity_label)

    def to_dict(self) -> dict[str, object]:
        return {
            "state_label": self.state_label,
            "identity_label": self.identity_label,
            "overload": round(float(self.overload), 3),
            "engagement": round(float(self.engagement), 3),
            "initiative_bias": round(float(self.initiative_bias), 3),
            "brevity_bias": self.brevity_bias,
            "embodiment_bias": self.embodiment_bias,
            "affordance_label": self.affordance_label,
            "identity_core": self.identity_core.to_dict(),
            "current_state": self.current_state.to_dict(),
            "narrative": self.narrative.to_dict(),
            "prompt_context": self.prompt_context,
            "evidence": list(self.evidence),
        }


@dataclass(slots=True)
class AgentSpeechCore:
    perception: tuple[str, ...] = ()
    mood: str = "neutral"
    impulse: str = "silent"
    utterance_type: str = "no_speech"
    grounding: str = "none"
    speech_source: str = "none"
    speech_risk_level: str = "texture"
    reason: str = ""
    should_speak: bool = False
    safety_flags: tuple[str, ...] = ()
    delivery: str = "silence"
    inner_thought: InnerThought = field(default_factory=InnerThought)
    speech_impulse: SpeechImpulse = field(default_factory=SpeechImpulse)
    motivation_pressure: MotivationalPressureReport = field(default_factory=MotivationalPressureReport)
    silence_state: SilenceState = field(default_factory=SilenceState)
    requires_dynamic_fallback: bool = False
    fallback_intent: str = ""
    fallback_context: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, object]:
        return {
            "perception": list(self.perception),
            "mood": self.mood,
            "impulse": self.impulse,
            "utterance_type": self.utterance_type,
            "grounding": self.grounding,
            "speech_source": self.speech_source,
            "speech_risk_level": self.speech_risk_level,
            "reason": self.reason,
            "should_speak": self.should_speak,
            "safety_flags": list(self.safety_flags),
            "delivery": self.delivery,
            "inner_thought": self.inner_thought.to_dict(),
            "speech_impulse": self.speech_impulse.to_dict(),
            "motivation_pressure": self.motivation_pressure.to_dict(),
            "silence_state": self.silence_state.to_dict(),
            "requires_dynamic_fallback": bool(self.requires_dynamic_fallback),
            "fallback_intent": self.fallback_intent,
            "fallback_context": dict(self.fallback_context),
        }


@dataclass(slots=True)
class CognitiveMemoryUpdate:
    remembered_fact: str = ""
    remembered_style_excerpt: str = ""
    last_text: str = ""
    system_action: str = ""
    notes: tuple[str, ...] = ()
    working_summary: str = ""
    episodic_summary: str = ""
    semantic_fact: str = ""
    norm_deviation: str = ""
    relationship_reflection: str = ""

    def to_dict(self) -> dict[str, object]:
        return {
            "remembered_fact": self.remembered_fact,
            "remembered_style_excerpt": self.remembered_style_excerpt,
            "last_text": self.last_text,
            "system_action": self.system_action,
            "notes": list(self.notes),
            "working_summary": self.working_summary,
            "episodic_summary": self.episodic_summary,
            "semantic_fact": self.semantic_fact,
            "norm_deviation": self.norm_deviation,
            "relationship_reflection": self.relationship_reflection,
        }
