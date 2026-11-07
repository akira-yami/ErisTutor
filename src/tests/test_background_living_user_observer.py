from __future__ import annotations

from runtime.inference_broker import lane_policy


def test_curator_lane_is_not_shed_during_active_chat():
    policy = lane_policy("memory_curator")
    assert policy.optional is False
    assert policy.max_concurrency == 1
