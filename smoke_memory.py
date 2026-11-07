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
