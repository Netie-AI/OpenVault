---
status: accepted
date: 2026-09-26
accepted: 2026-10-30
decision-makers: founder
---

> Accepted 2026-10-30. The founder's merge go in the build session, in his words: "ensure working freeroute and openship" and "all merge". This record merged with #99.

# DR-0018 - Fork OmniRoute and Openship into OpenVault as FreeRoute and FreeBuild

Supersedes, in part, [DR-0003](DR-0003-openship-app-plan.md) ("run none of OmniRoute's or
FreeBuild's stack", "port the algorithms, not wholesale") and the TAS lines in
`D:\Netie` that say "do not vendor OmniRoute". Those TAS lines are outside this repo and
still need marking there.

## Context and Problem Statement

OpenVault rebuilt FreeRoute and FreeBuild from scratch in Python and Next, borrowing
algorithms and UI tokens from OmniRoute and Openship (DR-0003). The founder decided on
2026-09-26 to stop rebuilding and take the upstream apps whole, in his words: "our logos
our theme our language style and our agents our rules terms ... our edition and our tone
our web design style ... then become our product then ship".

## Considered Options

- (a) Keep rebuilding in Python (DR-0003 path)
- (b) Fork both upstreams into this repo, rebrand, and cut what we will not ship
- (c) Depend on the upstream packages at runtime without forking

## Decision Outcome

Chosen option: (b). Import pruned snapshots as `apps/router` (ships as **FreeRoute**) and
`apps/ship` (ships as **FreeBuild**). Names follow PRODUCT_ROLES.md, which already used
FreeRoute and FreeBuild.

| App | Upstream | Commit | License |
|-----|----------|--------|---------|
| `apps/router` | diegosouzapw/OmniRoute (itself from decolua/9router) | `ae2ba35852d4e5a55486a1c0e6a779105564fd6d` | MIT |
| `apps/ship` | oblien/openship | `aba12c8dbee5cd92673645796ae68616a8fa30e1` | Apache-2.0 |

### Hard lines (legal, not style)

1. Every upstream LICENSE, NOTICE and THIRD_PARTY_NOTICES file and every copyright header
   stays verbatim. Our copyright is added beside theirs, never in place of it.
2. Root `THIRD_PARTY_NOTICES.md` records source URL, commit, license and what was pruned.
3. Modified Apache files carry `Modified by Netie AI, 2026`; comment-less files are listed
   in `apps/ship/NOTICE`.
4. Upstream names and logos do not appear in shipped UI, package names or binaries. They
   appear only as credit in notices and About. Internal identifiers (file names, import
   paths, env var names such as `OMNIROUTE_*`, DB tables, code comments) keep upstream
   names, the same rule PRODUCT_ROLES.md applies to OpenVault's own wire ids.
5. Not shipped, removed or hard-disabled with a named HTTP 501:
   - pooling of consumer ChatGPT / Claude / Gemini (and similar) subscription OAuth
     accounts: `consumer_subscription_pooling_disabled`
   - cookie or session relaying of consumer web logins: `consumer_session_relay_disabled`
   - TLS or browser fingerprint impersonation ("stealth"): `tls_fingerprint_stealth_disabled`
   API-key provider routing stays.
6. Provider keys live only in OpenVault's KeyVault. The forks read them over loopback
   (`GET /api/keys`, `GET /api/keys/{id}/secret` with `X-OpenVault-Reveal: intentional`),
   cache plaintext in memory only, and fail with a named 503 when OpenVault is down. Any
   route that would save a provider key into a fork's own store returns 501
   `keys_managed_by_openvault`.

### What changed in OpenVault itself

- `POST /api/apikeys/verify` (admin token from a loopback or allowlisted peer, or a service
  Bearer listed in `OPENVAULT_VERIFY_SERVICES`; 60 calls a minute per caller; loopback
  alone is 401): a fork checks a client's bearer token here
  instead of keeping its own key table. Unknown and revoked return the same answer.
- `CLAUDE.md`: npm is allowed in `apps/router` and `apps/ship`.

### FreeRoute (`apps/router`)

- One classifier, `open-sse/netie/policy.ts`, reads the provider registry
  (`authType: "oauth"`, `authHeader: "cookie"`, `-web` executors) plus a short override
  table. Gates: `getExecutor()`, `/v1/chat/completions`, and a path table in
  `src/lib/netie/hardDisabledRoutes.ts` run from `src/proxy.ts` before authz.
- Disabled (counted over the 276 registry ids by `classifyProviderId`): 26 subscription
  providers (21 `authType: "oauth"`, qoder, and four reached through the override table)
  and their login and import routes (`/api/oauth/*`, `/api/codex/*`, `/api/cursor-cli/*`,
  CLI credential import); 33 web-session providers plus MITM, agent bridge, traffic
  inspector, VNC and session pools; four client-impersonation providers
  (cloudflare-playground, freebuff, cursor-api, muse-code); `packages/browser-pool`
  stubbed; `wreq-js` dependency removed.
- Also disabled because they couple FreeRoute to the upstream project:
  `upstream_updates_disabled` (version check, updater, changelog fetch) and
  `upstream_service_not_included` (the embedded 9router service).
- Keys: `src/lib/netie/keyvault.ts`, called from `materializeConnection()` in
  `src/sse/services/auth.ts`. Client tokens: `validateApiKey()` and
  `getApiKeyMetadata()` accept OpenVault-issued tokens (scope `self:usage`, never
  `manage`); router-side key minting returns `keys_managed_by_openvault`.
- Kept on purpose: the deploy-time env key (`OMNIROUTE_API_KEY`) as the operator's
  bootstrap credential, `X-OmniRoute-*` header names, the `/api/omniroute/status` path,
  and internal identifiers. Also kept, because clients and tool configs key on them:
  the MCP tool names `omniroute_*`, the tool-config provider id `omniroute` and its
  `omniroute/<model>` model ids, the default local key `sk_omniroute`, backup
  directories `omniroute-backup-*`, plugin names `omniroute-cmd-*`, the `~/.omniroute`
  data directory, the `omniroute-pwa-*` service-worker cache name, the Qdrant
  collection `omniroute_memory`, and the Radar feed wire headers `x-omniroute-feed-*`
  (used only against a feed URL an operator sets). The telemetry and control headers
  that were renamed (26 `X-FreeRoute-*` names, from `X-FreeRoute-Response-Cost` to
  `X-FreeRoute-Peer-Trace`) are sent together with their `X-OmniRoute-*` twin, same
  value, and every reader accepts either name (`src/shared/constants/headers.ts`).
  `netie/names.test.mjs` fails on any other upstream name in a shipped UI string, a
  header or marker sent to a third party, a public asset, or `.env.example` prose.
  Radar has no upstream default: `/api/radar/*` answers 501
  `upstream_service_not_included`, the feed URL and the contributor and supporter
  links default to nothing, and the OmniCopilot banner and the Kimi preset card are
  removed.
- Also off: the Dario and CLIProxyAPI sidecar families and `/api/version-manager`
  (`consumer_subscription_pooling_disabled`), Claude Code client impersonation and the
  Pollinations rotating fingerprint pool (`tls_fingerprint_stealth_disabled`). Aliases
  resolve to the canonical provider before classification. The local scrub runs
  `secure_delete` and `VACUUM`, and the nested `providerSpecificData` guard matches
  secret-looking words at any depth.
- Security review fixes (2026-10-30, after the first Security NO on #99):
  - A key is released by provider name only to that provider's own endpoint.
    `resolveProviderApiKey()` checks that the connection's base URL has the same scheme,
    host and port as an endpoint the provider registry lists for it, or no override at
    all. A connection aimed anywhere else gets no key, and OpenVault is not asked for
    the secret. A key an operator stored in OpenVault with a `base_url` still serves
    exactly that URL, which is how a gateway in front of a provider is set up.
  - The hard-disable table matches the decoded, dot-resolved, lower-cased path (up to
    three layers of `%` escapes, backslashes read as slashes), and a path whose encoding
    cannot be read answers 400 `invalid_path_encoding`. Next.js decodes before it routes,
    so `/api/%6fauth/...` used to reach the OAuth handler past a table that read the raw
    string.
  - `REQUIRE_API_KEY` defaults to true (flag definition, `.env.example`, both startup
    warnings). Before, an anonymous caller on the loopback port spent the vault's keys
    with no token. An operator can still set it to false.
  - Service keys (SkillsMP, CLIProxyAPI, Qdrant, Notion) are not stored locally: a write
    answers 501 `keys_managed_by_openvault` (an empty value still clears a stale one),
    SkillsMP and Notion read provider `skillsmp` and `notion` from the vault, and the
    scrub deletes copies an older build left. The keyless OpenCode free tier no longer
    gets a synthesized OpenCode client identity (User-Agent, `x-opencode-*`, forged
    ids); only headers a real OpenCode client sent are forwarded. Provider limits and
    registered-key routes need the management scope, not any valid token.
  - `.env.example` no longer ships `INITIAL_PASSWORD=CHANGEME`.
- Open: `DATA_DIR/db_backups` is not scrubbed, so a backup taken by an earlier build can
  still hold key bytes. The CLI does not run on Node below 22.22.2 (its own version
  gate); the live test drives the CLI through the same loader on older Node.
- Second review round (2026-11-05), FreeRoute:
  - Client emulation is off. The GLM Anthropic transport sends only `content-type`,
    `accept`, `x-api-key`, `anthropic-version` and an honest `anthropic-beta`. freebuff,
    cursor-api and muse-code answer `tls_fingerprint_stealth_disabled`, qoder answers
    `consumer_subscription_pooling_disabled`, and all four are hidden in the catalog.
    Command Code keeps only its documented API-key endpoint (the CLI fallback is gone),
    the Cursor CLI build id is no longer scraped from cursor.com, and the gitlawb
    providers send no OpenClaude CLI headers. `freeroute login antigravity` prints the
    named 501 text and exits 1. The Client Identity presets and the VibeProxy preset are
    removed, and `POST` and `PUT /api/provider-nodes` refuse a vendor CLI User-Agent,
    `X-Stainless-*` or `X-Claude-Code-*` header. The open-wa (WhatsApp Web) sidecar
    answers `consumer_session_relay_disabled`, the 9router sidecar is not supervised at
    boot. The dormant executor files (freebuff, cursor, qoder, muse) are still in the
    tree behind the gates.
  - More local credential stores are refused with 501 `keys_managed_by_openvault`: proxy
    usernames and passwords, the BigQuery service account and the Telegram bot token
    (both read from the vault, provider `bigquery` and `telegram`, when it holds one),
    the Obsidian token, and credential-looking values in provider-node custom headers
    (which are also no longer echoed back). The import scrub and `export-json` cover
    these stores and the Radar supporter key and Command Code key. The boot scrub
    removes an authenticated proxy login an older build saved.
  - `bin/` and the generated `server.env`: `server.env` is mode 0600, the CLI default
    port is 3020, a literal `CHANGEME` password is treated as unset, and shipped scripts
    print FreeRoute.
  - Authenticated proxies, the Obsidian integration and the BigQuery/Telegram dashboard
    forms can no longer save a credential from the UI. The Telegram webhook wizard still
    asks for the token and then gets the 501; the vault path works through the API.
- Open (FreeRoute): `bin/antigravity-bridge.mjs` and `src/mitm` are dormant (their routes
  answer 501) but still in the tree. Management is anonymous on loopback until
  onboarding completes. `OMNIROUTE_API_KEY` grants the management scope and skips
  OpenVault (the operator's bootstrap credential, kept on purpose). A custom User-Agent
  or header typed on a connection (not a node) is not checked for a vendor CLI identity.
  `proxy_subscriptions.control_secret_enc` and the Radar supporter-key write path are not
  gated yet. The login page still shows a "default password" hint.

### FreeBuild (`apps/ship`)

- Hard-disable: `hosted_cloud_disabled` (cloud/billing routes, cloud/tunnel
  migrations, cloud-mode branching). Removed from shipped UI and MCP: cloud/tunnel
  modal tabs, "Openship Cloud" prose (208 rewrites), cloud-only MCP descriptions.
- Keys: `packages/core/src/netie/keyvault.ts`, which, like the FreeRoute client, refuses
  an `OPENVAULT_URL` that is not on this machine and treats a redirect as a failure, so
  the admin token cannot be sent to another host. Provider credential choke point is
  `packages/platform/.../credential.service.ts:requireProvider()`, which gates
  creation/updates and returns `keys_managed_by_openvault` for
  `OPENVAULT_MANAGED_CREDENTIAL_PROVIDERS` (currently `cloudflare`).
  `resolveCredentialSecrets` and `listProviderCredentials` read live from KeyVault
  per credential label.
- Kept on purpose: env-var names (`OPENSHIP_*`), internal identifiers
  (`ensureOpenship*`, SQL `openship_` prefixes), `@repo/*` workspace paths. Mail
  hosting works (apps/email restored). Stores not moved: docker-registry auth,
  GitHub App identity, SSH keys (per-host file paths), SMTP relay creds, Stripe
  billing key (already disabled), user sessions, deployment env vars, and these
  third-party credentials, which FreeBuild keeps in its own database: S3 and SFTP
  backup credentials (`backup_destination`), git clone tokens (user and project),
  git source secrets, per-server GitHub tokens and deploy keys, the GitHub device
  token, SSH passwords, private keys and passphrases of servers, webhook secrets,
  and OAuth application client secrets. They are sealed with AES-256-GCM under a
  per-install secret: an operator-set `BETTER_AUTH_SECRET` wins, otherwise a random
  one is generated on first start into `~/.openship/auth-secret` (mode 0600;
  `OPENSHIP_DATA_DIR` or `OPENSHIP_AUTH_SECRET_FILE` move it). Anyone who can read
  both the database and that file can read these credentials, and losing the file
  makes them unreadable. Earlier builds sealed them under the public placeholder
  `change-me-in-production` when the variable was unset; those values still open
  and are re-sealed under the install secret on their next write. A tunnel token
  is no longer stored at all: saving one answers 501 `keys_managed_by_openvault`,
  the onboarding step has no token field, and a boot scrub clears an old copy.
  Moving the backup and clone credentials into OpenVault is a later decision for
  the founder.
- Kept: `X-Openship-Signature-256` is sent alongside its FreeBuild-named twin so
  existing webhook receivers keep verifying; receivers see both names, with the same
  HMAC (proven on the bytes on the wire).
- Export files are stamped `freebuild-instance-export` or `freebuild-project-export`.
  The importer still reads the `openship-*` names, so an Openship archive or an export
  from an earlier build imports (`test/modules/data-transfer-kind.test.ts`). The
  dashboard-to-API headers `x-openship-chunk-sha256` and `x-openship-transfer-signature`
  stay: they never leave a FreeBuild install.
- Install: `npm ci` needs no bun. The mail app's postinstall used to run `bun install`
  and failed with exit 127 where bun is absent (the founder's volume cannot install bun).
  `apps/email/scripts/postinstall.mjs` runs it only when bun exists. bun is still needed
  to develop or test `apps/email` (its tests run under bun); the mail image installs its
  own dependencies in its build stage. `npm run build` builds only the API and dashboard.
- Runtime independence (2026-11-05): FreeBuild makes no request to the upstream
  project's servers or repositories in its default configuration. The app catalog is the
  bundled one; an operator can add an overlay with `FREEBUILD_CATALOG_URL` (https or
  loopback only, upstream hosts refused, no redirects). The Update Center and Settings
  Updates have no release feed (`GITHUB_REPO` is empty). `GET /api/billing/plans`
  answers 501 `hosted_cloud_disabled`. The GeoIP database, the native-modules catalog
  and the release dist come from operator-supplied sources only
  (`OPENSHIP_GEOIP_URL`, `OPENSHIP_MODULE_CATALOG_BASE_URL`, `OPENSHIP_RELEASE_DIST_PATH`;
  upstream URLs refused). Free project domains exist only under `HOST_DOMAIN`. The DNS
  ownership TXT record is `_freebuild-challenge`, and the old name is still accepted.
  Default image names are `localhost/freebuild/freebuild-*`; nothing is pulled from
  `ghcr.io/oblien`. Container and compose-project names, and the backup key prefix,
  keep `openship-*` so an existing install keeps working.
- Upgrade note (install secret): the first start after this change replaces the public
  placeholder `BETTER_AUTH_SECRET` with a generated one. Stored secrets sealed under the
  placeholder still open (the database client, the platform cipher and the backup
  adapters all fall back to it) and are re-sealed on their next write. Values derived
  from the secret change: sessions end, the DNS ownership token is re-derived (a domain
  verified before must be verified again if the record is checked again), and build-arg
  fingerprints differ once. The compose file requires `BETTER_AUTH_SECRET`, so a
  container cannot regenerate it on recreate.
- Open: the Docker images do not build. The api and dashboard Dockerfiles are the
  upstream project's and `COPY bun.lock` and `pnpm-workspace.yaml`, which this fork does
  not have (it uses npm workspaces and `package-lock.json`); the edge Dockerfile copies
  the GeoLite2 database, which is not shipped. `docker/docker-compose.yml` validates but
  `up --build` fails until the Dockerfiles are ported. Run FreeBuild with `npm ci &&
  npm run build` until then. No image is published.
- Open: the webmail (`apps/email`) is the Zero mail client by Mail-0, rebranded to
  FreeBuild Mail. It is credited in `apps/ship/NOTICE`, but its licence text is not in the
  Openship snapshot and was not confirmed. Check it before the webmail ships.
- Open: the host-channel advisory says "Re-run `freebuild up`", but this fork ships no CLI
  (`apps/cli` was not imported), so a host channel cannot be provisioned from FreeBuild.
  Restore a provisioning path or reword the advisory to say host-level operations are
  unavailable in this edition. The founder decides.
- Open: the app catalog's `mindwire` entries link to oblien repositories and images. They
  name a deployable third-party app, not FreeBuild; left as a catalog decision.
- Not run here: the Docker-driven mail suites (`apps/api` `test:e2e`), because no Docker
  daemon is reachable on the build machine.

## Seams

Two FreeRoutes run on one box. They do not compete for keys.

| | Python FreeRoute (`OpenMW`, `:5000`) | Node FreeRoute (`apps/router`, `:3020`) |
|---|---|---|
| Role | Custody gateway and the metered seat: issues `ov_` keys, meters usage, packs, parks, quota-aware health | Client-facing OpenAI-compatible router: many providers, MCP, A2A, combos, IDE tool configs |
| Keys | Owns the vault | Reads pooled keys over loopback with the admin token; never stores one (`keys_managed_by_openvault` on every write path) |
| Clients | Cortex, DMS, AirGPT through `CORTEX_FREEROUTE_TOKEN` | Cursor, Claude Code, Codex and other tools through an `ov_` token OpenVault issued |
| Client auth | `HttpGuard` | `POST /api/apikeys/verify` on OpenVault (admin or allowlisted service Bearer, 60 verify calls a minute per caller); the router caches a verdict 60 s per token and fails closed on 429 or any error |
| Bind | `127.0.0.1` only (HUMAN_STOP on `:5000`) | `127.0.0.1` by default; `HOST` or `OMNIROUTE_HOSTNAME` overrides |
| Usage ledger | `usage_events` in `keys.db` | Its own SQLite; not yet written back to OpenVault's ledger. Open seam. |

FreeBuild (`apps/ship`, `:3030` API, `:3031` dashboard) and the Python `ship/` hosts
overlap the same way: the Python `ship/` is CORE (founder 2026-10-06) and stays; FreeBuild
reads its Cloudflare token from the vault and keeps nothing local. Which one the console
calls for a deploy is not decided here. Open seam.

OpenVault itself, not a fork: `ChatProbeLoop` (`vault/chat_probe.py`) probes every enabled
OpenAI-compatible key, `tenant` custody included, and sends that key's secret to its own
`base_url`. That is a health check of the key against its own provider, not a spend by a
metered caller, so it does not break the pooled-only rule. Worth a look on the OpenVault
side if tenant keys ever point at hosts their uploader does not control.

### Parking-lot unlock

`PARKING_LOT.md` parked these trees on 2026-10-06 with the unlock "the founder re-asks
with a plan that keeps KeyVault as the only key store and does not bind `:5000` on a
public interface". The founder re-asked on 2026-10-22. The plan is this record: every
fork path that would persist a provider key answers 501 `keys_managed_by_openvault`
(proven live by `apps/router/netie/e2e-openvault.mjs` and
`apps/ship/netie/e2e-openvault.mjs`, which also grep the forks' data directories for the
vault secret), and neither fork binds `:5000`; OpenVault's own bind rule is unchanged.
A second credential store is the one thing DR-0003 feared, and it is the one thing the
forks are built not to have.

## Consequences

- Good: two working products instead of two partial rebuilds.
- Bad: two large Node trees to keep building, and an upstream to track by hand.
- Bad: two FreeRoutes and two ship engines. The seams above name what is still open.
- Verify rate: OpenVault admits 60 verify calls a minute per caller. With the router's
  60 s cache that is 60 distinct client tokens a minute before a legitimate client sees
  a transient 401. Raise the tier before a team passes that. Garbage tokens count against
  the same budget, so a caller sending random bearers can use it up and make real
  clients see that transient 401 (the router fails closed and logs
  `openvault_verify_rate_limited`).
- Revocation lag: the router caches a "valid" verdict for 60 s per token, so a token
  revoked in OpenVault can still be accepted for up to a minute.

## Confirmation

- `OpenMW/tests/test_apikeys_verify.py` (verify route: admin or service Bearer, never loopback alone)
- `apps/router/netie/keyvault.test.mjs` (admin token, named 503s, pooled-only, no custom-key borrowing, key released only to the provider's own endpoint, loopback-only `OPENVAULT_URL`, no redirects, service keys refused and scrubbed, `REQUIRE_API_KEY` default)
- `apps/router/open-sse/netie/__tests__/policy.test.mjs` (route table incl. escaped paths and 400 `invalid_path_encoding`, no synthesized OpenCode identity, no vendor CLI headers, disabled CLI-emulation providers and sidecars)
- `apps/router/netie/names.test.mjs` and `apps/router/netie/bootstrap.test.mjs` (no upstream name in shipped strings, assets and third-party traffic; `server.env` mode, ports, `CHANGEME`)
- `apps/router/netie/smoke.test.mjs` and `apps/ship/netie/smoke.test.mjs` (starts, named 501s)
- `apps/router/netie/e2e-openvault.mjs` and `apps/ship/netie/e2e-openvault.mjs` (real OpenVault, vault key reaches the provider, secret absent from the fork's disk)
- `apps/ship/netie/notice-check.mjs --check` (Apache change notices and NOTICE)
