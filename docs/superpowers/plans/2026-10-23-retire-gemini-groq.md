# Retire Gemini, Gemma and Groq Implementation Plan

> **For agentic workers:** Execute inline with superpowers:executing-plans. Use test-driven-development and verification-before-completion.

**Goal:** Remove these providers from Eris runtime and leave their credential slots empty, as explicitly requested by the user.

**Architecture:** DeepSeek keeps cognition/localization and ElevenLabs keeps speech. Residue retention becomes deterministic. Retired provider settings cannot reactivate remote lanes; credential field names remain available with empty strings/lists.

**Tech Stack:** Python, pytest, JSON/.env, existing portable source deployment.

**Spec:** User instruction in this session: “bỏ hết đi. chừa key trống đó thôi. cứ dọn đi”. Includes Gemini audio fallback, per preceding discussion.

## Constraints and rulings

- Work in the existing checkout to preserve the current uncommitted runtime fixes; no reset/stash or automatic commit.
- No new provider or replacement model; keep deterministic diary retention and existing local audio fallback.
- Do not erase backups, old logs, or other providers' credentials. Blank active Gemini/Groq credential sources in dev and portable configuration; keep slot names.
- Update source/default settings and the portable copy when its pre-change files match. Preserve unrelated portable differences.
- User explicitly requested immediate execution; no additional design/permission round.

## Review focus

1. Stale Gemini/Groq key/flag/model cannot recreate a provider client.
2. ElevenLabs errors/cancellation cannot trigger a Google audio request.
3. Private residue remains persisted and rendered without a model.
4. DeepSeek workers and language profiles remain intact.
5. Active key files are blank without logging credential values.

## Tasks

- [x] Add failing behavioral tests for retired clients/pools, deterministic residue and ElevenLabs-only routing.
- [x] Remove provider builders/fallback assembly and enforce provider retirement at transport construction and request boundaries.
- [x] Remove residue model work and Gemini audio/localizer routing; preserve deterministic storage and DeepSeek localization.
- [x] Normalize settings/defaults and blank retired credentials in active dev/portable sources; preserve empty slots.
- [x] Run focused regression suites, inspect remaining runtime references and synchronize the changed portable source files safely.
- [x] Review changes, update runtime knowledge with evidence, and report checks and deployment limits.

## Execution ledger

- Initial checkout has unrelated changes in ElevenLabs pool, settings, language-switch tests and export scripts. Preserve them.
- Tests: initial 14 retirement cases RED; all 23 final retirement cases GREEN. Additional RED→GREEN cases cover settings reactivation, proxy model aliases, mutated/cloned clients, raw localizer URLs and the direct Google weather handler.
- Review: one independent read-only reviewer found raw localizer, cloned-model and weather bypasses. All addressed with no-network regression tests. Live audio quality/export/deployment were declined by the reviewer and verified separately where possible below.
- Ruling: retire legacy weather handler/capability availability rather than add a new weather provider; it was an active direct Google request outside the generic transport.
- Ruling: keep old backup files/logs and failing compatibility entry points; remove active implementations, key loading and UI activation. No historical cleanup or credential revocation at providers.
- Ruling: preserve pre-existing worker test failures rather than modify unrelated worker behavior. Original source overlay reproduces the same three failures listed in the knowledge report.
- Verification: 162 tests passed in the final focused suite; dotnet Debug build 0 warnings/0 errors; Godot debug export exit 0; Manager PyInstaller build exit 0. First export needed the missing staging directory created, then completed successfully.
- Deployment: 18 changed source files copied only after matching their pre-task portable hashes; Python bytecode refreshed, staged native export plus Manager/Setup deployed and hashes checked. Empty key slots verified in both active secret files.
- Portable smoke: imports and client construction passed without network calls; companion/observer/critic remain DeepSeek workers, TTS ElevenLabs, localizer DeepSeek, retired pool sizes 0/0. No chat/audio live session was started.
