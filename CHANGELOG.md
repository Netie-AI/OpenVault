# CHANGELOG

Append-only. Never edited, only added to. Newest first.

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

## 2026-08-07 — Detect→build→ship completed; console proxy closed; work retro-routed (#33–#36)

- **#35 the end-to-end path.** "Auto-detect, build, ship online" worked for one of four real
  hosts. Each caller guessed whether this machine had to build — one-press hardcoded
  `run_build=False`, the UI sent it only for Cloudflare Pages — so any Pages or Netlify deploy
  through one-press, and every Netlify deploy from the UI, hit the host step with nothing built
  and refused with "nothing was built". The adapter is the only thing that knows, so it now
  says: `needs_local_build` on the protocol, `build_here = run_build or needs_local_build(target)`
  in the engine, and both callers stop guessing.
- **#34 the console proxy.** `/ov-api/*` rewrites to `127.0.0.1:5000`, so FastAPI's loopback
  check saw a local peer for every proxied request whoever sent it — and the middleware
  allowlist that was the only real control omitted `/api/keys`, `/api/secrets`, `/api/vault/`.
  `x-forwarded-for` was read first, so any machine could claim to be loopback. Fixed at the
  cheapest rung first: the console now binds `127.0.0.1` (it was on 0.0.0.0, which is what made
  it reachable at all — one flag, matching what the API already defaults to). Then defence in
  depth: forwarded headers ignored unless `OPENVAULT_TRUST_PROXY` is set and their presence
  fails closed, and the guard **default-denies** backend routes with a small public allowlist,
  so a new custody route is local-only until someone deliberately publishes it.
- **#33 retro-routing.** Both feature waves were built on direct founder asks with no epic,
  which CLAUDE.md's routing rule exists to prevent. Filed against the PRD after the fact, with
  what shipped and what is deliberately out of scope. #36 records the per-tenant key custody
  decision as blocked on the founder rather than guessed at.
- File law: `DR-0008-agent-split-2026-07-26.md` → `DR-0008-agent-split.md` (no dates in
  filenames outside an archive) and `0001-record-decisions-in-this-repo.md` →
  `DR-0001-record-decisions.md`; inbound links updated.

## 2026-08-07 — Metered gateway substrate: issued keys, usage ledger, output ceiling

**Security fix (the reason this went first).** `/v1/chat/completions` read `x-openfree-identity`
and `x-openfree-tier` straight off the request, and `DEFAULT_TIER` is `local` = 6000 rpm /
6,000,000 tpm. One header bought an unmetered pool of *our* vaulted provider keys; a caller who
hit a limit could change the identity string for a fresh bucket. Both headers are now ignored.

- `vault/api_keys.py` — `ov_`-prefixed keys, minted once, stored as SHA-256 only (same shape as
  `trust.py` `register_service`, same `keys.db`, no second scheme). `local` is not issuable.
- `vault/auth.py` — `resolve_caller`: bearer key → its tier; no key on **loopback** → historical
  dev tier (socket peer, never a forwarded header); no key from anywhere else → 401.
  `OPENVAULT_REQUIRE_API_KEY=1` closes the loopback exemption for an exposed deployment.
- `vault/usage_store.py` — one durable row per request: caller, tier, provider, **resolved** model,
  the vault key that actually spent, tokens, `estimated`, `cache_hit`, status, latency. A stream
  without `include_usage` is recorded as the reservation with `estimated=true`; the summary reports
  estimated separately and carries `priced: false`. No price is invented — pricing is NEEDS-YOU.
- `vault/budget.py` — the ceiling `providers.budget_for` never had (it only ever raises). Drops an
  invalid `max_tokens` instead of walking the whole pool with a body every provider 400s; applies
  `OPENVAULT_MAX_OUTPUT_TOKENS` *before* the budget reservation (clamping after it would 429 a
  caller out of their own quota for tokens we would never send); refuses a prompt that cannot fit
  with `openvault_context_length_exceeded` and **zero** upstream calls. Context windows are not
  invented: `ProviderSpec.context_window` defaults to 0 = unknown = never refuse, and operators
  supply `OPENVAULT_CONTEXT_WINDOWS` until a cited table exists.
- `vault/fallback.py` — rendezvous (HRW) hashing pins a conversation's reusable prefix to one hop
  so upstream prompt caches stay warm. Ties only: priority, health, park windows still win.
- Ship: `classify_deployment` — a **pending host step no longer counts as ready**. `vps_ssh` with no
  server address answered `ok: true`; it now answers `ok: false`, `status: "blocked"`. New
  `status` field (live | simulated | planned | blocked | failed). `project_deploy_lock` refuses a
  second concurrent deploy for one project with HTTP 409 — two runs picked the same blue/green
  colour, the same CRC32 port block, and raced `docker rm -f`.
- New: `POST/GET/DELETE /api/apikeys`, `GET /api/usage`. Demo script now holds a real issued key
  and asserts the ledger attributes to it.
- Tests: `test_freeroute_metering.py` (31) + ship cases. Four gates mutation-verified able to fail
  (R-0007): header trust, ledger attribution, env-file mode, the ready rule. Three existing tests
  moved from spoofed headers to issued keys — intent unchanged, mechanism replaced.

**Adversarial round (R-0003 — a different set of agents verified this, and found real defects).**
Six confirmed, all fixed, each with a regression gate:

- **Tier fail-open (critical, introduced by this wave).** `issue(tier=...)` validated against a
  one-item denylist, and `tier_for` fell back to `DEFAULT_TIER` — which *is* `local`. So
  `tier: "unlimited"` was accepted and resolved to 6000 rpm / 6M tpm: the same bypass, moved from
  the header to mint time. Now an allowlist at issue, *and* an unknown tier resolves to the
  **smallest** configured bucket. Two independent controls, because this one is worth getting
  wrong twice.
- **Unauthenticated control plane (critical).** `/api/apikeys` mint/list/revoke and `/api/usage`
  shipped without the `_require_loopback` every sibling custody route has — anyone reaching the
  port could mint themselves the credential the gateway requires, or enumerate and revoke every
  key. `/api/usage` and `/api/freeroute/ratelimit` now scope to the caller's own key; the
  unfiltered operator view is loopback-only. The ratelimit snapshot answered `?tier=local`
  (6M tpm) to a caller holding 40k, and Cortex reads that number to decide affordability.
- **Ungated remote deploys (critical, from the previous wave).** `/api/ship/engine` had no leave
  gate at all, and one-press hung its gate on `auto_execute` while running the engine
  unconditionally — so `auto_execute: false` deployed anyway, and `simulate: true` never reached
  the engine. Once `vps_ssh` landed, that was an ungated route that SSHes into a box and swaps
  live traffic. Both now gate on the target actually leaving the machine.
- **Affinity pinned nothing.** The key hashed `messages[:-1]`, which grows every turn — so each
  turn produced a different key and the conversation hopped accounts anyway. Now the fixed
  conversation head, tenant-namespaced, JSON-encoded (an unescaped join let two different
  conversations collide), with the `user` field no longer treated as a cache key.
- **Budget resolution.** First-match-wins across three fields widened a deliberate `100` cap to
  `8000`, deleted a valid `2000` alongside an invalid `0`, and invented a `max_tokens` for callers
  who sent only `max_completion_tokens`. Now min-of-valid, written back only to fields the caller
  sent. `0.5` no longer truncates to a forwarded `0`. A reasoning floor above the ceiling skips
  the hop instead of sending a budget too small to produce content — which returns HTTP 200 with
  empty content that `classify_attempt` scores as **success**, stopping the walk.
- **False context refusal.** The 400 fired when *one* hop was size-blocked and others were skipped
  for unrelated reasons (open breaker, anthropic, missing base_url), telling the caller nobody
  could serve a prompt a 200k-window key was never asked about.

Also: the blocking ship engine moved off the event loop (`run_in_threadpool`) — a 30-minute deploy
froze every other request, including health checks. Deploy-lock identity now derives from the
*remote* project name both the lock and the adapter use, so two local folders named `site` share a
lock (they share `/srv/openvault/site`) while two hostnames from one checkout do not.

Not built, deliberately: **skills library** (PRODUCT_ROLES gives OpenVault "not the agent loop";
this needs a founder amendment, not a ticket), **prompt compression** (Cortex owns deciding what
context matters; a gateway that silently shortens a prompt violates R-0011), **response cache**
and **serving-engine migration from AirGPT** (both owned by OpenVault, both unrouted).

## 2026-08-06 — FreeBuild: we host it — VPS adapter (`vps_ssh`)

- `ship/hosts/vps_ssh.py`: the first target where OpenVault does the hosting work.
  One box the user rents + one domain they own → preflight (SSH, root/sudo, apt) →
  install Docker + Caddy → upload source → build on the box (repo `Dockerfile` wins,
  else generated from detection) → N replicas on 127.0.0.1 ports → Caddy
  `reverse_proxy` with `lb_policy least_conn` + automatic TLS.
- Zero-downtime or refuse: new replicas come up on the other colour's port block and
  must answer HTTP before the proxy switches. Caddy config is staged, `caddy validate`d
  and rolled back on error, so a bad site block cannot take the box down. Old colour is
  removed only after the public URL answered.
- Honesty: `ok=True` only after a request to the customer-facing URL succeeded. DNS not
  pointed here yet returns the proxy's own answer plus the exact A record — not a URL.
- Secrets-at-ship: env values stream over the SSH pipe into a 0600 `--env-file`; never
  in argv, logs, step detail, or the API response.
- Replaces the `vps_ssh` engine stub ("wire ssh executor next"). Registered in
  `hosts/ADAPTERS`; `/api/ship/preflight` + target card + blueprint steps rewritten.
  `recommend_target(vps_configured=True)` now picks the user's own VPS for server
  stacks instead of a FreeBuild Cloud account they may not have.
- Tests: `test_hosts_vps_ssh.py` (53, fake SSH transport), engine pending/fail cases,
  preflight + recommend wiring. Health gate and 0600 gate verified able to fail
  (R-0007). No live box — HT1 still needs the founder.

## 2026-08-06 — One-seat demo path + docs (#32)

- `OpenMW/scripts/one_seat_demo.py`: in-process vault → FreeRoute empty/sealed refuse →
  gated ship allow (`local_demo`/simulate, no fake URL) → gate deny; writes evidence JSON.
- CLI: `openvault demo-path`. Buyer doc: `docs/ONE_SEAT_DEMO.md` (HT1–HT5 human-only stop).
- No live CF/Coolify/Netlify, no live FreeRoute paid keys. Ticket left open for adversary.
  HUMAN_STOP — founder clears HT1–HT5 on epic #18.

## 2026-08-06 — Gate + engine actionable UI (#31)

- `apps/web` `/gate`: user-triggered check against `/api/gate/check`; verdict matrix
  (allow/deny, keys_ready, sealed, reasons, locate/firewall) — not a JSON dump.
- `/engine`: readable Cortex online + orchestration selection from
  `/api/cortex/status` and `/api/orchestration/selection`.
- UI-only; no OpenMW API changes. Ticket left open for adversary. Next: #16 Mode B.

## 2026-08-06 — Netlify host adapter (#30)

- `ship/hosts/netlify.py`: preflight (token via GET `/user`) → zip Direct Upload
  POST `/sites/{id}/deploys` → poll until ready → return only observed `ssl_url` /
  `deploy_ssl_url` / `url`. Never invents `*.netlify.app`.
- Registered in `hosts/ADAPTERS`; target card + engine + `/api/ship/preflight` wired
  like Coolify/CF Pages. Vercel stays detect-only. No live Netlify/HT1.
- Tests: `test_hosts_netlify.py` (mocked HTTP). Ticket left open for adversary.
  Next: #31.

## 2026-08-06 — Coolify host adapter (#29)

- `ship/hosts/coolify.py`: preflight (URL+token+app UUID) → POST `/api/v1/deploy` →
  poll deployment → return only observed `deployment_url` / application `fqdn`.
- Registered in `hosts/ADAPTERS`; target card + engine + `/api/ship/preflight` wired
  like Cloudflare Pages. Never fabricates URLs; no live Coolify/HT1.
- Tests: `test_hosts_coolify.py` (mocked HTTP). Ticket left open for adversary.
  Next: #30 Netlify.

## 2026-08-06 — Secrets-at-ship inject (#28)

- `ship/inject.py`: resolve vault key/password refs into deploy env; sealed/missing/PCI
  card refuse with concrete blockers; scrub/redact helpers; systemd env quoting (stolen
  pattern, not vendor import).
- `app.py` `freebuild_execute`: `DeployExecuteBody.secrets` → inject → `envVars` on
  FreeBuild wire; API payload scrubbed; audit names/sources only.
- `openship.py`: accepts `env_vars` / `secrets_injected`; never echoes values in steps.
- Tests: `test_ship_inject.py` (10). Ticket left open for adversary. Next: #29. No HT5.

## 2026-08-06 — FreeBuild deploy honesty (#27)

- `ship/engine.py`: simulate / guide / pending host paths label non-production and leave
  `public_url` empty — no inventing `*.opsh.io` or hostname as live. Live URL only from
  observed CF Pages or remote FreeBuild payload; remote success without URL → fail.
- `openship.py` simulate sets `adapter.non_production` + empty `public_url`; `cicd.py`
  notes suggest-only + simulate-default honesty; stream logs the simulate label.
- Tests: `test_ship_engine.py` simulate vs live URL contract. Ticket left open for
  adversary. Next: #28. No Coolify/Netlify/HT1.

## 2026-08-06 — FreeRoute stream settle from include_usage (#26)

- `ratelimit.SseUsageCapture`: incremental SSE scan for usage (no full-stream buffer).
- `app.py` `v1_chat` stream: when `stream_options.include_usage`, settle from trail
  usage; if absent, keep reservation. Without the option, keep today's keep-reserve.
- Tests: `test_streaming_v1.py` + `test_ratelimit.py` SSE unit. Ticket left open for
  adversary. Epic #15 Mode B deferred until #25 closes. No FreeBuild / HT2.

## 2026-08-06 — FreeRoute model auto for BYOK/local (#25)

- `providers.py`: honest `chat_models` (strongest first) for `openai`,
  `deepseek`, `ollama`, `litellm`, `cortex` so `model: auto` resolves instead
  of skipping with `no catalogued model`. Anthropic still empty (Messages API).
- Tests: `test_model_resolution.py` + proxy auto hop in `test_attempt_policy.py`.
- Ticket left open for adversary verify. Next: #26.

## 2026-08-06 — FreeRoute sealed vault clear refuse (#24)

- `vault/proxy.py` fail-closed before hop walk when sealed (sync + stream):
  HTTP 403 `openvault_vault_sealed` — never uncaught `VaultSealedError` 500.
- Acceptance: `tests/test_freeroute_acceptance.py` (empty 503 / budget 429 / sealed 403).
- Ticket left open for adversary verify. Next: #25 after close.

## 2026-08-06 — Audit gate deny + ignore_gate WARN (#23)

- `/api/gate/check` and leave-machine execute denials append `gate_denied` /
  `gate_bypass_attempt` to `secret_audit.jsonl` (action, reasons, client; no secrets).
- `GateCheckBody.ignore_gate` accepted and treated like other bypass flags (WARN+deny).
- Ticket left open for adversary verify. After close: epic #15 FREEROUTE ticketting.

## 2026-08-06 — Leave-machine execute calls check_gate; sealed keys_ready=false (#22)

- `check_gate` denies deploy/leave when vault is sealed (`keys_ready=false`).
- `/api/deploy/*/execute`, `/api/freebuild/*/execute`, plan+execute, one-press
  auto_execute refuse with HTTP 403 + gate reasons when denied.
- Ticket left open for adversary verify. Next: #23. Do not start FreeRoute/FreeBuild hosts.

## 2026-08-06 — Seal GitHub PAT in vault; retire pat.json (#20)

- Ship PAT durable store is KeyVault row `github-ship-pat` (same Seal as keys).
- `save_pat` / `resolve_token` / `clear_pat` migrate legacy `github/pat.json` once
  then delete it; fail closed when sealed. Resolve order: gh CLI → sealed PAT → env.
- Docs: `SECRETS_CUSTODY.md` (item 5 closed). Ticket left open for adversary verify.
  Next: #21 after verify. Do not start FreeBuild host shortlist here.

## 2026-08-06 — Passphrase KDF + vault unseal/lock (#19)

- Additive `passphrase-scrypt` wrap (scrypt via cryptography) on `master.key`.
- Process starts sealed when passphrase configured; `POST /api/vault/unseal|lock|status|passphrase`.
- Reveal/mutate fail closed with sealed error until unseal. DPAPI/plain without
  passphrase still auto-unseals (no regression). Docs: `SECRETS_CUSTODY.md` §1b.
- Ticket left open for adversary verify. Next: #20 (PAT-in-vault).

## 2026-08-03 — NVIDIA catalog authorized (AirGPT F4 YES)

- OpenVault PRD-001 F1 -> founder YES (c). Ticket: [#12](https://github.com/Netie-AI/OpenVault/issues/12)
  add nvidia to PROVIDER_CATALOG + freenvidia/nvapi probe + Excel/RAG fit note.
- Serves AirGPT PRD-001 F4. No AirGPT-side vault. STATUS Next: NV row.

## 2026-08-03 — Free-API pile -> vault custody

Operator ingest from `D:\Netie\Free APIs for OpenVault Free\Keys.txt` into
`OPENVAULT_HOME` encrypted vault: refreshed OpenRouter/Cerebras/Mistral; added
custom Bytez/Aion/Kilo/Ollama Cloud (`https://ollama.com/v1`). Roles/priorities
reordered for free-fallback (Groq -> Google -> OpenRouter -> Cerebras...).
Plaintext scrubbed out of the Netie pile into gitignored
`.openvault/import/free-apis.keys.env`; `KEYS_PILE_FORMAT.md` added beside the
pile for next paste. Precheck: cloud keys `ok`; local Ollama/LiteLLM still
`error` until those processes run.

## 2026-08-02 — Repo cleanup: quarantine + docs migration

Audited `nvme_sentinel/`, `Profiler/`, `OpenMW/` — confirmed ~100% live/tested, no dead
product code. Quarantined ~18 confirmed-dead files/dirs into `bin/` for founder review
(stray generated artifacts, one-off scratch scripts, 3 zero-inbound-link docs, orphaned
`apps/click`, electron leftovers). Archived 7 closed/superseded docs as MADR-format
decision records under `docs/decisions/DR-0002..DR-0008`. Relocated `implementation_plan.md`
to `docs/reference/nvme-sentinel-spec.md`. Retired `next_plan.md` and `AGENT_LANES.md`
(content folded into this file, `STATUS.md`, `PARKING_LOT.md`, and `CLAUDE.md`).

## 2026-07-31 — Streaming, ship SSE, health history, FreeBuild CI/CD (next_plan #1-#6, #9)

- `#6` Stored-mask column on `keys` — `list_keys` no longer decrypts every secret to build a mask (`vault/store.py`, `tests/test_stored_mask.py`).
- `#1` Streaming `POST /v1/chat/completions` (`vault/proxy.py::prepare_chat_stream`, `tests/test_streaming_v1.py`).
- `#3` Ship SSE + `/ship/deploy/[id]` + BuildLogPane (`ship/stream.py`, `GET /api/ship/engine/{id}/stream`).
- `#2` Health history + vault sparklines (`vault/health_store.py`, `GET /api/keys/{id}/health`, `KeyHealthSpark` — see `docs/decisions/DR-0007-card-health-history.md`).
- `#4` FreeBuild CI/CD page (`apps/web/src/app/ship/cicd/page.tsx`, nav CI/CD).
- `#5` Remote FreeBuild `project_id` honesty — `POST /api/freebuild/{id}/execute` returns 400 with a clear detail instead of silently proceeding.
- `#9` Cortex `:8010` smoke snapshot green (health 200 + OV JWKS 200) — merge stayed operator-gated, not automatic.
- One-stop B pass: precheck logs use `key_ref` + label/provider/error (no full vault UUID); `openvault up` auto-opens `:3010`; Vault shows failing key identity + Sync preview; Ship has GitHub connect panel; Providers in nav.
- UI: real app is `apps/web` on `:3010`. Old `OpenMW/webui/index.html` deleted — `:5000/` redirects to the app.

## 2026-07-27 — Access routing, secrets custody, Free* rename

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

## 2026-07-25 — Redis+Lua FreeRoute, contract audit

- `vault/redis_store.py`: atomic dual-bucket Lua EVAL; `OPENVAULT_REDIS_URL` activates it,
  else in-memory. Cortex `workflow_openvault.ping` fixed to `/api/healthz`.
- Cross-layer contract audit — see `docs/decisions/DR-0004-contract-audit.md`.
- Backend honesty audit — see `docs/decisions/DR-0005-backend-honesty-audit.md`.

## 2026-07-24 — In-process ship engine, FreeRoute gateway, layer contract

- Ship engine made primary (not the remote client): `ship/engine.py` (detect to cicd to
  domain to target host), `ship/github_auth.py` (gh CLI + PAT), `ship/library.py`
  (folder/URL/upload/clone). AWS skills vendored for IaC generation.
- FreeRoute gateway: dual-bucket limiter (QPS + token budget) with smooth refill,
  reserve-then-refund around `max_tokens`, `local`/`free`/`pro` tiers, `429` +
  `Retry-After`, rate-limit headers on every response. Auto-vault from env
  (`vault/env_ingest.py`) — scans credential-shaped env vars, never echoes secrets.
- Layer contract conformance: fixed the mesh defaulting FreeIDE to the dead `:5100` stub
  instead of `:8765`; `DEFAULT_PORTS` in `mesh/local_mesh.py` is now the single source of
  truth. Added `GET /api/freeide/ready`. Locked by `tests/test_contract.py`.
- Small Software LAN cloud v0 shipped — see `docs/decisions/DR-0002-small-software-lan-cloud.md`.

## 2026-07-23 — nvme-sentinel v0.1.0

HAL/adapters/CLI/bench/CI green. Interview gate P1-P6 complete.
