# Learning Canvas: Godot → Local Web (2026-11-02)

## Architecture

- **UI:** `src/body/richchat/canvas.html`, `canvas.css`, `canvas.js` (responsive browser page; graph, text, Java code and exercises). No Godot panel, graph, code editor, quiz controls or playback widgets remain.
- **Launcher:** the existing `Learning Canvas (web)` button in the Eris context menu opens the same **127.0.0.1-only, tokenized** Rich Chat HTTP listener at `/canvas.html`. Rich Chat also has a Learning Canvas link.
- **Thin host adapter:** `src/body/UdpListener.WebLearningCanvas.cs` forwards strictly whitelisted browser commands (`api/canvas/request`) via the existing Godot UDP client on the **main thread**. It maintains a read-only state projection for `api/canvas/state`. HTTP workers never use the Godot UDP object.
- **Backend unchanged:** `src/runtime/learning_canvas/` remains the source of truth for focus, graph, mastery, quizzes, Java execution, assistive actions, playback and persistence. The web page does not grade or calculate mastery independently.
- **Feedback:** browser commands carry a unique request ID, focus revision, and exercise activity/nonce. Backend ACKs (last 16), state snapshots, activity updates and playback events are exposed via polling. The browser tolerates reordered focus responses and can recover after backend playback-stream restart.
- **Security:** loopback only, random 24-byte URL token, exact Host and Origin checks for the Canvas POST route, small bounded command queue, schema field allowlist, 12 KB Java code budget, restrictive CSP, no model-provided HTML inserted into DOM.

## Run

1. Apply the onlychange ZIP into the **root folder containing `eris/`**, keeping paths as-is; remove the files in `DELETED_FILES.txt`.
2. Re-export/build the **Godot .NET** body because C# and embedded browser resources changed. A stale `.pck` export is intentionally rejected by the existing build freshness check.
3. Start ERIS normally (`run_eris.bat` or the project's normal runtime startup). Open the character's context menu → **Learning Canvas (web)**, or open **Rich Chat → Learning Canvas**.
4. The browser uses an ephemeral `http://127.0.0.1:<port>/<token>/canvas.html` URL; it is not a public hosted site, and the ERIS/Godot process must remain running. Do **not** open `canvas.html` directly from disk.

## Verification

- `pytest -q src/tests/test_learning_canvas.py src/tests/test_learning_canvas_exercises.py src/tests/test_web_learning_canvas_migration.py src/tests/test_build_embedded_asset_freshness.py`
- `node --check src/body/richchat/canvas.js`
- An offline Chromium UI smoke run against the existing fixture exercised graph navigation, multiple choice, Java editor/submit and mobile layout. Running Godot .NET and testing its UDP↔HTTP bridge end-to-end on Windows is still recommended.

## Retired assets

`src/body/UdpListener.LearningCanvas{,Exercises,Playback}.cs` (+ `.uid`), `tools/ui-preview/LearningCanvasPreview.cs`, `tools/ui-preview/learning_canvas_preview.tscn`, and the old fixture location. The fixture is now `tools/web-canvas/learning_canvas_fixture.json`; `tools/learning_canvas_demo.py --write-fixture` writes there.
