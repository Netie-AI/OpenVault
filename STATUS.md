# OpenVault - Status

> Canonical "what's true now." History: [`CHANGELOG.md`](CHANGELOG.md). Deferred:
> [`PARKING_LOT.md`](PARKING_LOT.md). Map: [`docs/ACTIVE.md`](docs/ACTIVE.md).

Last reconciled: 2026-09-11. GitHub OPEN = #60 (Get free keys). #12-#39, #48,
#52, #61, #62 CLOSED. No public `:5000`. VPC allowlist for
`POST /keys/services` (loopback + `OPENVAULT_SERVICES_ALLOW`). JWKS pin kids
(#50). Home pack: passphrase-scrypt only. Packs are DR-0016, passkeys DR-0017.
DR-0015 accepted: irreversible IDs in the vault; rust console optional.
Mesh omits `#auth` when `:5055` is down. Only HT1 is lifted. HT2, HT3, HT4,
and HT5 remain HUMAN_STOP. HT3 also needs the human passphrase. Public
`:5000` stays HUMAN_STOP.

**UI:** `http://127.0.0.1:3010/` and `openvault app`. The Compiling-proxy hang
is fixed: Turbopack is not used (`dev --webpack`), prod uses `next start` when
`.next/BUILD_ID` exists, and readiness waits for real HTML instead of a bound
port. Free Keys wizard on `/keys#free` (Groq first; GitHub Models retired).
Ship targets are in-repo hosts only (DR-0003).

LOCAL-1 (#70): `local_qwen` hop, no cloud key. Chat stamps `served_provider` /
`served_model` / `served_local`; request `local_only`; status `local_reason`.
503 `openvault_local_only_unavailable`. Ceiling: merged, local not proven.

## Branch estate (2026-09-11 merge wave)

17 branches were unmerged against `origin/main`. Measured, not assumed: 14 are
squash-merge ghosts - their pull requests landed, so their commits never became
ancestors of main, and git reports them "ahead" while carrying no content main
lacks. Two more (PRs #5, #7) were closed unmerged and hold only retired
scaffolding. Ghost deletion is NOT independently verified - do not delete on
this note alone.

## Known-red - do not report this suite as green

`OpenMW` has 5 collection errors, so those files contribute zero coverage
(R-0002): `test_key_channel_quant`, `test_kv_quant`, `test_prefetch_flash`,
`test_prefetch_sparsity`, `test_run`. They are all quant / prefetch / run
files, so one shared root cause at one binding point is likely (R-0004).

## Distance

Checked: `waitForServer` 9/9, including a new gate proving a build-in-progress
shell at HTTP 200 is refused and named "compiling", not "unreachable".
`nvme_sentinel` 96 passed, 6 skipped (needs real NVMe). NOT checked: a full
`OpenMW` suite here, the `apps/web` build, the 14 ghosts. Usage $/unit NEEDS-YOU.

## Next

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
| Verify ghosts | R-0003: a different run must confirm before any deletion. |
| apps/web build | No CI job exists for it; `npm run build` unverified. |
| Usage $/unit | NEEDS-YOU. Display SKUs are locked (DR-0013). |
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

## HT gates

Only HT1 is lifted (`https://netie.ai/ht1-demo/`). HT2, HT3, HT4, and HT5 remain
HUMAN_STOP. HT3 also needs the human passphrase. Public `:5000` stays HUMAN_STOP.

## Clone-and-verify

```bash
cd OpenMW && uv run pytest tests/ -q
```
