import sys

modules = [
    "cortex.contracts",
    "cortex.se",
    "telemetry",
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
    print("\nAll telemetry and contract modules imported cleanly.")
