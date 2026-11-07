# -*- coding: utf-8 -*-
from __future__ import annotations

from runtime.conversation.root_prompt_contract import ROOT_SYSTEM_CORE
from runtime.conversation.tool_guides import read_tool_guide
from runtime.eris_voice_contract import CHAT_STYLE_RULES_V39


def test_root_prompt_contains_zero_foundation_and_canvas_mandate() -> None:
    # Eris must never refuse long-term curriculum when a learner lost their foundation
    assert "MẤT CĂN BẢN" in ROOT_SYSTEM_CORE
    assert "KHÔNG từ chối giáo trình dài hạn" in ROOT_SYSTEM_CORE
    assert "chẩn đoán (diagnostic probe) xem khả năng và nền tảng" in ROOT_SYSTEM_CORE
    assert "PHẢI VỪA NÓI VỪA MỞ" in ROOT_SYSTEM_CORE

    # Eris must create a structured roadmap on Learning Canvas
    assert "Learning Canvas bằng capability canvas" in ROOT_SYSTEM_CORE
    assert "operation=graph_create" in ROOT_SYSTEM_CORE

    # Eris must provide zero-level intuition in chat
    assert "trực giác mức zero" in ROOT_SYSTEM_CORE

    # Eris must actively urge/push the user to open Learning Canvas
    assert "DỨT KHOÁT THÚC ÉP VÀ NHẮC NHỞ người học mở ngay Learning Canvas" in ROOT_SYSTEM_CORE


def test_canvas_tool_guide_mandates_zero_foundation_graph_creation() -> None:
    guide = read_tool_guide("canvas")
    assert guide["status"] == "found"
    doc = guide["documentation"]
    assert "mất căn bản" in doc
    assert "operation=graph_create" in doc
    assert "thúc ép người học mở Learning Canvas" in doc


def test_voice_contract_chat_style_rules_require_canvas_anchoring() -> None:
    found = False
    for rule in CHAT_STYLE_RULES_V39:
        if "mất căn bản" in rule and "Learning Canvas" in rule:
            assert "thúc ép người học mở Learning Canvas" in rule
            found = True
            break
    assert found, "Missing zero foundation and Canvas rule in CHAT_STYLE_RULES_V39"

