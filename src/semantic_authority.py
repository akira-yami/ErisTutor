"""Single semantic authority for public chat generation.

PDF annotation, pages 12-13 and 19-20
--------------------------------------
The main chat model returns ``semantic_frame.v1`` and public wording in one
bounded call.  Legacy discourse, semantic and social classifiers were removed
at the P2 cleanup stage instead of being kept as a second source of truth.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class SemanticAuthorityPolicy:
    """Production invariants for the completed semantic cutover."""

    mode: str = "primary_model"
    primary_reasoning_effort: str = "medium"
    support_confidence_threshold: float = 0.48

    @classmethod
    def from_runtime(cls) -> "SemanticAuthorityPolicy":
        try:
            from runtime.runtime_settings_store import get_runtime_settings_store

            chat = get_runtime_settings_store().get("chat", default={}) or {}
            effort = str(chat.get("reasoning_effort", "medium") or "medium").strip().lower()
            threshold = float(chat.get("support_confidence_threshold", 0.48) or 0.48)
            return cls(
                primary_reasoning_effort=effort if effort in {"low", "medium", "high"} else "medium",
                support_confidence_threshold=max(0.05, min(0.95, threshold)),
            )
        except Exception:
            return cls()

    @classmethod
    def from_env(cls) -> "SemanticAuthorityPolicy":
        """Compatibility constructor; RuntimeSettingsStore still imports env."""

        return cls.from_runtime()

    @property
    def qwen_first(self) -> bool:
        """Old property name retained only as a harmless import compatibility shim."""

        return True

    @property
    def primary_model_first(self) -> bool:
        return True

    @property
    def allow_pre_llm_intercepts(self) -> bool:
        return False

    @property
    def allow_tutor_prepass(self) -> bool:
        return False

    @property
    def allow_relational_pre_llm_intercept(self) -> bool:
        return False

    @property
    def include_chat_act_markers(self) -> bool:
        return False

    @property
    def include_director_prompt(self) -> bool:
        return False

    @property
    def final_passthrough(self) -> bool:
        return True

    @property
    def generate_director_candidates(self) -> bool:
        return False

    @property
    def hard_only_final_guards(self) -> bool:
        return True

    @property
    def enforce_response_contract(self) -> bool:
        return False

    @property
    def low_quality_fail_open(self) -> bool:
        return True

    @property
    def bounded_refine_enabled(self) -> bool:
        return False

    @property
    def primary_uses_escalation_client(self) -> bool:
        return False

    def prompt_block(self) -> str:
        return (
            "The configured main model owns whole-turn meaning and final wording. "
            "Use history, evidence and memory as context only; return a validated semantic_frame.v1."
        )


GenerationAuthorityPolicy = SemanticAuthorityPolicy

__all__ = ["SemanticAuthorityPolicy", "GenerationAuthorityPolicy"]
