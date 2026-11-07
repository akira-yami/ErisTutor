# -*- coding: utf-8 -*-
"""Test các cơ chế bộ nhớ ERIS còn hoạt động:
1. AI curator quyết định meaning; admission chỉ kiểm cấu trúc và provenance.
2. Proactive Associative Recall (gợi ý chủ động theo nhịp sinh học, cảm xúc, việc dang dở).
3. Memory Tiering & Core Anchors (working, habit, core_anchor với decay immunity & curator protection).
"""
import time
from pathlib import Path

from memory.living_user.curator import LivingUserCuratorConfig, LivingUserMemoryCurator
from memory.living_user.models import (
    ProfileContextPacket,
    ProfileOperation,
    ProfileRecord,
    UserEpisode,
    UserMemoryScope,
    determine_memory_tier,
)
from memory.living_user.service import LivingUserMemoryConfig, LivingUserMemoryService
from memory.living_user.store import UserProfileStore
from memory.memory_admission import MemoryAdmissionPolicy


# ==============================================================================
# Nấc 1: Entity Grounding & Metaphor Guard
# ==============================================================================

def test_admission_does_not_guess_meaning_from_keywords():
    policy = MemoryAdmissionPolicy()

    # Noise record
    rec_noise = ProfileRecord(
        id="noise_1",
        path="learning.active_topics",
        value="nè",
        explicitness="observed",
        confidence=0.8,
        evidence_ids=("ev1",),
    )
    decision = policy.decide_profile_record(rec_noise)
    assert decision.admitted is True  # Meaning belongs to AI, not a word list.

    # Metaphor record
    rec_joke = ProfileRecord(
        id="joke_1",
        path="personal.relation_patent",
        value="chủ sở hữu bằng sáng chế có quan hệ tình thân",
        explicitness="observed",
        confidence=0.9,
        evidence_ids=("ev2",),
    )
    decision_joke = policy.decide_profile_record(rec_joke)
    assert decision_joke.admitted is True


def test_tier1_curator_cleans_noise_and_metaphor(tmp_path: Path):
    store = UserProfileStore(tmp_path / "living_user_profile.json")
    store.apply([
        ProfileOperation(op="add", path="learning.active_topics", value="nè", evidence_ids=("ev1",)),
        ProfileOperation(op="add", path="communication.assistant_identifier", value="$232232930220", evidence_ids=("ev2",)),
        ProfileOperation(op="add", path="personal.relation_patent", value="bằng sáng chế tình thân", evidence_ids=("ev3",)),
        ProfileOperation(op="add", path="communication.preferred_language", value="vi", evidence_ids=("ev4",)),
    ])
    assert len(store.all()) == 4

    class MockLivingUser:
        profile_store = store
        embedding_index = None

    import json
    ids = [r.id for r in store.all() if r.path != "communication.preferred_language"]
    def reviewer(system, user, max_tokens):
        return json.dumps({"operations":[{"action":"delete", "record_ids":[rid], "reason":"AI review of supplied evidence"} for rid in ids]})
    curator = LivingUserMemoryCurator(MockLivingUser(), reviewer=reviewer)
    result = curator.run()
    # Only the AI proposal may decide the meaning of these records.
    assert result.deleted >= 3
    remaining = store.all()
    assert len(remaining) == 1
    assert remaining[0].path == "communication.preferred_language"
    assert remaining[0].value == "vi"


# ==============================================================================
# Nấc 4: Memory Tiering & Core Anchors
# ==============================================================================

def test_tier4_determine_memory_tier():
    # Core Anchors
    assert determine_memory_tier("identity.role", value="tự nhận là người tạo ra ERIS") == "core_anchor"
    assert determine_memory_tier("identity.name", value="Hoà") == "core_anchor"
    assert determine_memory_tier("relationship.creator_relationship", value="nhà phát triển ERIS") == "core_anchor"
    assert determine_memory_tier("identity.eris_creation_basis", value="nền cảm xúc nhân tạo") == "core_anchor"

    # Habit
    assert determine_memory_tier("communication.preferred_language", value="vi") == "habit"
    assert determine_memory_tier("learning.style", value="thích đi thẳng vào code") == "habit"
    assert determine_memory_tier("gaming.farming_route", recurrence=3, value="Route 12") == "habit"

    # Working
    assert determine_memory_tier("state.recent_emotional_state", value="đang chán") == "working"
    assert determine_memory_tier("context.current_task", value="debug UI Live2D") == "working"


def test_tier4_core_anchor_decay_immunity(tmp_path: Path):
    store = UserProfileStore(tmp_path / "living_user_profile.json")
    # Add a core_anchor record and a working record with old timestamps
    past_ts = time.time() - 200 * 86400.0  # 200 days ago
    anchor_op = ProfileOperation(
        op="add",
        path="identity.role",
        value="Người tạo ra ERIS",
        tier="core_anchor",
        confidence=0.95,
        explicitness="observed",
        evidence_ids=("ev1",),
    )
    working_op = ProfileOperation(
        op="add",
        path="state.recent_emotional_state",
        value="đang mệt mỏi",
        tier="working",
        confidence=0.70,
        explicitness="observed",
        evidence_ids=("ev2",),
    )
    store.apply([anchor_op, working_op])

    # Manipulate last_confirmed_at into the past
    for r in store.all():
        r.last_confirmed_at = past_ts

    # Run decay
    store.decay()

    records = {r.path: r for r in store.all(include_inactive=True)}
    # Anchor must be 100% immune to decay
    assert records["identity.role"].confidence == 0.95
    assert records["identity.role"].status == "active"
    assert records["identity.role"].tier == "core_anchor"

    # Working record must decay
    assert records["state.recent_emotional_state"].confidence < 0.70


def test_tier4_core_anchor_protected_from_curator_deletion(tmp_path: Path):
    store = UserProfileStore(tmp_path / "living_user_profile.json")
    anchor_op = ProfileOperation(
        op="add",
        path="identity.role",
        value="Người tạo ra ERIS",
        tier="core_anchor",
        evidence_ids=("ev1",),
    )
    res = store.apply([anchor_op])
    anchor_id = res.record_ids[0]

    # Attempt to delete core_anchor via curator operations
    curator_delete = [{"action": "delete", "record_ids": [anchor_id], "reason": "delete test"}]
    curator_result = store.apply_curator_operations(curator_delete)
    assert any("core_anchor_protected" in r for r in curator_result.rejected)
    assert store.get("identity.role")[0].status == "active"


# ==============================================================================
# Nấc 2: Proactive Associative Recall
# ==============================================================================

def test_tier2_proactive_memory_cue_generation(tmp_path: Path):
    service = LivingUserMemoryService(tmp_path)

    # Setup state continuity: user was bored/tired in previous session
    service.apply_turn_operations([
        ProfileOperation(
            op="add",
            path="state.recent_emotional_state",
            value="đang chán; muốn ERIS nghĩ điều gì thú vị",
            confidence=0.90,
            explicitness="explicit",
            evidence_ids=("turn_mood",),
        )
    ])

    # Setup episode continuity: open loops
    service.episode_store.add(UserEpisode(
        id="ep_last",
        session_id="sess_1",
        title="Nâng cấp memory ERIS",
        summary="Thảo luận triển khai 4 nấc kiến trúc bộ nhớ",
        open_loops=("hoàn thiện unit test 4 nấc", "kiểm tra Live2D"),
        importance=0.88,
    ))

    # 1. Direct compile_proactive_cue method
    cue_text = service.compile_proactive_cue()
    assert "Thời điểm:" in cue_text
    assert "đang chán" in cue_text
    assert "hoàn thiện unit test 4 nấc" in cue_text
    assert "INFJ" in cue_text

    # 2. Casual greeting query triggers proactive nudge in context packet
    greeting_packet = service.compile_context("alo", include_proactive=False)
    assert greeting_packet.proactive_cue != ""
    assert "đang chán" in greeting_packet.proactive_cue
    rendered_prompt = greeting_packet.render_prompt_context()
    assert "[PROACTIVE_MEMORY_NUDGE]" in rendered_prompt
    assert "Mạch việc dang dở" in rendered_prompt

    # 3. Standard task query does not spam proactive nudge unless explicitly asked
    task_packet = service.compile_context("viết code python kiểm tra json", include_proactive=False)
    assert task_packet.proactive_cue == ""
    assert "[PROACTIVE_MEMORY_NUDGE]" not in task_packet.render_prompt_context()


def test_ai_curator_rewrites_contextual_interest(tmp_path: Path):
    complex_val = "Chơi Pokemmo; quan tâm farm EV Speed ở Route 12 Unova, dùng Sweet Scent; biết Dunsparce cho HP EV nên cần tránh đánh."
    # 2. Curator rewrites bloated interest to durable preference
    store = UserProfileStore(tmp_path / "living_user_profile.json")
    store.apply([
        ProfileOperation(
            op="add",
            path="interests.games.pokemmo",
            value=complex_val,
            evidence_ids=("ev_pokemmo",),
        )
    ])
    assert len(store.all()) == 1

    class MockLivingUser:
        profile_store = store
        embedding_index = None

    import json
    rid = store.all()[0].id
    def reviewer(system, user, max_tokens):
        return json.dumps({"operations":[{"action":"rewrite", "record_ids":[rid], "value":"Chơi Pokemmo", "reason":"AI distinguishes durable interest from one scene"}]})
    curator = LivingUserMemoryCurator(MockLivingUser(), reviewer=reviewer)
    result = curator.run()
    assert result.rewritten >= 1
    recs = store.all()
    assert len(recs) == 1
    assert recs[0].value == "Chơi Pokemmo"


def test_tier2_stale_open_loops_cutoff(tmp_path: Path):
    service = LivingUserMemoryService(tmp_path)

    now = time.time()
    # Add an old episode with open loops from 10 days ago (e.g. Pokemmo route 12 question)
    service.episode_store.add(UserEpisode(
        id="ep_old",
        session_id="sess_old",
        title="Hỏi về Route 12",
        summary="Thắc mắc tỉ lệ ra ngựa ở Route 12 Unova",
        open_loops=("sao vùng unova route 12 farm con ngựa mà lúc ra con khác vậy", "con dunsparce á"),
        started_at=now - 10 * 86400,
        ended_at=now - 10 * 86400 + 1800,
        importance=0.80,
    ))

    # Add a fresh episode from 2 hours ago
    service.episode_store.add(UserEpisode(
        id="ep_fresh",
        session_id="sess_fresh",
        title="Làm việc với Eris",
        summary="Cùng Eris tối ưu hóa memory",
        open_loops=("kiểm tra tính năng living user panel",),
        started_at=now - 7200,
        ended_at=now - 3600,
        importance=0.85,
    ))

    # Proactive cue should ONLY include fresh loop (< 72h), NEVER 10-day-old loop!
    cue = service.compile_proactive_cue(now=now)
    assert "kiểm tra tính năng living user panel" in cue
    assert "route 12" not in cue.lower()
    assert "dunsparce" not in cue.lower()


def test_topical_relevance_gate_prevents_unrelated_interest_leak(tmp_path: Path):
    service = LivingUserMemoryService(tmp_path)

    service.apply_turn_operations([
        # Identity / role record (eligible for generic background)
        ProfileOperation(
            op="add",
            path="identity.role",
            value="Người tạo ra ERIS",
            confidence=0.95,
            explicitness="explicit",
            evidence_ids=("ev_creator",),
        ),
        # Gaming specific interest (should NOT leak into unrelated queries)
        ProfileOperation(
            op="add",
            path="interests.games.pokemmo",
            value="Chơi Pokemmo",
            confidence=0.85,
            explicitness="explicit",
            evidence_ids=("ev_game",),
        ),
    ])

    # Query about opening Eris / project relation
    packet = service.compile_context("tui cần eris nên mở á")
    retrieved_paths = [it.path for it in packet.items]
    assert "identity.role" in retrieved_paths
    # Pokemmo interest MUST NOT be retrieved when query has zero topical relevance!
    assert "interests.games.pokemmo" not in retrieved_paths

    # Query directly mentioning gaming / pokemmo MUST retrieve it
    game_packet = service.compile_context("bạn có nhớ tôi chơi game gì không")
    game_paths = [it.path for it in game_packet.items]
    assert "interests.games.pokemmo" in game_paths


def test_tier4_initiative_permission_tiering():
    # Explicit initiative permission is a core anchor contract
    assert determine_memory_tier("relationship.initiative_permission", explicitness="explicit", value="cho phép làm phiền thoải mái") == "core_anchor"
    assert determine_memory_tier("communication.interruption_tolerance", explicitness="explicit", value="high") == "core_anchor"
    assert determine_memory_tier("relationship.interruption_tolerance", explicitness="explicit", value="cứ tự nhiên bắt chuyện") == "core_anchor"

    # Inferred tolerance / habit
    assert determine_memory_tier("relationship.initiative", explicitness="inferred", recurrence=1) == "habit"
    assert determine_memory_tier("communication.interruption_tolerance", explicitness="inferred") == "habit"


def test_tier2_proactive_cue_with_initiative_and_screen_context(tmp_path: Path):
    service = LivingUserMemoryService(tmp_path)
    service.apply_turn_operations([
        ProfileOperation(
            op="add",
            path="relationship.initiative_permission",
            value="Người dùng cho phép ERIS làm phiền thoải mái",
            confidence=0.98,
            explicitness="explicit",
            evidence_ids=("ev_perm",),
        )
    ])
    session_ctx = {
        "active_app": "PokeMMO.exe",
        "screen_summary": "Đang đứng ở Route 12 Unova chuẩn bị farm",
    }
    cue = service.compile_proactive_cue(session_context=session_ctx)
    assert "Quyền chủ động:" in cue
    assert "thoải mái bắt chuyện hoặc làm phiền" in cue
    assert "PokeMMO.exe" in cue
    assert "Route 12 Unova" in cue
    assert "Quan sát màn hình thực tế:" in cue

    # Also verify that casual greeting context packet includes this proactive cue with screen info
    packet = service.compile_context("alo", session_context=session_ctx)
    assert packet.proactive_cue != ""
    assert "PokeMMO.exe" in packet.proactive_cue
    assert "thoải mái bắt chuyện hoặc làm phiền" in packet.proactive_cue


