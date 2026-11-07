# -*- coding: utf-8 -*-
"""Offline multi-turn acceptance harness cho Unified Agent/Tutor/Conversation loop.

Chạy:
    PYTHONPATH=. python tools/run_agent_conversation_eval.py

Harness cố ý không gọi provider. Nó kiểm tra invariant production quan trọng:
progressive disclosure, continuation qua turn, hard WAIT, report override và
speech hard guard. Model-quality eval có thể cắm thêm sau mà không đổi contract.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from runtime.agent_loop import TurnAgentRuntime, TurnDecision
from runtime.agent_loop.speech import enforce_public_speech_budget


class ScriptedReasoner:
    def __init__(self, decisions: Sequence[Mapping[str, Any]]) -> None:
        self.decisions = list(decisions)
        self.calls = 0
        self.contexts: list[Mapping[str, Any]] = []

    def decide(self, *, user_text, recent_history, state, runtime_context):
        self.contexts.append(dict(runtime_context))
        index = min(self.calls, len(self.decisions) - 1)
        self.calls += 1
        return TurnDecision.from_mapping(self.decisions[index])


def decision(*, beats, disclosure="conversational", action="complete", wait=False, continuation=None):
    return {
        "situation_summary": "Đang phát triển một cuộc phân tích cùng user.",
        "objective": "Phân tích INTP 538 theo mạch hội thoại.",
        "task_shape": "analysis",
        "active_roles": ["general", "tutor"],
        "work_items": ["core 5", "fix 3", "fix 8", "INTJ mistype"],
        "completed_items": ["analysis map"],
        "remaining_items": ["user response"] if wait else [],
        "uncertainties": [],
        "next_action": action,
        "progress": 1.0 if not wait else 0.6,
        "uncertainty": 0.1,
        "completion_candidate": not wait,
        "requires_user_response": wait,
        "tutor_response_relation": "none",
        "response_mode": "progressive",
        "disclosure_mode": disclosure,
        "speech_budget": {
            "target_words": 500,
            "hard_max_words": 900,
            "max_beats": 6,
            "target_seconds": 120.0,
            "hard_max_seconds": 160.0,
        },
        "continuation": continuation or {
            "active_thread": "INTP 538",
            "deferred_threads": ["fix 3", "fix 8", "INTJ mistype"],
            "continuation_anchor": "538 làm Ti-dom trông cứng hơn stereotype.",
            "next_if_continue": "INTJ mistype",
        },
        "dialogue_beats": [
            {
                "kind": "finding",
                "proposition": item,
                "communicative_goal": "mỗi lượt nói một ý đáng phản ứng",
                "relation_to_previous": "deepen",
                "detail_level": "normal",
                "needs_example": False,
                "say_now": True,
            }
            for item in beats
        ],
        "style_notes": ["không dump encyclopedia"],
        "decision_summary": "offline acceptance fixture",
    }


@dataclass
class Check:
    name: str
    passed: bool
    detail: str


def main() -> int:
    checks: list[Check] = []

    # 1) INTP 538: voice phải nói ít, giữ phần sau.
    r1 = ScriptedReasoner([decision(beats=["Tổng quan 538.", "Core 5.", "Fix 3 và 8."])])
    runtime = TurnAgentRuntime(r1)
    first = runtime.prepare_turn(
        turn_id="mbti-1", session_id="eval-mbti", user_text="INTP 538 là gì?",
        runtime_context={"voice_mode": True, "tts_speed": 1.0},
    )
    checks.append(Check(
        "voice_progressive_disclosure",
        len(first.state.discourse.beats) == 1 and len(first.state.discourse.continuation.deferred_threads) >= 2,
        f"visible={len(first.state.discourse.beats)} deferred={len(first.state.discourse.continuation.deferred_threads)}",
    ))

    # 2) Turn sau phải nhìn thấy arc cũ để user steering vào INTJ.
    r2 = ScriptedReasoner([decision(beats=["Giải thích đúng nhánh INTJ."], continuation={
        "active_thread": "INTJ mistype", "deferred_threads": [], "continuation_anchor": "", "next_if_continue": ""
    })])
    runtime.reasoner = r2
    runtime.prepare_turn(
        turn_id="mbti-2", session_id="eval-mbti", user_text="sao giống INTJ?",
        runtime_context={"voice_mode": True, "tts_speed": 1.0},
    )
    arc = r2.contexts[0].get("disclosure_arc", {})
    checks.append(Check(
        "multi_turn_arc_resume",
        "INTJ mistype" in tuple(arc.get("deferred_threads", ())),
        json.dumps(arc, ensure_ascii=False),
    ))

    # 3) WAIT_USER là hard stop.
    rw = ScriptedReasoner([decision(beats=["Theo bạn điểm khác nhau nằm ở đâu?"], action="wait_user", wait=True)])
    wait_runtime = TurnAgentRuntime(rw)
    wait_result = wait_runtime.prepare_turn(turn_id="wait", user_text="tôi chưa hiểu", runtime_context={"voice_mode": True})
    checks.append(Check("hard_wait", wait_result.should_wait_user and rw.calls == 1, f"status={wait_result.state.status} calls={rw.calls}"))

    # 4) User yêu cầu report text thì được phép nhiều beat.
    rr = ScriptedReasoner([decision(beats=["A", "B", "C", "D"], disclosure="report")])
    report = TurnAgentRuntime(rr).prepare_turn(turn_id="report", user_text="viết full profile", runtime_context={"voice_mode": False})
    checks.append(Check("report_override", len(report.state.discourse.beats) == 4, f"beats={len(report.state.discourse.beats)}"))

    # 5) Seatbelt cuối cùng trước TTS.
    budget = first.state.discourse.speech_budget
    long_text = " ".join(["Một câu rất dài mà writer không nên xổ hết cho voice."] * 100)
    guarded = enforce_public_speech_budget(long_text, budget, speed=1.0)
    checks.append(Check("pre_tts_hard_guard", guarded.clipped, f"seconds={guarded.estimate.seconds:.1f} words={guarded.estimate.words}"))

    report_payload = {
        "schema": "eris.agent_conversation_eval.v1",
        "passed": all(item.passed for item in checks),
        "checks": [item.__dict__ for item in checks],
    }
    print(json.dumps(report_payload, ensure_ascii=False, indent=2))
    return 0 if report_payload["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
