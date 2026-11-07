"""Import smoke test for the LLM subsystem (incremental)."""
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

MODULES = [
    "llm.contracts",
    "llm.json_sanitizer",
    "llm.model_catalog",
    "llm.provider_policy",
    "llm.provider_errors",
    "llm.dialogue_recovery",
    "llm.prompt_compaction",
    "llm",
]

failed = []
for mod in MODULES:
    try:
        __import__(mod)
        print(f"OK   {mod}")
    except Exception as exc:
        print(f"FAIL {mod}: {exc}")
        failed.append(mod)

if failed:
    sys.exit(1)

print("\nAll step-1 LLM modules imported cleanly.")
