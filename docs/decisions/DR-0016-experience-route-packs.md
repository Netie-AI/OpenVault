---
status: accepted
date: 2026-09-04
decision-makers: founder
---

# DR-0016 - Experience route packs ($10 / $30 / $100 / $500)

Renumbered from a draft DR-0013 on 2026-09-07: `main` already shipped
`DR-0013-control-plane-locked-rates.md`. IDs are never reused.

## Context and Problem Statement

STATUS "Pricing NEEDS-YOU" and PRD F14 waited on a founder pick. Hosting SKUs
in `ship/service.py` ($24 / $79 / $9) are a different product (we wrap a box).
The founder asked for prepaid **experience** packs so someone can start the
ecosystem at about $10, get mixed cheap/free hops from our pooled keys
(DR-0009 a), and when that runs out be guided to free register, BYOK, or a
larger pack -- not a 1% skim invoice.

## Decision Outcome

- Packs: `starter` $10 (`api_credit_usd` $8), `plus` $30 ($24), `pro` $100
  ($80), `studio` $500 ($400). 80% of the sticker is mixed-hop credit.
- Credit is estimated from the usage ledger (`billable_tokens`) times an
  operator blend rate (`OPENVAULT_BLEND_USD_PER_1M`, default 0.20), labeled
  estimated -- never a Stripe invoice.
- Checkout stays `STRIPE_MODE=simulate`. No live card charge in this record.
- Issued `ov_` keys without a pack keep existing rate-limit behavior.
- Keys with a pack that is exhausted get HTTP 402 `openvault_pack_exhausted`
  and next-steps (free hops / Register+Install / BYOK / larger pack).
- Subscribe copy still must not name hop vendors (existing key-UI lock).
- Laptop loopback remains free (F14 option B for self-host).
- Fallback already walks `cheap` then `free` roles when those keys exist;
  packs do not mint third-party accounts. Stuck callers get Register+Install
  and BYOK next-steps instead of a silent paid-hop spend.

## Consequences

- Good: F14 is no longer an open pick; hosting SKUs stay for ship.
- Bad: blend rate is not a provider price table. Unlock: verified per-hop
  rates with citations, then replace the blend.
- Neutral: live Stripe price ids for these packs are not created here.
