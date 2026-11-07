# -*- coding: utf-8 -*-
"""Runner V57: chạy model thật, restart thật, chấm transcript thật."""
from __future__ import annotations

import logging
import time
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Callable, Sequence

from .contracts import RunValidity, Scenario, ScenarioEvaluation, SimulationReport, TurnEvaluation
from .judge import BehavioralJudge, build_explicit_judge_client
from .quality import (
    build_memory_evidence, deterministic_failures, repetition_context, runtime_invariant_failures,
)


if TYPE_CHECKING:
    from .live_runtime import LiveRuntimeHarness


HarnessFactory = Callable[..., "LiveRuntimeHarness"]


class LiveSimulationRunner:
    def __init__(
        self,
        *,
        run_root: Path,
        logger: logging.Logger | None = None,
        harness_factory: HarnessFactory | None = None,
        judge_factory: Callable[[object], BehavioralJudge] = BehavioralJudge,
    ) -> None:
        self.run_root = Path(run_root).resolve()
        self.logger = logger or logging.getLogger("eris.v57.runner")
        self.harness_factory = harness_factory
        self.judge_factory = judge_factory

    def run(self, scenarios: Sequence[Scenario]) -> SimulationReport:
        started_at = time.time()
        run_id = f"v57_1-{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}"
        root = self.run_root / run_id
        root.mkdir(parents=True, exist_ok=True)
        harness = None
        judge = None
        scenario_reports: list[ScenarioEvaluation] = []
        validity = RunValidity.VALID_REALISM_RUN
        notes: list[str] = []
        provider = model = judge_provider = judge_model = ""

        try:
            harness_factory = self.harness_factory
            if harness_factory is None:
                try:
                    from .live_runtime import LiveRuntimeHarness
                except ImportError as exc:
                    raise RuntimeError(f"V57 live runs require the full Eris runtime: {exc}") from exc
                harness_factory = LiveRuntimeHarness
            harness = harness_factory(run_root=root, logger=self.logger)
            provider, model = harness.provider, harness.model
            explicit_judge = build_explicit_judge_client()
            judge_client = explicit_judge or harness.judge_candidate_client()
            if judge_client is None:
                raise RuntimeError("Không có provider thật cho behavioral judge")
            judge = self.judge_factory(judge_client)
            judge_provider, judge_model = judge.provider, judge.model
            if judge_provider == provider and judge_model == model:
                notes.append(
                    "Judge đang dùng cùng provider/model với Eris; run vẫn là real, nhưng độ độc lập đánh giá thấp hơn. "
                    "Có thể cấu hình ERIS_SIM_JUDGE_* để dùng model khác."
                )
            if not harness.provider_real or not judge.is_real:
                validity = RunValidity.INVALID_REALISM_RUN
                notes.append("Run bị vô hiệu vì main reply hoặc judge không chứng minh được provider thật.")

            for scenario in scenarios:
                turns: list[TurnEvaluation] = []
                transcript: list[dict[str, str]] = []
                prior_replies: list[str] = []
                prior_user_texts: list[str] = []
                failures: list[str] = []
                for index, step in enumerate(scenario.steps, start=1):
                    if step.restart_before:
                        harness.close()
                        # Dựng runtime mới trên CÙNG run root. Đây là restart persistence thật,
                        # không phải copy state Python từ object cũ sang object mới.
                        harness = harness_factory(run_root=root, logger=self.logger)
                        if not harness.provider_real:
                            validity = RunValidity.INVALID_REALISM_RUN
                    artifact = harness.send_user(
                        scenario_id=scenario.scenario_id, step_id=step.step_id,
                        turn_index=index, day=step.day, text=step.user_text,
                    )
                    repetition, repetition_prompt_similarity, _ = repetition_context(
                        user_text=step.user_text,
                        eris_text=artifact.eris_text,
                        prior_user_texts=prior_user_texts,
                        prior_replies=prior_replies,
                    )
                    memory_evidence = build_memory_evidence(
                        user_text=step.user_text,
                        eris_text=artifact.eris_text,
                        prior_user_texts=prior_user_texts,
                    )
                    local_failures = tuple(dict.fromkeys((
                        *deterministic_failures(
                            user_text=step.user_text, eris_text=artifact.eris_text,
                            prior_replies=prior_replies, prior_user_texts=prior_user_texts,
                            provider_real=artifact.provider_real,
                        ),
                        *runtime_invariant_failures(artifact),
                    )))
                    transcript.append({"role": "user", "content": step.user_text})
                    transcript.append({"role": "assistant", "content": artifact.eris_text})
                    try:
                        behavior = judge.evaluate(
                            step=step, transcript=transcript, eris_text=artifact.eris_text,
                            repetition_score=repetition, memory_evidence=memory_evidence,
                        )
                    except Exception as exc:
                        validity = RunValidity.RUNTIME_FAILED
                        from .contracts import BehaviorScore
                        behavior = BehaviorScore(
                            total=0.0, passed=False, hard_failures=(f"judge_error:{type(exc).__name__}",),
                            notes=(str(exc)[:400],), judge_provider=judge_provider,
                            judge_model=judge_model, judge_real=bool(getattr(judge, "is_real", False)),
                        )
                    evaluation = TurnEvaluation(
                        artifact=artifact, behavior=behavior,
                        deterministic_failures=local_failures,
                        transcript_repetition=repetition,
                        repetition_prompt_similarity=repetition_prompt_similarity,
                        memory_evidence=memory_evidence,
                    )
                    turns.append(evaluation)
                    prior_user_texts.append(step.user_text)
                    if artifact.eris_text:
                        prior_replies.append(artifact.eris_text)
                    else:
                        # Giữ alignment user/reply để repetition_context không ghép nhầm turn.
                        prior_replies.append("")
                    if not evaluation.passed:
                        failures.append(f"{step.step_id}:behavior_or_invariant_failed")
                    if behavior.total < scenario.min_behavior_score:
                        failures.append(f"{step.step_id}:score<{scenario.min_behavior_score:g}")

                scores = [item.behavior.total for item in turns]
                scenario_score = sum(scores) / len(scores) if scores else 0.0
                scenario_passed = (
                    bool(turns)
                    and not failures
                    and scenario_score >= scenario.min_scenario_score
                    and all(item.passed for item in turns)
                )
                scenario_reports.append(ScenarioEvaluation(
                    scenario_id=scenario.scenario_id, title=scenario.title, turns=turns,
                    score=scenario_score, passed=scenario_passed,
                    failures=tuple(dict.fromkeys(failures)),
                ))
        except RuntimeError as exc:
            message = str(exc)
            validity = RunValidity.PROVIDER_UNAVAILABLE if any(
                token in message.lower() for token in ("api key", "provider", "remote", "credential")
            ) else RunValidity.RUNTIME_FAILED
            notes.append(f"{type(exc).__name__}: {message}")
        except Exception as exc:
            validity = RunValidity.RUNTIME_FAILED
            notes.append(f"{type(exc).__name__}: {exc}")
        finally:
            if harness is not None:
                try:
                    harness.close()
                except Exception:
                    pass

        return SimulationReport(
            run_id=run_id, validity=validity, started_at=started_at, completed_at=time.time(),
            scenarios=scenario_reports, provider=provider, model=model,
            judge_provider=judge_provider, judge_model=judge_model, notes=tuple(notes),
        )


__all__ = ["LiveSimulationRunner"]
