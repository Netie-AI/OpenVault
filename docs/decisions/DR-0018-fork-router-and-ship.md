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

<!-- FILL: per-app specifics after agent reports -->

## Consequences

- Good: two working products instead of two partial rebuilds.
- Bad: two large Node trees to keep building, and an upstream to track by hand.
- Bad: the Python FreeRoute (`/v1/chat/completions` on :5000) and the Node FreeRoute
  overlap. See "Seams".

## Confirmation

- `OpenMW/tests/test_apikeys_verify.py` (verify route, loopback gate)
- `apps/router/netie/smoke.test.mjs` (starts, named 501s)
- `apps/ship/netie/smoke.test.mjs` (starts, named 501s)
