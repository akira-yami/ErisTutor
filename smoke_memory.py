"""Import smoke test for the memory subsystem (incremental).

Only covers the modules pulled so far. Extended each step as more land.
"""
import importlib
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

MODULES = [
    # Step 1 — leaf modules (stdlib only)
    "memory.memory_record",
    "memory.schemas",
    "memory.text_normalize_vi",
    "memory.name_validation",
    # Step 2 — models & cue (build on step 1)
    "memory.claim_provenance",
    "memory.memory_cue",
    "memory.memory_models",
    "memory.memory_candidate",
    # Step 3 — persistence helpers, predicates & memory stores
    "memory.json_store_io",
    "memory.predicate_catalog",
    "memory.predicate_registry",
    "memory.episodic_memory",
    "memory.semantic_memory",
    "memory.procedural_memory",
    "memory.relational_memory",
    "memory.repair_memory",
    "memory.affective_residue_memory",
    "memory.unresolved_memory",
    "memory.active_state_snapshot",
    "memory.event_ledger",
    # Step 4 — scoring, retrieval & packet building
    "memory.memory_admission",
    "memory.memory_scoring",
    "memory.memory_activation",
    "memory.memory_feed_trace",
    "memory.canonical_long_term",
    "memory.memory_retrieval_engine",
    "memory.memory_packet_builder",
    # Step 5a — embedding index (numpy only at import; torch/transformers lazy)
    "memory.embedding_index",
]

failed = []
for name in MODULES:
    try:
        importlib.import_module(name)
        print(f"OK   {name}")
    except Exception as exc:  # noqa: BLE001
        failed.append((name, repr(exc)))
        print(f"FAIL {name}: {exc!r}")

if failed:
    print(f"\n{len(failed)} memory module(s) failed to import.")
    sys.exit(1)
print("\nAll pulled memory modules imported cleanly.")
