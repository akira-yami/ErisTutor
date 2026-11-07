# Pool Browser Login Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Click Login in Pool UI, manually complete DeepSeek login/verification, and automatically save the correct session token.

**Architecture:** A local browser helper observes official login responses through CDP in an isolated visible browser. Authenticated local API handlers validate account binding and token validity before saving; the UI polls safe session state and can run accounts sequentially.

**Tech Stack:** Go, Chrome/Edge CDP using chromedp/cdproto, existing SQLite pooldb, existing vanilla JavaScript UI and Node VM tests.

**Spec:** `docs/superpowers/specs/2026-10-12-pool-browser-login-design.md`

## Global Constraints

- Preserve existing dirty changes and login/device behavior outside this feature.
- Manual verification only; no bypass or stealth flags, and no access to the default browser profile.
- Never expose passwords, session tokens, browser debug endpoints or raw login bodies in logs/API status.
- Ten-minute session timeout; one active session globally; exact account binding.
- Browser login APIs require admin authentication, loopback client and same-origin requests.
- Do not commit unrelated changes.

## Review Focus

- Wrong account in login request must never overwrite selected credentials.
- Failed login/challenge must remain pending and allow the user to finish verification.
- Expiry/cancel must not race into a later credential write.
- Pool removal while the browser is open must prevent a token update.
- Browser closure and pool UI refresh must recover cleanly without leaking an orphan browser/debug service.

### Task 1: Browser observer

**Files:** `tools/whale2api/internal/browserlogin/{browser.go,capture.go,*_test.go}`, `tools/whale2api/go.mod`, `go.sum`.

**Interface:** `browserlogin.New() *Runner`; `(*Runner).Run(ctx context.Context, identifier string, onState func(string)) (string,error)`; Run blocks until matching successful login token, cancellation, timeout or browser failure. Root owns the session manager, API and token persistence.

- [ ] Write failing parser tests for success, wrong account, malformed bodies, non-success/risk and missing tokens.
- [ ] Implement visible Chrome/Edge startup, dedicated session profile, loopback debug connection, network observer and owned-process cleanup.
- [ ] Verify helper package tests and local browser smoke readiness without entering credentials.

### Task 2: Local API and persistence

**Files:** `internal/poolui/browser_login.go`, `browser_login_test.go`, `server.go`; `internal/pooldb/browser_login.go`, `browser_login_test.go`.

**API:** POST/GET `/api/keys/{api_key}/accounts/{identifier}/browser-login`; POST same path + `/cancel`. JSON response `{session_id,identifier,status,message}`; status idle/starting/waiting/verifying/completed/failed/cancelled. POST starts asynchronous work and returns 202. One globally active session; conflicts return 409. GET never starts a browser.

- [ ] Write failing tests for local access, membership, status/cancel lifecycle, token validation failure and successful atomic pool-bound update.
- [ ] Implement session manager and injected runner/validator callbacks; token validation uses CreateSession only, not password login/completion.
- [ ] Save via DB method that checks membership and clears only selected account's old persisted test row.
- [ ] Verify Go packages.

### Task 3: Pool UI

**Files:** `internal/poolui/static/{app.js,index.html,app.css}`, `tests/node/pool-ui-browser-login.test.js`, `tests/scripts/run-unit-node.sh`.

- [ ] Write failing VM flow tests for row button, session polling, pool sequential queue, successful row refresh, cancel, failure and pool switching.
- [ ] Implement modal instructions, per-account Login and Login pool, one-at-a-time queue and two-second status polling.
- [ ] Register tests in existing Node runner and verify complete Node suite.

### Task 4: Integration and delivery

- [ ] Review credential binding, cancellation and local browser behavior.
- [ ] Run `go test ./...`, full Node suite and syntax/whitespace checks.
- [ ] Build poolui/whale2api, back up and replace local binaries, restart Pool UI and check served assets/health.
- [ ] Report exact click flow and the limit that real DeepSeek verification must be completed by the user.
