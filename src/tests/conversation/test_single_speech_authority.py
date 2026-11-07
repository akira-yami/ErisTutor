# -*- coding: utf-8 -*-
from __future__ import annotations

import ast
from pathlib import Path


RUNTIME_ROOT = Path(__file__).resolve().parents[2] / "runtime"
LEGACY_WRITER_METHODS = {
    "generate_public_reply",
    "generate_autonomous_reply",
    "realize_surface_speech",
}
TEMPORARY_WRITER_MIGRATION_ALLOWLIST: set[str] = set()
TEMPORARY_DELIVERY_MIGRATION_ALLOWLIST: set[str] = set()


def _writer_port_calls(source: str) -> list[int]:
    tree = ast.parse(source)
    lines: list[int] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        if node.func.attr != "realize":
            continue
        owner = node.func.value
        is_writer_name = isinstance(owner, ast.Name) and owner.id.endswith("writer")
        is_writer_attribute = (
            isinstance(owner, ast.Attribute)
            and owner.attr.endswith("writer")
        )
        if is_writer_name or is_writer_attribute:
            lines.append(node.lineno)
    return lines


def _legacy_writer_calls(source: str) -> list[tuple[int, str]]:
    tree = ast.parse(source)
    calls = [
        (node.lineno, node.func.attr)
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in LEGACY_WRITER_METHODS
    ]
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name) or node.func.id != "getattr":
            continue
        if len(node.args) >= 2 and isinstance(node.args[1], ast.Constant) and node.args[1].value in LEGACY_WRITER_METHODS:
            calls.append((node.lineno, str(node.args[1].value)))
    return sorted(calls)


def _delivery_authority_calls(source: str) -> list[tuple[int, str]]:
    tree = ast.parse(source)
    calls: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if isinstance(node.func, ast.Attribute) and node.func.attr in {
            "_send_decision_to_body",
            "record_assistant",
        }:
            calls.append((node.lineno, node.func.attr))
            continue
        if not isinstance(node.func, ast.Attribute) or node.func.attr != "_append_chat_history":
            continue
        if node.args and isinstance(node.args[0], ast.Constant) and node.args[0].value == "assistant":
            calls.append((node.lineno, "_append_chat_history:assistant"))
    return calls


def test_architecture_scanner_rejects_external_writer_call():
    source = "def bypass(writer):\n    return writer.realize(turn=None)\n"

    assert _writer_port_calls(source) == [2]


def test_architecture_scanner_rejects_legacy_writer_entry():
    source = "def bypass(router):\n    return router.generate_public_reply(user_text='x')\n"

    assert _legacy_writer_calls(source) == [(2, "generate_public_reply")]


def test_architecture_scanner_rejects_aliased_legacy_writer_entry():
    source = "def bypass(router):\n    return getattr(router, 'realize_surface_speech', None)\n"

    assert _legacy_writer_calls(source) == [(2, "realize_surface_speech")]


def test_architecture_scanner_rejects_external_delivery_commit():
    source = "def bypass(host, ledger):\n    ledger.record_assistant('x')\n    host._send_decision_to_body('x')\n"

    assert _delivery_authority_calls(source) == [
        (2, "record_assistant"),
        (3, "_send_decision_to_body"),
    ]


def test_only_conversation_kernel_calls_writer_port():
    offenders: list[str] = []
    for path in RUNTIME_ROOT.rglob("*.py"):
        calls = _writer_port_calls(path.read_text(encoding="utf-8"))
        if calls and path.as_posix().split("/runtime/", 1)[-1] != "conversation/kernel.py":
            offenders.extend(f"{path.name}:{line}" for line in calls)

    assert offenders == []


def test_legacy_writer_calls_are_confined_to_adapter_or_named_migration_sites():
    offenders: list[str] = []
    observed_migration_sites: set[str] = set()
    for path in RUNTIME_ROOT.rglob("*.py"):
        calls = _legacy_writer_calls(path.read_text(encoding="utf-8"))
        if not calls:
            continue
        relative = path.relative_to(RUNTIME_ROOT).as_posix()
        if relative == "conversation/runtime_writer.py":
            continue
        if relative in TEMPORARY_WRITER_MIGRATION_ALLOWLIST:
            observed_migration_sites.add(relative)
            continue
        offenders.extend(f"{relative}:{line}:{method}" for line, method in calls)

    assert offenders == []
    assert observed_migration_sites == TEMPORARY_WRITER_MIGRATION_ALLOWLIST


def test_delivery_authority_is_confined_to_adapter_or_named_migration_sites():
    offenders: list[str] = []
    observed_migration_sites: set[str] = set()
    for path in RUNTIME_ROOT.rglob("*.py"):
        calls = _delivery_authority_calls(path.read_text(encoding="utf-8"))
        if not calls:
            continue
        relative = path.relative_to(RUNTIME_ROOT).as_posix()
        if relative == "conversation/delivery_adapter.py":
            continue
        if relative in TEMPORARY_DELIVERY_MIGRATION_ALLOWLIST:
            observed_migration_sites.add(relative)
            continue
        offenders.extend(f"{relative}:{line}:{method}" for line, method in calls)

    assert offenders == []
    assert observed_migration_sites == TEMPORARY_DELIVERY_MIGRATION_ALLOWLIST
