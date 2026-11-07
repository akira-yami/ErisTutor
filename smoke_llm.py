"""Import smoke test for the LLM subsystem (incremental)."""
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

MODULES = [
    # Step 1
    "llm.contracts",
    "llm.json_sanitizer",
    "llm.model_catalog",
    "llm.provider_policy",
    "llm.provider_errors",
    "llm.dialogue_recovery",
    "llm.prompt_compaction",
    # Step 2
    "llm.key_pool",
    "llm.token_budget",
    "llm.token_pressure",
    "llm.dds",
    "llm.web_session_guard",
    # Step 3
    "llm.raw_trace_store",
    "llm.raw_trace",
    # Step 4
    "llm.deepseek",
    "llm.deepseek_workers",
    "llm.client",
    "llm.process_manager",
    # Step 5
    "llm.tutor_backend",
    "llm.service",
    # Runtime support
    "runtime.diagnostic_logging",
    "runtime.observability",
    "runtime.pdf_rollout",
    "runtime.broker_state_store",
    "runtime.inference_broker",
    "runtime.rollout_control",
    "runtime.hardening_metrics",
    "runtime.provider_health",
    "runtime.work_keys",
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

