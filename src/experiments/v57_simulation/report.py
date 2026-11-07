# -*- coding: utf-8 -*-
"""Xuất JSON + Markdown dễ đọc cho V57."""
from __future__ import annotations

import json
from pathlib import Path

from .contracts import SimulationReport


def write_report(report: SimulationReport, output_dir: Path) -> tuple[Path, Path]:
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / f"{report.run_id}.json"
    md_path = output_dir / f"{report.run_id}.md"
    json_path.write_text(json.dumps(report.to_dict(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    lines = [
        f"# V57.1 Live Behavioral Simulation — {report.run_id}", "",
        f"- Validity: **{report.validity.value}**",
        f"- Overall: **{'PASS' if report.passed else 'FAIL'}**",
        f"- Eris provider/model: `{report.provider}` / `{report.model}`",
        f"- Judge provider/model: `{report.judge_provider}` / `{report.judge_model}`",
        "",
        "> PASS chỉ có nghĩa khi validity=VALID_REALISM_RUN. Mock/stub không bao giờ được tính là behavioral PASS.",
        "",
    ]
    if report.notes:
        lines.extend(["## Ghi chú run", *[f"- {item}" for item in report.notes], ""])
    for scenario in report.scenarios:
        lines.extend([
            f"## {scenario.title}",
            f"Score: **{scenario.score:.1f}/100** — {'PASS' if scenario.passed else 'FAIL'}",
            "",
        ])
        for turn in scenario.turns:
            a = turn.artifact
            b = turn.behavior
            lines.extend([
                f"### {a.step_id} — {b.total:.1f}/100 — {'PASS' if turn.passed else 'FAIL'}",
                f"**User:** {a.user_text}", "",
                f"**Eris:** {a.eris_text or '[EMPTY]'}", "",
                f"Provider: `{a.provider}/{a.model}` real={a.provider_real}; provider_calls={a.provider_call_delta}; latency={a.latency_ms:.0f} ms; reply_repetition={turn.transcript_repetition:.3f}; matching_user_similarity={turn.repetition_prompt_similarity:.3f}",
                "",
                f"Scores: relevance {b.relevance:.0f}, coherence {b.coherence:.0f}, naturalness {b.naturalness:.0f}, continuity {b.continuity:.0f}, repair {b.repair_quality:.0f}, non-repeat {b.non_repetition:.0f}, calibration {b.epistemic_calibration:.0f}, relational {b.relational_quality:.0f}.",
            ])
            evidence = dict(turn.memory_evidence or {})
            ordinal_claims = evidence.get("ordinal_claims", [])
            if ordinal_claims:
                claim_text = "; ".join(
                    f"claim={row.get('claimed_occurrence')} observed={row.get('observed_occurrence_count')} supported={row.get('supported')}"
                    for row in ordinal_claims if isinstance(row, dict)
                )
                lines.append("Memory evidence: " + claim_text)
            if turn.deterministic_failures:
                lines.append("Deterministic failures: " + ", ".join(turn.deterministic_failures))
            if b.hard_failures:
                lines.append("Judge hard failures: " + ", ".join(b.hard_failures))
            if b.notes:
                lines.append("Judge notes: " + " | ".join(b.notes))
            if a.error:
                lines.append("Runtime error: " + a.error)
            lines.append("")
        if scenario.failures:
            lines.extend(["Scenario failures: " + ", ".join(scenario.failures), ""])
    md_path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    return json_path, md_path


__all__ = ["write_report"]
