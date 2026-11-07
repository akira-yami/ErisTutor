# -*- coding: utf-8 -*-
"""Smoke test gọi DS2API/DeepSeek thật cho post-turn observer (OPT-IN).

Chạy từ thư mục src/ khi gateway của ERIS đã mở:
    set ERIS_POST_OBSERVER_LIVE=1
    set ERIS_POST_OBSERVER_LIVE_BASE_URL=http://127.0.0.1:6012/v1
    set ERIS_POST_OBSERVER_LIVE_MODEL=deepseek-v4-flash
    set ERIS_DEEPSEEK_RAW_TRACE=1
    python tools/probe_observer_live.py

Chỉ bật trace trên máy riêng: prompt, ký ức, response có thể là dữ liệu nhạy cảm.
Không in raw prompt/response hay API key ra terminal. Không tự động chạy trong CI.
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path
from types import SimpleNamespace

# Cho phép chạy trực tiếp từ src/tools hoặc từ root của repo.
SRC = Path(__file__).resolve().parents[1]
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from llm.client import OpenAICompatibleClient
from llm.deepseek_workers import JsonlWorkerClient
from runtime.ai_orchestrated.model_router import AIRuntimeModelRouter, ObserverInferenceError


PROBES = (
    ("banter", "Ơ, sao Eris lì thế?", "Tôi nghĩ cậu cứ muốn tôi cúi đầu."),
    ("conditional", "Nếu em nghe lời quá thì tui chán đó nha.", "Tôi không muốn làm một con rối."),
    ("technical", "Câu trước sai, 4+4 phải là 8, không phải 9.", "Đúng, tôi nhầm phép tính."),
)


def main() -> int:
    if os.getenv("ERIS_POST_OBSERVER_LIVE", "0") != "1":
        print("SKIP: Để tránh gọi model ngoài ý muốn, đặt ERIS_POST_OBSERVER_LIVE=1.")
        return 0
    endpoint = str(os.getenv("ERIS_POST_OBSERVER_LIVE_BASE_URL", "") or "").strip()
    if not endpoint:
        print("SKIP: Chưa có ERIS_POST_OBSERVER_LIVE_BASE_URL.")
        return 2
    client = OpenAICompatibleClient(
        base_url=endpoint,
        api_key=os.getenv("ERIS_POST_OBSERVER_LIVE_API_KEY", "local"),
        model_name=os.getenv("ERIS_POST_OBSERVER_LIVE_MODEL", "deepseek-v4-flash"),
        timeout=90,
    )
    wrapped = JsonlWorkerClient(client, role="observer", jsonl_enabled=True, repair_attempts=1)
    router = AIRuntimeModelRouter(SimpleNamespace(social_parser_client=wrapped))
    outcomes = []
    for index, (name, user, assistant) in enumerate(PROBES, 1):
        begin = time.perf_counter()
        try:
            observation = router.observe_completed_turn(
                turn_id=f"live-probe-{index}", turn_seq=index * 2,
                user_text=user, assistant_text=assistant, state_summary={}, timeout_seconds=90,
            )
            status = observation.observation_status
            confidence = round(observation.confidence, 3)
            evidence_count = len(observation.evidence)
        except ObserverInferenceError as error:
            status, confidence, evidence_count = error.status, 0, 0
        elapsed = round(time.perf_counter() - begin, 2)
        outcomes.append(status)
        print(f"{name}: status={status} confidence={confidence} evidence={evidence_count} latency={elapsed}s")
    print("Tóm tắt:", {key: outcomes.count(key) for key in sorted(set(outcomes))})
    # smoke test chứ không phải benchmark: chỉ đánh dấu lỗi pipeline rõ ràng.
    return 0 if all(value in {"success", "model_unknown"} for value in outcomes) else 1


if __name__ == "__main__":
    raise SystemExit(main())
