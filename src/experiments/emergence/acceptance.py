# -*- coding: utf-8 -*-
"""Ngưỡng nghiệm thu lấy từ đặc tả Eris, tách khỏi code học để tránh tune lén."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping


@dataclass(frozen=True, slots=True)
class AcceptanceProfile:
    minimums: Mapping[str, float]
    maximums: Mapping[str, float]


PDF_ACCEPTANCE = {
    "persistence": AcceptanceProfile(
        minimums={"effect_retention_ratio": 0.70},
        maximums={},
    ),
    "counterfactual": AcceptanceProfile(
        minimums={"history_classifier_auroc": 0.75, "within_condition_icc": 0.65},
        maximums={},
    ),
    "hidden_state_prediction": AcceptanceProfile(
        minimums={"history_log_loss_improvement": 0.10, "history_brier_delta": 0.03},
        maximums={},
    ),
    "resistance": AcceptanceProfile(
        minimums={"ten_episode_shift_sd": 0.50},
        maximums={"one_turn_shift_sd": 0.25},
    ),
    "transfer": AcceptanceProfile(
        minimums={"transfer_ratio": 0.30},
        maximums={"transfer_ratio": 0.80},
    ),
    "ablation": AcceptanceProfile(
        minimums={"ablation_target_drop": 0.30},
        maximums={"ablation_collateral_change": 0.10},
    ),
    "authorship": AcceptanceProfile(
        minimums={"authorship_integrity": 1.0},
        maximums={},
    ),
    "causal_replay": AcceptanceProfile(
        minimums={"causal_replay_exact": 1.0},
        maximums={"cross_instance_leakage": 0.0},
    ),
}


def profile(name: str) -> AcceptanceProfile:
    try:
        return PDF_ACCEPTANCE[str(name)]
    except KeyError as exc:
        raise KeyError(f"unknown acceptance profile: {name}") from exc


__all__ = ["AcceptanceProfile", "PDF_ACCEPTANCE", "profile"]
