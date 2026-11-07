# -*- coding: utf-8 -*-
"""Public-turn trace JSONL dùng chung cho chat, autonomous và TTS.

V97 hợp nhất trace public về một schema. Tên module ``autonomous_trace`` được
 giữ để import cũ không gãy, nhưng source of truth mới là ``PublicTurnTraceStore``.
Trace chỉ chứa provenance/decision metadata public-safe; tuyệt đối không ghi raw
prompt, OCR text, private mind hay nội dung câu trả lời.
"""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any, Mapping

from runtime.workspace_manager import default_eris_home


PUBLIC_TRACE_SCHEMA_V97 = "eris.public_turn.trace.v97"
NERVOUS_TRACE_SCHEMA = "eris.public_turn.trace.v98_nervous"
NERVOUS_COMPACT_SCHEMA = "eris.public_turn.compact.v103_nervous"


class PublicTurnTraceStore:
    """Store thống nhất cho vòng đời một public turn.

    Một ``trace_id`` đi xuyên input/candidate/authority/public_commit/TTS/outcome.
    ``compact_trace`` dựng một dòng dễ đọc để debug mà không phải ghép 20 log line.
    """

    SCHEMA = PUBLIC_TRACE_SCHEMA_V97

    def __init__(self, *, path: Path | None = None, logger: Any | None = None) -> None:
        # Giữ filename cũ để upgrade không làm mất lịch sử/debug tooling.
        self.path = path or (default_eris_home() / "logs" / "autonomous_trace.jsonl")
        self.logger = logger
        self._lock = threading.RLock()

    def append(self, event: str, payload: Mapping[str, Any] | None = None, *, now: float | None = None) -> None:
        clean_payload = self._sanitize(payload or {})
        event_name = str(event or "unknown")[:80]
        # Event nervous dùng schema mới đúng roadmap nhưng trace V97 cũ vẫn đọc
        # nguyên vẹn. Như vậy migration không phá tooling đang parse record legacy.
        schema = NERVOUS_TRACE_SCHEMA if (
            event_name == "nervous_state"
            or bool(clean_payload.get("nervous_schema"))
            or bool(clean_payload.get("field_snapshot"))
        ) else self.SCHEMA
        record = {
            "schema": schema,
            "at": float(time.time() if now is None else now),
            "event": event_name,
            **clean_payload,
        }
        try:
            with self._lock:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                with self.path.open("a", encoding="utf-8") as handle:
                    handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True, default=str) + "\n")
            if self._is_terminal(record):
                self._emit_compact_terminal(str(record.get("trace_id") or ""))
        except Exception as exc:
            if self.logger is not None:
                try:
                    self.logger.debug("[public trace v97] write failed: %s", exc)
                except Exception:
                    pass

    def recent(self, *, limit: int = 80, trace_id: str = "") -> list[dict[str, Any]]:
        try:
            lines = self.path.read_text(encoding="utf-8").splitlines()[-max(1, int(limit) * 4):]
        except Exception:
            return []
        output: list[dict[str, Any]] = []
        wanted = str(trace_id or "").strip()
        for line in lines:
            try:
                item = json.loads(line)
            except Exception:
                continue
            if not isinstance(item, dict):
                continue
            if wanted and str(item.get("trace_id") or "") != wanted:
                continue
            output.append(item)
        return output[-max(1, int(limit)):]

    def compact_trace(self, trace_id: str) -> dict[str, Any]:
        """Nén trace thành một record causal dễ đọc, không suy diễn từ prose."""
        wanted = str(trace_id or "").strip()
        result: dict[str, Any] = {
            "schema": "eris.public_turn.compact.v97",
            "trace_id": wanted,
            "channel": "",
            "turn_id": "",
            "semantic_owner": "unknown",
            "trigger_owner": "unknown",
            "source_kind": "",
            "goal_state": "",
            "proposal_source": "",
            "proposal_kind": "",
            "authority_action": "",
            "authority_reason": "",
            "motive": "",
            "speech_act": "",
            "public_committed": False,
            "tts_status": "not_started",
            "outcome": "pending",
            # Nervous causal projection: public-safe, numeric, không chain-of-thought.
            "field_snapshot": {},
            "top_inputs": [],
            "motor": {},
            "commit": "",
            "motor_reason": "",
            "motor_committed": False,
            "nervous_owner": "",
            "nervous_mode": "",
            "nervous_scope": "",
            "surface": "",
        }
        for item in self.recent(limit=160, trace_id=wanted):
            # Chuẩn hóa vocabulary legacy ngay lúc đọc trace. Production V97
            # ghi tên mới, nhưng event V96 trong cùng file vẫn debug được.
            if item.get("candidate_source") and not item.get("proposal_source"):
                item["proposal_source"] = item.get("candidate_source")
            if item.get("candidate_kind") and not item.get("proposal_kind"):
                item["proposal_kind"] = item.get("candidate_kind")
            if item.get("governor_action") and not item.get("authority_action"):
                item["authority_action"] = item.get("governor_action")
            if item.get("event") in {"initiative_decision", "executive_decision"} and item.get("reason") and not item.get("authority_reason"):
                item["authority_reason"] = item.get("reason")
            for key in (
                "channel", "turn_id", "semantic_owner", "trigger_owner", "source_kind",
                "goal_state", "proposal_source", "proposal_kind", "authority_action",
                "authority_reason", "motive", "speech_act",
            ):
                value = item.get(key)
                if value not in (None, ""):
                    result[key] = value
            if str(item.get("schema") or "") == NERVOUS_TRACE_SCHEMA or item.get("field_snapshot"):
                result["schema"] = NERVOUS_COMPACT_SCHEMA
                for key in (
                    "field_snapshot", "top_inputs", "motor", "commit", "motor_reason",
                    "motor_committed", "nervous_owner", "nervous_mode", "nervous_scope",
                ):
                    value = item.get(key)
                    if value not in (None, "", {}, []):
                        result[key] = value
            # Surface/TTS là event downstream nên vẫn mang schema V97 compatibility;
            # nếu trace đã có nervous_state thì nối public lexeme vào cùng compact record.
            if item.get("surface") not in (None, ""):
                result["surface"] = item.get("surface")
            if bool(item.get("public_committed", False)):
                result["public_committed"] = True
            tts_event = str(item.get("tts_event") or "").lower()
            if tts_event == "audio_start":
                result["tts_status"] = "started"
            elif tts_event == "audio_done":
                result["tts_status"] = "done"
            elif tts_event in {"audio_failed", "audio_localization_failed"}:
                result["tts_status"] = "failed"
            elif tts_event == "audio_cancelled":
                result["tts_status"] = "cancelled"
            outcome = str(item.get("outcome") or "").strip().lower()
            if outcome:
                result["outcome"] = outcome
        return result

    @staticmethod
    def _is_terminal(record: Mapping[str, Any]) -> bool:
        if str(record.get("event") or "") == "outcome":
            return str(record.get("outcome") or "").lower() in {
                "delivered", "failed", "cancelled", "cancelled_before_commit"
            }
        return False

    def _emit_compact_terminal(self, trace_id: str) -> None:
        if not trace_id:
            return
        compact = self.compact_trace(trace_id)
        if self.logger is not None:
            try:
                self.logger.info(
                    "[public trace] trace=%s channel=%s semantic=%s trigger=%s goal=%s proposal=%s/%s authority=%s nervous=%s commit=%s tts=%s outcome=%s",
                    compact.get("trace_id", ""), compact.get("channel", ""),
                    compact.get("semantic_owner", "unknown"), compact.get("trigger_owner", "unknown"),
                    compact.get("goal_state", ""), compact.get("proposal_source", ""),
                    compact.get("proposal_kind", ""), compact.get("authority_action", ""),
                    compact.get("commit", ""), int(bool(compact.get("public_committed"))),
                    compact.get("tts_status", ""), compact.get("outcome", ""),
                )
            except Exception:
                pass
        try:
            from runtime.observability import GLOBAL_OBSERVABILITY
            GLOBAL_OBSERVABILITY.log(
                "public_turn_trace",
                tags={"channel": compact.get("channel", "") or "unknown", "outcome": compact.get("outcome", "pending")},
                fields=compact,
                status="ok" if compact.get("outcome") == "delivered" else str(compact.get("outcome") or "pending"),
            )
        except Exception:
            pass

    def _sanitize(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        # V97: allow-list field public-safe duy nhất. Nếu field không ở đây thì
        # nó không được ghi. Cách này an toàn hơn blacklist raw prompt/text.
        allowed = {
            "turn_seq", "semantic_signature", "turn_kind", "speech_act", "importance",
            "intrusion_risk", "confidence", "screen_delta", "activity_session_id",
            "mechanical_event_keys", "discourse_mode", "reply_expected", "initiative_reason",
            "trigger_reason", "has_public_seed", "result", "body_only", "evidence_count",
            "reason", "activity_kind", "motion_hint", "trace_id", "stage", "owner",
            "candidate_source", "candidate_kind", "governor_action", "surface_status",
            "tts_event", "outcome", "turn_id", "generation_epoch", "claim_token",
            "idempotency_key", "anchor", "action", "speech_intent_owner",
            # V97 causal/authority vocabulary dùng chung cho chat + autonomous.
            "channel", "semantic_owner", "trigger_owner", "source_kind", "source_ref",
            "goal_state", "goal_owner", "proposal_source", "proposal_kind",
            "proposal_id", "authority_action", "authority_reason", "authority_owner",
            "motive", "why_now", "user_triggered", "public_committed", "provider_state",
            # V103 FINAL2 nervous trace. Chỉ metadata số học + local public lexeme;
            # không cho raw prompt/private material đi qua allow-list.
            "nervous_schema", "field_snapshot", "top_inputs", "motor", "commit",
            "motor_reason", "motor_committed", "nervous_owner", "nervous_mode",
            "nervous_scope", "surface", "same_text_tts", "source_revision", "trace_at",
        }
        clean: dict[str, Any] = {}
        for key, value in payload.items():
            if key not in allowed:
                continue
            if key in {"field_snapshot", "motor"} and isinstance(value, Mapping):
                clean[key] = {
                    str(k)[:48]: round(float(v), 6)
                    for k, v in list(value.items())[:16]
                    if isinstance(v, (int, float))
                }
                continue
            if key == "top_inputs" and isinstance(value, (list, tuple)):
                rows = []
                for item in list(value)[:8]:
                    if not isinstance(item, (list, tuple)) or len(item) < 3:
                        continue
                    try:
                        rows.append([str(item[0])[:96], str(item[1])[:48], round(float(item[2]), 6)])
                    except (TypeError, ValueError):
                        continue
                clean[key] = rows
                continue
            if isinstance(value, str):
                limit = 96 if key == "surface" else 360
                clean[key] = value[:limit]
            elif isinstance(value, (int, float, bool)) or value is None:
                clean[key] = value
            elif isinstance(value, (list, tuple)):
                clean[key] = [str(item)[:80] for item in value[:8]]
            elif isinstance(value, Mapping):
                clean[key] = {
                    str(k)[:48]: str(v)[:120]
                    for k, v in list(value.items())[:12]
                    if isinstance(v, (str, int, float, bool)) or v is None
                }
        return clean


# Compatibility aliases: tooling/test cũ vẫn import được nhưng production V97
# nên dùng tên public-turn để tránh ngộ nhận trace chỉ thuộc autonomous lane.
AutonomousTraceStore = PublicTurnTraceStore

_GLOBAL_TRACE_STORE: PublicTurnTraceStore | None = None
_GLOBAL_TRACE_LOCK = threading.RLock()


def public_turn_trace_store(*, logger: Any | None = None) -> PublicTurnTraceStore:
    global _GLOBAL_TRACE_STORE
    with _GLOBAL_TRACE_LOCK:
        if _GLOBAL_TRACE_STORE is None:
            _GLOBAL_TRACE_STORE = PublicTurnTraceStore(logger=logger)
        elif logger is not None and _GLOBAL_TRACE_STORE.logger is None:
            _GLOBAL_TRACE_STORE.logger = logger
        return _GLOBAL_TRACE_STORE


def autonomous_trace_store(*, logger: Any | None = None) -> PublicTurnTraceStore:
    """Compatibility wrapper V4/V96; không còn là store riêng."""
    return public_turn_trace_store(logger=logger)


def append_public_turn_trace(event: str, payload: Mapping[str, Any] | None = None, *, logger: Any | None = None) -> None:
    try:
        from runtime.architecture_flags import event_trace_enabled
        if not event_trace_enabled():
            return
    except Exception:
        pass
    public_turn_trace_store(logger=logger).append(event, payload)


def append_autonomous_trace(event: str, payload: Mapping[str, Any] | None = None, *, logger: Any | None = None) -> None:
    """Compatibility wrapper; production mới dùng ``append_public_turn_trace``."""
    append_public_turn_trace(event, payload, logger=logger)


__all__ = [
    "AutonomousTraceStore",
    "PublicTurnTraceStore",
    "PUBLIC_TRACE_SCHEMA_V97",
    "NERVOUS_TRACE_SCHEMA",
    "NERVOUS_COMPACT_SCHEMA",
    "append_autonomous_trace",
    "append_public_turn_trace",
    "autonomous_trace_store",
    "public_turn_trace_store",
]
