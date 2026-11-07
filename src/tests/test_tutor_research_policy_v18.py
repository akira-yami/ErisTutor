from __future__ import annotations

from types import SimpleNamespace

import runtime.tutor_research_policy as policy


def test_normalization_and_marker_boundaries() -> None:
    assert policy.normalize_marker_text("Nghiên cứu kỹ Gemini 2.5!") == "nghien cuu ky gemini 2 5"
    assert policy.is_explicit_research_request("Hãy nghiên cứu kỹ chuyện này") is True
    assert policy.is_explicit_research_request("Tôi đang giao việc") is False
    assert policy.is_explicit_search_request("search web giúp tôi") is True


def test_freshness_and_implicit_verification_detection() -> None:
    assert policy.needs_fresh_web("Giá hiện tại của dịch vụ này?") is True
    assert policy.needs_external_verification("Nghe nói Gemini API bị khóa đúng không?") is True
    assert policy.needs_external_verification("Claim này ở v2.4 đúng không?") is True
    assert policy.needs_external_verification("Con mèo này dễ thương đúng không?") is False


def test_standalone_anchor_does_not_invent_history() -> None:
    result = policy.resolve_research_anchor("So sánh Gemini và Gemma", ["Hôm nay trời đẹp"])
    assert result.topic == "So sánh Gemini và Gemma"
    assert result.assumption_note == "query_standalone"
    assert result.misread_risk == "low"


def test_reference_followup_resolves_recent_substantive_anchor() -> None:
    history = [
        "Tôi đang sửa TTS localizer cho tiếng Nhật",
        "Gemma có phù hợp để làm localizer không?",
    ]
    result = policy.resolve_research_anchor("đào sâu phần đó", history)
    assert result.anchor_text == history[-1]
    assert result.topic == history[-1]
    assert result.confidence >= 0.5
    assert result.candidates


def test_focused_correction_keeps_current_focus_and_anchor() -> None:
    result = policy.resolve_research_anchor(
        "Không, ý tôi là phần Gemma",
        ["So sánh Gemini và Gemma cho TTS localizer"],
    )
    assert "Neo hội thoại" in result.topic
    assert "Gemma" in result.topic
    assert result.assumption_note.startswith("focused_followup")


def test_missing_reference_anchor_requires_clarification() -> None:
    result = policy.resolve_research_anchor("phần đó", [])
    assert result.misread_risk == "high"
    assert policy.anchor_resolution_needs_clarification(result) is True
    assert "chưa chắc" in policy.build_anchor_clarification_text(result).lower()


def test_candidate_preview_is_used_in_clarification() -> None:
    result = policy.FollowupAnchorResolution(
        topic="",
        confidence=0.2,
        candidates=(policy.FollowupAnchorCandidate("Một chủ đề rất cụ thể", 0.5, "recent"),),
    )
    text = policy.build_anchor_clarification_text(result)
    assert "Một chủ đề rất cụ thể" in text


def test_explicit_research_always_scores_high() -> None:
    result = policy.score_research_need("Hãy deep research chuyện này", intent="other")
    assert result.should_delegate is True
    assert result.score == 1.0


def test_complex_world_knowledge_crosses_research_threshold() -> None:
    result = policy.score_research_need(
        "Phân tích chi tiết, so sánh nhiều nguồn, kiểm tra lại bằng chứng và đánh giá toàn diện vấn đề này",
        intent="world_knowledge",
        meaning_missing=True,
    )
    assert result.should_delegate is True
    assert "complexity_markers" in result.reason


def test_simple_world_knowledge_stays_on_main_model() -> None:
    result = policy.score_research_need("Mặt trăng là gì?", intent="world_knowledge")
    assert result.should_delegate is False
    assert result.score < 0.58


def test_delegation_modes_follow_runtime_switches(monkeypatch) -> None:
    values = {
        ("tutor", "enabled"): True,
        ("tutor", "research_enabled"): True,
        ("tutor", "quick_search_enabled"): True,
    }
    monkeypatch.setattr(policy, "_runtime_bool", lambda *path, default=True: values.get(tuple(path), default))
    assert policy.score_tutor_delegation("search web giúp tôi", intent="other").mode == "quick_search"
    retired = policy.score_tutor_delegation("deep research giúp tôi", intent="other")
    assert retired.mode == "quick_search"
    assert retired.reason == "research_retired_to_quick_search"

    values[("tutor", "enabled")] = False
    disabled = policy.score_tutor_delegation("search web giúp tôi", intent="other")
    assert disabled.should_delegate is False
    assert disabled.reason == "tutor_disabled"


def test_fresh_or_implicit_claim_routes_quick_search(monkeypatch) -> None:
    monkeypatch.setattr(policy, "_runtime_bool", lambda *path, default=True: True)
    fresh = policy.score_tutor_delegation("Giá hiện tại bao nhiêu?", intent="world_knowledge")
    implicit = policy.score_tutor_delegation("Gemini API còn hoạt động đúng không?", intent="unclear")
    assert fresh.mode == "quick_search"
    assert implicit.mode == "quick_search"


def test_prompt_builders_honor_character_budget_and_scrub_context(monkeypatch) -> None:
    monkeypatch.setattr(policy, "scrub_secrets", lambda value: str(value).replace("SECRET", "[redacted]"))
    search = policy.build_lightweight_search_prompt("X" * 600, context="SECRET", max_chars=260)
    research = policy.build_lightweight_research_prompt("Y" * 600, context="SECRET", max_chars=280)
    assert len(search) <= 260
    assert len(research) <= 280
    assert "SECRET" not in search + research


def test_compact_answer_preserves_source_section() -> None:
    answer = " ".join(f"word{i}" for i in range(180)) + "\nNguồn\nhttps://example.com tài liệu chính thức"
    compact = policy.compact_research_answer(answer, max_words=100)
    assert "Đã rút gọn" in compact
    assert "Nguồn" in compact


def test_compact_answer_without_sources_uses_plain_cap() -> None:
    answer = " ".join(f"word{i}" for i in range(180))
    compact = policy.compact_research_answer(answer, max_words=100)
    assert compact.endswith("[đã rút gọn]")
    assert len(compact.split()) <= 104


def test_clarification_helper_handles_broken_object() -> None:
    class Broken:
        @property
        def confidence(self):
            raise RuntimeError("bad")

    assert policy.anchor_resolution_needs_clarification(Broken()) is True
    assert "phần nào" in policy.build_anchor_clarification_text(Broken()).lower()


def test_candidate_items_support_turn_objects() -> None:
    result = policy.resolve_research_anchor(
        "phần này",
        [SimpleNamespace(text="Đánh giá TTS localizer hiện tại")],
    )
    assert result.anchor_index == 0
    assert policy.resolve_research_topic("phần này", ["Đánh giá TTS localizer hiện tại"]) == result.topic
