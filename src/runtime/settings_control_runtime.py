# -*- coding: utf-8 -*-
"""Advanced settings protocol, health snapshots and live refresh.

Extracted from RuntimeCoordinator so the composition root stops owning every policy.
"""
from __future__ import annotations

import os
import threading
import time
from collections.abc import Mapping


class SettingsControlRuntimeMixin:
    """Coordinator-facing mixin with no independent lifecycle."""

    def _advanced_effective_settings_snapshot(self) -> dict:
        """Snapshot đầy đủ cho Settings UI, không chứa credential.

        Body không tự đoán schema hay đọc file runtime_settings.json. Brain trả
        settings hiệu lực cùng catalog owner/type để panel dựng đúng control.
        """

        try:
            from runtime.runtime_settings_store import get_runtime_settings_store
            from runtime.settings_catalog import build_settings_catalog

            store = getattr(self, "runtime_settings_store", None) or get_runtime_settings_store(logger=self.logger)
            settings = store.snapshot()
            metadata = store.metadata()
            metadata["settings"] = {
                str(section): dict(values)
                for section, values in settings.items()
                if section != "version" and isinstance(values, Mapping)
            }
            metadata["catalog"] = build_settings_catalog(settings)
            metadata["authority"] = "brain_runtime_store"
            return metadata
        except Exception as exc:
            return {"revision": 0, "saved_at": 0.0, "settings": {}, "catalog": {}, "error": str(exc)[:240]}


    def _main_brain_health_snapshot(self) -> dict:
        """Trả danh tính não chính và credential hiệu lực, tuyệt đối không lộ key."""

        client = getattr(getattr(self, "llm_service", None), "client", None)
        requested_provider = str(os.environ.get("ERIS_REMOTE_PROVIDER", "deepseek") or "deepseek").strip().lower()
        base_url = str(getattr(client, "base_url", "") or "").strip()
        model = str(getattr(client, "model_name", "") or "").strip()
        try:
            from llm.token_budget import detect_provider
            detected_provider = detect_provider(base_url, model)
        except Exception:
            detected_provider = requested_provider or "unknown"

        pool = getattr(client, "key_pool", None)
        try:
            pool_snapshot = pool.snapshot() if pool is not None else {}
        except Exception:
            pool_snapshot = {}
        active_fingerprint = ""
        keys = pool_snapshot.get("keys", []) if isinstance(pool_snapshot, Mapping) else []
        if isinstance(keys, list):
            for item in keys:
                if isinstance(item, Mapping) and bool(item.get("active")):
                    active_fingerprint = str(item.get("fingerprint") or "")
                    break

        config_source = "ERIS_REMOTE_*"
        try:
            from eris_secrets import get_provider_profile
            profile = get_provider_profile(requested_provider, slot="remote_main")
            if profile:
                config_source = f"providers.{requested_provider}.remote_main"
        except Exception:
            pass

        fallback = getattr(getattr(self, "llm_service", None), "fallback_client", None)
        fallback_model = str(getattr(fallback, "model_name", "") or "").strip()
        return {
            "ready": client is not None,
            "requested_provider": requested_provider,
            "provider": detected_provider,
            "model": model,
            "base_url": base_url,
            "pool": "remote",
            "configured_keys": int(pool_snapshot.get("size", 0) or 0) if isinstance(pool_snapshot, Mapping) else 0,
            "available_keys": int(pool_snapshot.get("available", 0) or 0) if isinstance(pool_snapshot, Mapping) else 0,
            "active_key_fingerprint": active_fingerprint,
            "config_source": config_source,
            "fallback_model": fallback_model,
        }


    def _deepseek_workers_health_snapshot(self) -> dict:
        """Read-only DeepSeek worker wiring for Settings/diagnostics."""

        service = getattr(self, "llm_service", None)
        role_attrs = {
            "companion": "companion_client",
            "observer": "observer_client",
            "critic": "critic_client",
            "inner": "inner_client",
            "diary": "diary_client",
            "seed": "seed_client",
            "surface": "surface_client",
            "style": "style_client",
            "research": "research_client",
        }
        try:
            from llm.deepseek_workers import WORKER_ROLES, load_worker_spec
        except Exception as exc:
            return {"available": False, "error": f"{type(exc).__name__}: {exc}"[:240], "roles": {}}

        roles: dict[str, dict] = {}
        configured_count = 0
        ready_count = 0
        for role in WORKER_ROLES:
            try:
                spec = load_worker_spec(role)
            except Exception as exc:
                roles[str(role)] = {
                    "configured": False,
                    "ready": False,
                    "reason": f"spec_error:{type(exc).__name__}",
                }
                continue

            configured = bool(getattr(spec, "configured", False))
            configured_count += 1 if configured else 0
            client = getattr(service, role_attrs.get(role, ""), None) if service is not None else None
            if role == "companion" and client is None and service is not None:
                client = getattr(service, "client", None)
            worker_role = str(getattr(client, "worker_role", "") or "").strip().lower()
            ready = client is not None and (worker_role == role or (role == "companion" and not worker_role))
            ready_count += 1 if ready else 0

            fingerprint = ""
            api_key = str(getattr(spec, "api_key", "") or "")
            if api_key:
                try:
                    import hashlib

                    fingerprint = hashlib.sha256(api_key.encode("utf-8")).hexdigest()[:12]
                except Exception:
                    fingerprint = "configured"
            roles[role] = {
                "configured": configured,
                "ready": ready,
                "role": role,
                "worker_role": worker_role,
                "base_url": str(getattr(spec, "base_url", "") or ""),
                "model": str(getattr(spec, "model", "") or ""),
                "jsonl": bool(getattr(spec, "jsonl_enabled", False)),
                "key_fingerprint": fingerprint,
                "reason": "" if ready else ("not_configured" if not configured else "client_not_built"),
            }

        return {
            "available": True,
            "configured_count": configured_count,
            "ready_count": ready_count,
            "roles": roles,
        }


    def _handle_settings_connection_test(self, msg: dict) -> None:
        """Chạy request cực ngắn bằng đúng client main đang hiệu lực.

        Test chạy ở thread riêng để một provider chậm không khóa UDP/chat loop.
        Body chỉ nhận model, latency, HTTP status và fingerprint; không có raw key.
        """

        request_id = str(msg.get("request_id") or f"brain_test_{int(time.time() * 1000)}")
        worker = threading.Thread(
            target=self._run_settings_connection_test,
            args=(request_id,),
            name="eris-settings-connection-test",
            daemon=True,
        )
        worker.start()


    def _run_settings_connection_test(self, request_id: str) -> None:
        started = time.perf_counter()
        snapshot = self._main_brain_health_snapshot()
        payload = {
            "request_id": request_id,
            "ok": False,
            "latency_ms": 0,
            "response_text": "",
            "error_kind": "",
            "error": "",
            "main_brain": snapshot,
        }
        try:
            client = getattr(getattr(self, "llm_service", None), "client", None)
            if client is None:
                raise RuntimeError("main client chưa được khởi tạo")
            from runtime.inference_broker import inference_context
            with inference_context(
                "chat_tool_required",
                optional=False,
                timeout_seconds=15.0,
                cancel_scope=f"settings_test:{request_id}",
                metadata={"operation": "settings_connection_test"},
            ):
                response = client.create_chat_completion(
                    messages=[
                        {"role": "system", "content": "Connection health check. Reply with exactly OK."},
                        {"role": "user", "content": "OK"},
                    ],
                    temperature=0.0,
                    frequency_penalty=0.0,
                    presence_penalty=0.0,
                    max_tokens=8,
                    timeout=15,
                    reasoning_effort="low",
                )
            extract = getattr(getattr(self, "llm_service", None), "_extract_content", None)
            text_value = extract(response) if callable(extract) else ""
            payload["ok"] = True
            payload["response_text"] = str(text_value or "OK").strip()[:80]
            payload["main_brain"] = self._main_brain_health_snapshot()
        except Exception as exc:
            status = getattr(getattr(exc, "response", None), "status_code", None)
            if status in (401, 403):
                kind = "invalid_key"
            elif status == 404:
                kind = "model_or_endpoint_not_found"
            elif status == 429:
                kind = "rate_limited"
            elif status is not None and int(status) >= 500:
                kind = "provider_unavailable"
            elif isinstance(exc, TimeoutError) or "timeout" in str(exc).lower():
                kind = "timeout"
            else:
                kind = "connection_error"
            payload["error_kind"] = kind
            payload["error"] = str(exc)[:320]
            self.logger.warning("[settings] main brain connection test failed kind=%s error=%s", kind, exc)
        finally:
            payload["latency_ms"] = int(max(0.0, time.perf_counter() - started) * 1000)
            self._send_runtime_json({
                "type": "settings_connection_test_ack",
                "parse_mode": "settings_connection_test_ack",
                "should_speak": False,
                "settings_connection_test_ack": payload,
            }, channel="settings")


    def _handle_settings_health(self) -> None:
        """Gửi trạng thái credential owner và não chính mà không lộ API key.

        Trước đây nút kiểm tra chỉ gửi message nhưng coordinator chưa xử lý.
        Handler này trả snapshot thật gồm key đang dùng, key cooldown và cooldown
        toàn pool để UI hiển thị đúng trạng thái.
        """

        try:
            from llm.key_pool import get_key_pool

            pools = {
                "main": get_key_pool("remote_main").snapshot(),
                "gemini": get_key_pool("gemini").snapshot(),
                "groq": get_key_pool("groq").snapshot(),
            }
            # ElevenLabs là credential đơn, không đi qua llm.key_pool. Vẫn trả
            # cùng shape health để Body không phải bịa thêm một loại UI riêng.
            try:
                import hashlib
                from eris_secrets import get_secret

                eleven_key = str(get_secret("ELEVENLABS_API_KEY", "ERIS_ELEVENLABS_API_KEY") or "").strip()
                eleven_fingerprint = hashlib.sha256(eleven_key.encode("utf-8")).hexdigest()[:12] if eleven_key else ""
                pools["elevenlabs"] = {
                    "provider": "elevenlabs",
                    "size": 1 if eleven_key else 0,
                    "available": 1 if eleven_key else 0,
                    "dead": 0,
                    "global_cooldown_ms": 0,
                    "keys": ([{
                        "index": 0,
                        "fingerprint": eleven_fingerprint,
                        "status": "configured",
                        "reason": "",
                        "cooldown_ms": 0,
                        "active": True,
                    }] if eleven_key else []),
                }
            except Exception:
                pools["elevenlabs"] = {
                    "provider": "elevenlabs",
                    "size": 0,
                    "available": 0,
                    "dead": 0,
                    "global_cooldown_ms": 0,
                    "keys": [],
                }
            try:
                from actions.capability_runtime import runtime_capability_snapshot
                capability_health = runtime_capability_snapshot(refresh=True)
            except Exception as capability_exc:
                capability_health = {"error": f"{type(capability_exc).__name__}: {capability_exc}"}
            try:
                from runtime.observability import ensure_observability
                telemetry = ensure_observability(self)
                for provider_name, snapshot in pools.items():
                    telemetry.record_provider_pool(provider=provider_name, snapshot=snapshot, event="settings_health")
            except Exception:
                pass
            tts = getattr(self, "tts_engine", None)
            tts_ready = bool(tts is not None and getattr(tts, "_ok", False))
            try:
                from runtime.runtime_settings_store import get_runtime_settings_store

                tts_settings = (getattr(self, "runtime_settings_store", None) or get_runtime_settings_store(logger=self.logger)).snapshot().get("tts", {})
            except Exception:
                tts_settings = {}
            tts_health = {
                "ready": tts_ready,
                "provider": str(tts_settings.get("provider", "elevenlabs") or "elevenlabs"),
                "language": str(tts_settings.get("language", "ja") or "ja"),
                "voice": str(tts_settings.get("voice", "Aoede") or "Aoede"),
                "elevenlabs_voice_id": str(tts_settings.get("elevenlabs_voice_id", "") or ""),
                "elevenlabs_model": str(tts_settings.get("elevenlabs_model", "eleven_v3") or "eleven_v3"),
                "quality": str(tts_settings.get("quality", "balanced") or "balanced"),
                "volume": float(tts_settings.get("volume", 1.0) or 1.0),
                "speed": float(tts_settings.get("speed", 1.04) or 1.04),
            }
            subsystem_health = {}
            health_registry = getattr(self, "subsystem_health_registry", None)
            if callable(getattr(health_registry, "snapshot", None)):
                try:
                    subsystem_health = health_registry.snapshot(include_events=False)
                except Exception as health_exc:
                    subsystem_health = {"error": f"{type(health_exc).__name__}: {health_exc}"}
            ack = {
                "type": "settings_health_ack",
                "parse_mode": "settings_health_ack",
                "should_speak": False,
                "settings_health_ack": {
                    "pools": pools,
                    "main_brain": self._main_brain_health_snapshot(),
                    "deepseek_workers": self._deepseek_workers_health_snapshot(),
                    "tts": tts_health,
                    "capabilities": capability_health,
                    "subsystems": subsystem_health,
                    "runtime_boundaries": {
                        "transport": (
                            self.transport_boundary.snapshot()
                            if callable(getattr(getattr(self, "transport_boundary", None), "snapshot", None))
                            else {}
                        ),
                        "protocol": (
                            self.incoming_dispatcher.snapshot()
                            if callable(getattr(getattr(self, "incoming_dispatcher", None), "snapshot", None))
                            else {}
                        ),
                        "loop": (
                            self.loop_guard.snapshot()
                            if callable(getattr(getattr(self, "loop_guard", None), "snapshot", None))
                            else {}
                        ),
                    },
                    "runtime_diagnostics": (
                        self.runtime_diagnostics.snapshot(include_events=False)
                        if callable(getattr(getattr(self, "runtime_diagnostics", None), "snapshot", None))
                        else {}
                    ),
                    "effective": self._advanced_effective_settings_snapshot(),
                },
            }
        except Exception as exc:
            self.logger.warning("[settings] key pool health snapshot failed: %s", exc)
            ack = {
                "type": "settings_health_ack",
                "parse_mode": "settings_health_ack",
                "should_speak": False,
                "settings_health_ack": {
                    "pools": {},
                    "main_brain": self._main_brain_health_snapshot(),
                    "deepseek_workers": self._deepseek_workers_health_snapshot(),
                    "tts": {"ready": False},
                    "effective": self._advanced_effective_settings_snapshot(),
                    "error": str(exc)[:240],
                },
            }

        self._send_runtime_json(ack, channel="settings")


    def _handle_settings_update(self, msg: dict) -> None:
        """Áp dụng settings live qua schema, persist trước rồi mới refresh runtime."""

        scope = str(msg.get("scope", "runtime") or "runtime").strip().lower()
        try:
            from runtime.runtime_settings_store import get_runtime_settings_store
            from runtime.settings_protocol import normalize_settings_message

            store = getattr(self, "runtime_settings_store", None) or get_runtime_settings_store(logger=self.logger)
            normalized = normalize_settings_message(msg)
            if normalized.scope not in {"tts", "advanced", "personality", "runtime"}:
                raise ValueError(f"unsupported settings scope: {normalized.scope}")
            if not normalized.patch:
                raise ValueError("settings payload không có field hợp lệ")

            result = store.apply_patch(
                normalized.patch,
                restart_required_paths=set(normalized.restart_required_paths),
                materialize_env=False,
            )
            self.runtime_settings_store = store
            self.runtime_settings = result.settings
            rejected = list(normalized.rejected_fields) + list(result.rejected)

            refresh_meta = self._refresh_runtime_after_settings(
                scope=normalized.scope,
                applied_paths=set(result.applied),
            )
            ack_payload = result.to_ack(scope=normalized.scope)
            ack_payload["rejected"] = list(dict.fromkeys(rejected))
            ack_payload["legacy_payload"] = bool(normalized.legacy_payload)
            ack_payload.update(refresh_meta)
            ack_payload["effective"] = self._advanced_effective_settings_snapshot()
            if normalized.scope == "tts":
                tts_settings = result.settings.get("tts", {})
                ack_payload.update({
                    "provider": tts_settings.get("provider", "elevenlabs"),
                    "language": tts_settings.get("language", "ja"),
                    "voice": tts_settings.get("voice", "Aoede"),
                    "elevenlabs_voice_id": tts_settings.get("elevenlabs_voice_id", ""),
                    "elevenlabs_model": tts_settings.get("elevenlabs_model", "eleven_v3"),
                    "quality": tts_settings.get("quality", "balanced"),
                    "volume": tts_settings.get("volume", 1.0),
                    "speed": tts_settings.get("speed", 1.04),
                })

            self.logger.info(
                "[runtime-settings] live scope=%s applied=%d rejected=%d restart=%d",
                normalized.scope, len(result.applied), len(rejected), len(result.restart_required),
            )
            self._send_runtime_json({
                "type": "settings_update_ack",
                "parse_mode": "settings_update_ack",
                "should_speak": False,
                "settings_update_ack": ack_payload,
            })
        except Exception as exc:
            self.logger.warning("[runtime-settings] update scope=%s failed: %s", scope, exc)
            self._send_runtime_json({
                "type": "settings_update_ack",
                "parse_mode": "settings_update_ack",
                "should_speak": False,
                "settings_update_ack": {
                    "scope": scope,
                    "applied": [],
                    "rejected": [str(exc)[:240]],
                    "restart_required": [],
                    "ok": False,
                },
            }, channel="settings")


    def _refresh_runtime_after_settings(self, *, scope: str, applied_paths: set[str]) -> dict:
        """Refresh các component có contract live; mục bootstrap vẫn báo restart."""

        meta = {"worker_rebuilt": False, "engine_ready": False, "live_refreshed": []}
        self.stage_frame_builder = None
        meta["live_refreshed"].append("stage_frame_builder")

        if any(path.startswith("tts.") or path.startswith("stage.") for path in applied_paths):
            tts = getattr(self, "tts_engine", None)
            refresh = getattr(tts, "refresh_configuration", None) if tts is not None else None
            if callable(refresh):
                try:
                    meta["worker_rebuilt"] = bool(refresh())
                    meta["live_refreshed"].append("tts_engine")
                except Exception as exc:
                    self.logger.warning("[runtime-settings] TTS refresh failed: %s", exc)
            meta["engine_ready"] = bool(tts is not None and getattr(tts, "_ok", False))

        if "chat.max_concurrency" in applied_paths:
            try:
                from llm.key_pool import reset_concurrency_limits
                reset_concurrency_limits()
                meta["live_refreshed"].append("provider_concurrency")
            except Exception as exc:
                self.logger.debug("[runtime-settings] concurrency refresh failed: %s", exc)

        # Policy modules đọc store mỗi lượt nên không cần reconstruct. Ghi rõ để
        # observability/UI biết live apply không phải một cái nút trang trí.
        if any(path.startswith("chat.") or path.startswith("personality.") for path in applied_paths):
            meta["live_refreshed"].append("conversation_policy")
        if any(path.startswith("prompt.") for path in applied_paths):
            meta["live_refreshed"].append("prompt_budget_policy")
        if any(path.startswith("capabilities.") for path in applied_paths):
            try:
                from actions.capability_runtime import refresh_runtime_capabilities

                refresh_runtime_capabilities(owner=self, settings=self.runtime_settings)
                meta["live_refreshed"].append("capability_runtime_snapshot")
            except Exception as exc:
                self.logger.debug("[capability] live refresh failed: %s", exc)
        if any(path.startswith("tutor.") for path in applied_paths):
            meta["live_refreshed"].append("tutor_search_policy")
        if any(path.startswith("commentary.") or path.startswith("initiative.") or path.startswith("discovery.") for path in applied_paths):
            meta["live_refreshed"].append("autonomous_presence_policy")
        if any(path.startswith("memory.") for path in applied_paths):
            meta["live_refreshed"].append("memory_policy")
        if any(path.startswith("project_ops.") for path in applied_paths):
            meta["live_refreshed"].append("project_operations_policy")
        if any(path.startswith(("public_turn.", "engagement.", "health.", "ai_runtime.", "protocol.", "run_loop.")) for path in applied_paths):
            from runtime.policy_settings import RuntimePolicySettings

            self.runtime_policy_settings = RuntimePolicySettings.load()
            if getattr(self, "public_turn_runtime", None) is not None:
                self.public_turn_runtime.settings = self.runtime_policy_settings.public_turn
            if getattr(self, "incoming_dispatcher", None) is not None:
                self.incoming_dispatcher.settings = self.runtime_policy_settings.protocol
            if getattr(self, "loop_guard", None) is not None:
                self.loop_guard.settings = self.runtime_policy_settings.run_loop
            meta["live_refreshed"].append("typed_runtime_policy")
        return meta


__all__ = ["SettingsControlRuntimeMixin"]
