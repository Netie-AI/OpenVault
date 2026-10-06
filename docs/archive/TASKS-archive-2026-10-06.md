# TASKS archive 2026-10-06

TASKS.md was not on main at b4d68021. The rows below are copied verbatim from the STATUS.md Next table, then removed from the live status.

## Finished or stale

| # | Status |
|---|--------|
| #126 prove mint | POST /keys/services, POST /keys/intermediate, and revoke skip X-OpenVault-Admin. Services mint still needs the #52 socket allowlist plus X-OpenVault-Reveal: intentional. Remote with no credential stays 401. |
| #99 one seat | In-process FreeRoute and FreeBuild stay the seat. Vendor OpenShip HTTP client is deleted. /proxy redirects to /freeroute. Draft #99 Node trees are not copied. |
| #120 tz fallback | quota.window_bounds falls back to UTC with a warning; tzdata is a Windows dependency. |
| #118 admin lock | Lock removes sessionStorage `openvault.admin` and the in-tab admin field. The .env paste text is cleared. Re-entering the token, then unseal, still works. |
| #114 seal UX | VaultSealBar shows sealed vs open. Unseal sends the Providers admin session header and keeps the passphrase in tab memory until Lock. .env import masks values and POSTs /api/keys per row. |
| #116 Hello unlock | When webauthn_registered, unseal and session reopen use the existing passkey ceremony. Passphrase stays the tab-memory fallback. Lock, fail, and reload clear the session. |
| #112 public JWKS | GET/HEAD/OPTIONS `/keys/jwks` exact path is unauthenticated, same document as `/.well-known/jwks.json`. Other methods on that path stay 401. |
| #94 catalog | SambaNova and SEA-LION rows added. NVIDIA NIM chat models refreshed. DeepSeek stays on its own provider. |
| #83 admin token | Key and secret admin routes plus /keys require X-OpenVault-Admin, even from loopback. Mint POSTs are the #126 exception. |
| #76 T2 | Dead-model 404/unknown continues; 429 parks (key, model); pinned model is not swapped in-provider. |
| #80 strict pin | Opt-in field or header. pin_unavailable when the exact catalog id has no healthy hop. |
| #78 CLI add | openvault add <provider>: hidden prompt or stdin, 1-token chat, HMAC dedupe, one vault. |
| #72 SEC-GUARD | Fail-closed /api+/keys guard, docs off, GET mesh/connect-pack is read-only. |
| #70 LOCAL-1 | Hop + served_* + fail-closed local_only. Ceiling: merged, local not proven. |
| OpenMW collection errors | 5 files, one root-cause class. Blocks a green suite. |
| apps/web build | No CI job exists for it; `npm run build` unverified. |
| #79 cards | Provider cards: Get key, paste once, test and add. |
| #86 T3 | Persisted parks, quota-aware health, usable_provider_count. |
| #88 sqlite close | Vault and usage connections close after commit. Open handles do not grow. |
| #90 web CI | apps/web npm test runs in CI on ubuntu-latest with Node 20. |
| #93 T3 slice 2 | Admin GET /api/keys/quota, OpenRouter key precheck, hop_attempts ledger. |
| #92 chat probe | OPENVAULT_CHAT_PROBE_INTERVAL_S defaults to 86400s with a 3600s floor. OPENVAULT_CHAT_PROBE_TIMEOUT_S defaults to 120s with a 30s floor. Boot pass after 30-120s jitter. Keys checked within the floor are skipped (6h for sambanova and sea_lion). |
| #100 OpenRouter 404 | /key precheck maps 404 and other non-2xx (not 401/403/429) to error with the HTTP code. |
| #98 strict pin | openai/gpt-oss-120b strict pin binds to groq. A together key listing that id is not a hop. |
| #104 chat probe cadence | Boot after 30-120s jitter, then once a day (86400s, floor 3600). Timeout 120s. Unusable only for 402, plan 429, or 401/403. |
| #106 T4 | Single-turn same-provider keys spread by LRU weighted by remaining quota. The proxy breaker is per key. |
| #95 web audit | apps/web npm audit fix: 0 high, 0 critical (was 3 high, 1 critical). next 16.3.8, sharp 0.35.5, nanoid 3.3.19. |
| #109 strict pin | gemini-3.5-flash binds to google. google/gemma-4-31b-it:free binds to openrouter. |

## Still live, no GitHub issue

These two rows are not refiled in this pull request's commits. The pull request lists them as proposed issues.

| # | Status |
|---|--------|
| Verify ghosts | R-0003: a different run must confirm before any deletion. |
| Usage $/unit | NEEDS-YOU. Display SKUs are locked (DR-0013). |
