import sys

modules = [
    "runtime.subsystem_health",
    "runtime.policy_settings",
    "runtime.interaction_dynamics",
    "runtime.open_loop_store",
    "runtime.stage_protocol",
    "runtime.transport_boundary",
    "runtime.incoming_dispatcher",
    "runtime.micro_reflex_system",
    "runtime.presence_controller",
    "runtime.coordinator_shutdown",
    "runtime.incoming_protocol_runtime",
    "runtime.settings_catalog",
    "runtime.settings_protocol",
    "runtime.settings_control_runtime",
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
    print("\nAll 14 coordinator lifecycle, protocol, and settings modules imported cleanly.")
