# -*- coding: utf-8 -*-
"""expression_envelope.py — one continuous expression controller.

Trước đây "tính người" của ERIS bị mã hóa rải rác bằng hằng số chết:
``PersonalityLens.SUPPRESSION_RATIO=0.66``, trần/sàn 0.52/0.48, ngưỡng nói
cứng trong ``low_level_speech_policy``, và lệnh cấm tự thuật nhị phân trong
``micro_surface_lint``. Mỗi chỗ tự tính lại warmth/threat theo cùng một công
thức, rồi tự chọn một ngưỡng cứng. Kết quả: giọng đều, ngắn, lạnh — một kiểu
vô hồn mới.

Module này gom các tín hiệu trạng thái VỐN ĐÃ ĐƯỢC TÍNH SẴN (``process_axes``
qua ``PersistentInternalState``) thành MỘT envelope liên tục. Mọi điểm chính
sách đọc từ envelope thay vì hardcode:

    - expressiveness        → suppression + độ dài câu  (thay SUPPRESSION_RATIO/length)
    - directness_permission → cho phép tự thuật cảm xúc/hiện diện ở mức vừa
    - speak_bias            → dịch ngưỡng body_only/nói (signed)
    - warmth / threat       → nguồn canonical, hết lặp inline 3 chỗ

Đây là LEAF module: chỉ stdlib, duck-typed trên state (getattr), nên bất kỳ
consumer nào import cũng không tạo vòng import và test được offline.

Nguyên tắc: envelope KHÔNG hạ factual grounding. Nó chỉ điều biến *cách* nói
(dài/ngắn, thẳng/gián tiếp, nói/im), không cấp phép bịa sự thật.
"""

from __future__ import annotations

from dataclasses import dataclass


def _clamp(value: object, *, minimum: float = 0.0, maximum: float = 1.0) -> float:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        numeric = minimum
    if numeric != numeric:  # NaN
        numeric = minimum
    return max(minimum, min(maximum, numeric))


def _get(state: object, name: str, default: float = 0.0) -> float:
    return _clamp(getattr(state, name, default))


# Channels riêng tư — nơi ERIS được phép ấm/thẳng hơn.
_PRIVATE_CHANNELS = {"chat", "force_speak"}


@dataclass(frozen=True, slots=True)
class ExpressionEnvelope:
    """Một lát cắt liên tục của 'ERIS sẵn sàng lộ tới đâu, lúc này'.

    Tất cả trường trong [0,1] trừ ``speak_bias`` (signed) và length band (int).
    """

    warmth: float = 0.0
    threat: float = 0.0
    expressiveness: float = 0.30
    directness_permission: float = 0.0
    speak_bias: float = 0.0
    target_min_words: int = 3
    target_max_words: int = 14
    private: bool = False
    identity_reduction: bool = False

    # ── Các knob suy ra (giữ tương thích hành vi cũ của PersonalityLens) ──────

    def expression_ceiling(self) -> float:
        """Trần biểu đạt động (thay hằng số 0.52 cũ).

        Giữ NGUYÊN ngưỡng/độ nâng của Lever C cũ để wiring không đổi hành vi:
        - identity_reduction → 0.30
        - riêng tư + ấm + ít đe dọa → nâng dần về ~0.70
        - đang quan sát xa → siết về 0.48
        """
        base = 0.52
        if self.identity_reduction:
            return 0.30
        if self.private and self.warmth >= 0.45 and self.threat <= 0.30:
            lift = min(0.18, (self.warmth - 0.45) * 0.5 + 0.08)
            return max(base, min(0.70, base + lift - self.threat * 0.20))
        return base

    def suppression_floor(self) -> float:
        """Sàn suppression động (thay hằng số 0.48 cũ)."""
        if self.identity_reduction:
            return 0.52
        if self.private and self.warmth >= 0.45 and self.threat <= 0.30:
            return 0.40
        return 0.48

    def warmth_relief(self) -> float:
        """Lượng hạ suppression nhờ ấm (van ấm cũ của Lever B/C).

        Chỉ áp dụng ở channel riêng tư khi đủ ấm và ít đe dọa.
        """
        if not self.private:
            return 0.0
        if self.warmth >= 0.45 and self.threat <= 0.30:
            return min(0.16, (self.warmth - 0.45) * 0.4 + 0.08)
        return 0.0

    def default_suppression(self) -> float:
        """Mức suppression NỀN động, suy thẳng từ expressiveness.

        Thay cho hằng số chết ``PersonalityLens.SUPPRESSION_RATIO = 0.66``. Đây
        là nút chính của 'giọng đều/lạnh': khi ERIS mở (ấm/an toàn/còn sức) thì
        nền kìm nén thấp → câu cởi hơn; khi khép (mệt/kìm/đe dọa) thì nền cao →
        kín hơn.

        Dải [0.42, 0.80]. Tại state trung tính (expressiveness ≈ 0.41) cho ra
        ≈ 0.63 — sát giá trị cũ 0.66, nên hành vi nền gần như không lệch; biến
        thiên chỉ rõ ở hai đầu ấm/lạnh.
        """
        if self.identity_reduction:
            return 0.78  # bị coi như tool → khép lại, nền kìm cao.
        return max(0.42, min(0.80, 0.78 - self.expressiveness * 0.36))

    def to_debug(self) -> str:
        return (
            "expression_envelope: "
            f"warmth={self.warmth:.2f}, threat={self.threat:.2f}, "
            f"expressiveness={self.expressiveness:.2f}, "
            f"directness={self.directness_permission:.2f}, "
            f"speak_bias={self.speak_bias:+.2f}, "
            f"len={self.target_min_words}-{self.target_max_words}."
        )

    # ── Builder ───────────────────────────────────────────────────────────────

    @classmethod
    def from_state(
        cls,
        state: object,
        *,
        channel: str = "",
        trust: float = 0.0,
        identity_reduction: bool = False,
    ) -> "ExpressionEnvelope":
        """Dựng envelope từ PersistentInternalState (duck-typed).

        ``trust`` là tín hiệu quan hệ tích lũy (0..1) nếu caller có; mặc định 0.
        ``identity_reduction`` = True khi user coi ERIS như tool/waifu → lạnh lại.
        """
        warmth = _clamp(_get(state, "attachment") * 0.55 + _get(state, "tenderness") * 0.55)
        # threat giữ NGUYÊN công thức cũ (exposure_recoil/dignity_defense thường
        # vắng trên state → getattr về 0, đúng như hành vi hiện tại).
        threat = _clamp(
            _get(state, "irritation") * 0.42
            + _get(state, "protectiveness") * 0.30
            + _get(state, "exposure_recoil") * 0.26
            + _get(state, "dignity_defense") * 0.24
        )

        social_energy = _get(state, "social_energy")
        arousal = _get(state, "arousal")
        restraint = _get(state, "restraint_pressure")
        fatigue = _get(state, "fatigue")
        silence_pressure = _get(state, "silence_pressure")
        boredom = _get(state, "boredom")
        trust = _clamp(trust)
        private = str(channel or "").strip().lower() in _PRIVATE_CHANNELS

        # expressiveness: mức sẵn sàng lộ, bị kéo xuống bởi kìm nén/mệt/đe dọa.
        expressiveness = _clamp(
            0.30
            + warmth * 0.40
            + social_energy * 0.18
            + arousal * 0.10
            - restraint * 0.30
            - fatigue * 0.20
            - threat * 0.28
        )
        if identity_reduction:
            expressiveness = min(expressiveness, 0.28)

        # directness_permission: được nói THẲNG cảm xúc/hiện diện khi ấm + tin +
        # an toàn. Đây là cổng thay cho veto nhị phân trong micro_surface_lint.
        directness = _clamp(
            warmth * 0.42
            + trust * 0.30
            + social_energy * 0.14
            + (0.12 if private else 0.0)
            - threat * 0.45
        )
        if identity_reduction:
            directness = 0.0

        # speak_bias (signed): dương → hạ ngưỡng nói; âm → siết. Được CĂN ZERO ở
        # state mặc định để không làm lệch hành vi trung tính — chỉ bật lên khi
        # ERIS thực sự im lâu (silence_pressure cao) hoặc chán, và siết khi đang
        # kìm nén/mệt. social_energy chỉ điều biến nhẹ phần TRÊN ngưỡng 0.4.
        speak_bias = max(
            -0.15,
            min(
                0.20,
                silence_pressure * 0.35
                + boredom * 0.15
                + max(0.0, social_energy - 0.40) * 0.20
                - restraint * 0.20
                - fatigue * 0.10,
            ),
        )

        # Length band suy từ expressiveness: lộ nhiều → cho phép dài hơn.
        # low (~0.2): 3-10 từ; high (~0.9): ~8-30 từ.
        min_words = int(round(3 + expressiveness * 6))
        max_words = int(round(10 + expressiveness * 22))
        if max_words <= min_words:
            max_words = min_words + 2

        return cls(
            warmth=warmth,
            threat=threat,
            expressiveness=expressiveness,
            directness_permission=directness,
            speak_bias=speak_bias,
            target_min_words=min_words,
            target_max_words=max_words,
            private=private,
            identity_reduction=bool(identity_reduction),
        )
