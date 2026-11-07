# -*- coding: utf-8 -*-
"""Extracted coordinator runtime boundary.

The methods remain owner-oriented for backward compatibility while the main
coordinator becomes a small composition surface. New behavior should prefer
typed services over adding more methods to these compatibility mixins.
"""
from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import random
import re
import threading
import time
import traceback
import unicodedata
from collections.abc import Mapping
from types import SimpleNamespace




class CoordinatorShutdownMixin:
    def stop(self):
        repair_close = getattr(getattr(self, "ai_model_router", None), "close_contract_repair", None)
        if callable(repair_close):
            repair_close()
        self.running = False
        canvas_inbox = getattr(self, "canvas_control_inbox", None)
        if canvas_inbox is not None:
            canvas_inbox.stop()
        canvas_service = getattr(self, "learning_canvas_service", None)
        if canvas_service is not None:
            canvas_service.close()
        # P2: ngắt quyết định goal nền trước khi tháo Subject/Work Session.
        goal_driver = getattr(self, "_identity_goal_driver", None)
        if goal_driver is not None:
            try:
                goal_driver.close()
            except Exception as exc:
                self.logger.debug("[identity-goal] stop error: %s", exc)
        # IDE gateway giữ socket/thread riêng. Dừng nó trước khi tháo runtime để
        # client không nhận thêm event trong lúc các subsystem đang shutdown.
        agent_ws = getattr(self, "agent_ws_service", None)
        if agent_ws is not None:
            try:
                agent_ws.stop()
            except Exception as exc:
                self.logger.debug("[agent-ws] stop error: %s", exc)
        observer_link = getattr(self, "eris_code_observer_link", None)
        if observer_link is not None:
            try:
                observer_link.stop()
            except Exception as exc:
                self.logger.debug("[eris-link] stop error: %s", exc)
        agent_harness = getattr(self, "agent_harness", None)
        if agent_harness is not None:
            try:
                agent_harness.close()
            except Exception as exc:
                self.logger.debug("[agent-harness] close error: %s", exc)
        worker = getattr(self, "screen_text_worker", None)
        if worker is not None:
            try:
                worker.stop()
            except Exception as exc:
                self.logger.warning("[screen text] stop error: %s", exc)
        vision_worker = getattr(self, "vision_perception_worker", None)
        if vision_worker is not None:
            try:
                vision_worker.stop()
            except Exception as exc:
                self.logger.debug("[vision perception] stop error: %s", exc)
        # Dừng producer native trước, rồi mới dừng semantic consumer. Như vậy
        # shutdown không tạo thêm event sau khi worker đã ngừng nhận queue.
        native_broker = getattr(self, "native_environment_event_broker", None)
        if native_broker is not None:
            try:
                native_broker.stop()
            except Exception as exc:
                self.logger.debug("[deep environment] WinEvent stop error: %s", exc)
        for monitor in tuple(getattr(self, "native_environment_monitors", ()) or ()):
            try:
                monitor.stop()
            except Exception as exc:
                self.logger.debug("[deep environment] monitor stop error: %s", exc)
        semantic_worker = getattr(self, "semantic_environment_worker", None)
        if semantic_worker is not None:
            try:
                semantic_worker.stop()
            except Exception as exc:
                self.logger.debug("[deep environment] semantic worker stop error: %s", exc)
        self._stop_mental_process_loop()
        # V99 đọc activation + concern/goal/user-model nên phải dừng trước V98 và
        # trước khi các cognition service bị tháo.
        focus_runtime = getattr(self, "motivational_focus_runtime_v99", None)
        if focus_runtime is not None:
            try:
                focus_runtime.stop()
            except Exception as exc:
                self.logger.debug("[v99 focus] stop error: %s", exc)
        # V98 activation có thread local riêng; dừng trước các cognition service để
        # bridge không đọc state đang bị tháo trong lúc shutdown.
        activation_runtime = getattr(self, "continuous_dynamics_runtime_v98", None)
        if activation_runtime is not None:
            try:
                activation_runtime.stop()
            except Exception as exc:
                self.logger.debug("[v98 activation] stop error: %s", exc)
        for background_name in ("background_memory_digest",):
            background_service = getattr(self, background_name, None)
            if background_service is not None:
                try:
                    background_service.stop()
                except Exception as exc:
                    self.logger.debug("[%s] stop error: %s", background_name, exc)
        supervisor = getattr(self, "subsystem_supervisor", None)
        if supervisor is not None:
            supervisor.stop("runtime.tutor")
        if self.subconscious_runtime_enabled:
            try:
                snapshot = self.telemetry_service.capture_snapshot(
                    mood_score=self.mood_score,
                    inferred_pos_x=self.inferred_pos_x,
                )
                self._maybe_tick_subconscious_runtime(
                    channel="reflection",
                    snapshot=snapshot,
                    internal_state=self._get_internal_state(),
                    user_text="",
                    event_text="shutdown_reflection",
                    event_strength=0.22,
                    allow_lane5=True,
                    reflection=True,
                )
            except Exception as exc:
                self.logger.warning("Subconscious reflection tick error: %s", exc)
        self.cognitive_substrate.stop()
        runtime = getattr(self, "subconscious_runtime", None)
        if runtime is not None:
            try:
                runtime.stop()
            except Exception:
                pass
        try:
            from runtime.ai_orchestrated.bootstrap import shutdown_ai_orchestrated_runtime
            shutdown_ai_orchestrated_runtime(self)
        except Exception:
            pass
        if supervisor is not None:
            supervisor.shutdown_all()
        health_registry = getattr(self, "subsystem_health_registry", None)
        if health_registry is not None:
            health_registry.mark_stopped("runtime.coordinator")
        self.transport.close()
