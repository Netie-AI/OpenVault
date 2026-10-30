# Openship reseller catalog

`pricing.json` defines Openship's prices, namespace credit allowances, application
limits and top-up packs. The SaaS API publishes it at `GET /api/billing/plans`.
Marketing, the Cloud dashboard and linked installations read that API. New
checkout sends a generic Oblien `offer`; it does not buy the reseller account an
Oblien platform plan. The existing $10 / $39 / $99 monthly prices are retained.

## Authoring a plan

```jsonc
"price": { "monthly": 1000, "annual": null }, // USD cents
"billing": {
  "creditsPerCycle": 1200,                   // whole namespace credits
  "yearlyCreditsPerCycle": null,
  "overdraft": 0,
  "suspendThreshold": 0,
  "onOverdraftAction": "stop_workspaces",
  "checkoutName": "Openship Starter",       // optional; localized name is the default
  "checkoutDescription": "Your hosted application plan" // optional
}
```

Price and credits are independent. Paying $10 funds the reseller's Oblien wallet
at Oblien's standard rate (currently 1,000 platform credits), while the customer
receives this plan's configured namespace allowance. Neither amount is derived
from project limits, build minutes or display text. Current monthly allowances
are 1,200 / 3,000 / 15,000 namespace credits; they preserve the prior allowances
while using Openship's prices. Review the allowance economics before changing
these values.

Openship uses milli-credits internally (1,000 milli-credits = one Oblien credit).
`billing.creditsPerCycle` and the generic offer use whole credits; API
`monthlyCredits`, `annualCredits` and credit-pack `credits_milli` use milli-credits.
Top-ups retain the authored packs: 5,000 credits for $5, 25,000 for $20 and 100,000
for $70. Those namespace allowances are separate from wallet funding too.

Grace is zero by default. Set both `overdraft` and `suspendThreshold` to 60 to
allow 60 extra credits. Suspension cannot occur before the blocking threshold.
The provider's `balance` already includes grace. A top-up cannot change grace,
project/service/build limits or VM caps.

Annual checkout remains disabled. To enable it, publish an annual price, an
explicit `yearlyCreditsPerCycle` and `annual.enabled: true`. A yearly payment
delivers one annual allowance; it does not schedule twelve monthly grants.

## Limits and saved customer contracts

The `limits` object controls workloads, service stacks, service count, projects,
per-service machine size, build minutes and free domains. Custom domains and seats
remain uncapped; finite values are rejected because those admission gates are not
implemented. `null` in an application limit means uncapped. Paid plans leave the legacy
`computeMinutesPerMonth` field null: credit metering does not promise fixed CPU
minutes or guarantee all allowed services can run continuously for a month.

Checkout saves the validated application limits as versioned metadata with the
organization and immutable namespace identities. Oblien saves the offer's price,
credits, grace and VM caps. Renewals reapply that saved offer, and Openship reads
the saved application limits. Catalog edits affect new checkouts; they do not
silently change existing customers' terms. A paid plan change creates a new,
full-price cycle without automatic proration and replaces only that namespace's
subscription. An existing catalog subscription remains readable until replaced.
Its invoices and historical price remain in its own portal; the current catalog
is not displayed as the price that an older customer purchased.

`billing.resourceLimits` is explicit policy in the catalog, passed unchanged to
Oblien. `cloud-resource-limits.ts` copies it; it performs no budget calculation.

| Oblien field | Scope and default |
| --- | --- |
| `max_workspaces` | Allocated namespace workspaces: explicitly 2 / 5 / 12 / 52 / null for Free / Starter / Pro / Team / Enterprise, including build workspace room |
| `max_vcpus`, `max_ram_mb`, `max_disk_gb` | Per VM; null inherits Oblien capacity, or set an explicit stricter customer cap |

Oblien computes effective capacity from declared and saved paid limits, the
owner account and platform ceilings. Actual build/Compose resource requests are
workload sizing; they do not determine the namespace policy or credit allowance.

Openship enforces application service/project/build rules and per-service CPU/RAM
at mutation boundaries under organization locks. Oblien enforces namespace VM
caps and spending, including directly scoped VM calls. The VM cap does not
represent a per-container cap; raw VM API access is a different capability from
Openship application operations. Restrict customer credentials accordingly.

Oblien intersects saved offer caps with configured namespace caps and account
capacity. The Openship reconciler updates its namespace mirror from the saved
offer before spending/token issuance; it never writes credits. Lowering caps
does not delete or shrink existing resources. Stop/resize or remove resources
as required before a downgrade, and keep cleanup available when access is blocked.

Custom reseller plans do not inherit Oblien's Hobby/Pro/Scale edge traffic
allowances. Traffic is measured, but no per-plan traffic ceiling or request cap
is advertised without an enforced policy. Missing traffic capacity is unknown,
not unlimited.

## Product copy and provider requirements

Localized plan names, descriptions and feature text live in `locales/*.json`.
Optional `billing.checkoutName` / `checkoutDescription` override the checkout
copy. They do not grant resources. Numeric feature placeholders come from the
plan's limits. The current subscription view shows saved price, description and
limits, rather than newly edited catalog feature claims.

`stripePriceEnv`, campaign/coupon helpers and historical direct-Stripe records
remain for legacy compatibility. They do not control Cloud reseller checkout.
This path does not apply coupons, promotions, trials or proration. Do not
advertise a legacy campaign as a Cloud checkout discount.

The deployed Oblien API must report `reseller.contractVersion >= 2`,
`offerPolicy: true`, `resourceLimits: true`, `effectiveResourceLimits: true`
from `/billing/catalog`. Checkout and
readiness checks enforce this capability; startup logs a missing capability. The
published `oblien@2.4.0` SDK already transports the offer; no unpublished SDK is
required by Openship's lockfile. Oblien SDK 2.5.0 adds the new exported types.

See [Cloud release gate](../../../../docs/openship-cloud-launch.md) for deployment
order, webhook/return configuration and acceptance checks.
