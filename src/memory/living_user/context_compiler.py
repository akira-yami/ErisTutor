# -*- coding: utf-8 -*-
"""Biên dịch user profile thành context packet theo đúng query hiện tại."""
from __future__ import annotations
from core.di import resolve_dependency

import math
import time
from dataclasses import dataclass
from typing import Any, Iterable, Mapping

from memory.semantic_memory import normalize_text
from memory.memory_admission import MemoryAdmissionPolicy

from .action_memory import ActionMemoryStore
from .clarification import MemoryClarificationPolicy
from .episode_store import UserEpisodeStore
from .models import ProfileContextItem, ProfileContextPacket, ProfileRecord, UserMemoryScope
from .episode_projection import episode_view, token_cost
from .store import UserProfileStore


@dataclass(slots=True)
class ProfileContextCompilerConfig:
    max_items: int = 14
    max_episodes: int = 4
    min_score: float = 0.18
    include_inferred_above: float = 0.78
    default_token_budget: int = 900


class ProfileContextCompiler:
    """Chọn đúng phần profile cho từng nhiệm vụ.

    Toàn bộ profile không bao giờ được nhét vào prompt. Context compiler đánh điểm
    theo domain, scope, query relevance, độ chắc chắn và độ mới. Điều này vừa giảm
    token vừa tránh lời đáp bị ám bởi một sở thích không liên quan từ sáu tháng trước.
    """

    def __init__(
        self,
        profile_store: UserProfileStore,
        episode_store: UserEpisodeStore,
        *,
        embedding_index: Any | None = None,
        action_store: ActionMemoryStore | None = None,
        clarification_policy: MemoryClarificationPolicy | None = None,
        config: ProfileContextCompilerConfig | None = None,
        admission_policy: MemoryAdmissionPolicy | None = None,
        episode_rollups: Any | None = None,
    ) -> None:
        self.profile_store = profile_store
        self.episode_store = episode_store
        self.embedding_index = embedding_index
        self.episode_rollups = episode_rollups
        self.action_store = action_store
        self.clarification_policy = resolve_dependency(clarification_policy, lambda: MemoryClarificationPolicy())
        self.config = resolve_dependency(config, lambda: ProfileContextCompilerConfig())
        self.admission_policy = resolve_dependency(admission_policy, lambda: MemoryAdmissionPolicy())

    def compile(
        self,
        query: str,
        *,
        scope: UserMemoryScope | None = None,
        domain: str | None = None,
        token_budget: int | None = None,
        include_sensitive: bool = False,
        include_proactive: bool = False,
        session_context: Mapping[str, Any] | None = None,
    ) -> ProfileContextPacket:
        active_scope = scope or UserMemoryScope()
        resolved_domain = domain or infer_domain(query)
        budget = max(160, int(token_budget or self.config.default_token_budget))
        # V41: profile cũng phải qua admission trước relevance. Một record model suy đoán
        # không được vào prompt chỉ vì path khớp query. Explicit user memory vẫn có
        # quyền ưu tiên cao nhất; observed/inferred cần evidence thật.
        records: list[ProfileRecord] = []
        admission_rejected: list[str] = []
        for record in self.profile_store.all():
            if not record.scope.matches(active_scope):
                continue
            if not include_sensitive and record.sensitivity in {"private", "sensitive"}:
                admission_rejected.append(f"{record.path}:sensitive")
                continue
            decision = self.admission_policy.decide_profile_record(record)
            if not decision.admitted:
                admission_rejected.append(f"{record.path}:{decision.reason}")
                continue
            records.append(record)
        q_vec = self._query_vector(query)
        # Batch hóa recall: gom hết document candidate rồi gọi embed_documents MỘT
        # LẦN (qua _dense_similarities) thay vì embed_document từng cái trong _score.
        if q_vec:
            docs = [f"{record.path}: {record.value}" for record in records]
            dense_scores = self._dense_similarities(q_vec, docs)
        else:
            dense_scores = [0.0] * len(records)

        scored: list[tuple[float, ProfileRecord, str]] = []
        for i, record in enumerate(records):
            dense = dense_scores[i] if q_vec else 0.0
            score, reason = self._score(
                record,
                query=query,
                domain=resolved_domain,
                dense=dense,
                active_scope=active_scope,
            )
            if score >= self.config.min_score:
                scored.append((score, record, reason))
        scored.sort(key=lambda x: x[0], reverse=True)

        items: list[ProfileContextItem] = []
        clarifications: list[str] = []
        estimated_tokens = 0
        for score, record, reason in scored[: self.config.max_items * 2]:
            family = self.profile_store.get(record.path, scope=record.scope, include_inactive=True)
            decision = self.clarification_policy.decide(family)
            if decision.mode == "omit":
                continue
            text = f"{record.path}: {record.value}"
            cost = max(8, len(text) // 4)
            if items and estimated_tokens + cost > int(budget * 0.68):
                break
            items.append(ProfileContextItem(
                path=record.path,
                value=record.value,
                score=score,
                reason=reason,
                evidence_ids=record.evidence_ids,
                scope=record.scope,
                explicitness=record.explicitness,
                use_mode=decision.mode,
                uncertainty_note=decision.message,
            ))
            if decision.message:
                clarifications.append(decision.message)
            estimated_tokens += cost
            if len(items) >= self.config.max_items:
                break

        episodes = self.episode_store.search(
            query,
            limit=self.config.max_episodes,
            scope=active_scope,
            embedding_index=self.embedding_index,
        )
        # Nếu query quá chung, chỉ lấy episode gần đây (<= 7 ngày) có việc đang mở hoặc importance cao.
        if not normalize_text(query):
            now_ts_val = time.time()
            episodes = tuple(
                x for x in self.episode_store.recent(limit=self.config.max_episodes, scope=active_scope)
                if (now_ts_val - x.started_at <= 7 * 86400) and (x.open_loops or x.importance >= 0.72)
            )
        episode_recap = ""
        if self.episode_rollups is not None:
            current = time.time()
            if normalize_text(query):
                # Specific recall can rehydrate old sources, never all cold episodes.
                episodes = tuple(x for x in episodes if self.episode_rollups._public(x, include_sensitive=include_sensitive))[:2]
            else:
                episodes = self.episode_rollups.recent(scope=active_scope, now=current, include_sensitive=include_sensitive)
            episode_recap = self.episode_rollups.render(scope=active_scope, now=current, include_sensitive=include_sensitive)
            chosen = []
            remaining = max(0, budget - estimated_tokens - token_cost(episode_recap))
            for episode in episodes:
                if token_cost([episode_view(x, now=current) for x in (*chosen, episode)]) <= remaining:
                    chosen.append(episode)
            episodes = tuple(chosen)
        action_context = self.action_store.context(query, scope=active_scope) if self.action_store is not None else None

        # Gợi ý chủ động theo nhịp sinh học & mạch hội thoại (Nấc 2)
        proactive_cue = ""
        clean_q = normalize_text(query)
        is_greeting = not clean_q or clean_q in {
            "alo", "hi", "helo", "hello", "ê", "chào", "chao", "chào bạn", "ở đó ko",
            "co do ko", "có đó ko", "ơi", "eris", "bạn ơi", "hế lô", "hội thoại",
        }
        if include_proactive or is_greeting:
            proactive_cue = self.compile_proactive_cue(active_scope, session_context=session_context)

        return ProfileContextPacket(
            query=query,
            domain=resolved_domain,
            items=tuple(items),
            episodes=episodes,
            episode_recap=episode_recap,
            action_context=action_context,
            clarifications=tuple(dict.fromkeys(clarifications)),
            proactive_cue=proactive_cue,
            token_budget=budget,
            diagnostics=(
                f"profile_candidates={len(records)}",
                f"selected={len(items)}",
                f"admission_rejected={len(admission_rejected)}",
                f"domain={resolved_domain}",
                *(f"reject:{row}" for row in admission_rejected[:8]),
            ),
        )

    def compile_proactive_cue(
        self,
        active_scope: UserMemoryScope,
        *,
        now: float | None = None,
        session_context: Mapping[str, Any] | None = None,
    ) -> str:
        """Biên dịch gợi ý chủ động dựa trên nhịp thời gian, cảm xúc phiên trước, việc còn mở và quyền chủ động."""
        current_ts = float(now or time.time())
        local_tm = time.localtime(current_ts)
        hour = local_tm.tm_hour

        # 1. Nhịp sinh học thời gian (temporal context)
        if 23 <= hour or hour < 5:
            time_nudge = f"Đêm muộn ({hour:02d}:{local_tm.tm_min:02d}). Người dùng có thói quen thức khuya làm việc hoặc suy ngẫm."
        elif 5 <= hour < 11:
            time_nudge = f"Buổi sáng ({hour:02d}:{local_tm.tm_min:02d}). Bắt đầu ngày mới."
        elif 11 <= hour < 14:
            time_nudge = f"Buổi trưa ({hour:02d}:{local_tm.tm_min:02d})."
        elif 14 <= hour < 18:
            time_nudge = f"Buổi chiều ({hour:02d}:{local_tm.tm_min:02d})."
        else:
            time_nudge = f"Buổi tối ({hour:02d}:{local_tm.tm_min:02d})."

        # 2. Liền mạch trạng thái cảm xúc (emotional continuity)
        mood_records = self.profile_store.get("state.recent_emotional_state", scope=active_scope)
        if not mood_records:
            mood_records = self.profile_store.get("state.mental_state", scope=active_scope)
        recent_mood = ""
        if mood_records:
            active_m = [r for r in mood_records if r.is_current]
            if active_m:
                latest = max(active_m, key=lambda r: r.updated_at)
                recent_mood = str(latest.value or "").strip()

        # 3. Vấn đề / việc chưa hoàn tất (unresolved loops) - CHỈ xét episode trong vòng 72h
        open_loops: list[str] = []
        recent_episodes = self.episode_store.recent(limit=5, scope=active_scope)
        for ep in recent_episodes:
            if (current_ts - ep.started_at <= 72 * 3600) and ep.open_loops:
                open_loops.extend(ep.open_loops)
        open_loops = list(dict.fromkeys(open_loops))[:2]

        # 4. Quyền chủ động & mức độ chịu quấy rầy (initiative permission & interruption tolerance)
        initiative_records = (
            self.profile_store.get("relationship.initiative_permission", scope=active_scope)
            or self.profile_store.get("relationship.initiative", scope=active_scope)
            or self.profile_store.get("relationship.interruption_tolerance", scope=active_scope)
            or self.profile_store.get("communication.interruption_tolerance", scope=active_scope)
        )
        proactive_permission = ""
        if initiative_records:
            active_perms = [r for r in initiative_records if r.is_current]
            if active_perms:
                latest_perm = max(active_perms, key=lambda r: r.updated_at)
                proactive_permission = str(latest_perm.value or "").strip()

        # 5. Ngữ cảnh màn hình / Desktop session hiện tại
        screen_cue = ""
        if session_context:
            app = (
                session_context.get("active_app")
                or session_context.get("app")
                or session_context.get("active_window")
                or session_context.get("window_title")
                or ""
            )
            screen_summary = (
                session_context.get("screen_summary")
                or session_context.get("screen")
                or session_context.get("visual_observation")
                or ""
            )
            parts = []
            if app:
                parts.append(f"Cửa sổ/ứng dụng: {app}")
            if screen_summary:
                parts.append(f"Mô tả màn hình: {screen_summary}")
            if parts:
                screen_cue = " | ".join(parts)

        lines = [f"- Thời điểm: {time_nudge}"]
        if recent_mood:
            lines.append(f"- Trạng thái phiên gần nhất: {recent_mood}")
        if open_loops:
            lines.append(f"- Mạch việc dang dở: {'; '.join(open_loops)}")
        if proactive_permission:
            lines.append(
                f"- Quyền chủ động: {proactive_permission}. Người dùng đã cho phép bạn thoải mái bắt chuyện hoặc làm phiền. "
                "Hãy chủ động mở lời, chia sẻ suy nghĩ hoặc nhận xét điều bạn thấy mà không cần rụt rè lo lắng bị coi là spam."
            )
        if screen_cue:
            lines.append(f"- Quan sát màn hình thực tế: {screen_cue}. Dùng thông tin này làm chất liệu gợi mở câu chuyện một cách tự nhiên nếu thích hợp.")
        lines.append("- Định hướng: Giao tiếp tự nhiên, đồng hành chân thành theo cá tính INFJ (ấm áp, hơi trêu nhẹ, tinh tế, không gượng gạo như bot CSKH).")
        return "\n".join(lines)

    def _score(
        self,
        record: ProfileRecord,
        *,
        query: str,
        domain: str,
        dense: float,
        active_scope: UserMemoryScope,
    ) -> tuple[float, str]:
        parts = record.path.split(".")
        path_domain = parts[1] if parts[0] == "user" and len(parts) > 1 else parts[0]
        domain_score = 1.0 if path_domain == domain else _domain_affinity(domain, record.path)
        lexical = _lexical_similarity(query, f"{record.path} {record.value}")
        scope_score = 1.0 if record.scope.identity == active_scope.identity else (0.78 if record.scope.kind == "global" else 0.0)
        recency = _recency(record.last_confirmed_at)
        trust = record.trust_weight

        # Topical Relevance Gate:
        # Nếu record không thuộc nhóm persona/giao tiếp cốt lõi (communication, identity, relationship)
        # mà hoàn toàn không có liên hệ ngữ nghĩa hay từ vựng với query (domain_score <= 0.15, lexical == 0, dense < 0.20)
        # thì KHÔNG được kéo vào context của turn chỉ nhờ trust cao.
        if path_domain not in {"communication", "identity", "relationship"}:
            if domain_score <= 0.15 and lexical == 0.0 and dense < 0.20:
                return 0.0, f"no_topical_relevance (domain={domain_score:.2f})"

        score = (
            trust * 0.32
            + domain_score * 0.20
            + lexical * 0.16
            + dense * 0.12
            + scope_score * 0.06
            + recency * 0.04
        )
        reason = f"trust={trust:.2f}, domain={domain_score:.2f}, lexical={lexical:.2f}, dense={dense:.2f}"
        return max(0.0, min(1.0, score)), reason

    def _query_vector(self, query: str) -> tuple[float, ...] | None:
        if not query or self.embedding_index is None:
            return None
        embed = getattr(self.embedding_index, "embed_query", None)
        if not callable(embed):
            return None
        try:
            return tuple(float(x) for x in embed(query))
        except Exception:
            return None

    def _dense_similarities(self, query_vector: tuple[float, ...], documents: list[str]) -> list[float]:
        """Batch dense similarity: gọi embed_documents MỘT LẦN cho cả list document.

        Giữ nguyên công thức cosine của ``_dense_similarity`` (chỉ đổi cách lấy vector
        từ từng-cái sang gom-một-lần). Fallback: embedding_index None, không có
        embed_documents callable, embed lỗi, hoặc độ dài vector không khớp số document
        → tất cả 0.0 (recall dùng lexical, KHÔNG crash). Cache vẫn nằm trong
        EmbeddingIndex.embed_documents (_encode_with_cache), nên document cũ vẫn cache-hit.
        """
        if not documents:
            return []
        embed_many = getattr(self.embedding_index, "embed_documents", None)
        if self.embedding_index is None or not callable(embed_many):
            return [0.0] * len(documents)
        try:
            vectors = embed_many(documents)
            # Giữ len() TRONG biên fail-soft: provider trả None/giá trị không
            # len() được thì rơi về lexical thay vì raise làm sập recall.
            if len(vectors) != len(documents):
                return [0.0] * len(documents)
            # Vật chất hoá trước khi tính cosine để một row hỏng cũng bị bắt ở đây.
            return [self._cosine_vec(query_vector, vector) for vector in vectors]
        except Exception:
            return [0.0] * len(documents)

    def _cosine_vec(self, query_vector: tuple[float, ...], raw_vector: Iterable[float]) -> float:
        try:
            vector = tuple(float(x) for x in raw_vector)
        except Exception:
            return 0.0
        if not vector or len(vector) != len(query_vector):
            return 0.0
        dot = sum(a * b for a, b in zip(query_vector, vector, strict=False))
        na = math.sqrt(sum(a * a for a in query_vector))
        nb = math.sqrt(sum(b * b for b in vector))
        return max(0.0, dot / (na * nb)) if na and nb else 0.0

    def _dense_similarity(self, query_vector: tuple[float, ...], document: str) -> float:
        """Dense similarity cho một document (giữ cho tương thích/caller cũ)."""
        embed = getattr(self.embedding_index, "embed_document", None)
        if not callable(embed):
            return 0.0
        try:
            vector = embed(document)
        except Exception:
            return 0.0
        return self._cosine_vec(query_vector, vector)


def infer_domain(query: str) -> str:
    norm = normalize_text(query)
    groups = {
        "technical": ("code", "python", "bug", "loi", "kien truc", "embedding", "model", "api", "memory", "source", "refactor"),
        "workflow": ("file", "gui", "artifact", "ban giao", "patch", "zip", "tai"),
        "communication": ("tra loi", "noi", "viet", "giong", "tone", "ngan", "dai", "tu nhien"),
        "goals": ("muon", "du dinh", "ke hoach", "tiep theo", "muc tieu", "lam tiep"),
        "projects": ("du an", "project", "eris", "repo"),
        "identity": ("toi la ai", "ten", "gioi tinh", "que", "song o"),
        "health": ("suc khoe", "benh", "thuoc", "dau", "ngu"),
    }
    for domain, cues in groups.items():
        if any(cue in norm for cue in cues):
            return domain
    return "general"


def _domain_affinity(domain: str, path: str) -> float:
    parts = path.split(".")
    root = parts[1] if parts[0] == "user" and len(parts) > 1 else parts[0]
    affinities = {
        "technical": {"workflow": 0.55, "communication": 0.48, "projects": 0.72},
        "workflow": {"technical": 0.62, "communication": 0.48, "projects": 0.64},
        "communication": {"workflow": 0.42, "technical": 0.36},
        "goals": {"projects": 0.74, "workflow": 0.48},
        "projects": {"technical": 0.72, "goals": 0.76, "workflow": 0.52},
        "identity": {"communication": 0.30},
        "general": {"communication": 0.48, "workflow": 0.38, "goals": 0.44, "projects": 0.42, "identity": 0.44},
    }
    return affinities.get(domain, {}).get(root, 0.12)


def _lexical_similarity(query: str, text: str) -> float:
    q = set(normalize_text(query).split())
    d = set(normalize_text(text).split())
    if not q or not d:
        return 0.0
    return len(q & d) / max(1, len(q))


def _recency(timestamp: float) -> float:
    age_days = max(0.0, (time.time() - float(timestamp or 0.0)) / 86400.0)
    return 1.0 / (1.0 + age_days / 45.0)


__all__ = ["ProfileContextCompiler", "ProfileContextCompilerConfig", "infer_domain"]
