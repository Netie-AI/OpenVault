---
status: accepted
date: 2026-08-19
decision-makers: founder
---

# DR-0009 - The metered gateway spends OpenVault's own pooled keys

## Context and Problem Statement

The metered gateway (EPIC #33) began authenticating third-party callers with issued `ov_`
keys. `fallback.ordered_candidates` applied no owner filter at all, so tenant A's request
walked the same key pool as everyone else and could select a key tenant B had uploaded.

With one operator and one pool this was latent. It becomes real the moment a second tenant
holds a key, and it is not a bug an agent may fix by picking a side: the answer changes the
`ordered_candidates` signature, what a usage row *means*, whose provider terms are being
consumed, and which pricing models are even coherent. Filed as #36 and escalated rather
than guessed.

## Considered Options

- **(a) OpenVault's own pooled keys.** BYO-margin SaaS. The caller brings nothing; OpenVault
  carries the provider cost and the provider ToS exposure.
- **(b) Keys the tenant uploaded.** Custody-as-a-service. No cost exposure, but the ledger
  measures *their* spend rather than ours, and every pricing model built on it changes shape.

## Decision Outcome

Chosen option: **(a) pooled keys**, decided by the founder on 2026-08-19.

A `custody` tag (`pooled` | `tenant`) on `KeyRecord`. The gateway walks pooled keys only.
A key marked `tenant` is stored and remains visible to its owner, but never enters the
fallback pool, so no metered caller can reach it however healthy or high-priority it looks.

Two independent controls, because this is custody code:

1. `KeyVault.pooled_ordered()` is the list the walk, the hop dashboard and the deploy gate
   all source from. `enabled_ordered()` keeps its old meaning and is no longer a spend path.
2. `FallbackManager._is_available` refuses non-pooled records *before* it checks health, so
   a future caller who sources from the wrong list still cannot reach a tenant key.

Existing rows backfill to `pooled`: before this column, every key in the vault was the
operator's own. Defaulting the other way would empty the pool on upgrade and 503 every
route, which is a worse outage than the bug being fixed.

Where no pooled key is available but tenant keys are held, the refusal is typed
`openvault_no_pooled_keys` and says so. `openvault_no_keys` continues to mean an empty
vault. Telling an operator "no healthy API keys" while the vault visibly holds keys would
send them looking in the wrong place (R-0011).

## Consequences

- Good: the custody question is answered in one place, and the invariant is asserted at the
  layer the customer receives - `GET /api/usage` `vault_key_id` - rather than on the manager
  object, so route wiring cannot regress it silently (R-0001).
- Good: `KeyRecord.account_id` is left alone. It already means *provider account*
  (`vault/accounts.py`), and overloading it with tenant ownership would have produced two
  meanings for one column.
- Bad: OpenVault carries provider cost and provider ToS exposure for every metered request.
  That is the deliberate trade in (a), and it is what makes pricing a live question rather
  than a deferred one.
- Neutral: no owner field was added. Option (b) would need one; option (a) does not, and
  adding it now would be building for a decision that was not made.
- Deliberate omission: `PATCH /api/keys/{id}` cannot change `custody`. Moving a key into the
  pool is the one direction that turns somebody else's money into spendable balance, so there
  is no route that does it by accident. A key created with the wrong custody is deleted and
  recreated. If an operator ever needs to reclassify in bulk, that wants its own audited
  route, not a field on the general update.

## Confirmation

`OpenMW/tests/test_pooled_key_custody.py`. Mutation matrix, all three cases run:

| mutation | result |
|---|---|
| `pooled_ordered` stops filtering | status test fails |
| `_is_available` custody guard removed | walk test fails |
| both removed (the original #36 bug) | 5 tests fail, including the ledger assertion |

Each control fails independently, so neither is masked by the other (R-0007).
