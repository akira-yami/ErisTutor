from __future__ import annotations

import json
from copy import deepcopy
from dataclasses import replace
from types import SimpleNamespace
from typing import Any, Mapping

from runtime.ai_orchestrated.model_router import AIRuntimeModelRouter
from runtime.pedagogical_contracts import TeachingDirective
from runtime.pedagogical_plan_builder import TeachingPlanBuilder


class _FakePlanRouter:
    def __init__(self, payload: Any = None, *, error: Exception | None = None) -> None:
        self.payload = payload
        self.error = error
        self.calls: list[Mapping[str, Any]] = []

    def plan_teaching_episode(self, *, request_context: Mapping[str, Any]) -> Any:
        self.calls.append(request_context)
        if self.error is not None:
            raise self.error
        return self.payload


class _RecordingPlanRouter(AIRuntimeModelRouter):
    def __init__(self, result: Any) -> None:
        super().__init__(SimpleNamespace())
        self.result = result
        self.call: dict[str, Any] = {}

    def invoke_structured_worker(self, client: Any, **kwargs: Any) -> Any:
        self.call = {"client": client, **kwargs}
        return self.result


class _WriterClient:
    model_name = "unit-test-model"
    base_url = "http://localhost/v1"

    def __init__(self, payload: Mapping[str, Any] | None = None) -> None:
        self.calls: list[dict[str, Any]] = []
        self.payload = dict(payload or {
            "should_speak": True,
            "text": "One bounded teaching chunk.",
            "speech_text": "One bounded teaching chunk.",
            "claim_manifest": [],
        })

    def create_chat_completion(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(kwargs)
        return {
            "choices": [{
                "message": {
                    "content": json.dumps({
                        **self.payload,
                    })
                }
            }]
        }


def _context() -> dict[str, Any]:
    return {
        "explicit_plan_request": True,
        "request": "Help me understand dependency ordering.",
        "recent_history": ["bounded summary"],
    }


def _valid_plan_payload(*, concepts: int = 3) -> dict[str, Any]:
    return {
        "global_goal": "Explain dependency ordering clearly.",
        "concepts": [
            {
                "objective": f"Understand step {index + 1}",
                "description": f"Step {index + 1} depends on earlier foundations.",
                "prerequisite_ids": [] if index == 0 else [f"step-{index}"],
            }
            for index in range(concepts)
        ],
        "success_evidence": ["The learner can order the steps independently."],
        "misconception_hypotheses": [
            {
                "id": "reverse-order",
                "description": "The learner may reverse dependency direction.",
                "concept_id": "",
                "confidence": 0.4,
            }
        ],
        "example_policy": "Use one small dependency graph.",
    }


def _directive() -> TeachingDirective:
    return TeachingDirective(
        episode_id="teach:1",
        plan_revision=3,
        chunk_id="chunk:current",
        global_goal="Future goal that must not enter the writer projection.",
        current_objective="Distinguish a dependency from its dependent.",
        claim="A prerequisite must be available before the dependent step.",
        contrast="Order constraints are not importance rankings.",
        example_policy="one compact example",
        max_ideas=2,
        max_sentences=4,
        allow_question=True,
        stop_after_chunk=True,
        allow_autonomous_resume=True,
    )


def test_plan_builder_rejects_missing_success_evidence() -> None:
    payload = _valid_plan_payload()
    payload.pop("success_evidence")

    assert TeachingPlanBuilder(_FakePlanRouter(payload)).build(_context()) is None


def test_plan_builder_limits_concepts_and_assigns_unique_stable_ids() -> None:
    payload = _valid_plan_payload(concepts=12)

    first = TeachingPlanBuilder(_FakePlanRouter(payload)).build(_context())
    second = TeachingPlanBuilder(_FakePlanRouter(payload)).build(_context())

    assert first is not None
    assert second is not None
    assert len(first.concepts) == 8
    assert len({concept.concept_id for concept in first.concepts}) == 8
    assert [concept.concept_id for concept in first.concepts] == [
        concept.concept_id for concept in second.concepts
    ]


def test_plan_builder_drops_unknown_prerequisites() -> None:
    payload = _valid_plan_payload()
    payload["concepts"] = [
        {"id": "first", "objective": "First", "description": "First concept", "prerequisite_ids": ["missing"]},
        {"id": "second", "objective": "Second", "description": "Second concept", "prerequisite_ids": ["first"]},
        {"id": "third", "objective": "Third", "description": "Third concept", "prerequisite_ids": ["second", "unknown"]},
    ]

    plan = TeachingPlanBuilder(_FakePlanRouter(payload)).build(_context())

    assert plan is not None
    assert plan.concepts[0].prerequisites == ()
    assert plan.concepts[2].prerequisites == ("second",)
    assert plan.prerequisite_edges == (("first", "second"), ("second", "third"))


def test_plan_builder_rejects_graph_without_a_ready_first_concept() -> None:
    payload = _valid_plan_payload()
    payload["concepts"] = [
        {"id": "one", "objective": "One", "description": "One", "prerequisite_ids": ["three"]},
        {"id": "two", "objective": "Two", "description": "Two", "prerequisite_ids": ["one"]},
        {"id": "three", "objective": "Three", "description": "Three", "prerequisite_ids": ["two"]},
    ]

    assert TeachingPlanBuilder(_FakePlanRouter(payload)).build(_context()) is None


def test_plan_builder_returns_none_for_provider_or_malformed_payload() -> None:
    assert TeachingPlanBuilder(_FakePlanRouter(error=RuntimeError("offline"))).build(_context()) is None
    assert TeachingPlanBuilder(_FakePlanRouter(["not", "a", "mapping"])).build(_context()) is None


def test_plan_builder_rejects_public_prose_and_chain_of_thought_fields() -> None:
    public_prose = _valid_plan_payload()
    public_prose["public_utterance"] = "Say this to the learner."
    chain_of_thought = _valid_plan_payload()
    chain_of_thought["concepts"][0]["reasoning"] = "Hidden reasoning"

    assert TeachingPlanBuilder(_FakePlanRouter(public_prose)).build(_context()) is None
    assert TeachingPlanBuilder(_FakePlanRouter(chain_of_thought)).build(_context()) is None


def test_plan_builder_rejects_camel_case_and_separator_variants_of_forbidden_fields() -> None:
    for forbidden_key in (
        "chainOfThought",
        "Chain-Of Thought",
        "CHAIN_OF_THOUGHT",
        "publicUtterance",
        "Public-Utterance",
        "PUBLIC_UTTERANCE",
    ):
        payload = _valid_plan_payload()
        payload["concepts"][0][forbidden_key] = "raw private or public prose"

        assert TeachingPlanBuilder(_FakePlanRouter(payload)).build(_context()) is None


def test_plan_builder_rejects_non_string_plan_fields_instead_of_coercing_them() -> None:
    malformed_payloads: list[dict[str, Any]] = []

    goal = _valid_plan_payload()
    goal["global_goal"] = 42
    malformed_payloads.append(goal)

    objective = _valid_plan_payload()
    objective["concepts"][0]["objective"] = 42
    malformed_payloads.append(objective)

    description = _valid_plan_payload()
    description["concepts"][0]["description"] = 42
    malformed_payloads.append(description)

    evidence = _valid_plan_payload()
    evidence["success_evidence"] = [42]
    malformed_payloads.append(evidence)

    concept_id = _valid_plan_payload()
    concept_id["concepts"][0]["id"] = 42
    malformed_payloads.append(concept_id)

    prerequisite = _valid_plan_payload()
    prerequisite["concepts"][0]["prerequisite_ids"] = [42]
    malformed_payloads.append(prerequisite)

    hypothesis = _valid_plan_payload()
    hypothesis["misconception_hypotheses"][0]["description"] = 42
    malformed_payloads.append(hypothesis)

    example_policy = _valid_plan_payload()
    example_policy["example_policy"] = 42
    malformed_payloads.append(example_policy)

    for payload in malformed_payloads:
        assert TeachingPlanBuilder(_FakePlanRouter(deepcopy(payload))).build(_context()) is None


def test_plan_builder_does_not_call_router_without_explicit_request() -> None:
    router = _FakePlanRouter(_valid_plan_payload())

    assert TeachingPlanBuilder(router).build({"request": "ordinary chat"}) is None
    assert router.calls == []


def test_router_plan_method_uses_structured_worker_schema_and_fails_closed() -> None:
    router = _RecordingPlanRouter(_valid_plan_payload())

    result = router.plan_teaching_episode(request_context=_context())

    assert result["global_goal"]
    assert router.call["name"] == "eris_teaching_plan"
    assert router.call["schema"]["additionalProperties"] is False
    assert set(router.call["schema"]["required"]) == {
        "global_goal", "concepts", "success_evidence", "misconception_hypotheses", "example_policy"
    }
    schema_text = json.dumps(router.call["schema"])
    assert "utterance" not in schema_text
    assert "reasoning" not in schema_text

    router.result = {"global_goal": "missing required fields"}
    assert router.plan_teaching_episode(request_context=_context()) == {}
    malformed_nested = _valid_plan_payload()
    malformed_nested["concepts"][0].pop("objective")
    router.result = malformed_nested
    assert router.plan_teaching_episode(request_context=_context()) == {}
    router.result = ["provider failure"]
    assert router.plan_teaching_episode(request_context=_context()) == {}


def test_router_plan_method_bounds_nested_request_context() -> None:
    router = _RecordingPlanRouter(_valid_plan_payload())
    context = {
        "request": "x" * 5000,
        "recent_history": [{"summary": "y" * 5000}] * 20,
    }

    router.plan_teaching_episode(request_context=context)

    projected = router.call["payload"]["explicit_plan_request"]
    assert len(projected["request"]) == 1200
    assert len(projected["recent_history"]) == 12
    assert len(projected["recent_history"][0]["summary"]) == 1200


def test_writer_receives_bounded_current_directive_and_stop_rule() -> None:
    client = _WriterClient()
    router = AIRuntimeModelRouter(SimpleNamespace(client=client, fallback_client=None))
    router._run_inference = lambda _tier, func, **_kwargs: func()

    payload = router.generate_public_reply(
        chat_history=[], user_text="Teach this step.", teaching_directive=_directive()
    )

    system = client.calls[0]["messages"][0]["content"]
    assert payload["text"] == "One bounded teaching chunk."
    assert '"episode_id": "teach:1"' in system
    assert '"plan_revision": 3' in system
    assert '"current_objective": "Distinguish a dependency from its dependent."' in system
    assert '"stop_after_chunk": true' in system
    assert "never claim that the learner understands" in system
    assert "never claim the episode is complete" in system
    assert "Future goal that must not enter" not in system
    assert "allow_autonomous_resume" not in system


def test_writer_without_directive_preserves_ordinary_request_shape() -> None:
    client = _WriterClient()
    router = AIRuntimeModelRouter(SimpleNamespace(client=client, fallback_client=None))
    router._run_inference = lambda _tier, func, **_kwargs: func()

    payload = router.generate_public_reply(chat_history=[], user_text="Ordinary chat.")

    request = client.calls[0]
    assert payload["text"] == "One bounded teaching chunk."
    assert len(request["messages"]) == 2
    assert "LOCKED TEACHING DIRECTIVE" not in request["messages"][0]["content"]
    assert request["messages"][1] == {"role": "user", "content": "Ordinary chat."}


def test_writer_rejects_teaching_reply_over_sentence_budget() -> None:
    client = _WriterClient({
        "should_speak": True,
        "text": "Dependencies impose order.",
        "speech_text": "Dependencies impose order. Importance is a separate property.",
        "claim_manifest": [],
    })
    router = AIRuntimeModelRouter(SimpleNamespace(client=client, fallback_client=None))
    router._run_inference = lambda _tier, func, **_kwargs: func()

    payload = router.generate_public_reply(
        chat_history=[],
        user_text="Teach this step.",
        teaching_directive=replace(_directive(), max_sentences=1, max_ideas=3),
    )

    assert payload == {}


def test_writer_rejects_teaching_reply_over_idea_budget() -> None:
    client = _WriterClient({
        "should_speak": True,
        "text": "Dependencies impose order; importance ranks value; timing describes execution.",
        "speech_text": "Dependencies impose order.",
        "claim_manifest": [],
    })
    router = AIRuntimeModelRouter(SimpleNamespace(client=client, fallback_client=None))
    router._run_inference = lambda _tier, func, **_kwargs: func()

    payload = router.generate_public_reply(
        chat_history=[],
        user_text="Teach this step.",
        teaching_directive=replace(_directive(), max_sentences=4, max_ideas=2),
    )

    assert payload == {}


def test_writer_rejects_teaching_reply_that_claims_controller_authority() -> None:
    forbidden_texts = (
        "You understand this concept now.",
        "The learner has understood this concept.",
        "The student has mastered this lesson.",
        "The learner understands this concept.",
        "This teaching episode is complete.",
        "This course is over.",
        "The course has finished.",
        "I finished the lesson.",
        "Next, I will teach the following concept.",
    )
    for text in forbidden_texts:
        client = _WriterClient({
            "should_speak": True,
            "text": text,
            "speech_text": text,
            "claim_manifest": [],
        })
        router = AIRuntimeModelRouter(SimpleNamespace(client=client, fallback_client=None))
        router._run_inference = lambda _tier, func, **_kwargs: func()

        payload = router.generate_public_reply(
            chat_history=[], user_text="Teach this step.", teaching_directive=_directive()
        )

        assert payload == {}


def test_writer_allows_future_tense_wording_about_the_current_chunk() -> None:
    text = "I will explain this current concept."
    client = _WriterClient({
        "should_speak": True,
        "text": text,
        "speech_text": text,
        "claim_manifest": [],
    })
    router = AIRuntimeModelRouter(SimpleNamespace(client=client, fallback_client=None))
    router._run_inference = lambda _tier, func, **_kwargs: func()

    payload = router.generate_public_reply(
        chat_history=[], user_text="Teach this step.", teaching_directive=_directive()
    )

    assert payload["text"] == text


def test_writer_authority_language_is_unchanged_without_teaching_directive() -> None:
    client = _WriterClient({
        "should_speak": True,
        "text": "This ordinary task is complete.",
        "speech_text": "This ordinary task is complete.",
        "claim_manifest": [],
    })
    router = AIRuntimeModelRouter(SimpleNamespace(client=client, fallback_client=None))
    router._run_inference = lambda _tier, func, **_kwargs: func()

    payload = router.generate_public_reply(chat_history=[], user_text="Is the task done?")

    assert payload["text"] == "This ordinary task is complete."
