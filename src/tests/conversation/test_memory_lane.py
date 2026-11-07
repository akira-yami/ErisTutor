# -*- coding: utf-8 -*-
"""MemoryLane: lane nền cho Living User Memory, không chẹn critical path."""
import time

from runtime.conversation.memory_lane import MemoryLane, ensure_memory_lane


def _wait_until(pred, timeout=3.0):
    end = time.time() + timeout
    while time.time() < end:
        if pred():
            return True
        time.sleep(0.01)
    return pred()


def test_first_call_seeds_synchronously_fast():
    lane = MemoryLane()
    calls = []

    def build(q):
        calls.append(q)
        return ("ctx cho " + q, ("rec1",), False)

    t0 = time.time()
    text, records, degraded = lane.get(build, "xin chao")
    assert time.time() - t0 < 2.0
    assert text == "ctx cho xin chao"
    assert records == ("rec1",)
    assert degraded is False


def test_second_call_uses_snapshot_then_refreshes_in_background():
    lane = MemoryLane()
    build_log = []

    def build(q):
        build_log.append(q)
        return ("ctx:" + q, (q,), False)

    # Lượt 1: seed, có snapshot.
    first = lane.get(build, "A")
    assert first[0] == "ctx:A"

    # Lượt 2: trả NGAY snapshot cũ (của A), rồi refresh nền sang B.
    t0 = time.time()
    second = lane.get(build, "B")
    assert time.time() - t0 < 0.5  # không chờ build B
    assert second[0] == "ctx:A"  # dùng snapshot gần nhất (A), trễ 1 lượt

    # Nền refresh xong -> snapshot thành B.
    assert _wait_until(lambda: (lane.current() or ("",))[0] == "ctx:B")


def test_seed_deadline_does_not_hang_on_slow_build():
    lane = MemoryLane()

    def slow_build(q):
        time.sleep(5.0)  # lâu hơn deadline 2s
        return ("cham:" + q, (), False)

    t0 = time.time()
    text, records, degraded = lane.get(slow_build, "X")
    elapsed = time.time() - t0
    assert elapsed < 3.0  # không treo 5s
    # Quá hạn -> degraded rỗng, KHÔNG raise.
    assert text == ""
    assert degraded is True


def test_background_refresh_failure_keeps_old_snapshot():
    lane = MemoryLane()

    def good(q):
        return ("good:" + q, (), False)

    def boom(q):
        raise RuntimeError("recall failed")

    lane.get(good, "A")  # seed snapshot
    # Lượt sau build lỗi ở nền; lượt hiện tại vẫn trả snapshot cũ.
    result = lane.get(boom, "B")
    assert result[0] == "good:A"
    # Snapshot KHÔNG bị hỏng bởi lỗi nền.
    time.sleep(0.2)
    assert (lane.current() or ("",))[0] == "good:A"


def test_records_and_degraded_preserved_through_snapshot():
    lane = MemoryLane()

    def build(q):
        return ("t", ("r1", "r2"), True)

    text, records, degraded = lane.get(build, "q")
    assert records == ("r1", "r2")
    assert degraded is True


def test_ensure_memory_lane_reused_on_host():
    class Host:
        pass

    h = Host()
    a = ensure_memory_lane(h)
    b = ensure_memory_lane(h)
    assert a is b  # cùng một lane -> snapshot bền xuyên lượt
