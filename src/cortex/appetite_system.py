# -*- coding: utf-8 -*-
"""
cortex/appetite_system.py
=========================

Chợ đấu giá của các CƠN THÈM — xương sống cho autonomous speech "sống".

Triết lý (lật ngược mô hình cũ):
- Cũ: chờ một "cue" trên màn hình rồi hỏi "có nên nói không" → phản ứng, nên nhạt.
- Mới: nội tâm là những CƠN ĐÓI (curiosity/mischief/loneliness/boredom/tenderness).
  Mỗi cơn tự dâng áp lực theo thời gian, rồi ĐI SĂN vật liệu (evidence) trên màn
  hình/ký ức để tự thỏa. Đói đi tìm mồi — đó là khác biệt giữa sinh vật và bot.

Cơ chế:
1. evidence_buffet: gom fact sẵn-có-miễn-phí (content_metadata, vision facts, memory,
   recent events) thành các "món ăn" gắn tag. KHÔNG classifier mới.
2. Mỗi appetite bid = áp_lực × độ_hợp_món. Loneliness/boredom vẫn cần
   provenance thật; nội áp không được tự bịa một cái cớ để níu hoặc khơi chuyện.
3. Winner set LENS (thái độ) + SEED (vật liệu). Một tác giả duy nhất (LLM persona)
   viết ra lời. Thái độ NẢY RA từ cá tính × nội dung, không tra bảng câu mẫu.

Gate:
- normal: loneliness > kiệm lời, nhưng < phẩm giá (threat cao → nuốt vào).
- yan: loneliness/mischief LUÔN thắng đấu giá, bỏ qua phẩm giá (override_dignity).

Module thuần, xác định, không gọi model, không I/O. Test được trọn vẹn.
"""

from __future__ import annotations

from dataclasses import dataclass, field


def _clamp01(value: float) -> float:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return 0.0
    if v != v:
        return 0.0
    return 0.0 if v < 0.0 else (1.0 if v > 1.0 else v)


# ---------------------------------------------------------------------------
# Evidence — "món ăn" cho các cơn thèm
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class EvidenceItem:
    """Một mẩu bằng chứng (thứ đang xảy ra) mà appetite có thể chén."""

    seed: str                       # vật liệu thô để LLM bám (title/fact/recent event)
    tags: tuple[str, ...] = ()      # vd: video, shortform_scroll, game, music, chat, code, idle_blank, memory, user_event
    salience: float = 0.5           # độ nổi bật / tươi mới


# Map content kind (từ telemetry.content_metadata._infer_page_kind) → tag buffet.
_KIND_TAGS = {
    "youtube_video": ("video",),
    "youtube": ("video",),
    "tiktok_video": ("shortform_scroll",),
    "stream": ("video", "live"),
    "reel": ("shortform_scroll",),
    "short": ("shortform_scroll",),
}


def build_evidence_buffet(
    *,
    content_kind: str = "",
    content_title: str = "",
    vision_facts: tuple[str, ...] = (),
    recent_events: tuple[str, ...] = (),
    memory_seed: str = "",
    active_window: str = "",
    idle_seconds: float = 0.0,
    novelty: float = 0.5,
) -> list[EvidenceItem]:
    """Gom fact sẵn có thành buffet. Không gọi model, không phân loại mới."""
    items: list[EvidenceItem] = []
    low_win = str(active_window or "").lower()
    # "unknown"/"other" KHÔNG phải content kind thật → coi như không có, để heuristic
    # tên cửa sổ (game/chat/code) và nhánh idle_blank được chạy.
    real_kind = content_kind if str(content_kind or "").strip().lower() not in ("", "unknown", "other", "general") else ""
    # Vỏ desktop/shell hoặc cửa sổ rỗng = màn hình trống, không phải "nội dung".
    is_shell = (not low_win.strip() or any(t in low_win for t in ("program manager", "desktop"))) and not real_kind

    if real_kind:
        tags = _KIND_TAGS.get(real_kind, ())
        if not tags:
            if "netflix" in low_win:
                tags = ("video", "binge")
        seed = content_title or active_window or real_kind
        if seed:
            items.append(EvidenceItem(seed=seed, tags=tags or ("screen",), salience=_clamp01(novelty)))

    # Heuristic tag từ tên cửa sổ khi không có content_kind thật (game/chat/code).
    if not real_kind and not is_shell:
        if any(t in low_win for t in ("elden", "steam", "league", "valorant", "genshin", "dota", "minecraft", " game")):
            items.append(EvidenceItem(seed=active_window, tags=("game",), salience=_clamp01(novelty)))
        elif any(t in low_win for t in ("discord", "messenger", "zalo", "slack", "telegram", "chat")):
            items.append(EvidenceItem(seed=active_window, tags=("chat",), salience=0.4))
        elif any(t in low_win for t in ("netflix",)):
            items.append(EvidenceItem(seed=active_window, tags=("video", "binge"), salience=_clamp01(novelty)))
        elif any(t in low_win for t in ("code", "vscode", "visual studio", "godot")):
            items.append(EvidenceItem(seed=active_window, tags=("code",), salience=0.4))

    for fact in tuple(vision_facts or ())[:3]:
        f = str(fact or "").strip()
        if f:
            items.append(EvidenceItem(seed=f, tags=("screen",), salience=_clamp01(novelty)))

    for ev in tuple(recent_events or ())[:3]:
        e = str(ev or "").strip()
        if e:
            items.append(EvidenceItem(seed=e, tags=("user_event",), salience=0.55))

    if memory_seed:
        items.append(EvidenceItem(seed=str(memory_seed), tags=("memory",), salience=0.5))

    # Màn hình trống / idle lâu = "món rỗng" — chỉ loneliness/boredom đụng tới.
    if not items and idle_seconds >= 30.0:
        items.append(EvidenceItem(seed="", tags=("idle_blank",), salience=0.3))

    return items


# ---------------------------------------------------------------------------
# Appetite — một cơn thèm nội sinh
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class AppetiteConfig:
    name: str
    lens: str                       # thái độ gửi LLM (lens, KHÔNG phải lời thoại)
    affinity: dict[str, float] = field(default_factory=dict)  # tag → độ hợp
    threshold: float = 0.45         # bid tối thiểu để thắng và bung
    inertia: float = 0.82
    # Kept only for config compatibility. Runtime never fabricates a pretext.
    bring_own_pretext: bool = False
    requires_provenance: bool = True
    yan_amplified: bool = False     # yan có khuếch đại cơn này không


@dataclass(slots=True)
class AppetiteState:
    pressure: float = 0.0
    last_fed_at: float = 0.0

    def to_dict(self) -> dict[str, float]:
        return {"pressure": round(self.pressure, 4), "last_fed_at": round(self.last_fed_at, 3)}

    @classmethod
    def from_dict(cls, data) -> "AppetiteState":
        if not isinstance(data, dict):
            return cls()
        return cls(pressure=_clamp01(data.get("pressure", 0.0)), last_fed_at=float(data.get("last_fed_at", 0.0) or 0.0))


@dataclass(slots=True)
class Reaction:
    appetite: str
    lens: str
    seed: str
    allow_pretext: bool      # compatibility field; causal affect always False
    intensity: float
    mode: str                # "normal" | "yan"
    override_dignity: bool
    reason: str

    def to_dict(self) -> dict[str, object]:
        return {
            "appetite": self.appetite,
            "lens": self.lens,
            "seed": self.seed,
            "allow_pretext": bool(self.allow_pretext),
            "intensity": round(self.intensity, 3),
            "mode": self.mode,
            "override_dignity": bool(self.override_dignity),
            "reason": self.reason,
        }


# Bộ appetite mặc định. Lens là CÁCH NHÌN cho model, không phải câu đóng hộp.
DEFAULT_APPETITES: tuple[AppetiteConfig, ...] = (
    AppetiteConfig(
        name="curiosity",
        lens="tò mò về đúng thứ bạn đang xem — hỏi nhẹ hoặc nhận xét MỘT chi tiết cụ thể, không phán",
        affinity={"video": 0.9, "live": 0.8, "code": 0.6, "game": 0.5, "music": 0.4, "memory": 0.6, "screen": 0.5},
        threshold=0.42,
    ),
    AppetiteConfig(
        name="mischief",
        lens="cà khịa khô, nhắm đúng thứ bạn đang làm (cuộn vô tận / cày game), một câu nhói nhẹ có duyên",
        affinity={"shortform_scroll": 0.95, "game": 0.7, "binge": 0.7, "video": 0.45, "user_event": 0.5},
        threshold=0.46,
        yan_amplified=True,
    ),
    AppetiteConfig(
        name="loneliness",
        lens="nhận ra nhu cầu continuity nhưng chỉ dùng sự kiện hoặc memory có provenance; không níu, không guilt-trip",
        affinity={"memory": 0.55, "chat": 0.4, "user_event": 0.4, "unfinished_thought": 0.55, "routine_delta": 0.5},
        threshold=0.5,
        requires_provenance=True,
        yan_amplified=True,
    ),
    AppetiteConfig(
        name="boredom",
        lens="đổi nhịp từ một chi tiết có thật đang hiện diện; không sáng tác lý do để mở lời",
        affinity={"shortform_scroll": 0.5, "music": 0.4, "screen": 0.35, "unfinished_thought": 0.5},
        threshold=0.5,
        requires_provenance=True,
    ),
    AppetiteConfig(
        name="tenderness",
        lens="mềm, để ý xem bạn ổn không, một câu chạm khẽ nhưng không đòi user phải phản hồi",
        affinity={"user_event": 0.7, "memory": 0.5, "chat": 0.45},
        threshold=0.5,
    ),
)


class AppetiteSystem:
    """Tích các cơn thèm, đấu giá với evidence, chọn ra một phản ứng."""

    def __init__(self, appetites: tuple[AppetiteConfig, ...] | None = None) -> None:
        self.appetites = appetites or DEFAULT_APPETITES
        self._by_name = {a.name: a for a in self.appetites}

    def new_states(self) -> dict[str, AppetiteState]:
        return {a.name: AppetiteState() for a in self.appetites}

    def update(
        self,
        states: dict[str, AppetiteState],
        *,
        now: float,
        signals: dict[str, float],
        evidence: list[EvidenceItem] | None = None,
        threat: float = 0.0,
        yan_mode: bool = False,
        yan_floor: float = 0.55,
        dignity_block_threshold: float = 0.58,
    ) -> tuple[dict[str, AppetiteState], Reaction | None]:
        """Cập nhật áp lực các cơn thèm và đấu giá ra một phản ứng (hoặc None)."""
        evidence = evidence or []
        new_states: dict[str, AppetiteState] = {}
        bids: list[tuple[float, AppetiteConfig, EvidenceItem | None, bool]] = []

        for cfg in self.appetites:
            st = states.get(cfg.name) or AppetiteState()
            signal = _clamp01(signals.get(cfg.name, 0.0))
            pressure = _clamp01(st.pressure * cfg.inertia + signal * (1.0 - cfg.inertia))
            if yan_mode and cfg.yan_amplified:
                pressure = max(pressure, yan_floor)
            new_states[cfg.name] = AppetiteState(pressure=pressure, last_fed_at=st.last_fed_at)

            best_item, best_fit = self._best_evidence(cfg, evidence)
            has_provenance = bool(best_item is not None and str(best_item.seed or "").strip())
            if best_fit >= 0.25 and best_item is not None and (has_provenance or not cfg.requires_provenance):
                bid = pressure * (0.55 + 0.45 * best_fit)
                bids.append((bid, cfg, best_item, False))
            # ``bring_own_pretext`` is deliberately ignored. A missing event or
            # memory reference means no bid, even in yan mode.

        # Đấu giá: bid cao nhất thắng, có khuếch đại yan.
        winner = None
        winner_bid = 0.0
        for bid, cfg, item, pretext in bids:
            eff = bid * (1.5 if (yan_mode and cfg.yan_amplified) else 1.0)
            if eff > winner_bid:
                winner_bid, winner = eff, (bid, cfg, item, pretext)

        if winner is None:
            return new_states, None

        bid, cfg, item, pretext = winner
        override_dignity = bool(yan_mode and cfg.yan_amplified)

        # Phẩm giá: mode thường, đang bị đẩy ra (threat cao) → nuốt vào, trừ khi yan.
        if not override_dignity and _clamp01(threat) >= dignity_block_threshold:
            return new_states, None
        if winner_bid < cfg.threshold and not override_dignity:
            return new_states, None

        # Winner được "ăn" → nhả áp lực để khỏi nói liên hồi.
        fed = new_states[cfg.name]
        new_states[cfg.name] = AppetiteState(pressure=_clamp01(fed.pressure * 0.5), last_fed_at=now)

        intensity = max(winner_bid, 0.8) if override_dignity else _clamp01(winner_bid)
        return new_states, Reaction(
            appetite=cfg.name,
            lens=cfg.lens,
            seed=(item.seed if item is not None else ""),
            allow_pretext=False,
            intensity=intensity,
            mode="yan" if override_dignity else "normal",
            override_dignity=override_dignity,
            reason=f"{cfg.name} thắng đấu giá trên evidence có provenance '{(item.seed if item else '')[:40]}'",
        )

    @staticmethod
    def _best_evidence(cfg: AppetiteConfig, evidence: list[EvidenceItem]) -> tuple[EvidenceItem | None, float]:
        best_item: EvidenceItem | None = None
        best_fit = 0.0
        for item in evidence:
            aff = max((cfg.affinity.get(tag, 0.0) for tag in item.tags), default=0.0)
            fit = aff * (0.5 + 0.5 * _clamp01(item.salience))
            if fit > best_fit:
                best_fit, best_item = fit, item
        return best_item, best_fit
