---
status: proposed
date: 2026-09-07
decision-makers: founder
---

# DR-0013 - SYSTEM control plane locked display rates; usage $/unit stays NEEDS-YOU

## Context and Problem Statement

Issue [#48](https://github.com/Netie-AI/OpenVault/issues/48) asks OpenVault to own
the SYSTEM control plane (entitlements, routing, unlock, metering, seats) without
shipping a public marketing rate page (Marketing owns netie.ai) and without inventing
a usage $/unit. STATUS.md still said "pricing is NEEDS-YOU, not a ticket" after #33
closed metering. Those two statements can both be true if display SKUs are locked
constants and the usage unit price remains unset.

## Considered Options

- Invent a usage $/unit so a bill can be shown now
- Put the catalog on a public `:5000` page
- Encode founder-locked display SKUs as config; keep usage unit explicit NEEDS-YOU

## Decision Outcome

Chosen option: encode the locked display SKUs as versioned policy constants on a
loopback-only `/api/system/*` surface. Usage `$/unit` stays `None` / `NEEDS-YOU`.
Ultra/Giga reuse the existing FreeRoute `pro` limiter because rpm/tpm for those
SKUs was not locked; the 20% usage-credit discount is the locked differentiator.
Team seats add `USD 30` each to the *display* monthly total only.

Public `:5000` bind stays off. Internal writers stay on
`http://35.253.229.206:8080`. One vault: entitlements live in `accounts.db`, not a
second key store. `ov_` tokens are never written into this catalog.

On merge, this record may flip to `accepted`. Decision Agent does not merge.

## Consequences

- Good: agents cannot re-litigate Basic/Pro/Ultra/Team/Giga display numbers or the
  20% Ultra/Giga credit.
- Good: a test fails if anyone fills in `USAGE_UNIT_USD`.
- Bad: a dollar bill still cannot be computed until the founder sets the unit.
- Neutral: issued API keys remain `free`/`pro` limiter names; control-plane plans
  map onto those existing buckets rather than inventing new rpm.

## Confirmation

`OpenMW/tests/test_control_plane.py` plus `test_freeroute_metering.py` asserting
`priced is False` and `usage_unit_status == "NEEDS-YOU"`.
