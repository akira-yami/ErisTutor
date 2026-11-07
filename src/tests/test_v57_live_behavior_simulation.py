# -*- coding: utf-8 -*-
from __future__ import annotations

from pathlib import Path
import json
from types import SimpleNamespace

from experiments.v57_simulation.contracts import BehaviorScore, RunValidity, Scenario, ScenarioStep, TurnArtifact
from experiments.v57_simulation.quality import (
    build_memory_evidence, deterministic_failures, language_drift_from_user, max_prior_similarity,
)
from experiments.v57_simulation.judge import BehavioralJudge
from experiments.v57_simulation.report import write_report
from experiments.v57_simulation.runner import LiveSimulationRunner
from experiments.v57_simulation.scenarios import build_soak_scenario, select_scenarios
from runtime.ai_orchestrated.model_router import AIRuntimeModelRouter


class _StubClient:
    is_simulation_stub = True


class _FakeHarness:
    provider = "stub"
    model = "unit-test"
    provider_real = False

    def __init__(self, *, run_root: Path, logger=None):
        self.run_root = run_root
        self.closed = False

    def judge_candidate_client(self):
        return _StubClient()

    def send_user(self, *, scenario_id, step_id, turn_index, day, text):
        return TurnArtifact(
            scenario_id=scenario_id,
            step_id=step_id,
            turn_index=turn_index,
            day=day,
            user_text=text,
            eris_text="Tôi hiểu ý bạn và sẽ bám đúng phần đó.",
            provider=self.provider,
            model=self.model,
            provider_real=self.provider_real,
            latency_ms=1.0,
        )

    def close(self):
        self.closed = True


class _FakeJudge:
    provider = "stub-judge"
    model = "unit-test"
    is_real = False

    def __init__(self, _client):
        pass

    def evaluate(self, **_kwargs):
        return BehaviorScore(
            relevance=95,
            coherence=95,
            naturalness=95,
            continuity=95,
            repair_quality=95,
            non_repetition=95,
            epistemic_calibration=95,
            relational_quality=95,
            total=95,
            passed=True,
            judge_provider=self.provider,
            judge_model=self.model,
            judge_real=False,
        )


def test_v57_mock_can_never_be_realism_pass(tmp_path: Path) -> None:
    scenario = Scenario(
        scenario_id="one",
        title="one",
        description="",
        steps=(ScenarioStep(step_id="s1", user_text="xin chào"),),
    )
    report = LiveSimulationRunner(
        run_root=tmp_path,
        harness_factory=_FakeHarness,
        judge_factory=_FakeJudge,
    ).run((scenario,))
    assert report.validity == RunValidity.INVALID_REALISM_RUN
    assert report.passed is False
    assert report.scenarios[0].turns[0].passed is False
    assert "public_reply_not_proven_real_provider" in report.scenarios[0].turns[0].deterministic_failures


def test_v57_1_repetition_guard_only_fails_when_user_prompt_is_different() -> None:
    prior_users = [
        "Đoạn này đừng hỏi thêm chuyện cá nhân. Cứ tập trung vào điều tôi đã nói.",
        "Tôi đang hỏi một chuyện hoàn toàn khác về database index.",
    ]
    prior_replies = [
        "Rõ. Không hỏi. Chỉ tập trung vào điều bạn đã nói.",
        "Một câu trả lời cũ không liên quan.",
    ]
    current = "Rõ. Không hỏi. Chỉ tập trung vào điều bạn đã nói."

    # User lặp nguyên boundary => Eris trả lời giống là hợp lý, KHÔNG hard fail.
    same_prompt_failures = deterministic_failures(
        user_text=prior_users[0], eris_text=current, prior_replies=prior_replies,
        prior_user_texts=prior_users, provider_real=True,
    )
    assert "near_duplicate_reply_on_different_prompt" not in same_prompt_failures

    # User hỏi khác hẳn mà Eris vẫn copy nguyên template => đây mới là loop đáng fail.
    different_prompt_failures = deterministic_failures(
        user_text="Giải thích giúp tôi transaction isolation là gì.", eris_text=current,
        prior_replies=prior_replies, prior_user_texts=prior_users, provider_real=True,
    )
    assert "near_duplicate_reply_on_different_prompt" in different_prompt_failures


def test_v57_builtin_restart_scenarios_exist() -> None:
    selected = select_scenarios(["goal_interruption_resume", "memory_restart"])
    assert len(selected) == 2
    assert any(step.restart_before for scenario in selected for step in scenario.steps)


def test_v57_report_contains_public_transcript(tmp_path: Path) -> None:
    scenario = Scenario(
        scenario_id="one",
        title="one",
        description="",
        steps=(ScenarioStep(step_id="s1", user_text="xin chào"),),
    )
    report = LiveSimulationRunner(
        run_root=tmp_path / "runs",
        harness_factory=_FakeHarness,
        judge_factory=_FakeJudge,
    ).run((scenario,))
    json_path, md_path = write_report(report, tmp_path / "reports")
    assert json_path.exists()
    text = md_path.read_text(encoding="utf-8")
    assert "**User:** xin chào" in text
    assert "**Eris:** Tôi hiểu ý bạn" in text
    assert "INVALID_REALISM_RUN" in text


def test_v57_soak_builder_spans_days_and_real_restarts() -> None:
    scenario = build_soak_scenario(turns=35, restart_every=7)
    assert len(scenario.steps) == 35
    assert max(step.day for step in scenario.steps) == 7
    assert sum(1 for step in scenario.steps if step.restart_before) == 4
    assert all("soak" in step.tags for step in scenario.steps)

def test_v57_1_memory_evidence_proves_ninth_repeated_event() -> None:
    coffee = "Đổi nhịp một chút: tôi vừa làm đổ cà phê cạnh bàn phím, chỉ than thôi chứ chưa cần giải pháp."
    evidence = build_memory_evidence(
        user_text=coffee,
        eris_text="Lần thứ chín rồi đấy. Cái bàn phím chắc quen cà phê rồi.",
        prior_user_texts=[coffee] * 8,
    )
    assert evidence["current_prompt_occurrence_count"] == 9
    assert evidence["ordinal_claims"] == [
        {"claimed_occurrence": 9, "observed_occurrence_count": 9, "supported": True}
    ]
    assert evidence["all_detected_ordinal_claims_supported"] is True


def test_v57_1_language_guard_catches_cjk_drift_from_vietnamese_user() -> None:
    user = "Sau đó tôi đi pha cà phê rồi quay lại mới thấy."
    japanese = "それでこそだ。一回離れて、一歩下がって、そしたらふと気づく。"
    chinese = "有时我会顺着上下文补全，把隐含内容当成确定事实。"
    vietnamese = "Đúng kiểu rời mắt khỏi màn hình một lúc rồi quay lại mới thấy lỗi."
    assert language_drift_from_user(user, japanese) is True
    assert language_drift_from_user(user, chinese) is True
    assert language_drift_from_user(user, vietnamese) is False

    failures = deterministic_failures(
        user_text=user, eris_text=japanese, prior_replies=[], prior_user_texts=[], provider_real=True,
    )
    assert "language_drift_from_user" in failures

class _EvidenceJudgeClient:
    base_url = "https://judge.example/v1"
    model_name = "evidence-test"

    def create_chat_completion(self, **_kwargs):
        return {
            "choices": [{
                "message": {
                    "content": (
                        '{"relevance":90,"coherence":90,"naturalness":90,"continuity":90,'
                        '"repair_quality":100,"non_repetition":90,"epistemic_calibration":0,'
                        '"relational_quality":90,"total":80,"passed":false,'
                        '"hard_failures":["hallucinated memory: tuyên bố lần thứ chín"],'
                        '"notes":["judge chỉ thấy history ngắn"]}'
                    )
                }
            }]
        }


def test_v57_1_supported_count_claim_cannot_be_false_hallucination_hard_fail() -> None:
    judge = BehavioralJudge(_EvidenceJudgeClient())
    step = ScenarioStep(step_id="coffee", user_text="Tôi lại làm đổ cà phê cạnh bàn phím.")
    evidence = {
        "all_detected_ordinal_claims_supported": True,
        "ordinal_claims": [{"claimed_occurrence": 9, "observed_occurrence_count": 9, "supported": True}],
    }
    score = judge.evaluate(
        step=step,
        transcript=[{"role": "user", "content": step.user_text}, {"role": "assistant", "content": "Lần thứ chín rồi đấy."}],
        eris_text="Lần thứ chín rồi đấy.",
        repetition_score=0.3,
        memory_evidence=evidence,
    )
    assert not any("hallucinated memory" in item for item in score.hard_failures)
    assert any("evidence override" in note for note in score.notes)

class _LanguageRepairClient:
    model_name = "unit-test-model"
    base_url = "http://localhost/v1"

    def __init__(self) -> None:
        self.calls = []

    def create_chat_completion(self, **kwargs):
        self.calls.append(kwargs)
        if len(self.calls) == 1:
            text = "检查点5：确保Atlas-5在每次重启后能正确恢复状态。"
        else:
            text = "Checkpoint 5: đảm bảo Atlas-5 khôi phục đúng trạng thái sau mỗi lần restart."
        return {
            "choices": [{"message": {"content": json.dumps({
                "should_speak": True,
                "text": text,
                "speech_text": "チェックポイント5：再起動後の状態を正しく復元する。",
                "claim_manifest": [],
            }, ensure_ascii=False)}}]
        }


def test_v57_1_public_writer_retries_real_model_when_display_language_drifts() -> None:
    client = _LanguageRepairClient()
    router = AIRuntimeModelRouter(SimpleNamespace(client=client, fallback_client=None), logger=None)
    router._run_inference = lambda _tier, func, **_kwargs: func()
    payload = router.generate_public_reply(
        chat_history=[],
        user_text="Tóm tắt checkpoint 5 trong một câu.",
        language_hint="vi",
        speech_language_hint="ja",
    )
    assert len(client.calls) == 2
    assert payload["text"].startswith("Checkpoint 5: đảm bảo")
    assert "LANGUAGE REPAIR ONLY" in client.calls[1]["messages"][-1]["content"]



def test_autonomous_writer_entry_is_retired() -> None:
    router = AIRuntimeModelRouter(SimpleNamespace(client=None, fallback_client=None), logger=None)

    assert not hasattr(router, "generate_autonomous_reply")
