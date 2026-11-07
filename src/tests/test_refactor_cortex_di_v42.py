from __future__ import annotations

from core.di import resolve_dependency
from cortex.daydream_consolidation import DaydreamConsolidator
from cortex.endogenous_drive import EndogenousDriveEngine
from cortex.internal_agenda import InternalAgendaStack
from cortex.user_context_stream import UserContextStream
from runtime.ai_orchestrated.cortex import (
    CortexAttentionSignal,
    CortexDependencies,
    CortexRuntime,
)
from runtime.ai_orchestrated.cortex_runtime import CortexRuntime as CompatibilityCortexRuntime
from runtime.model_budget_policy import ModelBudgetPolicy


class _FalseyDependency:
    def __bool__(self) -> bool:
        return False


def test_resolve_dependency_preserves_falsey_injected_object() -> None:
    provided = _FalseyDependency()
    assert resolve_dependency(provided, _FalseyDependency) is provided


def test_cortex_dependencies_keep_explicit_instances() -> None:
    stream = UserContextStream()
    drive = EndogenousDriveEngine()
    agenda = InternalAgendaStack()
    daydream = DaydreamConsolidator()
    budget = ModelBudgetPolicy()

    deps = CortexDependencies.build(
        user_context_stream=stream,
        endogenous_drive=drive,
        agenda_stack=agenda,
        daydream=daydream,
        budget_policy=budget,
    )

    assert deps.user_context_stream is stream
    assert deps.endogenous_drive is drive
    assert deps.agenda_stack is agenda
    assert deps.daydream is daydream
    assert deps.budget_policy is budget


def test_cortex_compatibility_import_points_to_new_runtime() -> None:
    assert CompatibilityCortexRuntime is CortexRuntime


def test_cortex_attention_has_no_public_speech_authority() -> None:
    payload = CortexAttentionSignal().to_dict()
    assert payload["semantic_authority"] == "none"
    assert "should_speak" not in payload
    assert "speech_text" not in payload


def test_cortex_runtime_uses_composition_object() -> None:
    deps = CortexDependencies.build()
    runtime = CortexRuntime(dependencies=deps)
    assert runtime.dependencies is deps
    assert runtime.agenda_stack is deps.agenda_stack
