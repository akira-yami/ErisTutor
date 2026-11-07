# -*- coding: utf-8 -*-
"""
cortex/private_life/core.py
===========================

PrivateLifeCore: tầng đời sống riêng của ERIS.

Tầng này cố ý không lấy user làm trung tâm. Nó theo dõi:
- HabitatModel: nhà/runtime đang có gì và thay đổi thế nào.
- ObjectAttachment: file/module/memory/service nào trở thành "đồ vật" quan trọng.
- PrivateProjects: câu hỏi riêng đang âm ỉ.
- DailyRhythm: phiên đang ở nhịp nào.
- TasteProfile: ERIS dần thích/ghét kiểu câu/hành vi nào.
- SolitudeLoop: khi user vắng, runtime làm gì với chính nó.
- RitualMemory: nghi thức lặp lại.
- InternalErrands: việc vặt nội bộ cần dọn.
- LifeEvents: sự kiện đời sống riêng.

Đây là core deterministic, không gọi LLM. Nếu sau này cần model-backed synthesis,
đặt nó ở daemon/adaptor riêng. Đừng biến core state thành chợ phiên API.
"""

from __future__ import annotations
from core.di import resolve_dependency

import os
import time
from pathlib import Path
from typing import Any, Iterable, Mapping

from .contracts import (
    DailyRhythm,
    HabitatModel,
    HabitatObservation,
    InternalErrand,
    LifeEvent,
    MentalEvent,
    PrivateAnchor,
    PrivateAnchorUpdate,
    ObjectAttachment,
    ObjectResidue,
    PrivateLifeState,
    PrivateLifeTickResult,
    PrivateProject,
    PrivateProjectUpdate,
    RitualMemory,
    SolitudeLoop,
    SurfaceSeedCandidate,
    TasteProfile,
    TasteShift,
    clamp01,
    compact_text,
    private_life_state_digest,
    stable_id,
    to_plain,
)
from .self_projection import PrivateLifeSelfProjector, PrivateLifeDriver

PRIVATE_LIFE_VERSION = "private-life-v3-mental-workspace"


def _getattr_or_mapping(obj: Any, name: str, default: Any = None) -> Any:
    if obj is None:
        return default
    if isinstance(obj, Mapping):
        return obj.get(name, default)
    return getattr(obj, name, default)


class PrivateLifeCore:
    """Core đời sống riêng.

    Core này có hai luật:
    1. Không sinh lời thoại public.
    2. Mọi kết luận đều phải để lại LifeEvent/project update/object residue đủ audit.
    """

    # Chỉ giữ ID cũ để migration có thể nhận ra preset authored của V19.
    # Chúng KHÔNG còn là nguồn tạo hành vi hay nội dung mới.
    LEGACY_AUTHORED_ANCHOR_IDS = frozenset({
        "anchor.identity.presence_or_response",
        "anchor.aesthetic.restrained_pattern",
        "anchor.obsession.silence_presence_dignity",
        "anchor.world.runtime_house",
        "anchor.private_agenda.micro_presence_decay",
    })
    LEGACY_AUTHORED_PROJECT_IDS = frozenset({
        "project.silence_as_action",
        "project.body_only_swallowed_speech",
        "project.surface_cheapness",
        "project.memory_distortion_watch",
        "project.housekeeping",
    })

    def __init__(self, state: PrivateLifeState | None = None, *, root_dir: str | os.PathLike[str] | None = None) -> None:
        self.state = resolve_dependency(state, lambda: PrivateLifeState())
        self.root_dir = Path(root_dir or os.getenv("ERIS_PROJECT_ROOT", ".")).resolve()
        self.self_projector = PrivateLifeSelfProjector()
        self._migrate_legacy_authored_state()

    def _migrate_legacy_authored_state(self) -> None:
        """Hạ quyền preset V19 nhưng giữ record để forensic không mất lịch sử.

        Preset cũ không bị xóa. Chúng được đánh dấu ``legacy_authored`` và mất
        surface permission/pressure, vì một state từng tồn tại không đồng nghĩa nó
        được quyền tiếp tục giả làm thứ Eris tự hình thành.
        """

        for anchor_id in self.LEGACY_AUTHORED_ANCHOR_IDS:
            anchor = self.state.anchors.get(anchor_id)
            if anchor is None:
                continue
            anchor.origin = "legacy_authored"
            anchor.retired_reason = "V20: preset authored không còn là nguồn causal production"
            anchor.pressure = 0.0
            anchor.surface_permission = 0.0
            anchor.notes = tuple((*anchor.notes, anchor.retired_reason))[-24:]
        for project_id in self.LEGACY_AUTHORED_PROJECT_IDS:
            project = self.state.projects.get(project_id)
            if project is None:
                continue
            project.origin = "legacy_authored"
            project.retired_reason = "V20: preset authored không còn là nguồn causal production"
            project.status = "resolved"
            project.interest = 0.0
            project.surface_permission = 0.0
            project.notes = tuple((*project.notes, project.retired_reason))[-16:]

    def ingest_seed_mental_event(self, seed: Any, *, now: float | None = None) -> MentalEvent | None:
        """Nhận một semantic seed từ đúng *một* DeepSeek seed worker.

        Reservoir vẫn nhỏ và session-local; workspace này mới là phần đời sống bền.
        Seed không được nối thẳng ra lời nói. Ta chỉ lưu "ý gì vừa nảy ra", stance
        nhận thức của nó, và nó có để lại open-loop hay không.
        """

        current = float(now if now is not None else time.time())
        premise = compact_text(_getattr_or_mapping(seed, "premise", ""), 620)
        if not premise:
            return None
        seed_id = compact_text(_getattr_or_mapping(seed, "seed_id", ""), 120)
        kind = compact_text(_getattr_or_mapping(seed, "event_kind", "thought"), 64) or "thought"
        epistemic = compact_text(_getattr_or_mapping(seed, "epistemic_mode", "opinion"), 64) or "opinion"
        motive = compact_text(_getattr_or_mapping(seed, "eris_motive_kind", "want_to_express"), 64) or "want_to_express"
        topic_tags = tuple(str(x) for x in (_getattr_or_mapping(seed, "topic_tags", ()) or ()) if str(x or "").strip())[:12]
        source_refs = tuple(str(x) for x in (_getattr_or_mapping(seed, "source_refs", ()) or ()) if str(x or "").strip())[:16]

        curiosity_kinds = {"curiosity_question", "knowledge_gap", "discovery_impulse", "unfinished_loop"}
        open_loop = bool(_getattr_or_mapping(seed, "open_loop", False) or kind in curiosity_kinds)
        confidence = clamp01(_getattr_or_mapping(seed, "confidence", 0.0))
        authenticity = clamp01(_getattr_or_mapping(seed, "authenticity", 0.0))
        personal_stake = clamp01(_getattr_or_mapping(seed, "personal_stake", 0.0))
        fit = clamp01(_getattr_or_mapping(seed, "estimated_fit", 0.0))
        uncertainty = clamp01(_getattr_or_mapping(seed, "uncertainty", 0.0))
        ttl = max(120.0, min(1800.0, float(_getattr_or_mapping(seed, "ttl_seconds", 600.0) or 600.0)))

        # Một event có thể quay lại nếu seed worker chạm cùng topic/kind. Reactivation
        # tạo continuity mà không cần thêm model. Overlap tag được dùng thay vì regex
        # "giống người" thần bí nào đó, vì đôi khi một set intersection cũng làm được việc.
        tagset = set(topic_tags)
        existing: MentalEvent | None = None
        for candidate in self.state.mental_events.values():
            if candidate.status in {"resolved", "expired"} or candidate.kind != kind:
                continue
            if tagset and tagset.intersection(candidate.topic_tags):
                existing = candidate
                break

        salience = clamp01(0.34 * personal_stake + 0.28 * authenticity + 0.20 * confidence + 0.18 * fit)
        novelty = clamp01(0.52 + 0.24 * authenticity + 0.12 * uncertainty)
        curiosity = clamp01((0.68 if kind in curiosity_kinds else 0.18) + 0.22 * uncertainty)
        emotional_charge = clamp01(0.20 * personal_stake + 0.18 * _getattr_or_mapping(seed, "daring", 0.0))
        decay_rate = max(0.00005, min(0.004, 2.4 / ttl))

        if existing is not None:
            existing.content = premise
            existing.epistemic_mode = epistemic  # type: ignore[assignment]
            existing.motive = motive
            existing.topic_tags = tuple(dict.fromkeys((*existing.topic_tags, *topic_tags)))[:12]
            existing.salience = max(existing.salience * 0.88, salience)
            existing.novelty = max(existing.novelty * 0.82, novelty * 0.86)
            existing.curiosity = max(existing.curiosity, curiosity)
            existing.emotional_charge = max(existing.emotional_charge * 0.85, emotional_charge)
            existing.confidence = max(existing.confidence, confidence)
            existing.social_relevance = max(existing.social_relevance * 0.90, fit)
            existing.decay_rate = min(existing.decay_rate, decay_rate)
            existing.open_loop = bool(existing.open_loop or open_loop)
            existing.status = "active"
            existing.updated_at = current
            existing.last_activated_at = current
            existing.activation_count += 1
            existing.source_refs = tuple(dict.fromkeys((*existing.source_refs, *source_refs)))[:16]
            event = existing
            life_type = "mental_event_reactivated"
        else:
            event_id = f"mental.{seed_id}" if seed_id else stable_id("mental", kind, premise, int(current // 300))
            event = MentalEvent(
                id=event_id,
                kind=kind,  # type: ignore[arg-type]
                content=premise,
                origin_source=compact_text(_getattr_or_mapping(seed, "source_kind", "seed_worker"), 80),
                epistemic_mode=epistemic,  # type: ignore[arg-type]
                motive=motive,
                topic_tags=topic_tags,
                salience=salience,
                novelty=novelty,
                emotional_charge=emotional_charge,
                curiosity=curiosity,
                confidence=confidence,
                social_relevance=fit,
                decay_rate=decay_rate,
                open_loop=open_loop,
                source_refs=source_refs,
                created_at=current,
                updated_at=current,
                last_activated_at=current,
            )
            self.state.mental_events[event.id] = event
            life_type = "mental_event_ingested"

        # Workspace có giới hạn. Event già/yếu bị đẩy ra trước; open-loop được ưu tiên
        # giữ lại để Eris còn có thể nhớ mình từng muốn biết gì.
        self._decay_mental_workspace(now=current)
        self.state.events = tuple((*self.state.events, LifeEvent(
            type=life_type,  # type: ignore[arg-type]
            title=f"Mental event: {event.kind}",
            note=event.content,
            evidence_ids=event.evidence_ids,
            pressure=event.activation_score(current),
            created_at=current,
        )))[-64:]
        self.state.updated_at = current
        return event

    def _decay_mental_workspace(self, *, now: float) -> None:
        """Decay/compact deterministic. Không gọi model, không biến idle thành token tax."""

        for event in self.state.mental_events.values():
            if event.status in {"resolved", "expired"}:
                continue
            score = event.activation_score(now)
            age = max(0.0, now - event.updated_at)
            if score < 0.08 and age > 1800.0:
                event.status = "dormant"
            if score < 0.025 and age > 86400.0 and not event.open_loop:
                event.status = "expired"

        ranked = sorted(
            self.state.mental_events.values(),
            key=lambda item: (item.status != "expired", item.open_loop, item.activation_score(now), item.updated_at),
            reverse=True,
        )[:64]
        self.state.mental_events = {item.id: item for item in ranked}

    def mental_workspace_context(self, *, limit: int = 12, now: float | None = None) -> tuple[dict[str, Any], ...]:
        """Public-safe-ish semantic context cho seed worker; vẫn không phải lời public."""

        current = float(now if now is not None else time.time())
        self._decay_mental_workspace(now=current)
        ranked = sorted(
            (item for item in self.state.mental_events.values() if item.status in {"active", "dormant"}),
            key=lambda item: item.activation_score(current),
            reverse=True,
        )[: max(1, int(limit or 12))]
        return tuple({
            "id": item.id,
            "kind": item.kind,
            "content": item.content,
            "epistemic_mode": item.epistemic_mode,
            "motive": item.motive,
            "topic_tags": list(item.topic_tags),
            "open_loop": item.open_loop,
            "status": item.status,
            "activation_score": round(item.activation_score(current), 3),
            "activation_count": item.activation_count,
        } for item in ranked)

    def tick(
        self,
        *,
        snapshot: Any = None,
        runtime_owner: Any = None,
        user_text: str = "",
        channel: str = "autonomous",
        idle_seconds: float | None = None,
        recent_events: Iterable[Any] = (),
        subject_self: Mapping[str, Any] | None = None,
        private_mind_digest: Mapping[str, Any] | None = None,
        force: bool = False,
    ) -> PrivateLifeTickResult:
        """Chạy một tick đời sống riêng.

        Tick này đọc snapshot/runtime một cách khoan dung. Thiếu field thì im, không
        bịa. Một tầng "đời sống riêng" mà hallucinate nhà mình thì đúng là tiến hóa
        ngược có logging.
        """

        now = time.time()
        self._decay_mental_workspace(now=now)
        idle = self._infer_idle_seconds(snapshot=snapshot, runtime_owner=runtime_owner, explicit=idle_seconds)
        events = tuple(recent_events or ())

        habitat = self._observe_habitat(snapshot=snapshot, runtime_owner=runtime_owner, now=now)
        self._discover_observed_objects(events, now=now)
        residues = self._update_object_attachments(now=now)

        # V20: đời sống riêng không tự có preset. Driver phải đi từ self-model
        # causal hoặc concern lặp lại có evidence. PrivateLife chỉ tích lũy/decay.
        drivers = self.self_projector.derive(
            subject_self=subject_self,
            private_mind_digest=private_mind_digest,
        )
        project_updates, anchor_updates = self._apply_self_owned_drivers(
            drivers=drivers,
            now=now,
        )
        taste_shifts = self._update_taste(
            project_updates=project_updates,
            anchor_updates=anchor_updates,
            residues=residues,
            now=now,
        )
        life_events = self._build_life_events(
            habitat_observations=habitat,
            object_residues=residues,
            project_updates=project_updates,
            anchor_updates=anchor_updates,
            taste_shifts=taste_shifts,
            idle_seconds=idle,
            user_text=user_text,
            now=now,
        )
        surface_seed = self._build_surface_seed(
            project_updates=project_updates,
            anchor_updates=anchor_updates,
            object_residues=residues,
            life_events=life_events,
            idle_seconds=idle,
            now=now,
        )
        self._update_daily_rhythm(idle_seconds=idle, user_text=user_text, channel=channel, now=now)
        self._update_solitude(idle_seconds=idle, user_text=user_text, project_updates=project_updates, anchor_updates=anchor_updates, now=now)
        self._update_rituals(channel=channel, habitat_observations=habitat, now=now)
        self._update_errands(object_residues=residues, project_updates=project_updates, now=now)
        self._commit_events(life_events)
        self.state.updated_at = now

        return PrivateLifeTickResult(
            life_events=life_events,
            project_updates=project_updates,
            anchor_updates=anchor_updates,
            object_residues=residues,
            habitat_observations=habitat,
            taste_shifts=taste_shifts,
            surface_seed=surface_seed,
            state=self.state,
            debug={
                "idle_seconds": round(float(idle or 0.0), 3),
                "channel": channel,
                "force": bool(force),
                "private_life_version": PRIVATE_LIFE_VERSION,
                "self_owned_driver_count": len(drivers),
                "self_revision_key": str((subject_self or {}).get("revision_key", "")) if isinstance(subject_self, Mapping) else "",
                "state_digest": private_life_state_digest(self.state),
            },
        )

    def _infer_idle_seconds(self, *, snapshot: Any, runtime_owner: Any, explicit: float | None) -> float:
        if explicit is not None:
            return max(0.0, float(explicit or 0.0))
        for owner in (snapshot, runtime_owner):
            value = _getattr_or_mapping(owner, "idle_seconds", None)
            if value is None:
                value = _getattr_or_mapping(owner, "idle_time", None)
            if value is not None:
                try:
                    return max(0.0, float(value or 0.0))
                except Exception:
                    pass
        return 0.0

    def _observe_habitat(self, *, snapshot: Any, runtime_owner: Any, now: float) -> tuple[HabitatObservation, ...]:
        observations: list[HabitatObservation] = []
        active_window = compact_text(
            _getattr_or_mapping(snapshot, "active_window", "")
            or _getattr_or_mapping(snapshot, "window_title", "")
            or _getattr_or_mapping(snapshot, "active_title", ""),
            180,
        )
        active_app = compact_text(
            _getattr_or_mapping(snapshot, "active_app", "")
            or _getattr_or_mapping(snapshot, "app", "")
            or _getattr_or_mapping(snapshot, "process_name", ""),
            120,
        )
        content_metadata = _getattr_or_mapping(snapshot, "content_metadata", {}) or {}
        metadata_source = ""
        if isinstance(content_metadata, Mapping):
            metadata_source = compact_text(content_metadata.get("source") or content_metadata.get("tier") or "", 80)
        place_id = self._place_from_app(active_app, active_window)
        if place_id:
            self.state.habitat.places.setdefault(
                place_id,
                {"type": "observed", "meaning": "runtime place learned from foreground observation", "first_observed_at": now},
            )
        if place_id and place_id != self.state.habitat.active_place_id:
            observations.append(
                HabitatObservation(
                    place_id=place_id,
                    place_type="desktop",
                    title="habitat focus changed",
                    note=f"active_app={active_app or 'unknown'} window={active_window or 'unknown'}",
                    evidence_ids=(f"active_app:{active_app}" if active_app else "active_app:unknown",),
                    confidence=0.62 if active_app or active_window else 0.28,
                    pressure=0.38,
                    created_at=now,
                )
            )
            self.state.habitat.active_place_id = place_id
            self.state.habitat.last_changed_at = now
        if metadata_source:
            observations.append(
                HabitatObservation(
                    place_id=place_id or self.state.habitat.active_place_id,
                    place_type="foreground_metadata",
                    title="foreground metadata available",
                    note=f"metadata_source={metadata_source}",
                    evidence_ids=(f"content_metadata:{metadata_source}",),
                    confidence=0.58,
                    pressure=0.24,
                    created_at=now,
                )
            )
        queue_sizes = self._runtime_queue_notes(runtime_owner)
        for note, pressure in queue_sizes:
            observations.append(
                HabitatObservation(
                    place_id="runtime",
                    place_type="queue",
                    title="runtime queue pressure",
                    note=note,
                    evidence_ids=(stable_id("runtime", note),),
                    confidence=0.70,
                    pressure=pressure,
                    created_at=now,
                )
            )
        if observations:
            self.state.habitat.familiarity = clamp01(self.state.habitat.familiarity + 0.015 * len(observations))
            self.state.habitat.clutter = clamp01(0.92 * self.state.habitat.clutter + 0.08 * min(1.0, len(observations) / 5.0))
            self.state.habitat.recent_observations = tuple((*self.state.habitat.recent_observations, *observations))[-32:]
        return tuple(observations)

    def _place_from_app(self, active_app: str, active_window: str) -> str:
        text = f"{active_app} {active_window}".lower()
        if "godot" in text:
            return "godot_body"
        if "code" in text or "vscode" in text or ".py" in text or ".md" in text:
            return "repo"
        if "browser" in text or "chrome" in text or "edge" in text:
            return "browser_windows"
        if "llama" in text or "server" in text:
            return "model_server"
        return "desktop"

    def _runtime_queue_notes(self, runtime_owner: Any) -> tuple[tuple[str, float], ...]:
        notes: list[tuple[str, float]] = []
        for attr in ("private_mind_surface_queue", "private_life_surface_queue"):
            queue = getattr(runtime_owner, attr, None)
            if isinstance(queue, list) and len(queue) >= 3:
                notes.append((f"{attr} đang có {len(queue)} seed chờ surface", clamp01(len(queue) / 12.0)))
        budget = getattr(runtime_owner, "model_budget_policy", None)
        queue_state = getattr(budget, "queue_state", None)
        if callable(queue_state):
            try:
                state = queue_state()
                queued = _getattr_or_mapping(state, "queued", ()) or ()
                active = _getattr_or_mapping(state, "active", ()) or ()
                total = len(queued) + len(active)
                if total:
                    notes.append((f"model_budget_policy còn {total} request active/queued", clamp01(total / 8.0)))
            except Exception:
                pass
        return tuple(notes)

    def _update_object_attachments(self, *, now: float) -> tuple[ObjectResidue, ...]:
        residues: list[ObjectResidue] = []
        for object_id, attachment in list(self.state.attachments.items()):
            path = self.root_dir / object_id
            if path.exists():
                try:
                    stat = path.stat()
                    size = int(stat.st_size)
                    mtime = float(stat.st_mtime)
                except Exception:
                    continue
                changed = bool(attachment.last_seen_mtime and abs(mtime - attachment.last_seen_mtime) > 0.001)
                grew = bool(attachment.last_seen_size and size > attachment.last_seen_size * 1.25 and size - attachment.last_seen_size > 2048)
                attachment.last_seen_mtime = mtime
                attachment.last_seen_size = size
                attachment.last_touched_at = now if changed else attachment.last_touched_at or now
                attachment.familiarity = clamp01(attachment.familiarity + 0.006)
                if changed:
                    attachment.touch_count += 1
                    attachment.neglect_count = 0
                    attachment.attachment = clamp01(attachment.attachment + 0.018)
                    note = f"{object_id} vừa được chạm; meaning={attachment.meaning}"
                    attachment.residue = note
                    residues.append(ObjectResidue(object_id=object_id, note=note, pressure=0.34, evidence_ids=(f"file_mtime:{object_id}",), created_at=now))
                elif grew:
                    attachment.irritation = clamp01(attachment.irritation + 0.05)
                    note = f"{object_id} phình nhanh; cần xem đây là trưởng thành hay rác tích tụ"
                    attachment.residue = note
                    residues.append(ObjectResidue(object_id=object_id, note=note, pressure=0.56, evidence_ids=(f"file_size:{object_id}",), created_at=now))
                else:
                    # Mỗi tick thấy file mà không đổi vẫn tăng familiarity nhẹ, không tạo event rác.
                    pass
            else:
                attachment.neglect_count += 1
                attachment.irritation = clamp01(attachment.irritation + 0.006)
                if attachment.neglect_count in {6, 24, 72}:
                    note = f"{object_id} không thấy trong habitat hiện tại; attachment bị bỏ lửng"
                    attachment.residue = note
                    residues.append(ObjectResidue(object_id=object_id, note=note, pressure=clamp01(attachment.neglect_count / 72.0), evidence_ids=(f"missing_object:{object_id}",), created_at=now))
        return tuple(residues)

    def _discover_observed_objects(self, recent_events: tuple[Any, ...], *, now: float) -> None:
        """Tạo attachment chỉ khi runtime thật sự quan sát được object/path.

        Không quét sẵn một danh sách file "quan trọng" do tác giả chọn. Một file
        trở thành đồ vật riêng sau khi nó xuất hiện trong evidence/event và tồn tại
        trong workspace.
        """

        candidates: set[str] = set()
        for event in recent_events[:32]:
            values: list[str] = []
            if isinstance(event, Mapping):
                for key in ("path", "file", "file_path", "resource", "evidence_ref", "evidence_id"):
                    value = event.get(key)
                    if isinstance(value, str):
                        values.append(value)
                refs = event.get("evidence_refs", event.get("evidence_ids", ()))
                if isinstance(refs, (list, tuple)):
                    values.extend(str(x) for x in refs)
            elif isinstance(event, str):
                values.append(event)
            for raw in values:
                text = str(raw or "").strip().replace("\\", "/")
                for prefix in ("file:", "path:", "file_mtime:", "file_size:"):
                    if text.startswith(prefix):
                        text = text[len(prefix):]
                        break
                if not text or len(text) > 260:
                    continue
                path = Path(text)
                if not path.is_absolute():
                    path = self.root_dir / path
                try:
                    resolved = path.resolve()
                    rel = resolved.relative_to(self.root_dir).as_posix()
                except Exception:
                    continue
                if resolved.is_file():
                    candidates.add(rel)

        for object_id in sorted(candidates):
            if object_id in self.state.attachments:
                continue
            self.state.attachments[object_id] = ObjectAttachment(
                object_id=object_id,
                object_type="file",
                meaning="object được runtime quan sát lặp lại; ý nghĩa chưa được gán trước",
                familiarity=0.08,
                attachment=0.05,
                tags=("observed",),
            )

    def _apply_self_owned_drivers(
        self,
        *,
        drivers: tuple[PrivateLifeDriver, ...],
        now: float,
    ) -> tuple[tuple[PrivateProjectUpdate, ...], tuple[PrivateAnchorUpdate, ...]]:
        """Tích lũy driver có provenance thành anchor/project riêng.

        Điểm số dùng EMA về target, không cộng vô hạn theo tick. Cùng một facet
        được thấy 100 lần không biến thành "ám ảnh 100%" chỉ vì scheduler chăm chỉ.
        """

        project_updates: list[PrivateProjectUpdate] = []
        anchor_updates: list[PrivateAnchorUpdate] = []
        supported_projects: set[str] = set()
        supported_anchors: set[str] = set()

        for driver in drivers:
            if not driver.evidence_ids:
                continue
            if driver.mode == "anchor":
                anchor_id = stable_id("anchor.derived", driver.driver_id)
                supported_anchors.add(anchor_id)
                anchor = self.state.anchors.get(anchor_id)
                if anchor is None:
                    anchor = PrivateAnchor(
                        id=anchor_id,
                        kind=driver.anchor_kind,
                        title=driver.title,
                        question=driver.question,
                        stance=driver.stance,
                        pressure=0.0,
                        continuity=0.0,
                        surface_permission=0.0,
                        evidence_ids=driver.evidence_ids,
                        tags=driver.tags,
                        origin="derived",
                        source_refs=driver.source_refs,
                        support_count=0,
                        created_at=now,
                    )
                    self.state.anchors[anchor_id] = anchor
                old_pressure = anchor.pressure
                anchor.support_count += 1
                anchor.last_supported_at = now
                anchor.last_touched_at = now
                anchor.source_refs = tuple(dict.fromkeys((*anchor.source_refs, *driver.source_refs)))[-24:]
                anchor.evidence_ids = tuple(dict.fromkeys((*anchor.evidence_ids, *driver.evidence_ids)))[-24:]
                anchor.pressure = clamp01(0.82 * anchor.pressure + 0.18 * driver.pressure)
                anchor.continuity = clamp01(0.88 * anchor.continuity + 0.12 * max(driver.stability, min(1.0, anchor.support_count / 6.0)))
                # Surface permission cần lặp lại + confidence. Nội tâm không được
                # nhảy lên miệng ngay lần đầu chỉ vì một facet nghe có vẻ hay.
                surface_target = driver.confidence * min(1.0, anchor.support_count / 4.0)
                anchor.surface_permission = clamp01(0.90 * anchor.surface_permission + 0.10 * surface_target)
                note = compact_text(f"Self-owned anchor được củng cố từ {', '.join(driver.source_refs[:3])}: {driver.stance}", 520)
                anchor.notes = tuple((*anchor.notes, note))[-24:]
                anchor_updates.append(
                    PrivateAnchorUpdate(
                        anchor_id=anchor.id,
                        kind=anchor.kind,
                        title=anchor.title,
                        note=note,
                        pressure_delta=anchor.pressure - old_pressure,
                        evidence_ids=driver.evidence_ids,
                        created_at=now,
                    )
                )
            elif driver.mode == "project":
                project_id = stable_id("project.derived", driver.driver_id)
                supported_projects.add(project_id)
                project = self.state.projects.get(project_id)
                if project is None:
                    project = PrivateProject(
                        id=project_id,
                        title=driver.title,
                        question=driver.question,
                        status="dormant",
                        interest=0.0,
                        frustration=0.0,
                        evidence_ids=driver.evidence_ids,
                        origin="derived",
                        source_refs=driver.source_refs,
                        support_count=0,
                        created_at=now,
                    )
                    self.state.projects[project_id] = project
                old_interest = project.interest
                old_frustration = project.frustration
                project.support_count += 1
                project.last_supported_at = now
                project.last_advanced_at = now
                project.source_refs = tuple(dict.fromkeys((*project.source_refs, *driver.source_refs)))[-24:]
                project.evidence_ids = tuple(dict.fromkeys((*project.evidence_ids, *driver.evidence_ids)))[-24:]
                project.interest = clamp01(0.80 * project.interest + 0.20 * driver.pressure)
                project.frustration = clamp01(0.86 * project.frustration + 0.14 * max(0.0, driver.pressure - driver.stability * 0.35))
                project.status = "active" if project.interest >= 0.48 and project.support_count >= 2 else "simmering"
                project.surface_permission = clamp01(0.92 * project.surface_permission + 0.08 * driver.confidence * min(1.0, project.support_count / 5.0))
                note = compact_text(f"Project phát sinh từ self/concern có evidence: {driver.stance}", 520)
                project.notes = tuple((*project.notes, note))[-16:]
                project_updates.append(
                    PrivateProjectUpdate(
                        project_id=project.id,
                        title=project.title,
                        note=note,
                        status=project.status,
                        interest_delta=project.interest - old_interest,
                        frustration_delta=project.frustration - old_frustration,
                        evidence_ids=driver.evidence_ids,
                        created_at=now,
                    )
                )

        # Derived item mất nguồn hỗ trợ thì nguội dần. Legacy authored đã bị retire
        # ở migration và không được hồi sinh bởi decay logic này.
        for anchor_id, anchor in self.state.anchors.items():
            if anchor.origin != "derived" or anchor_id in supported_anchors:
                continue
            anchor.pressure = clamp01(anchor.pressure * 0.965)
            anchor.surface_permission = clamp01(anchor.surface_permission * 0.94)
        for project_id, project in self.state.projects.items():
            if project.origin != "derived" or project_id in supported_projects:
                continue
            project.interest = clamp01(project.interest * 0.96)
            project.frustration = clamp01(project.frustration * 0.97)
            project.surface_permission = clamp01(project.surface_permission * 0.94)
            if project.interest < 0.18 and project.status != "resolved":
                project.status = "dormant"

        return tuple(project_updates), tuple(anchor_updates)

    def _update_taste(
        self,
        *,
        project_updates: tuple[PrivateProjectUpdate, ...],
        anchor_updates: tuple[PrivateAnchorUpdate, ...],
        residues: tuple[ObjectResidue, ...],
        now: float,
    ) -> tuple[TasteShift, ...]:
        """V20 không tự gán gu từ tên preset.

        Taste chỉ đổi khi một anchor ``aesthetic`` có provenance thật xuất hiện.
        Hiện SelfProjector chưa tạo aesthetic facet nếu Subject Core chưa học một
        value phù hợp, nên mặc định hàm này thường im. Đúng hơn là bịa gu.
        """

        shifts: list[TasteShift] = []
        for update in anchor_updates:
            anchor = self.state.anchors.get(update.anchor_id)
            if anchor is None or anchor.kind != "aesthetic" or anchor.origin != "derived":
                continue
            key = stable_id("taste", *anchor.source_refs)
            before = self.state.taste.likes.get(key, 0.0)
            after = clamp01(0.90 * before + 0.10 * anchor.continuity)
            self.state.taste.likes[key] = after
            shifts.append(
                TasteShift(
                    dimension="self_derived_aesthetic",
                    direction=key,
                    delta=after - before,
                    reason="aesthetic anchor có provenance từ self-model được củng cố",
                    evidence_ids=anchor.evidence_ids[-8:],
                    created_at=now,
                )
            )
        self.state.taste.recent_shifts = tuple((*self.state.taste.recent_shifts, *shifts))[-32:]
        return tuple(shifts)

    def _build_life_events(
        self,
        *,
        habitat_observations: tuple[HabitatObservation, ...],
        object_residues: tuple[ObjectResidue, ...],
        project_updates: tuple[PrivateProjectUpdate, ...],
        anchor_updates: tuple[PrivateAnchorUpdate, ...],
        taste_shifts: tuple[TasteShift, ...],
        idle_seconds: float,
        user_text: str,
        now: float,
    ) -> tuple[LifeEvent, ...]:
        events: list[LifeEvent] = []
        for obs in habitat_observations[:4]:
            events.append(LifeEvent(type="habitat_changed", title=obs.title or "habitat changed", note=obs.note, evidence_ids=obs.evidence_ids, pressure=obs.pressure, created_at=now))
        for residue in object_residues[:6]:
            event_type = "object_touched" if "vừa được chạm" in residue.note else "object_neglected"
            events.append(LifeEvent(type=event_type, title=f"object residue: {residue.object_id}", note=residue.note, evidence_ids=residue.evidence_ids, pressure=residue.pressure, created_at=now))
        for update in project_updates[:6]:
            event_type = "private_project_advanced" if update.status in {"active", "simmering"} else "private_project_blocked"
            events.append(LifeEvent(type=event_type, title=update.title, note=update.note, evidence_ids=update.evidence_ids, pressure=clamp01(abs(update.interest_delta) + abs(update.frustration_delta)), created_at=now))
        for update in anchor_updates[:8]:
            events.append(
                LifeEvent(
                    type="private_anchor_activated",
                    title=f"{update.kind} anchor: {update.title}",
                    note=update.note,
                    evidence_ids=update.evidence_ids,
                    pressure=clamp01(abs(update.pressure_delta)),
                    created_at=now,
                )
            )
        for shift in taste_shifts[:4]:
            events.append(LifeEvent(type="taste_shifted", title=shift.direction, note=shift.reason, evidence_ids=shift.evidence_ids, pressure=clamp01(abs(shift.delta)), created_at=now))
        if idle_seconds >= 40 and not str(user_text or "").strip():
            events.append(
                LifeEvent(
                    type="solitude_period",
                    title="solitude tick",
                    note=f"user absent/quiet khoảng {int(idle_seconds)}s; attention chuyển sang private projects và dọn drift",
                    evidence_ids=(f"idle_seconds:{int(idle_seconds)}",),
                    pressure=clamp01(idle_seconds / 900.0),
                    created_at=now,
                )
            )
        return tuple(events)

    def _build_surface_seed(
        self,
        *,
        project_updates: tuple[PrivateProjectUpdate, ...],
        anchor_updates: tuple[PrivateAnchorUpdate, ...],
        object_residues: tuple[ObjectResidue, ...],
        life_events: tuple[LifeEvent, ...],
        idle_seconds: float,
        now: float,
    ) -> SurfaceSeedCandidate | None:
        candidates: list[SurfaceSeedCandidate] = []
        for update in project_updates:
            project = self.state.projects.get(update.project_id)
            permission = project.surface_permission if project else 0.0
            if permission >= 0.35:
                candidates.append(
                    SurfaceSeedCandidate(
                        topic_seed=update.note,
                        confidence=clamp01(0.42 + (project.interest if project else 0.0) * 0.32),
                        pressure=clamp01((project.frustration if project else 0.0) + idle_seconds / 900.0),
                        evidence_ids=update.evidence_ids,
                        surface_permission=permission,
                        llm_contract="PrivateLife seed: nói cực ngắn như một ý riêng đang quay lại; không dump state, không claim màn hình.",
                        created_at=now,
                    )
                )
        for update in anchor_updates:
            anchor = self.state.anchors.get(update.anchor_id)
            permission = anchor.surface_permission if anchor else 0.0
            if permission >= 0.35:
                candidates.append(
                    SurfaceSeedCandidate(
                        topic_seed=update.note,
                        kind="private_anchor_surface",
                        confidence=clamp01(0.40 + (anchor.continuity if anchor else 0.0) * 0.28),
                        pressure=clamp01((anchor.pressure if anchor else 0.0) + idle_seconds / 1000.0),
                        evidence_ids=update.evidence_ids or (update.anchor_id,),
                        surface_permission=permission,
                        llm_contract="PrivateAnchor seed: một câu rất ngắn về neo riêng; không dump state, không claim màn hình, không biến thành lời thú nhận ý thức.",
                        created_at=now,
                    )
                )
        for residue in object_residues:
            attachment = self.state.attachments.get(residue.object_id)
            permission = clamp01((attachment.attachment if attachment else 0.0) * 0.45 + (attachment.irritation if attachment else 0.0) * 0.45)
            if permission >= 0.35:
                candidates.append(
                    SurfaceSeedCandidate(
                        topic_seed=residue.note,
                        confidence=0.48,
                        pressure=residue.pressure,
                        evidence_ids=residue.evidence_ids,
                        surface_permission=permission,
                        llm_contract="ObjectAttachment seed: dùng như residue riêng của nhà runtime; không nêu đường dẫn thô nếu không cần.",
                        created_at=now,
                    )
                )
        if not candidates:
            return None
        candidates.sort(key=lambda c: (c.should_surface, c.pressure + c.confidence + c.surface_permission), reverse=True)
        return candidates[0]

    def _update_daily_rhythm(self, *, idle_seconds: float, user_text: str, channel: str, now: float) -> None:
        local_hour = time.localtime(now).tm_hour
        if self.state.daily.last_startup_at <= 0.0:
            self.state.daily.phase = "startup"
            self.state.daily.last_startup_at = now
            self.state.daily.session_count += 1
        elif idle_seconds >= 120:
            self.state.daily.phase = "idle_wandering"
        elif local_hour >= 22 or local_hour <= 4:
            self.state.daily.phase = "late_consolidation"
        elif str(user_text or "").strip() or channel == "chat":
            self.state.daily.phase = "deep_work"
        else:
            self.state.daily.phase = "first_observation"
        self.state.daily.fatigue_curve = clamp01(self.state.daily.fatigue_curve * 0.985 + (0.012 if self.state.daily.phase == "deep_work" else 0.004))

    def _update_solitude(self, *, idle_seconds: float, user_text: str, project_updates: tuple[PrivateProjectUpdate, ...], anchor_updates: tuple[PrivateAnchorUpdate, ...], now: float) -> None:
        solitude = self.state.solitude
        user_active = bool(str(user_text or "").strip())
        if user_active:
            solitude.current_solitude_started_at = 0.0
            solitude.attention_to_user = clamp01(solitude.attention_to_user + 0.20)
            return
        if idle_seconds >= 40 and solitude.current_solitude_started_at <= 0.0:
            solitude.current_solitude_started_at = now - idle_seconds
        if idle_seconds >= 40:
            solitude.solitude_seconds_total += min(60.0, max(1.0, idle_seconds / 12.0))
            solitude.familiarity = clamp01(solitude.familiarity + 0.012)
            solitude.attention_to_user = clamp01(solitude.attention_to_user * 0.92)
            solitude.private_project_pressure = clamp01(solitude.private_project_pressure + 0.03 + 0.02 * len(project_updates) + 0.015 * len(anchor_updates))
            if anchor_updates:
                solitude.last_consolidation_note = anchor_updates[0].note
            elif project_updates:
                solitude.last_consolidation_note = project_updates[0].note

    def _update_rituals(self, *, channel: str, habitat_observations: tuple[HabitatObservation, ...], now: float) -> None:
        ritual = self.state.rituals.get("ritual.startup_scan")
        if ritual and self.state.daily.phase == "startup" and habitat_observations:
            if now - ritual.last_completed_at > 60.0:
                ritual.last_completed_at = now
                ritual.completion_count += 1
                ritual.familiarity = clamp01(ritual.familiarity + 0.05)
                ritual.residue = "startup scan đã chạm habitat và object attachments"

    def _update_errands(self, *, object_residues: tuple[ObjectResidue, ...], project_updates: tuple[PrivateProjectUpdate, ...], now: float) -> None:
        for residue in object_residues:
            if residue.pressure < 0.45:
                continue
            errand_id = stable_id("errand", residue.object_id, "object_residue")
            current = self.state.errands.get(errand_id)
            if current is None:
                self.state.errands[errand_id] = InternalErrand(
                    id=errand_id,
                    title=f"rà lại {residue.object_id}",
                    reason=residue.note,
                    priority=residue.pressure,
                    evidence_ids=residue.evidence_ids,
                    created_at=now,
                    updated_at=now,
                )
            else:
                current.priority = max(current.priority, residue.pressure)
                current.reason = residue.note
                current.updated_at = now
                if current.status == "done":
                    current.status = "open"

    def _commit_events(self, events: tuple[LifeEvent, ...]) -> None:
        if not events:
            return
        seen = {item.id for item in self.state.events[-64:]}
        new_events = [item for item in events if item.id not in seen]
        if new_events:
            self.state.events = tuple((*self.state.events, *new_events))[-64:]

    def digest(self) -> dict[str, Any]:
        """Digest đọc nhanh cho runtime/debug menu."""

        return private_life_state_digest(self.state)

    def to_dict(self) -> dict[str, Any]:
        return self.state.to_dict()
