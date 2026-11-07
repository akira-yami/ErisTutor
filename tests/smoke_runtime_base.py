import sys

modules = [
    "runtime.architecture_flags",
    "runtime.workspace_manager",
    "runtime.runtime_settings_store",
    "runtime.no_fallback_policy",
    "runtime.eris_voice_contract",
    "memory.understanding",
    "runtime.memory_semantic_parser_adapter",
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
    print("\nAll runtime base modules imported cleanly.")

