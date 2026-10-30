import { Type, type Static } from "@sinclair/typebox";
import { PLAN_IDS, PLANS } from "@repo/core";

/** Only a catalog choice reaches the server. Prices, credits and identity are server-owned. */
const purchasableTiers = PLAN_IDS.filter(id => (PLANS[id].price.monthly ?? 0) > 0);
export const CreateSubscriptionBody = Type.Object(
  {
    planTierId: Type.Union(purchasableTiers.map((id) => Type.Literal(id))),
    interval: Type.Union([Type.Literal("monthly"), Type.Literal("annual")]),
    idempotencyKey: Type.Optional(
      Type.String({ minLength: 16, maxLength: 128, pattern: "^[A-Za-z0-9_-]+$" }),
    ),
  },
  { additionalProperties: false },
);
export type CreateSubscriptionInput = Static<typeof CreateSubscriptionBody>;
export const CreateTopupBody = Type.Object(
  {
    packId: Type.String({ minLength: 1, maxLength: 64 }),
    idempotencyKey: Type.Optional(
      Type.String({ minLength: 16, maxLength: 128, pattern: "^[A-Za-z0-9_-]+$" }),
    ),
  },
  { additionalProperties: false },
);
export type CreateTopupInput = Static<typeof CreateTopupBody>;
