# CHANGELOG

Append-only. Never edited, only added to. Newest first.

## 2026-10-05 - Revoke and service rotation need a credential (OpenVault #128)

- Supersedes the #126 line that said intermediate revoke keeps a Bearer gate. After that change, loopback `POST /keys/intermediate/{kid}/revoke` with no credential returned 200. Revoke now requires a Bearer that passes the same `verify_service` check as issue, or `X-OpenVault-Admin`. Loopback alone is 401. Non-loopback revoke stays denied.
- First `POST /keys/services` is unchanged: loopback or an `OPENVAULT_SERVICES_ALLOW` peer (default `10.128.0.3`, `34.30.222.22`) plus `X-OpenVault-Reveal: intentional`. That first mint does not need admin.
- Re-registering an existing service_id rotates the token only with that service's current Bearer or `X-OpenVault-Admin`. Reveal alone does not rotate. An allowlisted peer can send the current service Bearer. A presented `ov_` key is still verified. No public `:5000` bind change.

## 2026-10-05 - Signing mint is not an admin route (OpenVault #126)

- `POST /keys/services`, `POST /keys/intermediate`, and `POST /keys/intermediate/{kid}/revoke` no longer require `X-OpenVault-Admin`. Loopback prove was 401 `openvault_unauthenticated` before the #52 peer allowlist and reveal check ran.
- `POST /keys/services` still requires a socket peer on loopback or `OPENVAULT_SERVICES_ALLOW` (default `10.128.0.3`, `34.30.222.22`) plus `X-OpenVault-Reveal: intentional`. An allowlisted peer with no `ov_` key reaches that gate. An unlisted remote with no credential stays 401. `X-Forwarded-For` and `Host` are not a peer.
- Intermediate issue and revoke keep their loopback and Bearer service-token gates. `/api/keys`, `/api/vault`, secrets, and apikeys still require admin. `GET`/`HEAD`/`OPTIONS` `/keys/jwks` stay public.

## 2026-10-05 - One FreeRoute seat; vendor OpenShip client cut (Refs #99)

- FreeRoute and FreeBuild stay the in-process OpenMW routers (`routers/freeroute.py`, `vault/proxy.py`, `ship/`). Draft #99's Node trees (`apps/router`, `apps/ship`, about 10k files) are not copied. They would be a second process beside the seat that already routes and ships. Key custody stays the OpenVault vault. No public `:5000` bind change.
- Deleted `ship/openship_client.py` (`OpenShipClient` HTTP to `OPENSHIP_URL`). `adapter_status()` now lives on `ship/openship.py` and still reports in-repo hosts only (`effective` is `simulate`, `api_url` is null).
- `/proxy` is no longer a second Route dashboard. The page redirects to `/freeroute`. Nav, home, and Add key follow that page. `apps/web/src/proxy.ts` stays: it is the Next middleware entry. `vault/proxy.py` stays: it is the chat engine. `/api/route/*` stays: strategy, breakers, and metrics for the in-process router.

## 2026-10-05 - Windows quota crash and tzdata (OpenVault #120)

- `quota.window_bounds` no longer raises when the machine has no tz database; it falls back to UTC and logs `quota_tz_missing_using_utc`.
- `tzdata` is a declared dependency on Windows. Before: chat routing and `/api/freeroute/status` returned 500 on Windows once a key existed.

## 2026-10-05 - Lock clears the admin session (OpenVault #118)

- After `lockVault` resolves, Lock removes `openvault.admin` from sessionStorage and clears the in-tab admin field. The next unseal does not resend the old token.
- The same Lock drops the .env paste textarea. Re-entering the admin token, then unseal, still sends `X-OpenVault-Admin`. Tests use fixtures only.

## 2026-10-05 - Session unlock via Windows Hello (OpenVault #116)

- When vault status says webauthn_registered, unseal and session reopen use the existing passkey ceremony (Windows Hello / platform authenticator). A successful passkey unseal marks the same in-tab session as a passphrase unseal. No second unlock API.
- The passphrase cache still reopens a sealed vault when Hello is not registered, this tab already used the passphrase, or the platform refuses. Lock, a failed reopen, and reload clear the session. Nothing is written to browser storage. Tests use fixtures only.

## 2026-10-05 - Vault unseal lasts for the app session (OpenVault #114)

- After a successful passphrase unseal, the passphrase stays in tab memory. A later screen that still sees the server sealed re-posts it once and does not open the gate again.
- Lock, a failed re-unseal, or a reload drops that cache. It is not written to localStorage or sessionStorage. Windows Hello / passkey unlock is unchanged.

## 2026-10-05 - In-app vault unseal and masked .env import (OpenVault #114)

- VaultSealBar shows sealed vs open from GET /api/vault/status. While sealed, a gate asks for the passphrase once. POST /api/vault/unseal sends the same X-OpenVault-Admin session the Providers page stores.
- Pasted or picked .env text is parsed in the browser. The list shows a mask. One click adds each known provider key with POST /api/keys. Each row reports ok or fail. Secret bodies are not shown.
- Provider cards stay as they are. sea_lion is shown by name and stored as custom with the public SEA-LION base URL, because POST /api/keys has no sea_lion provider id.

## 2026-10-05 - Public JWKS alt is unauthenticated (OpenVault #112)

- `GET /keys/jwks` matched the `/keys` admin root, so the published `jwks_alt` returned 401 without `X-OpenVault-Admin`. Live on `68514a72` (deploy 2026-10-02 02:13 MYT) the poller recorded 376x 401.
- `GET`, `HEAD`, and `OPTIONS` on the exact path `/keys/jwks` (the `request.url.path` string `http_guard` already uses) are public. The body is the same bytes as `/.well-known/jwks.json` from `TrustStore.jwks()`. No private JWK members.
- `POST`, `PUT`, and `DELETE` on that path stay on the admin gate (401, not 405). Near paths stay closed: `/keys/jwksX`, trailing slash, traversal, encoded, case, `/api/keys`, `/api/keys/quota`, `/keys/services`. A query token is not a credential. Loopback still needs the admin token off this path.

## 2026-10-01 - Strict pins for gemini-3.5-flash and gemma-4 free (OpenVault #109)

- Strict mode for `gemini-3.5-flash` binds to google. Strict mode for
  `google/gemma-4-31b-it:free` binds to openrouter. Another provider that
  lists the same catalog id is not a hop for that pin.
- No hop on the bound provider returns 503 `pin_unavailable` with `no_hop`.
  The groq pin for `openai/gpt-oss-120b` is unchanged.

## 2026-10-01 - STATUS HT gates and chat-probe cadence

- Only HT1 is lifted. HT2, HT3, HT4, and HT5 remain HUMAN_STOP. HT3 also needs the human passphrase. Public `:5000` stays HUMAN_STOP.
- The #92 row matches the merged chat probe: `OPENVAULT_CHAT_PROBE_INTERVAL_S` defaults to 86400s with a 3600s floor, `OPENVAULT_CHAT_PROBE_TIMEOUT_S` defaults to 120s with a 30s floor, a boot pass runs after 30-120s of jitter, and keys checked within the floor are skipped (6h for sambanova and sea_lion).

## 2026-10-01 - apps/web npm audit high and critical (OpenVault #95)

- `npm audit fix` (no `--force`) in `apps/web`. Before: 4 vulnerabilities (3 high, 1 critical). After: 0 high, 0 critical.
- Lockfile only. next 16.2.11 to 16.3.8, sharp 0.34.5 to 0.35.5, nanoid 3.3.16 to 3.3.19. The nested postcss 8.4.31 copy is gone. No major-version bumps.

## 2026-10-01 - Spread single-turn keys and break per key (OpenVault #106)

- Single-turn calls, which have no affinity key, pick the least-recently-used
  key of the same provider inside a priority band. The score is idle steps
  times quota remaining in the usage ledger. Provider order and band order
  stay as they are.
- Multi-turn calls and `prompt_cache_key` still use rendezvous hashing.
- The chat proxy circuit breaker is per vault key. One open key does not
  block the other keys of that provider, including under a strict pin.
  A local hop keeps the provider name.
- Spread state is in memory. Choosing a key does not add a ledger write.

## 2026-10-01 - Chat probe cadence is boot plus daily (OpenVault #104)

- `OPENVAULT_CHAT_PROBE_INTERVAL_S` defaults to 86400s with a 3600s floor.
  Junk or non-finite values keep the default. SambaNova and SEA-LION stay
  at most once every 6h via max(). A hop_attempts 2xx inside the gap is
  still skipped.
- Startup runs one boot pass after a random 30-120s jitter. A key whose
  chat_probe checked_at is younger than 3600s is skipped.
- `OPENVAULT_CHAT_PROBE_TIMEOUT_S` defaults to 120s with a 30s floor. A
  timeout or connect error stores status only. It does not park the key
  and does not mark it unusable.
- Unusable is only 402, a request-limit-0 429, a plan-code 429, or 401/403.
  A transient 429 still parks. 404 and 5xx store status only. Only a 2xx
  clears unusable.

## 2026-10-01 - Strict pin binds provider and model (OpenVault #98)

- Strict mode for `openai/gpt-oss-120b` is a pin-site provider bind: groq only.
  Together lists the same catalog id and is not a hop for that pin.
- No groq hop for that pin returns 503 `pin_unavailable` with `no_hop`.
  Non-strict routing for the same model is unchanged.

## 2026-10-01 - OpenRouter /key 404 is error; doc corrections (OpenVault #100)

- OpenRouter /api/v1/key: 404 and any other non-2xx except 401/403 (auth_fail) and 429 (rate_limit) now map to `error` with `HTTP {code}`. Other providers keep the shared classifier.
- Corrections: the #92 chat probe defaults to 3600s, OPENVAULT_CHAT_PROBE_INTERVAL_S has a 600s floor, sambanova and sea_lion are probed at most every 6h, keys with a hop_attempts 2xx inside the gap are skipped, and it uses the first non-reasoning catalog chat model at 16 tokens (512 only if every chat model is reasoning). The #97 OpenRouter precheck statuses are auth_fail / rate_limit / error, not failed.

## 2026-10-01 - Chat health probe per key (OpenVault #92)

- A chat probe POSTs "Reply with OK" on its own 300s loop, separate from the
  60s models probe. The model is the provider's first catalog chat id.
  `max_tokens` is 16, or 512 when that id is a reasoning model.
- HTTP 402, a 429 whose request-limit header is 0, or a 429 with an account
  or plan error code, marks that key unusable. `usable_provider_count` skips
  it. A transient 429 parks the key and does not mark it unusable.
- The probe writes no `usage_events` rows. Stored error text is scrubbed and
  capped at 200 characters. Request and response bodies are not stored.

## 2026-10-01 - Per-key quota, OpenRouter precheck, hop attempts (OpenVault #93)

- `GET /api/keys/quota` is admin-only (`http_guard` and `X-OpenVault-Admin`).
  Each row is a masked id, provider, tokens used today against the catalog
  daily limit, reset time, park state, and a scrubbed error. No secret.
- OpenRouter precheck calls `GET https://openrouter.ai/api/v1/key` and stores
  `limit_remaining` and `is_free_tier` only. A non-2xx sets `precheck_status`
  to `failed` with the HTTP code. Other providers are unchanged.
- `hop_attempts` records one row per fallback hop. No bodies and no keys.
  Rows older than 7 days, and rows past the cap, are pruned on write.

## 2026-10-01 - Catalog SambaNova, SEA-LION, and NVIDIA NIM (OpenVault #94)

- SambaNova Cloud is an OpenAI-compatible catalog row. Chat ids come from the
  public models list. DeepSeek ids on that list are not copied. Llama 3.1 405B
  is not on the list.
- AI Singapore SEA-LION is a catalog row for the three documented chat ids.
  The guard model and the embedding model are not chat hops.
- NVIDIA NIM chat models are refreshed from the public models list. DeepSeek
  ids on that list are not copied. The separate deepseek provider is unchanged.
  Cerebras and Mistral catalog rows are unchanged.
- Letter-mark icons for the two new rows, so the existing card page can render
  them. No card-renderer change.

## 2026-10-01 - Close vault sqlite handles (OpenVault #88)

- `with self._connect() as conn` under `openmw/openvault` now closes the
  connection after the commit. Parks and the quota reader already closed.
- A repeated vault and usage-store run leaves no extra open database handles.

## 2026-10-01 - Close park DB handles and widen error scrub (OpenVault #86)

- `hop_parks` connections close after each ensure, save, delete, and load.
  The schema is created once when a fallback manager starts.
- Stored provider messages also redact `csk-`, `xai-`, and any 32+ character
  token. The 200-character cap and message-only rule stay.

## 2026-10-01 - Persisted parks and quota-aware health (OpenVault #86)

- Hop parks live in `keys.db` table `hop_parks`. A restart keeps them. An
  expired park is shown as expired and is routed to again. Google stays
  parked until the next Pacific midnight, which is when its quota resets.
- Groq health uses the catalog daily token limit (200K) summed from
  `usage_events`. Over the limit the status is `quota_exhausted` with a
  reset time. The 18-column usage ledger is unchanged.
- A park stores a provider message of at most 200 characters. Key-like
  text is scrubbed. Request and response bodies are not stored.
- When every hop is parked the gateway returns 503
  `all hops parked, retry at <ISO time>` and sets `Retry-After`.
  OpenVault's own 429 already sends `Retry-After`.
- `GET /api/freeroute/status` adds `usable_provider_count`. `spendable_count`
  and `pooled_key_count` are unchanged.

## 2026-10-01 - Provider cards admin field (OpenVault #79)

- The `/providers` proxy no longer reads the admin token file. The operator
  types `X-OpenVault-Admin` into a password field. It stays in memory or
  sessionStorage, and the proxy forwards that header. A missing header is 401
  and does not call upstream.
- `GET /provider-cards` uses the same field. No GitHub Models card; the catalog
  row stays because the service is retiring.

## 2026-10-01 - Provider cards (OpenVault #79)

- Cards page in the console (`/providers`) and `GET /provider-cards`. Free and
  Premium only, for catalog providers. No DeepSeek card, no Cloudflare card,
  no Sign in with ChatGPT. Premium is a plain paste field.
- `POST /api/keys/cards` is POST-only, behind `http_guard` and
  `X-OpenVault-Admin`. It reuses `key_add.add_tested_key`. The response is the
  label, masked id, and outcome. The key is not echoed.
- Letter-mark icons are local SVGs (CC0). Catalog `register_url` fixes: NVIDIA,
  Together, Fireworks, GitHub Models, SiliconFlow.

## 2026-10-01 - Admin credential for key and secret routes (OpenVault #83)

- `/api/keys`, `/api/secrets`, other key and secret management routes, and
  `/keys` require `X-OpenVault-Admin` even from loopback. The token is not an
  `ov_` key. It is created with `secrets.token_urlsafe(32)` at
  `<vault home>/admin_token` (mode 0600) and compared with `hmac.compare_digest`.
- `OPENVAULT_ADMIN_TOKEN_PATH` overrides the file location. The value is not logged.
- `scripts/add_key.py`, `openvault secret get`, and other admin HTTP callers
  read that file. `openvault add` still writes the vault in process.
- `/api/freeroute/status`, `/api/healthz`, and `/v1/*` are unchanged.

## 2026-10-01 - CLI openvault add (OpenVault #78)

- `openvault add <provider>` reads the key from a hidden prompt or stdin.
  A key in argv or an environment variable is refused.
- The key is stored only after a 1-token chat on the provider's first catalog
  model, in the existing vault. GET /models is not the test. Bodies are not logged.
- Dedupe is an HMAC with a vault-held secret, stored beside the key. Output is
  the label and masked id. The test and dedupe live in `vault/key_add.py`.

## 2026-10-01 - Opt-in strict model pin (OpenVault #80)

- `strict: true` on the chat body, or header `X-OpenVault-Strict: true`,
  pins the request to an exact catalog model id. The default is unchanged.
- When that id has no healthy hop (parked, quota-exhausted, or circuit open),
  the gateway returns 503 `pin_unavailable` and does not call another provider
  or swap models. A park sets `Retry-After`.
- `served_provider` and `served_model` name the hop that actually served.
  A pin that was not served reports both as null.

## 2026-10-01 - Dead-model skip and per-model 429 (OpenVault #76)

- A 404, or a 400/422 whose body says the model is unknown, decommissioned,
  or not found, ejects that (key, model) for the job and the chain continues.
  Any other 400/422 still fails the request after one upstream call.
- A 429 parks (key, model) and tries the provider's next catalog model.
  The key is parked only when every model returns 429. A 402 or credits
  error still parks the whole key. 401/403 quarantine is unchanged.
- In-provider fallback runs only for `auto`, or when the provider does not
  serve the requested model. A pinned model the provider serves is never
  swapped for a sibling model. Upstream bodies are not logged or stored.

## 2026-10-01 - FreeRoute catalog refresh (OpenVault #74)

- OpenRouter `chat_models` are only `:free` ids whose prompt and completion
  price were 0 on https://openrouter.ai/api/v1/models at 2026-10-01T08:00:04Z.
  Vision ids are the ones whose `input_modalities` include image.
- Groq drops `llama-3.1-8b-instant`, `llama-3.3-70b-versatile`, and
  `qwen/qwen3.6-27b`. Vision is `qwen/qwen3.8-27b`.
- Cerebras models are `gpt-oss-120b` and `qwen-3.8-27b`. Notes call it a trial
  ($5 credits, 30 days, 5 RPM), not a free tier.
- Mistral drops retired `open-mistral-nemo`.
- NVIDIA drops `meta/llama-3.1-8b-instruct`, `meta/llama-3.1-70b-instruct`,
  and unlisted `mistralai/mistral-nemotron`. First choice is
  `nvidia/llama-3.1-nemotron-70b-instruct`.
- `local_qwen` is unchanged. Routing, precheck, limiter, parks, and guards
  are unchanged.

## 2026-09-25 - Fail-closed auth guard on /api and /keys (OpenVault #72)

- Every `/api/*` and `/keys/*` route now requires a valid issued OpenVault API
  key (Bearer / X-API-Key, same verification FreeRoute already uses) or a
  loopback socket peer. The guard is fail-closed whether `REQUIRE_API_KEY` is
  set, unset, or false. Allowlist is `/api/healthz` only. Forwarded headers are
  not consulted.
- `/docs`, `/redoc`, and `/openapi.json` return 404 unless `OPENVAULT_DEV_DOCS=1`.
- `GET /api/local/mesh` and `GET /api/local/connect-pack` no longer write state;
  the previous behavior is `POST` on those paths.
- The route-walk contract iterates every declared `(path, method)` pair under
  `/api/*` and `/keys/*` (GET, POST, PUT, PATCH, DELETE), not GET alone.
- Console `uvicorn.run` passes `proxy_headers=False` so forwarded headers
  cannot rewrite `request.client`.

## 2026-09-25 - LOCAL-1 FreeRoute local_qwen hop (OpenVault #70)

- Register `local_qwen` as a spendable FreeRoute provider that needs no cloud key.
  Talks to an OpenAI-compat loopback server (llama.cpp / Ollama style) via
  `OPENVAULT_LOCAL_BASE_URL` + `OPENVAULT_LOCAL_MODEL` (default `qwen2.5:0.5b`).
  Non-loopback hosts are refused with `local_base_url_not_loopback` and are never
  contacted. No new listener, no auto-start, no model download.
- Chat responses stamp `served_provider`, `served_model`, `served_local` from the
  hop that actually served (JSON body + `X-OpenVault-Served-*` headers; SSE JSON
  too). Only `local_qwen` is `served_local=true`. Spendable/hops use
  `served_local` (JSON boolean), not `local`. `local_reason` is always a string.
- `local_only: true` is fail-closed inside OpenVault: cloud hops are never
  attempted. Refusal is HTTP 503 `openvault_local_only_unavailable`. The field is
  stripped before any upstream POST.
- `/api/freeroute/status` hop `provider=local_qwen` with `served_local: true` and
  `local_reason` `""` / `local_unreachable` / `local_model_not_loaded` /
  `local_base_url_not_loopback`. Existing cloud arming / precheck / circuit
  rules are unchanged.
- Tests: `OpenMW/tests/test_local_freeroute.py` (stub transport only). Ceiling is
  merged, local not proven. Public `:5000` stays HUMAN_STOP.

## 2026-09-10 - CVV import test no longer searches whole secrets JSON

- `test_cvv_column_is_stripped_and_never_stored` asserted fixture digits
  `737` against `json.dumps(listed)`. Post-merge CI on `e6f3c5ce` (#61 / #60)
  https://github.com/Netie-AI/OpenVault/actions/runs/34463864873 failed because
  uuid4 hex id `42b1422acf7f47379992ed75fba39532` contained those digits, not a
  stored CVV field.
- Still guaranteed: CSV CVV columns are stripped (`cvv_stripped`); payment_card
  records have no cvv/cvc field; reveal payload is the PAN only. Unblocks R-0003
  for FreeRoute #61 / #60 merge SHA `e6f3c5ce`. No public `:5000`.

## 2026-09-10 - Free Keys wizard locks #60 checklist (no site-password form)

- Wizard UI shows Groq-first register_url / base_url / provider= rows; Register
  opens the locked register_url. POST /api/keys uses role=free and custody=pooled.
- Site passwords are not this wizard (`/api/secrets*` stays on /vault).
- Hugging Face base_url confirmed from PROVIDER_CATALOG (`https://huggingface.co`,
  not OpenAI-compat). CF /models 405 remains warn-not-fail.
- Home card syntax fix. Prefer `openvault app` (STATUS: :3010 hang).

## 2026-09-10 - FreeRoute Get free keys onboard wizard (#60)

- Groq-first checklist on `/keys#free` (Electron tray: Get free keys). Paste-to-save
  via `POST /api/keys` role=free with catalog `base_url`. Cloudflare Workers AI is
  `provider=custom` plus Account ID -> `/client/v4/accounts/{ACCOUNT_ID}/ai/v1`.
- `GET /api/freeroute/onboard` + Groq-first `/api/tool/register` (GitHub Models not listed).
- `POST /api/vault/ingest-env` accepts pasted `.env` (`env_text`); dry-run default.
  SITE_* / passwords go to `/api/secrets*`, never empty-base_url keys.
- Precheck: Cloudflare GET `/models` 405 is a probe-mismatch warn, not a dead key.
  Save does not wait on that probe.
- Tests: `OpenMW/tests/test_free_keys_onboard.py`; `apps/web` `freeKeysOnboard.test.ts`.
## 2026-09-08 - Close #16 and #15 from live evidence

- #16 CLOSED: `GET /api/ship/targets` has no `openship_cloud` / openship.io /
  OPENSHIP_URL. Playwright `/ship` paints. No Cloudflare Containers. No Stripe.
- #15 CLOSED: `/freeroute` Try a hop Send posts `/ov-api/v1/chat/completions`;
  sealed vault showed explicit refuse (403). Did not pull F15 RTK/skills.
- #17 stays OPEN: remaining live `openvault app` (#56 OPEN). #18 stays CLOSED.

## 2026-09-08 - Restore :3010 HTML; retire vendor OpenShip; FreeRoute GUI chat

- `:3010` hung because Next 16 Turbopack panics on this exFAT volume and keeps
  the port bound. `openvault up` / Electron now wait for real HTML; `dev` is
  webpack; prod `next start` is used when `.next/BUILD_ID` exists. Electron
  reuses an already-up :5000 / HTML :3010 instead of a second bind.
- Browser this session: `/` `/vault` `/ship` `/freeroute` `/gate` paint AppBar.
  FreeRoute "Try a hop" posts `/v1/chat/completions`; sealed vault showed an
  explicit refuse (not empty success). Desktop window title OpenVault.
- `GET /api/ship/targets` no longer lists `openship_cloud` / openship.io /
  OPENSHIP_URL. Internal hosts remain. DR-0003.
- Tickets #55-#59 closed. Epics #15 #16 #17 CLOSED. #18 stays CLOSED.

## 2026-09-08 - F32 reopen #17 #16; queue #15

- Decomposition: #17 closed from pytest+tsx while `:3010` still hangs
  (homepage timeout 8s this session). #16 still embeds vendor OpenShip
  (`openship_client.py`, `openship_cloud` / openship.io). #15 API EARS hold;
  playground UI queued.
- Tickets: #55 (FIRST, GUI HTML), #56 (desktop after #55), #57 (retire
  OpenShip), #58 (`/ship` UI), #59 (FreeRoute playground). #18 stays CLOSED.

## 2026-09-07 - Renumber packs/passkeys off main DR-0013/DR-0014

- `main` already shipped DR-0013 (locked display SKUs) and DR-0014 (JWKS pin).
- This branch remaps unpublished experience packs to DR-0016 and passkey unseal
  to DR-0017. IDs are never reused. DR-0015 vault line is unchanged.

## 2026-09-07 - DR-0015 accepted; mesh stops advertising a down rust #auth

- Vault line accepted: irreversible IDs and 2FA recovery codes live in OpenVault;
  name/address/phone/email/DOB stay in Cortex memory. Rust console stays an
  optional sandbox. Python `accounts` are not moved into it. Phone-verify stays
  off. Pointer `DR-0004` Ask 3 stays blocked.
- Connect-pack `rust_console.auth_ui` is null unless the last probe is
  `online`/`approved`. `register_passkey` no longer hands out `:5055/#auth`
  when the process is down. Handshake no longer stamps `approved` over `offline`.
- Tests: `OpenMW/tests/test_local_mesh.py` rust auth_ui cases;
  `tests/test_recovery_codes_identity.py` still the identity gate.

## 2026-09-07 - Sealed home pack for another laptop you own (F31)

- `openvault home pack` / `home unpack`: zip of `OPENVAULT_HOME` as it sits on
  disk. No decrypt. No CSV. Refuses DPAPI, plain wrap, and `master.key.v0.bak`.
  Skips `import/` staging. Passkey unseal stays on the source box.
- Tests: `OpenMW/tests/test_vault_home_pack.py`. Not cloud containers, not
  Control-hosted Cortex, not SSH.

## 2026-09-07 - Board empty: #48 already on main

- GitHub OPEN count is 0. Control-plane ticket #48 CLOSED after PR #49
  squash-merge (`3ddcb014` on `origin/main`): entitlements / routing / unlock /
  metering / seats, locked USD display tiers, usage $/unit stays NEEDS-YOU.
  Not this branch. Do not rebuild.

## 2026-09-07 - HT1-HT5 boxes ticked; human-gate clerk rule

- Epic #18 was already CLOSED 2026-09-04 with founder evidence. Empty `[ ] HT`
  boxes on the issue body made the board look unfinished. Boxes now `[x]`.
- Standing rule: HUMAN_TEST_GATES are human-only to perform, agent duty to
  record. When the founder walks a gate, tick GitHub + `STATUS.md` same turn.
  Do not tick from pytest/simulate. Do not rebuild CLEARED gates.
- Law: `.cursor/rules/human-test-gates.mdc`, `CLAUDE.md`, Netie
  `AGENT_SYSTEM.md` + `DOCUMENT_SYSTEM.md`.

## 2026-09-06 - Rust console assessed (DR-0015 still proposed)

- `OpenMW/rust/openvault-console`: cargo 1.97.1, `cargo test` 2 passed, release
  exe exists on this machine (gitignored). It is a second accounts+secrets
  store (`rust-auth.db`) with a demo passkey that mints `demo_private_key`.
  Not identity SoT. Python WebAuthn unseal (DR-0017) stays the real path.
- Mesh still advertises `:5055/#auth` when the process is down. Founder call
  stays in DR-0015 Open; do not move Python `accounts` into the crate.

## 2026-09-06 - The app-grant pairing code is real (KB A-0009)

- **Was decorative:** `decide_grant()` rendered a code nothing compared, so the
  only gates on approving a grant were loopback and a client-supplied header.
  `docs/SECRETS_CUSTODY.md` already said loopback does not separate processes,
  which means any process running as the user could approve any other app's
  grant and collect the `ov_` token.
- **Now:** `decide_grant(..., user_code=...)` compares with
  `hmac.compare_digest`. `POST /api/local/grants/{id}/decide` takes `user_code`;
  wrong or missing is **403 refused**, not a warning, and audits as
  `app_grant_code_refused`. Five wrong codes burn the grant.
- **The code leaves once:** only `POST /api/local/grants` returns it, to the app
  that asked. `GET /api/local/grants` and `GET /api/local/grants/{id}` no longer
  carry it, so a second local process cannot read it back and replay it. The
  `/grant/<id>` screen now asks the human to type it instead of displaying it.
- **Not this:** peer-process identity. A hostile process as the user still reads
  `master.key` off disk. Named pipe / Unix socket is still the real fix.
- **Tests:** `OpenMW/tests/test_app_grants.py` - 11 passing, six of them
  negative (no code, wrong code, wrong-code deny, second loopback client with
  byte-identical headers, brute-force burn, non-ASCII code). All six fail if the
  compare is removed.

## 2026-09-05 - Passkeys unseal the vault (DR-0017, F22)

- **Windows Hello / Face ID / fingerprint**, plus optional **iPhone** (hybrid
  QR / nearby). Second wrap of the live master key under the authenticator PRF
  (`OPENVAULT_HOME/webauthn_unlock.json`). Passphrase wrap on disk stays backup.
- **Loopback APIs:** `POST /api/vault/webauthn/register/{begin,finish}`,
  `POST /api/vault/webauthn/unseal/{begin,finish}`, `POST /api/vault/webauthn/clear`.
  Register requires an open vault. Unseal-with-passkey works while sealed.
- **UI:** `/vault` SecretsPanel. Hidden when `PublicKeyCredential` is missing.
  Electron + Next send `Permissions-Policy` for WebAuthn.
- **Not this:** browser autofill, login-agent, iCloud dump (F18 + PRD §3).
  Agents still use `openvault secret get` after the vault is open.
- **Tests:** `OpenMW/tests/test_webauthn_unlock.py` (PRF wrap + crafted ES256;
  no live Hello in CI). GitHub has no open tickets.

## 2026-09-05 - FreeRoute 500 was decrypt, not "no keys"

- **Symptom:** loopback `POST /v1/chat/completions` returned plain
  `Internal Server Error`. `GET /api/keys/{id}/secret` 500d the same way.
  Usage ledger wrote nothing. Live listener was a worktree console with
  `--mock-health` and `cortex_url` on Constructor `:8010`.
- **Fix:** hop walk catches `VaultCryptoError` and skips that key.
  Reveal returns 409. Chat maps remaining exceptions to JSON 500 with a
  type. `Start-NetieStack.ps1` pins OpenVault to engine `:8011` when `:8010`
  is Constructor. Canonical console is `D:\OpenVault` on `:5000`.
- **Not this:** auto-unseal. Passphrase wrap still starts sealed.

## 2026-09-04 - Desktop app + loopback Grant (F20)

- **Open like an app:** `scripts/windows/Start-OpenVaultApp.bat` +
  `Install-OpenVaultDesktopShortcut.ps1` (Desktop + Start Menu). Electron still
  runs `next dev` so this repo's UI changes reload. DevTools only if
  `OPENVAULT_DEVTOOLS=1`. Protocol `openvault://grant/<id>` focuses the window.
- **Other local app gets a key:** `POST /api/local/grants` then human Grant on
  `/grant/<id>`. The app polls once for the `ov_` token (never written to
  disk). CLI: `openvault grant request --client MyApp`. Loopback only.
- **Not this:** passkeys, browser autofill, login-agent, LAN/SaaS embed (F13).
  Agents already retrieve keys/passwords with `openvault secret get` (never cards).

## 2026-09-04 - Experience packs $10/$30/$100/$500 (DR-0016, F14/F19)

- **Founder pick for STATUS pricing:** prepaid mixed-hop credit on pooled keys
  (DR-0009 a), not hosting SKUs (`ov_hosted` $24 / `ov_fast` $79 / `byo_*` $9)
  and not a 1% skim invoice. Starter $10 (~$8 credit), then $30/$100/$500.
- **`vault/route_packs.py`:** estimated spend = billable tokens *
  `OPENVAULT_BLEND_USD_PER_1M` (default 0.20), labeled estimated. Exhausted
  pack -> HTTP 402 `openvault_pack_exhausted` with Register / Install / BYOK
  next-steps. No pack on an `ov_` key keeps existing rate limits. Loopback
  stays free. Checkout is simulate; no live pack price ids.
- **Surfaces:** `GET /api/keys/packs`, `POST /api/keys/packs/simulate`,
  optional `pack_id` on `POST /api/apikeys`. `/keys` subscribe names prices
  without hop-vendor strings.
- **Tests:** `OpenMW/tests/test_route_packs.py` plus subscribe copy lock.
## 2026-09-07 - #52 VPC allowlist for POST /keys/services (not public :5000)

- **Scoped helper, not a widened loopback gate.** `POST /keys/services` now
  accepts loopback plus configured prove peers. Secret reveal, key create,
  intermediate issue/revoke, and every other `_require_loopback` site stay
  loopback-only. No public `:5000` bind.
- **Defaults:** Cortex prove `10.128.0.3` and `34.30.222.22`. Ops extend with
  `OPENVAULT_SERVICES_ALLOW` (comma/CIDR/IP list). Defaults stay on so an env
  typo cannot drop prove. Unlisted remotes get 403 naming the env var, not the
  peer list. `X-Forwarded-For` is not read by this gate (`_client_host` /
  `_normalise_host` only).
- **Tests:** loopback allow, default prove IPs allow, env CIDR allow, unlisted
  deny, XFF spoof deny, allowlisted peer still cannot issue intermediates or
  create provider keys. `mint_loopback_only` on `/api/system/bind` still means
  not world-open; `services_allow_env` names the CIDR list.
- **Cite only:** dms#116 remount context in the other repo. This PR does not
  change dms.

## 2026-09-07 - #50 JWKS pin kids + FreeRoute register (no public mint)

- **JWKS bind (Platform/Decision addendum):** `GET /.well-known/jwks.json` and
  `GET /keys/jwks` publish the trust-root public JWK (`netie_verify_only`,
  `netie_role: trust-root`) so Cortex prove can obtain a kid without minting.
  `GET /api/keys` remaining `keys=[]` is the empty *vault*, not a missing JWKS.
  `POST /keys/services` and `POST /keys/intermediate` stay loopback-only.
- **Env for Platform bind:** writers `http://35.253.229.206:8080`; JWKS
  `http://35.253.229.206:8080/.well-known/jwks.json`; Cortex prove
  `http://34.30.222.22:8010`; `LIVE_KEY_ID=119691f2c637` (id only) already in
  Secret Manager `openvault-dms-writer-token`. `/api/system/bind` echoes the id
  when set and refuses values that start with `ov_`. No public `:5000`.
- **FreeRoute:** Together + SiliconFlow `chat_models` wired so `model=auto`
  spends pooled keys; GitHub Models inference retired (empty pool, skip).
  `GET /api/tool/register` + UI `/tool/register`, `/freeroute`, `/system`.
- **Cite only:** dms#116 verify remount after kids exist. This repo writes no
  DMS product code. Usage $/unit stays NEEDS-YOU. DR-0014 proposed.
  Decision Agent does not merge.

## 2026-09-07 - #48 addendum: control-plane rates labeled USD (prefix + sign)

- Catalog, entitlements, metering, and usage summary now carry `currency=USD`
  plus both founder labels: prefix `USD10` and sign `$10`. Seat is `USD30` /
  `$30`. Usage $/unit stays unset: `USD NEEDS-YOU` / `$ NEEDS-YOU` -- no
  invented number. Policy version 2. Still no public rate page and no public
  `:5000`.

## 2026-09-07 - SYSTEM control plane: entitlements / routing / unlock / metering / seats (#48)

- **Locked display SKUs** (not a public rate page): Individual Basic USD10 /
  Pro USD100 / Ultra USD500; Team USD10 / Team Ultra USD500 / Team Giga USD1000;
  team seat USD30. Usage credits 20% cheaper on Ultra/Giga; normal on Pro and
  the USD30 seat. Usage $/unit stays `None` / `NEEDS-YOU` -- no invented rate.
- **Loopback `/api/system/*`**: catalog, bind, entitlements, unlock, lock, seats,
  route (maps onto existing `free`/`pro` limiter tiers), metering overlay.
  Extends `GET /api/accounts/{id}` with an entitlement snapshot. Same
  `accounts.db`, one vault. Hardware `/api/control/*` is unchanged.
- **No public :5000 bind.** Default host stays `127.0.0.1`. Internal writers
  URL is `http://35.253.229.206:8080`. `0.0.0.0` refuses unless
  `OPENVAULT_ALLOW_PUBLIC_BIND=1`.
- **Tests:** `OpenMW/tests/test_control_plane.py`. Existing usage summary still
  asserts `priced is False` and now also `usage_unit_status == NEEDS-YOU`.
- **DR-0013** proposed. Decision Agent does not merge.

## 2026-09-04 - HT3 passphrase + vault Lock/Set-passphrase UI; founder closed #18 #33

- Human HT3: wrap=`passphrase-scrypt`, bak retired, restart boots sealed,
  sealed `POST /api/ship/engine` spaceship_ftp returns 403. API path used
  because `:3010` stayed on Compiling proxy.
- Vault page: Set passphrase / Lock wired to `/api/vault/passphrase` and
  `/api/vault/lock` (unseal + retire bak already existed).
- Founder closed demo epic #18 and host+meter epic #33. Pricing stays
  NEEDS-YOU, not a ticket.

## 2026-09-04 - Spaceship FTP host adapter (existing estate host)

- **Real publish path for the FTP account this estate already pays for.**
  `ship/hosts/spaceship_ftp.py` uploads a built folder, probes
  `SPACESHIP_PUBLIC_URL`, and returns that URL only after a successful probe  - 
  never invents `https://netie.ai`. Live overwrite is opt-in via
  `OPENVAULT_SPACESHIP_ALLOW_PUBLISH=1`.
- **Recommend prefers Spaceship when FTP host+user are configured** for static
  stacks; otherwise Cloudflare Pages stays the free default.
  Preflight/engine/target cards wire the new id through `app.py`.
- **Vault env inject refuses public FTP.** Secrets write only when
  `SPACESHIP_FTP_ENV_DIR` is set and differs from the upload dir; `.env` files
  stay out of the upload set. Ship UI shows the ALLOW_PUBLISH + env-dir honesty
  when Spaceship is selected.
- **Tests:** `test_hosts_spaceship_ftp.py` plus targets/preflight coverage in
  `test_ship_cloud.py` / `test_ship_recommend_upload.py` (SPACESHIP_* cleared
  in the isolated env fixture).

## 2026-09-04 - Lazy openmw import so console and one-seat demo skip numpy

- `openmw/__init__.py` no longer imports `openmw.run` at module load. Offload
  helpers resolve via `__getattr__`. `import openmw.openvault.app` (one-seat
  demo, FreeRoute console) no longer requires a working numpy DLL.

## 2026-09-03 - Friendly key UI: Cortex subscribe, honest BYOK, easy free register (#42)

- **Subscribe shows a Cortex API key only.** `POST /api/keys/cortex` and
  `POST /api/accounts/{id}/cortex-key` mint an `ov_` token, store it as `provider=cortex`,
  and frame it as a Cortex key. No hop vendor or fake vendor string on that screen;
  `GET /api/keys/ui-copy` serves the locked copy and `OpenMW/tests/test_key_ui.py` plus
  `apps/web/src/keys/*.test.ts` fail if any surface drifts from it.
- **Bring your key shows the provider name the user pasted** (`apps/web/src/keys/byok.ts`);
  an `ov_` token is never labelled as another vendor. **Free keys** are two steps: Register,
  then Install.
- **Landed in the Next console, not the retired webui.** The branch had added a Keys tab to
  `OpenMW/webui/index.html`, which main had already deleted (`:5000/` redirects to the app).
  The tab is ported to `apps/web/src/app/keys/page.tsx` (`/keys`, nav "Keys"); Operator hop
  status stays on `/vault` (R-0011). The loopback proof server (`npm run serve`) keeps its
  `127.0.0.1:3010` default; set `KEY_UI_PORT` to run it beside `next dev`.
- **Custody kept honest on merge.** An account-issued Cortex key is stored `custody=tenant`
  (DR-0009, #41) so the metered gateway never spends it, and the mint routes run the same
  loopback + unsealed guards and custody audit line as every other vault mutation.
- **`next build` type-checks again.** Settings and ClipDropZone each declared a different
  shape for `window.openvault`, which TypeScript rejects; the bridge is now declared once in
  `apps/web/src/types/openvault-window.d.ts`. No CI job runs the web build yet, so this had
  been failing silently on main.

## 2026-09-03 - OpenVault Service SKUs, Stripe checkout (simulate), ship to netie.ai (PR #40)

- **OpenVault owns HTTP; Cursor Origin stays source-only.** `ship/server.py` emits the
  Caddyfile (TLS + `reverse_proxy` / `file_server` + `/healthz`), the systemd unit and an
  AWS SSM restart; `ship/origin.py` plans the Origin push (`ORIGIN_MODE=simulate` default);
  `ship/hosting.py` maps `host_kind` (`static_http` / `edge_http` / `process` /
  `container`) onto that runtime and reports ready-to-ship gates. `cicd_plan` writes
  `.github/workflows/openvault-ship.yml` (build, scp, restart, reload caddy, curl
  `/healthz`); `vercel.json` is a detect hint only.
- **Service login + SKUs** (`ship/service.py`): customers log into OpenVault Service, not
  the laptop. `ov_hosted` $24 (wraps Lightsail + VPS), `ov_fast` $79, `byo_aws` / `byo_vps`
  $9 platform fee. Connect secrets are never persisted.
- **Stripe Hosted Checkout** (`ship/stripe_billing.py`, `httpx` only): NETIE test-mode
  prices `price_1U8SQSFV5wcFod2fggATWBtT` (hosted), `price_1U8SQbFV5wcFod2fBfkEFyl4`
  (fast), `price_1U8SQbFV5wcFod2fv5r1WD8o` (byo_aws), `price_1U8SQcFV5wcFod2fw5s1cqSs`
  (byo_vps). `STRIPE_MODE=simulate` default; live only with `STRIPE_MODE=live` and
  `STRIPE_SECRET_KEY`. `POST /api/service/ship-netie` runs login -> checkout -> confirm ->
  Caddy/systemd onto `{slug}.netie.ai`; flags `airgpt: false`, `dms: false`.
- **Merge with main (#9, #43, #41, #45):** the PR's stub copies of `aws_guide`, `cicd`,
  `cloud_targets`, `engine`, `github_auth`, `library`, `openship_client`, `pick_folder`,
  `stacks`, `redis_store` yielded to main's implementations; `DetectedStack.host_kind`,
  `Stack.host_kind` / `origin_http`, the `CicdReport` HTTP-ship fields and `cicd_plan`
  were carried over onto them. `tomli` is now declared (main's `languages.py` already
  imported it on 3.10). The PR's `OpenMW/webui/index.html` edits were dropped with the
  webui main removed; `openvault_hosted` / `hetzner` / `aws` targets live on the ship
  router, not on `/api/ship/engine`.
## 2026-08-25 - Skill SoT is Netie-KB :8030, not Cortex (DR-0012)

- Founder/constitution answer landed: **keys = OpenVault**, **skills+MCP
  catalog = Netie-KB R-0016 at 127.0.0.1:8030**, **Cortex stirs**. Access ids
  are `netie-kb.skills` / `netie-kb.mcp`. Connect pack publishes `netie_kb` and
  a Constructor pointer (consumer skin, not merged). Grok is a model slot, not
  an app. Netie Control is the supervised shell; Cortex does not grow a UI.
- Cortex-crew is a Cortex **worktree branch**. This agent still cannot merge it
  (`Netie-AI/Cortex` 404). Estate-gate FAILING lives in netie-control, also 404.

## 2026-08-25 - Cortex crew gate + skill/mcp signposts (DR-0012 wire)

- **Cortex agents have an OpenVault number to call.** `POST /api/crew/gate`
  is resolve + audit (`parent_run_id`, `child_id`, `deficit`). Location and
  allowed, never a skill body. Connect pack publishes `crew_gate`,
  `access_resolve`, and Cortex's `skills` / `crew` / `mcp` URLs.
- **Access kinds `skill` and `mcp`** signpost `cortex.skills` and `cortex.mcp`
  the same way `cortex.memory` already works. `runtime.crew` points at Cortex
  `/api/crew`. Indexes `GET /api/cortex/skills` and `/api/cortex/crew` strip
  `skill_body` / `transcript` if Cortex ever sends them.
- **Cortex code is not in this tree.** `Netie-AI/Cortex` is 404 with this
  agent's GitHub token. The registry, parent/child loop, and next-email skill
  load still have to land there once the repo is on the environment.

## 2026-08-25 - Skills, KB, and Cortex crew: name the three stores (DR-0012, proposed)

- **The skill library is still not here.** Reviews of agent outreach, human-email
  skills for Grok, internal system skills, and Cortex crew A2A need one loop, not
  a catalog in the vault. [`DR-0012`](docs/decisions/DR-0012-skills-kb-crew-wiring.md)
  is the RFC: Cortex stirs (load skill this turn, crew parent-task, deficit
  `need skill X`), OpenVault signposts and gates (same shape as memory resolve),
  Netie KB is the one skill registry (R-0016, `:8030`). Immediate next-email use
  loads from that registry, not a second Cortex catalog and not a vault store.
- **OpenVault's slice is the negative space.** Tests now fail if `/api/skills`
  (or agent-skills / omni-skills) appears, if the access registry grows
  skill-body fields, or if PRODUCT_ROLES stops saying the agent loop is not
  ours. Compatible later work: access kinds `skill`/`mcp` as location+gate only,
  and #39 custody MCP. Distill ingest and crew scheduler stay out.

## 2026-08-25 - Custody reopen lands on GitHub (F17 bak + F18 CSV + agent retrieve)

- **#37 re-landed on the branch that GitHub actually has.** Independent verify had
  closed the ticket, but `main` still required `master.key.v0.bak` after migrate and
  had no retire route. Status now reports `plaintext_backup_present` even while
  unsealed. `POST /api/vault/backup/retire` unwraps the live wrap, byte-compares the
  bak, and deletes only on match. A folder of `keys.db` + bak without a live wrapped
  key does not yield plaintext. DR-0010 stays `proposed`.
- **#38 password-manager CSV ingest.** `POST /api/vault/ingest-pm` and
  `OPENVAULT_HOME/import/*.csv` accept Google / Apple / Chrome shapes. Dry-run
  default. CVV columns stripped with an explicit reason. Sealed fails closed.
  Synthetic fixtures only.
- **#39 agent thin-client retrieve.** `openvault secret get` calls existing reveal
  gates over loopback HTTP. Hard-denies `payment_card` / PAN. Does not cache
  passwords on disk. Sealed fails closed.
- **Account-attached keys are tenant custody (DR-0009).** `POST /api/accounts/{id}/keys`
  no longer defaulted into the pooled spend list.

## 2026-08-20 - Port custody: name the application that is blocking us (DR-0011, proposed)

- **The launcher stopped adopting strangers.** Every launcher had an "already listening on
  :5000 - reusing it" branch that reused whatever was there, including a server pointed at a
  different vault home. `openvault up` now identifies the listener first: our own server is
  still reused, and a foreign one is refused in about 10 seconds with its name and executable
  path, instead of a 90-second wait and a timeout that blames the wrong thing.
- **"Port busy" became actionable.** `openmw ports` lists all four stack ports and, for a
  blocked one, prints the process name, pid and full executable path. Via `psutil`, already a
  dependency. Verified on Windows 11 without elevation: all 54 listening sockets resolved.
- **A port choice now persists.** `openmw ports --set api=5099` writes
  `$OPENVAULT_HOME/ports.json`. Precedence is explicit flag > env var > saved file > default,
  so a one-off `--port` never rewrites a saved preference.
- **Refusal stays narrow** (R-0005): only a listener that fails to identify itself on its
  health endpoint counts as foreign. Cortex :8010 and AirGPT :8765 are reported, never
  treated as intruders and never reconfigured - they belong to other repos.
- **Nothing is killed.** Naming a process is decision support, not a licence to terminate
  somebody else's work. Whether to add an explicit, confirming `--kill` is open in DR-0011.
- Two implementation errors worth recording, both of which looked right. A command-line
  heuristic added to recognise our own process matched *any* process launched from
  `OpenMW/.venv`, so it would have adopted a stranger's script as ours - the exact bug the
  module exists to prevent. And the first version resolved nothing: the command wrote the
  file while `openmw console` still defaulted to a hardcoded 5000 and the launcher still had
  `API_PORT = 5000`, printing "used on every later start" while nothing read it. Both are now
  gated, and the record carries a live end-to-end check rather than only unit tests.
- **Four decisions are open and blocking** - see
  [`DR-0011`](docs/decisions/DR-0011-port-custody.md), filed `proposed`: where ports.json
  lives given two vault homes, the web port being inert until package.json reads the env,
  whether anything may ever be killed, and mesh-wide port ownership.

## 2026-08-20 - Five more intermittent-startup causes, found by a completed adversarial sweep

The first sweep lost 24 of 31 agents to a session limit. Re-run whole: 5 confirmed (two verifiers
each), 5 refuted - three of those killed because the behaviour was deterministic rather than
intermittent, which is the distinction that makes this class findable at all.

- **The two-vault mystery, solved.** The Electron shell spawned the custody API with
  `env: { ...process.env }` and never set `OPENVAULT_HOME`, so `paths.py` fell back to
  `~/.openvault` while every other launcher pins `<repo>/.openvault`. Which key store the desktop
  app talked to depended on who won `:5000` first. `Start-NetieStack.ps1` has pinned this since it
  was written and even has a "wrong vault home - restarting" branch; the pin was never copied here.
- **`next start` with nothing that builds.** The shell defaulted to the production server, and no
  code path in the repo runs `next build`. On any machine without leftover `.next/` the web child
  died instantly - and the readiness result was discarded, so the window opened on a dead port
  anyway. Defaults to `dev` now (`OPENVAULT_PROD=1` opts in), and the web branch got the same
  refuse-to-open dialog the API branch already had. That asymmetry was ours from the previous
  commit: the API branch was fixed and the web branch ten lines below was left alone.
- **The error was thrown away and the diagnostic could not see it.** `_start_web` sent stdout and
  stderr to `DEVNULL`, so `next start`'s real message vanished, and the user was pointed at
  `openvault doctor` - which checked ports, node and npm but never `.next/BUILD_ID`. Web now logs
  to `web.up.log`, its tail prints on timeout like the API's does, and doctor reports the build.
- **`openmw/cli.py` still carried U+2192 and U+2026.** This is the process every launcher spawns
  *with its stdout redirected*, which is precisely the condition that selects cp1252. The gate added
  hours earlier covered the launcher and not the thing being launched. Fixed and added to the gate.
- **Preflight tests read whatever the ambient vault held.** `create_app()` with no vault argument
  opens the developer's real key store, and four tests assert a host credential is *absent*. Pinned
  to an empty vault. Latent rather than live - neither vault here holds a matching row - and the
  first version of that fix claimed an environment leak that mutation-checking disproved: the token
  comes from the vault via `from_vault`, not the shell. The comment now says what is actually true.

## 2026-08-20 - Launchers get a gate, because both startup bugs were unguarded code

- `tests/test_launcher_contract.py`. Two assertions over the real launcher files, not copies:
  every `openmw <command>` a launcher spawns must be a command Typer registers, and every launcher
  Python runs must encode under cp1252. Both incidents this week were the same class - a launcher is
  executable code that no test executed - and neither fix had anything stopping it regressing.
- Mutation-checked, both halves independently (R-0007): putting `serve` back in the Electron spawn
  fails the first; putting the U+2192 arrow back in the CLI fails the second. Either one would have
  caught its original bug before it shipped.
- **The gate was broken on its own first run.** The `serve` mutation passed, because a comment sits
  between `"openmw",` and `"console",` in the argv, so the regex found nothing there and matched a
  valid command name in unrelated prose further down the file instead - passing while guarding
  nothing. Line comments are now stripped before scanning. Worth remembering: a green mutation run is
  the only reason this was noticed, and the failure mode was the exact one the test's own assertion
  message warns about.

## 2026-08-20 - The intermittent-startup class: the desktop app never started a backend

- **`openvault app` spawned a command that does not exist.** `main-openvault.js` ran
  `uv run --directory OpenMW openmw serve`; the Typer app registers console, demo-ui, doctor, infer,
  route and train. `serve` exited 2 immediately on every cold start. Nothing stopped: `waitForServer`
  polled a dead port for 180s, warned "showing window anyway", and `createWindow()` ran regardless
  because the readiness check had no else branch. The console painted on :3010 and every panel then
  502'd, because next.config rewrites `/ov-api/*` to the custody API that was never started.
- **Why it read as random.** `openvault up`, `Start-NetieStack.ps1` and `Start-LocalMesh.ps1` all
  leave a long-lived `openmw console` on :5000, each with an explicit "already listening - reusing it"
  branch. Run any of those first and the desktop shell is perfect, because readiness succeeds on its
  first poll against somebody else's process. Cold machine: three-minute stall, then a dead window.
- Fixed: spawn `console --no-open-browser`, and a failed readiness check now says so with the exit
  code and the command to reproduce it. A window that paints and then fails on every action is a
  silent fallback, and a silent fallback is a lie (R-0011). The exit code had to be tracked
  separately - `sendToRenderer` no-ops while `mainWindow` is null, which it always is during
  start-up, and nothing in `apps/web` subscribes to "server-status" at all.
- **R-0012 was fixed on a branch that does not ship.** The laptop-ASCII fix for `openvault_cli.py`
  lived only on `fix/r0012-ascii-cli-output`; the integration branch still carried 15 non-ASCII lines
  and still died under cp1252. Cherry-picked. A fix that is not on the branch that merges is not a fix.

## 2026-08-19 - Key custody decided: the gateway spends our own pooled keys (#36)

- **The founder chose (a).** [`DR-0009`](docs/decisions/DR-0009-pooled-key-custody.md). OpenVault's
  metered gateway spends OpenVault's own keys and carries the provider cost and ToS exposure. Keys a
  tenant uploads are stored but never enter the fallback pool.
- **The hole that closes.** `fallback.ordered_candidates` applied no owner filter at all, so with
  issued `ov_` keys authenticating third parties, tenant A's request walked the same pool as everyone
  else and could select a key tenant B uploaded. Latent with one operator; real on the second tenant.
- **Two controls, because this is custody code.** A `custody` tag (`pooled` | `tenant`) on
  `KeyRecord`. `KeyVault.pooled_ordered()` is what the walk, the hop dashboard and the deploy gate all
  source from - `enabled_ordered()` keeps its meaning and is no longer a spend path. And
  `FallbackManager._is_available` refuses a non-pooled record *before* it checks health, so a future
  caller who sources from the wrong list still cannot reach a tenant key. Custody is checked ahead of
  priority: a tenant key at priority 0 loses to a pooled key at 100.
- **Upgrades keep working.** The migration backfills `pooled`, because before this column every key in
  the vault was the operator's own. Defaulting the other way would have 503'd every route on upgrade.
- **The refusal stopped lying** (R-0011). No pooled key while tenant keys are held is now typed
  `openvault_no_pooled_keys` and says how many are held; `openvault_no_keys` still means an empty vault.
  "No healthy API keys" while the vault visibly holds keys sends an operator looking in the wrong place.
- Asserted at the layer the customer receives (R-0001): `GET /api/usage` `vault_key_id`, not the
  manager object. Mutation matrix run on both controls independently plus together - the first draft of
  the suite could not detect removal of the availability guard at all, which is why the walk test exists.
- **Pricing is no longer deferrable.** (a) means we carry provider cost on every metered request.

## 2026-08-07  -  Detect->build->ship completed; console proxy closed; work retro-routed (#33-#36)

- **#35 the end-to-end path.** "Auto-detect, build, ship online" worked for one of four real
  hosts. Each caller guessed whether this machine had to build  -  one-press hardcoded
  `run_build=False`, the UI sent it only for Cloudflare Pages  -  so any Pages or Netlify deploy
  through one-press, and every Netlify deploy from the UI, hit the host step with nothing built
  and refused with "nothing was built". The adapter is the only thing that knows, so it now
  says: `needs_local_build` on the protocol, `build_here = run_build or needs_local_build(target)`
  in the engine, and both callers stop guessing.
- **#34 the console proxy.** `/ov-api/*` rewrites to `127.0.0.1:5000`, so FastAPI's loopback
  check saw a local peer for every proxied request whoever sent it  -  and the middleware
  allowlist that was the only real control omitted `/api/keys`, `/api/secrets`, `/api/vault/`.
  `x-forwarded-for` was read first, so any machine could claim to be loopback. Fixed at the
  cheapest rung first: the console now binds `127.0.0.1` (it was on 0.0.0.0, which is what made
  it reachable at all  -  one flag, matching what the API already defaults to). Then defence in
  depth: forwarded headers ignored unless `OPENVAULT_TRUST_PROXY` is set and their presence
  fails closed, and the guard **default-denies** backend routes with a small public allowlist,
  so a new custody route is local-only until someone deliberately publishes it.
- **#33 retro-routing.** Both feature waves were built on direct founder asks with no epic,
  which CLAUDE.md's routing rule exists to prevent. Filed against the PRD after the fact, with
  what shipped and what is deliberately out of scope. #36 records the per-tenant key custody
  decision as blocked on the founder rather than guessed at.
- File law: `DR-0008-agent-split-2026-07-26.md` -> `DR-0008-agent-split.md` (no dates in
  filenames outside an archive) and `0001-record-decisions-in-this-repo.md` ->
  `DR-0001-record-decisions.md`; inbound links updated.

## 2026-08-07  -  Metered gateway substrate: issued keys, usage ledger, output ceiling

**Security fix (the reason this went first).** `/v1/chat/completions` read `x-openfree-identity`
and `x-openfree-tier` straight off the request, and `DEFAULT_TIER` is `local` = 6000 rpm /
6,000,000 tpm. One header bought an unmetered pool of *our* vaulted provider keys; a caller who
hit a limit could change the identity string for a fresh bucket. Both headers are now ignored.

- `vault/api_keys.py`  -  `ov_`-prefixed keys, minted once, stored as SHA-256 only (same shape as
  `trust.py` `register_service`, same `keys.db`, no second scheme). `local` is not issuable.
- `vault/auth.py`  -  `resolve_caller`: bearer key -> its tier; no key on **loopback** -> historical
  dev tier (socket peer, never a forwarded header); no key from anywhere else -> 401.
  `OPENVAULT_REQUIRE_API_KEY=1` closes the loopback exemption for an exposed deployment.
- `vault/usage_store.py`  -  one durable row per request: caller, tier, provider, **resolved** model,
  the vault key that actually spent, tokens, `estimated`, `cache_hit`, status, latency. A stream
  without `include_usage` is recorded as the reservation with `estimated=true`; the summary reports
  estimated separately and carries `priced: false`. No price is invented  -  pricing is NEEDS-YOU.
- `vault/budget.py`  -  the ceiling `providers.budget_for` never had (it only ever raises). Drops an
  invalid `max_tokens` instead of walking the whole pool with a body every provider 400s; applies
  `OPENVAULT_MAX_OUTPUT_TOKENS` *before* the budget reservation (clamping after it would 429 a
  caller out of their own quota for tokens we would never send); refuses a prompt that cannot fit
  with `openvault_context_length_exceeded` and **zero** upstream calls. Context windows are not
  invented: `ProviderSpec.context_window` defaults to 0 = unknown = never refuse, and operators
  supply `OPENVAULT_CONTEXT_WINDOWS` until a cited table exists.
- `vault/fallback.py`  -  rendezvous (HRW) hashing pins a conversation's reusable prefix to one hop
  so upstream prompt caches stay warm. Ties only: priority, health, park windows still win.
- Ship: `classify_deployment`  -  a **pending host step no longer counts as ready**. `vps_ssh` with no
  server address answered `ok: true`; it now answers `ok: false`, `status: "blocked"`. New
  `status` field (live | simulated | planned | blocked | failed). `project_deploy_lock` refuses a
  second concurrent deploy for one project with HTTP 409  -  two runs picked the same blue/green
  colour, the same CRC32 port block, and raced `docker rm -f`.
- New: `POST/GET/DELETE /api/apikeys`, `GET /api/usage`. Demo script now holds a real issued key
  and asserts the ledger attributes to it.
- Tests: `test_freeroute_metering.py` (31) + ship cases. Four gates mutation-verified able to fail
  (R-0007): header trust, ledger attribution, env-file mode, the ready rule. Three existing tests
  moved from spoofed headers to issued keys  -  intent unchanged, mechanism replaced.

**Adversarial round (R-0003  -  a different set of agents verified this, and found real defects).**
Six confirmed, all fixed, each with a regression gate:

- **Tier fail-open (critical, introduced by this wave).** `issue(tier=...)` validated against a
  one-item denylist, and `tier_for` fell back to `DEFAULT_TIER`  -  which *is* `local`. So
  `tier: "unlimited"` was accepted and resolved to 6000 rpm / 6M tpm: the same bypass, moved from
  the header to mint time. Now an allowlist at issue, *and* an unknown tier resolves to the
  **smallest** configured bucket. Two independent controls, because this one is worth getting
  wrong twice.
- **Unauthenticated control plane (critical).** `/api/apikeys` mint/list/revoke and `/api/usage`
  shipped without the `_require_loopback` every sibling custody route has  -  anyone reaching the
  port could mint themselves the credential the gateway requires, or enumerate and revoke every
  key. `/api/usage` and `/api/freeroute/ratelimit` now scope to the caller's own key; the
  unfiltered operator view is loopback-only. The ratelimit snapshot answered `?tier=local`
  (6M tpm) to a caller holding 40k, and Cortex reads that number to decide affordability.
- **Ungated remote deploys (critical, from the previous wave).** `/api/ship/engine` had no leave
  gate at all, and one-press hung its gate on `auto_execute` while running the engine
  unconditionally  -  so `auto_execute: false` deployed anyway, and `simulate: true` never reached
  the engine. Once `vps_ssh` landed, that was an ungated route that SSHes into a box and swaps
  live traffic. Both now gate on the target actually leaving the machine.
- **Affinity pinned nothing.** The key hashed `messages[:-1]`, which grows every turn  -  so each
  turn produced a different key and the conversation hopped accounts anyway. Now the fixed
  conversation head, tenant-namespaced, JSON-encoded (an unescaped join let two different
  conversations collide), with the `user` field no longer treated as a cache key.
- **Budget resolution.** First-match-wins across three fields widened a deliberate `100` cap to
  `8000`, deleted a valid `2000` alongside an invalid `0`, and invented a `max_tokens` for callers
  who sent only `max_completion_tokens`. Now min-of-valid, written back only to fields the caller
  sent. `0.5` no longer truncates to a forwarded `0`. A reasoning floor above the ceiling skips
  the hop instead of sending a budget too small to produce content  -  which returns HTTP 200 with
  empty content that `classify_attempt` scores as **success**, stopping the walk.
- **False context refusal.** The 400 fired when *one* hop was size-blocked and others were skipped
  for unrelated reasons (open breaker, anthropic, missing base_url), telling the caller nobody
  could serve a prompt a 200k-window key was never asked about.

Also: the blocking ship engine moved off the event loop (`run_in_threadpool`)  -  a 30-minute deploy
froze every other request, including health checks. Deploy-lock identity now derives from the
*remote* project name both the lock and the adapter use, so two local folders named `site` share a
lock (they share `/srv/openvault/site`) while two hostnames from one checkout do not.

Not built, deliberately: **skills library** (PRODUCT_ROLES gives OpenVault "not the agent loop";
this needs a founder amendment, not a ticket), **prompt compression** (Cortex owns deciding what
context matters; a gateway that silently shortens a prompt violates R-0011), **response cache**
and **serving-engine migration from AirGPT** (both owned by OpenVault, both unrouted).

## 2026-08-06  -  FreeBuild: we host it  -  VPS adapter (`vps_ssh`)

- `ship/hosts/vps_ssh.py`: the first target where OpenVault does the hosting work.
  One box the user rents + one domain they own -> preflight (SSH, root/sudo, apt) ->
  install Docker + Caddy -> upload source -> build on the box (repo `Dockerfile` wins,
  else generated from detection) -> N replicas on 127.0.0.1 ports -> Caddy
  `reverse_proxy` with `lb_policy least_conn` + automatic TLS.
- Zero-downtime or refuse: new replicas come up on the other colour's port block and
  must answer HTTP before the proxy switches. Caddy config is staged, `caddy validate`d
  and rolled back on error, so a bad site block cannot take the box down. Old colour is
  removed only after the public URL answered.
- Honesty: `ok=True` only after a request to the customer-facing URL succeeded. DNS not
  pointed here yet returns the proxy's own answer plus the exact A record  -  not a URL.
- Secrets-at-ship: env values stream over the SSH pipe into a 0600 `--env-file`; never
  in argv, logs, step detail, or the API response.
- Replaces the `vps_ssh` engine stub ("wire ssh executor next"). Registered in
  `hosts/ADAPTERS`; `/api/ship/preflight` + target card + blueprint steps rewritten.
  `recommend_target(vps_configured=True)` now picks the user's own VPS for server
  stacks instead of a FreeBuild Cloud account they may not have.
- Tests: `test_hosts_vps_ssh.py` (53, fake SSH transport), engine pending/fail cases,
  preflight + recommend wiring. Health gate and 0600 gate verified able to fail
  (R-0007). No live box  -  HT1 still needs the founder.

## 2026-08-06  -  One-seat demo path + docs (#32)

- `OpenMW/scripts/one_seat_demo.py`: in-process vault -> FreeRoute empty/sealed refuse ->
  gated ship allow (`local_demo`/simulate, no fake URL) -> gate deny; writes evidence JSON.
- CLI: `openvault demo-path`. Buyer doc: `docs/ONE_SEAT_DEMO.md` (HT1-HT5 human-only stop).
- No live CF/Coolify/Netlify, no live FreeRoute paid keys. Ticket left open for adversary.
  HUMAN_STOP  -  founder clears HT1-HT5 on epic #18.

## 2026-08-06  -  Gate + engine actionable UI (#31)

- `apps/web` `/gate`: user-triggered check against `/api/gate/check`; verdict matrix
  (allow/deny, keys_ready, sealed, reasons, locate/firewall)  -  not a JSON dump.
- `/engine`: readable Cortex online + orchestration selection from
  `/api/cortex/status` and `/api/orchestration/selection`.
- UI-only; no OpenMW API changes. Ticket left open for adversary. Next: #16 Mode B.

## 2026-08-06  -  Netlify host adapter (#30)

- `ship/hosts/netlify.py`: preflight (token via GET `/user`) -> zip Direct Upload
  POST `/sites/{id}/deploys` -> poll until ready -> return only observed `ssl_url` /
  `deploy_ssl_url` / `url`. Never invents `*.netlify.app`.
- Registered in `hosts/ADAPTERS`; target card + engine + `/api/ship/preflight` wired
  like Coolify/CF Pages. Vercel stays detect-only. No live Netlify/HT1.
- Tests: `test_hosts_netlify.py` (mocked HTTP). Ticket left open for adversary.
  Next: #31.

## 2026-08-06  -  Coolify host adapter (#29)

- `ship/hosts/coolify.py`: preflight (URL+token+app UUID) -> POST `/api/v1/deploy` ->
  poll deployment -> return only observed `deployment_url` / application `fqdn`.
- Registered in `hosts/ADAPTERS`; target card + engine + `/api/ship/preflight` wired
  like Cloudflare Pages. Never fabricates URLs; no live Coolify/HT1.
- Tests: `test_hosts_coolify.py` (mocked HTTP). Ticket left open for adversary.
  Next: #30 Netlify.

## 2026-08-06  -  Secrets-at-ship inject (#28)

- `ship/inject.py`: resolve vault key/password refs into deploy env; sealed/missing/PCI
  card refuse with concrete blockers; scrub/redact helpers; systemd env quoting (stolen
  pattern, not vendor import).
- `app.py` `freebuild_execute`: `DeployExecuteBody.secrets` -> inject -> `envVars` on
  FreeBuild wire; API payload scrubbed; audit names/sources only.
- `openship.py`: accepts `env_vars` / `secrets_injected`; never echoes values in steps.
- Tests: `test_ship_inject.py` (10). Ticket left open for adversary. Next: #29. No HT5.

## 2026-08-06  -  FreeBuild deploy honesty (#27)

- `ship/engine.py`: simulate / guide / pending host paths label non-production and leave
  `public_url` empty  -  no inventing `*.opsh.io` or hostname as live. Live URL only from
  observed CF Pages or remote FreeBuild payload; remote success without URL -> fail.
- `openship.py` simulate sets `adapter.non_production` + empty `public_url`; `cicd.py`
  notes suggest-only + simulate-default honesty; stream logs the simulate label.
- Tests: `test_ship_engine.py` simulate vs live URL contract. Ticket left open for
  adversary. Next: #28. No Coolify/Netlify/HT1.

## 2026-08-06  -  FreeRoute stream settle from include_usage (#26)

- `ratelimit.SseUsageCapture`: incremental SSE scan for usage (no full-stream buffer).
- `app.py` `v1_chat` stream: when `stream_options.include_usage`, settle from trail
  usage; if absent, keep reservation. Without the option, keep today's keep-reserve.
- Tests: `test_streaming_v1.py` + `test_ratelimit.py` SSE unit. Ticket left open for
  adversary. Epic #15 Mode B deferred until #25 closes. No FreeBuild / HT2.

## 2026-08-06  -  FreeRoute model auto for BYOK/local (#25)

- `providers.py`: honest `chat_models` (strongest first) for `openai`,
  `deepseek`, `ollama`, `litellm`, `cortex` so `model: auto` resolves instead
  of skipping with `no catalogued model`. Anthropic still empty (Messages API).
- Tests: `test_model_resolution.py` + proxy auto hop in `test_attempt_policy.py`.
- Ticket left open for adversary verify. Next: #26.

## 2026-08-06  -  FreeRoute sealed vault clear refuse (#24)

- `vault/proxy.py` fail-closed before hop walk when sealed (sync + stream):
  HTTP 403 `openvault_vault_sealed`  -  never uncaught `VaultSealedError` 500.
- Acceptance: `tests/test_freeroute_acceptance.py` (empty 503 / budget 429 / sealed 403).
- Ticket left open for adversary verify. Next: #25 after close.

## 2026-08-06  -  Audit gate deny + ignore_gate WARN (#23)

- `/api/gate/check` and leave-machine execute denials append `gate_denied` /
  `gate_bypass_attempt` to `secret_audit.jsonl` (action, reasons, client; no secrets).
- `GateCheckBody.ignore_gate` accepted and treated like other bypass flags (WARN+deny).
- Ticket left open for adversary verify. After close: epic #15 FREEROUTE ticketting.

## 2026-08-06  -  Leave-machine execute calls check_gate; sealed keys_ready=false (#22)

- `check_gate` denies deploy/leave when vault is sealed (`keys_ready=false`).
- `/api/deploy/*/execute`, `/api/freebuild/*/execute`, plan+execute, one-press
  auto_execute refuse with HTTP 403 + gate reasons when denied.
- Ticket left open for adversary verify. Next: #23. Do not start FreeRoute/FreeBuild hosts.

## 2026-08-06  -  Seal GitHub PAT in vault; retire pat.json (#20)

- Ship PAT durable store is KeyVault row `github-ship-pat` (same Seal as keys).
- `save_pat` / `resolve_token` / `clear_pat` migrate legacy `github/pat.json` once
  then delete it; fail closed when sealed. Resolve order: gh CLI -> sealed PAT -> env.
- Docs: `SECRETS_CUSTODY.md` (item 5 closed). Ticket left open for adversary verify.
  Next: #21 after verify. Do not start FreeBuild host shortlist here.

## 2026-08-06  -  Passphrase KDF + vault unseal/lock (#19)

- Additive `passphrase-scrypt` wrap (scrypt via cryptography) on `master.key`.
- Process starts sealed when passphrase configured; `POST /api/vault/unseal|lock|status|passphrase`.
- Reveal/mutate fail closed with sealed error until unseal. DPAPI/plain without
  passphrase still auto-unseals (no regression). Docs: `SECRETS_CUSTODY.md` §1b.
- Ticket left open for adversary verify. Next: #20 (PAT-in-vault).

## 2026-08-03  -  NVIDIA catalog authorized (AirGPT F4 YES)

- OpenVault PRD-001 F1 -> founder YES (c). Ticket: [#12](https://github.com/Netie-AI/OpenVault/issues/12)
  add nvidia to PROVIDER_CATALOG + freenvidia/nvapi probe + Excel/RAG fit note.
- Serves AirGPT PRD-001 F4. No AirGPT-side vault. STATUS Next: NV row.

## 2026-08-03  -  Free-API pile -> vault custody

Operator ingest from `D:\Netie\Free APIs for OpenVault Free\Keys.txt` into
`OPENVAULT_HOME` encrypted vault: refreshed OpenRouter/Cerebras/Mistral; added
custom Bytez/Aion/Kilo/Ollama Cloud (`https://ollama.com/v1`). Roles/priorities
reordered for free-fallback (Groq -> Google -> OpenRouter -> Cerebras...).
Plaintext scrubbed out of the Netie pile into gitignored
`.openvault/import/free-apis.keys.env`; `KEYS_PILE_FORMAT.md` added beside the
pile for next paste. Precheck: cloud keys `ok`; local Ollama/LiteLLM still
`error` until those processes run.

## 2026-08-02  -  Repo cleanup: quarantine + docs migration

Audited `nvme_sentinel/`, `Profiler/`, `OpenMW/`  -  confirmed ~100% live/tested, no dead
product code. Quarantined ~18 confirmed-dead files/dirs into `bin/` for founder review
(stray generated artifacts, one-off scratch scripts, 3 zero-inbound-link docs, orphaned
`apps/click`, electron leftovers). Archived 7 closed/superseded docs as MADR-format
decision records under `docs/decisions/DR-0002..DR-0008`. Relocated `implementation_plan.md`
to `docs/reference/nvme-sentinel-spec.md`. Retired `next_plan.md` and `AGENT_LANES.md`
(content folded into this file, `STATUS.md`, `PARKING_LOT.md`, and `CLAUDE.md`).

## 2026-07-31  -  Streaming, ship SSE, health history, FreeBuild CI/CD (next_plan #1-#6, #9)

- `#6` Stored-mask column on `keys`  -  `list_keys` no longer decrypts every secret to build a mask (`vault/store.py`, `tests/test_stored_mask.py`).
- `#1` Streaming `POST /v1/chat/completions` (`vault/proxy.py::prepare_chat_stream`, `tests/test_streaming_v1.py`).
- `#3` Ship SSE + `/ship/deploy/[id]` + BuildLogPane (`ship/stream.py`, `GET /api/ship/engine/{id}/stream`).
- `#2` Health history + vault sparklines (`vault/health_store.py`, `GET /api/keys/{id}/health`, `KeyHealthSpark`  -  see `docs/decisions/DR-0007-card-health-history.md`).
- `#4` FreeBuild CI/CD page (`apps/web/src/app/ship/cicd/page.tsx`, nav CI/CD).
- `#5` Remote FreeBuild `project_id` honesty  -  `POST /api/freebuild/{id}/execute` returns 400 with a clear detail instead of silently proceeding.
- `#9` Cortex `:8010` smoke snapshot green (health 200 + OV JWKS 200)  -  merge stayed operator-gated, not automatic.
- One-stop B pass: precheck logs use `key_ref` + label/provider/error (no full vault UUID); `openvault up` auto-opens `:3010`; Vault shows failing key identity + Sync preview; Ship has GitHub connect panel; Providers in nav.
- UI: real app is `apps/web` on `:3010`. Old `OpenMW/webui/index.html` deleted  -  `:5000/` redirects to the app.

## 2026-07-27  -  Access routing, secrets custody, Free* rename

- Access routing (`route/access.py`): registry derived from live mesh peers + vault keys
  (never a hardcoded catalogue); `/api/access/resolve` returns location + owner + gate
  verdict; explicit intent-to-gate mapping; 16 tests in `tests/test_access_routing.py`.
  Known gap: resolve reports from the last mesh probe, not a live check.
- Secrets custody (`vault/secrets.py`): password + payment-card kinds in the same `keys.db`
  under the same master key; every custody mutation (not just reveal) is now loopback-only
  and audited; CVV is refused outright, never stored. See `docs/SECRETS_CUSTODY.md` and
  `docs/decisions/DR-0005-backend-honesty-audit.md`.
- Free* rename: OpenIDE to FreeIDE, OpenShip to FreeBuild, OpenFree to FreeRoute (display +
  routes). OpenVault keeps its name. Old paths stay as hidden aliases. Not renamed: Python
  modules, class names, env vars, `~/.openvault`. See `PRODUCT_ROLES.md`.

## 2026-07-25  -  Redis+Lua FreeRoute, contract audit

- `vault/redis_store.py`: atomic dual-bucket Lua EVAL; `OPENVAULT_REDIS_URL` activates it,
  else in-memory. Cortex `workflow_openvault.ping` fixed to `/api/healthz`.
- Cross-layer contract audit  -  see `docs/decisions/DR-0004-contract-audit.md`.
- Backend honesty audit  -  see `docs/decisions/DR-0005-backend-honesty-audit.md`.

## 2026-07-24  -  In-process ship engine, FreeRoute gateway, layer contract

- Ship engine made primary (not the remote client): `ship/engine.py` (detect to cicd to
  domain to target host), `ship/github_auth.py` (gh CLI + PAT), `ship/library.py`
  (folder/URL/upload/clone). AWS skills vendored for IaC generation.
- FreeRoute gateway: dual-bucket limiter (QPS + token budget) with smooth refill,
  reserve-then-refund around `max_tokens`, `local`/`free`/`pro` tiers, `429` +
  `Retry-After`, rate-limit headers on every response. Auto-vault from env
  (`vault/env_ingest.py`)  -  scans credential-shaped env vars, never echoes secrets.
- Layer contract conformance: fixed the mesh defaulting FreeIDE to the dead `:5100` stub
  instead of `:8765`; `DEFAULT_PORTS` in `mesh/local_mesh.py` is now the single source of
  truth. Added `GET /api/freeide/ready`. Locked by `tests/test_contract.py`.
- Small Software LAN cloud v0 shipped  -  see `docs/decisions/DR-0002-small-software-lan-cloud.md`.

## 2026-07-23  -  nvme-sentinel v0.1.0

HAL/adapters/CLI/bench/CI green. Interview gate P1-P6 complete.
