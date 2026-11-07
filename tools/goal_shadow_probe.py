"""Offline probe a JSON export of RuntimeMindState against isolated shadow SQLite.

Example: PYTHONPATH=src python tools/goal_shadow_probe.py --source state.json
         --db /tmp/eris-goal-shadow.sqlite --observation-id snapshot:1

Never points to a live state store; only writes the explicit --db file.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from runtime.autonomy.goal_registry import ShadowGoalRegistry


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, type=Path, help="JSON export: {version, goal_records}")
    parser.add_argument("--db", required=True, type=Path, help="Separate SHADOW SQLite path")
    parser.add_argument("--observation-id", required=True, help="Stable id for this state snapshot")
    args = parser.parse_args()
    source = args.source.resolve()
    dest = args.db.resolve()
    if source == dest:
        parser.error("source and --db must be different files")
    data = json.loads(source.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("goal_records"), list):
        parser.error("expected a JSON object with goal_records list")
    version = data.get("version")
    if not isinstance(version, int) or isinstance(version, bool):
        parser.error("expected integer RuntimeMindState.version")
    report = ShadowGoalRegistry(dest).observe_legacy(
        data["goal_records"], source_revision=version, observation_id=args.observation_id,
    )
    print(json.dumps({
        "status": report.status,
        "applied": report.applied,
        "source_revision": report.source_revision,
        "shadow_revision": report.shadow_revision,
        "total_known": report.total_known,
        "diffs": [
            {"code": diff.code, "goal_id": diff.goal_id, "detail": diff.detail}
            for diff in report.diffs
        ],
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
