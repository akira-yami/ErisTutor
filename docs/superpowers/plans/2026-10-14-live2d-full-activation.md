# Live2D INFJ full activation implementation plan

> **For agentic workers:** Use test-driven-development for each code change and independent read-only review before deployment. Tasks have disjoint ownership; preserve concurrent conversation-runtime changes.

**Goal:** Apply the user's INFJ correction table and activate its expressions/motions while the mouth follows real ElevenLabs playback.

**Architecture:** Keep the existing native Live2D renderer, stage state and TTS event contract. Correct their parameter domains and ownership; use the native expression/motion APIs already installed. Authoring/model repairs must be made on backups and validated by native loading and controlled renders.

**Tech Stack:** Python, Cubism Native 5.1 via installed live2d-py, GLFW/OpenGL, JSON assets, pytest.

**Spec:** User's six-row correction table and `docs/reports/2026-10-14-live2d-model-audit.md`, explicitly approved for full application.

## Global constraints

- Mouth closed/half/open = 0/.5/1; voiced curve .22..88; open/close response 48/64.
- Silence/missing waveform remains closed; start/done/cancel ownership from real TTS playback stays intact.
- Per-eye X: negative inward, positive outward; coherent gaze uses opposite signs.
- Eye defaults .44/.30; inward ratio .82.
- Clamp cornerL/R to -1..1 and lipPress/tongue to 0..1; propagate all four from affect through stage to renderer.
- Physics Rotation5/6/7 uses vertex1/2/3; position and angle normalization±15.
- Native model remains loadable; no blind binary offsets or guessed keyform coordinates.
- Preserve user's original CMO3 and current compiled model with backups before authoring changes.
- Do not touch concurrent `src/runtime/conversation` edits, secrets, or provider credentials. No real TTS request/audio needed for automated validation.

## Review focus

- Idle/terminal stages cannot reopen the speech mouth or keep custom mouth parameters stuck.
- Motion/expression calls occur on edges, and their outputs are not overwritten by default stage parameters.
- Asset playback cannot steal mouth ownership from ElevenLabs; muted output remains closed.
- Every emitted emotion/motion/prosody label either maps explicitly or has a documented safe behavior.
- Combined native rig poses are validated visually, not merely by metadata or mocked tests.

### Task 1: Correct parameter/profile/affect and stage semantics

Own `src/live2d_body/parameter_profile.py`, `affect.py`, `stage_state.py`, `speech_animation.py`, `idle.py`, corresponding existing tests and a new profile/native contract test.

- [ ] Write regressions for 0/.5/1, bounded voiced output, custom clamps/reset and catalog coverage; watch expected failures.
- [ ] Implement mouth response48/64, all aliases/targets/styles, custom affect channels and head amplitudes in proper parameter units.
- [ ] Run affected tests and verify waveform/lifecycle invariants remain intact.

### Task 2: Correct assets and activate native playback

Own `src/live2d_body/config.py`, `renderer.py`, any focused asset playback helper and tests, JSON motions/physics/expressions under `src/assets/live2d/infj`. Do not modify MOC3 or authoring CMO3.

- [ ] Add regressions for coherent eye signs, physics vertices, asset reference validity, native playback edges and exclusive speech-mouth ownership; watch expected failures.
- [ ] Apply .44/.30/.82 defaults, mirrored motion eyes, reasonable head-unit amplitudes and physics1/2/3 with±15.
- [ ] Call native SetExpression/StartMotion on relevant stage edges; arrange parameter/update composition so playback has visible effect and audio retains final MouthOpenY ownership.
- [ ] Run affected tests and validate a hidden native render without network/audio.

### Task 3: Repair original rig and export

Own only staged model-authoring work and reports, then reviewed compiled/source model artifacts. Original: `C:/Users/Gtvkun/Documents/infj/infj pro demo.cmo3`.

- [ ] Find a verified authoring/export route and inspect exact brow/head/eye/deformer keyforms; do not guess missing graph data.
- [ ] Back up originals and repair smooth raised brows, correct head-right X, all18 corner/press forms, combined head/blink/gaze eye poses, and head rotation[-10,0,10].
- [ ] Export staged MOC3, native-load it and render the full pose/combination checks; publish only validated artifacts.

### Task 4: Integrate, verify and deploy

- [ ] Review each disjoint change and resolve material defects with regression tests.
- [ ] Run the combined Live2D/TTS suite, native parameter checks and rendered pose checks. Record broader-suite baseline failures separately.
- [ ] Save originals and copy verified source/assets to `C:/Users/Gtvkun/Documents/eris`; compare deployed hashes and test portable imports/rendering.
- [ ] Report exactly which items are active, validation evidence and any genuine remaining authoring limitation.
