import sys

modules = [
    "runtime.cognition.normalization",
    "runtime.input_revision_buffer",
    "runtime.interrupt_reaction_policy",
    "runtime.source_grounding",
    "runtime.public_lifecycle",
    "runtime.temporal_mind",
    "runtime.expression_boundary_preference",
    "runtime.attention_router",
    "runtime.behavior_proposals",
    "runtime.world_event_journal",
    "runtime.intent_model",
    "runtime.world_state",
    "runtime.structured_evidence",
    "runtime.chat_perception",
    "runtime.conversation.projection",
    "runtime.chat_cycle",
    "runtime.runtime_status",
    "runtime.autonomous_loop_runtime",
    "runtime.user_control_runtime",
]

failed = []
for mod in modules:
    try:
        __import__(mod)
        print(f"OK   {mod}")
    except Exception as exc:
        print(f"FAIL {mod}: {exc}")
        failed.append((mod, exc))

if failed:
    print(f"\n{len(failed)} module(s) failed to import.")
    sys.exit(1)
else:
    print("\nAll chat cycle, autonomous loop, and user control modules imported cleanly.")
