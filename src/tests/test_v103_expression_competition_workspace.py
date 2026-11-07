# -*- coding: utf-8 -*-
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from cortex.subject.expression_commitment import commitment_payload
from cortex.subject.neuro_control import SelectionResult, WorkspaceBroadcast
from runtime.expression_competition_workspace_v103 import ExpressionCompetitionWorkspaceV103


class _Logger:
    def debug(self, *args, **kwargs):
        pass
    def info(self, *args, **kwargs):
        pass


def _broadcast():
    return WorkspaceBroadcast(signals=(), surprise=0.0, precision=0.7, resource=1.0, social_drive=0.5)


def _coalition(source_id: str, *, origin: str = "expression_urge", target: str | None = None):
    content = target or f"Ý định tự thân {source_id} đang cạnh tranh để được biểu đạt."
    return SimpleNamespace(
        action_kind="speak",
        arguments={
            "semantic_target": content,
            "speech_act": "share_self_originated_thought",
            "expression_commitment": commitment_payload(
                origin=origin,
                source_id=source_id,
                semantic_target=content,
                motive="share",
                pressure=0.8,
                freshness=0.9,
                persistence=0.8,
            ),
        },
    )


def _silence():
    return SimpleNamespace(action_kind="silence", arguments={})


def _frame(*coalitions):
    return SimpleNamespace(
        observation=SimpleNamespace(
            kind="autonomous_opportunity",
            context={"context_key": "autonomous", "runtime_context": {}},
        ),
        coalitions=tuple(coalitions),
    )


def _selection(winner: int, scores: tuple[float, ...], probs: tuple[float, ...], reason: str = "fast"):
    ordered = sorted(scores, reverse=True)
    margin = ordered[0] - ordered[1] if len(ordered) > 1 else 1.0
    return SelectionResult(
        winner_index=winner,
        scores=scores,
        probabilities=probs,
        margin=margin,
        entropy=0.5,
        requires_deliberation=False,
        parallel_review_recommended=False,
        reason=reason,
        broadcast=_broadcast(),
    )


def test_single_commitment_is_not_delayed_by_v103():
    runtime = ExpressionCompetitionWorkspaceV103(logger=_Logger())
    frame = _frame(_coalition("urge:a"))
    original = _selection(0, (0.8,), (1.0,))

    result = runtime.stabilize(frame, original, now=100.0)

    assert result.winner_index == 0
    status = runtime.status(now=100.0)
    assert status["occupant_commitment_id"] == "expression_urge:urge:a"


def test_first_public_competition_occupies_baseline_winner_without_random_tie_break():
    runtime = ExpressionCompetitionWorkspaceV103(logger=_Logger())
    frame = _frame(_coalition("urge:a"), _coalition("urge:b"))

    result = runtime.stabilize(
        frame,
        _selection(0, (0.72, 0.61), (0.70, 0.30)),
        now=100.0,
    )

    assert result.winner_index == 0
    assert runtime.status(now=100.0)["last_event"] == "occupy"


def test_one_frame_challenger_spike_does_not_flip_incumbent():
    runtime = ExpressionCompetitionWorkspaceV103(logger=_Logger())
    frame = _frame(_coalition("urge:a"), _coalition("urge:b"))
    runtime.stabilize(frame, _selection(0, (0.72, 0.61), (0.70, 0.30)), now=100.0)

    result = runtime.stabilize(
        frame,
        _selection(1, (0.60, 0.73), (0.30, 0.70)),
        now=102.0,
    )

    assert result.winner_index == 0
    assert runtime.status(now=102.0)["last_event"] == "hold_incumbent"


def test_sustained_challenger_advantage_eventually_takes_over():
    runtime = ExpressionCompetitionWorkspaceV103(logger=_Logger())
    frame = _frame(_coalition("urge:a"), _coalition("urge:b"))
    runtime.stabilize(frame, _selection(0, (0.72, 0.61), (0.70, 0.30)), now=100.0)
    runtime.stabilize(frame, _selection(1, (0.60, 0.73), (0.30, 0.70)), now=102.0)
    # Hai frame tiếp theo giữ cùng lợi thế để activation tích lũy và vượt band.
    runtime.stabilize(frame, _selection(1, (0.58, 0.75), (0.25, 0.75)), now=104.0)
    result = runtime.stabilize(frame, _selection(1, (0.56, 0.77), (0.22, 0.78)), now=106.0)

    assert result.winner_index == 1
    status = runtime.status(now=106.0)
    assert status["occupant_commitment_id"] == "expression_urge:urge:b"
    assert status["last_event"] == "takeover"


def test_disappeared_incumbent_is_not_kept_as_a_ghost_winner():
    runtime = ExpressionCompetitionWorkspaceV103(logger=_Logger())
    two = _frame(_coalition("urge:a"), _coalition("urge:b"))
    runtime.stabilize(two, _selection(0, (0.72, 0.61), (0.70, 0.30)), now=100.0)

    only_b = _frame(_coalition("urge:b"))
    result = runtime.stabilize(only_b, _selection(0, (0.78,), (1.0,)), now=105.0)

    assert result.winner_index == 0
    assert runtime.status(now=105.0)["occupant_commitment_id"] == "expression_urge:urge:b"


def test_structured_commitment_lane_bypasses_v103_competition_authority():
    runtime = ExpressionCompetitionWorkspaceV103(logger=_Logger())
    frame = _frame(
        _coalition("urge:a"),
        _coalition("arc:1", origin="conversation_arc", target="Tiếp tục beat đã được plan."),
    )
    original = _selection(1, (0.66, 0.80), (0.35, 0.65))

    result = runtime.stabilize(frame, original, now=100.0)

    assert result is original
    assert runtime.status(now=100.0)["occupant_commitment_id"] == ""


def test_non_speech_semantic_authority_is_never_overridden_by_old_occupant():
    runtime = ExpressionCompetitionWorkspaceV103(logger=_Logger())
    speech = _frame(_coalition("urge:a"), _coalition("urge:b"))
    runtime.stabilize(speech, _selection(0, (0.74, 0.60), (0.72, 0.28)), now=100.0)

    frame = _frame(_coalition("urge:a"), _coalition("urge:b"), _silence())
    original = _selection(2, (0.61, 0.60, 0.88), (0.0, 0.0, 1.0), reason="semantic_defer")
    result = runtime.stabilize(frame, original, now=103.0)

    assert result.winner_index == 2
    assert result.reason == "semantic_defer"


def test_public_expression_receipt_releases_workspace_without_public_text_feedback():
    runtime = ExpressionCompetitionWorkspaceV103(logger=_Logger())
    frame = _frame(_coalition("urge:a"), _coalition("urge:b"))
    runtime.stabilize(frame, _selection(0, (0.72, 0.61), (0.70, 0.30)), now=100.0)

    assert runtime.note_expressed("urge:a", now=108.0)
    assert runtime.status(now=108.0)["occupant_commitment_id"] == ""
    row = next(item for item in runtime.snapshot(now=108.0) if item["source_id"] == "urge:a")
    assert row["expressed_count"] == 1
    assert row["activation"] <= 0.12


def test_v103_production_wiring_is_after_neuro_selection_and_before_commit_mapping() -> None:
    """V12 giữ competition workspace như observer/evidence, bỏ self-turn speech lane."""
    root = Path(__file__).resolve().parents[1]
    executive = (root / "runtime" / "ai_orchestrated" / "executive_cortex.py").read_text(encoding="utf-8")
    bootstrap = (root / "runtime" / "coordinator_bootstrap.py").read_text(encoding="utf-8")
    urge = (root / "runtime" / "expression_urge_runtime.py").read_text(encoding="utf-8")

    assert "expression_competition_workspace_v103" in bootstrap
    assert 'getattr(self.owner, "expression_competition_workspace_v103", None)' in executive
    assert "selection = stabilize(" in executive
    assert executive.index("selection = stabilize(") < executive.index("return commit_selection(")
    assert 'note_competition(urge.urge_id' in urge
    assert not (root / "runtime" / "ai_orchestrated" / "self_turn_pipeline.py").exists()


