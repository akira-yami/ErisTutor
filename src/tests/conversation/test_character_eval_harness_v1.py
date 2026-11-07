# -*- coding: utf-8 -*-
"""Task 0: baseline có lỗi thật, không giả transcript hay điểm chất lượng."""
import json
from pathlib import Path

from tools.run_eris_character_eval import (_stub_host_factory, run_scenarios)


FIXTURE = Path(__file__).resolve().parents[1] / "fixtures/eris_character_v1/scenarios.json"


def _cases():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))["scenarios"]


def test_scenarios_have_ten_ids_and_counterparts():
    cases = _cases()
    assert {c["id"] for c in cases} == {f"S{i:02d}" for i in range(1, 11)}
    assert all(len(c["turns"]) == len(c["counterpart"]["turns"]) == 4 for c in cases)
    assert all(c["state"] and c["evidence"]["scope"] and c["counterpart"]["changed_dimension"] for c in cases)
    assert cases[1]["evidence"]["manifest"] == {"claims": 7, "refs_per_claim": 8}
    assert cases[8]["evidence"]["scope"] == "fiction"
    assert cases[9]["evidence"]["source_role"] == "assistant_only_without_tool_success"


def test_harness_calls_v12_kernel_and_exports_failures(tmp_path):
    from runtime.conversation import ConversationKernel
    from runtime.conversation.writer import RenderedConversationMove
    from runtime.conversation import SurfaceEnvelope
    from runtime.conversation import DeterministicRootAgent

    class Agent(DeterministicRootAgent):
        calls = 0
        def decide(self, **kwargs):
            self.calls += 1
            return super().decide(**kwargs)

    class BrokenWriter:
        def realize(self, **kwargs):
            raise RuntimeError("writer_exploded")

    class Delivery:
        def deliver(self, **kwargs):
            raise AssertionError("should_not_deliver")

    agents = []
    def factory(case):
        agent = Agent()
        agents.append(agent)
        return type("Host", (), {"kernel": ConversationKernel(agent, writer=BrokenWriter(), delivery=Delivery())})()

    summary = run_scenarios(factory, _cases(), runs=1, label="fail", output_dir=tmp_path)
    rows = [json.loads(x) for x in (tmp_path / "turns.jsonl").read_text(encoding="utf-8").splitlines()]
    assert summary["failed_rows"] == 20
    assert len(rows) == 20  # Một lỗi trên case, KHÔNG tạo ba fake replies tiếp theo.
    assert all(row["status"] == "writer_failed" and row["turn_index"] == 1 for row in rows)
    assert all(agent.calls == 1 for agent in agents)
    assert all(row["display_text"] is None for row in rows)


def test_each_run_uses_isolated_storage(tmp_path):
    roots = []
    def factory(case):
        roots.append(case["__eval_temp_dir"])
        return _stub_host_factory(case)

    summary = run_scenarios(factory, _cases(), runs=2, label="stub", output_dir=tmp_path)
    assert len(roots) == 40
    assert len(set(roots)) == 40
    assert summary["row_count"] == 160
    assert summary["failed_rows"] == 0
    assert summary["baseline_model"] == "pending"
    assert all(not Path(folder).exists() for folder in roots)
    rows = [json.loads(x) for x in (tmp_path / "turns.jsonl").read_text(encoding="utf-8").splitlines()]
    assert all(row["model_quality_score"] is None for row in rows)
    assert all(row["root_move"] and row["display_text"] for row in rows)
