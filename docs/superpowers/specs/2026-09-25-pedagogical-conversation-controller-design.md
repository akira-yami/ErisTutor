# Pedagogical Conversation Controller Design

## Status

Proposed for implementation after user review.

## Problem

Eris already has a durable coding-agent loop and several conversation mechanisms, but normal foreground chat does not have a durable goal loop that owns teaching across multiple public turns.

The current chat path deliberately avoids a separate remote planner for ordinary conversation. The main writer receives broad context and produces wording, semantic metadata, and future social-plan proposals in one call. That is appropriate for ordinary conversation, but it gives the writer too much authority when the user is trying to understand a topic over several turns.

Several existing modules solve parts of the problem:

- `runtime.agent_harness` provides checkpointed agent/tool execution, interrupt/resume, completion gates, and recovery after restart.
- `runtime.ai_orchestrated.conversation_arc` provides durable timed continuation, leases, idempotency, and audio-anchored dwell.
- `runtime.adaptive_engineering_learning` tracks evidence-based mastery, confidence, self-report, and candidate misconceptions for a small engineering curriculum.
- `runtime.ai_orchestrated.state_reducer` provides a single-writer event reduction model.

These pieces do not currently form a general pedagogical controller. A conversation arc is short, writer-proposed, and removed from the active slot when the user speaks. Adaptive engineering learning is domain-specific and can suggest a prompt, but it does not own a multi-turn teaching plan. Consequently Eris can explain well in one response without reliably maintaining the distinction between the long teaching goal and the next small spoken chunk.

## Goals

1. Maintain a durable teaching episode across multiple user and autonomous turns.
2. Separate long-horizon teaching decisions from public wording.
3. Estimate comprehension conservatively from typed evidence rather than silence or short acknowledgements.
4. Emit one bounded teaching chunk at a time.
5. Support audio-anchored dwell followed by an optional autonomous continuation.
6. Replan without losing the teaching goal when the user interrupts, corrects Eris, or changes direction.
7. Stop deterministically when the episode is complete, stale, unwanted, stalled, or out of budget.
8. Reuse Eris infrastructure and proven external invariants without embedding Cline or Codex as a second runtime.

## Non-Goals

- Replacing the existing coding Agent Harness.
- Turning every explanation into a lesson or quiz.
- Building an LMS, curriculum marketplace, or general knowledge graph.
- Treating silence, `ok`, `uh-huh`, or topic changes as proof of mastery.
- Letting the public writer create, advance, or complete a teaching episode.
- Importing the Cline TypeScript runtime or Codex Rust core into the Python foreground-chat process.
- Exposing private reasoning or hidden plans in public conversation.

## Architectural Decision

Version 1 will use a native Python state machine rather than placing LangGraph directly on the foreground chat hot path.

The controller will reuse:

- `SQLiteAtomicStateStore` for durable compare-and-replace state.
- The timer, claim lease, audio-finished anchor, and idempotency patterns from `ConversationArcRuntime`.
- The evidence rules from `AdaptiveEngineeringLearning`, generalized for episode-local concepts.
- The existing orchestrator, initiative, grounding, and public commit boundaries.

LangGraph remains the execution engine for coding tasks. The pedagogical controller may later move to LangGraph if it needs cross-process graph replay or substantially more complex branching, but its typed contracts must remain framework-independent.

From Cline, Eris will port the following invariants rather than source code:

- Explicit maximum iteration and retry budgets.
- A completion receipt instead of trusting free-form final text.
- Progress-sensitive retry counters that reset only after material progress.
- Lifecycle events around planning, realization, commit, pause, resume, and failure.
- Fail-closed behavior after repeated output truncation or provider failures.

From Codex, Eris will port these operating principles:

- Continue until a real stopping condition, not merely until the model emits text.
- Incorporate pending user input at safe boundaries.
- Externalize durable state rather than relying on chat history alone.
- Observe outcomes and repair before advancing.
- Compact or summarize state without changing the goal or completion criteria.

No Cline or Codex package, repository, submodule, or service will be added. Any future copied implementation fragment must retain the required Apache-2.0 notice. LangGraph remains an MIT-licensed package dependency used by the existing Agent Harness.

## Ownership Boundaries

### PedagogicalConversationController

Owns:

- Whether a teaching episode should exist.
- The episode goal, concept graph, current objective, and plan revision.
- Comprehension estimates and evidence.
- Selection of the next teaching chunk.
- Continue, wait, repair, complete, abandon, and expire transitions.
- Autonomous continuation budgets and stall detection.

Does not own:

- Natural-language wording.
- Voice, expression, or body performance.
- Factual grounding outside the controller's evidence inputs.
- General social agenda decisions unrelated to the active teaching episode.

### TeachingPlanBuilder

Owns:

- Producing a typed candidate concept graph when a new episode starts.
- Producing a revised candidate graph when evidence proves the current plan inadequate.

Does not own:

- Public wording or delivery.
- Episode lifecycle transitions.
- Comprehension estimates.
- Completion decisions.

The plan builder is a structured model call separate from the public writer. It runs only at episode creation or explicit replan boundaries, not before every spoken chunk. Runtime validation limits the candidate to eight concepts, rejects missing prerequisites or completion evidence, assigns stable IDs, and increments `plan_revision`. If planning fails, Eris answers the current turn normally without opening a hidden episode.

### Public Writer

Owns:

- Natural realization of one locked directive.
- Language, tone, phrasing, and local examples allowed by the directive.
- A truthful semantic frame for the emitted utterance.

Does not own:

- The global teaching goal.
- Which concept comes next.
- Whether the user has understood.
- Whether the episode is complete.
- Scheduling a pedagogical continuation.

### Runtime

Owns:

- Persistence, leases, deadlines, wakeups, and exactly-once commit receipts.
- Checking current interaction state before autonomous speech.
- Cancelling stale timers and suppressing duplicate or unsafe output.
- Routing user events back to the controller.

## Core Contracts

### TeachingEpisode

```python
@dataclass(frozen=True, slots=True)
class TeachingEpisode:
    episode_id: str
    topic_key: str
    global_goal: str
    status: str
    plan_revision: int
    concepts: tuple[TeachingConcept, ...]
    current_concept_id: str
    comprehension: ComprehensionState
    pending_chunk: TeachingChunk | None
    last_committed_chunk_id: str
    last_progress_signature: str
    autonomous_chunks_used: int
    consecutive_stalls: int
    repair_attempts: int
    created_at: float
    updated_at: float
    expires_at: float
```

Allowed statuses:

```text
planning
ready
speaking
dwelling
waiting_user
observing
repairing
completed
abandoned
expired
failed
```

### TeachingConcept

```python
@dataclass(frozen=True, slots=True)
class TeachingConcept:
    concept_id: str
    objective: str
    prerequisites: tuple[str, ...]
    success_evidence: tuple[str, ...]
    misconception_targets: tuple[str, ...]
    status: str
```

The plan is a small dependency graph, not a fixed script. A concept may be skipped, taught, repaired, or revisited based on evidence.

### ComprehensionState

```python
@dataclass(frozen=True, slots=True)
class ComprehensionState:
    estimated_mastery: float
    confidence: float
    understood: tuple[str, ...]
    uncertain: tuple[str, ...]
    misconceptions: tuple[MisconceptionHypothesis, ...]
    cognitive_load: float
    interest: float
    evidence: tuple[ComprehensionEvidence, ...]
```

`mastery` and `confidence` are separate. Weak evidence may adjust confidence without changing mastery.

### TeachingDirective

```python
@dataclass(frozen=True, slots=True)
class TeachingDirective:
    episode_id: str
    plan_revision: int
    chunk_id: str
    global_goal: str
    current_objective: str
    claim: str
    contrast: str
    example_policy: str
    max_ideas: int
    max_sentences: int
    allow_question: bool
    stop_after_chunk: bool
    allow_autonomous_resume: bool
```

The directive is immutable for one writer call. The writer may not silently expand it into later concepts.

### TeachingReceipt

```python
@dataclass(frozen=True, slots=True)
class TeachingReceipt:
    episode_id: str
    plan_revision: int
    chunk_id: str
    public_turn_id: str
    result: str
    text_signature: str
    committed_at: float
```

Only a successful public commit advances the episode. Generated but suppressed, rejected, interrupted, or failed utterances do not consume the chunk.

## State Transitions

### Building the long plan

When teaching intent passes the activation gate, the controller asks `TeachingPlanBuilder` for a typed plan containing:

- One global learning goal.
- Three to eight concepts.
- Prerequisite edges.
- Likely misconceptions expressed as hypotheses.
- Observable success evidence for each concept.
- Optional example strategies, without public prose.

The planner receives the current request, a bounded recent-history projection, relevant grounded memory, and domain evidence. It does not receive authority to schedule speech or mark concepts understood.

The controller validates the result, chooses the first ready concept, and creates the durable episode. Replanning preserves evidence and already-completed concepts where their IDs and meanings remain compatible. A failed or invalid replan leaves the previous valid plan active unless continuing it would be unsafe or nonsensical, in which case the episode moves to `waiting_user`.

### Starting an episode

An episode starts only when at least one of these conditions is present:

- The user explicitly asks to learn, understand, compare, or be guided through a topic.
- The user asks a follow-up that requires a structured explanation and the confidence threshold is met.
- A domain evidence provider proposes teaching and `AdaptiveCompanion` grants teaching permission.

Ordinary factual answers, casual conversation, and fast execution requests remain outside the controller.

### User input

```text
user turn
  -> cancel pending dwell timer and release any claim
  -> preserve active TeachingEpisode
  -> classify typed comprehension evidence
  -> update comprehension conservatively
  -> replan current concept if needed
  -> select one next action
```

Possible next actions are `answer_user`, `teach_chunk`, `repair`, `ask_probe`, `wait`, `complete`, and `abandon`.

User correction invalidates the affected controller hypothesis and enters `repairing`; it is not treated as user misunderstanding.

### Public commit

```text
directive selected
  -> writer realizes directive
  -> grounding and public surface gates run
  -> successful public commit writes TeachingReceipt
  -> episode advances to dwelling or waiting_user
```

The receipt uses `(episode_id, plan_revision, chunk_id)` as an idempotency key. Duplicate callbacks are ignored.

### Dwell

Dwell begins after audio completion, not text generation. Before autonomous continuation, runtime verifies:

- The same episode and plan revision remain active.
- The user is not typing or speaking.
- No user or system interrupt occurred after the anchor.
- The conversation topic is still active.
- The next chunk adds new value.
- The episode has autonomous budget remaining.
- The claim lease can be acquired.

Version 1 defaults:

- At most two autonomous teaching chunks without new user evidence.
- At most one repair retry for the same misconception before waiting for the user.
- At most two consecutive no-progress transitions.
- Episode expiry after ten minutes of inactivity unless explicitly resumed.

These are configuration defaults with hard upper bounds, not model-controlled numbers.

### Completion

The writer cannot complete an episode. Completion requires a controller receipt with one of these bases:

- Strong application or teach-back evidence for all required concepts.
- The user explicitly indicates they have enough and no contradictory evidence is present.
- The user changes or ends the topic and the controller records `abandoned` rather than mastery.

Silence and minimal acknowledgements never produce mastery completion.

## Comprehension Evidence

Initial evidence types:

| Evidence | Mastery effect | Confidence effect |
|---|---:|---:|
| Correct paraphrase in user's own words | strong positive | positive |
| Correct application to a new example | strongest positive | strong positive |
| Focused follow-up using the concept correctly | moderate positive | positive |
| Repeated question about the same distinction | none or slight negative | positive that uncertainty exists |
| Explicit misconception assertion | negative for affected concept | positive |
| User corrects Eris | none for user mastery | invalidates Eris hypothesis |
| `ok`, `uh-huh`, `I understand` | none | weak self-report only |
| Silence | none | none |
| Topic change | none | evidence for stop/abandon, not mastery |

Evidence classification must be typed and auditable. Raw chat and private reasoning are not persisted in the comprehension store; only short evidence codes, refs, and bounded summaries are retained.

## Integration

### New modules

- `runtime/pedagogical_contracts.py`: immutable contracts and validation.
- `runtime/pedagogical_controller.py`: reducer, planning transitions, budgets, and evidence updates.
- `runtime/pedagogical_runtime.py`: durable store, dwell timer, leases, wake callback, and snapshots.
- `runtime/pedagogical_plan_builder.py`: structured long-plan generation and validation boundary.

The modules remain independent of LangGraph and model-provider classes.

### Existing modules

`runtime/ai_orchestrated/orchestrator.py`:

- Observe the active episode early in a user turn.
- Request a directive before `plan_chat` and public writer generation.
- Inject only the bounded directive into compiled public context.
- Submit a receipt only after a successful public commit.
- Notify the pedagogical runtime after audio completion.

`runtime/ai_orchestrated/model_router.py`:

- Add a structured `plan_teaching_episode` call that cannot emit public text or lifecycle decisions.
- Accept a typed `TeachingDirective` projection.
- Lock the writer to the current chunk.
- Reject output that introduces later concepts, exceeds chunk limits, or contradicts the directive.
- Keep ordinary chat behavior unchanged when no directive exists.

`runtime/ai_orchestrated/conversation_arc.py`:

- Remains responsible for social and dramatic arcs.
- Is not expanded into the teaching controller.
- Its timer, claim, and persistence patterns may be factored into a small shared helper only if implementation otherwise duplicates substantial logic.

`runtime/adaptive_engineering_learning.py`:

- Remains the engineering evidence provider.
- May project evidence into a general episode.
- Does not become the owner of general teaching state.

`runtime/adaptive_companion.py`:

- Continues to grant or deny teaching permission and delivery stance.
- Cannot mutate episode progress directly.

## Error Handling

- Invalid persisted state fails closed to no active episode and emits a repair metric.
- A stale plan revision cannot commit or schedule a dwell.
- Provider or writer failure leaves the pending chunk unconsumed.
- Repeated writer contract violations abandon the autonomous continuation and wait for user input.
- Timer callbacks acquire a lease before producing a candidate.
- Restart recovery clears expired claims and rearms only a valid pending dwell.
- All loops have explicit step, repair, autonomous, and time budgets.

## Observability

Emit structured lifecycle events:

```text
pedagogy.episode_started
pedagogy.directive_selected
pedagogy.chunk_committed
pedagogy.dwell_started
pedagogy.dwell_cancelled
pedagogy.evidence_observed
pedagogy.replanned
pedagogy.repair_started
pedagogy.episode_completed
pedagogy.episode_abandoned
pedagogy.episode_expired
pedagogy.contract_rejected
```

Metrics must include episode and concept identifiers, revisions, reason codes, and bounded counts. They must not include raw chat, generated chain-of-thought, or sensitive memory content.

## Configuration

The feature ships behind `ERIS_PEDAGOGICAL_CONTROLLER_ENABLED`, defaulting off during development and evaluation.

Configurable values have runtime-clamped ranges:

- Minimum and maximum dwell.
- Maximum autonomous chunks.
- Maximum repair attempts.
- Maximum consecutive stalls.
- Episode inactivity expiry.
- Writer chunk sentence and idea limits.

The model cannot override hard upper bounds.

## Testing

### Unit tests

- Episode creation requires explicit or sufficiently grounded teaching intent.
- Silence and acknowledgement do not increase mastery.
- Correct paraphrase and application update the intended concept only.
- Candidate misconception routes to repair without becoming a permanent user label.
- User correction invalidates Eris' hypothesis.
- User interruption cancels dwell but preserves the episode.
- Stale receipts and stale timers are ignored.
- Duplicate commit callbacks are idempotent.
- Autonomous and repair budgets terminate loops.
- No-progress signatures trigger wait instead of repeated speech.

### Integration tests

- Orchestrator obtains the directive before calling the writer.
- Writer receives one locked chunk and cannot advance the episode.
- Only a successful public commit advances state.
- Audio completion anchors dwell.
- A user turn wins the race against a due timer.
- Restart during dwell produces at most one continuation.
- Grounding or surface rejection does not consume the chunk.
- Normal non-teaching chat remains unchanged.
- Engineering mentor evidence can feed an episode without gaining delivery authority.

### Behavioral evaluations

Golden scenarios cover:

- Core 7 versus core 8 with behavior/motivation confusion.
- User already knows the first concept and should skip it.
- User repeats the same misunderstanding.
- User gives a correct novel example.
- User says only `ok` and remains silent.
- User interrupts an autonomous continuation.
- User loses interest or changes topic.
- Eris makes a wrong assumption and is corrected.

Acceptance requires fewer repeated explanations and fewer premature mastery claims than the current writer-only baseline, without increasing unsolicited teaching in ordinary conversations.

## Delivery Sequence

1. Add contracts, pure controller transitions, and unit tests.
2. Add durable runtime, receipts, dwell leases, and restart tests.
3. Integrate read-only directive projection into the orchestrator behind the feature flag.
4. Lock writer scope and connect successful public commit receipts.
5. Connect audio completion and autonomous wakeup.
6. Feed engineering mentor evidence through the general contract.
7. Run golden behavioral evaluations and compare against baseline.
8. Enable by default only after race, restart, and false-positive teaching thresholds pass.

## Success Criteria

- Eris can maintain one teaching goal across several user turns and pauses.
- Each public response contains only the controller-selected chunk.
- User interruption never loses the episode or causes duplicate speech.
- Silence never increases mastery.
- Completion is evidence-based and auditable.
- Every loop has explicit finite budgets.
- Existing coding Agent Harness and ordinary chat behavior remain intact.
- No Cline or Codex runtime is embedded into the product.
