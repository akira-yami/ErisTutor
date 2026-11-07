# -*- coding: utf-8 -*-
"""Pure reducer cho Identity Kernel.

StateReducer gọi lớp này trong cùng single-writer thread. File riêng để phần
"ERIS trở thành ai" không bị trộn vào logic TTS/tool/relationship cơ học.
"""

from __future__ import annotations

import time
import hashlib
from dataclasses import replace
from typing import Any, Mapping, Sequence

from .identity import (
    DEFAULT_BELIEFS,
    DEFAULT_DEFENSES,
    DEFAULT_EMERGENT_VALUES,
    DEFAULT_MOTIVES,
    DEFAULT_RELATIONSHIP,
    DEFAULT_VALUES,
    Activation,
    BeliefRecord,
    IdentityProfile,
    IdentitySignal,
    ImprintRecord,
    PendingStrategyUse,
    PRIMARY_RELATIONSHIP_ID,
    RelationshipIdentity,
    StrategyRecord,
    clamp01,
    compact,
    signed01,
)


IDENTITY_THRESHOLDS = {
    "value": 0.82,
    "motive": 0.76,
    "defense": 0.74,
    "belief": 0.76,
    "relationship": 0.70,
    "emergent_value": 0.82,
    "imprint": 0.84,
}

IDENTITY_EVENT_CAPS = {
    "value": 0.0035,
    "motive": 0.008,
    "defense": 0.010,
    "belief": 0.020,
    "relationship": 0.024,
    "emergent_value": 0.004,
    "imprint": 0.035,
}


class IdentityKernelReducer:
    def __init__(self, *, logger: Any | None = None) -> None:
        self.logger = logger

    def add_pending_strategy(
        self,
        profile: IdentityProfile,
        pending: PendingStrategyUse,
        *,
        now: float | None = None,
    ) -> IdentityProfile:
        """Ghi việc đã dùng chiến lược, nhưng chưa tự chấm thành công."""

        if not pending.strategy_use_id or not pending.strategy:
            return profile
        if (any(item.strategy_use_id == pending.strategy_use_id for item in profile.pending_strategies)
                or any(item.get("dimension") == "strategy_use" and
                       item.get("strategy_use_id") == pending.strategy_use_id for item in profile.ledger)):
            return profile
        current_time = float(now if now is not None else time.time())
        pending_list = [
            item
            for item in profile.pending_strategies
            if item.strategy_use_id != pending.strategy_use_id
            and current_time - item.created_at <= 24 * 3600
            and pending.turn_seq - item.turn_seq <= 8
        ]
        pending_list.append(pending)
        pending_list = pending_list[-8:]

        strategies = dict(profile.strategies)
        record = strategies.get(pending.strategy, StrategyRecord())
        strategies[pending.strategy] = replace(
            record,
            uses=record.uses + 1,
            relationship_scope=pending.relationship_scope,
            last_used_turn=max(record.last_used_turn, pending.turn_seq),
        )
        ledger = list(profile.ledger)
        ledger.append(
            {
                "timestamp": current_time,
                "turn_seq": pending.turn_seq,
                "dimension": "strategy_use",
                "key": pending.strategy,
                "strategy_use_id": pending.strategy_use_id,
                "goal": pending.goal,
                "context": list(pending.context),
                "status": "pending",
            }
        )
        return IdentityProfile.from_mapping(
            {
                **profile.to_dict(),
                "version": profile.version + 1,
                "strategies": {key: value.to_dict() for key, value in strategies.items()},
                "pending_strategies": [item.to_dict() for item in pending_list],
                "ledger": ledger[-160:],
                "last_updated_at": current_time,
            }
        )

    def reduce_observation(
        self,
        profile: IdentityProfile,
        observation: Any,
        *,
        relationship_state: Mapping[str, Any] | None = None,
        now: float | None = None,
    ) -> IdentityProfile:
        current_time = float(now if now is not None else time.time())
        values = dict(profile.values)
        motives = dict(profile.motives)
        defenses = dict(profile.defenses)
        beliefs = dict(profile.beliefs)
        emergent_values = dict(profile.emergent_values)
        relationships = dict(profile.relationships)
        strategies = dict(profile.strategies)
        pending = list(profile.pending_strategies)
        imprints = list(profile.imprints)
        ledger = [dict(item) for item in profile.ledger]
        corroboration = dict(profile.corroboration)
        changed = False

        # RelationshipState cũ chỉ còn là compatibility/public summary. Đồng bộ các
        # trường trùng về Identity Kernel để panel và prompt không kể hai câu khác nhau.
        if relationship_state:
            current_rel = relationships.get(PRIMARY_RELATIONSHIP_ID, RelationshipIdentity())
            rel_payload = current_rel.to_dict()
            for key in ("relational_trust", "competence_trust", "familiarity", "closeness", "playful_safety"):
                if key in relationship_state:
                    rel_payload[key] = clamp01(relationship_state.get(key), rel_payload[key])
            rel_payload["last_turn_seq"] = max(
                current_rel.last_turn_seq,
                int(getattr(observation, "turn_seq", 0) or 0),
            )
            relationships[PRIMARY_RELATIONSHIP_ID] = RelationshipIdentity.from_mapping(rel_payload)

        consequence = getattr(observation, "consequence", None)
        # Chỉ một user turn thật mới được dùng làm hậu quả cho chiến lược trước.
        # Autonomous/force-speak không phải phản ứng của User; dùng chúng để chấm
        # điểm sẽ khiến Eris tự thưởng cho chính màn diễn của mình.
        event_kind = compact(getattr(observation, "event_kind", ""), 40).lower()
        if consequence is not None and event_kind == "chat":
            strategies, pending, entries, settled = self._settle_consequence(
                strategies,
                pending,
                consequence,
                turn_seq=int(getattr(observation, "turn_seq", 0) or 0),
                now=current_time,
            )
            if settled:
                ledger.extend(entries)
                changed = True

        active_motives = tuple(getattr(observation, "active_motives", ()) or ())[:4]
        active_defenses = tuple(getattr(observation, "activated_defenses", ()) or ())[:4]
        active_conflict = getattr(observation, "active_conflict", profile.active_conflict)
        value_choice = getattr(observation, "value_choice", profile.latest_value_choice)

        # Motive/defense/conflict là state tức thời của đúng lượt observer vừa đọc.
        # Trước đây reducer chỉ đánh dấu changed khi state mới không rỗng, nên một
        # activation cũ có thể treo trên panel qua nhiều lượt yên lặng. Xóa state
        # tức thời cũng là một thay đổi thật và phải được single-writer commit.
        transient_identity_changed = (
            active_motives != profile.active_motives
            or active_defenses != profile.active_defenses
            or active_conflict != profile.active_conflict
        )
        if transient_identity_changed or getattr(value_choice, "chosen", ""):
            changed = True

        signals = self._resolve_conflicts(tuple(getattr(observation, "identity_signals", ()) or ()))
        for signal in signals:
            dimension = signal.dimension
            threshold = IDENTITY_THRESHOLDS.get(dimension)
            if threshold is None or signal.confidence < threshold:
                continue
            evidence = tuple(dict.fromkeys((*signal.evidence, *tuple(getattr(observation, "evidence", ()) or ()))))
            if dimension not in {"motive", "defense"} and not evidence:
                continue

            applied = 0.0
            before = 0.0
            after = 0.0
            target_name = signal.key
            scale = self._signal_scale(signal)
            proposed = signal.delta * IDENTITY_EVENT_CAPS[dimension] * scale

            if dimension == "value" and target_name in DEFAULT_VALUES:
                choice = value_choice
                # Giá trị chỉ dịch khi ERIS thật sự đã chọn/vi phạm nó, không vì
                # observer thấy câu trả lời "có vibe".
                # Một lượt nhập vai/nhượng bộ chỉ là lựa chọn nhất thời.
                # Value dài hạn chỉ dịch khi observer có thể chỉ ra cả cái giá
                # Eris thực sự chấp nhận LẪN bằng chứng gắn với lựa chọn ấy.
                # Không dùng mỗi nhãn chosen để biến thói quen thành bản chất.
                if choice.confidence < 0.64 or not choice.cost or not choice.evidence:
                    continue
                if signal.delta > 0 and choice.chosen != target_name:
                    continue
                if signal.delta < 0 and target_name not in {choice.rejected, choice.chosen}:
                    continue
                before = values[target_name]
                after = clamp01(before + proposed)
                applied = after - before
                values[target_name] = after

            elif dimension == "motive" and target_name in DEFAULT_MOTIVES:
                before = motives[target_name]
                after = clamp01(before + proposed)
                applied = after - before
                motives[target_name] = after

            elif dimension == "defense" and target_name in DEFAULT_DEFENSES:
                before = defenses[target_name]
                after = clamp01(before + proposed)
                applied = after - before
                defenses[target_name] = after

            elif dimension == "belief" and self._valid_belief_key(target_name):
                if float(getattr(observation, "confidence", 0.0) or 0.0) < 0.62:
                    # Signal tự tuyên bố chắc chắn nhưng observation lại không
                    # chắc thì không được dùng nó để chỉnh niềm tin bền vững.
                    continue
                # Belief là niềm tin tương đối bền vững, KHÔNG phải "cách Eris
                # vừa cư xử". Một signal của reflection có thể chỉ là diễn giải
                # về lần thử hành vi khác thói quen. Chờ ít nhất hai *lượt*
                # độc lập có evidence, thay vì cho một lượt tự cập nhật Self.
                # Corroboration chỉ lưu thống kê, không tạo belief mới/đáp án
                # đạo đức, và reducer vẫn là nơi DUY NHẤT có quyền ghi state.
                turn_seq = int(getattr(observation, "turn_seq", 0) or 0)
                direction = "positive" if signal.delta > 0 else "negative"
                if turn_seq <= 0 or signal.delta == 0:
                    continue
                count_key = f"belief:{target_name}:{direction}"
                seen_key = f"belief_last_turn:{target_name}:{direction}"
                # Không đếm hai bản sao của cùng lời giải thích thành hai
                # trải nghiệm độc lập, dù observer gắn turn_seq khác nhau.
                # Dùng SHA256 ổn định thay vì hash() ngẫu nhiên mỗi tiến trình.
                # Lấy evidence gắn với signal trước (cụ thể nhất); một nhãn
                # tổng quan mới của observation không biến signal cũ thành mới.
                primary_evidence = tuple(signal.evidence or ()) or tuple(getattr(observation, "evidence", ()) or ())
                distinct_evidence = tuple(
                    sorted({" ".join(str(item).lower().split()) for item in primary_evidence if str(item).strip()})
                )
                evidence_fingerprint = int.from_bytes(
                    hashlib.sha256("|".join(distinct_evidence).encode("utf-8")).digest()[:8], "big"
                )
                fp_key = f"belief_evidence_hash:{target_name}:{direction}"
                if (turn_seq > corroboration.get(seen_key, 0)
                        and evidence_fingerprint != corroboration.get(fp_key, -1)):
                    corroboration[seen_key] = turn_seq
                    corroboration[fp_key] = evidence_fingerprint
                    corroboration[count_key] = min(1000, corroboration.get(count_key, 0) + 1)
                    changed = True
                if corroboration.get(count_key, 0) < 2:
                    continue
                record = beliefs.get(target_name, BeliefRecord())
                # Phòng signal trùng trong cùng một turn làm drift lặp lại.
                if record.last_turn_seq >= turn_seq:
                    continue
                before = record.strength
                after = clamp01(before + proposed)
                applied = after - before
                evidence_ids = tuple(dict.fromkeys((*record.evidence_ids, *[compact(x, 80) for x in evidence])))
                beliefs[target_name] = replace(
                    record,
                    strength=after,
                    confidence=clamp01(record.confidence + 0.015 * signal.confidence),
                    evidence_ids=evidence_ids[-8:],
                    contradictions=record.contradictions + (1 if signal.delta < 0 else 0),
                    last_turn_seq=turn_seq,
                )

            elif dimension == "relationship" and target_name in DEFAULT_RELATIONSHIP:
                scope = signal.scope or PRIMARY_RELATIONSHIP_ID
                rel = relationships.get(scope, RelationshipIdentity())
                payload = rel.to_dict()
                before = float(payload.get(target_name, DEFAULT_RELATIONSHIP[target_name]) or DEFAULT_RELATIONSHIP[target_name])
                after = clamp01(before + proposed)
                applied = after - before
                payload[target_name] = after
                payload["last_turn_seq"] = int(getattr(observation, "turn_seq", 0) or 0)
                relationships[scope] = RelationshipIdentity.from_mapping(payload)

            elif dimension == "emergent_value" and target_name in DEFAULT_EMERGENT_VALUES:
                corr_key = f"emergent_value:{target_name}:{1 if signal.delta > 0 else -1}"
                seen = corroboration.get(corr_key, 0) + 1
                corroboration[corr_key] = seen
                changed = True
                if seen < 2 or value_choice.confidence < 0.64:
                    continue
                before = emergent_values[target_name]
                after = clamp01(before + proposed)
                applied = after - before
                emergent_values[target_name] = after

            elif dimension == "imprint":
                corr_key = f"imprint:{target_name}:{1 if signal.delta > 0 else -1}"
                seen = corroboration.get(corr_key, 0) + 1
                corroboration[corr_key] = seen
                changed = True
                if seen < 3:
                    continue
                imprints, before, after = self._apply_imprint(
                    imprints,
                    signal,
                    observation=observation,
                )
                applied = after - before

            if abs(applied) < 1e-9:
                continue
            changed = True
            ledger.append(
                {
                    "timestamp": current_time,
                    "turn_seq": int(getattr(observation, "turn_seq", 0) or 0),
                    "turn_id": str(getattr(observation, "turn_id", "") or ""),
                    "dimension": dimension,
                    "key": target_name,
                    "scope": signal.scope,
                    "context": signal.context,
                    "before": round(before, 6),
                    "after": round(after, 6),
                    "applied_delta": round(applied, 6),
                    "confidence": signal.confidence,
                    "importance": signal.importance,
                    "evidence": list(evidence[:5]),
                    "self_interpretation": str(getattr(getattr(observation, "self_interpretation", None), "summary", "") or ""),
                }
            )

        if not changed:
            return profile
        return IdentityProfile.from_mapping(
            {
                **profile.to_dict(),
                "version": profile.version + 1,
                "values": values,
                "motives": motives,
                "defenses": defenses,
                "beliefs": {key: value.to_dict() for key, value in beliefs.items()},
                "emergent_values": emergent_values,
                "relationships": {key: value.to_dict() for key, value in relationships.items()},
                "strategies": {key: value.to_dict() for key, value in strategies.items()},
                "pending_strategies": [item.to_dict() for item in pending[-8:]],
                "imprints": [item.to_dict() for item in imprints[-32:]],
                "active_motives": [item.to_dict() for item in active_motives],
                "active_defenses": [item.to_dict() for item in active_defenses],
                "active_conflict": active_conflict.to_dict(),
                "latest_value_choice": value_choice.to_dict(),
                "ledger": ledger[-160:],
                "corroboration": corroboration,
                "last_updated_at": current_time,
            }
        )

    def _settle_consequence(
        self,
        strategies: dict[str, StrategyRecord],
        pending: list[PendingStrategyUse],
        consequence: Any,
        *,
        turn_seq: int,
        now: float,
    ) -> tuple[dict[str, StrategyRecord], list[PendingStrategyUse], list[dict[str, Any]], bool]:
        strategy_use_id = compact(getattr(consequence, "strategy_use_id", ""), 100)
        if not strategy_use_id:
            return strategies, pending, [], False
        matched = next((item for item in pending if item.strategy_use_id == strategy_use_id), None)
        if matched is None:
            return strategies, pending, [], False
        # New Root choices require actual, scoped next-user evidence. Inference
        # confidence alone cannot turn the assistant's prediction into reward.
        evidence = tuple(getattr(consequence, "evidence", ()) or ())
        cited_refs = {str(item).strip().removeprefix("reaction_ref:").strip() for item in evidence}
        if matched.session_id and (
            not matched.reaction_ref or not matched.reaction_text
            or not matched.turn_seq < matched.reaction_turn_seq <= turn_seq
            or matched.reaction_ref not in cited_refs
        ):
            return strategies, pending, [], False

        pending = [item for item in pending if item.strategy_use_id != strategy_use_id]
        strategy = matched.strategy
        record = strategies.get(strategy, StrategyRecord())
        confidence = clamp01(getattr(consequence, "confidence", 0.0))
        reinforcement = signed01(getattr(consequence, "reinforcement", 0.0))
        outcome = compact(getattr(consequence, "outcome", "unknown"), 120).lower() or "unknown"
        context = compact(getattr(consequence, "context", "general"), 80).lower() or "general"
        if matched.session_id and matched.context:
            context = matched.context[0]

        success = record.successes
        failure = record.failures
        unknown = record.unknown
        if confidence < 0.62 or outcome == "unknown":
            unknown += 1
            reinforcement = 0.0
        elif reinforcement > 0.08:
            success += 1
        elif reinforcement < -0.08:
            failure += 1
        else:
            unknown += 1

        shift = reinforcement * confidence * 0.08
        if matched.strategy == "root_move":
            # Unclassified moves have no shared behavioral policy to reinforce.
            # Retain the case and its interpreted outcome, not an aggregate bias.
            shift = 0.0
        contexts = dict(record.contexts)
        contexts[context] = signed01(float(contexts.get(context, 0.0) or 0.0) + shift)
        experience = {
            "assistant_ref": matched.strategy_use_id, "reaction_ref": matched.reaction_ref,
            "settled_at": now, "goal": matched.goal,
            "session_id": matched.session_id, "situation": matched.situation,
            "context": context, "expected_effect": matched.expected_effect,
            "listener_action": matched.listener_action,
            "assistant_text": matched.assistant_text, "reaction_text": matched.reaction_text,
            "outcome": outcome, "reinforcement": reinforcement, "confidence": confidence,
            "interpretation_source": "post_turn_observer", "evidence": list(evidence[:5]),
        }
        strategies[strategy] = replace(
            record,
            successes=success,
            failures=failure,
            unknown=unknown,
            utility=signed01(record.utility + shift),
            contexts=contexts,
            last_used_turn=max(record.last_used_turn, matched.turn_seq),
            experiences=tuple([*record.experiences, experience][-2:]) if matched.session_id else record.experiences,
        )
        entry = {
            "timestamp": now,
            "turn_seq": turn_seq,
            "dimension": "strategy_outcome",
            "key": strategy,
            "strategy_use_id": strategy_use_id,
            "assistant_turn_seq": matched.turn_seq,
            "goal": matched.goal,
            "context": context,
            "outcome": outcome,
            "reinforcement": reinforcement,
            "confidence": confidence,
            "evidence": list(getattr(consequence, "evidence", ()) or ())[:5],
        }
        return strategies, pending, [entry], True

    @staticmethod
    def _apply_imprint(
        imprints: list[ImprintRecord],
        signal: IdentitySignal,
        *,
        observation: Any,
    ) -> tuple[list[ImprintRecord], float, float]:
        turn_seq = int(getattr(observation, "turn_seq", 0) or 0)
        existing = next((item for item in imprints if item.theme == signal.key), None)
        before = existing.strength if existing is not None else 0.0
        after = clamp01(before + abs(signal.delta) * IDENTITY_EVENT_CAPS["imprint"] * signal.confidence)
        defense = ""
        active = tuple(getattr(observation, "activated_defenses", ()) or ())
        if active:
            defense = max(active, key=lambda item: item.strength).key
        interpretation = str(getattr(getattr(observation, "self_interpretation", None), "summary", "") or "")
        updated = ImprintRecord(
            theme=signal.key,
            valence=1.0 if signal.delta > 0 else -1.0,
            strength=after,
            generalization=clamp01((existing.generalization if existing else 0.0) + abs(signal.delta) * 0.01),
            triggers=tuple(dict.fromkeys((*((existing.triggers if existing else ())), signal.context)))[:8],
            default_interpretation=compact(interpretation or (existing.default_interpretation if existing else ""), 180),
            preferred_defense=defense or (existing.preferred_defense if existing else ""),
            evidence_turns=tuple(dict.fromkeys((*((existing.evidence_turns if existing else ())), turn_seq)))[-8:],
            last_reinforced_turn=turn_seq,
        )
        result = [item for item in imprints if item.theme != signal.key]
        result.append(updated)
        return result[-32:], before, after

    @staticmethod
    def _signal_scale(signal: IdentitySignal) -> float:
        return max(0.20, signal.importance) * (0.55 + 0.45 * signal.confidence)

    @staticmethod
    def _valid_belief_key(key: str) -> bool:
        return bool(key) and any(key.startswith(prefix) for prefix in ("self.", "user.", "world."))

    def _resolve_conflicts(self, signals: Sequence[IdentitySignal]) -> tuple[IdentitySignal, ...]:
        grouped: dict[tuple[str, str, str], list[IdentitySignal]] = {}
        for signal in signals:
            grouped.setdefault((signal.dimension, signal.key, signal.scope), []).append(signal)
        result: list[IdentitySignal] = []
        for group in grouped.values():
            signs = {1 if item.delta > 0 else -1 for item in group if item.delta != 0.0}
            if len(signs) <= 1:
                result.extend(group)
                continue
            ordered = sorted(group, key=lambda item: item.confidence, reverse=True)
            if len(ordered) >= 2 and ordered[0].confidence - ordered[1].confidence < 0.08:
                continue
            result.append(ordered[0])
        return tuple(result)


__all__ = [
    "IDENTITY_EVENT_CAPS",
    "IDENTITY_THRESHOLDS",
    "IdentityKernelReducer",
]
