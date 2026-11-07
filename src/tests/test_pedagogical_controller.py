from dataclasses import replace

from runtime.pedagogical_contracts import (
    ComprehensionState,
    TeachingConcept,
    TeachingPlan,
    TeachingReceipt,
)
from runtime.pedagogical_controller import (
    PedagogicalLimits,
    expire_if_needed,
    note_audio_finished,
    note_public_commit,
    observe_user_turn,
    select_next_action,
    start_episode,
)


def _plan():
    return TeachingPlan(
        "Understand the Core 7 model",
        (
            TeachingConcept("core-7", "Core 7 is about escaping stuckness.", "Explain Core 7."),
            TeachingConcept("contrast", "Core 8 differs in its motivation.", "Contrast Core 7 and 8."),
            TeachingConcept("apply", "Apply the distinction to an example.", "Apply the model."),
        ),
    )


def _episode_with_chunk():
    return start_episode(_plan(), now=100.0)


def test_acknowledgement_does_not_increase_mastery():
    episode = _episode_with_chunk()
    result = observe_user_turn(episode, "Ừ, hiểu rồi.", evidence=(), now=101.0)
    assert result.episode.comprehension.estimated_mastery == episode.comprehension.estimated_mastery
    assert "self_report_understood" in result.evidence_codes


def test_common_acknowledgements_cannot_gain_mastery_from_positive_tag():
    episode = _episode_with_chunk()
    for acknowledgement in (
        "yes", "yeah", "yep", "okay", "ok", "uh-huh", "mm-hmm", "got it", "understood",
        "I understand", "makes sense", "sure", "yes, got it",
    ):
        result = observe_user_turn(episode, acknowledgement, evidence=("user_paraphrase",), now=101.0)
        assert result.episode.comprehension.estimated_mastery == episode.comprehension.estimated_mastery


def test_topic_change_cannot_turn_paraphrase_tag_into_mastery():
    episode = _episode_with_chunk()
    result = observe_user_turn(
        episode, "Anyway, about something else.",
        evidence=("topic_change", "user_paraphrase"), now=101.0,
    )
    assert result.episode.comprehension.estimated_mastery == episode.comprehension.estimated_mastery
    assert "topic_change" in result.evidence_codes


def test_topic_change_abandons_episode_instead_of_teaching_another_chunk():
    result = observe_user_turn(_episode_with_chunk(), "Let's discuss another topic.", evidence=("topic_change",), now=101.0)
    assert result.episode.status == "abandoned"
    assert result.action.kind == "abandon"


def test_correct_paraphrase_advances_current_concept():
    episode = _episode_with_chunk()
    result = observe_user_turn(
        episode,
        "7 muốn thoát cảm giác bị mắc kẹt, không chỉ là người nói nhiều.",
        evidence=("user_paraphrase",), now=101.0,
    )
    assert result.episode.comprehension.estimated_mastery > episode.comprehension.estimated_mastery
    assert result.action.kind in {"advance", "probe", "complete"}


def test_explicit_application_still_advances_current_concept():
    episode = _episode_with_chunk()
    result = observe_user_turn(
        episode,
        "In this example, that means avoiding the stuck feeling by trying something new.",
        evidence=("user_application",), now=101.0,
    )
    assert result.episode.comprehension.estimated_mastery > episode.comprehension.estimated_mastery
    assert result.action.kind == "advance"


def test_user_correction_enters_repair_without_lowering_user_mastery():
    episode = _episode_with_chunk()
    result = observe_user_turn(
        episode, "Không, bạn đang hiểu sai ý mình.", evidence=("eris_corrected",), now=101.0,
    )
    assert result.episode.status == "repairing"
    assert result.episode.comprehension.estimated_mastery == episode.comprehension.estimated_mastery


def test_two_stalls_force_wait():
    episode = replace(_episode_with_chunk(), consecutive_stalls=1)
    result = select_next_action(episode, now=101.0)
    assert result.kind == "wait"


def test_mismatched_receipts_are_not_consumed():
    episode = _episode_with_chunk()
    receipts = (
        TeachingReceipt("other", episode.plan_revision, episode.pending_chunk.chunk_id),
        TeachingReceipt(episode.episode_id, episode.plan_revision + 1, episode.pending_chunk.chunk_id),
        TeachingReceipt(episode.episode_id, episode.plan_revision, "other-chunk"),
    )
    assert all(note_public_commit(episode, receipt) == episode for receipt in receipts)


def test_matching_receipt_starts_audio_and_audio_completion_waits_for_user():
    episode = _episode_with_chunk()
    receipt = TeachingReceipt(episode.episode_id, episode.plan_revision, episode.pending_chunk.chunk_id)
    speaking = note_public_commit(episode, receipt)
    assert speaking.status == "speaking"
    assert speaking.autonomous_chunks_used == 1
    assert note_audio_finished(speaking, now=101.0).status == "waiting_user"


def test_repeated_focused_question_marks_uncertainty_without_mastery_gain():
    episode = replace(
        _episode_with_chunk(),
        comprehension=ComprehensionState(estimated_mastery=0.4, confidence=0.7, current_concept_id="core-7"),
    )
    result = observe_user_turn(episode, "But why does that make 7 different?", evidence=("focused_question",), now=101.0)
    assert "repeated_focused_question" in result.evidence_codes
    assert result.episode.comprehension.estimated_mastery == 0.4
    assert result.episode.comprehension.confidence < 0.7


def test_explicit_misconception_assertion_is_a_candidate():
    episode = _episode_with_chunk()
    result = observe_user_turn(
        episode, "Core 7 just means talking a lot.", evidence=("misconception_assertion",), now=101.0,
    )
    assert "candidate_misconception" in result.evidence_codes
    assert result.action.kind == "repair"


def test_episode_expires_after_ten_minutes():
    episode = _episode_with_chunk()
    assert episode.expires_at == 700.0
    assert expire_if_needed(episode, now=700.0).status == "expired"


def test_meaningful_user_turn_refreshes_inactivity_deadline():
    episode = _episode_with_chunk()
    active = observe_user_turn(episode, "Why is that?", evidence=("focused_question",), now=650.0).episode
    assert active.last_activity_at == 650.0
    assert active.expires_at == 1250.0
    assert expire_if_needed(active, now=1249.0).status != "expired"
    assert expire_if_needed(active, now=1250.0).status == "expired"


def test_different_concept_gets_its_own_repair_allowance():
    episode = _episode_with_chunk()
    first = observe_user_turn(episode, "Core 7 means talking.", evidence=("misconception_assertion",), now=101.0).episode
    repaired = note_public_commit(first, TeachingReceipt(first.episode_id, first.plan_revision, first.pending_chunk.chunk_id))
    next_concept = replace(
        note_audio_finished(repaired, now=102.0),
        comprehension=ComprehensionState(current_concept_id="contrast"),
    )
    result = observe_user_turn(
        next_concept, "Core 8 is the same.", evidence=("misconception_assertion",), now=103.0,
    )
    assert result.episode.repair_concept_id == "contrast"
    assert result.action.kind == "repair"


def test_same_misconception_is_limited_to_one_repair():
    episode = _episode_with_chunk()
    first = observe_user_turn(episode, "Core 7 means talking.", evidence=("misconception_assertion",), now=101.0).episode
    repaired = note_public_commit(first, TeachingReceipt(first.episode_id, first.plan_revision, first.pending_chunk.chunk_id))
    result = observe_user_turn(
        note_audio_finished(repaired, now=102.0),
        "Core 7 means talking.", evidence=("misconception_assertion",), now=103.0,
    )
    assert result.action.kind == "wait"
    assert result.action.reason == "repair_limit"


def test_autonomous_budget_exhaustion_forces_wait():
    episode = replace(_episode_with_chunk(), autonomous_chunks_used=2)
    assert select_next_action(episode, now=101.0).kind == "wait"


def test_typed_limits_override_autonomous_and_episode_expiry_defaults():
    limits = PedagogicalLimits(max_autonomous_chunks=0, episode_expiry_seconds=60.0)
    episode = start_episode(_plan(), now=100.0, limits=limits)
    assert episode.expires_at == 160.0
    assert select_next_action(episode, now=101.0, limits=limits).reason == "autonomous_budget_exhausted"
    assert expire_if_needed(episode, now=160.0, limits=limits).status == "expired"
