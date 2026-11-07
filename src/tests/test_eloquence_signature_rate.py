# -*- coding: utf-8 -*-
"""Gói eloquence — kiểm default tỉ lệ đuôi ですね đã hạ 0.55 -> 0.25 và
mode-gating không đổi.
"""
from __future__ import annotations

from runtime import speech_persona_polish as spp


_LONG_DECLARATIVE = "Tôi đã sửa lại nhịp câu cho mượt hơn một chút rồi."


def test_signature_default_rate_is_lowered_to_025(monkeypatch) -> None:
    # Ép runtime store trả về default (không có override) để đi theo literal mới.
    monkeypatch.setattr(
        "runtime.runtime_settings_store.runtime_float",
        lambda section, name, default: default,
    )
    monkeypatch.setattr(
        "runtime.runtime_settings_store.runtime_bool",
        lambda section, name, default: default,
    )
    monkeypatch.setattr(
        "runtime.runtime_settings_store.runtime_text",
        lambda section, name, default: default,
    )
    ending, selected, rate = spp._signature_ending_policy(
        _LONG_DECLARATIVE, profile="eris", mode="neutral"
    )
    assert ending == "desu_ne"
    assert abs(rate - 0.25) < 0.001
    assert rate != 0.55
    assert isinstance(selected, bool)


def test_signature_mode_gating_unchanged(monkeypatch) -> None:
    monkeypatch.setattr(
        "runtime.runtime_settings_store.runtime_float",
        lambda section, name, default: default,
    )
    monkeypatch.setattr(
        "runtime.runtime_settings_store.runtime_bool",
        lambda section, name, default: default,
    )
    monkeypatch.setattr(
        "runtime.runtime_settings_store.runtime_text",
        lambda section, name, default: default,
    )
    # dry mode vẫn bị cap <= 0.24.
    _e, _s, dry_rate = spp._signature_ending_policy(
        _LONG_DECLARATIVE, profile="eris", mode="dry"
    )
    assert dry_rate <= 0.24

    # technical mode vẫn tắt hẳn signature.
    ending, selected, rate = spp._signature_ending_policy(
        _LONG_DECLARATIVE, profile="eris", mode="technical"
    )
    assert selected is False
    assert rate == 0.0
