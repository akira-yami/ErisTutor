import sys

modules = [
    "actions",
    "parser_grounding",
    "guard_config",
    "semantic_authority",
    "sender",
    "runtime.coordinator",
    "vision",
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

# Verify key symbol exports
try:
    from actions.executor import ActionExecutor
    from parser_grounding import DecisionParser
    from runtime.coordinator import RuntimeCoordinator
    from vision import AttentionOverseer, SingleInstanceLock
    print("OK   Key integration classes (ActionExecutor, DecisionParser, RuntimeCoordinator, AttentionOverseer, SingleInstanceLock) verified.")
except Exception as exc:
    print(f"FAIL class export verification: {exc}")
    sys.exit(1)

print("\nAll integration and entrypoint modules imported and verified cleanly.")
