# -*- coding: utf-8 -*-
"""V85 offline golden replay cho Mentor.

Không gọi provider/model. Mục đích là khóa policy/role behavior deterministically để
refactor sau này không phải đánh giá bằng cảm giác "hình như Eris tự nhiên hơn".
"""
from __future__ import annotations

import argparse
import json
import tempfile
from pathlib import Path
from typing import Any

from runtime.engineering_mentor import EngineeringMentorService


def _flow(findings: list[str], *, now: float, age_seconds: float = 1.0) -> dict[str, Any]:
    ended = now - max(0.0, float(age_seconds))
    return {
        "schema": "eris.coding.flow.v1",
        "task_id": "golden-task",
        "workspace": "C:/repo/eris",
        "status": "completed",
        "started_at": ended - 5.0,
        "ended_at": ended,
        "changed_files": ["src/runtime/example.py"],
        "tests_seen": 1,
        "failed_test_runs": 0,
        "reviewer_seen": True,
        "phase_summaries": {"explorer": "inspected", "reviewer": "reviewed"},
        "findings": [
            {"code": code, "severity": "warning", "message": code}
            for code in findings
        ],
    }


def evaluate_scenario(scenario: dict[str, Any], *, index: int = 0) -> dict[str, Any]:
    now = 10_000.0 + index * 100.0
    with tempfile.TemporaryDirectory(prefix="eris-v85-eval-") as root:
        base = Path(root)
        mentor = EngineeringMentorService(path=base / "skills.json")
        findings = [str(x) for x in (scenario.get("flow_findings") or [])]
        if findings or "flow_age_seconds" in scenario:
            mentor.observe_coding_flow(_flow(
                findings,
                now=now,
                age_seconds=float(scenario.get("flow_age_seconds", 1.0) or 1.0),
            ))
        for idx, text in enumerate(scenario.get("pre_observe") or []):
            mentor.observe_user_turn(str(text), turn_id=f"pre-{idx}", now=now - 20 + idx)
        prompt = mentor.prompt_context(
            str(scenario.get("user_text") or ""), turn_id="golden", now=now
        )
        snap = mentor.snapshot()
        decision = snap["last_decision"]
        role = snap["last_role_directive"]
        evidence_prefix, _, evidence_json = prompt.partition("\n")
        evidence = json.loads(evidence_json) if evidence_prefix == "ENGINEERING_EVIDENCE:" else {}
        actual = {
            "relevant": bool(decision.get("relevant", False)),
            "mode": str(decision.get("mode") or "none"),
            "primary_role": str(role.get("primary_role") or "none") if bool(role.get("active", False)) else "none",
            "prompt_present": bool(prompt),
            "delivery_owner": str(evidence.get("delivery_owner") or "none"),
        }
        expected = dict(scenario.get("expected") or {})
        passed = all(actual.get(key) == value for key, value in expected.items())
        return {
            "name": str(scenario.get("name") or f"scenario-{index}"),
            "passed": passed,
            "expected": expected,
            "actual": actual,
        }


def run_suite(path: Path) -> dict[str, Any]:
    scenarios = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(scenarios, list):
        raise ValueError("golden file must be a JSON array")
    results = [evaluate_scenario(dict(item), index=i) for i, item in enumerate(scenarios)]
    passed = sum(1 for item in results if item["passed"])
    return {
        "schema": "eris.mentor_golden_eval.v1",
        "total": len(results),
        "passed": passed,
        "failed": len(results) - passed,
        "results": results,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--golden",
        type=Path,
        default=Path(__file__).with_name("mentor_golden_v85.json"),
    )
    args = parser.parse_args()
    report = run_suite(args.golden)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
