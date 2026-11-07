# Pedagogical Conversation Controller Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a feature-flagged, durable teaching loop that keeps a multi-turn learning goal, selects one bounded chunk at a time, observes comprehension evidence, and resumes or stops safely.

**Architecture:** Keep the existing LangGraph Agent Harness for coding tasks. Add a framework-independent Python pedagogical controller with immutable contracts, a pure transition/evidence layer, and a SQLite-backed timer/lease runtime. Integrate it before the public writer and advance it only from successful public commit receipts.

**Tech Stack:** Python 3.11+, dataclasses, existing `SQLiteAtomicStateStore`, existing runtime settings/observability, current model router JSON-schema boundary, pytest.

**Spec:** `docs/superpowers/specs/2026-09-25-pedagogical-conversation-controller-design.md`

## Global Constraints

- Version 1 uses a native Python state machine; LangGraph remains the coding-task engine.
- No Cline or Codex package, repository, submodule, or service is added.
- The writer receives only a typed `TeachingDirective` and cannot advance or complete an episode.
- Silence and short acknowledgements never increase mastery.
- Only successful public commits consume a teaching chunk.
- Maximum autonomous chunks, repair attempts, stalls, and episode lifetime are runtime-clamped.
- The feature is disabled by default behind `ERIS_PEDAGOGICAL_CONTROLLER_ENABLED`.
- Persist only bounded typed evidence and refs; never raw chat, source code, or chain-of-thought.

## Review Focus

- A stale timer or stale writer result must not speak or advance a newer plan revision; owned by Tasks 3 and 6.
- A user message racing a due dwell must win and preserve the episode; owned by Task 6.
- A writer/provider failure must leave the pending chunk unconsumed; owned by Tasks 2 and 5.
- A `yes/ok/silence` acknowledgement must not become mastery; owned by Tasks 1 and 2.
- Normal non-teaching chat must remain byte-for-byte behaviorally equivalent when the flag is off; owned by Task 5.

## File Map

- Create `src/runtime/pedagogical_contracts.py`: immutable contracts, enum/status validation, bounded serialization.
- Create `src/runtime/pedagogical_controller.py`: pure episode transitions, evidence classification, plan selection, budgets.
- Create `src/runtime/pedagogical_runtime.py`: durable storage, timer/lease lifecycle, wake candidates, restart recovery.
- Create `src/runtime/pedagogical_plan_builder.py`: structured plan-call boundary and plan validation.
- Create `src/tests/test_pedagogical_contracts.py`.
- Create `src/tests/test_pedagogical_controller.py`.
- Create `src/tests/test_pedagogical_runtime.py`.
- Create `src/tests/test_pedagogical_plan_builder.py`.
- Create `src/tests/test_pedagogical_integration.py`.
- Modify `src/runtime/ai_orchestrated/model_router.py`: teaching-plan schema/call and writer directive projection.
- Modify `src/runtime/ai_orchestrated/orchestrator.py`: feature-gated directive injection, user evidence, public receipts, audio handoff.
- Modify `src/runtime/ai_orchestrated/self_turn_pipeline.py`: due pedagogical candidate selection and claim/commit receipt.
- Modify `src/runtime/runtime_settings_store.py` and `src/runtime/settings_catalog.py`: feature flag and bounded defaults.

### Task 1: Define Typed Contracts

**Files:**
- Create: `src/runtime/pedagogical_contracts.py`
- Test: `src/tests/test_pedagogical_contracts.py`

**Interfaces:**
- Produces `TeachingPlan`, `TeachingEpisode`, `TeachingConcept`, `ComprehensionState`, `ComprehensionEvidence`, `MisconceptionHypothesis`, `TeachingChunk`, `TeachingDirective`, and `TeachingReceipt`.
- Produces `normalize_episode(payload) -> TeachingEpisode | None` and `validate_directive(directive) -> tuple[bool, str]`.

- [ ] **Step 1: Write failing contract tests**

```python
def test_episode_round_trips_with_bounded_fields():
    episode = TeachingEpisode.new(
        episode_id="teach:1",
        topic_key="core7_vs_core8",
        global_goal="distinguish motivation from behavior",
        concepts=(TeachingConcept("motivation", "Separate motive from behavior"),),
        now=100.0,
        expires_at=700.0,
    )
    restored = TeachingEpisode.from_mapping(episode.to_dict())
    assert restored == episode
    assert len(restored.global_goal) <= 420


def test_invalid_status_and_out_of_range_values_fail_closed():
    assert normalize_episode({"status": "invented", "comprehension": {}}) is None
    state = ComprehensionState.from_mapping({"estimated_mastery": 4, "confidence": -2})
    assert state.estimated_mastery == 1.0
    assert state.confidence == 0.0


def test_directive_cannot_exceed_hard_chunk_limits():
    directive = TeachingDirective(
        episode_id="teach:1", plan_revision=1, chunk_id="chunk:1",
        global_goal="goal", current_objective="obj", claim="claim",
        contrast="contrast", example_policy="allowed", max_ideas=99,
        max_sentences=99, allow_question=True, stop_after_chunk=True,
        allow_autonomous_resume=True,
    )
    valid, reason = validate_directive(directive)
    assert not valid
    assert reason == "directive_budget_exceeded"
```

- [ ] **Step 2: Run the focused tests and verify they fail**

Run: `pytest -q src/tests/test_pedagogical_contracts.py`

Expected: collection failure because the new contracts module does not exist.

- [ ] **Step 3: Implement the minimal contracts**

Use frozen/slots dataclasses. Clamp all numeric values with the existing project convention (`0.0..1.0`), truncate strings at serialization boundaries, accept only the statuses in the spec, and generate stable IDs from the caller rather than timestamps inside equality-sensitive objects. `TeachingEpisode.new()` must initialize `status="planning"`, `plan_revision=1`, zero autonomous/repair/stall counters, and an empty pending chunk.

- [ ] **Step 4: Run the focused tests**

Run: `pytest -q src/tests/test_pedagogical_contracts.py`

Expected: all contract tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/runtime/pedagogical_contracts.py src/tests/test_pedagogical_contracts.py
git commit -m "feat: add pedagogical conversation contracts"
```

### Task 2: Implement Pure Controller Transitions

**Files:**
- Create: `src/runtime/pedagogical_controller.py`
- Test: `src/tests/test_pedagogical_controller.py`

**Interfaces:**
- `start_episode(plan: TeachingPlan, now: float) -> TeachingEpisode`
- `observe_user_turn(episode, user_text, *, evidence, now) -> ControllerResult`
- `select_next_action(episode, *, now) -> ControllerAction`
- `note_public_commit(episode, receipt: TeachingReceipt) -> TeachingEpisode`
- `note_audio_finished(episode, *, now) -> TeachingEpisode`
- `expire_if_needed(episode, *, now) -> TeachingEpisode`

`ControllerAction` is a frozen dataclass with `kind: Literal["answer_user", "teach_chunk", "repair", "probe", "advance", "wait", "complete", "abandon"]`, an optional `TeachingDirective`, and a short reason code. `ControllerResult` is a frozen dataclass with `episode`, `action`, and `evidence_codes`.

`ControllerResult` must carry the updated episode, typed evidence codes, and an optional `TeachingDirective`; it must not call a model or persist state.

- [ ] **Step 1: Write failing transition tests**

```python
def test_acknowledgement_does_not_increase_mastery():
    episode = _episode_with_chunk()
    result = observe_user_turn(episode, "Ừ, hiểu rồi.", evidence=(), now=101.0)
    assert result.episode.comprehension.estimated_mastery == episode.comprehension.estimated_mastery
    assert "self_report_understood" in result.evidence_codes


def test_correct_paraphrase_advances_current_concept():
    episode = _episode_with_chunk()
    result = observe_user_turn(
        episode,
        "7 muốn thoát cảm giác bị mắc kẹt, không chỉ là người nói nhiều.",
        evidence=("user_paraphrase",),
        now=101.0,
    )
    assert result.episode.comprehension.estimated_mastery > episode.comprehension.estimated_mastery
    assert result.action.kind in {"advance", "probe", "complete"}


def test_user_correction_enters_repair_without_lowering_user_mastery():
    episode = _episode_with_chunk()
    result = observe_user_turn(
        episode, "Không, bạn đang hiểu sai ý mình.",
        evidence=("eris_corrected",), now=101.0,
    )
    assert result.episode.status == "repairing"
    assert result.episode.comprehension.estimated_mastery == episode.comprehension.estimated_mastery


def test_two_stalls_force_wait():
    episode = replace(_episode_with_chunk(), consecutive_stalls=1)
    result = select_next_action(episode, now=101.0)
    assert result.kind == "wait"
```

- [ ] **Step 2: Run tests to verify failure**

Run: `pytest -q src/tests/test_pedagogical_controller.py`

Expected: import/signature failures.

- [ ] **Step 3: Implement typed evidence classification and transitions**

Use explicit evidence codes. `ok`, `ừ`, `hmm`, silence, and topic change may affect interaction policy but never mastery. Correct paraphrase/application increases mastery; repeated focused questions mark uncertainty; explicit misconception assertions create a `MisconceptionHypothesis`; `eris_corrected` invalidates controller assumptions and routes to `repairing`. `note_public_commit` must reject mismatched episode/revision/chunk IDs and must not consume a rejected receipt.

- [ ] **Step 4: Implement plan-branch selection and budgets**

Choose `teach_chunk`, `repair`, `probe`, `advance`, `wait`, or `complete` from concept status, evidence, `autonomous_chunks_used`, `repair_attempts`, `consecutive_stalls`, and `expires_at`. Hard-clamp defaults to two autonomous chunks, one same-misconception repair, two stalls, and ten-minute expiry.

- [ ] **Step 5: Run tests to verify pass**

Run: `pytest -q src/tests/test_pedagogical_controller.py`

Expected: all pure-transition tests pass without filesystem or network access.

- [ ] **Step 6: Commit**

```bash
git add src/runtime/pedagogical_controller.py src/tests/test_pedagogical_controller.py
git commit -m "feat: add pedagogical controller transitions"
```

### Task 3: Add Durable Runtime, Leases, and Dwell

**Files:**
- Create: `src/runtime/pedagogical_runtime.py`
- Test: `src/tests/test_pedagogical_runtime.py`
- Reference: `src/runtime/ai_orchestrated/atomic_state.py` and `src/runtime/ai_orchestrated/conversation_arc.py`

**Interfaces:**
- `PedagogicalRuntime(store, *, state_key="eris.pedagogy", clock, timer_factory, wake_callback)`
- `ensure_pedagogical_runtime(host) -> PedagogicalRuntime | None`
- `runtime.start_episode(episode) -> bool`
- `runtime.snapshot(now=None) -> dict[str, Any]`
- `runtime.observe_user_turn(...) -> ControllerResult`
- `runtime.claim_due(*, worker_id, now=None) -> TeachingDirective | None`
- `runtime.commit_receipt(receipt, *, now=None) -> bool`
- `runtime.notify_audio_finished(timestamp, *, source_turn_id="") -> None`
- `runtime.cancel_dwell(reason, *, now=None) -> None`
- `runtime.shutdown() -> None`

- [ ] **Step 1: Write failing persistence/race tests**

```python
def test_restart_clears_expired_claim_and_rearms_pending_dwell(tmp_path):
    store = SQLiteAtomicStateStore(tmp_path / "state.sqlite")
    first = PedagogicalRuntime(store, clock=lambda: 1000.0, timer_factory=_FakeTimer)
    first.start_episode(_episode_with_chunk(status="dwelling"))
    candidate = first.claim_due(worker_id="worker-a", now=1001.0)
    assert candidate is not None
    first.shutdown()

    second = PedagogicalRuntime(store, clock=lambda: 1003.0, timer_factory=_FakeTimer)
    assert second.claim_due(worker_id="worker-b", now=1003.0) is not None


def test_stale_receipt_is_ignored():
    runtime = _runtime()
    runtime.start_episode(_episode_with_chunk(plan_revision=2))
    assert not runtime.commit_receipt(_receipt(plan_revision=1))


def test_user_turn_cancels_due_dwell_before_claim():
    runtime = _runtime()
    runtime.start_episode(_episode_with_chunk(status="dwelling"))
    runtime.observe_user_turn("Khoan, ý này chưa rõ", now=1001.0)
    assert runtime.claim_due(worker_id="worker", now=1002.0) is None
    assert runtime.snapshot()["active_episode"]["status"] in {"observing", "repairing", "waiting_user"}
```

- [ ] **Step 2: Run tests to verify failure**

Run: `pytest -q src/tests/test_pedagogical_runtime.py`

Expected: import failures.

- [ ] **Step 3: Implement atomic state loading and persistence**

Use `SQLiteAtomicStateStore.transaction()` with one state key. Persist only `episode.to_dict()` plus schema/version metadata. On startup call `ensure`, reload the record inside each mutation transaction, clear claims whose lease expired, and preserve a valid episode through process restart.

- [ ] **Step 4: Implement timer and claim lifecycle**

Mirror the existing conversation-arc pattern: arm a timer for `due_at`, acquire a short lease with `worker_id`, return a directive only while the episode/revision is current, and release claims on generation failure. `claim_due` must be idempotent and must return no candidate while a user turn is active.

- [ ] **Step 5: Implement audio anchor and receipt commit**

`notify_audio_finished()` moves the dwell anchor forward. `commit_receipt()` validates the composite idempotency key and advances only once. A failed surface, TTS, grounding, or stale revision leaves the pending chunk available for retry.

- [ ] **Step 6: Run tests and commit**

Run: `pytest -q src/tests/test_pedagogical_runtime.py`

Expected: all persistence, restart, lease, and race tests pass.

```bash
git add src/runtime/pedagogical_runtime.py src/tests/test_pedagogical_runtime.py
git commit -m "feat: add durable pedagogical runtime"
```

### Task 4: Add Structured Teaching Plan Builder and Writer Contract

**Files:**
- Create: `src/runtime/pedagogical_plan_builder.py`
- Modify: `src/runtime/ai_orchestrated/model_router.py:814-860, 1480-1815`
- Test: `src/tests/test_pedagogical_plan_builder.py`

**Interfaces:**
- `TeachingPlanBuilder(router) -> build(request_context) -> TeachingPlan | None`
- `AIRuntimeModelRouter.plan_teaching_episode(...) -> Mapping[str, Any]`
- `AIRuntimeModelRouter.generate_public_reply(..., teaching_directive: TeachingDirective | None = None)`

- [ ] **Step 1: Write failing schema tests**

```python
def test_plan_builder_rejects_missing_success_evidence():
    builder = TeachingPlanBuilder(_FakeRouter({"global_goal": "x", "concepts": [{"id": "a"}]}))
    assert builder.build(_context()) is None


def test_plan_builder_limits_concepts_and_assigns_stable_ids():
    payload = _valid_plan_payload(concepts=12)
    plan = TeachingPlanBuilder(_FakeRouter(payload)).build(_context())
    assert plan is not None
    assert len(plan.concepts) == 8
    assert len({concept.concept_id for concept in plan.concepts}) == 8


def test_writer_directive_is_injected_without_granting_future_plan_authority():
    router = _FakeWriterRouter()
    router.generate_public_reply(user_text="x", teaching_directive=_directive())
    assert router.last_kwargs["teaching_directive"].current_objective
    assert router.last_kwargs["teaching_directive"].stop_after_chunk is True
```

- [ ] **Step 2: Run tests to verify failure**

Run: `pytest -q src/tests/test_pedagogical_plan_builder.py`

Expected: missing builder/schema failures.

- [ ] **Step 3: Add the structured plan schema and builder**

Define a JSON schema with `global_goal`, `concepts`, prerequisite IDs, success evidence, misconception hypotheses, and example policy. `plan_teaching_episode` must request JSON only, have no public utterance field, and return an empty result on provider/schema failure. `TeachingPlanBuilder` validates, truncates to eight concepts, drops invalid prerequisite edges, and returns `None` when no valid first concept remains.

- [ ] **Step 4: Add the locked writer projection**

Extend the public writer input with a bounded directive projection. Include `episode_id`, revision, current objective, claim, contrast, and delivery limits. Explicitly instruct the writer to stop after the chunk and never claim comprehension or completion. Keep ordinary calls unchanged when the directive is absent.

- [ ] **Step 5: Run tests and commit**

Run: `pytest -q src/tests/test_pedagogical_plan_builder.py`

Expected: schema, builder fallback, and writer-boundary tests pass.

```bash
git add src/runtime/pedagogical_plan_builder.py src/runtime/ai_orchestrated/model_router.py src/tests/test_pedagogical_plan_builder.py
git commit -m "feat: add structured teaching plans and writer directives"
```

### Task 5: Integrate User Turns and Public Commit Receipts

**Files:**
- Modify: `src/runtime/ai_orchestrated/orchestrator.py:700-920, 1588-1748, 2572-2670`
- Modify: `src/runtime/runtime_settings_store.py` and `src/runtime/settings_catalog.py`
- Test: `src/tests/test_pedagogical_integration.py`

**Interfaces:**
- Orchestrator resolves `ensure_pedagogical_runtime(host)` and calls `observe_user_turn()` before planning.
- Orchestrator passes `TeachingDirective | None` to `generate_public_reply()`.
- Orchestrator calls `commit_receipt()` only after `public_text` is sealed successfully.

- [ ] **Step 1: Write failing integration tests with a fake host**

```python
def test_flag_off_does_not_call_plan_builder_or_change_writer_inputs():
    host = _fake_host(pedagogy_enabled=False)
    run_chat_turn(host, "Giải thích ngắn khái niệm này")
    assert host.plan_builder.calls == 0
    assert host.writer.teaching_directive is None


def test_teaching_directive_reaches_writer_before_public_generation():
    host = _fake_host(pedagogy_enabled=True)
    run_chat_turn(host, "Mình muốn thật sự hiểu core 7 và core 8")
    assert host.writer.teaching_directive is not None
    assert host.writer.teaching_directive.stop_after_chunk is True


def test_writer_failure_does_not_consume_chunk():
    host = _fake_host(pedagogy_enabled=True, writer_result={})
    run_chat_turn(host, "Dạy mình phân biệt hai khái niệm này")
    assert host.pedagogy.snapshot()["active_episode"]["pending_chunk"] is not None
```

- [ ] **Step 2: Run tests to verify failure**

Run: `pytest -q src/tests/test_pedagogical_integration.py`

Expected: missing runtime wiring or absent feature flag failures.

- [ ] **Step 3: Add feature configuration**

Register `("pedagogy", "enabled")` with environment mapping `ERIS_PEDAGOGICAL_CONTROLLER_ENABLED` and default `False`. Add runtime-clamped defaults for dwell bounds, autonomous budget, repair budget, stall budget, and episode expiry. Expose the values in the settings catalog without changing current defaults.

- [ ] **Step 4: Wire the read path**

At the existing user-turn observation boundary, pass only a bounded current-turn/history projection to the pedagogical runtime. If no episode exists and activation intent is explicit, invoke `TeachingPlanBuilder`; otherwise keep the controller absent. Add the directive to the compiled public context only after the controller has selected it.

- [ ] **Step 5: Wire the commit path**

After the existing public-surface seal and before any social continuation side effect, create a `TeachingReceipt` from the episode ID, plan revision, chunk ID, turn ID, and text signature. Commit it only when `public_text` is non-empty and the existing failure/parse gates allow the public turn. Never commit for deterministic fallback, grounding failure, or suppressed output.

- [ ] **Step 6: Run integration tests and commit**

Run: `pytest -q src/tests/test_pedagogical_integration.py src/tests/test_public_writer_recovery_v6.py`

Expected: flag-off behavior remains unchanged; flag-on directive and receipt behavior passes.

```bash
git add src/runtime/ai_orchestrated/orchestrator.py src/runtime/runtime_settings_store.py src/runtime/settings_catalog.py src/tests/test_pedagogical_integration.py
git commit -m "feat: integrate pedagogical controller with chat turns"
```

### Task 6: Integrate Dwell and Autonomous Continuation

**Files:**
- Modify: `src/runtime/ai_orchestrated/self_turn_pipeline.py`
- Modify: `src/runtime/ai_orchestrated/orchestrator.py:2641-2665`
- Test: `src/tests/test_pedagogical_integration.py`

**Interfaces:**
- `SelfTurnPipeline` asks the pedagogical runtime for a due candidate before generic autonomous continuation candidates.
- Candidate claim returns a directive carrying the episode/revision/chunk composite key.
- Successful autonomous public commit calls `commit_receipt()`; surface failure releases the claim.

- [ ] **Step 1: Add failing race tests**

```python
def test_user_turn_wins_against_due_autonomous_teaching_chunk():
    host = _fake_host(pedagogy_enabled=True, episode_status="dwelling")
    host.pedagogy.observe_user_turn("Khoan, mình chưa hiểu", now=1001.0)
    assert run_autonomous_tick(host, now=1002.0) is False


def test_audio_finished_anchors_dwell_and_allows_one_claim():
    host = _fake_host(pedagogy_enabled=True, episode_status="speaking")
    host.pedagogy.notify_audio_finished(1002.0)
    assert run_autonomous_tick(host, now=1002.1) is False
    candidate = run_autonomous_tick(host, now=1005.0)
    assert candidate is True
    assert run_autonomous_tick(host, now=1005.1) is False


def test_autonomous_surface_failure_releases_claim_without_receipt():
    host = _fake_host(pedagogy_enabled=True, surface_ok=False)
    assert run_autonomous_tick(host, now=1005.0) is False
    assert host.pedagogy.snapshot()["active_episode"]["pending_chunk"] is not None
```

- [ ] **Step 2: Run tests to verify failure**

Run: `pytest -q src/tests/test_pedagogical_integration.py -k 'due or audio or autonomous'`

Expected: no pedagogical candidate path exists yet.

- [ ] **Step 3: Add due candidate selection**

Expose a bounded pedagogical candidate from `SelfTurnPipeline` only when the runtime claims a valid directive. Run it through the existing initiative/grounding/surface gates. Do not bypass user-busy or interrupt checks except for the controller's own validated dwell conditions.

- [ ] **Step 4: Anchor dwell to audio completion**

At the existing `audio_finished_at` catch-up path, call `pedagogical_runtime.notify_audio_finished()` with the real playback timestamp. Do not anchor on text commit alone. User input must cancel the timer and release the claim before any autonomous call can speak.

- [ ] **Step 5: Commit autonomous receipts and release failures**

On successful autonomous public commit, create the same `TeachingReceipt` used by foreground chat. On no text, duplicate, grounding rejection, preemption, or TTS failure, release/defer the claim without advancing the episode. Enforce the two-chunk/two-stall hard budgets in runtime, not prompt text.

- [ ] **Step 6: Run race tests and commit**

Run: `pytest -q src/tests/test_pedagogical_integration.py -k 'due or audio or autonomous' src/tests/test_v103_final_continuous_nervous_system.py`

Expected: due/claim/audio races pass and existing autonomous nervous-system tests remain green.

```bash
git add src/runtime/ai_orchestrated/self_turn_pipeline.py src/runtime/ai_orchestrated/orchestrator.py src/tests/test_pedagogical_integration.py
git commit -m "feat: add pedagogical dwell and autonomous continuation"
```

### Task 7: Full Verification and Behavioral Evaluation

**Files:**
- Modify: `src/tests/test_pedagogical_integration.py` if missing scenarios are found.
- Create: `src/tests/test_pedagogical_behavioral_v1.py`
- Modify: `src/evals/mentor_golden_v85.json` only if the existing eval format can represent the new scenarios; otherwise keep a separate fixture.

- [ ] **Step 1: Add golden behavioral fixtures**

Add deterministic cases for: correct paraphrase, novel application, repeated misunderstanding, explicit correction of Eris, `ok` only, silence, topic change, already-known concept, due dwell interrupted by user input, and restart during dwell. Each expected result must assert state transition/evidence code, not exact wording.

- [ ] **Step 2: Run focused controller/runtime/integration tests**

Run: `pytest -q src/tests/test_pedagogical_contracts.py src/tests/test_pedagogical_controller.py src/tests/test_pedagogical_runtime.py src/tests/test_pedagogical_plan_builder.py src/tests/test_pedagogical_integration.py src/tests/test_pedagogical_behavioral_v1.py`

Expected: all new tests pass.

- [ ] **Step 3: Run regression tests around affected boundaries**

Run: `pytest -q src/tests/test_conversation_arcs_v19.py src/tests/test_social_agenda_big_patch_v20.py src/tests/test_public_writer_recovery_v6.py src/tests/test_v103_final_continuous_nervous_system.py src/tests/test_agent_harness_v2.py`

Expected: existing social arc, writer recovery, autonomous nervous-system, and coding harness tests pass unchanged.

- [ ] **Step 4: Run repository verification**

Run: `python run_tests.py`.

Expected: repository test runner completes without new failures. If the full suite has unrelated pre-existing failures, record exact test names and do not weaken pedagogical assertions.

- [ ] **Step 5: Inspect feature-off behavior and diff**

Run: `git diff --check` and the flag-off integration test. Confirm the default settings leave the controller inactive and no new model call appears in ordinary chat.

- [ ] **Step 6: Commit evaluation fixtures and final verification**

```bash
git add src/tests/test_pedagogical_behavioral_v1.py src/evals
git commit -m "test: add pedagogical conversation evaluations"
```

## Self-Review Checklist

- [ ] Every contract in the spec has an owning task.
- [ ] Plan builder failure does not create a hidden episode.
- [ ] Public writer never owns plan progress or completion.
- [ ] Stale revision, duplicate receipt, and user/timer race are tested.
- [ ] Feature-off behavior is tested before enabling the feature.
- [ ] No external Cline/Codex runtime dependency is introduced.
- [ ] Existing Agent Harness and social conversation arcs remain separate.
