# ErisTutor — Dependency Map & 3-Branch Rebuild Plan

Quick, read-only investigation of `eris/src/vision.py` and its local package graph, to plan an incremental rebuild of ErisTutor across 3 developer branches with CI/CD. Nothing was modified.

---

## Summary answer

`vision.py` is the top-level orchestrator. It pulls in **10 local packages** plus 3 top-level modules (`app_logging`, `eris_secrets`, and the implicit `runtime.*` tree). The graph has a clear shape:

- **Clean leaves** (no cross-package deps, trivial to isolate first): `domain`, `transport`, `core`, `screen`.
- **Mid** (depend on a few things): `actions`, `llm`, `parser_grounding`, `telemetry`, `app_logging`.
- **Core / heavy** (deeply interconnected hubs — build last): `runtime` (504 files, imports 9 other local packages), `memory` (105 files), `cortex` (179 files).

The practical bottleneck is **`runtime`**: it imports actions, core, cortex, domain, llm, memory, screen, telemetry, and transport. It must be the last group assembled. Everything else can be layered under it.

**Biggest surprise:** `app_logging.py` imports `runtime.secure_redaction`, so logging is **NOT** a clean leaf — the foundation group must carry that one redaction module (or a stub) or nothing that logs will import.

---

## Evidence

### 1. `vision.py` first-level local imports

From `C:\Users\Gtvkun\Documents\GitHub\eris\src\vision.py` (top of file):

```python
from actions import ActionExecutor
from app_logging import configure_logging, get_logger
from domain import Decision
from llm import LlmService
from llm.process_manager import RemoteModelProcessManager
from runtime.architecture_flags import (...)           # background_tutor_enabled, inner_diary_*, legacy_model_lanes_enabled, ...
from memory.memory_cognition import MemoryCognitionCore
from parser_grounding import DecisionParser
from runtime.coordinator import RuntimeCoordinator
from runtime.memory_semantic_parser_adapter import build_runtime_memory_semantic_parser
from runtime.tts_engine import build_tts_engine
from runtime.workspace_manager import ErisHome
from telemetry import TelemetryService
from transport import UdpTransport
```

Plus, inside `__main__`: `from eris_secrets import apply_secrets_to_environ, log_config_sources`, and inside `AttentionOverseer.__init__`: `from runtime.runtime_settings_store import RuntimeSettingsStore`.

So the entrypoint touches: **actions, app_logging, domain, llm, runtime (×5 submodules), memory, parser_grounding, telemetry, transport, eris_secrets**.

### 2. Package size + cross-package dependency table

File counts from `eris/src/`; cross-package deps from grepping each package's `from X / import X` lines against the set of local packages.

| Package           | Files | Cross-package deps (local)                                        | Class       |
|-------------------|-------|-------------------------------------------------------------------|-------------|
| `domain`          | 2     | (none)                                                            | **LEAF**    |
| `transport`       | 2     | (none)                                                            | **LEAF**    |
| `core`            | 7     | (none)                                                            | **LEAF**    |
| `screen`          | 11    | (none)                                                            | **LEAF**    |
| `app_logging`     | 1     | `runtime.secure_redaction`                                        | LEAF*⚠      |
| `eris_secrets`    | 1     | (none local — stdlib/json only)                                   | **LEAF**    |
| `telemetry`       | 4     | `cortex`                                                          | **MID**     |
| `actions`         | 7     | `cortex`, `runtime`, `screen`, `eris_secrets`                     | **MID**     |
| `parser_grounding`| 3     | `actions`, `domain`, `llm`, `runtime`                             | **MID**     |
| `llm`             | 21    | `actions`, `core`, `cortex`, `memory`, `runtime`, `eris_secrets`  | **MID/HEAVY**|
| `memory`          | 105   | `core`, `cortex`, `llm`, `runtime`                                | **CORE**    |
| `cortex`          | 179   | `core`, `llm`, `memory`, `runtime`                                | **CORE**    |
| `runtime`         | 504   | actions, core, cortex, domain, llm, memory, screen, telemetry, transport | **CORE (hub)** |

`*` `app_logging` would be a leaf except for its single `runtime.secure_redaction` import — see surprises below.

Note the **cycle**: `llm ↔ memory ↔ cortex ↔ runtime` all import each other. These four cannot be cleanly separated into independent branches; they form one tightly-coupled core that should be rebuilt together or in a carefully staged order with interface stubs.

### 3. Windows-only code in `vision.py` (expected, all machines are Windows — leave as-is)

- `import msvcrt` and `msvcrt.locking(...)` — single-instance lock (`SingleInstanceLock`).
- `subprocess.run(["tasklist", "/NH"], ...)` in `_is_body_running` — Windows process check for `godot`/`eris`.
- `creationflags = 0x08000000` (`CREATE_NO_WINDOW`) when spawning WebAI/DS2API daemons.
- `.venv\Scripts\python.exe` hardcoded path for WebAI daemons.
- `signal.SIGBREAK` guarded by `hasattr` (Windows-only signal).

These are fine for the all-Windows fleet but should be noted as non-portable if a Linux CI runner is ever used — **CI must run on `windows-latest`** for anything that imports these paths.

### 4. Runtime / build config

- `eris/requirements.txt` and `eris/pyproject.toml` both present.
- `requires-python >= 3.11`.
- Runtime deps: numpy, Pillow, psutil, requests, fastapi, uvicorn, mss, playwright, fsrs, sounddevice, miniaudio.
- `pyproject` `packages-find` includes: actions, config, core, cortex, domain, llm, memory, models, parser_grounding, runtime, screen, settings, telemetry, transport, experiments, tools.
- `eris/src/tests` contains ~365 test files — a substantial existing suite to mine for per-group tests.

---

## Dependency surprises (flags)

1. **`app_logging.py` → `runtime.secure_redaction`** (confirmed by reading the file). Logging is not a free-standing leaf. Any module that calls `configure_logging()`/`get_logger()` transitively needs `runtime.secure_redaction`. Options for the foundation branch: (a) copy only `runtime/secure_redaction.py` alongside `app_logging.py`, or (b) ship a thin redaction stub and swap it later. Pick (a) — copy the one real module — so logging behaves identically from day one.
2. **`telemetry` → `cortex`** — telemetry looks small (4 files) but reaches into the heavy `cortex` tree, so it cannot ship before cortex exists. If the dev needs telemetry early, isolate the cortex touchpoint behind an interface.
3. **`actions` → `runtime` + `cortex`** — `ActionExecutor` (used directly by `vision.py`) is not a leaf; it depends on both heavy packages. It must land with or after the core group.
4. **Four-way cycle `llm ↔ memory ↔ cortex ↔ runtime`** — these resist clean branch separation. Treat them as one "core cognition" unit owned by one dev, not split across three.
5. **`runtime` is the universal hub** (imports 9 of the other packages). It is effectively the integration layer; it can only be finished once its dependencies exist.

---

## Proposed split: 3 devs, dependency-ordered branches

Execution is **sequential by dependency layer**, but within a layer the pieces are independent and can be worked in parallel. Recommended ownership:

### Group A — Foundation / Contracts (Dev 1) — FIRST, blocks everyone
- **Packages:** `domain`, `transport`, `core`, `screen`, `eris_secrets`, `app_logging` (+ the single `runtime/secure_redaction.py` it needs).
- **Why first:** all are clean leaves (except the one logging dep, handled above). Nothing else can import or be tested without these contracts and the logging/config bootstrap.
- **Branch:** `feat/foundation`
- **Independent?** Internally yes — these packages don't depend on each other (except logging→secure_redaction). Everyone else depends on this branch merging first.

### Group B — Core Cognition (Dev 2) — SECOND, the heavy middle
- **Packages:** `llm`, `memory`, `cortex` (the cyclic cluster) + `core/di` wiring.
- **Why together:** they import each other cyclically; splitting them across branches would create merge/interface churn. One owner keeps the cycle coherent.
- **Branch:** `feat/core-cognition`
- **Independent?** No — depends on Group A. This is the largest, longest-running branch (≈300+ files). Start it as soon as A's contracts are stable, even before A fully merges, by coding against A's interfaces.

### Group C — Runtime, Actions & Orchestration (Dev 3) — LAST, integration
- **Packages:** `runtime` (the hub), `actions`, `parser_grounding`, `telemetry`, and finally `vision.py` itself (the entrypoint + `AttentionOverseer`, `SingleInstanceLock`, daemon bootstrap).
- **Why last:** `runtime` imports everything; `actions`/`parser_grounding`/`telemetry` depend on the core cluster; `vision.py` wires them all together.
- **Branch:** `feat/runtime-orchestration`
- **Independent?** No — depends on A and B. This is the integration branch that produces a runnable `vision.py`.

### Ordering & parallelism summary
```
Layer 1 (parallel-safe within):  A = domain, transport, core, screen, eris_secrets, app_logging   <- merge first
Layer 2 (one owner, cyclic):     B = llm, memory, cortex                                            <- after A
Layer 3 (integration):           C = runtime, actions, parser_grounding, telemetry, vision.py       <- after B
```
If the user wants earlier parallelism: Dev 3 can build `telemetry` + `actions` **interface stubs** and the `vision.py` skeleton against A's contracts while Dev 2 fills in the core, then wire real imports once B lands. Keep the sequencing honest — don't merge C before B compiles.

---

## CI/CD: minimal per-push GitHub Actions (Windows)

All machines are Windows and the code uses msvcrt/tasklist/`creationflags`, so **the runner must be `windows-latest`** (a Linux runner will fail at import on `import msvcrt`).

Per-push workflow (`.github/workflows/ci.yml`), triggered on push to each `feat/*` branch and on PR:

1. **Prereqs:** `actions/setup-python` with **Python 3.11+**; `pip install -r requirements.txt` (and `pip install -e .` so the `pyproject` packages resolve). Cache pip for speed.
2. **Lint:** `ruff check` using the project's `pyproject.toml` ruff config. Fail the build on lint errors.
3. **Import smoke test:** `python -c "import <group modules>"` for the packages that branch owns (e.g. `import domain, transport, core` for Group A). This catches the cross-package surprises (e.g. a missing `runtime.secure_redaction`) immediately and cheaply.
4. **Unit tests:** `python -m pytest eris/src/tests/<relevant subset>` (or `-k <group>`), scoped to the branch's packages so each dev's CI stays fast. Expand to the full ~365-test suite on the integration branch (C) / merges to main.

Prerequisites to call out in the workflow: Python ≥ 3.11, `requirements.txt` install, editable install of the package, Playwright browsers only if the branch actually exercises `playwright` (skip otherwise to keep CI quick). Keep per-branch CI minimal; run the full suite + full import smoke on the integration branch and on `main`.

---

## Recommendations (no code changed)

- Rebuild in the three layers above; **merge A before starting integration on C**.
- Treat `llm/memory/cortex/runtime` as a coupled core — resist splitting the cycle across devs.
- Copy `runtime/secure_redaction.py` into the foundation branch alongside `app_logging.py`, or logging breaks on import.
- Pin CI to `windows-latest` and Python 3.11; scope pytest per branch for speed, full suite on merges.
- Don't copy `experiments/`, `tools/`, `patches/`, `powers/`, `research/`, `evals/`, `integration(s)/`, `native/`, `vscode-extension/`, model-weight folders, or `__pycache__` into the clean repo yet — none are on `vision.py`'s import path and they bloat the rebuild. Add them later only when a feature needs them.
