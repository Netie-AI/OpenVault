# Claude decisions — review verdicts, feature matrix, and design calls

Companion to [`AGENT_SPLIT.md`](AGENT_SPLIT.md). Everything here is a decision
or a design. No code changes accompany this document.

---

## 1. Review verdicts

### 1.1 Secret-reveal gate — **ACCEPT**

Loopback check + `X-OpenVault-Reveal: intentional` + per-reveal audit line.

Evidence: 428 without the header, 428 with a wrong value, 200 from loopback
with it, 403 from `192.168.1.50` with it. Audit line written, and a test
asserts the plaintext never appears in the audit file. Four behaviours pinned
in `tests/test_secret_reveal_gate.py`.

Why the header matters and is not theatre: a custom request header cannot be
attached cross-origin without a preflight the attacker must survive, so a page
the user merely has open cannot walk the key list and exfiltrate secrets. That
is the actual threat model for a localhost service with no auth.

**Caveat, stated so it is not mistaken for solved:** this reduces *remote and
drive-by* access. It does nothing about a malicious process running as the
user — that process reads `master.key` and `keys.db` directly and never
touches the API. That is §5.

**Move to "done" in the split. Cursor's only follow-up is V1**: confirm the
header is sent on reveal and that nothing calls `/secret` on mount.

### 1.2 LIVE honesty (`profile_source`) — **ACCEPT**

Evidence: on a machine with an AMD 780M and a Samsung NVMe, the payload
previously read `demo_mode: false` while serving `GeForce RTX 4050` /
`Micron 3400`. It now reports `AMD Radeon 780M Graphics` /
`SAMSUNG MZAL8512HFLU-00BLL` with `profile_source: "live"`.

The root cause was worth more than the fix: `demo_mode` was derived from
*which branch the caller asked for*, not from *whether fabricated data was
served*. Those are different questions the moment live detection returns
empty. `profile_source` answers the second one, which is the only one a badge
should ever render.

Three supporting changes, all accepted:

- **AMD/Intel via `Win32_VideoController`.** `_probe_amd_gpu` only ever tried
  `rocm-smi`, which is never present on Windows, so every non-NVIDIA machine
  reported *no GPU*. Correct fix, right layer.
- **NVMe identity via `Get-PhysicalDisk`.** Same shape of fix.
- **Inventory timeout 5s → 20s.** This was the highest-value line in the
  session. Enumeration takes 8.3s on this machine; at 5s it returned **zero
  devices**, which downstream read as "no NVMe exists", which triggered the
  fabricated-profile substitution. One constant, three symptoms. The new
  constant is separate from `_HARDWARE_PROBE_TIMEOUT_S` so genuinely-fast
  probes keep a tight budget, and it is cached per boot behind a 10s TTL.

**Accepted with one condition:** none of this is visible until **C10** removes
`--mock-health` from both Windows launchers. Until then the shipped stack
still runs in demo mode and every honesty fix above is unobservable. C10 is
correctly ranked #1.

### 1.3 Vault UI vs vendor Key Vault UX — gaps only

The paste → infer → precheck → harvest flow is **better than both vendors** on
the add path. OmniRoute's `AddApiKeyModal` still asks for provider, key and
label separately; ours derives all three from the paste. Do not redesign it.

Real gaps, in value order:

| Gap | Vendor reference | Note |
|---|---|---|
| **Per-key usage + spend** | OmniRoute `costs`, `usage`, `tokens`, `quota` | The rate limiter already measures tokens; nothing surfaces them per key. Biggest missing thing. |
| **Provider health history** | OmniRoute `health`, `provider-stats` | We show the *last* precheck. A sparkline of the last N would make a flaky key obvious. |
| **Coverage prompt** | OmniRoute `free-tiers`, `free-provider-rankings` | `/api/providers/coverage` exists and is unused. "You have no free fallback — add one" with a register link is a one-component win. |
| **Rotate in the UI** | — | `rotateKey` is in the client, unused by the page. Rotation is manual-only and nothing warns on key age. |
| **Bulk select** | OmniRoute `api-manager` | Only worth it past ~20 keys. Defer. |

Not gaps, deliberately: drag-between-groups (no bulk reorder endpoint — it
would silently revert on reload; the role dropdown is one click and honest),
and the key-detail sub-page (a modal is enough at this scale).

---

## 2. Feature matrix — OmniRoute (51 routes) + FreeBuild (15)

Verdict key: **Have** = we already do it · **Port** = mechanical, Cursor card ·
**Design** = Claude first · **Skip** = with reason.

### OmniRoute

| Feature | Verdict | Note |
|---|---|---|
| `providers`, `media-providers`, `endpoint`, `api-endpoints` | **Have** | `/api/providers/*` + vault. Catalog is 16 vs their ~290 — breadth is data, not code. |
| `free-tiers`, `free-provider-rankings` | **Port** | Backend exists (`/api/providers/free`, `/coverage`). This is also the **ad-ranking surface** — see §6. |
| `api-manager`, `limits`, `quota`, `tokens` | **Have** (partial) | `vault/ratelimit.py` is genuinely good — dual refilling buckets, reserve-then-refund against real `usage`. Not surfaced in UI. |
| `costs`, `usage`, `analytics`, `provider-stats` | **Design** | §7. Must use real provider billing APIs, never the operator-typed `spent_usd_estimate`. |
| `combos`, `auto-combo` | **Design** | `combo.ts` is 3629 LOC and recon called it "not extractable as a unit". Port ~8 of 18 strategies; the rest need their quota telemetry and `localDb`. |
| `compression`, `context`, `memory` | **Design** | Genuinely valuable and genuinely hard. Highest-value *engine* port after the cascade. |
| `health`, `chaos` | **Port** (health) / **Skip** (chaos) | Health = precheck history. Chaos engineering is a scale problem we do not have. |
| `cache` | **Design** | Response caching needs a correctness story (what is safe to cache for an LLM call) before any code. |
| `relay` | **Have** | Account custody already allocates relay addresses. |
| `playground` | **Port** | Cheap, high perceived value: one page against `/v1/chat/completions`. |
| `webhooks` | **Skip for now** | Needs a public callback URL → conflicts with no-servers (§4). |
| `mcp`, `tools`, `search-tools`, `plugins`, `agent-skills`, `omni-skills` | **Skip** | Whole adjacent product. Not the vault/ship story. |
| `a2a`, `acp-agents`, `cli-agents`, `cloud-agents`, `cli-code`, `runtime` | **Skip** | Agent orchestration — Cortex's job, not OpenVault's. |
| `gamification`, `leaderboard` | **Skip** | Engagement mechanics for a multi-tenant SaaS. We are single-user local. |
| `discovery`, `translator`, `batch`, `onboarding`, `profile`, `settings`, `system`, `logs`, `audit`, `activity`, `changelog`, `components` | **Skip / Have** | Housekeeping; adopt individually if a need appears. |

### FreeBuild

| Feature | Verdict | Note |
|---|---|---|
| `library`, `projects`, `apps` | **Have** | `/api/ship/library` + inspect + detect. |
| `(deployment)`, `deployments` | **Have** (real for Pages) | Now genuinely deploys via `ship/hosts/cloudflare_pages.py`. |
| `domains` | **Have** (partial) | `attach_domain` works; external-registrar records returned to paste. |
| `servers` | **Skip** | Their model is "your VPS fleet". Ours is BYOC-serverless. Revisit only with the ECS adapter. |
| `billing` | **Design** | §7. |
| `monitoring` | **Port** (thin) | Uptime ping on the deployed URL is cheap and real. Full APM is not. |
| `jobs` | **Design** | Needs a scheduler that survives the app being closed → Windows Task Scheduler, not a daemon. |
| `backups` | **Port** | Vault export with re-encryption. Pairs with §5. |
| `emails` | **Skip** | Their transactional-email product. Unrelated. |
| `members`, `audit` | **Skip** (members) | Single-user. Audit we already have for secrets and control actions. |
| **Stack detection table** | **Have** | Ported: 45 of 46 stacks (C8 adds `webmail`). |
| **SSE build-log contract** | **Have** | Frame shape copied; `lib/sse` implements it. |
| **Build executor** | **Never** | `@repo/adapters` + a proprietary `oblien` package not in the repo. Cannot be ported at any effort. |

**Two highest-value ports, if only two happen:** provider *health history*, and
*per-key usage/cost*. Both turn the vault from a store into a thing you check.

---

## 3. Middleware Gain — **KILL the render**

Not "fix later". Delete the UI surface now.

`_middleware_comparison()` computes `idle_reduction = clamp(gpu_idle/100 ×
0.62, …)` then `speed_factor = 1 + idle_reduction × 1.35`, against a
`gpu_idle` that is itself the constant 27.9. The result is **always ≈ +23.4%**,
on every machine. `baseline_tok_s` comes from the midpoint of a hardcoded
per-tier table — **no token was ever generated or timed**.

No sensor can produce this number. It requires an A/B harness: the same prompt
through the baseline and optimised paths, tokens from the response `usage`,
wall time from `perf_counter()`. Until that exists, any value shown is
invented.

A constant that looks like telemetry is worse than a blank space, because a
blank space does not make a claim. **Cursor: remove the Middleware Gain tile.**
When the A/B harness lands it comes back with real numbers.

Same reasoning, different verdict, for **Bottleneck**: keep it, because the
SSD-side hops become real the moment §6/SMART lands — but the GPU-side hops
(PCIe 5.0ms, RAM→VRAM 30.0ms, GPU 100.0ms) are hardcoded and must carry a
**per-hop** synthetic marker (C5). A response-level badge is not enough once
half the hops are real.

---

## 4. "OAuth at our server" vs no-servers — **RESOLVED**

The stated goal was "auto directly integrate and oauth at our server". That
contradicts the model in [`SHIPPING_MODEL.md`](SHIPPING_MODEL.md). Resolution:

> **We run no server in any credential path. OAuth happens on the user's
> machine, via loopback redirect with PKCE, falling back to device flow.**

### Why a server is not needed

The only reason desktop apps historically needed a server is the OAuth
**client secret**. That reason is gone:

- **RFC 8252 (OAuth for Native Apps)** specifies the loopback redirect: the app
  opens a temporary listener on `127.0.0.1:<random>`, sends the user to the
  provider in their browser, and receives the code on that listener.
- **PKCE (RFC 7636)** removes the need for a client secret entirely. The client
  id is public by design.

This is exactly how `gh auth login`, `aws sso login`, `az login` and `wrangler
login` already work. It is the well-trodden path, not a workaround.

### Why a server is actively harmful here

A distributed desktop app **cannot keep a client secret secret** — it ships in
the binary. So a relay would exist only to hold that secret, and in doing so:

- becomes the single breach target the local-first model was designed to avoid;
- sees every user's authorization codes in transit;
- costs money per user with no revenue attached;
- creates a dependency that outlives us — if the relay dies, every user's
  integrations break, including for people who stopped using the app.

### The decision ladder

| Rung | When | Example |
|---|---|---|
| 1. **Loopback + PKCE** | Provider supports a public client with `http://127.0.0.1` redirect | GitHub, Google, most modern APIs |
| 2. **Device authorization grant** (RFC 8628) | No loopback allowed, or headless/SSH | GitHub, Azure, AWS SSO |
| 3. **Paste a scoped token** | Provider has no public-client OAuth | **Cloudflare** — API tokens only, which is why the Pages adapter takes a token |
| 4. **Never** | Provider requires a confidential client | Say so plainly; do not build a relay to work around it |

Rung 3 is not a failure state. A scoped Cloudflare token is arguably *better*
than OAuth: the user picks the exact permission (`Pages: Edit`), it is
revocable independently, and it never grants account-wide access.

### The one legitimate server, and what it may hold

Provider catalog updates and ranking metadata (§6) need a fetch. That payload
is **public, non-credential, non-PII** — provider names, endpoints, free-tier
notes, ranking. A static JSON on a CDN, or a GitHub Pages file, is sufficient.
No OAuth, no database, no user identity, no logs.

**The invariant to hold:** if a thing we host ever needs to *authenticate a
user*, we have left the model. Fetching a public file is not authentication.

---

## 5. `master.key` — envelope redesign

The gap the reveal gate does not close. Today `Fernet.generate_key()` writes a
raw key to `~/.openvault/master.key` in plaintext beside `keys.db`; the
`chmod(0o600)` is a no-op on NTFS. Any process running as the user recovers
every secret without touching the API.

**Design:**

```
passphrase ──Argon2id(m=64MiB,t=3,p=1,salt)──> KEK
                                               │ wraps
                              per-key DEK ─────┘   (AES-256-GCM)
                                    │ encrypts
                                    └──> secret ciphertext in keys.db
```

Decisions:

1. **Envelope, not direct encryption.** A per-key DEK wrapped by the KEK means
   changing the passphrase rewraps N small DEKs instead of re-encrypting every
   secret, and a compromised single DEK does not expose the rest.
2. **Versioned header on every ciphertext** (`v1:alg:kdf-params:nonce:ct`).
   Fernet today is AES-128-CBC with no version field, so there is no migration
   path to AES-256-GCM. Add the header before it is needed, not after.
3. **Machine-bound fallback for the no-passphrase case.** Wrap the KEK with
   DPAPI (`CryptProtectData`, user scope) so a copied `keys.db` + `master.key`
   is useless on another machine. This is strictly better than today at zero
   UX cost, and should ship **first** as it needs no user-facing change.
4. **Unseal state.** The vault is currently open forever once the process
   starts. Add an idle re-lock and require re-auth to reveal. Zeroise decrypted
   material on lock.
5. **Migration must be automatic and reversible.** Detect v0 (raw Fernet) on
   open, re-encrypt to v1 in a transaction, keep a `.bak` until the next clean
   start. Losing `master.key` today is unrecoverable; the migration must not
   add a second way to lose everything.
6. **Move the GitHub PAT into the vault** (`github_auth.py:153` writes plaintext
   JSON) — it is the one credential currently bypassing all of this.

**Ship order:** DPAPI wrap (invisible, big win) → versioned envelope →
passphrase/Argon2id → unseal state. Cursor gets a card only after 1–3 are
signed off; this is not a mechanical change.

---

## 6. No-admin SMART IOCTL — **keep, with a verification rule**

Confirmed still the single blocker for a live Bottleneck: Sentinel now resolves
the real device (`\\.\PhysicalDrive0`) and correctly reports `needs_admin`
rather than faking, which is the right failure — but it is still a failure.

The route (Win10 1903+):

```
DeviceIoControl(IOCTL_STORAGE_QUERY_PROPERTY)
  STORAGE_PROPERTY_QUERY.PropertyId = StorageDeviceProtocolSpecificProperty
  STORAGE_PROTOCOL_SPECIFIC_DATA { ProtocolType = ProtocolTypeNvme,
                                   DataType     = NVMeDataTypeLogPage,
                                   RequestValue = 0x02 }
```
on a handle opened with `dwDesiredAccess = 0`.

**Non-negotiable verification rule, because this is the failure mode that
matters:** a wrong struct offset does not raise — it returns *plausible
garbage*. Every field must be cross-checked against a known-good source before
the adapter is trusted:

- `percentage_used` ∈ 0..255 and consistent with `MSFT_StorageReliabilityCounter.Wear`
- `composite_temperature` is in **Kelvin** — sanity-bound to 273..373
- `data_units_written` × 512 × 1000 must be monotonically increasing across two
  reads minutes apart
- cross-check model/serial against `Get-PhysicalDisk` for the same device

If any check fails, the adapter must decline rather than emit numbers. Ship
behind a capability probe that reports which rung bound.

---

## 7. Bill visualisation + ECS adapter

### Bill viz — contract first

The rule: **every figure shown must come from a provider billing API, and be
labelled with its as-of timestamp.** Today `spent_usd_estimate` is typed by the
operator and fed by nothing; rendering it in a chart would repeat the
Middleware Gain mistake in a costlier place.

| Provider | Real source | Notes |
|---|---|---|
| Cloudflare | GraphQL Analytics API | Pages free tier: usually $0. Show "free tier" not "$0.00 estimated". |
| AWS | Cost Explorer `GetCostAndUsage` | ~24h lag, and Cost Explorer itself bills per request. Cache. |
| Anthropic / OpenAI | usage endpoints where offered | Otherwise derive from our own token counts and **label it as our estimate**. |

Three display rules:

1. Never mix measured and estimated in one number. Two series, two labels.
2. Show the as-of time next to every figure. A 24h-stale AWS number presented
   as current is a lie with a delay.
3. A "suggestion" may only compare **published list prices** against **measured
   usage**. It may not predict savings — that depends on workload shape we
   cannot see.

### ECS adapter — design, deferred build

Offer ECS only when the detected stack **cannot** run on Pages: a long-running
server process, a non-HTTP listener, or a workload needing persistent local
state. Static/SSG/edge-compatible → Pages, always.

Resource graph the adapter must own (and tear down cleanly):

```
ECR repo ── image ──> ECS task definition ──> ECS service
                                                │
                                    ALB ── target group ── listener
                                     │
                          ACM cert ── Route 53 / external CNAME
```

Cost honesty is the hard requirement, not the plumbing: **a bare ALB is roughly
$16–18/month before a single request.** The adapter must show that figure and
require explicit confirmation before creating one, because a user arriving from
"free Cloudflare Pages" will not expect it. Offer Fargate Spot and scale-to-zero
where the workload allows.

**Do not build until:** (a) a real user has a workload Pages cannot host, and
(b) the `preflight()`-before-build pattern (S1) is proven on Pages. Building a
20-resource adapter for a hypothetical is exactly the effort sink the split
exists to prevent.

---

## 8. Paid target ranking — policy before code

OmniRoute already ships `free-provider-rankings`, so the surface is proven.
Ours must not become an ad unit wearing a recommendation's clothes.

**Rules:**

1. **Two lists, never merged.** Organic ranking (measured latency, uptime,
   free-tier limits) and paid placements are separate sections with separate
   headings. A paid slot never sorts into organic results.
2. **Label on the item, not in a footer.** "Sponsored" on the card itself.
   A disclosure the user must scroll to find is not a disclosure.
3. **Paid placement may never alter organic order.** Not weighting, not
   tie-breaking, not "featured within results".
4. **No claim we cannot verify.** A sponsor may supply a name, logo, and their
   own published pricing. They may not supply "fastest" or "most reliable" —
   those are our measurements or they do not appear.
5. **Auto-select never picks a sponsor.** S2 preselects a target from the
   detected stack. If a sponsored option were ever preselected, "we chose this
   for you" becomes a paid recommendation. Hard line.
6. **Disclose the model once, plainly**, in Settings: how we make money, and
   that ranking is not for sale.

Rule 5 is the one that will be under pressure, because auto-select is the most
valuable placement in the product. It is also the one that would destroy trust
fastest. Write it into the picker's tests, not just the docs.

---

## 9. What I am NOT deciding yet

- **Context/memory compression port.** Needs a read of OmniRoute's actual
  implementation, not just the route name. Highest-value engine port after the
  cascade; deserves its own pass.
- **Which 8 of 18 cascade strategies.** Requires reading `combo.ts` properly.
- **Jobs/scheduler.** Blocked on deciding whether OpenVault may install a
  Windows Task Scheduler entry — that is a system-settings change and needs the
  user's explicit call.
