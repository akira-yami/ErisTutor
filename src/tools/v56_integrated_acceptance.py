# -*- coding: utf-8 -*-
"""CLI kiểm tra Decision Ledger V56 từ một JSON projection.

Ví dụ:
    python tools/v56_integrated_acceptance.py subject_state.json --strict

File đầu vào có thể là:
- một list record của ``decision_ledger``;
- hoặc một dict chứa key ``decision_ledger``.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Mapping

from cortex.subject.acceptance import IntegratedAcceptanceEvaluator


def _load_records(path: Path) -> list[Mapping[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, Mapping):
        payload = payload.get("decision_ledger", ())
    if not isinstance(payload, list):
        raise ValueError("JSON phải là list ledger record hoặc dict có key decision_ledger")
    return [item for item in payload if isinstance(item, Mapping)]


def main() -> int:
    parser = argparse.ArgumentParser(description="Kiểm tra invariant Decision Ledger V56")
    parser.add_argument("input", type=Path, help="JSON ledger/subject-state")
    parser.add_argument("--output", type=Path, default=None, help="ghi report JSON ra file")
    parser.add_argument("--strict", action="store_true", help="exit 2 nếu invariant fail")
    args = parser.parse_args()

    records = _load_records(args.input)
    report = IntegratedAcceptanceEvaluator().evaluate(records).to_dict()
    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    print(rendered)
    if args.output is not None:
        args.output.write_text(rendered + "\n", encoding="utf-8")
    if args.strict and not bool(report.get("passed")):
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
