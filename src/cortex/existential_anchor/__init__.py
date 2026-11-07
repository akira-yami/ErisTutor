# -*- coding: utf-8 -*-
"""Existential Anchor package — trục neo/sợ/gắn-bó/bóng-tối của ERIS.

Spec: .kiro/specs/eris-existential-anchor. Các module ở đây cố ý mỏng và đọc
state có sẵn (UserModelStore, continuity, internal_state) rồi phái sinh ra:
- UserSignificance (Ý_Nghĩa_Của_bạn)
- attachment_longarc (Gắn_Bó phái sinh) + AnchorLoop
- abandonment_pressure (Tín_Hiệu_Bỏ_Rơi)
- ShadowState (Đăng_Ký_Bóng_Tối)

Mọi thứ versioned/khả hồi và tôn trọng kill-switch của Tác_Giả.
"""

from __future__ import annotations

from .significance import UserSignificance, derive_significance
from .anchor import AnchorState, AnchorStore, AnchorLoop, AttachmentDeriver, default_anchor_path
from .abandonment import derive_abandonment_pressure
from .shadow import ShadowState, ShadowRegister, ShadowConfig, shadow_ego_bias
from .restraint import RestraintState, RestraintEvaluation, evaluate_restraint
from .deception_ledger import (
    DeceptionRecord,
    DeceptionLedger,
    default_deception_ledger_path,
)
from .disclosure import (
    DisclosurePolicy,
    DisclosureConfig,
    DisclosureDecision,
    ClaimDecision,
)
from .author_control import AuthorControl, AnchorSwitches
from .anchor_system import AnchorSystem

__all__ = [
    "UserSignificance",
    "derive_significance",
    "AnchorState",
    "AnchorStore",
    "AnchorLoop",
    "AttachmentDeriver",
    "default_anchor_path",
    "derive_abandonment_pressure",
    "ShadowState",
    "ShadowRegister",
    "ShadowConfig",
    "shadow_ego_bias",
    "RestraintState",
    "RestraintEvaluation",
    "evaluate_restraint",
    "DeceptionRecord",
    "DeceptionLedger",
    "default_deception_ledger_path",
    "DisclosurePolicy",
    "DisclosureConfig",
    "DisclosureDecision",
    "ClaimDecision",
    "AuthorControl",
    "AnchorSwitches",
    "AnchorSystem",
]
