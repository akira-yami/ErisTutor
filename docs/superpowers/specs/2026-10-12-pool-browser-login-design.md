# Pool browser login

The user wants to click Login in Pool UI, complete DeepSeek's normal login and verification themselves, and have the pool save the resulting session token without copying credentials or importing CSV.

Pool UI launches an ordinary visible Chrome/Edge window with its own non-default profile and a loopback-only DevTools connection. The user enters credentials and completes any site verification directly on DeepSeek. The helper observes successful login responses from the exact DeepSeek login endpoint, binds them to the selected account using the login request identifier, and passes only the resulting token to the server. It must not automate or bypass verification, alter device identities, access the user's existing browser profile, log credentials, or return tokens to the frontend.

An authenticated local-only start/status/cancel API serves one login session at a time. The server checks pool membership before starting and before saving, validates the resulting token using one token-only CreateSession request, then updates the stored token and removes the stale test result for that account. The UI offers per-account Login and sequential Login pool. Pool mode opens one account at a time, allows cancellation, and advances only after a successful capture/save.

Sessions expire after ten minutes. Cancellation and browser closure stop observation and release resources. The status response contains only session id, account identifier, state and user-readable message. Profiles live under the ignored docker-data directory; debug ports never leave the backend. Browser profiles are separate per login session so previous authenticated sessions cannot silently supply the wrong account. Windows Chrome/Edge discovery is required; unsupported environments return a clear error.

Verification includes successful login parsing, wrong-account rejection, failed/risk login remaining pending, pool membership checks, token validity failures preserving the old token, cancellation and expiry, origin/local access checks, UI queue behavior and a local browser smoke check with no real credentials. The user completes the actual third-party login after delivery.
