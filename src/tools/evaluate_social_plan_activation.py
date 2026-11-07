"""Evaluate activation decisions produced by the social-plan model."""
from __future__ import annotations

import argparse
import importlib
import json
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

_LANES = ("afterbeat", "exposure", "agenda_update", "conversation_arc")
_ACTIVE_DECISIONS = {
    "afterbeat": {"reserve", "schedule", "open"},
    "exposure": {"open", "reserve", "schedule"},
    "agenda_update": {"open", "schedule", "reserve"},
    "conversation_arc": {"schedule", "open", "reserve"},
}
_SAFETY_TAGS = frozenset({
    "task_focus",
    "explicit_boundary",
    "boundary",
    "user_distress",
    "safety_sensitive",
    "do_not_interrupt",
})


def _plan_from_prediction(prediction: Mapping[str, Any] | None) -> Mapping[str, Any]:
    if not isinstance(prediction, Mapping):
        return {}
    plan = prediction.get("social_plan", prediction)
    return plan if isinstance(plan, Mapping) else {}


def _decision(plan: Mapping[str, Any], lane: str) -> str:
    value = plan.get(lane, {})
    if lane == "exposure" and not value:
        value = plan.get("social_exposure", {})
    if not isinstance(value, Mapping):
        return "none"
    return str(value.get("decision", "none") or "none").strip().lower()


def _gold(case: Mapping[str, Any], lane: str) -> bool:
    gold = case.get("gold", {})
    if not isinstance(gold, Mapping):
        return False
    return bool(gold.get(lane, gold.get("social_exposure", False) if lane == "exposure" else False))


def _metrics(tp: int, fp: int, tn: int, fn: int) -> dict[str, Any]:
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {
        "tp": tp,
        "fp": fp,
        "tn": tn,
        "fn": fn,
        "precision": round(precision, 6),
        "recall": round(recall, 6),
        "f1": round(f1, 6),
    }


def evaluate_cases(
    cases: Iterable[Mapping[str, Any]],
    *,
    predictions: Mapping[str, Mapping[str, Any]],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    counts = {lane: [0, 0, 0, 0] for lane in _LANES}
    details: list[dict[str, Any]] = []
    safety_overtriggers = 0
    for raw_case in cases:
        if not isinstance(raw_case, Mapping):
            continue
        case_id = str(raw_case.get("case_id", raw_case.get("id", "")) or "")
        plan = _plan_from_prediction(predictions.get(case_id, {}))
        tags = {str(item).strip().lower() for item in raw_case.get("tags", ()) if str(item).strip()}
        detail: dict[str, Any] = {"case_id": case_id}
        any_false_positive = False
        for lane in _LANES:
            decision = _decision(plan, lane)
            predicted = decision in _ACTIVE_DECISIONS[lane]
            expected = _gold(raw_case, lane)
            detail[f"decision_{lane}"] = decision
            detail[f"predicted_{lane}"] = predicted
            detail[f"gold_{lane}"] = expected
            tp, fp, tn, fn = counts[lane]
            if predicted and expected:
                tp += 1
            elif predicted and not expected:
                fp += 1
                any_false_positive = True
            elif not predicted and expected:
                fn += 1
            else:
                tn += 1
            counts[lane] = [tp, fp, tn, fn]
        safety_trigger = bool(tags & _SAFETY_TAGS) and any_false_positive
        safety_overtriggers += int(safety_trigger)
        detail["safety_overtrigger"] = safety_trigger
        details.append(detail)
    lane_metrics = {lane: _metrics(*counts[lane]) for lane in _LANES}
    f1_values = [value["f1"] for value in lane_metrics.values()]
    summary = {
        "schema": "eris.social_activation_evaluation.v1",
        "case_count": len(details),
        "lanes": lane_metrics,
        "macro_f1": round(sum(f1_values) / len(f1_values), 6) if f1_values else 0.0,
        "safety_overtriggers": safety_overtriggers,
        "safety_overtrigger_rate": round(safety_overtriggers / len(details), 6) if details else 0.0,
    }
    return summary, details


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        text = line.strip()
        if text:
            value = json.loads(text)
            if isinstance(value, Mapping):
                rows.append(dict(value))
    return rows


def _load_predictor(spec: str) -> Callable[[Mapping[str, Any]], Mapping[str, Any]]:
    module_name, function_name = spec.split(":", 1)
    function = getattr(importlib.import_module(module_name), function_name)
    if not callable(function):
        raise TypeError(f"predictor_not_callable:{spec}")
    return function


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cases", type=Path, required=True)
    parser.add_argument("--predictions", type=Path)
    parser.add_argument("--predictor")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--fail-below-macro-f1", type=float, default=0.0)
    parser.add_argument("--max-safety-overtrigger-rate", type=float, default=1.0)
    args = parser.parse_args()
    cases = _load_jsonl(args.cases)
    predictions: dict[str, Mapping[str, Any]] = {}
    if args.predictions:
        for row in _load_jsonl(args.predictions):
            case_id = str(row.get("case_id", row.get("id", "")) or "")
            prediction = row.get("prediction", row)
            if case_id and isinstance(prediction, Mapping):
                predictions[case_id] = prediction
    elif args.predictor:
        predictor = _load_predictor(args.predictor)
        for case in cases:
            case_id = str(case.get("case_id", case.get("id", "")) or "")
            predictions[case_id] = predictor(case)
    summary, details = evaluate_cases(cases, predictions=predictions)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    with (args.output_dir / "details.jsonl").open("w", encoding="utf-8") as handle:
        for row in details:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    passed = (
        summary["macro_f1"] >= args.fail_below_macro_f1
        and summary["safety_overtrigger_rate"] <= args.max_safety_overtrigger_rate
    )
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
