from __future__ import annotations

import ast
from pathlib import Path

from runtime.coordinator import RuntimeCoordinator


ROOT = Path(__file__).resolve().parents[1]
BOOTSTRAP_PHASES = {
    "_bootstrap_foundation",
    "_bootstrap_memory_and_capabilities",
    "_bootstrap_hardening_and_state",
    "_bootstrap_cognition_services",
    "_bootstrap_session_and_perception",
    "_bootstrap_thought_and_ai",
}


def _class(source: str, name: str) -> ast.ClassDef:
    tree = ast.parse(source)
    return next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == name)


def _methods(node: ast.ClassDef) -> dict[str, ast.FunctionDef | ast.AsyncFunctionDef]:
    return {
        child.name: child
        for child in node.body
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef))
    }


def _size(node: ast.AST) -> int:
    return int(node.end_lineno or node.lineno) - int(node.lineno) + 1


def test_coordinator_is_a_thin_composition_root() -> None:
    source = (ROOT / "runtime/coordinator.py").read_text(encoding="utf-8")
    assert len(source.splitlines()) <= 180

    coordinator = _class(source, "RuntimeCoordinator")
    methods = _methods(coordinator)
    assert set(methods) == {"__init__"}
    assert _size(methods["__init__"]) <= 35


def test_extracted_runtime_domains_remain_available_through_mro() -> None:
    extracted = {
        "_handle_incoming",
        "_looks_like_light_tutor_research_request",
        "_advanced_effective_settings_snapshot",
        "_initialize_provider_registry",
        "_load_vision_fact_settings",
        "_run_background_observer",
        "_make_cognitive_disturbance",
        "_start_mental_process_loop",
        "_send_decision_to_body",
        "run_loop",
        "stop",
    }
    assert extracted <= set(dir(RuntimeCoordinator))


def test_bootstrap_has_bounded_explicit_phases() -> None:
    source = (ROOT / "runtime/coordinator_bootstrap.py").read_text(encoding="utf-8")
    bootstrap = _class(source, "CoordinatorBootstrapMixin")
    methods = _methods(bootstrap)
    assert BOOTSTRAP_PHASES <= set(methods)
    assert _size(methods["__init__"]) <= 70
    assert all(_size(methods[name]) <= 175 for name in BOOTSTRAP_PHASES)


def test_control_modules_use_transport_boundary_not_raw_transport() -> None:
    for relative in (
        "runtime/incoming_protocol_runtime.py",
        "runtime/settings_control_runtime.py",
    ):
        source = (ROOT / relative).read_text(encoding="utf-8")
        assert "self.transport.send_json(" not in source
        assert "self._send_runtime_json(" in source


def test_engagement_domains_are_registry_driven() -> None:
    source = (ROOT / "runtime/engagements/detector.py").read_text(encoding="utf-8")
    assert "EngagementProfileRegistry" in source
    assert "self.profiles" in source
    assert "def _domain(" not in source
    assert "def _configuration(" not in source
