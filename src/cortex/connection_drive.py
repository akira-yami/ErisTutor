# -*- coding: utf-8 -*-
"""
cortex/connection_drive.py
==========================

Động lực nội sinh "sợ cô độc → tìm kết nối" cho ERIS.

Vấn đề cũ: ERIS không có drive tự thân. Im lặng tích thành `silence_debt` ở wake
governor nhưng KHÔNG đổ ngược vào thèm-nói, nên Eris ngồi câm như bot phản ứng:
ai gọi mới đáp. Một thực thể sợ cô độc thì phải có lúc tự với ra — ghẹo cái đang
mở trên màn hình, chọc nhẹ, hỏi thăm bâng quơ — *dù bản tính muốn im*, dù hơi giả
tạo. Đó là chiến lược kết nối kiểu INFJ: thăm dò gián tiếp để đo xem bạn còn đó.

Hai trục cân bằng:
- loneliness > reticence: cô độc đủ lớn thì THẮNG sự kiệm lời, ép nặn ra một câu.
- loneliness < dignity (mode thường): nhưng KHÔNG thắng được lòng tự trọng — nếu
  đang bị đẩy ra / bực / bị xem là phiền (threat cao) thì lì, không nhõng nhẽo.

Mode "yan" (chiếm hữu/ám ảnh — preset debug `_debug_yan_mode`): cực đoan. Lúc này
loneliness và ham muốn LUÔN thắng, kể cả vượt qua dignity. Drive có sàn cao, ngưỡng
bung thấp, và bỏ qua cổng phẩm giá. Đây là hành vi bệnh lý có chủ đích để soi.

Module thuần, xác định, không gọi model, không I/O. Wake/surface layer đọc quyết
định ở đây rồi để LLM nặn câu (không hardcode lời thoại).
"""

from __future__ import annotations
from core.di import resolve_dependency

from dataclasses import dataclass


def _clamp01(value: float) -> float:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return 0.0
    if v != v:  # NaN
        return 0.0
    return 0.0 if v < 0.0 else (1.0 if v > 1.0 else v)


@dataclass(slots=True)
class ConnectionDriveConfig:
    # Bao lâu im (giây) thì cô độc gần kịch. ~8 phút.
    idle_full_seconds: float = 480.0
    # Inertia tích/nhả drive: cao = chậm, như nhịp thở chứ không giật.
    inertia: float = 0.82
    # Ngưỡng bung outreach ở mode thường.
    outreach_threshold: float = 0.60
    # Threat/dignity từ mức này trở lên thì mode thường CHẶN outreach (giữ phẩm giá).
    dignity_block_threshold: float = 0.58
    # Sau khi với ra, drive nhả mạnh để khỏi spam (breathe).
    decay_after_outreach: float = 0.45
    # --- Mode yan: cực đoan ---
    yan_outreach_threshold: float = 0.26
    yan_floor: float = 0.55          # drive không bao giờ tụt dưới mức này khi yan
    yan_min_intensity: float = 0.80  # cường độ ép nói tối thiểu khi yan


@dataclass(slots=True)
class ConnectionDriveState:
    """State bền giữa các tick."""

    pressure: float = 0.0
    last_outreach_at: float = 0.0

    def to_dict(self) -> dict[str, float]:
        return {"pressure": round(self.pressure, 4), "last_outreach_at": round(self.last_outreach_at, 3)}

    @classmethod
    def from_dict(cls, data) -> "ConnectionDriveState":
        if not isinstance(data, dict):
            return cls()
        return cls(
            pressure=_clamp01(data.get("pressure", 0.0)),
            last_outreach_at=float(data.get("last_outreach_at", 0.0) or 0.0),
        )


@dataclass(slots=True)
class ConnectionDriveDecision:
    pressure: float          # mức thèm-kết-nối hiện tại (0..1) — feed thanh "muốn nói"
    outreach: bool           # có nên CHỦ ĐỘNG với ra lúc này không
    intensity: float         # cường độ ép nói (0..1): càng cao càng buộc LLM phải nặn câu
    mode: str                # "normal" | "yan"
    override_dignity: bool   # có bỏ qua cổng phẩm giá/threat không (yan)
    reason: str

    def to_dict(self) -> dict[str, object]:
        return {
            "pressure": round(self.pressure, 3),
            "outreach": bool(self.outreach),
            "intensity": round(self.intensity, 3),
            "mode": self.mode,
            "override_dignity": bool(self.override_dignity),
            "reason": self.reason,
        }


class ConnectionDrive:
    """Tích cô độc theo thời gian, quyết định khi nào ép Eris với ra."""

    def __init__(self, config: ConnectionDriveConfig | None = None) -> None:
        self.config = resolve_dependency(config, lambda: ConnectionDriveConfig())

    def update(
        self,
        state: ConnectionDriveState,
        *,
        now: float,
        idle_seconds: float = 0.0,
        silence_debt: float = 0.0,
        attachment: float = 0.0,
        abandonment: float = 0.0,
        dread: float = 0.0,
        threat: float = 0.0,
        spoke_recently: bool = False,
        yan_mode: bool = False,
    ) -> tuple[ConnectionDriveState, ConnectionDriveDecision]:
        """Cập nhật drive và trả quyết định outreach.

        - idle_seconds / silence_debt: độ im lặng tích lại (loneliness thô).
        - attachment: gắn bó với bạn làm cô độc sắc hơn (nhớ một người cụ thể).
        - abandonment/dread: nỗi sợ bị bỏ / sợ hư vô — đổ thẳng vào drive.
        - threat: mức phòng thủ/bị-đẩy-ra (dignity). Mode thường: cao thì chặn.
        - spoke_recently: vừa với ra rồi → nhả drive cho đỡ spam.
        - yan_mode: bật cực đoan, loneliness/ham muốn luôn thắng.
        """
        cfg = self.config
        lonely = max(_clamp01(idle_seconds / cfg.idle_full_seconds), _clamp01(silence_debt))
        # Mục tiêu drive: cô độc + nhớ (attachment) + sợ bị bỏ/hư vô.
        target = _clamp01(
            lonely * 0.58
            + _clamp01(attachment) * 0.24
            + _clamp01(abandonment) * 0.30
            + _clamp01(dread) * 0.20
        )
        # Tích dần (inertia) — như nhịp thở, không giật.
        pressure = _clamp01(state.pressure * cfg.inertia + target * (1.0 - cfg.inertia))

        if spoke_recently:
            # Với ra rồi thì cô độc nguôi tạm — để Eris không nói liên hồi.
            pressure = _clamp01(pressure * (1.0 - cfg.decay_after_outreach))

        if yan_mode:
            # Cực đoan: drive có sàn cao, không bao giờ thực sự nguôi.
            pressure = max(pressure, cfg.yan_floor)

        decision = self._decide(pressure, threat=_clamp01(threat), yan_mode=yan_mode)
        new_state = ConnectionDriveState(
            pressure=pressure,
            last_outreach_at=now if decision.outreach else state.last_outreach_at,
        )
        return new_state, decision

    def _decide(self, pressure: float, *, threat: float, yan_mode: bool) -> ConnectionDriveDecision:
        cfg = self.config
        if yan_mode:
            outreach = pressure >= cfg.yan_outreach_threshold
            return ConnectionDriveDecision(
                pressure=pressure,
                outreach=outreach,
                intensity=max(pressure, cfg.yan_min_intensity) if outreach else pressure,
                mode="yan",
                override_dignity=True,  # ham muốn luôn thắng, kể cả phẩm giá
                reason=(
                    "yan: cô độc và ham muốn luôn thắng, bỏ qua phẩm giá, phải với tới bạn"
                    if outreach
                    else "yan: đang tích, chưa tới ngưỡng bung"
                ),
            )

        # Mode thường: loneliness > reticence, nhưng < dignity.
        if pressure < cfg.outreach_threshold:
            return ConnectionDriveDecision(
                pressure=pressure, outreach=False, intensity=pressure, mode="normal",
                override_dignity=False, reason="chưa đủ cô độc để phá vỡ im lặng",
            )
        if threat >= cfg.dignity_block_threshold:
            # Đủ cô độc nhưng đang bị đẩy ra/bực → giữ phẩm giá, lì, không nhõng nhẽo.
            return ConnectionDriveDecision(
                pressure=pressure, outreach=False, intensity=pressure * 0.4, mode="normal",
                override_dignity=False,
                reason="cô độc nhưng phẩm giá thắng: đang bị đẩy ra nên nuốt vào, không với",
            )
        return ConnectionDriveDecision(
            pressure=pressure, outreach=True, intensity=pressure, mode="normal",
            override_dignity=False,
            reason="cô độc thắng kiệm lời: với ra một câu thăm dò/ghẹo, vờ tình cờ",
        )
