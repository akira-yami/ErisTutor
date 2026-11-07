# Single Speech Authority And Legacy Speech Path Removal Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Route every linguistic public output through `ConversationKernel -> RootAgent -> ConversationDecision -> PublicWriter -> ConversationDeliveryAdapter`, allow deliberate `NoReply`, and delete the V7-V11 semantic speech architecture.

**Architecture:** `ConversationKernel.handle_event()` owns the only call to a `ConversationWriter` port and authorizes one delivery ID for positive moves. Runtime adapters encapsulate the existing model writer and stage transport, while social, autonomous, opening, tutor, work, and research code can only submit typed events or capability data. `ContinuationLeaseScheduler` remains the only conversation continuation scheduler and carries mechanical state only.

**Tech Stack:** Python 3.11, frozen dataclasses, structural `Protocol` ports, `pytest`, Python `ast` architecture tests, existing stage/TTS transport.

**Spec:** `docs/superpowers/specs/2026-10-01-single-speech-authority-design.md`

## Global Constraints

- Do not add new conversation, social, tutor, voice, work, or research features.
- `ConversationDecision` is exactly `NoReply | NextConversationMove`.
- Non-user model/schema failure fails closed to `NoReply`; runtime never invents autonomous speech.
- Only `ConversationKernel` calls the public writer port.
- Only `ConversationDeliveryAdapter` initiates linguistic public delivery and commits assistant history.
- Nonverbal body-only output may bypass conversation authority only when it carries no display text, speech text, or assistant commit.
- Continuation state contains no topic, objective, intended wording, speech act, deferred beat, teaching chunk, social recovery line, or planned thread.
- Legacy speech-plan compatibility is read-only during migration and is deleted in this plan.
- Preserve existing no-TTS, fast-audio, stale-generation, and exactly-once delivery behavior.

## Review Focus

- An autonomous/opening event with no worthwhile content returns `NoReply` and makes zero writer, delivery, or assistant-ledger calls; covered in Task 1 and Task 6.
- A user turn racing a claimed continuation invalidates the old generation before writer or delivery; covered in Task 5.
- A no-TTS or very short audio callback arriving before transport setup settles exactly once; covered in Task 4.
- A writer result that becomes stale during generation cannot commit assistant history or reach TTS; covered in Task 3 and Task 4.
- A capability result that fails or arrives twice cannot speak independently or produce duplicate public delivery; covered in Task 7.

---

### Task 1: Add Deliberate Silence To The Root Contract

**Files:**
- Modify: `src/runtime/conversation/contracts.py`
- Modify: `src/runtime/conversation/agent.py`
- Modify: `src/runtime/conversation/kernel.py`
- Modify: `src/runtime/conversation/__init__.py`
- Modify: `src/tests/conversation/test_kernel_v12.py`
- Create: `src/tests/conversation/test_root_decision_v12.py`

**Interfaces:**
- Produces: `NoReply(reason: str, decision_id: str)` with `to_dict()` and `from_mapping()`.
- Produces: `ConversationDecision = NoReply | NextConversationMove`.
- Changes: `RootAgent.decide(*, event: ConversationEvent, recent_history, note, runtime_context) -> ConversationDecision`.
- Preserves: `NextConversationMove(intent, say, delivery, after, move_id)` unchanged.

- [ ] **Step 1: Write failing decision-contract tests**

Add tests named:

```python
def test_root_decision_parses_explicit_no_reply():
    ...
    assert isinstance(decision, NoReply)
    assert decision.reason == "nothing_worth_saying"

def test_non_user_model_failure_fails_closed_to_no_reply():
    ...
    assert isinstance(decision, NoReply)

def test_user_model_failure_uses_root_owned_degraded_move():
    ...
    assert isinstance(decision, NextConversationMove)
```

Assert that the autonomous failure does not contain `say`, `delivery`, or `after` in its serialized form.

- [ ] **Step 2: Run the focused tests and verify RED**

Run: `pytest -q src/tests/conversation/test_root_decision_v12.py`

Expected: collection/import failure because `NoReply` and `ConversationDecision` do not exist.

- [ ] **Step 3: Implement the decision union and update root agents**

Add explicit `kind` discriminators to serialized decisions. Replace `user_text` and `continuation` root arguments with one `ConversationEvent`; preserve bounded user text in `event.text` and pass event kind/metadata to the model payload. Parse `kind=no_reply` without filling `NextConversationMove` defaults. Use deterministic positive fallback only for `event.kind == "user_turn"`; all other failure paths return `NoReply`. Update the existing kernel compatibility methods to construct a `ConversationEvent` before calling root so the repository remains green before `handle_event()` lands in Task 2.

- [ ] **Step 4: Run focused and existing V12 tests**

Run: `pytest -q src/tests/conversation/test_root_decision_v12.py src/tests/conversation/test_kernel_v12.py`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/runtime/conversation/contracts.py src/runtime/conversation/agent.py src/runtime/conversation/kernel.py src/runtime/conversation/__init__.py src/tests/conversation/test_root_decision_v12.py src/tests/conversation/test_kernel_v12.py
git commit -m "feat: add explicit no-reply conversation decisions"
```

### Task 2: Make ConversationKernel Own Writer Invocation

**Files:**
- Create: `src/runtime/conversation/writer.py`
- Modify: `src/runtime/conversation/delivery.py`
- Modify: `src/runtime/conversation/kernel.py`
- Modify: `src/runtime/conversation/__init__.py`
- Modify: `src/tests/conversation/test_kernel_v12.py`
- Create: `src/tests/conversation/test_single_speech_authority.py`

**Interfaces:**
- Produces: `RenderedConversationMove(surface: SurfaceEnvelope, decision_payload: Mapping[str, Any], metadata: Mapping[str, Any])`.
- Produces: `ConversationWriter.realize(*, turn: KernelTurn, event: ConversationEvent, recent_history, runtime_context) -> RenderedConversationMove`.
- Produces: `ConversationDelivery.deliver(*, turn: KernelTurn, rendered: RenderedConversationMove, delivery_id: str) -> None`.
- Produces: `ConversationRunResult(event, decision, turn, rendered, delivery_id, status)`.
- Produces: `ConversationKernel.handle_event(event, *, recent_history=(), runtime_context=None) -> ConversationRunResult`.

- [ ] **Step 1: Write failing kernel authority tests**

Use recording fake ports and add tests named:

```python
def test_no_reply_never_calls_writer_or_delivery(): ...
def test_positive_move_calls_writer_and_delivery_once(): ...
def test_writer_failure_creates_no_delivery_or_continuation(): ...
def test_stale_event_cannot_reach_writer(): ...
```

Add an AST rule that treats calls to a `ConversationWriter.realize` port as legal only in `runtime/conversation/kernel.py`. The test must fail on a fixture source containing an external `.realize(...)` call, proving the scanner catches behavior rather than filenames alone.

- [ ] **Step 2: Run the focused tests and verify RED**

Run: `pytest -q src/tests/conversation/test_kernel_v12.py src/tests/conversation/test_single_speech_authority.py`

Expected: FAIL because the ports and `handle_event()` do not exist.

- [ ] **Step 3: Implement ports and `handle_event()`**

The kernel increments generation for `user_turn`, cancels prior leases, calls the root once, returns immediately for `NoReply`, and calls writer then delivery exactly once for a positive move. Generate the delivery ID inside the kernel, mark delivery started before invoking the adapter, and mark failure without scheduling a lease when writer or delivery raises.

Keep `begin_user_turn()` and `begin_continuation()` only until Tasks 4-5 migrate their callers; mark them compatibility-only and do not add new consumers.

- [ ] **Step 4: Run focused tests**

Run: `pytest -q src/tests/conversation/test_kernel_v12.py src/tests/conversation/test_single_speech_authority.py`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/runtime/conversation/writer.py src/runtime/conversation/delivery.py src/runtime/conversation/kernel.py src/runtime/conversation/__init__.py src/tests/conversation/test_kernel_v12.py src/tests/conversation/test_single_speech_authority.py
git commit -m "feat: make conversation kernel own writer invocation"
```

### Task 3: Adapt The Existing Model Writer Behind The Kernel Port

**Files:**
- Create: `src/runtime/conversation/runtime_writer.py`
- Modify: `src/runtime/ai_orchestrated/model_router.py`
- Modify: `src/runtime/ai_orchestrated/orchestrator.py`
- Modify: `src/tests/conversation/test_single_speech_authority.py`
- Create: `src/tests/conversation/test_runtime_writer_v12.py`
- Modify: `src/tests/test_pedagogical_integration.py`
- Modify: `src/tests/test_public_writer_recovery_v6.py`

**Interfaces:**
- Produces: `RuntimeConversationWriter(host).realize(*, turn, event, recent_history, runtime_context) -> RenderedConversationMove`.
- Consumes: `KernelTurn.move: NextConversationMove` as the complete semantic scope.
- Removes from callers: direct use of `generate_public_reply()`, `generate_autonomous_reply()`, and `realize_surface_speech()` as public entry points.

- [ ] **Step 1: Write failing runtime-writer and architecture tests**

Add tests asserting:

```python
def test_runtime_writer_projects_exact_root_move_into_writer_context(): ...
def test_runtime_writer_cannot_change_after_or_open_a_second_move(): ...
def test_stale_generation_discards_rendered_surface(): ...
def test_production_modules_do_not_call_legacy_public_writer_entries(): ...
```

The AST rule may allow the definitions in `model_router.py` and their single adapter call in `runtime_writer.py`. At this intermediate commit, list the exact autonomous migration call sites in `self_turn_pipeline.py` and the legacy autonomous method in `orchestrator.py` in a temporary, named allowlist; foreground orchestration must already fail. Task 6 removes those entries and asserts the allowlist is empty. Tutor and opening code do not call these router writer methods.

- [ ] **Step 2: Run focused tests and verify RED**

Run: `pytest -q src/tests/conversation/test_runtime_writer_v12.py src/tests/conversation/test_single_speech_authority.py src/tests/test_pedagogical_integration.py src/tests/test_public_writer_recovery_v6.py`

Expected: FAIL on direct orchestrator/self-turn writer calls and missing adapter.

- [ ] **Step 3: Extract the model-backed writer adapter**

Move the reusable public generation, parsing, grounding, claim validation, and bounded retry behavior needed after root selection behind `RuntimeConversationWriter`. Its request must carry `ROOT_CONVERSATION_MOVE_V12` and must not run an independent discourse/social planner. Return a rendered surface without committing history or calling transport.

Convert router-level tests to exercise the adapter when they are testing public behavior; retain direct router tests only for provider parsing and schema validation.

- [ ] **Step 4: Remove direct production writer calls and run focused tests**

Run: `pytest -q src/tests/conversation/test_runtime_writer_v12.py src/tests/conversation/test_single_speech_authority.py src/tests/test_pedagogical_integration.py src/tests/test_public_writer_recovery_v6.py`

Expected: PASS and no production caller outside `runtime_writer.py` matches a legacy public-writer entry.

- [ ] **Step 5: Commit**

```bash
git add src/runtime/conversation/runtime_writer.py src/runtime/ai_orchestrated/model_router.py src/runtime/ai_orchestrated/orchestrator.py src/tests/conversation/test_runtime_writer_v12.py src/tests/conversation/test_single_speech_authority.py src/tests/test_pedagogical_integration.py src/tests/test_public_writer_recovery_v6.py
git commit -m "refactor: place public writer behind conversation kernel"
```

### Task 4: Centralize Assistant Commit And Public Delivery

**Files:**
- Create: `src/runtime/conversation/delivery_adapter.py`
- Create: `src/runtime/conversation/bootstrap.py`
- Modify: `src/runtime/conversation/kernel.py`
- Modify: `src/runtime/conversation/__init__.py`
- Modify: `src/runtime/ai_orchestrated/orchestrator.py`
- Modify: `src/runtime/public_output_runtime.py`
- Modify: `src/runtime/state_commit_service.py`
- Modify: `src/tests/conversation/test_single_speech_authority.py`
- Create: `src/tests/conversation/test_delivery_adapter_v12.py`
- Modify: `src/tests/test_basic_settings_main_brain.py`

**Interfaces:**
- Produces: `RuntimeConversationDeliveryAdapter(host).deliver(*, turn, rendered, delivery_id) -> None`.
- Produces: `ensure_conversation_kernel(host) -> ConversationKernel`, wiring root, runtime writer, delivery adapter, session store, receipts, and lease scheduler once.
- Produces: `ConversationKernel.settle_delivery_id(*, session_id, delivery_id, successful) -> bool` for asynchronous transport/audio callbacks.
- Removes: production bootstrap through `ensure_agent_turn_runtime()`.

- [ ] **Step 1: Write failing delivery and architecture tests**

Add tests named:

```python
def test_delivery_adapter_commits_assistant_and_transport_once(): ...
def test_no_tts_receipt_before_send_returns_settles_once(): ...
def test_duplicate_audio_finished_receipt_is_ignored(): ...
def test_stale_render_never_commits_or_reaches_transport(): ...
def test_only_delivery_adapter_calls_public_transport_and_records_assistant(): ...
```

The AST scanner must catch calls to `_send_decision_to_body`, assistant-role `_append_chat_history`, and `record_assistant` outside `delivery_adapter.py`. Permit definitions and direct unit tests, not production call sites. At this intermediate commit, use one explicit migration allowlist containing only `greeting_preset_runtime.py` and `tutor_research_runtime.py`; Tasks 6-7 delete those entries and assert the allowlist is empty. Treat `stage_dispatcher.py` and `tts_task_manager.py` as low-level delivery internals; fail any new public TTS initiation outside the delivery stack.

- [ ] **Step 2: Run focused tests and verify RED**

Run: `pytest -q src/tests/conversation/test_delivery_adapter_v12.py src/tests/conversation/test_single_speech_authority.py src/tests/test_basic_settings_main_brain.py`

Expected: FAIL because the delivery adapter does not exist and foreground orchestration still commits/sends directly; the two explicitly allowlisted tutor/greeting migration sites remain visible for Tasks 6-7.

- [ ] **Step 3: Implement the adapter and foreground cutover**

Move assistant history/ledger commit, last-public pointers, public observers, and the high-level transport call into `RuntimeConversationDeliveryAdapter`. Preserve the existing stage/TTS implementation below `_send_decision_to_body`; do not duplicate TTS logic. Replace foreground bootstrap and writer/commit flow with `ensure_conversation_kernel(host).handle_event(ConversationEvent.user_turn(...))`.

Route no-TTS and audio-finished callbacks directly to `settle_delivery_id()`. Remove compatibility translation through `SpeechPlanSnapshot` from `public_output_runtime.py`.

- [ ] **Step 4: Run focused and foreground integration tests**

Run: `pytest -q src/tests/conversation src/tests/test_basic_settings_main_brain.py src/tests/test_pedagogical_integration.py`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/runtime/conversation/delivery_adapter.py src/runtime/conversation/bootstrap.py src/runtime/conversation/kernel.py src/runtime/conversation/__init__.py src/runtime/ai_orchestrated/orchestrator.py src/runtime/public_output_runtime.py src/runtime/state_commit_service.py src/tests/conversation src/tests/test_basic_settings_main_brain.py src/tests/test_pedagogical_integration.py
git commit -m "refactor: centralize public delivery authority"
```

### Task 5: Collapse Continuation To One Mechanical Lease Scheduler

**Files:**
- Modify: `src/runtime/conversation/contracts.py`
- Modify: `src/runtime/conversation/continuation.py`
- Modify: `src/runtime/conversation/kernel.py`
- Modify: `src/runtime/ai_orchestrated/self_turn_pipeline.py`
- Modify: `src/runtime/autonomous_loop_runtime.py`
- Modify: `src/runtime/pedagogical_runtime.py`
- Modify: `src/runtime/ai_orchestrated/conversation_arc.py`
- Modify: `src/runtime/ai_orchestrated/social_continuation.py`
- Modify: `src/runtime/ai_orchestrated/social_agenda.py`
- Modify: `src/tests/conversation/test_kernel_v12.py`
- Create: `src/tests/conversation/test_continuation_authority_v12.py`
- Modify: `src/tests/conversation/test_single_speech_authority.py`

**Interfaces:**
- Produces: `ConversationEvent.continuation_due(*, session_id, generation, lease_id)`.
- Produces: `ConversationKernel.handle_due_continuation(...) -> ConversationRunResult | None`.
- Preserves: `ContinuationLeaseScheduler.schedule/claim_due/consume/release/cancel_session` as mechanical operations.
- Removes: public continuation claims from pedagogical, conversation-arc, social-continuation, social-agenda, and V11 agent runtimes.

- [ ] **Step 1: Write failing continuation tests**

Add tests named:

```python
def test_due_lease_reenters_kernel_and_root_can_choose_no_reply(): ...
def test_user_generation_beats_claimed_continuation_before_writer(): ...
def test_duplicate_due_wakes_claim_only_one_lease(): ...
def test_lease_schema_contains_no_semantic_content(): ...
def test_only_continuation_lease_scheduler_is_bootstrapped_for_public_continuation(): ...
```

The architecture test must identify production `claim_due`/timer-to-public routes and fail while the old schedulers can yield public candidates.

- [ ] **Step 2: Run focused tests and verify RED**

Run: `pytest -q src/tests/conversation/test_continuation_authority_v12.py src/tests/conversation/test_single_speech_authority.py`

Expected: FAIL on old semantic scheduler paths.

- [ ] **Step 3: Route continuation wakes through the kernel**

Use the existing autonomous loop only as a mechanical wake executor. It claims the V12 lease, constructs `continuation_due`, and calls the kernel. Consume the claim for both successful speech and deliberate `NoReply`; release only transient root/writer failure while generation remains current.

Remove public-candidate return paths and public timers from social continuation, conversation arc, pedagogical runtime, and social agenda. Preserve only domain evidence that can be included in a later `social_signal` or `tutor_result`; remove due/claim fields when they exist solely to schedule speech.

- [ ] **Step 4: Run continuation, race, and pedagogical tests**

Run: `pytest -q src/tests/conversation/test_continuation_authority_v12.py src/tests/conversation/test_kernel_v12.py src/tests/test_pedagogical_runtime.py src/tests/test_pedagogical_integration.py`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/runtime/conversation src/runtime/ai_orchestrated/self_turn_pipeline.py src/runtime/autonomous_loop_runtime.py src/runtime/pedagogical_runtime.py src/runtime/ai_orchestrated/conversation_arc.py src/runtime/ai_orchestrated/social_continuation.py src/runtime/ai_orchestrated/social_agenda.py src/tests/conversation src/tests/test_pedagogical_runtime.py src/tests/test_pedagogical_integration.py
git commit -m "refactor: unify conversation continuation leases"
```

### Task 6: Convert Autonomous, Social, And Opening Speech Into Events

**Files:**
- Modify: `src/runtime/ai_orchestrated/self_turn_pipeline.py`
- Modify: `src/runtime/ai_orchestrated/orchestrator.py`
- Modify: `src/runtime/ai_orchestrated/bootstrap.py`
- Modify: `src/runtime/autonomous_loop_runtime.py`
- Delete: `src/runtime/greeting_preset_runtime.py`
- Delete: `src/runtime/ai_orchestrated/opening_contracts.py`
- Delete: `src/runtime/ai_orchestrated/opening_director.py`
- Delete: `src/runtime/ai_orchestrated/opening_history.py`
- Delete: `src/runtime/ai_orchestrated/opening_input_assembler.py`
- Delete: `src/runtime/ai_orchestrated/opening_move_catalog.py`
- Delete: `src/runtime/ai_orchestrated/opening_reentry.py`
- Delete: `src/runtime/ai_orchestrated/opening_safety.py`
- Modify: `src/runtime/ai_orchestrated/__init__.py`
- Modify: `src/runtime/ai_orchestrated/model_router.py`
- Modify: `src/runtime/runtime_settings_store.py`
- Modify: `src/runtime/settings_catalog.py`
- Create: `src/tests/conversation/test_event_ingress_v12.py`
- Modify: `src/tests/conversation/test_single_speech_authority.py`
- Delete: `src/tests/test_opening_contracts.py`
- Delete: `src/tests/test_opening_director_rules.py`
- Delete: `src/tests/test_opening_history_cooldowns.py`
- Delete: `src/tests/test_opening_memory_callback_v16.py`
- Delete: `src/tests/test_opening_move_catalog.py`
- Delete: `src/tests/test_opening_safety.py`
- Delete: `src/tests/test_opening_surface_writer_contract.py`
- Delete: `src/tests/test_opening_v25_regressions.py`
- Delete: `src/tests/test_session_reentry_opening_v27.py`
- Modify: `src/tests/test_social_stance_and_greetings_v2.py`
- Modify: `src/tests/test_startup_greeting_plus_context_v25.py`
- Modify: `src/tests/test_settings_catalog_v14.py`

**Interfaces:**
- Consumes: `ConversationEvent(kind="autonomous_signal" | "social_signal" | "opening_signal", metadata=...)`.
- Removes: autonomous/opening creation of public `Decision`, writer calls, public commit calls, and session-ritual transport.
- Preserves: body-only nonverbal output with an explicit empty-text assertion.

- [ ] **Step 1: Write failing event-ingress tests**

Add tests named:

```python
def test_idle_autonomous_signal_can_end_in_no_reply_without_surface_calls(): ...
def test_positive_autonomous_signal_uses_kernel_writer_and_delivery(): ...
def test_opening_signal_is_an_opportunity_not_force_speak(): ...
def test_body_only_path_rejects_display_or_speech_text(): ...
def test_autonomous_social_opening_modules_cannot_commit_public_output(): ...
```

- [ ] **Step 2: Run focused tests and verify RED**

Run: `pytest -q src/tests/conversation/test_event_ingress_v12.py src/tests/conversation/test_single_speech_authority.py src/tests/test_social_stance_and_greetings_v2.py src/tests/test_startup_greeting_plus_context_v25.py`

Expected: FAIL on autonomous writer/commit and greeting transport calls.

- [ ] **Step 3: Replace public self-turns with kernel events**

Keep autonomous perception, grounding, and signal collection, but replace `generate_autonomous_reply()` and `commit_public()` with one typed event to the kernel, then delete the now-unused `generate_autonomous_reply()` router entry. Delete the unused semantic opening contracts/director/history/input/move/reentry/safety stack, its settings, its router opening-plan/surface calls, and tests whose only purpose is the removed planner; retain only generic grounded facts already available to root context. Delete the unused greeting preset emission module and its transport test. Remove social-continuation bootstrap as a public speech runtime. Remove the temporary autonomous and greeting entries from the architecture-test allowlists and assert only the tutor-research entry remains.

- [ ] **Step 4: Run focused and autonomous integration tests**

Run: `pytest -q src/tests/conversation/test_event_ingress_v12.py src/tests/conversation/test_single_speech_authority.py src/tests/test_social_stance_and_greetings_v2.py src/tests/test_startup_greeting_plus_context_v25.py src/tests/test_v57_live_behavior_simulation.py`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add -A -- src/runtime/ai_orchestrated src/runtime/autonomous_loop_runtime.py src/runtime/greeting_preset_runtime.py src/runtime/runtime_settings_store.py src/runtime/settings_catalog.py src/tests/conversation src/tests/test_opening_contracts.py src/tests/test_opening_director_rules.py src/tests/test_opening_history_cooldowns.py src/tests/test_opening_memory_callback_v16.py src/tests/test_opening_move_catalog.py src/tests/test_opening_safety.py src/tests/test_opening_surface_writer_contract.py src/tests/test_opening_v25_regressions.py src/tests/test_session_reentry_opening_v27.py src/tests/test_social_stance_and_greetings_v2.py src/tests/test_startup_greeting_plus_context_v25.py src/tests/test_settings_catalog_v14.py src/tests/test_v57_live_behavior_simulation.py
git commit -m "refactor: route autonomous and opening signals through root"
```

### Task 7: Remove Public Authority From Tutor, Work, And Research

**Files:**
- Modify: `src/runtime/capabilities/tutor.py`
- Modify: `src/runtime/capabilities/work.py`
- Create: `src/runtime/capabilities/research.py`
- Modify: `src/runtime/capabilities/__init__.py`
- Modify: `src/runtime/tutor_research_runtime.py`
- Modify: `src/runtime/ai_orchestrated/orchestrator.py`
- Create: `src/tests/conversation/test_capability_boundaries_v12.py`
- Modify: `src/tests/conversation/test_single_speech_authority.py`
- Modify: `src/tests/test_tutor_executive_v1.py`
- Modify: `src/tests/test_tutor_research_policy_v18.py`

**Interfaces:**
- Preserves: `TutorCapability.advise(...) -> TutorAdvice`.
- Replaces: `commit_delivery(public_text=...)` with `observe_delivery_receipt(receipt: DeliveryReceipt) -> None`.
- Produces: `ResearchResult` and `ResearchCapability` with data-only `snapshot/result` methods.
- Consumes: `ConversationEvent(kind="tutor_result" | "work_result" | "research_result")`.

- [ ] **Step 1: Write failing capability-boundary tests**

Add tests named:

```python
def test_tutor_returns_advice_without_public_surface_methods(): ...
def test_research_completion_reenters_root_before_public_delivery(): ...
def test_duplicate_work_result_produces_at_most_one_delivery(): ...
def test_capability_failure_does_not_create_fallback_speech(): ...
def test_capability_modules_never_record_assistant_or_call_transport(): ...
```

- [ ] **Step 2: Run focused tests and verify RED**

Run: `pytest -q src/tests/conversation/test_capability_boundaries_v12.py src/tests/conversation/test_single_speech_authority.py src/tests/test_tutor_executive_v1.py src/tests/test_tutor_research_policy_v18.py`

Expected: FAIL on `TutorResearchRuntimeMixin` assistant commit/transport and the public-text tutor receipt API.

- [ ] **Step 3: Convert completions into capability events**

Return bounded diagnosis/evidence/advice/results to root context. Replace tutor research follow-up `Decision` construction and direct delivery with a `research_result` event. Use delivery receipts only to update private tutor state; capability code never receives authority to schedule the next public turn. Remove the final tutor-research migration entry from the architecture-test allowlist and assert the writer, assistant-commit, and public-transport allowlists are empty.

- [ ] **Step 4: Run capability and integration tests**

Run: `pytest -q src/tests/conversation/test_capability_boundaries_v12.py src/tests/conversation/test_single_speech_authority.py src/tests/test_tutor_executive_v1.py src/tests/test_tutor_research_policy_v18.py src/tests/test_pedagogical_integration.py`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/runtime/capabilities src/runtime/tutor_research_runtime.py src/runtime/ai_orchestrated/orchestrator.py src/tests/conversation src/tests/test_tutor_executive_v1.py src/tests/test_tutor_research_policy_v18.py src/tests/test_pedagogical_integration.py
git commit -m "refactor: make domain capabilities data only"
```

### Task 8: Delete The V7-V11 Speech Architecture

**Files:**
- Delete: `src/runtime/conversation/legacy_adapter.py`
- Delete: `src/runtime/turn_projection_runtime.py`
- Delete: `src/runtime/ai_orchestrated/social_continuation.py`
- Delete: `src/runtime/agent_loop/__init__.py`
- Delete: `src/runtime/agent_loop/completion.py`
- Delete: `src/runtime/agent_loop/contracts.py`
- Delete: `src/runtime/agent_loop/director.py`
- Delete: `src/runtime/agent_loop/pacing.py`
- Delete: `src/runtime/agent_loop/reasoner.py`
- Delete: `src/runtime/agent_loop/runtime.py`
- Delete: `src/runtime/agent_loop/speech.py`
- Delete: `src/runtime/agent_loop/speech_plan.py`
- Delete: `src/runtime/agent_loop/tutor_role.py`
- Delete: `src/runtime/agent_loop/validator.py`
- Modify: `src/runtime/public_output_runtime.py`
- Modify: `src/runtime/ai_orchestrated/orchestrator.py`
- Modify: `src/runtime/ai_orchestrated/self_turn_pipeline.py`
- Delete: `src/tests/test_agent_conversation_pacing_v2.py`
- Delete: `src/tests/test_agent_disclosure_authority_v7.py`
- Delete: `src/tests/test_agent_productionization_v3.py`
- Delete: `src/tests/test_agent_speech_plan_v8.py`
- Delete: `src/tests/test_agent_turn_loop_v1.py`
- Delete: `src/tests/test_agent_turn_projection_v11.py`
- Delete: `src/tests/test_agent_writer_surface_immutable_v6.py`
- Delete: `src/tests/test_conversation_arcs_v19.py`
- Modify: `src/tests/test_inference_resource_profile_v4.py`
- Modify: `src/tests/test_learning_kernel_boundary_v4.py`
- Modify: `src/tests/test_social_agenda_big_patch_v20.py`
- Modify: `src/tests/test_social_agenda_full_cutover_v22.py`
- Modify: `src/tests/test_social_agenda_live_selector_v23.py`
- Modify: `src/tests/test_social_agenda_production_v21.py`
- Modify: `src/tests/test_v103_expression_competition_workspace.py`
- Modify: `src/tests/test_v103_final_continuous_nervous_system.py`
- Modify: `src/tests/conversation/test_architecture_v12.py`
- Modify: `src/tests/conversation/test_single_speech_authority.py`

**Interfaces:**
- Removes: `SpeechPlanLedger`, `SpeechPlanSnapshot`, `TurnProjection`, `TurnFloor`, `DialogueBeat`, `ConversationSpeechPlan`, `SpeechContinuationMode`, `TurnAgentRuntime`, and V12-to-V11 projections.
- Preserves: V12 conversation contracts, root model resource-profile assertions, and tutor capability tests under their new owners.

- [ ] **Step 1: Write the failing legacy-absence test**

Add assertions that production imports and package exports contain none of:

```python
(
    "SpeechPlanLedger", "SpeechPlanSnapshot", "TurnProjection", "TurnFloor",
    "DialogueBeat", "ConversationSpeechPlan", "SpeechContinuationMode",
    "TurnAgentRuntime", "V12AgentTurnRuntime",
)
```

Also assert the deleted modules cannot be found through `importlib.util.find_spec`.

- [ ] **Step 2: Run architecture tests and verify RED**

Run: `pytest -q src/tests/conversation/test_architecture_v12.py src/tests/conversation/test_single_speech_authority.py`

Expected: FAIL because legacy modules and imports still exist.

- [ ] **Step 3: Delete legacy production code and obsolete tests**

Remove all remaining imports first, migrate resource-profile assertions from the V11 reasoner to `RuntimeModelRootAgent`, replace surviving social-agenda assertions with data-only signal behavior, and then delete the package/files and old social continuation policy. Do not leave re-export shims, deprecated aliases, or placeholder classes.

- [ ] **Step 4: Run conversation and affected historical tests**

Run: `pytest -q src/tests/conversation src/tests/test_inference_resource_profile_v4.py src/tests/test_learning_kernel_boundary_v4.py`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add -A src/runtime/agent_loop src/runtime/conversation src/runtime/turn_projection_runtime.py src/runtime/public_output_runtime.py src/runtime/ai_orchestrated src/tests
git commit -m "refactor: delete legacy speech planning architecture"
```

### Task 9: Verify The Hard Cutover End To End

**Files:**
- Modify only if verification exposes a regression in files already owned by Tasks 1-8.

**Interfaces:**
- Consumes all V12 interfaces from Tasks 1-8.
- Produces no new feature or compatibility layer.

- [ ] **Step 1: Run static and focused verification**

Run:

```bash
ruff check src/runtime/conversation src/runtime/capabilities src/tests/conversation
pytest -q src/tests/conversation
```

Expected: zero lint errors and all focused tests PASS.

- [ ] **Step 2: Run affected integration groups**

Run:

```bash
pytest -q src/tests/test_pedagogical_integration.py src/tests/test_pedagogical_runtime.py src/tests/test_tutor_executive_v1.py src/tests/test_tutor_research_policy_v18.py src/tests/test_v57_live_behavior_simulation.py src/tests/test_basic_settings_main_brain.py
```

Expected: PASS.

- [ ] **Step 3: Run the full repository test suite**

Run: `pytest -q`

Expected: PASS. If pre-existing unrelated failures exist, record their exact node IDs and verify the full set of changed/affected tests remains green; do not hide or omit them.

- [ ] **Step 4: Audit production source for forbidden authority**

Run:

```bash
rg -n "SpeechPlanLedger|TurnProjection|DialogueBeat|ConversationSpeechPlan|V12AgentTurnRuntime" src/runtime
rg -n "_send_decision_to_body\(|record_assistant\(|generate_autonomous_reply\(" src/runtime
```

Expected: the first command has no matches; the second contains only delivery/writer implementation definitions and allowed adapter call sites enforced by architecture tests.

- [ ] **Step 5: Commit any verification-only fixes**

```bash
git add <only-files-fixed-during-verification>
git commit -m "fix: close single speech authority regressions"
```

Skip this commit when verification required no changes.
