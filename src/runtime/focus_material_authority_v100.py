# -*- coding: utf-8 -*-
"""ERIS V100 - Bound Focus -> Private Mental Material Authority.

V98 đã tạo năng lượng liên tục, V99 bind năng lượng đó vào object có provenance.
V100 đóng nốt lỗ hổng còn lại của seed-era: worker sinh ``MentalMaterial`` KHÔNG
được quét toàn context rồi tự chọn "mình sẽ nghĩ về cái gì" nữa.

Production flow sau V100:

    activation V98
         -> FocusObject / BoundFocus V99
         -> FocusMaterialAuthority V100
         -> CausalMotive (runtime-owned)
         -> associative material worker
         -> MentalMaterial V45 lifecycle
         -> concern / expression urge / public authority cũ

Điểm quan trọng:
- V100 KHÔNG viết câu public, KHÔNG gọi LLM và KHÔNG cấp quyền SPEAK.
- ``private_activation`` quyết định object có đủ động lực để *nghĩ tiếp* hay không.
  ``surface_readiness`` cố ý KHÔNG dùng làm gate ở đây; Eris có thể nghĩ mạnh mà
  vẫn không muốn nói.
- Một CausalMotive luôn phải mang ``object_id`` + evidence_refs từ BoundFocus.
  Không có focus có provenance => không có generation request production.
- RandomChatSeedReservoir vẫn giữ tên/API legacy để giảm blast radius, nhưng khi
  production truyền provider V100 thì ``derive_causal_motives(context)`` không còn
  authority chọn chủ đề.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
import time
from typing import Any, Callable, Mapping, Sequence

from cortex.mental_material import CausalMotive


FOCUS_MATERIAL_AUTHORITY_SCHEMA = "eris.focus_material_authority.v100"


def _clamp01(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = default
    if not math.isfinite(number):
        number = default
    return max(0.0, min(1.0, number))


def _compact(value: Any, limit: int = 520) -> str:
    text = " ".join(str(value or "").split()).strip()
    return text if len(text) <= limit else text[: max(0, limit - 1)].rstrip() + "…"


def _seq(value: Any) -> Sequence[Any]:
    return value if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)) else ()


def _refs(values: Sequence[Any], limit: int = 12) -> tuple[str, ...]:
    output: list[str] = []
    seen: set[str] = set()
    for raw in values or ():
        value = _compact(raw, 160)
        if not value or value in seen:
            continue
        seen.add(value)
        output.append(value)
        if len(output) >= limit:
            break
    return tuple(output)


def _tag_value(tags: Sequence[Any], prefix: str, default: str = "") -> str:
    needle = str(prefix or "").strip().lower() + ":"
    for raw in tags or ():
        text = str(raw or "").strip()
        if text.lower().startswith(needle):
            return text[len(needle):].strip()
    return default


@dataclass(frozen=True, slots=True)
class FocusMaterialPolicyV100:
    cause_kind: str
    motive_kind: str
    allowed_material_kinds: tuple[str, ...]
    min_private_activation: float
    min_persistence: float
    generation_budget: int = 1
    development_required: bool = False
    allowed_development_modes: tuple[str, ...] = ()


_DEFAULT_POLICY = FocusMaterialPolicyV100(
    cause_kind="bound_focus",
    motive_kind="share_internal_material",
    allowed_material_kinds=(
        "observation", "opinion", "association", "unfinished_thought",
        "hunch", "self_reflection", "story_fragment",
    ),
    min_private_activation=0.30,
    min_persistence=0.14,
)


# Policy quyết định *hình dạng private material có thể sinh từ object đã có*.
# Nó không chọn topic và cũng không làm public policy.
_POLICIES: dict[str, FocusMaterialPolicyV100] = {
    "continue_trajectory": FocusMaterialPolicyV100(
        cause_kind="conversation_trajectory",
        motive_kind="continue_conversation_trajectory",
        allowed_material_kinds=(
            "reaction", "opinion", "association", "unfinished_thought",
            "hunch", "self_reflection", "question", "story_fragment",
        ),
        min_private_activation=0.22,
        min_persistence=0.12,
        generation_budget=2,
        development_required=True,
        allowed_development_modes=("deepen", "connect", "reinterpret", "test", "personalize"),
    ),
    "develop_concern": FocusMaterialPolicyV100(
        cause_kind="active_concern",
        motive_kind="continue_concern",
        allowed_material_kinds=(
            "unfinished_thought", "opinion", "association", "question",
            "hunch", "self_reflection", "story_fragment",
        ),
        min_private_activation=0.20,
        min_persistence=0.14,
        generation_budget=2,
        development_required=True,
        allowed_development_modes=("deepen", "contradict", "connect", "reinterpret", "test", "personalize"),
    ),
    "support_active_work": FocusMaterialPolicyV100(
        cause_kind="active_goal",
        motive_kind="support_active_work",
        allowed_material_kinds=("observation", "reaction", "association", "plan", "hunch"),
        min_private_activation=0.24,
        min_persistence=0.12,
        generation_budget=1,
    ),
    "understand_user": FocusMaterialPolicyV100(
        cause_kind="epistemic_gap",
        motive_kind="understand_user",
        allowed_material_kinds=("observation", "self_reflection", "association", "question", "small_contact"),
        # User-model gap là object dài hạn. Gate cao để tránh biến Eris thành form
        # khảo sát biết đi chỉ vì model người dùng còn mỏng.
        min_private_activation=0.48,
        min_persistence=0.24,
        generation_budget=1,
    ),
    "inspect_environment": FocusMaterialPolicyV100(
        cause_kind="environment_event",
        motive_kind="notice_environment_change",
        allowed_material_kinds=("observation", "reaction", "association", "hunch"),
        min_private_activation=0.28,
        min_persistence=0.08,
        generation_budget=1,
    ),
    "protect_user_future": FocusMaterialPolicyV100(
        cause_kind="future_evidence",
        motive_kind="protect_user_future",
        allowed_material_kinds=("observation", "reaction", "plan", "hunch", "question"),
        min_private_activation=0.30,
        min_persistence=0.12,
        generation_budget=1,
    ),
    "maintain_connection": FocusMaterialPolicyV100(
        cause_kind="social_focus",
        motive_kind="maintain_connection",
        allowed_material_kinds=("small_contact", "playful_impulse", "association", "story_fragment"),
        min_private_activation=0.36,
        min_persistence=0.20,
        generation_budget=1,
    ),
    "generic_interest": _DEFAULT_POLICY,
}


class FocusMaterialAuthorityV100:
    """Projection read-only từ BoundFocus V99 sang ``CausalMotive`` V45.

    Object này không có thread. Reservoir gọi ``causal_motives`` ngay trước một
    generation attempt, vì vậy authority luôn đọc focus field hiện tại thay vì
    giữ một queue topic thứ hai.
    """

    def __init__(
        self,
        focus_runtime: Any,
        *,
        clock: Callable[[], float] = time.time,
        logger: Any | None = None,
    ) -> None:
        self.focus_runtime = focus_runtime
        self.clock = clock
        self.logger = logger
        self._last_snapshot: dict[str, Any] = {
            "schema": FOCUS_MATERIAL_AUTHORITY_SCHEMA,
            "focus_revision": 0,
            "eligible": 0,
            "blocked": 0,
            "motives": [],
        }

    def causal_motives(
        self,
        context: Mapping[str, Any] | None = None,
        *,
        now: float | None = None,
    ) -> tuple[CausalMotive, ...]:
        """Trả motive chỉ từ BoundFocus đang sống; ``context`` không chọn topic.

        ``context`` được giữ trong signature để reservoir có thể thay provider mà
        không cần adapter. V100 cố ý không đọc recent_user_events/memory ở đây:
        nếu chúng đáng nghĩ tới, ingress phải biến chúng thành FocusObject trước.
        """

        del context  # Chứng minh bằng code rằng context scan không còn authority.
        current = float(now if now is not None else self.clock())
        snapshot_fn = getattr(self.focus_runtime, "snapshot", None)
        if not callable(snapshot_fn):
            self._remember(0, (), blocked=0)
            return ()
        try:
            snapshot = snapshot_fn(limit=32)
        except TypeError:
            snapshot = snapshot_fn()
        except Exception as exc:
            self._log("debug", "[v100 focus-material] focus snapshot degraded: %s", exc)
            self._remember(0, (), blocked=0)
            return ()
        if not isinstance(snapshot, Mapping):
            self._remember(0, (), blocked=0)
            return ()

        rows = tuple(item for item in _seq(snapshot.get("focuses")) if isinstance(item, Mapping))
        motives: list[CausalMotive] = []
        blocked = 0
        for row in rows:
            motive = self._motive_from_focus(row, now=current)
            if motive is None:
                blocked += 1
                continue
            motives.append(motive)

        # Sort chỉ để generation scheduler deterministic. Đây KHÔNG phải public
        # winner; mỗi motive vẫn giữ identity/budget riêng và có thể materialize ở
        # tick sau nếu còn sống.
        motives.sort(key=lambda item: (item.strength, item.cause_id), reverse=True)
        self._remember(int(snapshot.get("revision", 0) or 0), motives, blocked=blocked)
        return tuple(motives)

    def snapshot(self) -> dict[str, Any]:
        return dict(self._last_snapshot)

    def _motive_from_focus(self, row: Mapping[str, Any], *, now: float) -> CausalMotive | None:
        object_id = _compact(row.get("object_id", ""), 180)
        label = _compact(row.get("label", ""), 1200)
        motive_key = _compact(row.get("motive_kind", "generic_interest"), 80).lower() or "generic_interest"
        refs = _refs(tuple(_seq(row.get("evidence_refs"))) + ((f"focus:{object_id}",) if object_id else ()))
        if not object_id or not label or not refs:
            return None

        private_activation = _clamp01(row.get("private_activation", 0.0))
        persistence = _clamp01(row.get("persistence", 0.0))
        policy = _POLICIES.get(motive_key, _DEFAULT_POLICY)
        if private_activation < policy.min_private_activation or persistence < policy.min_persistence:
            return None

        tags = tuple(str(x).strip() for x in _seq(row.get("tags")) if str(x or "").strip())[:12]
        # Strength chỉ dùng private field. surface_readiness không được lén biến
        # thành cognitive energy, nếu không "muốn nói" lại quay ngược thành "nghĩ".
        strength = _clamp01(0.76 * private_activation + 0.24 * persistence)
        source = _compact(row.get("source", "unknown"), 96)
        kind = _compact(row.get("kind", "unknown"), 80)

        semantic_focus = label
        if kind == "conversation_trajectory":
            preferred = _tag_value(tags, "development_mode", "deepen") or "deepen"
            semantic_focus = _compact(
                f"{label} preferred_development={preferred}. Phát triển cùng semantic spine; "
                "không mở chủ đề ngẫu nhiên và không coi lời ERIS trước đó là factual evidence.",
                1200,
            )

        generation_budget = policy.generation_budget
        if motive_key == "develop_concern" and not (private_activation >= 0.58 and persistence >= 0.42):
            generation_budget = 1
        if motive_key == "continue_trajectory":
            try:
                tagged_budget = int(_tag_value(tags, "private_budget", "1") or 1)
            except ValueError:
                tagged_budget = 1
            generation_budget = max(1, min(policy.generation_budget, tagged_budget))

        # cause_id kế thừa identity của focus. V99 trajectory object đã gắn epoch
        # external input vào object_id nên lời ERIS tự nói không reset budget.
        cause_id = f"focus:{object_id}"
        rumination = _clamp01(private_activation * (0.45 + 0.55 * persistence)) if motive_key == "develop_concern" else 0.0
        development_required = bool(policy.development_required and persistence >= 0.22)

        return CausalMotive(
            cause_id=cause_id,
            cause_kind=policy.cause_kind,
            motive_kind=policy.motive_kind,
            semantic_focus=semantic_focus,
            why_now=(
                f"bound_focus_v100:{object_id}:kind={kind}:source={source}:"
                f"private={private_activation:.2f}:persistence={persistence:.2f}"
            ),
            strength=strength,
            source_refs=refs,
            allowed_material_kinds=policy.allowed_material_kinds,
            generation_budget=max(1, min(2, int(generation_budget))),
            relationship_modulation=0.0,
            rumination_attractor=rumination,
            rumination_awareness=persistence if motive_key == "develop_concern" else 0.0,
            development_required=development_required,
            allowed_development_modes=policy.allowed_development_modes if development_required else (),
        )

    def _remember(self, focus_revision: int, motives: Sequence[CausalMotive], *, blocked: int) -> None:
        self._last_snapshot = {
            "schema": FOCUS_MATERIAL_AUTHORITY_SCHEMA,
            "focus_revision": int(focus_revision),
            "eligible": len(motives),
            "blocked": int(blocked),
            "authority": "bound_focus_only",
            "context_scan_authority": False,
            "motives": [item.to_dict() for item in motives[:16]],
            "contract": (
                "Only evidence-backed BoundFocus V99 may create production associative-material motives. "
                "private_activation gates thinking; surface_readiness remains outside this authority."
            ),
        }

    def _log(self, level: str, message: str, *args: Any) -> None:
        if self.logger is None:
            return
        try:
            getattr(self.logger, level)(message, *args)
        except Exception:
            pass


__all__ = [
    "FOCUS_MATERIAL_AUTHORITY_SCHEMA",
    "FocusMaterialAuthorityV100",
    "FocusMaterialPolicyV100",
]
