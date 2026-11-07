"""Import smoke test for the foundation group.

Run: python smoke_foundation.py  (from repo root, or in CI)
Goal: import every foundation module and fail fast if a dependency/module is missing.
"""
import importlib
import sys

# Force UTF-8 so output never dies on a cp1252 Windows console.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

MODULES = [
    # leaf utilities ở gốc src/
    "failure_meta",
    "secret_versions",
    "eris_secrets",
    "app_logging",
    "runtime.secure_redaction",
    # packages
    "domain",
    "transport",
    "core",
    "screen",
]

failed = []
for name in MODULES:
    try:
        importlib.import_module(name)
        print(f"OK   {name}")
    except Exception as exc:  # noqa: BLE001 - smoke test intentionally catches broad
        failed.append((name, repr(exc)))
        print(f"FAIL {name}: {exc!r}")

if failed:
    print(f"\n{len(failed)} foundation module(s) failed to import.")
    sys.exit(1)
print("\nAll foundation modules imported cleanly.")
