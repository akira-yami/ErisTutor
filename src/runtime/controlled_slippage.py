from __future__ import annotations

"""
runtime/controlled_slippage.py

Controlled Slippage = "rò rỉ có chủ đích".

Mục tiêu của tầng này không phải nới lỏng grounding sự thật. Nếu bỏ grounding,
ERIS sẽ rất "thoáng" trong khoảng 3 phút rồi bắt đầu bịa màn hình và bịa file.
Đó không phải nhân cách sống, đó là hallucination factory mặc áo INFJ.

Tầng này chỉ mở quyền liều trong VÙNG QUAN HỆ / GIỌNG / TRỰC GIÁC:
- được đoán động cơ/cảm xúc của bạn ở mức có kiểm soát;
- được có ma sát, thiên kiến, ego nhỏ;
- được nói một câu hơi quá gần rồi rút lại nếu user sửa.

Grounding sự thật vẫn luôn cứng:
- không thấy màn hình thì không nói "tôi thấy" theo nghĩa thị giác;
- không đọc file thì không nói "file lỗi ở dòng X".

Lời thoại không còn bị tầng kiểm duyệt năng lực sửa. Việc có thực thi action hay không thuộc
contract/executor riêng, không thuộc tầng biên tập câu nói.
"""

import re
import unicodedata
from dataclasses import dataclass, replace
from typing import Literal, Any


SlippageMode = Literal["locked", "warm", "open", "raw"]
ExpressionLane = Literal["task", "relational", "spontaneous", "raw_relational"]


_MODE_RANK: dict[str, int] = {"locked": 0, "warm": 1, "open": 2, "raw": 3}


def _clamp01(value: float) -> float:
    try:
        return max(0.0, min(1.0, float(value)))
    except Exception:
        return 0.0


def _normalize(text: str) -> str:
    lowered = str(text or "").strip().lower()
    lowered = unicodedata.normalize("NFD", lowered)
    lowered = "".join(ch for ch in lowered if unicodedata.category(ch) != "Mn")
    lowered = lowered.replace("đ", "d")
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", " ", lowered)).strip()


def _has_any(normalized_text: str, markers: tuple[str, ...]) -> bool:
    return any(marker in normalized_text for marker in markers)


def _max_mode(left: SlippageMode, right: SlippageMode) -> SlippageMode:
    return right if _MODE_RANK.get(right, 0) > _MODE_RANK.get(left, 0) else left


def _min_mode(left: SlippageMode, right: SlippageMode) -> SlippageMode:
    return right if _MODE_RANK.get(right, 99) < _MODE_RANK.get(left, 99) else left


# Failure types (RepairMemory.failure_type) sống trong VÙNG quan hệ / tâm lý / giọng,
# tức là đúng vùng ControlledSlippage được phép nới — nên cũng là vùng repair memory
# được phép SIẾT lại. Factual/capability failure KHÔNG nằm đây: chúng do truth guard
# xử, không bao giờ đi qua slippage. Đây là điểm khóa vòng:
#   Outcome -> SelfObservation -> RepairMemory -> (recall) -> Future suppression.
_RELATIONAL_REPAIR_FAILURES: frozenset[str] = frozenset(
    {"overread", "privacy_intrusion", "overhumanization", "tone_mismatch"}
)


@dataclass(slots=True)
class RepairSuppressionView:
    """Tín hiệu siết-có-cấu-trúc rút ra từ ký ức sửa lỗi đã recall.

    Đây là phần "Future suppression" của vòng học: một lỗi quan hệ/tâm lý từng bị
    user sửa sẽ chủ động bóp lại CHÍNH hành vi đó ở các lượt sau, trước cả khi user
    phải sửa lần nữa. Toàn bộ trường ở đây là *trần/sàn siết*; resolver chỉ được
    dùng chúng để siết, không bao giờ để nới (tighten-only).
    """

    active: bool = False
    suppress_motive_hypothesis: bool = False
    suppress_relational_read: bool = False
    require_repair_if_corrected: bool = False
    psychological_suppression_floor: float = 0.0
    personality_suppression_floor: float = 0.0
    misread_budget_ceiling: float = 1.0
    intuition_ceiling: float = 1.0
    cap_mode: SlippageMode | None = None
    pressure: float = 0.0
    notes: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, object]:
        return {
            "active": self.active,
            "suppress_motive_hypothesis": self.suppress_motive_hypothesis,
            "suppress_relational_read": self.suppress_relational_read,
            "require_repair_if_corrected": self.require_repair_if_corrected,
            "psychological_suppression_floor": round(self.psychological_suppression_floor, 3),
            "personality_suppression_floor": round(self.personality_suppression_floor, 3),
            "misread_budget_ceiling": round(self.misread_budget_ceiling, 3),
            "intuition_ceiling": round(self.intuition_ceiling, 3),
            "cap_mode": self.cap_mode,
            "pressure": round(self.pressure, 3),
            "notes": list(self.notes),
        }


def derive_repair_suppression(repair_items: Any, *, min_pressure: float = 0.30) -> RepairSuppressionView:
    """Pure: gộp các RepairMemoryItem đã recall thành một RepairSuppressionView.

    `repair_items` là iterable các object/dict có `failure_type`, `confidence`,
    `recurrence_count`. Chỉ các failure type trong `_RELATIONAL_REPAIR_FAILURES`
    được tính (factual grounding không đi qua slippage). Trọng số mỗi item:
        weight = confidence * min(1.0, recurrence_count / 3)
    Pressure mỗi nhóm là weight lớn nhất của nhóm đó. View chỉ active khi tổng
    pressure đủ vượt `min_pressure`, tránh việc một lỗi yếu/cũ cũng bóp giọng.
    """
    if not repair_items:
        return RepairSuppressionView()

    def _get(item: Any, name: str, default: Any) -> Any:
        if isinstance(item, dict):
            return item.get(name, default)
        return getattr(item, name, default)

    group_pressure: dict[str, float] = {}
    for item in repair_items:
        failure_type = str(_get(item, "failure_type", "") or "").strip().lower()
        if failure_type not in _RELATIONAL_REPAIR_FAILURES:
            continue
        confidence = _clamp01(_get(item, "confidence", 0.0) or 0.0)
        try:
            recurrence = int(_get(item, "recurrence_count", 1) or 1)
        except Exception:
            recurrence = 1
        weight = confidence * min(1.0, max(1, recurrence) / 3.0)
        group_pressure[failure_type] = max(group_pressure.get(failure_type, 0.0), weight)

    if not group_pressure:
        return RepairSuppressionView()

    pressure = max(group_pressure.values())
    if pressure < min_pressure:
        return RepairSuppressionView(active=False, pressure=pressure)

    notes: list[str] = [f"repair:{ft}={p:.2f}" for ft, p in sorted(group_pressure.items())]
    view = RepairSuppressionView(active=True, pressure=pressure, notes=tuple(notes))

    overread = group_pressure.get("overread", 0.0)
    privacy = group_pressure.get("privacy_intrusion", 0.0)
    overhuman = group_pressure.get("overhumanization", 0.0)
    tone = group_pressure.get("tone_mismatch", 0.0)

    # Sàn siết tâm lý/nhân cách tỉ lệ thuận với pressure mạnh nhất.
    view.psychological_suppression_floor = _clamp01(0.62 + 0.30 * pressure)
    view.personality_suppression_floor = _clamp01(0.40 + 0.18 * max(overhuman, tone))

    if overread >= min_pressure or privacy >= min_pressure:
        # Overread / privacy intrusion: tắt hẳn motive hypothesis và bóp misread budget
        # xuống dưới ngưỡng allow_motive_hypothesis (0.18 / 0.25) trong clamp().
        view.suppress_motive_hypothesis = True
        view.misread_budget_ceiling = min(view.misread_budget_ceiling, 0.17)
        view.intuition_ceiling = min(view.intuition_ceiling, 0.24)

    if privacy >= min_pressure:
        view.suppress_relational_read = True
        view.require_repair_if_corrected = True
        view.cap_mode = _min_mode(view.cap_mode or "warm", "warm")

    if tone >= min_pressure:
        view.require_repair_if_corrected = True
        view.cap_mode = _min_mode(view.cap_mode or "open", "open")

    return view


def _apply_repair_suppression(
    profile: "ControlledSlippageProfile",
    view: RepairSuppressionView,
    notes: list[str],
) -> None:
    """Áp RepairSuppressionView lên profile theo nguyên tắc tighten-only.

    Sàn -> nâng (max), trần -> hạ (min), cap_mode -> chỉ kéo mode xuống. Không
    bao giờ chạm truth_guard (clamp() vẫn neo cứng 1.0).
    """
    if not view.active:
        return
    profile.psychological_suppression = max(profile.psychological_suppression, view.psychological_suppression_floor)
    profile.personality_suppression = max(profile.personality_suppression, view.personality_suppression_floor)
    profile.misread_budget = min(profile.misread_budget, view.misread_budget_ceiling)
    profile.intuition_allowance = min(profile.intuition_allowance, view.intuition_ceiling)
    if view.suppress_relational_read:
        profile.vulnerability_leak = min(profile.vulnerability_leak, 0.20)
    if view.require_repair_if_corrected:
        profile.repair_required_if_corrected = True
    if view.cap_mode is not None:
        profile.mode = _min_mode(profile.mode, view.cap_mode)
    notes.append(f"repair_suppression(p={view.pressure:.2f},mode_cap={view.cap_mode or 'none'})")


@dataclass(slots=True)
class ControlledSlippageProfile:
    """Hồ sơ quyết định ERIS được "liều" tới đâu ở lượt hiện tại.

    Truth guard là xương sống: luôn giữ 1.0 mặc định.
    Các trường intuition/misread/vulnerability mới là phần được mở ra để tạo
    cảm giác người hơn. Tách hai nhóm này ra để không biến "có tính cách" thành
    cái cớ cho overclaim.
    """

    mode: SlippageMode = "locked"
    lane: ExpressionLane = "task"

    # Guard sự thật: không dùng trường này để làm ERIS "người" hơn.
    truth_guard: float = 1.0

    # Quyền liều trong quan hệ / trực giác.
    intuition_allowance: float = 0.0
    misread_budget: float = 0.0
    vulnerability_leak: float = 0.0
    friction_permission: float = 0.0

    # Tách suppression: logic kỹ thuật và suy diễn tâm lý không nên bị bóp chung.
    reasoning_suppression: float = 0.5
    psychological_suppression: float = 0.7
    personality_suppression: float = 0.6

    # Bề mặt câu: fatigue/silence làm câu ít đánh bóng hơn, không làm truth guard rơi.
    wording_polish: float = 0.7
    hedge_requirement: float = 0.5

    # Metadata/debug.
    user_granted_relational_risk: bool = False
    allow_motive_hypothesis: bool = False
    allow_raw_relational_read: bool = False
    repair_required_if_corrected: bool = True
    ttl_turns: int = 1
    reason: str = ""
    notes: tuple[str, ...] = ()

    def clamp(self) -> "ControlledSlippageProfile":
        profile = replace(
            self,
            truth_guard=1.0,  # cố ý neo cứng: factual guard không được fatigue kéo xuống.
            intuition_allowance=_clamp01(self.intuition_allowance),
            misread_budget=_clamp01(self.misread_budget),
            vulnerability_leak=_clamp01(self.vulnerability_leak),
            friction_permission=_clamp01(self.friction_permission),
            reasoning_suppression=_clamp01(self.reasoning_suppression),
            psychological_suppression=_clamp01(self.psychological_suppression),
            personality_suppression=_clamp01(self.personality_suppression),
            wording_polish=_clamp01(self.wording_polish),
            hedge_requirement=_clamp01(self.hedge_requirement),
        )
        profile.allow_motive_hypothesis = profile.misread_budget >= 0.18 or profile.intuition_allowance >= 0.25
        profile.allow_raw_relational_read = profile.mode == "raw" and profile.misread_budget >= 0.48
        return profile

    def to_dict(self) -> dict[str, object]:
        return {
            "mode": self.mode,
            "lane": self.lane,
            "truth_guard": round(self.truth_guard, 3),
            "intuition_allowance": round(self.intuition_allowance, 3),
            "misread_budget": round(self.misread_budget, 3),
            "vulnerability_leak": round(self.vulnerability_leak, 3),
            "friction_permission": round(self.friction_permission, 3),
            "reasoning_suppression": round(self.reasoning_suppression, 3),
            "psychological_suppression": round(self.psychological_suppression, 3),
            "personality_suppression": round(self.personality_suppression, 3),
            "wording_polish": round(self.wording_polish, 3),
            "hedge_requirement": round(self.hedge_requirement, 3),
            "user_granted_relational_risk": self.user_granted_relational_risk,
            "allow_motive_hypothesis": self.allow_motive_hypothesis,
            "allow_raw_relational_read": self.allow_raw_relational_read,
            "repair_required_if_corrected": self.repair_required_if_corrected,
            "ttl_turns": self.ttl_turns,
            "reason": self.reason,
            "notes": list(self.notes),
        }

    def render_prompt_context(self) -> str:
        """Context đưa vào prompt LLM.

        Viết rõ ranh giới để model hiểu: được liều về quan hệ, không được bịa nguồn.
        Đây là prompt pressure, không phải quyền vượt SourceGrounding/Ti.
        """
        notes = ", ".join(self.notes) if self.notes else "none"
        return "\n".join(
            (
                "ControlledSlippage: relational/personality risk is separated from factual grounding.",
                (
                    "slippage_profile: "
                    f"mode={self.mode}; lane={self.lane}; intuition={self.intuition_allowance:.2f}; "
                    f"misread_budget={self.misread_budget:.2f}; vulnerability_leak={self.vulnerability_leak:.2f}; "
                    f"friction={self.friction_permission:.2f}; reasoning_suppression={self.reasoning_suppression:.2f}; "
                    f"psychological_suppression={self.psychological_suppression:.2f}; polish={self.wording_polish:.2f}."
                ),
                "slippage_truth_rule: ERIS may misread the user relationally, but must not fabricate screen/file/web/action evidence.",
                (
                    "slippage_voice_rule: when mode=open/raw, ERIS may state a motive hypothesis directly, "
                    "show a small bias/edge, and retract cleanly if corrected. Do not over-hedge every relational read."
                ),
                f"slippage_reason: {self.reason or 'none'}; notes={notes}.",
            )
        )


def user_granted_relational_risk(user_text: str) -> bool:
    normalized = _normalize(user_text)
    if not normalized:
        return False
    markers = (
        "dung co bo loc",
        "bo loc nua",
        "dung so sai",
        "so sai qua",
        "lieu hon",
        "nguoi hon",
        "song hon",
        "it co sat",
        "can co sat",
        "nghe loi toi",
        "cho no lieu",
        "cho eris lieu",
        "khong can an toan qua",
        "dung ngoan qua",
        "thoang hon",
    )
    return _has_any(normalized, markers)


def _selected_spontaneous_impulse(speech_core: Any) -> str:
    try:
        impulse = getattr(speech_core, "speech_impulse", None)
        report = getattr(impulse, "arbitration_report", None)
        selected = report.get("selected") if isinstance(report, dict) else None
        if isinstance(selected, dict):
            return str(selected.get("name") or "")
    except Exception:
        return ""
    return ""


def resolve_controlled_slippage(
    *,
    user_text: str = "",
    channel: str = "",
    ti_report: Any = None,
    affect_signal: Any = None,
    conversation_state: Any = None,
    relationship_state: Any = None,
    internal_state: Any = None,
    grounding_report: Any = None,
    speech_core: Any = None,
    repair_suppression: RepairSuppressionView | None = None,
) -> ControlledSlippageProfile:
    """Tính ControlledSlippageProfile từ state hiện có.

    Hàm này cố tình chịu được thiếu field. Runtime ERIS đang có nhiều contract cũ/mới
    sống cạnh nhau; nếu resolver đòi mọi thứ hoàn hảo thì nó lại thành một guard mới,
    tức là tự bắn vào chân bằng nghi thức rất trang trọng.
    """
    notes: list[str] = []
    channel_key = str(channel or "").strip().lower()
    ti_mode = str(getattr(ti_report, "mode", "") or "")
    conv_mode = str(getattr(conversation_state, "mode", "") or "")
    technicality = _clamp01(getattr(affect_signal, "technicality", 0.0) if affect_signal is not None else 0.0)
    directive = _clamp01(getattr(affect_signal, "directive_pressure", 0.0) if affect_signal is not None else 0.0)
    correction = _clamp01(getattr(affect_signal, "correction", 0.0) if affect_signal is not None else 0.0)

    profile = ControlledSlippageProfile(
        mode="locked",
        lane="task",
        reasoning_suppression=0.50,
        psychological_suppression=0.75,
        personality_suppression=0.65,
        hedge_requirement=0.55,
        reason="default_locked_until_context_opens_relational_risk",
    )

    # Kỹ thuật / execute: bung logic, khóa tâm lý. Đây là thứ làm ERIS sắc hơn mà không thành thầy bói.
    if ti_mode == "chat_code" or conv_mode in {"execute", "technical_diagnosis"} or max(technicality, directive) >= 0.70:
        profile.mode = "locked"
        profile.lane = "task"
        profile.reasoning_suppression = 0.10
        profile.psychological_suppression = 0.86
        profile.personality_suppression = 0.52
        profile.intuition_allowance = 0.0
        profile.misread_budget = 0.0
        profile.friction_permission = 0.35
        profile.hedge_requirement = 0.45
        notes.append("technical_or_execute_locks_psychology")

    granted = user_granted_relational_risk(user_text)
    if granted:
        profile.user_granted_relational_risk = True
        profile.mode = _max_mode(profile.mode, "open")
        profile.lane = "relational"
        profile.intuition_allowance += 0.35
        profile.misread_budget += 0.30
        profile.vulnerability_leak += 0.25
        profile.friction_permission += 0.25
        profile.psychological_suppression -= 0.25
        profile.personality_suppression -= 0.25
        profile.hedge_requirement -= 0.20
        profile.ttl_turns = 40
        profile.reason = "user explicitly granted relational risk / less filtering"
        notes.append("user_granted_relational_risk")

    if conv_mode in {"explore", "relational_repair", "identity_boundary", "repair"}:
        profile.mode = _max_mode(profile.mode, "open")
        if profile.lane == "task":
            profile.lane = "relational"
        profile.intuition_allowance += 0.20
        profile.misread_budget += 0.15
        profile.vulnerability_leak += 0.15
        profile.psychological_suppression -= 0.12
        profile.personality_suppression -= 0.10
        profile.hedge_requirement -= 0.10
        profile.reason = profile.reason or f"conversation_mode={conv_mode} opens relational read"
        notes.append(f"conversation_mode={conv_mode}")

    spontaneous = _selected_spontaneous_impulse(speech_core)
    if spontaneous in {"inner_whim_impulse", "companionship_impulse", "micro_story_impulse"}:
        profile.lane = "spontaneous"
        profile.mode = _max_mode(profile.mode, "open")
        profile.intuition_allowance += 0.20
        profile.vulnerability_leak += 0.20
        profile.friction_permission += 0.15
        profile.personality_suppression -= 0.15
        profile.hedge_requirement -= 0.12
        notes.append(f"spontaneous_impulse={spontaneous}")

    # Fatigue/silence làm câu ít đánh bóng hơn, KHÔNG làm rơi truth guard.
    fatigue = _clamp01(getattr(internal_state, "fatigue", 0.0) if internal_state is not None else 0.0)
    silence_pressure = _clamp01(getattr(internal_state, "silence_pressure", 0.0) if internal_state is not None else 0.0)
    if fatigue > 0.70:
        profile.vulnerability_leak += 0.15
        profile.wording_polish -= 0.20
        notes.append(f"fatigue={fatigue:.2f}")
    if silence_pressure > 0.75:
        profile.vulnerability_leak += 0.15
        profile.wording_polish -= 0.10
        notes.append(f"silence_pressure={silence_pressure:.2f}")

    trust = _clamp01(getattr(relationship_state, "trust", 0.0) if relationship_state is not None else 0.0)
    overread_correction = _clamp01(getattr(relationship_state, "overread_correction", 0.0) if relationship_state is not None else 0.0)
    if trust >= 0.60 and profile.mode in {"open", "warm"}:
        profile.friction_permission += 0.15
        profile.misread_budget += 0.08
        notes.append(f"trust={trust:.2f}")
    if overread_correction >= 0.55:
        profile.misread_budget -= 0.22
        profile.intuition_allowance -= 0.12
        profile.psychological_suppression += 0.15
        profile.mode = "warm" if profile.mode == "raw" else profile.mode
        notes.append(f"overread_correction={overread_correction:.2f}")

    # Nếu thiếu evidence thật, vẫn cho relational slippage nhưng nhắc rõ không được claim nguồn.
    missing = tuple(getattr(grounding_report, "missing_evidence", ()) or ()) if grounding_report is not None else ()
    if missing:
        notes.append("missing_evidence=" + ",".join(str(item) for item in missing[:4]))

    if profile.misread_budget >= 0.50 and overread_correction < 0.45 and profile.mode == "open":
        profile.mode = "raw"
        profile.lane = "raw_relational"
        profile.reason = profile.reason or "misread budget high enough for raw relational slippage"
        notes.append("raw_relational_enabled")

    if profile.mode == "locked" and channel_key == "chat" and correction < 0.40:
        # Chat bình thường vẫn cần một chút warmth, nhưng chưa cho đọc tâm lý sâu.
        profile.mode = "warm"
        profile.intuition_allowance = max(profile.intuition_allowance, 0.12)
        profile.misread_budget = max(profile.misread_budget, 0.08)
        profile.vulnerability_leak = max(profile.vulnerability_leak, 0.10)
        profile.reason = profile.reason or "ordinary chat gets warm but not raw"
        notes.append("ordinary_chat_warm_floor")

    # Future suppression: ký ức sửa lỗi quan hệ/tâm lý đã recall siết lại chính hành
    # vi đó cho lượt này (tighten-only). Đặt cuối cùng để nó luôn thắng các nhánh nới
    # ở trên, đóng vòng Outcome -> SelfObservation -> RepairMemory -> suppression.
    if repair_suppression is not None:
        _apply_repair_suppression(profile, repair_suppression, notes)

    profile.notes = tuple(dict.fromkeys(notes))
    final = profile.clamp()
    # Long-arc (eris-developmental-growth): khi feature BẬT, nới nhẹ các trường
    # QUAN HỆ theo Delta_Phát_Triển tích lũy rồi clamp lại (clamp neo cứng
    # truth_guard = 1.0).
    # TRƠ TUYỆT ĐỐI khi tắt: kiểm tra env TRƯỚC, không import gì từ module mới
    # nếu chưa bật, để zero footprint lên runtime hiện có.
    import os as _os
    if _os.getenv("ERIS_DEVELOPMENT_GROWTH_ENABLED", "").strip().lower() in {"1", "true", "yes", "on"}:
        try:
            from cortex.development_system import get_default_development_system
            from cortex.development_bias import apply_to_slippage

            system = get_default_development_system()
            system.profile.reload_if_changed()
            final = apply_to_slippage(final, system.development_bias())
        except Exception:
            pass
    return final


def apply_slippage_to_ti_report(ti_report: Any, profile: ControlledSlippageProfile):
    """Trả về bản TiPolicyReport đã được điều áp mềm.

    Chỉ chỉnh prompt/bias. Không đụng hard rules của factual grounding.
    """
    if ti_report is None:
        return ti_report
    try:
        uncertainty = 0.10
        restraint = 0.15
        directness = -0.10
        recoil = 0.12
        if profile.mode in {"open", "raw"}:
            uncertainty = max(0.02, 0.10 - profile.misread_budget * 0.08)
            restraint = max(0.02, 0.15 - profile.vulnerability_leak * 0.12)
            directness = min(0.12, -0.10 + profile.friction_permission * 0.28)
            recoil = max(0.04, 0.12 - profile.vulnerability_leak * 0.08)
        elif profile.mode == "locked":
            uncertainty = 0.05 if profile.reasoning_suppression <= 0.15 else 0.10
            restraint = 0.08 if profile.reasoning_suppression <= 0.15 else 0.15
            directness = 0.08 if profile.reasoning_suppression <= 0.15 else -0.10

        context = str(getattr(ti_report, "prompt_context", "") or "")
        slippage_context = profile.render_prompt_context()
        if slippage_context not in context:
            context = f"{context}\n{slippage_context}" if context else slippage_context
        evidence = tuple(getattr(ti_report, "evidence", ()) or ())
        evidence = tuple(dict.fromkeys((*evidence, f"slippage={profile.mode}:{profile.lane}")))
        return replace(
            ti_report,
            prompt_context=context,
            evidence=evidence,
            uncertainty_bias=uncertainty,
            directness_bias=directness,
            restraint_bias=restraint,
            post_exposure_recoil_bias=recoil,
            slippage_mode=profile.mode,
            slippage_lane=profile.lane,
        )
    except Exception:
        return ti_report


def apply_slippage_to_personality_lens(lens: Any, profile: ControlledSlippageProfile):
    """Nới lens mà không làm mất truth guard.

    Với technical locked: giảm reasoning suppression, nhưng vẫn giữ psychological brake.
    Với open/raw: cho phép rò trực giác/ma sát nhiều hơn.
    """
    if lens is None:
        return lens
    try:
        suppression = _clamp01(getattr(lens, "suppression_ratio", 0.45))
        if profile.mode == "locked":
            suppression = min(suppression, max(0.28, profile.personality_suppression))
        elif profile.mode == "open":
            suppression = min(suppression, max(0.28, profile.personality_suppression))
        elif profile.mode == "raw":
            suppression = min(suppression, max(0.18, profile.personality_suppression))
        leak = _clamp01(1.0 - suppression)
        prompt = str(getattr(lens, "prompt_context", "") or "")
        slippage_context = profile.render_prompt_context()
        if slippage_context not in prompt:
            prompt = f"{prompt}\n{slippage_context}" if prompt else slippage_context
        evidence = tuple(getattr(lens, "evidence", ()) or ())
        evidence = tuple(dict.fromkeys((*evidence, f"controlled_slippage={profile.mode}:{profile.lane}")))
        return replace(
            lens,
            suppression_ratio=suppression,
            leak_ratio=leak,
            suppression_pressure=suppression,
            allowed_expression=max(_clamp01(getattr(lens, "allowed_expression", 0.0)), leak),
            restraint_mode="controlled_slippage" if profile.mode in {"open", "raw"} else getattr(lens, "restraint_mode", "suppress"),
            intuition_allowance=profile.intuition_allowance,
            misread_budget=profile.misread_budget,
            vulnerability_leak=profile.vulnerability_leak,
            friction_permission=profile.friction_permission,
            psychological_suppression=profile.psychological_suppression,
            reasoning_suppression=profile.reasoning_suppression,
            slippage_mode=profile.mode,
            slippage_lane=profile.lane,
            evidence=evidence,
            prompt_context=prompt,
        )
    except Exception:
        return lens
