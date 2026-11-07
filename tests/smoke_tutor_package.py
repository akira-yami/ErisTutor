import importlib
import pytest

MODULES = [
    "runtime.tutor",
    "runtime.tutor.contracts",
    "runtime.tutor.policy",
    "runtime.tutor.adaptive_policy",
    "runtime.tutor.reasoner",
    "runtime.tutor.model_reasoner",
    "runtime.tutor.session_store",
    "runtime.tutor.auto_path",
    "runtime.tutor.composition",
    "runtime.tutor.sources",
    "runtime.tutor.learner_context",
    "runtime.tutor.executive",
    "runtime.tutor.activities",
    "runtime.pedagogical_contracts",
    "runtime.pedagogical_plan_builder",
    "runtime.capabilities.tutor",
    "runtime.ai_orchestrated.bootstrap",
    "runtime.ai_orchestrated.model_router",
    "runtime.stage_contracts",
    "runtime.stage_output",
    "runtime.public_surface_commit",
    "runtime.voice_profile_compiler",
]


@pytest.mark.parametrize("mod", MODULES)
def test_import_tutor_module(mod):
    imported = importlib.import_module(mod)
    assert imported is not None

