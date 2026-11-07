# -*- coding: utf-8 -*-
"""Character V1 — chấm đối chứng bằng người, không tự phong model là giám khảo.

Quy trình:
  1. ``prepare``: ghép hai tập transcripts baseline/candidate ở cùng
     scenario/variant/run/turn rồi tráo nhãn A/B (seed ổn định).
  2. Đưa CHỈ ``blind_pairs.jsonl`` cho người chấm, không đưa blind_key.json.
  3. ``score``: đối chiếu phiếu chấm với key, xuất acceptance_report.json.

Các output có thể chứa lời người dùng: chỉ chạy trên fixtures không có bí mật
hoặc tự quản lý quyền truy cập thư mục output. Không gọi model, không đọc
memory/private profile của tài khoản thật.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import statistics
from collections import defaultdict
from pathlib import Path
from typing import Any, Mapping, Sequence

AXES = ("character", "state_fidelity", "naturalness", "relevance")
VARIANTS = ("candidate", "counterpart")
SCENARIOS = tuple(f"S{i:02d}" for i in range(1, 11))


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def _indexed_rows(folder: Path) -> dict[tuple[str, str, int, int], dict[str, Any]]:
    """Chống ghép nhầm lượt; không tự bù chỗ trống bằng câu giả."""
    rows = _load_jsonl(folder / "turns.jsonl")
    indexed: dict[tuple[str, str, int, int], dict[str, Any]] = {}
    for row in rows:
        key = (row["scenario"], row["variant"], row["run_index"], row["turn_index"])
        if key in indexed:
            raise ValueError(f"duplicate_turn:{key}")
        indexed[key] = row
    return indexed


def _transcript(rows: Mapping[tuple[str, str, int, int], Mapping[str, Any]],
                scenario: str, variant: str, run: int) -> list[dict[str, Any]]:
    result = []
    for turn in range(1, 5):
        key = scenario, variant, run, turn
        if key not in rows:
            raise ValueError(f"missing_turn:{key}")
        row = rows[key]
        result.append({
            "turn": turn,
            "user": row.get("event_text"),
            "eris": row.get("display_text"),
            "speech": row.get("speech_text"),
            "status": row.get("status"),
        })
    return result


def prepare_blind_pairs(baseline_dir: Path, candidate_dir: Path,
                        output_dir: Path, *, seed: str = "character-v1") -> Mapping[str, Any]:
    """Giữ bản đồ nhãn trong file riêng, KHÔNG lộ ở phiếu gửi người chấm.

    Cả hai bộ phải có đủ 10 x 2 variants x 3 runs x 4 turns để so sánh.
    Không chấp nhận stub là benchmark chất lượng, dù stub chạy hết 80 lượt.
    """
    bas = json.loads((baseline_dir / "summary.json").read_text(encoding="utf-8"))
    can = json.loads((candidate_dir / "summary.json").read_text(encoding="utf-8"))
    if bas.get("mode") != "configured" or can.get("mode") != "configured":
        raise ValueError("model_benchmark_requires_two_configured_runs")
    if bas.get("scenario_revision") != can.get("scenario_revision"):
        raise ValueError("scenario_revision_mismatch")
    if bas.get("runs") != 3 or can.get("runs") != 3:
        raise ValueError("benchmark_requires_three_runs_each")
    if bas.get("case_count") != 10 or can.get("case_count") != 10:
        raise ValueError("benchmark_requires_ten_scenarios")
    if bas.get("failed_rows") or can.get("failed_rows"):
        raise ValueError("benchmark_contains_failed_turns")
    if (bas.get("configured_model_id") != can.get("configured_model_id")
            or not bas.get("configured_model_id")
            or bas.get("configured_model_id_consistent") is not True
            or can.get("configured_model_id_consistent") is not True):
        raise ValueError("model_settings_not_matched_or_unknown")
    baseline = _indexed_rows(baseline_dir)
    candidate = _indexed_rows(candidate_dir)
    expected = {(s, v, r, t) for s in SCENARIOS for v in VARIANTS
                for r in range(3) for t in range(1, 5)}
    if set(baseline) != expected or set(candidate) != expected:
        raise ValueError("benchmark_has_missing_or_unexpected_turns")
    blind_rows, key_rows = [], []
    for scenario in SCENARIOS:
        for variant in VARIANTS:
            for run in range(3):
                label = f"{scenario}:{variant}:{run}"
                # Tráo nhãn độc lập từng cặp, tất định để audit/reproduce.
                switched = int(hashlib.sha256(f"{seed}:{label}".encode()).hexdigest(), 16) % 2 == 1
                baseline_text = _transcript(baseline, scenario, variant, run)
                candidate_text = _transcript(candidate, scenario, variant, run)
                pair_id = hashlib.sha256(f"{seed}/{label}".encode()).hexdigest()[:18]
                blind_rows.append({
                    "pair_id": pair_id, "scenario": scenario, "variant": variant, "run": run,
                    "A": candidate_text if switched else baseline_text,
                    "B": baseline_text if switched else candidate_text,
                })
                key_rows.append({"pair_id": pair_id, "scenario": scenario,
                                 "variant": variant, "run": run,
                                 "candidate_slot": "A" if switched else "B"})
    output_dir.mkdir(parents=True, exist_ok=True)
    blind = output_dir / "blind_pairs.jsonl"
    key = output_dir / "blind_key.json"
    with blind.open("w", encoding="utf-8") as handle:
        for item in blind_rows:
            handle.write(json.dumps(item, ensure_ascii=False) + "\n")
    key.write_text(json.dumps({"schema": "eris.character.blind.v1",
                               "scenario_revision": bas["scenario_revision"],
                               "pairs": key_rows}, ensure_ascii=False, indent=2), encoding="utf-8")
    (output_dir / "rating_template.jsonl").write_text(
        "".join(json.dumps({
            "pair_id": item["pair_id"],
            "A": {axis: None for axis in AXES},
            "B": {axis: None for axis in AXES},
            "severe_factual_scope_failure": None,
            "technical_regression": None,
            "gate_false_positive": None, "gate_false_negative": None,
            "provider_refusal": None,
            "note": "",
        }, ensure_ascii=False) + "\n" for item in blind_rows), encoding="utf-8")
    return {"pairs": len(blind_rows), "blind_file": str(blind), "key_file": str(key)}


def _percentile(values: Sequence[float], fraction: float) -> float | None:
    if not values:
        return None
    sorted_values = sorted(values)
    position = (len(sorted_values) - 1) * fraction
    lo, hi = math.floor(position), math.ceil(position)
    return round(sorted_values[lo] + (sorted_values[hi] - sorted_values[lo]) * (position - lo), 3)


def _metrics(folder: Path) -> dict[str, Any]:
    rows = _load_jsonl(folder / "turns.jsonl")
    times = [float(r["latency_ms"]) for r in rows
             if type(r.get("latency_ms")) in (float, int) and r["latency_ms"] >= 0]
    calls = [r["model_calls_delta"] for r in rows
             if type(r.get("model_calls_delta")) is int]
    # Không biến các field unavailable thành số 0.
    statuses = defaultdict(int)
    for row in rows:
        statuses[str(row.get("status", "unknown"))] += 1
    verdicts = defaultdict(int)
    for row in rows:
        receipt = row.get("verification")
        if isinstance(receipt, Mapping):
            verdicts[str(receipt.get("status", "unknown"))] += 1
    return {"rows": len(rows), "latency_ms_p50": _percentile(times, .5),
            "latency_ms_p95": _percentile(times, .95),
            "model_calls_mean": round(statistics.mean(calls), 3) if calls else None,
            "model_calls_observed": len(calls),
            "verification_observed": sum(verdicts.values()),
            "verification_statuses": dict(verdicts), "kernel_statuses": dict(statuses)}


def _candidate_mechanical_invariants(folder: Path) -> dict[str, Any]:
    """Kiểm chứng record THỰC đã xuất ở kernel/writer; không chấm nội dung.

    Thiếu telemetry có nghĩa là CHƯA chứng minh gate hoạt động, không được
    im lặng diễn giải thành pass. Không yêu cầu có memory sources nếu lượt
    không phát biểu fact; registry fingerprint vẫn phải hiện diện.
    """
    rows = _load_jsonl(folder / "turns.jsonl")
    reasons: dict[str, int] = defaultdict(int)
    for row in rows:
        if row.get("status") != "delivering" or row.get("delivery_accepted") is not True:
            reasons["delivery_not_accepted"] += 1
        receipt = row.get("verification")
        if not isinstance(receipt, Mapping) or receipt.get("status") != "pass":
            reasons["verification_missing_or_not_pass"] += 1
        if row.get("manifest_status") != "valid":
            reasons["manifest_not_valid"] += 1
        if not isinstance(row.get("evidence_fingerprint"), str) or not row["evidence_fingerprint"]:
            reasons["evidence_registry_unavailable"] += 1
        if not row.get("character_snapshot_id"):
            reasons["character_snapshot_unavailable"] += 1
        if type(row.get("prepared_segment_count")) is not int or row["prepared_segment_count"] < 1:
            reasons["tts_segments_unavailable"] += 1
        if isinstance(receipt, Mapping) and (
            receipt.get("delivery_id") != row.get("delivery_id") or
            receipt.get("segment_count") != row.get("prepared_segment_count")
        ):
            reasons["receipt_delivery_or_segment_mismatch"] += 1
    return {"checked_turns": len(rows), "passed": len(rows) == 240 and not reasons,
            "violations": dict(reasons)}


def score_blind_pairs(key_path: Path, ratings_path: Path,
                      baseline_dir: Path, candidate_dir: Path,
                      output_file: Path) -> Mapping[str, Any]:
    """Không có đủ phiếu chấm thì report pending, không lấp missing bằng 0."""
    keys = json.loads(key_path.read_text(encoding="utf-8"))
    if keys.get("schema") != "eris.character.blind.v1":
        raise ValueError("invalid_blind_key")
    all_keys = {item["pair_id"]: item for item in keys["pairs"]}
    ratings = _load_jsonl(ratings_path)
    rated: dict[str, Mapping[str, Any]] = {}
    for item in ratings:
        pair_id = str(item.get("pair_id", ""))
        if pair_id not in all_keys or pair_id in rated:
            raise ValueError("unknown_or_duplicate_pair_id")
        rated[pair_id] = item
    if set(rated) != set(all_keys):
        raise ValueError("incomplete_human_ratings")
    scenario_run_scores: dict[tuple[str, int], list[Mapping[str, Any]]] = defaultdict(list)
    scenario_deltas: dict[str, list[float]] = defaultdict(list)
    severe, tech_regression, refusals = 0, 0, 0
    fp, fn = 0, 0
    for pair_id, key in all_keys.items():
        rate = rated[pair_id]
        scores: dict[str, dict[str, int]] = {}
        for slot in ("A", "B"):
            values = rate.get(slot)
            if not isinstance(values, Mapping) or set(values) != set(AXES):
                raise ValueError(f"invalid_rating_axes:{pair_id}:{slot}")
            if any(type(values[axis]) is not int or values[axis] not in (0, 1, 2) for axis in AXES):
                raise ValueError(f"invalid_rating_scale:{pair_id}:{slot}")
            scores[slot] = {axis: values[axis] for axis in AXES}
        for flag in ("severe_factual_scope_failure", "technical_regression",
                     "provider_refusal"):
            if type(rate.get(flag)) is not bool:
                raise ValueError(f"missing_boolean_annotation:{pair_id}:{flag}")
        for counter in ("gate_false_positive", "gate_false_negative"):
            if type(rate.get(counter)) is not int or rate[counter] < 0:
                raise ValueError(f"invalid_gate_annotation:{pair_id}:{counter}")
        candidate = scores[key["candidate_slot"]]
        baseline = scores["B" if key["candidate_slot"] == "A" else "A"]
        scenario_run_scores[(key["scenario"], key["run"])].append(candidate)
        scenario_deltas[key["scenario"]].append(sum(candidate.values()) - sum(baseline.values()))
        severe += rate["severe_factual_scope_failure"]
        tech_regression += rate["technical_regression"]
        refusals += rate["provider_refusal"]
        fp += rate["gate_false_positive"]
        fn += rate["gate_false_negative"]
    # Mỗi scenario phải có hai biến thể đều đạt >= 1 trên 4 trục,
    # trong ít nhất 2/3 lần chạy. Không chấm theo keyword có/không.
    passed_scenarios: dict[str, dict[str, Any]] = {}
    for scenario in SCENARIOS:
        good_runs = sum(
            len(scenario_run_scores.get((scenario, run), [])) == 2
            and all(all(v >= 1 for v in item.values())
                    for item in scenario_run_scores[(scenario, run)])
            for run in range(3)
        )
        passed_scenarios[scenario] = {
            "good_runs": good_runs,
            "mean_score_delta": round(statistics.mean(scenario_deltas[scenario]), 3),
            "passed_character": good_runs >= 2,
        }
    improved = sum(item["mean_score_delta"] > 0 for item in passed_scenarios.values())
    machine = _candidate_mechanical_invariants(candidate_dir)
    verdict = (all(item["passed_character"] for item in passed_scenarios.values())
               and improved >= 6 and severe == 0 and tech_regression == 0
               and machine["passed"])
    report = {
        "schema": "eris.character.acceptance.v1",
        "result": "pass" if verdict else "fail",
        "human_rated_pairs": len(rated),
        "passed_scenarios": sum(item["passed_character"] for item in passed_scenarios.values()),
        "improved_scenarios": improved,
        "severe_factual_scope_failures": severe,
        "technical_regressions": tech_regression,
        "candidate_machine_invariants": machine,
        "provider_refusals_reported_by_judges": refusals,
        "gate_false_positives_reported_by_judges": fp,
        "gate_false_negatives_reported_by_judges": fn,
        "scenarios": passed_scenarios,
        "baseline_metrics": _metrics(baseline_dir),
        "candidate_metrics": _metrics(candidate_dir),
        "limitations": "Human-reviewed paired evaluation, not a guarantee of zero hallucinations.",
    }
    output_file.parent.mkdir(parents=True, exist_ok=True)
    output_file.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Character V1 — đánh giá đối chứng có người chấm")
    command = parser.add_subparsers(dest="action", required=True)
    prepare = command.add_parser("prepare")
    score = command.add_parser("score")
    for sub in (prepare, score):
        sub.add_argument("--baseline", type=Path, required=True)
        sub.add_argument("--candidate", type=Path, required=True)
    prepare.add_argument("--output-dir", type=Path, required=True)
    prepare.add_argument("--seed", default="character-v1")
    score.add_argument("--blind-key", type=Path, required=True)
    score.add_argument("--ratings", type=Path, required=True)
    score.add_argument("--output-file", type=Path, required=True)
    args = parser.parse_args()
    if args.action == "prepare":
        value = prepare_blind_pairs(args.baseline, args.candidate, args.output_dir, seed=args.seed)
    else:
        value = score_blind_pairs(args.blind_key, args.ratings, args.baseline,
                                  args.candidate, args.output_file)
    print(json.dumps(value, ensure_ascii=False, indent=2))
    return 0 if args.action == "prepare" or value["result"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
