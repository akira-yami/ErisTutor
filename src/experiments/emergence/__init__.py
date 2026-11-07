# -*- coding: utf-8 -*-
"""Công cụ kiểm chứng lịch sử nội bộ, ablation và soak của Eris."""

from .ablation import (
    AblationReport,
    BehavioralProjection,
    run_causal_ablation,
)
from .report import ExperimentReport
from .runner import ExperimentSpec, ProbeSpec, run_benchmark_suite, run_paired_experiment
from .soak import SoakReport, SoakSpec, run_simulated_soak

__all__ = [
    "AblationReport",
    "BehavioralProjection",
    "ExperimentReport",
    "ExperimentSpec",
    "ProbeSpec",
    "SoakReport",
    "SoakSpec",
    "run_benchmark_suite",
    "run_causal_ablation",
    "run_paired_experiment",
    "run_simulated_soak",
]

from .validation import CausalValidationReport, run_causal_validation
from .acceptance import AcceptanceProfile, PDF_ACCEPTANCE, profile
