---
status: proposed
date: 2026-09-26
decision-makers: founder
---

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

- `POST /api/apikeys/verify` (loopback only): a fork checks a client's bearer token here
  instead of keeping its own key table. Unknown and revoked return the same answer.
- `CLAUDE.md`: npm is allowed in `apps/router` and `apps/ship`.

### FreeRoute (`apps/router`)

- One classifier, `open-sse/netie/policy.ts`, reads the provider registry
  (`authType: "oauth"`, `authHeader: "cookie"`, `-web` executors) plus a short override
  table. Gates: `getExecutor()`, `/v1/chat/completions`, and a path table in
  `src/lib/netie/hardDisabledRoutes.ts` run from `src/proxy.ts` before authz.
- Disabled: 21 subscription-OAuth providers and their login and import routes
  (`/api/oauth/*`, `/api/codex/*`, `/api/cursor-cli/*`, CLI credential import); 32
  web-session providers plus MITM, agent bridge, traffic inspector, VNC and session
  pools; `packages/browser-pool` stubbed; `wreq-js` dependency removed.
- Also disabled because they couple FreeRoute to the upstream project:
  `upstream_updates_disabled` (version check, updater, changelog fetch) and
  `upstream_service_not_included` (the embedded 9router service).
- Keys: `src/lib/netie/keyvault.ts`, called from `materializeConnection()` in
  `src/sse/services/auth.ts`. Client tokens: `validateApiKey()` and
  `getApiKeyMetadata()` accept OpenVault-issued tokens (scope `self:usage`, never
  `manage`); router-side key minting returns `keys_managed_by_openvault`.
- Kept on purpose: the deploy-time env key (`OMNIROUTE_API_KEY`) as the operator's
  bootstrap credential, `X-OmniRoute-*` header names, the `/api/omniroute/status` path,
  and internal identifiers.
- Also off: the Dario and CLIProxyAPI sidecar families and `/api/version-manager`
  (`consumer_subscription_pooling_disabled`), Claude Code client impersonation and the
  Pollinations rotating fingerprint pool (`tls_fingerprint_stealth_disabled`). Aliases
  resolve to the canonical provider before classification. The local scrub runs
  `secure_delete` and `VACUUM`, and the nested `providerSpecificData` guard matches
  secret-looking words at any depth.
- Open: `DATA_DIR/db_backups` is not scrubbed, so a backup taken by an earlier build can
  still hold key bytes. The CLI does not run on Node below 22.22.2 (its own version
  gate); the live test drives the CLI through the same loader on older Node.

### FreeBuild (`apps/ship`)

- Hard-disable: `hosted_cloud_disabled` (cloud/billing routes, cloud/tunnel
  migrations, cloud-mode branching). Removed from shipped UI and MCP: cloud/tunnel
  modal tabs, "Openship Cloud" prose (208 rewrites), cloud-only MCP descriptions.
- Keys: `packages/core/src/netie/keyvault.ts`. Provider credential choke point is
  `packages/platform/.../credential.service.ts:requireProvider()`, which gates
  creation/updates and returns `keys_managed_by_openvault` for
  `OPENVAULT_MANAGED_CREDENTIAL_PROVIDERS` (currently `cloudflare`).
  `resolveCredentialSecrets` and `listProviderCredentials` read live from KeyVault
  per credential label.
- Kept on purpose: env-var names (`OPENSHIP_*`), internal identifiers
  (`ensureOpenship*`, SQL `openship_` prefixes), `@repo/*` workspace paths. Mail
  hosting works (apps/email restored). Stores not moved: docker-registry auth,
  GitHub App identity, SSH keys (per-host file paths), SMTP relay creds, Stripe
  billing key (already disabled), user sessions, deployment env vars.
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
  a transient 401. Raise the tier before a team passes that.

## Confirmation

- `OpenMW/tests/test_apikeys_verify.py` (verify route: admin or service Bearer, never loopback alone)
- `apps/router/netie/keyvault.test.mjs` (admin token, named 503s, pooled-only, no custom-key borrowing)
- `apps/router/netie/smoke.test.mjs` and `apps/ship/netie/smoke.test.mjs` (starts, named 501s)
- `apps/router/netie/e2e-openvault.mjs` and `apps/ship/netie/e2e-openvault.mjs` (real OpenVault, vault key reaches the provider, secret absent from the fork's disk)
- `apps/ship/netie/notice-check.mjs --check` (Apache change notices and NOTICE)
