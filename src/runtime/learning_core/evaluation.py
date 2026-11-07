"""Observational teaching evaluation. No optimizer and no production policy mutation."""

from __future__ import annotations

from collections import defaultdict
from statistics import mean


def evaluate_attempts(attempts, *, min_samples=5):
    groups = defaultdict(list)
    for attempt in attempts:
        if attempt.get("superseded_by") or attempt.get("result") == "ungraded":
            continue
        groups[attempt.get("teaching_policy", "unknown")].append(attempt)
    reports = {}
    for policy, rows in sorted(groups.items()):
        independent = [
            a for a in rows if a.get("assistance") == "independent" and a.get("result") in {"correct", "incorrect"}
        ]
        # Multiple answers from the same item family do not become independent
        # experimental units. This is still observational, not randomization.
        distinct = {}
        for row in sorted(independent, key=lambda r: (r.get("recorded_at", 0), r["attempt_id"])):
            key = (row["kp_id"], row.get("item_family_id") or row.get("interaction_id") or row["attempt_id"])
            distinct[key] = row
        samples = list(distinct.values())
        phases = {}
        for phase in ("practice", "transfer", "review"):
            subset = [a for a in samples if a.get("phase") == phase]
            phases[phase] = {
                "n": len(subset),
                "success_rate": mean(a["result"] == "correct" for a in subset) if len(subset) >= min_samples else None,
            }
        durations = [a["response_ms"] for a in samples if a.get("response_ms") is not None]
        reports[policy] = {
            "attempt_count": len(rows),
            "independent_families": len(samples),
            "assisted_attempts": sum(a.get("assistance") != "independent" for a in rows),
            "phases": phases,
            "mean_response_ms": mean(durations) if durations else None,
            "enough_samples": len(samples) >= min_samples,
        }
    return {
        "schema": "eris.teaching.evaluation.v1",
        "design": "observational",
        "causal_improvement_established": False,
        "minimum_samples_per_metric": min_samples,
        "policies": reports,
        "limitations": [
            "Item familiarity, order effects and domain difficulty are not controlled.",
            "No cost or learning gain is inferred from missing data.",
        ],
    }
