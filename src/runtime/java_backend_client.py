# -*- coding: utf-8 -*-
"""Thin Strangler-Fig HTTP adapter connecting ErisTutor Desktop to the Java 27 Backend.

Uses Python standard library only so Desktop/foundation imports never break.
When ``ERIS_JAVA_BACKEND_URL`` is unset or unreachable, every call falls back
smoothly to the local Python LearningService / DockerJavaWorkspace.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import os
from typing import Any
import urllib.error
import urllib.request
import uuid


@dataclass(frozen=True, slots=True)
class JavaBackendConfig:
    base_url: str = ""
    desktop_key: str = "eris-desktop-local-key"
    learning_mode: str = "off"  # off | mirror
    judge_enabled: bool = False
    timeout_seconds: float = 3.0

    @classmethod
    def from_env(cls) -> "JavaBackendConfig":
        base_url = str(os.getenv("ERIS_JAVA_BACKEND_URL", "") or "").strip().rstrip("/")
        desktop_key = str(os.getenv("ERIS_JAVA_DESKTOP_KEY", "eris-desktop-local-key") or "eris-desktop-local-key").strip()
        mode = str(os.getenv("ERIS_JAVA_LEARNING_MODE", "off") or "off").strip().lower()
        if mode not in {"off", "mirror"}:
            mode = "off"
        raw_judge = os.getenv("ERIS_JAVA_JUDGE_ENABLED")
        if raw_judge is not None and str(raw_judge).strip():
            judge_enabled = str(raw_judge).strip().lower() in {"1", "true", "yes", "on"}
        else:
            judge_enabled = bool(base_url)
        try:
            timeout = max(0.5, min(30.0, float(os.getenv("ERIS_JAVA_BACKEND_TIMEOUT_S", "3.0") or "3.0")))
        except ValueError:
            timeout = 3.0
        return cls(
            base_url=base_url,
            desktop_key=desktop_key,
            learning_mode=mode,
            judge_enabled=judge_enabled and bool(base_url),
            timeout_seconds=timeout,
        )

    @property
    def enabled(self) -> bool:
        return bool(self.base_url)


class JavaBackendClient:
    """Minimal stdlib HTTP client with ``X-Eris-Desktop-Key`` authentication."""

    def __init__(self, config: JavaBackendConfig | None = None) -> None:
        self.config = config or JavaBackendConfig.from_env()

    def _request(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
        *,
        timeout: float | None = None,
    ) -> tuple[int, dict[str, Any]]:
        url = f"{self.config.base_url}{path}"
        headers = {
            "Accept": "application/json",
            "X-Eris-Desktop-Key": self.config.desktop_key,
            "X-Eris-User-Id": "desktop-eris",
        }
        data: bytes | None = None
        if payload is not None:
            headers["Content-Type"] = "application/json"
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(url, data=data, headers=headers, method=method.upper())
        effective_timeout = timeout if timeout is not None else self.config.timeout_seconds
        try:
            with urllib.request.urlopen(req, timeout=effective_timeout) as resp:
                raw = resp.read().decode("utf-8", errors="replace")
                parsed = json.loads(raw) if raw.strip() else {}
                return int(resp.status), parsed if isinstance(parsed, dict) else {"data": parsed}
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8", errors="replace")
            try:
                parsed = json.loads(raw) if raw.strip() else {}
            except ValueError:
                parsed = {"detail": raw}
            return int(exc.code), parsed if isinstance(parsed, dict) else {"data": parsed}

    def health(self) -> bool:
        if not self.config.enabled:
            return False
        try:
            status, body = self._request("GET", "/actuator/health", timeout=1.5)
            return status == 200 and body.get("status") == "UP"
        except Exception:
            return False

    def judge_readiness(self) -> dict[str, Any] | None:
        if not self.config.enabled:
            return None
        try:
            status, body = self._request("GET", "/api/v1/judge/java/readiness", timeout=3.0)
            if status == 200 and "available" in body:
                return body
        except Exception:
            pass
        return None

    def execute_java_exercise(
        self,
        code: str,
        exercise: dict[str, Any],
        *,
        request_id: str | None = None,
        timeout: float = 15.0,
    ) -> dict[str, Any] | None:
        if not self.config.enabled:
            return None
        req_id = request_id or f"desktop-{uuid.uuid4().hex}"
        wait_s = max(1, min(25, int(round(timeout)) + 2))
        payload = {
            "requestId": req_id,
            "packId": "inline",
            "nodeId": str(exercise.get("node_id") or exercise.get("id") or "desktop-node"),
            "exerciseId": str(exercise.get("id") or "desktop-exercise"),
            "code": code,
            "timeoutSeconds": timeout,
            "commitToLearning": False,
            "inlineExercise": {
                "className": str(exercise.get("class_name") or "OrderService"),
                "harness": str(exercise.get("harness") or ""),
                "tests": [
                    {"id": str(t.get("id") or ""), "description": str(t.get("description") or "")}
                    for t in exercise.get("tests", ())
                    if isinstance(t, dict)
                ],
            },
        }
        try:
            status, body = self._request(
                "POST",
                f"/api/v1/judge/java/submissions?waitSeconds={wait_s}",
                payload,
                timeout=wait_s + 3.0,
            )
            if status in (200, 202) and isinstance(body, dict) and body.get("status") in {"completed", "unavailable"}:
                return {
                    "status": body.get("status"),
                    "result": body.get("result", ""),
                    "tests": body.get("tests", []),
                    "reason": body.get("reason", ""),
                    "diagnostic": body.get("diagnostic", ""),
                    "duration_ms": body.get("durationMs", 0),
                    "exit_code": body.get("exitCode", 0),
                    "guidance": body.get("guidance", ""),
                }
        except Exception:
            pass
        return None

    def sync_path(self, path_obj: Any) -> bool:
        if not self.config.enabled:
            return False
        try:
            kps = [
                {
                    "kpId": kp.kp_id,
                    "title": kp.title,
                    "kpType": kp.kp_type,
                    "prerequisites": list(kp.prerequisites),
                }
                for kp in getattr(path_obj, "knowledge_points", ())
            ]
            payload = {
                "pathId": path_obj.path_id,
                "title": path_obj.title,
                "goal": path_obj.goal,
                "domainId": getattr(path_obj, "domain_id", "") or "",
                "source": getattr(path_obj, "source", "user") or "user",
                "knowledgePoints": kps,
            }
            status, _ = self._request("POST", "/api/v1/learning/paths", payload)
            return status == 200
        except Exception:
            return False

    def record_attempt(self, attempt: Any) -> bool:
        if not self.config.enabled:
            return False
        try:
            payload = {
                "attemptId": attempt.attempt_id,
                "kpId": attempt.kp_id,
                "result": attempt.result,
                "evidenceType": attempt.evidence_type,
                "strength": attempt.strength,
                "interactionId": attempt.interaction_id,
                "turnId": attempt.turn_id,
                "pathId": attempt.path_id,
                "itemId": attempt.item_id,
                "itemFamilyId": attempt.item_family_id,
                "activityId": attempt.activity_id,
                "assistance": attempt.assistance,
                "hintCount": attempt.hint_count,
                "answerRevealed": attempt.answer_revealed,
                "reviewRecall": attempt.review_recall,
                "rating": attempt.rating,
                "errorCode": attempt.error_code,
                "recordedAt": attempt.recorded_at or None,
            }
            status, _ = self._request("POST", "/api/v1/learning/attempts", payload)
            return status in (200, 409)
        except Exception:
            return False

    def correct_attempt(self, attempt_id: str, *, correction_id: str, result: str) -> bool:
        if not self.config.enabled:
            return False
        try:
            status, _ = self._request(
                "POST",
                f"/api/v1/learning/attempts/{attempt_id}/correct",
                {"correctionId": correction_id, "result": result},
            )
            return status == 200
        except Exception:
            return False

    def forget_attempt(self, attempt_id: str) -> bool:
        if not self.config.enabled:
            return False
        try:
            status, _ = self._request("DELETE", f"/api/v1/learning/attempts/{attempt_id}")
            return status in (200, 404)
        except Exception:
            return False


class RemoteJavaJudgeWorkspace:
    """Drop-in replacement for DockerJavaWorkspace delegating to Java Backend with local fallback."""

    def __init__(self, fallback_workspace: Any, client: JavaBackendClient | None = None) -> None:
        self._fallback = fallback_workspace
        self._client = client or JavaBackendClient()

    def available(self) -> bool:
        return self._client.health() or self._fallback.available()

    def readiness(self) -> dict[str, Any]:
        remote = self._client.judge_readiness()
        if remote is not None:
            return remote
        return self._fallback.readiness()

    def execute(self, code: str, exercise: dict[str, Any], *, timeout: float = 15.0) -> dict[str, Any]:
        remote = self._client.execute_java_exercise(code, exercise, timeout=timeout)
        if remote is not None:
            return remote
        return self._fallback.execute(code, exercise, timeout=timeout)


class JavaLearningMirrorProxy:
    """Strangler proxy preserving local LearningService while mirroring mutations to Java."""

    def __init__(
        self,
        local_service: Any,
        client: JavaBackendClient | None = None,
        logger: Any = None,
    ) -> None:
        self._local = local_service
        self._client = client or JavaBackendClient()
        self._logger = logger

    def register_path(self, path: Any, *, now: float | None = None) -> None:
        self._local.register_path(path, now=now)
        self._best_effort("sync_path", lambda: self._client.sync_path(path))

    def record_attempt(self, attempt: Any) -> bool:
        recorded = self._local.record_attempt(attempt)
        if recorded:
            self._best_effort("record_attempt", lambda: self._client.record_attempt(attempt))
        return recorded

    def correct_attempt(self, attempt_id: str, *, correction_id: str, result: str = "ungraded", now: float | None = None):
        out = self._local.correct_attempt(attempt_id, correction_id=correction_id, result=result, now=now)
        self._best_effort(
            "correct_attempt",
            lambda: self._client.correct_attempt(attempt_id, correction_id=correction_id, result=result),
        )
        return out

    def forget_attempt(self, attempt_id: str, *, now: float | None = None) -> bool:
        removed = self._local.forget_attempt(attempt_id, now=now)
        if removed:
            self._best_effort("forget_attempt", lambda: self._client.forget_attempt(attempt_id))
        return removed

    def _best_effort(self, op: str, fn: Any) -> None:
        try:
            fn()
        except Exception as exc:
            debug = getattr(self._logger, "debug", None)
            if callable(debug):
                debug("[java-mirror] %s skipped: %s", op, exc)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._local, name)
