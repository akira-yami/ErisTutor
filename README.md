# ErisTutor

Clean, incremental rebuild of the Eris runtime, assembled layer by layer so each
functional group lands on its own branch, passes CI, and merges independently.

All target machines are **Windows**; parts of the runtime use Windows-only APIs
(`msvcrt`, `tasklist`, `CREATE_NO_WINDOW`), so CI runs on `windows-latest`.

## Layers

The rebuild follows the dependency order discovered in
[`.agents/tasks/dependency-map-and-branch-plan.md`](.agents/tasks/dependency-map-and-branch-plan.md):

| Layer | Branch | Packages | Status |
|-------|--------|----------|--------|
| 1. Foundation | `feat/foundation` | `domain`, `transport`, `core`, `screen`, `app_logging`, `eris_secrets`, `failure_meta`, `secret_versions`, `runtime.secure_redaction` | in progress |
| 2. Core cognition | `feat/core-cognition` | `llm`, `memory`, `cortex` | planned |
| 3. Runtime & orchestration | `feat/runtime-orchestration` | `runtime`, `actions`, `parser_grounding`, `telemetry`, `vision.py` | planned |

Foundation must merge to `main` before layers 2 and 3 can be tested end to end.

## Foundation layer

The foundation modules import on the Python standard library alone — no third-party
package is required just to import or test them. Screen OCR/vision workers
(`mss`, `Pillow`, `pytesseract`, …) are lazy-imported, so leaving those packages
uninstalled does not break the layer; install the optional extras only when a
feature needs them (see `requirements.txt` and `pyproject.toml [screen]`).

## Requirements

- Python >= 3.11 (CI and local dev pinned to 3.12)
- Windows

## Running CI checks locally

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt

$env:PYTHONUTF8 = "1"
$env:PYTHONPATH = "src"

.\.venv\Scripts\ruff.exe check src       # lint
.\.venv\Scripts\python.exe smoke_foundation.py   # import smoke test
.\.venv\Scripts\python.exe -m pytest -q  # unit tests
```

CI (`.github/workflows/ci.yml`) runs the same three gates on every push to
`main` / `feat/**` and on pull requests.
