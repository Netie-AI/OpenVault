# OpenVault — Status

> Canonical "what's true right now." History: [`CHANGELOG.md`](CHANGELOG.md). Deferred:
> [`PARKING_LOT.md`](PARKING_LOT.md). Map: [`docs/ACTIVE.md`](docs/ACTIVE.md).

Last reconciled: 2026-08-19 (**HUMAN_STOP** still in force. The two unrouted founder-ask waves
are now committed and retro-routed under #33. #34 and #35 verified and closed; every remaining
open item is a human gate or a founder decision, so no agent-closeable ticket is left).

**UI:** `:3010` / `:5000`. Scripted demo:
`cd OpenMW && uv run --no-sync python scripts/one_seat_demo.py`

## Distance

~70%. Epics #13–#17 + FreeRoute/FreeBuild capability closed. Demo #18 open until HT1–HT5.

FreeBuild has four real hosts: CF Pages, Coolify, Netlify, and the user's own VPS (Docker + Caddy,
replicas, TLS). No live box has run it — that is HT1. FreeRoute can now tell **who** called and
**what it cost**: issued `ov_` keys, a usage row per request naming the hop that served, an output
ceiling, prompt-cache affinity. Metering is in; **pricing is not** (`summary.priced` is `false`).

## Security note (2026-08-07)

Three holes closed, all reachable by anyone who could talk to `:5000`:
`x-openfree-tier` selected the unmetered `local` tier (6000 rpm / 6M tpm) on our vaulted keys;
`/api/apikeys` would mint a credential for any caller; `/api/ship/engine` ran real host adapters
with no leave gate. If `:5000` was ever exposed beyond loopback, treat it as having been open.
**Still open:** the Next console proxies `/ov-api/*` to loopback, so `_require_loopback` passes for
remote callers on any prefix missing from `LOCAL_ONLY_API_PREFIXES` — and that list omits
`/api/keys`, `/api/secrets`, `/api/vault/`. Not this wave's bug; needs its own ticket.

## HUMAN_STOP

Auto-orch loop **stopped**. Founder clears https://github.com/Netie-AI/OpenVault/issues/18 :

1. HT1 Live CF/Coolify/Netlify/VPS URL under gate
2. HT2 Live FreeRoute + real vaulted keys
3. HT3 Unseal/lock UX on `:3010`
4. HT4 Phase 0 Cortex smoke via OpenVault
5. HT5 Secrets-at-ship inject; no plaintext leak

## Next

| # | Status |
|---|--------|
| #18 DEMO epic | Awaits your HT1–HT5 |
| #33 EPIC host+meter | Open — children #34/#35 closed; blocked on #36 and on #18 HT1 |
| #34 console proxy | **CLOSED** 2026-08-19 — verified, gate mutation-checked |
| #35 detect→build→ship | **CLOSED** 2026-08-19 — verified, gate mutation-checked |
| #36 per-tenant custody | **NEEDS-YOU.** Whose key does a metered caller spend — ours or theirs? |
| **Skills library** | **NEEDS-YOU.** A skill store here is a **PRODUCT_ROLES amendment** across four repos, not a ticket |
| Pricing | Deferred by you 2026-08-07. Metering is in; no rate exists anywhere in code |
| Serving-engine selection | OpenVault owns it per PRODUCT_ROLES. Not moved — cross-repo |
| Multi-node LB / Route53 | Not built. One box, one Caddy — see PARKING_LOT |

## Clone-and-verify

```bash
cd OpenMW && uv run pytest tests/ -q
```
