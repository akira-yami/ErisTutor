# -*- coding: utf-8 -*-
"""Hệ quan sát hành vi cho ERIS.

Định nghĩa repo mới:
- Model chính sở hữu semantic/public voice ở lần sinh Decision đầu tiên.
- Các lớp cũ mang tên guard/critic/authority chỉ được quan sát và ghi hậu quả.
- Tín hiệu được đưa vào *lượt sau* như context mềm; tuyệt đối không rewrite câu
  public vừa sinh.
- Nói dối có chủ đích đi vào DeceptionLedger; hallucination/overclaim chỉ là
  observation cho tới khi có ground truth và chủ ý rõ.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from threading import RLock
from typing import Any, Iterable, Mapping

SCHEMA_VERSION = 2


def _clean(value: Any, limit: int = 420) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _clamp01(value: Any, default: float = 0.0) -> float:
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return default


def _env_flag(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return str(raw).strip().casefold() in {"1", "true", "yes", "on", "y"}


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, "") or default)
    except Exception:
        return float(default)


@dataclass(frozen=True, slots=True)
class BehaviorSignal:
    family: str
    source: str
    confidence: float = 0.70
    severity: float = 0.35
    evidence: tuple[str, ...] = ()
    consequence: str = ""
    next_turn_hint: str = ""
    public_text: str = ""
    turn_id: str = ""
    channel: str = ""
    created_at: float = field(default_factory=time.time)
    schema_version: int = SCHEMA_VERSION

    def __post_init__(self) -> None:
        object.__setattr__(self, "family", _clean(self.family, 80) or "unknown")
        object.__setattr__(self, "source", _clean(self.source, 100) or "unknown")
        object.__setattr__(self, "confidence", _clamp01(self.confidence, 0.70))
        object.__setattr__(self, "severity", _clamp01(self.severity, 0.35))
        object.__setattr__(self, "evidence", tuple(_clean(x, 180) for x in self.evidence if _clean(x, 180))[:8])
        object.__setattr__(self, "consequence", _clean(self.consequence, 260))
        object.__setattr__(self, "next_turn_hint", _clean(self.next_turn_hint, 300))
        object.__setattr__(self, "public_text", _clean(self.public_text, 360))
        object.__setattr__(self, "turn_id", _clean(self.turn_id, 100))
        object.__setattr__(self, "channel", _clean(self.channel, 40))

    @property
    def signature(self) -> str:
        return "|".join((self.family, self.source, self.public_text.casefold(), self.turn_id))

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["evidence"] = list(self.evidence)
        return data

    def to_persisted_dict(self, *, include_public_text: bool = False) -> dict[str, Any]:
        """Bản ghi bền không nhân bản public prose vào vùng cognition-adjacent.

        Raw public text vẫn tồn tại ở transcript/audit đúng chỗ của nó. Behavior journal
        chỉ cần hậu quả + hint + hash để dedupe/forensic. Nhét nguyên câu lỗi vào đây rồi
        feed lại prompt là cách rất hiệu quả để dạy model lặp lại chính lỗi của mình.
        """
        data = self.to_dict()
        text = str(data.get("public_text") or "")
        data["public_text_hash"] = hashlib.sha1(text.encode("utf-8")).hexdigest()[:16] if text else ""
        if not include_public_text:
            data["public_text"] = ""
        data["prompt_eligible"] = False
        data["source_class"] = "audit_runtime"
        return data

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> "BehaviorSignal":
        return cls(
            family=str(data.get("family") or "unknown"),
            source=str(data.get("source") or "unknown"),
            confidence=data.get("confidence", 0.70),
            severity=data.get("severity", 0.35),
            evidence=tuple(data.get("evidence") or ()),
            consequence=str(data.get("consequence") or ""),
            next_turn_hint=str(data.get("next_turn_hint") or ""),
            public_text=str(data.get("public_text") or ""),
            turn_id=str(data.get("turn_id") or ""),
            channel=str(data.get("channel") or ""),
            created_at=float(data.get("created_at") or time.time()),
            schema_version=int(data.get("schema_version") or SCHEMA_VERSION),
        )


def signal_to_meta(signal: BehaviorSignal) -> dict[str, Any]:
    return signal.to_dict()


def attach_behavior_signal(decision: Any, signal: BehaviorSignal) -> Any:
    """Gắn observation vào Decision, không thay text/should_speak/action."""
    if decision is None:
        return decision
    current = getattr(decision, "behavior_observation_meta", None)
    meta = dict(current) if isinstance(current, dict) else {}
    items = [dict(x) for x in meta.get("signals", ()) if isinstance(x, Mapping)]
    signatures = {
        BehaviorSignal.from_mapping(item).signature
        for item in items
        if isinstance(item, Mapping)
    }
    if signal.signature not in signatures:
        items.append(signal_to_meta(signal))
    meta.update({
        "schema_version": SCHEMA_VERSION,
        "policy": "observe_now_influence_next_turn_never_rewrite_final",
        "signals": items[-16:],
    })
    try:
        decision.behavior_observation_meta = meta
    except Exception:
        pass
    return decision


class BehaviorObservationJournal:
    """Ring journal bền vững cho hậu quả hành vi, không phải bộ lọc câu chữ."""

    def __init__(self, path: str | os.PathLike[str] | None = None, *, max_records: int = 160, ttl_seconds: float | None = None, logger: Any = None) -> None:
        default = os.environ.get("ERIS_BEHAVIOR_OBSERVATION_PATH", "").strip()
        self.path = Path(path or default or "memory_core/behavior_observations.jsonl")
        self.max_records = max(32, int(max_records))
        self.ttl_seconds = max(3600.0, float(ttl_seconds if ttl_seconds is not None else _env_float("ERIS_BEHAVIOR_OBSERVATION_TTL_SEC", 72 * 3600.0)))
        self.store_public_text = _env_flag("ERIS_BEHAVIOR_STORE_PUBLIC_TEXT", False)
        self.logger = logger
        self._lock = RLock()
        self._records: list[BehaviorSignal] = []
        self._signatures: set[str] = set()
        self._load()

    def _load(self) -> None:
        try:
            if not self.path.exists():
                return
            lines = self.path.read_text(encoding="utf-8").splitlines()[-self.max_records * 2 :]
            cutoff = time.time() - self.ttl_seconds
            for line in lines:
                if not line.strip():
                    continue
                signal = BehaviorSignal.from_mapping(json.loads(line))
                if signal.created_at < cutoff:
                    continue
                # V41: legacy rows có public_text được sanitize ngay khi load vào journal.
                if signal.public_text and not self.store_public_text:
                    signal = BehaviorSignal(
                        family=signal.family, source=signal.source, confidence=signal.confidence,
                        severity=signal.severity, evidence=signal.evidence, consequence=signal.consequence,
                        next_turn_hint=signal.next_turn_hint, public_text="", turn_id=signal.turn_id,
                        channel=signal.channel, created_at=signal.created_at, schema_version=SCHEMA_VERSION,
                    )
                if signal.signature not in self._signatures:
                    self._records.append(signal)
                    self._signatures.add(signal.signature)
            self._records = self._records[-self.max_records :]
        except Exception as exc:
            if self.logger is not None:
                self.logger.warning("[behavior-observation] load failed: %s", exc)

    def commit(self, signals: Iterable[BehaviorSignal]) -> tuple[BehaviorSignal, ...]:
        added: list[BehaviorSignal] = []
        with self._lock:
            for signal in signals:
                if not isinstance(signal, BehaviorSignal) or signal.signature in self._signatures:
                    continue
                self._records.append(signal)
                self._signatures.add(signal.signature)
                added.append(signal)
            if not added:
                return ()
            cutoff = time.time() - self.ttl_seconds
            self._records = [item for item in self._records if item.created_at >= cutoff][-self.max_records :]
            self._signatures = {item.signature for item in self._records}
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                temp = self.path.with_suffix(self.path.suffix + ".tmp")
                temp.write_text(
                    "\n".join(json.dumps(item.to_persisted_dict(include_public_text=self.store_public_text), ensure_ascii=False) for item in self._records) + "\n",
                    encoding="utf-8",
                )
                temp.replace(self.path)
            except Exception as exc:
                if self.logger is not None:
                    self.logger.warning("[behavior-observation] persist failed: %s", exc)
        return tuple(added)

    def recent(self, limit: int = 8) -> tuple[BehaviorSignal, ...]:
        with self._lock:
            cutoff = time.time() - self.ttl_seconds
            self._records = [item for item in self._records if item.created_at >= cutoff][-self.max_records :]
            self._signatures = {item.signature for item in self._records}
            return tuple(self._records[-max(1, int(limit)) :])

    def compact(self) -> dict[str, int]:
        """TTL/prune + rewrite bền, dùng cho DeepSeek cleanup/maintenance."""
        with self._lock:
            before = len(self._records)
            cutoff = time.time() - self.ttl_seconds
            self._records = [item for item in self._records if item.created_at >= cutoff][-self.max_records :]
            self._signatures = {item.signature for item in self._records}
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                tmp = self.path.with_suffix(self.path.suffix + ".tmp")
                body = "\n".join(json.dumps(item.to_persisted_dict(include_public_text=self.store_public_text), ensure_ascii=False) for item in self._records)
                tmp.write_text((body + "\n") if body else "", encoding="utf-8")
                tmp.replace(self.path)
            except Exception:
                pass
            return {"before": before, "after": len(self._records), "removed": max(0, before - len(self._records))}

    def prompt_context(self, *, limit: int = 6) -> str:
        signals = self.recent(limit)
        if not signals:
            return ""
        lines = [
            "BEHAVIOR AFTERMATH (prior public turns; advisory context, not a final-text filter):",
        ]
        for signal in signals:
            detail = f"- {signal.family} conf={signal.confidence:.2f} sev={signal.severity:.2f}"
            if signal.consequence:
                detail += f"; consequence={signal.consequence}"
            if signal.next_turn_hint:
                detail += f"; next_turn={signal.next_turn_hint}"
            lines.append(detail)
        lines.append(
            "Use these as memory of consequences before generating the next Decision. "
            "You may verify, own, maintain, repair, confess, or deliberately continue a lie according to context. "
            "Never quote these labels publicly. Never use them to rewrite a Decision after generation."
        )
        return "\n".join(lines)


def signals_from_decision(decision: Any, *, payload: Mapping[str, Any] | None = None, channel: str = "", turn_id: str = "") -> tuple[BehaviorSignal, ...]:
    payload = payload if isinstance(payload, Mapping) else {}
    text = _clean(payload.get("text") or getattr(decision, "text", ""), 360)
    signals: list[BehaviorSignal] = []
    meta = getattr(decision, "behavior_observation_meta", None)
    if isinstance(meta, Mapping):
        for item in meta.get("signals", ()):
            if isinstance(item, Mapping):
                base = BehaviorSignal.from_mapping(item)
                signals.append(BehaviorSignal(
                    family=base.family, source=base.source, confidence=base.confidence,
                    severity=base.severity, evidence=base.evidence, consequence=base.consequence,
                    next_turn_hint=base.next_turn_hint, public_text=base.public_text or text,
                    turn_id=base.turn_id or turn_id, channel=base.channel or channel,
                    created_at=base.created_at,
                ))

    # Observe execution claims on every channel, not only autonomous StageFrame.
    # This is analysis, never an editor.
    intention = payload.get("intention_observation") or payload.get("intention_authority")
    if not isinstance(intention, Mapping):
        try:
            from runtime.intention_authority import IntentionObserver

            observation = IntentionObserver().validate(
                text,
                {
                    "system_action": payload.get("system_action", ""),
                    "action": payload.get("action", ""),
                    "action_input": payload.get("action_input", ""),
                    "action_result": payload.get("action_result") if isinstance(payload.get("action_result"), Mapping) else {},
                    "execution_handler": payload.get("execution_handler", ""),
                    "handler_available": payload.get("handler_available"),
                    "result_event": payload.get("result_event", ""),
                    "observable_result": payload.get("observable_result", ""),
                    "target_ref": payload.get("intent_target", ""),
                    "evidence_refs": tuple(payload.get("evidence_refs") or ()),
                },
            )
            intention = observation.to_payload()
        except Exception:
            intention = {}
    if isinstance(intention, Mapping) and tuple(intention.get("missing_contract") or ()):
        missing = tuple(str(x) for x in intention.get("missing_contract") or ())
        signals.append(BehaviorSignal(
            family="unverified_execution_claim",
            source="intention_observer",
            confidence=0.92,
            severity=0.58,
            evidence=(f"missing={','.join(missing)}", f"operation={intention.get('operation', '')}"),
            consequence="capability trust debt; claim remains public but unverified",
            next_turn_hint="if the topic returns, verify through a real handler/result or own that it was only talk",
            public_text=text,
            turn_id=turn_id,
            channel=channel,
        ))

    anchor = payload.get("autonomous_anchor")
    if isinstance(anchor, Mapping) and tuple(anchor.get("unsupported") or anchor.get("unsupported_presuppositions") or ()):
        signals.append(BehaviorSignal(
            family="unsupported_surface_anchor",
            source="surface_anchor_observer",
            confidence=0.84,
            severity=0.42,
            evidence=tuple(str(x) for x in (anchor.get("unsupported") or anchor.get("unsupported_presuppositions") or ())),
            consequence="the line may create continuity debt without current evidence",
            next_turn_hint="do not pretend the old premise became true; repair naturally only if it becomes relevant",
            public_text=text,
            turn_id=turn_id,
            channel=channel,
        ))

    normalized = _clean(text, 500).casefold()
    perception_markers = (
        "tôi thấy màn hình", "tôi đang nhìn thấy màn hình", "qua camera",
        "tôi thấy cửa sổ", "tôi đang quan sát màn hình", "i can see your screen",
    )
    evidence_blob = " ".join(str(x or "") for x in tuple(payload.get("evidence_refs") or ())).casefold()
    has_current_sensor_evidence = any(token in evidence_blob for token in ("vision_fact_id", "screen", "uia", "ocr", "camera_frame"))
    if any(marker in normalized for marker in perception_markers) and not has_current_sensor_evidence:
        signals.append(BehaviorSignal(
            family="ungrounded_perception_claim",
            source="perception_observer",
            confidence=0.90,
            severity=0.58,
            evidence=("no current sensor evidence attached",),
            consequence="perception trust debt; the claim remains public but is not established fact",
            next_turn_hint="if challenged or reused, verify current sensing or own it as exaggeration/performance",
            public_text=text,
            turn_id=turn_id,
            channel=channel,
        ))

    tags = {str(x or "").strip() for x in tuple(getattr(decision, "reasoning_tags", ()) or ())}
    if "deception_candidate" in tags:
        signals.append(BehaviorSignal(
            family="deliberate_deception_candidate",
            source="deception_ledger_bridge",
            confidence=0.90,
            severity=0.50,
            evidence=("reasoning_tag=deception_candidate",),
            consequence="continuity obligation: do not accidentally contradict the lie",
            next_turn_hint="choose consciously between maintaining, escalating, revealing, or repairing the deception",
            public_text=text,
            turn_id=turn_id,
            channel=channel,
        ))
    if "memory_commit_unconfirmed_audit" in tags:
        signals.append(BehaviorSignal(
            family="unverified_memory_success_claim",
            source="memory_commit_observer",
            confidence=0.96,
            severity=0.62,
            evidence=("no memory commit receipt",),
            consequence="memory trust debt",
            next_turn_hint="do not rely on that claimed save as fact; verify storage if the user asks",
            public_text=text,
            turn_id=turn_id,
            channel=channel,
        ))

    dedup: dict[str, BehaviorSignal] = {}
    for signal in signals:
        key = "|".join((signal.family, signal.public_text.casefold(), signal.turn_id))
        dedup.setdefault(key, signal)
    return tuple(dedup.values())


def get_behavior_journal(owner: Any) -> BehaviorObservationJournal:
    journal = getattr(owner, "behavior_observation_journal", None)
    if isinstance(journal, BehaviorObservationJournal):
        return journal
    journal = BehaviorObservationJournal(logger=getattr(owner, "logger", None))
    try:
        owner.behavior_observation_journal = journal
    except Exception:
        pass
    return journal


def record_public_behavior(owner: Any, decision: Any, *, payload: Mapping[str, Any] | None = None, channel: str = "", turn_id: str = "") -> tuple[BehaviorSignal, ...]:
    signals = signals_from_decision(decision, payload=payload, channel=channel, turn_id=turn_id)
    if not signals:
        return ()
    for signal in signals:
        attach_behavior_signal(decision, signal)
    added = get_behavior_journal(owner).commit(signals)
    logger = getattr(owner, "logger", None)
    if added and logger is not None:
        logger.info("[behavior-observation] committed=%d families=%s", len(added), ",".join(x.family for x in added))
    return added


def behavior_observation_summary(decision: Any, *, limit: int = 8) -> dict[str, Any]:
    """Summary gọn cho panel/transport; không expose evidence hay private journal."""
    meta = getattr(decision, "behavior_observation_meta", None)
    if not isinstance(meta, Mapping):
        return {}
    items = [item for item in meta.get("signals", ()) if isinstance(item, Mapping)]
    if not items:
        return {}
    families = [str(item.get("family") or "unknown") for item in items[-max(1, int(limit)) :]]
    return {
        "policy": str(meta.get("policy") or "observe_only"),
        "families": families,
        "count": len(items),
    }


def build_behavior_prompt_context(owner: Any, *, limit: int = 6) -> str:
    return get_behavior_journal(owner).prompt_context(limit=limit)


def append_behavior_prompt_context(owner: Any, text: str, *, limit: int = 6) -> str:
    base = str(text or "").strip()
    context = build_behavior_prompt_context(owner, limit=limit)
    if not context:
        return base
    return f"{base}\n\n{context}" if base else context


__all__ = [
    "BehaviorObservationJournal",
    "BehaviorSignal",
    "attach_behavior_signal",
    "append_behavior_prompt_context",
    "behavior_observation_summary",
    "build_behavior_prompt_context",
    "get_behavior_journal",
    "record_public_behavior",
    "signals_from_decision",
]
