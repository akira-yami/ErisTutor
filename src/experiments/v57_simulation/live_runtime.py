# -*- coding: utf-8 -*-
"""Harness chạy chat pipeline THẬT của Eris cho V57.

Khác unit test, harness này dựng provider client thật và RuntimeCoordinator thật.
Nó chỉ thay hai biên vật lý:
- UDP Body bằng CaptureTransport để lấy chính public payload mà UI sẽ nhận;
- telemetry desktop bằng snapshot kiểm soát để benchmark tái lập.

Không mock LlmService, không gọi một prompt "giống Eris" bên ngoài runtime.
"""
from __future__ import annotations

import contextlib
import logging
import os
import time
import threading
from pathlib import Path
from typing import Any

from actions import ActionExecutor
from cortex.contracts import TelemetrySnapshot
from domain import Decision
from llm import LlmService
from llm.client import GeminiClient, OpenAICompatibleClient
from llm.process_manager import RemoteModelProcessManager
from memory.memory_cognition import MemoryCognitionCore
from parser_grounding import DecisionParser
from runtime.architecture_flags import (
    background_tutor_enabled, inner_diary_model_enabled,
    inner_diary_deep_reflection_enabled, legacy_model_lanes_enabled, private_residue_curator_enabled,
)
from runtime.coordinator import RuntimeCoordinator
from runtime.memory_semantic_parser_adapter import build_runtime_memory_semantic_parser

from .contracts import TurnArtifact


class ProviderCallProbe:
    """Decorator đếm request thật mà không đổi contract của provider client."""
    def __init__(self, client: Any, *, lane: str) -> None:
        self._client = client
        self.lane = lane
        self._lock = threading.Lock()
        self.call_count = 0
        self.success_count = 0
        self.failure_count = 0

    def __getattr__(self, name: str) -> Any:
        return getattr(self._client, name)

    def create_chat_completion(self, **kwargs):
        with self._lock:
            self.call_count += 1
        try:
            result = self._client.create_chat_completion(**kwargs)
        except Exception:
            with self._lock:
                self.failure_count += 1
            raise
        with self._lock:
            self.success_count += 1
        return result


class CaptureTransport:
    """Transport quan sát public surface mà không cần Godot đang chạy."""
    def __init__(self) -> None:
        self.payloads: list[dict[str, Any]] = []
        self.panel_payloads: list[dict[str, Any]] = []

    def send_json(self, payload: dict) -> None:
        self.payloads.append(dict(payload or {}))

    def send_text(self, message: str) -> None:
        self.payloads.append({"type": "raw_text", "text": str(message or "")})

    def send_panel_state(self, payload: dict) -> str:
        self.panel_payloads.append(dict(payload or {}))
        return f"sim-panel-{len(self.panel_payloads)}"

    def poll_json(self):
        return None

    def wait_for_autonomous(
        self,
        *,
        scenario_id: str,
        step_id: str,
        turn_index: int,
        day: int = 1,
        timeout_seconds: float = 150.0,
    ) -> TurnArtifact:
        """Chờ chính runtime tự phát public turn, không gửi thêm user message.

        Đây là điểm V58 kiểm tra *chủ động thật*: nếu hết timeout mà không có public
        text thì test ghi silence; nếu có text nhưng không qua provider thật thì run
        không được tính là realism pass.
        """
        if self.coordinator is None:
            raise RuntimeError("runtime_not_built")
        start_payload = len(self.transport.payloads)
        main_before = int(getattr(self.main_probe, "success_count", 0) or 0)
        surface_before = int(getattr(self.surface_probe, "success_count", 0) or 0)
        started = time.perf_counter()
        deadline = time.monotonic() + max(1.0, float(timeout_seconds))
        payload: dict[str, Any] = {}
        while time.monotonic() < deadline:
            payload = self.transport.public_payload_since(start_payload)
            if str(payload.get("text", "") or "").strip():
                break
            time.sleep(0.25)
        latency = (time.perf_counter() - started) * 1000.0
        eris_text = str(payload.get("text", "") or "").strip()
        main_after = int(getattr(self.main_probe, "success_count", 0) or 0)
        surface_after = int(getattr(self.surface_probe, "success_count", 0) or 0)
        provider_call_delta = max(0, main_after - main_before) + max(0, surface_after - surface_before)
        provider_real = bool(eris_text and self.provider_real and provider_call_delta > 0)
        subject = getattr(self.coordinator, "subject_service", None)
        ledger_tail: list[dict[str, Any]] = []
        ledger_view = getattr(subject, "decision_ledger_view", None)
        if callable(ledger_view):
            with contextlib.suppress(Exception):
                ledger_tail = list(ledger_view(limit=6))
        initiative_view = {}
        view = getattr(subject, "initiative_view", None)
        if callable(view):
            with contextlib.suppress(Exception):
                initiative_view = dict(view())
        return TurnArtifact(
            scenario_id=scenario_id, step_id=step_id, turn_index=turn_index, day=day,
            user_text="<SILENCE: user không gửi lệnh mới>", eris_text=eris_text,
            provider=self.provider, model=self.model, provider_real=provider_real,
            latency_ms=latency, provider_call_delta=provider_call_delta,
            raw_public_payload=payload, subject_ledger_tail=ledger_tail,
            runtime_state={
                "public_payload_count": len(self.transport.payloads),
                "initiative": initiative_view,
                "autonomous_timeout_seconds": float(timeout_seconds),
            },
            error="" if eris_text else "autonomous_timeout_no_public_text",
        )

    def close(self) -> None:
        return None

    def public_payload_since(self, start: int) -> dict[str, Any]:
        candidates = []
        for payload in self.payloads[start:]:
            text = str(payload.get("text", "") or "").strip()
            if text and bool(payload.get("should_speak", True)):
                candidates.append(payload)
        return dict(candidates[-1]) if candidates else {}


class ControlledTelemetry:
    """Telemetry deterministic để câu benchmark không đổi theo cửa sổ desktop."""
    def capture_snapshot(self, *, mood_score: int, inferred_pos_x: float) -> TelemetrySnapshot:
        return TelemetrySnapshot(
            captured_at=time.strftime("%H:%M"), active_window="V57 Simulation",
            active_window_kind="simulation", focus_summary="controlled chat benchmark",
            active_app="eris-v57-simulator", active_process="python", active_window_title="V57 Simulation",
            interaction_state="active", cpu_percent=20.0, ram_percent=40.0,
            ambient_tone="neutral", tease_bias="neutral", inferred_pos_x=float(inferred_pos_x),
            idle_seconds=0.0, idle_label="active", recent_events=("simulation_user_turn",),
            evidence=("v57:controlled_telemetry",), claim_style="FACT",
        )

    def format_snapshot(self, snapshot: TelemetrySnapshot) -> str:
        return "V57 controlled chat simulation; user is active."

    def format_directive_snapshot(self, snapshot: TelemetrySnapshot) -> str:
        return self.format_snapshot(snapshot)

    def capture_machine_context(self, *, mood_score: int, inferred_pos_x: float) -> dict[str, object]:
        return self.capture_snapshot(mood_score=mood_score, inferred_pos_x=inferred_pos_x).to_machine_context()


class LiveRuntimeHarness:
    """Một instance runtime sống trên persistent root riêng của simulation."""
    def __init__(self, *, run_root: Path, logger: logging.Logger | None = None) -> None:
        self.run_root = Path(run_root).resolve()
        self.run_root.mkdir(parents=True, exist_ok=True)
        self.logger = logger or logging.getLogger("eris.v57.live")
        self.transport = CaptureTransport()
        self.process_manager: RemoteModelProcessManager | None = None
        self.llm_service: LlmService | None = None
        self.coordinator: RuntimeCoordinator | None = None
        self.provider = ""
        self.model = ""
        self.provider_real = False
        self.main_probe: ProviderCallProbe | None = None
        self.surface_probe: ProviderCallProbe | None = None
        self._previous_env: dict[str, str | None] = {}
        self._configure_isolation()
        self._build()

    def _set_env(self, key: str, value: str) -> None:
        if key not in self._previous_env:
            self._previous_env[key] = os.environ.get(key)
        os.environ[key] = value

    def _configure_isolation(self) -> None:
        # Không cho benchmark viết vào ký ức thật của user hoặc thực thi mạng/tool.
        memory_root = self.run_root / "memory" / "core"
        subject_root = self.run_root / "subject_core"
        workspace = self.run_root / "workspace"
        for path in (memory_root, subject_root, workspace):
            path.mkdir(parents=True, exist_ok=True)
        self._set_env("ERIS_MEMORY_CORE_DIR", str(memory_root))
        self._set_env("ERIS_SUBJECT_CORE_DATA_DIR", str(subject_root))
        self._set_env("ERIS_SUBJECT_WORKSPACE", str(workspace))
        self._set_env("ERIS_SANDBOX_NETWORK_ENABLED", "0")
        self._set_env("ERIS_TTS_ENABLED", "0")
        self._set_env("ERIS_TTS_SYNC_TEXT", "0")
        self._set_env("ERIS_REMOTE_API_ENABLED", "1")
        self._set_env("ERIS_EXPOSE_DEBUG_PAYLOAD", "1")
        self._set_env("ERIS_SIMULATION_MODE", "live_behavior_v57")

    @staticmethod
    def _provider_identity(client: Any) -> tuple[str, str, bool]:
        base = str(getattr(client, "base_url", "") or "").strip()
        model = str(getattr(client, "model_name", "") or getattr(client, "configured_model_name", "") or "unknown")
        lower = base.lower()
        provider = (
            "deepseek" if "deepseek" in lower or "deepseek" in model.lower() else
            "groq" if "groq" in lower else
            "gemini" if "generativelanguage.googleapis.com" in lower or "gemini" in model.lower() else
            "openai" if "openai" in lower else
            "local_gateway" if lower.startswith(("http://127.0.0.1", "http://localhost")) else
            base or client.__class__.__name__
        )
        real = isinstance(client, (OpenAICompatibleClient, GeminiClient)) or (
            callable(getattr(client, "create_chat_completion", None))
            and bool(base)
            and not bool(getattr(client, "is_simulation_stub", False))
        )
        return provider, model, real

    def _build(self) -> None:
        project_root = str(Path(__file__).resolve().parents[2])
        manager = RemoteModelProcessManager(logger=self.logger, project_root=project_root)
        self.process_manager = manager
        raw_main = manager.build_client()
        self.provider, self.model, self.provider_real = self._provider_identity(raw_main)
        if not self.provider_real:
            raise RuntimeError("V57 refuses non-real main provider")
        # Probe main semantic authority: provider_real của từng turn chỉ TRUE nếu
        # chính turn đó thực sự đi qua create_chat_completion thành công.
        main = ProviderCallProbe(raw_main, lane="companion")
        self.main_probe = main

        style = manager.build_style_client() if legacy_model_lanes_enabled() else None
        vision = manager.build_vision_client()
        fallback = manager.build_format_fallback_client()
        escalation = manager.build_escalation_client()
        social_enabled = str(os.getenv("ERIS_SOCIAL_OBSERVER_ENABLED", "1") or "1").lower() not in {"0", "false", "off", "no"}
        social_parser = manager.build_social_parser_client() if social_enabled else None
        social_critic = manager.build_social_critic_client() if social_enabled else None
        social_fallback = manager.build_social_fallback_client() if social_enabled and legacy_model_lanes_enabled() else None
        inner = manager.build_inner_client()
        executive = manager.build_executive_client()
        diary = manager.build_diary_client() if inner_diary_model_enabled() and inner_diary_deep_reflection_enabled() else None
        residue_curator = manager.build_residue_curator_client() if private_residue_curator_enabled() else None
        seed = manager.build_seed_client()
        raw_surface = manager.build_surface_client()
        surface = ProviderCallProbe(raw_surface, lane="surface") if raw_surface is not None else None
        self.surface_probe = surface if isinstance(surface, ProviderCallProbe) else None
        research = manager.build_research_client() if background_tutor_enabled() else None
        tutor = manager.build_tutor_client() if background_tutor_enabled() else None
        curator = manager.build_curator_client()
        self.llm_service = LlmService(
            main, style_client=style, vision_client=vision, fallback_client=fallback,
            escalation_client=escalation, social_parser_client=social_parser,
            social_critic_client=social_critic, social_fallback_client=social_fallback,
            observer_client=social_parser, critic_client=social_critic, inner_client=inner,
            diary_client=diary, residue_curator_client=residue_curator, seed_client=seed, surface_client=surface,
            executive_client=executive, research_client=research, tutor_client=tutor,
            curator_client=curator, logger=self.logger,
        )
        memory_parser = build_runtime_memory_semantic_parser(llm_service=self.llm_service, logger=self.logger)
        memory_core = MemoryCognitionCore(str(self.run_root / "memory" / "core"))
        with contextlib.suppress(Exception):
            memory_core.load()
        parser = DecisionParser(
            Decision(text="V57 runtime parse failure", animation="stand", extracted_fact=""),
            logger=self.logger,
        )
        self.coordinator = RuntimeCoordinator(
            logger=self.logger, transport=self.transport, llm_service=self.llm_service,
            parser=parser, telemetry_service=ControlledTelemetry(), memory_core=memory_core,
            body_running_fn=lambda: False, action_executor=ActionExecutor(logger=self.logger),
            memory_semantic_parser=memory_parser,
        )
        self.coordinator.long_term_memory = []

    def judge_candidate_client(self) -> Any:
        service = self.llm_service
        if service is None:
            return None
        # Ưu tiên lane khác main để giảm tự-chấm-cùng-model nếu cấu hình hiện tại có.
        return (
            getattr(service, "escalation_client", None)
            or getattr(service, "critic_client", None)
            or getattr(service, "client", None)
        )

    def send_user(self, *, scenario_id: str, step_id: str, turn_index: int, day: int, text: str) -> TurnArtifact:
        if self.coordinator is None:
            raise RuntimeError("runtime_not_built")
        start_payload = len(self.transport.payloads)
        probe_before = int(getattr(self.main_probe, "success_count", 0) or 0)
        started = time.perf_counter()
        error = ""
        try:
            self.coordinator._handle_incoming({
                "type": "chat", "message": str(text or ""), "timestamp": time.time(),
                "simulation": {"scenario_id": scenario_id, "step_id": step_id, "day": day},
            })
        except Exception as exc:  # report phải giữ lỗi thật thay vì biến nó thành câu fallback giả đẹp.
            error = f"{type(exc).__name__}: {exc}"
        latency = (time.perf_counter() - started) * 1000.0
        payload = self.transport.public_payload_since(start_payload)
        eris_text = str(payload.get("text", "") or "").strip()
        probe_after = int(getattr(self.main_probe, "success_count", 0) or 0)
        provider_call_delta = max(0, probe_after - probe_before)
        turn_provider_real = bool(self.provider_real and provider_call_delta > 0)

        ledger_tail: list[dict[str, Any]] = []
        subject = getattr(self.coordinator, "subject_service", None)
        ledger_view = getattr(subject, "decision_ledger_view", None)
        if callable(ledger_view):
            with contextlib.suppress(Exception):
                ledger_tail = list(ledger_view(limit=4))
        runtime_state = {
            "chat_history_size": len(getattr(self.coordinator, "chat_history", ()) or ()),
            "subject_projection_revision": int(getattr(getattr(subject, "projection", None), "revision", 0) or 0),
            "public_payload_count": len(self.transport.payloads),
        }
        return TurnArtifact(
            scenario_id=scenario_id, step_id=step_id, turn_index=turn_index, day=day,
            user_text=text, eris_text=eris_text, provider=self.provider, model=self.model,
            provider_real=turn_provider_real, latency_ms=latency, provider_call_delta=provider_call_delta,
            raw_public_payload=payload,
            subject_ledger_tail=ledger_tail, runtime_state=runtime_state, error=error,
        )

    def wait_for_autonomous(
        self,
        *,
        scenario_id: str,
        step_id: str,
        turn_index: int,
        day: int = 1,
        timeout_seconds: float = 150.0,
    ) -> TurnArtifact:
        """Chờ chính runtime tự phát public turn, không gửi thêm user message.

        Đây là điểm V58 kiểm tra *chủ động thật*: nếu hết timeout mà không có public
        text thì test ghi silence; nếu có text nhưng không qua provider thật thì run
        không được tính là realism pass.
        """
        if self.coordinator is None:
            raise RuntimeError("runtime_not_built")
        start_payload = len(self.transport.payloads)
        main_before = int(getattr(self.main_probe, "success_count", 0) or 0)
        surface_before = int(getattr(self.surface_probe, "success_count", 0) or 0)
        started = time.perf_counter()
        deadline = time.monotonic() + max(1.0, float(timeout_seconds))
        payload: dict[str, Any] = {}
        while time.monotonic() < deadline:
            payload = self.transport.public_payload_since(start_payload)
            if str(payload.get("text", "") or "").strip():
                break
            time.sleep(0.25)
        latency = (time.perf_counter() - started) * 1000.0
        eris_text = str(payload.get("text", "") or "").strip()
        main_after = int(getattr(self.main_probe, "success_count", 0) or 0)
        surface_after = int(getattr(self.surface_probe, "success_count", 0) or 0)
        provider_call_delta = max(0, main_after - main_before) + max(0, surface_after - surface_before)
        provider_real = bool(eris_text and self.provider_real and provider_call_delta > 0)
        subject = getattr(self.coordinator, "subject_service", None)
        ledger_tail: list[dict[str, Any]] = []
        ledger_view = getattr(subject, "decision_ledger_view", None)
        if callable(ledger_view):
            with contextlib.suppress(Exception):
                ledger_tail = list(ledger_view(limit=6))
        initiative_view = {}
        view = getattr(subject, "initiative_view", None)
        if callable(view):
            with contextlib.suppress(Exception):
                initiative_view = dict(view())
        return TurnArtifact(
            scenario_id=scenario_id, step_id=step_id, turn_index=turn_index, day=day,
            user_text="<SILENCE: user không gửi lệnh mới>", eris_text=eris_text,
            provider=self.provider, model=self.model, provider_real=provider_real,
            latency_ms=latency, provider_call_delta=provider_call_delta,
            raw_public_payload=payload, subject_ledger_tail=ledger_tail,
            runtime_state={
                "public_payload_count": len(self.transport.payloads),
                "initiative": initiative_view,
                "autonomous_timeout_seconds": float(timeout_seconds),
            },
            error="" if eris_text else "autonomous_timeout_no_public_text",
        )

    def close(self) -> None:
        coordinator = self.coordinator
        if coordinator is not None:
            for name in ("shutdown", "close", "stop"):
                method = getattr(coordinator, name, None)
                if callable(method):
                    with contextlib.suppress(Exception):
                        method()
                    break
        if self.process_manager is not None:
            with contextlib.suppress(Exception):
                self.process_manager.stop()
        self.transport.close()
        for key, prior in self._previous_env.items():
            if prior is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = prior


__all__ = ["CaptureTransport", "ControlledTelemetry", "LiveRuntimeHarness", "ProviderCallProbe"]
