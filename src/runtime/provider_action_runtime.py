# -*- coding: utf-8 -*-
"""Provider registry boundary and system-action execution.

Extracted from RuntimeCoordinator so the composition root stops owning every policy.
"""
from __future__ import annotations

from actions.confirmation import public_action_text
from domain import Decision


class ProviderActionRuntimeMixin:
    """Coordinator-facing mixin with no independent lifecycle."""

    def _initialize_provider_registry(self) -> None:
        """Bọc component hiện tại vào registry typed, không rewrite nóng toàn repo."""
        from llm.token_pressure import GLOBAL_TOKEN_PRESSURE_GOVERNOR
        from runtime.provider_adapters import build_provider_registry

        memory_provider = self.memory_kernel or self.memory_core
        self.provider_registry = build_provider_registry(
            action_executor=self.action_executor,
            speech_engine=getattr(self, "tts_engine", None),
            memory=memory_provider,
            budget_governor=GLOBAL_TOKEN_PRESSURE_GOVERNOR,
        )
        self._record_provider_health_snapshot(event="bootstrap")


    def bind_tts_engine(self, tts_engine) -> None:
        """Binding duy nhất khi TTS engine được tạo/hot-reload.

        ``vision.py`` trước đây gán attribute trực tiếp, khiến registry và
        VoiceManager không biết engine đã đổi. Giờ cả ba cùng nhận một instance.
        """
        self.tts_engine = tts_engine
        try:
            self.voice_manager.bind_tts_engine(tts_engine)
        except Exception as exc:
            self.logger.warning("Không bind được TTS vào voice manager: %s", exc)
        try:
            from runtime.provider_adapters import SpeechEngineProvider, register_or_replace
            from runtime.provider_interfaces import ProviderRegistry

            registry = getattr(self, "provider_registry", None)
            if not isinstance(registry, ProviderRegistry):
                self._initialize_provider_registry()
                registry = self.provider_registry
            register_or_replace(registry, "speech", SpeechEngineProvider(tts_engine))
            self._record_provider_health_snapshot(event="speech_bind")
        except Exception as exc:
            self.logger.warning("Không bind được speech provider: %s", exc)


    def _provider_registry_is_strict(self) -> bool:
        config = getattr(self, "hardening_config", None)
        if config is None:
            try:
                from runtime.hardening_config import HardeningConfig

                config = HardeningConfig.load()
                self.hardening_config = config
            except (ImportError, OSError, ValueError, RuntimeError) as exc:
                self.logger.warning("Không đọc được hardening config, dùng strict mode: %s", exc)
                return True
        return bool(getattr(config, "provider_registry_strict", True))


    def _get_runtime_provider(self, kind: str):
        registry = getattr(self, "provider_registry", None)
        if registry is None:
            return None
        try:
            return registry.get_optional(kind)
        except (AttributeError, KeyError, TypeError):
            return None


    def _execute_action_provider(self, action_name: str, action_input: str = ""):
        provider = self._get_runtime_provider("action")
        if provider is None:
            raise RuntimeError("action_provider_unavailable")
        return provider.execute(action_name, action_input)


    def query_memory_provider(self, request):
        provider = self._get_runtime_provider("memory")
        if provider is None:
            raise RuntimeError("memory_provider_unavailable")
        return provider.query(request)


    def commit_memory_provider(self, record):
        provider = self._get_runtime_provider("memory")
        if provider is None:
            raise RuntimeError("memory_provider_unavailable")
        return provider.commit(record)


    def plan_budget_provider(self, *, provider: str, lane: str, prompt_tokens: int, output_tokens: int):
        budget = self._get_runtime_provider("budget")
        if budget is None:
            raise RuntimeError("budget_provider_unavailable")
        return budget.plan(
            provider=provider, lane=lane, prompt_tokens=prompt_tokens, output_tokens=output_tokens
        )


    def provider_health_snapshot(self) -> dict[str, dict]:
        registry = getattr(self, "provider_registry", None)
        if registry is None:
            return {}
        output: dict[str, dict] = {}
        try:
            health = registry.health()
        except Exception as exc:
            self.logger.warning("Không đọc được provider health: %s", exc)
            return output
        for kind, item in health.items():
            output[str(kind)] = {
                "ready": bool(getattr(item, "ready", False)),
                "provider": str(getattr(item, "provider", "") or ""),
                "reason": str(getattr(item, "reason", "") or ""),
                "details": dict(getattr(item, "details", {}) or {}),
            }
        return output


    def _record_provider_health_snapshot(self, *, event: str) -> None:
        snapshot = self.provider_health_snapshot()
        obs = getattr(self, "observability", None)
        if obs is None:
            return
        for kind, item in snapshot.items():
            try:
                obs.gauge(
                    "provider_ready",
                    1.0 if item.get("ready") else 0.0,
                    tags={"kind": kind, "provider": item.get("provider", "unknown")},
                    fields={"event": event, "reason": item.get("reason", "")},
                )
            except Exception as exc:
                self.logger.debug("Provider readiness metric failed for %s: %s", kind, exc)
        try:
            obs.log(
                "provider_health_snapshot",
                tags={"event": event},
                fields={"providers": snapshot},
                status="ok" if all(item.get("ready") for item in snapshot.values()) else "error",
            )
        except Exception as exc:
            self.logger.debug("Provider health log failed: %s", exc)


    def _execute_system_action(self, decision: Decision, *, snapshot, frame=None) -> Decision:
        # Phòng thủ tương thích: model/frame cũ có thể còn phát action đã nghỉ hưu.
        # Bỏ action nhưng giữ nguyên câu trả lời, không cho lỗi executor chen vào chat.
        retired_action = str(getattr(decision, "system_action", "") or "").strip()
        if retired_action in {"search_web", "project_execute_task"}:
            # ``project_execute_task`` used to own a second one-shot coding loop.
            # Keep its grammar/contract for old checkpoints, but never let a stale
            # model decision bypass the dedicated coding Agent Harness.
            decision.system_action = ""
            decision.action_input = ""
            decision.debug_trace = self._join_debug_trace(
                getattr(decision, "debug_trace", ""),
                f"retired_{retired_action}_suppressed",
            )
            return decision
        if self.action_executor is None or not decision.system_action:
            return decision

        typed_arguments = getattr(decision, "capability_arguments", None)
        if isinstance(typed_arguments, dict):
            executor = getattr(self.action_executor, "execute_capability", None)
            result = (
                executor(decision.system_action, typed_arguments)
                if callable(executor)
                else self._execute_action_provider(decision.system_action, decision.action_input)
            )
        else:
            result = self._execute_action_provider(decision.system_action, decision.action_input)
        decision.action_result_meta = result.to_public_meta()
        if frame is not None:
            try:
                frame.tool_execution_result = result
            except (AttributeError, TypeError):
                pass
        recorder = getattr(self, "_record_tool_grounding", None)
        if callable(recorder):
            recorder(result, action_name=decision.system_action)
        action_trace = f"action={result.action_name}:{'ok' if result.success else 'fail'}:{result.public_mode}"
        if decision.debug_trace:
            decision.debug_trace = f"{decision.debug_trace} | {action_trace}"[:240]
        else:
            decision.debug_trace = action_trace
        result_text = self._action_result_to_text(result)
        # Tool/action là nguồn sự thật cuối. Không được để model giữ câu "đã làm"
        # khi executor fail hoặc action bị alias/sanitize. Báo thành công giả là cách
        # nhanh nhất để biến assistant thành cái đài phát thanh của niềm tin mù quáng.
        # Once a capability actually ran, its verified result owns the factual
        # public text. The language model may frame intent beforehand, but it may
        # not preserve a contradictory success/denial sentence afterwards.
        if str(result_text or "").strip():
            decision.text = result_text
            decision.should_speak = bool(str(result_text or "").strip())
            if not result.success:
                decision.confidence = min(float(getattr(decision, "confidence", 0.0) or 0.0), 0.45)
                decision.extracted_fact = {}
        if result.action_name == "get_now_playing" and "media session" in decision.text.lower():
            decision.text = self._resolve_now_playing_text(snapshot) or decision.text
        return decision


    def _action_animation_hint(self, action_name: str, fallback: str) -> str:
        if action_name == "get_now_playing":
            return "read"
        if action_name in {
            "review_discourse_claim", "inspect_unresolved_questions",
            "inspect_memory_candidates", "compare_recent_turns",
            "inspect_social_state", "detect_internal_contradictions",
            "summarize_open_loops", "inspect_capability_health",
        }:
            return "read"
        if action_name in {"open_url", "focus_window", "launch_app"}:
            return "stand_walk"
        if action_name in {"copy_text", "set_volume", "play_pause_media"}:
            return "stand"
        return fallback or "stand"


    def _action_result_to_text(self, result) -> str:
        """Public surface chỉ được mạnh bằng execution evidence hiện có."""
        return public_action_text(result)


    def _resolve_now_playing_text(self, snapshot) -> str | None:
        title = self._extract_media_title_from_snapshot(snapshot)
        if not title:
            return None
        return f"Tôi thấy tab media hiện tại là {title}."


    def _extract_media_title_from_snapshot(self, snapshot) -> str:
        candidates = [snapshot.active_window, *snapshot.browser_tabs, *snapshot.open_windows]
        for candidate in candidates:
            title = self._extract_media_title(candidate)
            if title:
                return title
        return ""


    def _extract_media_title(self, raw_title: str) -> str:
        title = (raw_title or "").strip()
        if not title:
            return ""

        lowered = title.lower()
        if "youtube" in lowered:
            cleaned = title
            for suffix in (
                " - YouTube - Brave",
                " - YouTube - Google Chrome",
                " - YouTube - Microsoft Edge",
                " - YouTube",
                " - Brave",
                " - Google Chrome",
                " - Microsoft Edge",
            ):
                if cleaned.endswith(suffix):
                    cleaned = cleaned[: -len(suffix)]
            cleaned = cleaned.strip(" -|")
            if cleaned and cleaned.lower() != "youtube":
                return cleaned

        if "spotify" in lowered:
            cleaned = title
            for suffix in (
                " - Spotify Premium",
                " - Spotify",
                " - Brave",
                " - Google Chrome",
                " - Microsoft Edge",
            ):
                if cleaned.endswith(suffix):
                    cleaned = cleaned[: -len(suffix)]
            cleaned = cleaned.strip(" -|")
            if cleaned and cleaned.lower() != "spotify":
                return cleaned

        return ""


__all__ = ["ProviderActionRuntimeMixin"]
