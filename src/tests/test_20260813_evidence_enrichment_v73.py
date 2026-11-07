from __future__ import annotations

from types import SimpleNamespace

from runtime.evidence_enrichment import EvidenceEnrichmentCompiler
from runtime.session_turn_ledger import SessionTurnLedger
from runtime.claim_domain_contract import validate_claim_manifest
from runtime.self_biography_guard import user_probe_fake_memory_premise
from runtime.ai_orchestrated.contracts import IntentionPlan
from runtime.ai_orchestrated.initiative import SpeechIntent


class _Record:
    def __init__(self, path, value, *, evidence_ids=(), confidence=0.9, explicitness="explicit", sensitivity="normal"):
        self.path = path
        self.value = value
        self.evidence_ids = tuple(evidence_ids)
        self.confidence = confidence
        self.explicitness = explicitness
        self.sensitivity = sensitivity
        self.is_current = True
        self.source = "test"
        self.trust_weight = confidence


class _Episode:
    def __init__(self, summary, *, evidence_ids=("turn:1",), title="", importance=0.8):
        self.id = "ep-1"
        self.title = title
        self.summary = summary
        self.evidence_ids = tuple(evidence_ids)
        self.importance = importance
        self.scope = SimpleNamespace(kind="global")


class _EpisodeStore:
    def __init__(self, rows=()):
        self.rows = tuple(rows)

    def search(self, query, *, limit=3):
        return self.rows[:limit]

    def recent(self, *, limit=3):
        return self.rows[:limit]


class _Living:
    def __init__(self, records=(), episodes=()):
        self._records = tuple(records)
        self.episode_store = _EpisodeStore(episodes)

    def list_memories(self, **kwargs):
        return self._records


class _Subject:
    def self_view(self):
        return {
            "dominant_facets": [
                {
                    "facet_id": "self.value.verify",
                    "kind": "value",
                    "statement": "Giá trị đang được hành vi hỗ trợ: kiểm chứng trước khi kết luận",
                    "confidence": 0.86,
                    "evidence_refs": ["decision:verify:1"],
                }
            ]
        }


def _host(records=(), episodes=(), user_turns=()):
    ledger = SessionTurnLedger()
    for text in user_turns:
        ledger.record_user(text)
    return SimpleNamespace(
        living_user=_Living(records, episodes),
        subject_service=_Subject(),
        _ensure_session_turn_ledger=lambda: ledger,
        logger=SimpleNamespace(debug=lambda *a, **k: None),
    )


def test_user_probe_gap_tracks_current_repo_context_not_random_coffee():
    host = _host(user_turns=(
        "đọc repo rồi fix lỗi TTS giúp tôi",
        "đừng chỉ thêm lớp chặn, tăng đầu vào evidence nữa",
    ))
    bundle = EvidenceEnrichmentCompiler(host).compile(
        speech_act="user_probe",
        semantic_goal="understand user",
        topic="ask one real user-model gap",
    )
    assert bundle.open_user_gap is not None
    assert bundle.open_user_gap.key == "collaboration_preference"
    assert bundle.open_user_gap.key != "taste_preference"


def test_profile_enrichment_only_exports_grounded_explicit_or_strong_observed_records():
    host = _host(records=(
        _Record("workflow.patch_style", "prefers direct patches", evidence_ids=("turn:7",), explicitness="explicit"),
        _Record("preferences.coffee", "black", evidence_ids=(), explicitness="explicit"),
        _Record("relationship.guess", "likes being interrogated", evidence_ids=("turn:8",), explicitness="inferred"),
        _Record("technical.observed", "checks diffs", evidence_ids=("turn:9",), explicitness="observed", confidence=0.82),
        _Record("technical.weak", "likes rust", evidence_ids=("turn:10",), explicitness="observed", confidence=0.51),
    ))
    bundle = EvidenceEnrichmentCompiler(host).compile(speech_act="user_probe", topic="debugging preferences")
    texts = [item.text for item in bundle.user_facts]
    assert any("workflow.patch_style" in item for item in texts)
    assert any("technical.observed" in item for item in texts)
    assert not any("preferences.coffee" in item for item in texts)
    assert not any("relationship.guess" in item for item in texts)
    assert not any("technical.weak" in item for item in texts)
    assert "turn:7" in bundle.evidence_refs


def test_bundle_contains_recent_turn_episode_and_self_provenance():
    host = _host(
        records=(_Record("workflow.review", "wants concrete diffs", evidence_ids=("turn:3",)),),
        episodes=(_Episode("Hai người đã cùng debug một lỗi TTS multi-segment.", evidence_ids=("turn:4", "turn:5")),),
        user_turns=("fix mạnh đi rồi gửi file cho tôi",),
    )
    bundle = EvidenceEnrichmentCompiler(host).compile(speech_act="user_probe", topic="how to work together")
    assert bundle.recent_user_turns[0].claim_domain == "conversation_history"
    assert bundle.shared_episodes[0].claim_domain == "shared_episode"
    assert bundle.self_facts[0].claim_domain == "self_model"
    assert "session-user-turn:1" in bundle.evidence_refs
    assert "turn:4" in bundle.evidence_refs
    assert "decision:verify:1" in bundle.evidence_refs


def test_new_positive_domains_require_real_evidence():
    for domain in ("user_profile", "shared_episode", "self_model"):
        denied = validate_claim_manifest(
            [{"domain": domain, "claim": "grounded fact", "evidence_refs": []}],
            allowed_domains=[domain],
            allowed_evidence_refs=["ev:1"],
        )
        assert not denied.allowed
        allowed = validate_claim_manifest(
            [{"domain": domain, "claim": "grounded fact", "evidence_refs": ["ev:1"]}],
            allowed_domains=[domain],
            allowed_evidence_refs=["ev:1"],
        )
        assert allowed.allowed


def test_real_shared_episode_provenance_can_support_a_user_probe_memory_bridge():
    text = "Lần trước bạn có kể về cách debug đó, giờ anh còn thích kiểu ấy không?"
    assert user_probe_fake_memory_premise(
        text,
        claim_manifest=[{"domain": "shared_episode", "claim": "shared debug episode", "evidence_refs": ["turn:4"]}],
        allowed_evidence_refs=["turn:4"],
    ) is False
    assert user_probe_fake_memory_premise(
        text,
        claim_manifest=[{"domain": "opinion", "claim": "just a vibe", "evidence_refs": []}],
        allowed_evidence_refs=[],
    ) is True


def test_speech_intent_serializes_positive_evidence_bundle():
    plan = IntentionPlan.silence(channel="autonomous", reason="test")
    intent = SpeechIntent(
        plan=plan,
        mode="random_chat",
        anchor_type="internal",
        anchor_id="test",
        evidence_refs=("turn:1",),
        allowed_claim_domains=("user_profile",),
        evidence_bundle={"schema": "eris.evidence_enrichment.v1", "open_user_gap": {"key": "decision_style"}},
    )
    payload = intent.to_dict()
    assert payload["evidence_bundle"]["open_user_gap"]["key"] == "decision_style"


def test_surface_and_public_reply_schemas_accept_positive_evidence_domains() -> None:
    """V12 chỉ giữ claim-domain schema dùng cho public grounding hiện hành.

    SURFACE_REALIZATION_SCHEMA thuộc writer hai tầng cũ và đã bị retire; không
    được kéo schema đó quay lại chỉ để giữ test V73.
    """
    from runtime.ai_orchestrated.model_router import CLAIM_MANIFEST_SCHEMA

    public_domains = set(CLAIM_MANIFEST_SCHEMA["items"]["properties"]["domain"]["enum"])
    for domain in ("user_profile", "shared_episode", "self_model"):
        assert domain in public_domains


