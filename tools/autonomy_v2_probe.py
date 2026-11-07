# -*- coding: utf-8 -*-
"""Kiểm chứng V2 độc lập, không khởi chạy TTS, Vision hay làm thay đổi máy thật.

Fixture ở đây đại diện cho cấu trúc initiative_view; KHÔNG phải Eris tự
suy nghĩ bằng model thật. Hãy bật ERIS_AUTONOMY_V2_MODE=sandbox trong host
để lấy chính các ý định Subject runtime hiện hành.
"""
from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
import tempfile

from runtime.autonomy.agency_v2 import AutonomyLoopV2
from runtime.autonomy.goal_events import GoalEventLedger
from runtime.world import CapabilityAuthority, CapabilityPolicy, CommandPolicy, SandboxWorld


def run(root: Path) -> dict:
    root.mkdir(parents=True, exist_ok=True)
    ledger = GoalEventLedger(root / "goals.sqlite")
    authority = CapabilityAuthority(secret=b"eris-v2-offline-probe-not-production!", issuer="probe", audience="sandbox")
    world = SandboxWorld(policy=CapabilityPolicy(CommandPolicy(frozenset(), str(root)), token_verifier=authority.verify))
    def token(command):
        return authority.issue(subject_id="eris", capabilities=frozenset({"simulate"}),
            policy_version="v1", scope={"command_ids": (command.command_id,),
                "action_ids": (command.action_id,), "decision_ids": (command.decision_id,),
                "idempotency_keys": (command.idempotency_key,)}, ttl_seconds=30)
    loop = AutonomyLoopV2(ledger, world, token)
    view = {"revision": 1, "pending_intentions": [{
        "intention_id": "probe:subject:explore", "semantic_goal": "Khám phá khả năng công cụ",
        "motive_kind": "explore", "pressure": 0.9, "commitment_strength": 0.9,
        "evidence_refs": ["probe:local"], "status": "pending",
    }]}
    result = asyncio.run(loop.cycle(view, preempted=lambda: False))
    result["goals_panel"] = ledger.panel_projection()
    result["disclaimer"] = "Fixture Subject giả lập; tool và receipt từ SandboxWorld thật"
    return result


def main():
    parser = argparse.ArgumentParser(description="Thử vòng goal → sandbox → outcome của Eris V2")
    parser.add_argument("--output", type=Path, default=None, help="Thư mục ghi SQLite; bỏ trống sẽ dùng thư mục tạm")
    args = parser.parse_args()
    if args.output:
        result = run(args.output)
    else:
        with tempfile.TemporaryDirectory(prefix="eris-v2-proof-") as name:
            result = run(Path(name))
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
