# Memory subsystem — remaining work (deferred)

The `feat/core-memory` branch pulled the self-contained part of `memory/` in 5
incremental steps (33 files), all importing cleanly with numpy only:

- Step 1 — leaf: memory_record, schemas, text_normalize_vi, name_validation
- Step 2 — models/cue: claim_provenance, memory_cue, memory_models, memory_candidate
- Step 3 — stores + predicates: json_store_io, predicate_catalog, predicate_registry,
  episodic, semantic, procedural, relational, repair, affective_residue,
  unresolved, active_state_snapshot, event_ledger
- Step 4 — scoring/retrieval/packet: memory_admission, memory_scoring,
  memory_activation, memory_feed_trace, canonical_long_term,
  memory_retrieval_engine, memory_packet_builder
- Step 5a — embedding_index (numpy-only at import; torch/transformers lazy)

## What is intentionally NOT pulled yet, and why

`memory/memory_cognition.py` (the top-level MemoryCognitionCore) and the
`memory/living_user/` sub-package (23 files) are **deferred**. They cross the
memory boundary into packages that do not exist in ErisTutor yet:

- `living_user/` imports:
  - `core.di`                    — present (foundation)
  - `llm.provider_errors`        — MISSING (Core Cognition layer)
  - `runtime.inference_broker`   — MISSING (Runtime layer)
  - `runtime.work_keys`          — stdlib-only leaf (easy to carry)
  - `requests`                   — optional third-party

Tracing those bridges further:
- `llm.provider_errors`  -> `runtime.provider_health`, `llm.key_pool`
- `runtime.inference_broker` -> `runtime.pdf_rollout`, `runtime.broker_state_store`,
  `llm.token_budget`, `runtime.observability`

So completing `memory_cognition` would pull a chain across the `llm` and
`runtime` packages — that is the known `memory <-> llm <-> runtime` dependency
cycle. It belongs to the Core Cognition / Runtime layers, not to this memory
branch.

## Plan

Finish `memory_cognition.py` + `living_user/` AFTER the `llm` and `runtime`
layers land (or at least their leaf bridges: provider_errors, key_pool,
provider_health, inference_broker + its deps, token_budget, work_keys). At that
point restore the full `memory/__init__.py` (the eris version that re-exports
MemoryKernel, MemoryRuntimeServices, LivingUser*, ...) and add a smoke test that
constructs `MemoryCognitionCore`.
