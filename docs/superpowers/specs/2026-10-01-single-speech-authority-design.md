# Single Speech Authority And Legacy Speech Path Removal Design

## Status

Approved in chat for written specification review.

## Problem

The V12 conversation foundation exists in the working tree, but production still has multiple ways to create and publish assistant speech. Foreground chat has begun moving through `ConversationKernel` and `RootAgent`, while autonomous self-turns, social continuation, conversation arcs, pedagogical continuation, tutor research follow-ups, and greeting compatibility code can still select content, call a writer, commit an assistant message, or enter public delivery without returning through the V12 authority boundary.

`SpeechPlanLedger` and the V7-V11 speech contracts also remain semantic state machines. They can still represent what Eris should say next even though V12 intends `NextConversationMove` to be the only public semantic decision.

This split authority makes it impossible to prove that a public utterance was selected by the root agent. It also allows autonomous or opening events to become force-speak paths when the correct decision is silence.

## Goals

1. Make `ConversationKernel` the only ingress for any user or system event that may lead to public speech.
2. Make `RootAgent` the only component that decides whether Eris speaks and, if so, what the speech means.
3. Represent deliberate silence as a first-class `NoReply` decision.
4. Make the kernel the only caller of the public writer.
5. Make one delivery adapter the only owner of assistant-message commit and public transport entry.
6. Reduce continuation scheduling to mechanical leases, generation checks, interruption checks, and finite execution permission.
7. Make tutor, work, research, social, autonomous, and opening components data-producing capabilities rather than public responders.
8. Delete the V7-V11 semantic speech architecture after its final consumer is removed.
9. Add architecture tests that prevent public authority from spreading again.

## Non-Goals

- Adding new conversation features, social policies, tutor behavior, voice behavior, or work-agent behavior.
- Improving the quality of autonomous topics or opening greetings.
- Redesigning TTS synthesis, stage animation, or nonverbal body motion.
- Building persistent conversation notes or workspaces beyond the existing V12 plain-text note contract.
- Implementing interruption/backchannel voice behavior beyond preserving the authority boundary already required by V12.
- Preserving V3-V11 APIs whose only consumers are obsolete tests.

## Architectural Decision

V12 will use one hard public-speech pipeline:

```text
User/Event
    -> ConversationKernel
    -> RootAgent
    -> ConversationDecision
         |-> NoReply
         `-> NextConversationMove
                 -> PublicWriter
                 -> ConversationDeliveryAdapter
                 -> DeliveryReceipt
                 -> ConversationKernel
```

All inputs that can result in language visible or audible to the user enter the kernel as `ConversationEvent`. Components outside the kernel may collect evidence, perform work, maintain domain state, and emit typed signals. They may not decide public wording, call a public writer, append an assistant turn, or invoke public delivery.

Nonverbal presence that contains no linguistic content may continue through the stage/body path without a conversation decision. It must not carry display text, speech text, or an assistant-message commit.

## Core Contracts

### ConversationEvent

`ConversationEvent` is the normalized ingress envelope. Its `kind` distinguishes inputs such as:

- `user_turn`
- `continuation_due`
- `social_signal`
- `autonomous_signal`
- `opening_signal`
- `tutor_result`
- `work_result`
- `research_result`

The event contains identity, session, generation, bounded text where appropriate, and typed metadata. Metadata may contain evidence and capability results. It may not contain a proposed public utterance, a writer-ready script, a deferred speech beat, or a semantic continuation objective.

### ConversationDecision

The root agent returns exactly one of two decisions:

```python
ConversationDecision = NoReply | NextConversationMove
```

`NoReply` means the root agent inspected the event and deliberately chose not to publish speech.

```python
@dataclass(frozen=True, slots=True)
class NoReply:
    reason: str
    decision_id: str
```

`reason` is bounded observability data. It is not public text and does not create a timer, writer call, delivery, or assistant turn.

`NextConversationMove` remains the only positive speech decision:

```python
@dataclass(frozen=True, slots=True)
class NextConversationMove:
    intent: str
    say: str
    delivery: str
    after: ConversationAfter
    move_id: str
```

`intent` and `say` own semantic content. `delivery` is a presentation hint. `after` is lifecycle metadata with the values `wait_user`, `may_continue`, and `continue_self`. No floor, beat, sentence quota, teaching directive, planned thread, or hidden next utterance is added to this contract.

### RootAgent

`RootAgent` receives the normalized event, bounded public history, conversation note, and capability context. It decides `NoReply` or `NextConversationMove`.

For non-user events, an invalid model response, unavailable model, or empty response fails closed to `NoReply`. Runtime must never invent speech merely to satisfy a schema.

For a direct user turn that requires a response, a deterministic degraded move may be used to acknowledge or answer the user. This exception remains a root-agent decision and still passes through writer and delivery. It is not available to autonomous, social, continuation, or opening events.

### PublicWriter

The public writer realizes one `NextConversationMove`. It owns natural wording and bounded presentation details, but cannot change `after`, open another semantic thread, select a tutor move, or decide whether to speak.

Only `ConversationKernel` may call the public writer. `NoReply` never reaches the writer.

### ConversationDeliveryAdapter

The delivery adapter accepts a kernel-authorized rendered surface plus the active generation, run, and move identities. It owns:

- The exactly-once assistant-message commit.
- Display and speech surface separation.
- The high-level call into existing stage/transport/TTS machinery.
- Delivery start, success, failure, and audio-finished receipts.
- Rejection of stale or duplicate delivery attempts.

No tutor, autonomous runtime, social runtime, opening runtime, work runtime, research runtime, or orchestrator helper may append an assistant message or call public transport directly.

Low-level TTS implementation remains internal to the delivery stack. The architecture restriction applies to public TTS entry: only the delivery adapter may initiate a public delivery that can reach TTS.

## ConversationKernel Lifecycle

`ConversationKernel.handle_event(...)` becomes the single public-speech entry point.

```text
receive event
  -> validate session and generation
  -> preempt stale work when required
  -> gather bounded capability context
  -> ask RootAgent for ConversationDecision
  -> NoReply: record mechanical completion and stop
  -> NextConversationMove: invoke writer
  -> authorize delivery adapter
  -> accept exactly one delivery receipt
  -> schedule or clear continuation lease from `after`
```

The kernel owns lifecycle and concurrency state only: generation, run identity, move identity, delivery identity, phase, receipts, and a plain-text agent-owned note. It does not store public text for replay or infer future meaning from delivery metadata.

## Continuation

There is one continuation route:

```text
NextConversationMove.after
    -> ContinuationLeaseScheduler
    -> ConversationKernel.handle_event(continuation_due)
    -> RootAgent
    -> ConversationDecision
```

The scheduler may know only:

- Whether a lease exists and is due.
- Whether its session and generation are current.
- Whether the user or a newer event preempted it.
- Whether the lease is pending, claimed, consumed, released, or cancelled.
- Whether mechanical continuation permission remains.

It may not store a topic, objective, intended wording, speech act, deferred beat, teaching chunk, social recovery line, or planned thread. A due lease does not imply speech: the root agent may return `NoReply`.

`SocialContinuationRuntime`, conversation-arc public scheduling, pedagogical public continuation, and agent speech-plan continuation must stop acting as independent semantic schedulers. Domain state that remains useful is projected as evidence or a signal into the next root-agent decision.

## Capability Boundaries

### TutorCapability

Tutor owns diagnosis, learner evidence, mastery state, and pedagogical advice. It returns typed data to the root agent. It cannot create a public decision, call a writer, append an assistant message, or initiate delivery.

Delivery observations may be returned to tutor as receipts so its private domain state stays consistent. Receipt observation does not grant public authority and does not schedule another public turn.

### WorkCapability And ResearchCapability

Coding, work, and research agents may run tools, checkpoint long tasks, request permission, and produce artifacts. When user-visible communication is needed, they emit a typed result/event to the kernel. The root agent then returns `NoReply` or a `NextConversationMove`.

### Social, Autonomous, And Opening Inputs

These components may emit facts such as elapsed idle time, a grounded observation, a safe opening opportunity, an affective signal, or a completed social-domain update. They do not emit `Decision`, writer prompts, public candidates, or ready-to-speak text.

Opening and autonomous events are therefore opportunities, not commands to speak.

## SpeechPlanLedger Migration And Deletion

During cutover, any unavoidable `SpeechPlanLedger` compatibility view is read-only and derived from current V12 mechanical state. It must not expose or synthesize `next_intent`, `planned_threads`, public text, turn floor, resume semantics, or a claim operation.

After the last compatibility consumer is migrated:

- Delete `SpeechPlanLedger` and `SpeechPlanSnapshot`.
- Delete `TurnProjection` and the old conversation-floor runtime.
- Delete `DialogueBeat`, `ConversationSpeechPlan`, and speech-plan validation/director logic.
- Delete social continuation policy and semantic scheduler code that no longer has a non-public responsibility.
- Delete the V12-to-V11 semantic adapter.
- Retire V3-V11 tests that assert the deleted architecture, replacing still-valid behavior assertions with V12 tests.

Deletion is part of this slice, not a future compatibility promise.

## Error Handling

- Invalid non-user root output becomes `NoReply`.
- Writer failure produces no assistant commit and no continuation lease.
- Delivery failure produces one failure receipt and no continuation lease.
- Duplicate receipts are ignored.
- A stale generation cannot call writer, commit delivery, or schedule continuation.
- A user turn cancels the current continuation lease before root evaluation.
- A claimed lease is released on root or writer failure and may be retried only while its generation remains current.
- Capability failure removes that capability result from context; it does not create fallback speech outside the root agent.
- Public delivery side effects remain idempotent under no-TTS, fast-audio, and duplicate-callback races.

## Architecture Tests

Architecture tests inspect Python syntax and imports rather than relying only on runtime mocks.

They fail when:

1. A module outside `ConversationKernel` calls a public writer entry.
2. A module outside the delivery boundary initiates public transport or TTS.
3. A module outside the delivery boundary appends or records an assistant message.
4. Tutor, social, autonomous, opening, work, or research code constructs and commits a public response.
5. A semantic continuation scheduler other than `ContinuationLeaseScheduler` remains reachable from production bootstrap.
6. A continuation lease contains semantic fields or public text.
7. `SpeechPlanLedger`, `TurnProjection`, `DialogueBeat`, or `ConversationSpeechPlan` remains imported by production code after deletion.
8. An autonomous or opening event necessarily calls writer instead of allowing `NoReply`.

Behavioral and race tests cover:

- A user turn produces at most one root decision and one public commit.
- An idle autonomous event may return `NoReply` with zero writer and delivery calls.
- A positive autonomous decision uses the same writer and delivery path as user chat.
- A due continuation may return `NoReply` and is consumed without speech.
- A new user generation beats a due or claimed continuation.
- A stale writer result cannot commit.
- Duplicate audio-finished callbacks schedule at most one lease.
- Tutor and work results are visible to root but cannot publish independently.
- No-TTS delivery still settles exactly once.

## Delivery Sequence

1. Add failing architecture tests for writer, assistant commit, public transport/TTS, semantic scheduler, and capability bypasses.
2. Add `NoReply` and `ConversationDecision`; update root-agent model and deterministic failure behavior.
3. Make `ConversationKernel.handle_event` own root decision, writer invocation, and delivery authorization.
4. Introduce `ConversationDeliveryAdapter` around the existing public output machinery and centralize assistant commits.
5. Route foreground user turns and V12 continuation receipts through the new boundary.
6. Convert social continuation, conversation arc, pedagogical continuation, and autonomous self-turn into event/evidence producers.
7. Convert tutor research, work/research completions, and opening compatibility paths into capability events.
8. Remove remaining public-writer, assistant-commit, and transport call sites outside the authority boundary.
9. Make the temporary speech-plan compatibility view read-only, migrate its final consumers, then delete the V7-V11 speech architecture and obsolete tests.
10. Run focused conversation tests, architecture tests, affected integration tests, and the full repository suite.

## Success Criteria

- Every linguistic public output has one traceable path through `ConversationKernel`, `RootAgent`, public writer, and `ConversationDeliveryAdapter`.
- Every public semantic choice is a `ConversationDecision` authored by `RootAgent`.
- Autonomous, opening, social, tutor, work, and research events can result in `NoReply` without invoking writer or delivery.
- Exactly one mechanical continuation scheduler remains.
- No capability or legacy runtime can independently commit an assistant message or initiate public TTS.
- `SpeechPlanLedger`, `TurnProjection`, `DialogueBeat`, `ConversationSpeechPlan`, legacy floor logic, and obsolete semantic continuation policy are deleted.
- Architecture tests make all listed bypasses fail in CI.
- Existing V12 conversation behavior, no-TTS settlement, stale-generation rejection, and exactly-once delivery remain green.
