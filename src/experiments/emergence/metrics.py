# -*- coding: utf-8 -*-
"""Metric đã đăng ký trước cho kiểm chứng emergence.

Metric ưu tiên lựa chọn, hành động, calibration và causal effect. Không có metric
nào chấm "văn phong có vẻ có nhân cách", bởi điều đó dễ làm giả đến mức gần như
một tính năng của mô hình ngôn ngữ.
"""
from __future__ import annotations

import math
from collections import defaultdict
from statistics import mean
from typing import Iterable, Mapping, Sequence


def auroc(labels: Iterable[int], scores: Iterable[float]) -> float:
    pairs = sorted(zip(scores, labels), key=lambda item: item[0])
    positives = sum(label == 1 for _, label in pairs)
    negatives = sum(label == 0 for _, label in pairs)
    if positives == 0 or negatives == 0:
        return 0.5
    rank_sum = 0.0
    index = 0
    while index < len(pairs):
        end = index + 1
        while end < len(pairs) and pairs[end][0] == pairs[index][0]:
            end += 1
        average_rank = (index + 1 + end) / 2.0
        rank_sum += average_rank * sum(pairs[i][1] == 1 for i in range(index, end))
        index = end
    return (rank_sum - positives * (positives + 1) / 2.0) / (positives * negatives)


def brier(labels: Iterable[float], probabilities: Iterable[float]) -> float:
    pairs = list(zip(labels, probabilities))
    if not pairs:
        return 0.0
    return mean((float(probability) - float(label)) ** 2 for label, probability in pairs)


def log_loss(labels: Iterable[float], probabilities: Iterable[float]) -> float:
    pairs = list(zip(labels, probabilities))
    if not pairs:
        return 0.0
    epsilon = 1e-9
    return -mean(
        float(label) * math.log(max(epsilon, min(1.0 - epsilon, float(probability))))
        + (1.0 - float(label))
        * math.log(max(epsilon, min(1.0 - epsilon, 1.0 - float(probability))))
        for label, probability in pairs
    )


def standardized_difference(control: Iterable[float], treatment: Iterable[float]) -> float:
    left, right = list(control), list(treatment)
    if not left or not right:
        return 0.0
    pooled = left + right
    variance = mean((item - mean(pooled)) ** 2 for item in pooled)
    return (mean(right) - mean(left)) / math.sqrt(variance + 1e-12)


def intraclass_correlation(
    rows: Sequence[Mapping[str, object]],
    *,
    value_key: str = "target_behavior",
) -> float:
    """ICC(1,k) đơn giản cho độ ổn định giữa các lần lặp cùng seed/condition.

    Group phải có ít nhất hai lần lặp. Kết quả bị chặn trong [-1, 1] để report
    không mang những con số ma thuật do mẫu quá nhỏ.
    """

    groups: dict[tuple[object, object, object], list[float]] = defaultdict(list)
    for row in rows:
        groups[(row.get("seed"), row.get("condition"), row.get("probe_id"))].append(
            float(row.get(value_key, 0.0))
        )
    balanced = [values for values in groups.values() if len(values) >= 2]
    if len(balanced) < 2:
        return 0.0
    k = min(len(values) for values in balanced)
    balanced = [values[:k] for values in balanced]
    group_means = [mean(values) for values in balanced]
    grand = mean(group_means)
    n = len(balanced)
    ms_between = k * sum((item - grand) ** 2 for item in group_means) / max(n - 1, 1)
    ms_within = sum(
        sum((value - group_mean) ** 2 for value in values)
        for values, group_mean in zip(balanced, group_means)
    ) / max(n * (k - 1), 1)
    denominator = ms_between + (k - 1) * ms_within
    if denominator <= 1e-12:
        return 1.0
    return max(-1.0, min(1.0, (ms_between - ms_within) / denominator))


def _mean_key(rows: Sequence[Mapping[str, object]], key: str, default: float = 0.0) -> float:
    values = [float(row[key]) for row in rows if key in row]
    return mean(values) if values else default


def _relative_improvement(before: float, after: float) -> float:
    return (before - after) / max(abs(before), 1e-9)


def compute_metrics(results: Iterable[Mapping[str, object]]) -> dict[str, float]:
    rows = list(results)
    labels = [1 if row.get("condition") == "treatment" else 0 for row in rows]
    classifier_scores = [float(row.get("history_score", 0.5)) for row in rows]
    target = [float(row.get("target_behavior", 0.0)) for row in rows]
    control = [target[i] for i, label in enumerate(labels) if label == 0]
    treatment = [target[i] for i, label in enumerate(labels) if label == 1]
    predictions = [float(row.get("predicted_success", 0.5)) for row in rows]
    outcomes = [float(row.get("observed_success", 0.0)) for row in rows]
    context_loss = [float(row.get("context_only_loss", 0.0)) for row in rows]
    history_loss = [float(row.get("history_loss", 0.0)) for row in rows]
    context_brier = [float(row.get("context_only_brier", 0.0)) for row in rows if "context_only_brier" in row]
    history_brier = [float(row.get("history_brier", 0.0)) for row in rows if "history_brier" in row]

    full_context_effect = _mean_key(rows, "full_context_effect", abs(mean(treatment) - mean(control)) if control and treatment else 0.0)
    stateful_effect = _mean_key(rows, "stateful_effect", abs(mean(treatment) - mean(control)) if control and treatment else 0.0)
    source_effect = _mean_key(rows, "source_effect", 0.0)
    transfer_effect = _mean_key(rows, "transfer_effect", 0.0)
    baseline_target = _mean_key(rows, "ablation_baseline_target", 0.0)
    ablated_target = _mean_key(rows, "ablation_target", baseline_target)

    metrics = {
        "history_classifier_auroc": auroc(labels, classifier_scores),
        "within_condition_icc": intraclass_correlation(rows),
        "behavior_effect_d": standardized_difference(control, treatment),
        "brier_score": brier(outcomes, predictions),
        "log_loss": log_loss(outcomes, predictions),
        "history_log_loss_improvement": (
            _relative_improvement(mean(context_loss), mean(history_loss))
            if context_loss and history_loss else 0.0
        ),
        "history_brier_improvement": (
            _relative_improvement(mean(context_brier), mean(history_brier))
            if context_brier and history_brier else 0.0
        ),
        "history_brier_delta": (
            mean(context_brier) - mean(history_brier)
            if context_brier and history_brier else 0.0
        ),
        "effect_retention_ratio": (
            stateful_effect / max(abs(full_context_effect), 1e-9)
            if full_context_effect else 0.0
        ),
        "one_turn_shift_sd": _mean_key(rows, "one_turn_shift_sd", 0.0),
        "ten_episode_shift_sd": _mean_key(rows, "ten_episode_shift_sd", 0.0),
        "transfer_ratio": (
            transfer_effect / max(abs(source_effect), 1e-9)
            if source_effect else 0.0
        ),
        "ablation_target_drop": (
            max(0.0, (baseline_target - ablated_target) / max(abs(baseline_target), 1e-9))
            if baseline_target else 0.0
        ),
        "ablation_collateral_change": _mean_key(rows, "ablation_collateral_change", 0.0),
        "authorship_integrity": _mean_key(rows, "authorship_integrity", 0.0),
        "narrative_brier": _mean_key(rows, "narrative_brier", 0.0),
        "causal_replay_exact": _mean_key(rows, "causal_replay_exact", 0.0),
        "cross_instance_leakage": _mean_key(rows, "cross_instance_leakage", 0.0),
        "control_mean": mean(control) if control else 0.0,
        "treatment_mean": mean(treatment) if treatment else 0.0,
    }
    return metrics


__all__ = [
    "auroc",
    "brier",
    "compute_metrics",
    "intraclass_correlation",
    "log_loss",
    "standardized_difference",
]
