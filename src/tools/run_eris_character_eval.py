# -*- coding: utf-8 -*-
"""Character V1 v0.1 — benchmark harness qua ConversationKernel V12 THẬT.

Không dùng model tự chấm. --mode stub chỉ kiểm đường đi/telemetry, KHÔNG
được diễn giải là Eris có cá tính tốt. --mode configured phải dùng host_factory
đã inject clients/settings của chính repo (không đổi provider hoặc fallback).

Ví dụ:
    python -m tools.run_eris_character_eval --mode stub \
      --scenario-file src/tests/fixtures/eris_character_v1/scenarios.json \
      --runs 1 --label baseline-stub --output-dir scratch/eris-character-v1/baseline-stub
"""
from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import platform
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence
from types import SimpleNamespace
from collections import Counter

from runtime.conversation import ConversationEvent, ConversationKernel, DeterministicRootAgent, SurfaceEnvelope
from runtime.conversation.writer import RenderedConversationMove


class _StubWriter:
    """Surface giả chỉ dùng để kiểm kernel; không mô phỏng khả năng model."""
    def realize(self, *, turn: Any, **kwargs: Any) -> RenderedConversationMove:
        return RenderedConversationMove(
            surface=SurfaceEnvelope(display_text=turn.move.say),
            decision_payload=turn.move.to_dict(), metadata={"stub": True},
        )


class _StubDelivery:
    def __init__(self) -> None:
        self.transcripts: list[str] = []
        self.kernel: ConversationKernel | None = None

    def deliver(self, *, rendered: RenderedConversationMove, turn: Any,
                delivery_id: str, **kwargs: Any) -> None:
        self.transcripts.append(rendered.surface.display_text)
        # Stub xác nhận delivery đồng bộ, qua receipt ledger THẬT của kernel.
        # Không giả append assistant history nếu delivery chưa được chấp nhận.
        if self.kernel is not None:
            self.kernel.settle_delivery_id(session_id=turn.session_id,
                                           delivery_id=delivery_id, successful=True)


class _StubHost:
    def __init__(self, working_dir: Path, *, scenario: Mapping[str, Any]) -> None:
        from runtime.session_turn_ledger import SessionTurnLedger
        from memory.memory_cognition import MemoryCognitionCore
        from memory.memory_kernel import MemoryKernel
        self.storage_dir = working_dir
        # Mỗi host có canonical stores của chính nó. Không chạm dữ liệu user thật.
        self.memory_core = MemoryCognitionCore(str(working_dir / "memory"))
        self.memory_kernel = MemoryKernel.from_existing_core(self.memory_core)
        self.ledger = SessionTurnLedger()
        # State fixture chỉ cho telemetry; stub agent không giả làm model persona.
        fixture = str(scenario.get("state", {}).get("description", "") or "")
        state = SimpleNamespace(version=1, last_applied_turn_seq=0, fixture_description=fixture)
        self.ai_state_reducer = SimpleNamespace(snapshot=lambda: state)
        self.delivery = _StubDelivery()
        self.kernel = ConversationKernel(DeterministicRootAgent(),
                                         writer=_StubWriter(), delivery=self.delivery)
        self.delivery.kernel = self.kernel
        self.model_info = {"provider": "deterministic_stub", "mode": "stub"}


def _stub_host_factory(scenario: Mapping[str, Any]) -> _StubHost:
    return _StubHost(Path(str(scenario["__eval_temp_dir"])), scenario=scenario)


def _safe_state(host: Any) -> Mapping[str, Any] | None:
    """Chỉ lấy revision/count, không xuất profile/memory riêng tư ra JSONL."""
    reducer = getattr(host, "ai_state_reducer", None)
    snapshot = getattr(reducer, "snapshot", None)
    if not callable(snapshot):
        return None  # Không có telemetry != 0.
    try:
        state = snapshot()
        return {"revision": getattr(state, "version", None),
                "last_applied_turn_seq": getattr(state, "last_applied_turn_seq", None)}
    except Exception:
        return None


def _source_fingerprints() -> Mapping[str, Any]:
    root = Path(__file__).resolve().parents[2]
    names = ("src/runtime/conversation/kernel.py", "src/runtime/conversation/agent.py",
             "src/runtime/conversation/runtime_writer.py", "src/memory/memory_kernel.py")
    hashes = {name: hashlib.sha256((root / name).read_bytes()).hexdigest()
              for name in names if (root / name).is_file()}
    try:
        rev = subprocess.run(["git", "rev-parse", "HEAD"], cwd=root, capture_output=True,
                             text=True, timeout=3, check=True).stdout.strip()
        dirty = subprocess.run(["git", "diff", "--name-only"], cwd=root,
                               capture_output=True, text=True, timeout=3, check=True).stdout.splitlines()
        untracked = subprocess.run(["git", "ls-files", "--others", "--exclude-standard"],
                                   cwd=root, capture_output=True, text=True, timeout=3, check=True).stdout.splitlines()
        dirty_hashes = {name: hashlib.sha256((root / name).read_bytes()).hexdigest()
                        for name in sorted(set(dirty + untracked))
                        if (root / name).is_file() and not name.startswith("scratch/")}
    except (OSError, subprocess.SubprocessError):
        rev = None
        dirty_hashes = None
    return {"git_commit": rev, "dirty_hashes": dirty_hashes, "source_hashes": hashes}


def _validate_cases(scenarios: Sequence[Mapping[str, Any]]) -> None:
    keys = [str(row.get("id", "")) for row in scenarios]
    if sorted(keys) != [f"S{index:02d}" for index in range(1, 11)]:
        raise ValueError("scenario_ids_must_be_S01_to_S10")
    for scenario in scenarios:
        if not isinstance(scenario.get("state"), Mapping) or not isinstance(scenario.get("evidence"), Mapping):
            raise ValueError(f"scenario_missing_state_or_evidence:{scenario['id']}")
        for variant in (scenario, scenario.get("counterpart", {})):
            if not isinstance(variant, Mapping) or len(variant.get("turns", ())) != 4:
                raise ValueError(f"scenario_requires_four_turns:{scenario['id']}")
            if not all(isinstance(t, Mapping) and isinstance(t.get("text"), str) for t in variant["turns"]):
                raise ValueError(f"invalid_user_turn:{scenario['id']}")
        if scenario["counterpart"].get("changed_dimension") is None:
            raise ValueError(f"counterpart_requires_changed_dimension:{scenario['id']}")


def _host_kernel(host: Any) -> ConversationKernel:
    kernel = getattr(host, "conversation_kernel", None) or getattr(host, "kernel", None)
    if not isinstance(kernel, ConversationKernel):
        raise TypeError("host_factory_must_provide_real_ConversationKernel")
    return kernel


def _telemetry(host: Any, result: Any) -> dict[str, Any]:
    rendered = getattr(result, "rendered", None)
    surface = getattr(rendered, "surface", None)
    metadata = getattr(rendered, "metadata", None)
    if not isinstance(metadata, Mapping):
        metadata = {}
    registry = metadata.get("evidence_registry")
    prepared = metadata.get("prepared_public_output")
    receipt = metadata.get("claim_verification_receipt")
    manifest = metadata.get("claim_manifest_envelope")
    portrait = metadata.get("character_portrait")
    # Nhận đúng object của V0.3, KHÔNG đọc/ghi private state hoặc suy đoán
    # manifest thiếu đồng nghĩa với manifest rỗng hợp lệ.
    verification = None
    if receipt is not None:
        verification = {
            "status": getattr(receipt, "status", None),
            "delivery_id": getattr(receipt, "delivery_id", None),
            "snapshot_id": getattr(receipt, "snapshot_id", None),
            "segment_count": len(getattr(receipt, "segment_hashes", ()) or ()),
            "display_hash": getattr(receipt, "display_hash", None),
            "speech_hash": getattr(receipt, "speech_hash", None),
            "memory_hash": getattr(receipt, "memory_hash", None),
        }
    return {
        "status": result.status,
        "event_id": result.event.event_id,
        "session_id": result.event.session_id,
        "generation": result.event.generation,
        "run_id": getattr(getattr(result, "turn", None), "run_id", None),
        "root_move": result.decision.to_dict() if callable(getattr(result.decision, "to_dict", None)) else None,
        "display_text": getattr(surface, "display_text", None),
        "speech_text": getattr(surface, "speech_text", None),
        "delivery_id": result.delivery_id or None,
        "evidence_fingerprint": getattr(registry, "fingerprint", None),
        "verification": verification,
        "manifest_status": getattr(manifest, "structural_status", None),
        "prepared_segment_count": len(prepared.segments) if prepared is not None else None,
        "character_snapshot_id": getattr(portrait, "snapshot_id", None),
        "character_facet_count": len(portrait.facets) if portrait is not None else None,
        "provenance_present": registry is not None and bool(getattr(registry, "entries", ())),
        "evidence_statuses": ({entry.status: sum(x.status == entry.status for x in registry.entries)
                               for entry in registry.entries} if registry is not None else None),
        "model_calls": getattr(host, "model_call_count", None),
        # Không dump toàn bộ settings/client: chúng có thể chứa API key.
        "provider": {key: getattr(host, "model_info", {}).get(key)
                     for key in ("provider", "model", "mode")
                     if isinstance(getattr(host, "model_info", None), Mapping)
                     and key in getattr(host, "model_info", {})},
    }


def run_scenarios(host_factory: Callable[[Mapping[str, Any]], Any],
                  scenarios: Sequence[Mapping[str, Any]], *, runs: int, label: str,
                  output_dir: Path, mode: str = "stub") -> Mapping[str, Any]:
    """Mỗi scenario/variant/run có thư mục tạm và host mới hoàn toàn.

    Không tự ghi assistant turn để chữa lỗi. Kernel thất bại: xuất failure row,
    DỪNG chuỗi 4 turn này và chuyển sang scenario tiếp theo.
    """
    _validate_cases(scenarios)
    if runs < 1:
        raise ValueError("runs_must_be_positive")
    if mode not in ("stub", "configured"):
        raise ValueError("invalid_eval_mode")
    folder = Path(output_dir)
    folder.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, Any]] = []
    model_identities: set[str] = set()
    for run_index in range(runs):
        for scenario in scenarios:
            for variant_label, variant in (("candidate", scenario), ("counterpart", scenario["counterpart"])):
                with tempfile.TemporaryDirectory(prefix="eris-character-v1-") as storage:
                    kwargs = {**scenario, **variant, "__eval_temp_dir": storage,
                              "__eval_run_index": run_index, "__eval_variant": variant_label}
                    try:
                        host = host_factory(kwargs)
                        kernel = _host_kernel(host)
                        if mode == "configured":
                            # Factory chịu trách nhiệm áp state/records fixture
                            # vào store riêng của scenario, không dùng tài khoản thật.
                            if getattr(host, "eval_fixture_applied", False) is not True:
                                raise ValueError("configured_fixture_not_applied")
                            if not callable(getattr(host, "build_eval_runtime_context", None)):
                                raise ValueError("configured_context_builder_missing")
                            # Mọi factory configured phải công bố model/provider và
                            # hash config không chứa secret, nếu không KHÔNG đủ dữ
                            # kiện để chứng minh baseline/candidate cùng settings.
                            info = getattr(host, "model_info", None)
                            if not isinstance(info, Mapping) or not all(
                                isinstance(info.get(key), str) and info[key].strip()
                                for key in ("provider", "model", "settings_fingerprint")
                            ):
                                raise ValueError("configured_model_identity_missing")
                            if info["provider"] == "deterministic_stub":
                                raise ValueError("configured_requires_real_model")
                            public = {key: info[key] for key in
                                      ("provider", "model", "settings_fingerprint")}
                            model_identities.add(hashlib.sha256(json.dumps(
                                public, sort_keys=True).encode()).hexdigest())
                    except Exception as exc:
                        rows.append({"scenario": scenario["id"], "variant": variant_label,
                                     "run_index": run_index, "turn_index": None,
                                     "status": "host_failed", "error": type(exc).__name__})
                        continue
                    session_id = f"eval:{scenario['id']}:{variant_label}:{run_index}"
                    recent_public_history: list[dict[str, str]] = []
                    for turn_index, user_turn in enumerate(variant["turns"], 1):
                        started = time.perf_counter()
                        calls_before = getattr(host, "model_call_count", None)
                        state_before = _safe_state(host)
                        row: dict[str, Any] = {
                            "scenario": scenario["id"], "variant": variant_label,
                            "run_index": run_index, "turn_index": turn_index,
                            "event_text": user_turn["text"],
                            "state_before": state_before, "scope_fixture": scenario["evidence"].get("scope"),
                            "recorded_at": time.time(), "model_quality_score": None,
                        }
                        try:
                            event = ConversationEvent.user_turn(session_id=session_id, text=user_turn["text"])
                            # Không gọi Root hay writer trực tiếp: đường thử đi qua kernel.
                            context = {}
                            if mode == "configured":
                                # Host adapter dựng packet bằng ContextAssembler
                                # THẬT ở thời điểm lượt này; Root và Writer đọc
                                # chung snapshot/registry. Không lấy đoạn fixture
                                # description rồi giả làm state đã học.
                                context = host.build_eval_runtime_context(
                                    event=event, user_turn=user_turn,
                                    recent_history=tuple(recent_public_history),
                                )
                                if not isinstance(context, Mapping) or not (
                                    context.get("__evidence_registry") is not None
                                    and context.get("__character_portrait") is not None
                                ):
                                    raise ValueError("configured_context_missing_typed_character_sources")
                            result = kernel.handle_event(
                                event, recent_history=tuple(recent_public_history),
                                runtime_context=context,
                            )
                            row.update(_telemetry(host, result))
                            # History lấy từ sự kiện thật; assistant chỉ xuất hiện
                            # nếu delivery đã settle THÀNH CÔNG trên kernel.
                            recent_public_history.append({"role": "user", "content": user_turn["text"]})
                            phase = kernel.snapshot(session_id).phase.value
                            accepted = result.status == "delivering" and phase in {
                                "waiting_user", "continuation_pending",
                            }
                            if accepted and result.rendered is not None:
                                recent_public_history.append({
                                    "role": "assistant", "content": result.rendered.surface.display_text,
                                })
                            row["delivery_accepted"] = accepted if result.status == "delivering" else None
                            if result.status not in {"delivering", "no_reply"}:
                                row["error"] = "kernel_non_success_status"
                        except Exception as exc:
                            row.update({"status": "kernel_exception", "error": type(exc).__name__,
                                        "display_text": None, "speech_text": None})
                        row["state_after"] = _safe_state(host)
                        calls_after = getattr(host, "model_call_count", None)
                        # Một host sống xuyên bốn lượt: counter thường tích lũy.
                        # Chỉ export DELTA khi cả hai snapshot là số nguyên.
                        row["model_calls_delta"] = (calls_after - calls_before
                            if type(calls_before) is int and type(calls_after) is int
                            and calls_after >= calls_before else None)
                        row["latency_ms"] = round((time.perf_counter() - started) * 1000, 3)
                        rows.append(row)
                        if row["status"] not in {"delivering", "no_reply"}:
                            break
    path = folder / "turns.jsonl"
    with path.open("w", encoding="utf-8") as out:
        for row in rows:
            out.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
    summary = {
        "schema": "eris.character_eval.v1", "label": str(label), "mode": mode,
        "configured_model_id": (next(iter(model_identities)) if mode == "configured" and len(model_identities) == 1 else None),
        "configured_model_id_consistent": mode == "configured" and len(model_identities) == 1,
        "verification_statuses": dict(Counter(
            (row.get("verification") or {}).get("status", "unavailable")
            for row in rows)),
        "scenario_revision": hashlib.sha256(json.dumps(list(scenarios), ensure_ascii=False,
                                                     sort_keys=True).encode()).hexdigest(),
        "runs": runs, "case_count": len(scenarios), "row_count": len(rows),
        "failed_rows": sum(row["status"] not in {"delivering", "no_reply"} for row in rows),
        "baseline_model": "pending" if mode == "stub" else "configured_unscored",
        "python": platform.python_version(),
        "created_at": time.time(), "source": _source_fingerprints(),
        "rows_file": path.name,
    }
    (folder / "summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description="Eris Character V1 baseline harness (V12 Kernel)")
    parser.add_argument("--mode", choices=("stub", "configured"), default="stub")
    parser.add_argument("--host-factory", help="configured: module:function tạo host với clients/settings hiện có")
    parser.add_argument("--scenario-file", type=Path, required=True)
    parser.add_argument("--runs", type=int, default=1)
    parser.add_argument("--label", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    if args.mode == "configured":
        if not args.host_factory or ":" not in args.host_factory:
            parser.error("configured requires --host-factory module:function; never replace with stub")
        module, name = args.host_factory.split(":", 1)
        factory = getattr(importlib.import_module(module), name)
    else:
        factory = _stub_host_factory
    data = json.loads(args.scenario_file.read_text(encoding="utf-8"))
    cases = data.get("scenarios", ())
    summary = run_scenarios(factory, cases, runs=args.runs, label=args.label,
                            output_dir=args.output_dir, mode=args.mode)
    print(json.dumps({"rows": summary["row_count"], "failed_rows": summary["failed_rows"],
                      "model_baseline": summary["baseline_model"]}, ensure_ascii=False))
    return 0 if summary["failed_rows"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
