# -*- coding: utf-8 -*-
"""Event-driven ActiveConcern runtime cho ERIS V42.

V40 từng đọc lại affordance/seed ở mỗi scheduler tick và vô tình coi việc "thấy
lại cùng dữ liệu" như một lần cognition mới. V42 đảo luật sở hữu:

    NEW CognitiveEvent -> update concern đúng một lần
    scheduler tick      -> chỉ project/decay, không mutate concern

Không tạo database concern mới. ConcernStore vẫn là nguồn persistent; module này
chỉ làm ingress idempotent và projection cho Subject/Neuro/chat.
"""
from __future__ import annotations

import time
from typing import Any, Mapping, Sequence

from cortex.active_concern import (
    agenda_concern_view,
    clamp01,
    merge_concern_views,
    private_concern_view,
    thought_concern_view,
)
from cortex.cognitive_event import CognitiveEvent, CognitiveEventLedger


class ActiveConcernRuntime:
    def __init__(self, host: Any, *, logger: Any | None = None) -> None:
        self.host = host
        self.logger = logger or getattr(host, "logger", None)
        self._last_activation: dict[str, float] = {}
        self._last_signature = ""
        # Hot-path idempotency. ConcernStore còn giữ cognitive_event_ids để cùng
        # event không sống lại sau khi runtime object được dựng lại.
        self._event_ledger = CognitiveEventLedger(max_events=1024, ttl_seconds=24.0 * 3600.0)

    def _store(self) -> Any | None:
        daemon = getattr(self.host, "private_mind_daemon", None)
        return getattr(daemon, "concern_store", None)

    def _interruption_cost(self) -> float:
        attention = getattr(self.host, "last_cortex_attention", None)
        return clamp01(getattr(attention, "interrupt_cost", 0.0), 0.0)

    def ingest_affordances(self, affordances: Sequence[Any], *, now: float | None = None) -> tuple[str, ...]:
        """Chuyển seed mới thành CognitiveEvent và consume đúng một lần.

        Một seed đã ingest có thể tiếp tục nằm trong reservoir để Neuro cân nhắc
        speech, nhưng việc scheduler nhìn lại nó KHÔNG được làm concern tăng tuổi
        đời/recurrence/activation. Nó đã xảy ra rồi. Muốn concern đổi tiếp phải có
        event mới thật sự: user turn, evidence, association, outcome, thought mutate…
        """

        store = self._store()
        ingest = getattr(store, "ingest_cognitive_event", None) if store is not None else None
        if not callable(ingest):
            return ()
        current = float(now or time.time())
        changed: list[str] = []
        for affordance in tuple(affordances or ()):
            candidate = getattr(affordance, "candidate", None)
            if candidate is None:
                continue
            quality = clamp01(getattr(candidate, "seed_quality_score", 0.0), 0.0)
            if quality < 0.58:
                continue
            event = CognitiveEvent.from_seed_candidate(candidate, now=current)
            if event is None:
                continue
            if not self._event_ledger.accept(event, now=current):
                self._log("debug", "[cognitive event] replay ignored id=%s", event.event_id)
                continue
            try:
                receipt = ingest(event.to_dict())
            except Exception as exc:
                self._log("warning", "[cognitive event] ingest failed id=%s error=%s", event.event_id, exc)
                continue
            concern = getattr(receipt, "concern", None)
            outcome = str(getattr(receipt, "outcome", "") or "")
            if concern is None:
                continue
            concern_id = str(getattr(concern, "id", "") or "")
            if outcome == "duplicate":
                self._log("debug", "[cognitive event] persisted replay ignored id=%s concern=%s", event.event_id, concern_id)
                continue
            changed.append(concern_id)
            self._log(
                "info",
                "[cognitive event] applied id=%s outcome=%s concern=%s kind=%s related=%s subject=%s",
                event.event_id,
                outcome,
                concern_id,
                event.kind,
                len(tuple(getattr(receipt, "related_ids", ()) or ())),
                event.subject,
            )
            # V43: cùng CognitiveEvent có thể nudge affect đúng một lần. Affect
            # runtime có ledger riêng vì concern và mood là hai state machine
            # độc lập, nhưng cùng dùng một event identity. Surface text không đi
            # qua đường này nên không thể tự nuôi mood bằng prose của chính nó.
            affect_runtime = getattr(self.host, "affective_dynamics_runtime", None)
            affect_ingest = getattr(affect_runtime, "ingest_cognitive_event", None)
            if callable(affect_ingest):
                try:
                    affect_ingest(event, now=current)
                except Exception as exc:
                    self._log("debug", "[affect-v43] cognitive event degraded id=%s error=%s", event.event_id, exc)
        return tuple(dict.fromkeys(changed))

    # Compatibility cho code/plugin cũ. Alias này giữ API nhưng đổi semantics:
    # "absorb" giờ idempotent event ingress, không còn re-absorb mỗi tick.
    def absorb_affordances(self, affordances: Sequence[Any], *, now: float | None = None) -> tuple[str, ...]:
        return self.ingest_affordances(affordances, now=now)

    def snapshot(self, *, now: float | None = None, limit: int = 8) -> tuple[dict[str, Any], ...]:
        current = float(now or time.time())
        interruption = self._interruption_cost()
        projected = []

        # 1) PrivateConcern là nguồn persistent mạnh nhất.
        store = self._store()
        if store is not None:
            try:
                concerns = tuple(store.open_concerns(limit=max(12, limit * 2)) or ())
            except Exception:
                concerns = ()
            for concern in concerns:
                cid = str(getattr(concern, "id", "") or "")
                projected.append(private_concern_view(
                    concern,
                    now=current,
                    previous_activation=self._last_activation.get(cid),
                    interruption_cost=interruption,
                ))

        # 2) ThoughtStream là cognition transient. Projection không copy nó vào
        # ConcernStore và không tạo recurrence chỉ vì tick đọc nó lại.
        thought_stream = getattr(self.host, "thought_stream", None)
        latest_report = getattr(thought_stream, "latest_report", None)
        if callable(latest_report):
            try:
                report = latest_report()
                thoughts = tuple(getattr(report, "recent_thoughts", ()) or ())
            except Exception:
                thoughts = ()
            for thought in thoughts[:8]:
                if bool(getattr(thought, "resolved", False)):
                    continue
                cid = f"thought:{str(getattr(thought, 'thought_id', '') or '')}"
                projected.append(thought_concern_view(
                    thought,
                    now=current,
                    previous_activation=self._last_activation.get(cid),
                    interruption_cost=interruption,
                ))

        # 3) InternalAgenda giữ task/question có provenance. Cũng chỉ project.
        agenda = getattr(self.host, "internal_agenda", None)
        top_items = getattr(agenda, "top_items", None)
        if callable(top_items):
            try:
                items = tuple(top_items(limit=8) or ())
            except Exception:
                items = ()
            for item in items:
                if not tuple(getattr(item, "evidence_ids", ()) or ()):
                    continue
                cid = f"agenda:{str(getattr(item, 'id', '') or '')}"
                projected.append(agenda_concern_view(
                    item,
                    now=current,
                    previous_activation=self._last_activation.get(cid),
                    interruption_cost=interruption,
                ))

        # V42 merge projection rất bảo thủ: chỉ cùng identity/shared evidence mạnh
        # mới gộp. "Có liên quan" là graph link, không phải một concern khổng lồ.
        views = list(merge_concern_views(projected))
        for view in views:
            self._last_activation[view.concern_id] = view.activation
        # V70: snapshot phục vụ cả private material generation lẫn expression.
        # Một concern đã nói đủ vẫn được nổi lên nếu cognitive attractor còn mạnh;
        # ExpressionUrge sẽ đọc completion riêng và không vì vậy mà tự được nói.
        views.sort(
            key=lambda item: (
                max(item.speech_pressure, 0.92 * item.attractor_strength),
                item.activation,
                item.persistence,
            ),
            reverse=True,
        )
        payload = tuple(item.to_dict() for item in views[: max(0, int(limit))])
        signature = "|".join(
            f"{item['concern_id']}:{item['state']}:{round(float(item['activation']),1)}:{round(float(item['speech_pressure']),1)}"
            for item in payload[:3]
        )
        if signature and signature != self._last_signature:
            top = payload[0]
            meta = dict(top.get("metadata") or {})
            self._log(
                "info",
                "[active concern] top id=%s state=%s activation=%.2f novelty=%.2f habituation=%.2f attractor=%.2f awareness=%.2f speech=%.2f trajectory=%s events=%s echoes=%s sources=%s",
                top.get("concern_id", ""),
                top.get("state", ""),
                float(top.get("activation", 0.0)),
                float(top.get("novelty", 0.0)),
                float(top.get("habituation", 0.0)),
                float(top.get("attractor_strength", 0.0)),
                float(top.get("rumination_awareness", 0.0)),
                float(top.get("speech_pressure", 0.0)),
                top.get("trajectory", ""),
                int(meta.get("event_count", 0) or 0),
                int(meta.get("echo_streak", 0) or 0),
                ",".join(str(x) for x in tuple(top.get("source_kinds", ()) or ())[:4]),
            )
            self._last_signature = signature
        return payload

    def note_private_return(
        self,
        concern_id: str,
        *,
        material_id: str,
        development_mode: str = "",
        development_score: float = 0.0,
        now: float | None = None,
    ) -> bool:
        """Receipt V70: một mental material thật vừa quay lại concern này.

        Method chỉ delegate sang ConcernStore để giữ single owner persistence.
        Không tăng speech pressure và không tạo CognitiveEvent giả.
        """
        store = self._store()
        note = getattr(store, "note_private_return", None) if store is not None else None
        if not callable(note):
            return False
        current = float(now or time.time())
        try:
            changed = bool(note(
                concern_id,
                material_id=material_id,
                development_mode=development_mode,
                development_score=development_score,
                now=current,
            ))
        except Exception as exc:
            self._log("debug", "[rumination v70] private return degraded concern=%s error=%s", concern_id, exc)
            return False
        if changed:
            self._log(
                "info",
                "[rumination v70] private-return concern=%s material=%s mode=%s development=%.2f",
                concern_id, material_id, development_mode or "none", float(development_score or 0.0),
            )
        return changed

    def note_public_surface(self, concern_id: str, *, now: float | None = None, evidence_ref: str = "") -> bool:
        """Ghi habituation sau public speech, KHÔNG feed câu Surface ngược vào cognition."""

        store = self._store()
        if store is None or not concern_id:
            return False
        concern = getattr(store, "get", lambda _x: None)(concern_id)
        if concern is None:
            return False
        current = float(now or time.time())
        meta = dict(getattr(concern, "metadata", {}) or {})
        meta["surfaced_count"] = int(meta.get("surfaced_count", 0) or 0) + 1
        meta["last_surfaced_at"] = current
        # V69: concern vẫn có thể sống sau khi nói, nhưng nhu cầu *biểu đạt* của
        # concern đó đã được consummate. Ba trục completion tách việc "còn quan
        # tâm" khỏi "còn cần nói".
        completion = dict(meta.get("completion") or {})
        completion["expressive"] = clamp01(max(float(completion.get("expressive", 0.0) or 0.0), 0.90))
        completion["epistemic"] = clamp01(max(float(completion.get("epistemic", 0.0) or 0.0), 0.10))
        completion["relational"] = clamp01(max(float(completion.get("relational", 0.0) or 0.0), 0.26))
        completion["updated_at"] = current
        meta["completion"] = completion
        if evidence_ref:
            refs = list(getattr(concern, "evidence_ids", ()) or ())
            if evidence_ref not in refs:
                refs.append(evidence_ref)
                concern.evidence_ids = tuple(refs[:64])
        concern.metadata = meta
        # Nói ra xả speech pressure nhưng không tạo một cognitive event mới.
        # Surface wording tuyệt đối không trở thành oracle cho vòng seed kế tiếp.
        concern.pressure = clamp01(concern.pressure * 0.80)
        store.add_or_update(concern)
        self._log(
            "info",
            "[active concern] surfaced id=%s count=%s pressure=%.2f",
            concern_id,
            meta["surfaced_count"],
            concern.pressure,
        )
        return True

    def event_ledger_snapshot(self) -> dict[str, Any]:
        return self._event_ledger.snapshot()

    def _log(self, level: str, message: str, *args: Any) -> None:
        logger = self.logger
        fn = getattr(logger, level, None) if logger is not None else None
        if callable(fn):
            fn(message, *args)


__all__ = ["ActiveConcernRuntime"]
