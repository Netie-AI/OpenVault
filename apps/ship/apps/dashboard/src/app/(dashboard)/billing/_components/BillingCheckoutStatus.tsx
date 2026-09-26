"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { useI18n } from "@/components/i18n-provider";
import { billingApi } from "@/lib/api/billing";

/** A return URL starts polling; only a fresh provider entitlement confirms access. */
export function BillingCheckoutStatus({
  kind,
  checkoutId,
  expectedTier,
  expectedInterval,
}: {
  kind: "subscription" | "topup";
  checkoutId?: string;
  expectedTier?: string;
  expectedInterval?: "monthly" | "annual";
}) {
  const router = useRouter();
  const { t } = useI18n();
  const [status, setStatus] = useState<"checking" | "active" | "pending" | "failed" | "reversed">(
    "checking",
  );
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setStatus("checking");
    setError(null);
    let disposed = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const deadline = Date.now() + 30_000;
    async function refresh() {
      try {
        const [state, checkout] = await Promise.all([
          billingApi.getBillingState(),
          checkoutId ? billingApi.getCheckoutStatus(checkoutId) : null,
        ]);
        if (disposed) return;
        setError(null);
        router.refresh();
        if (
          checkout &&
          ["refunded", "partially_refunded", "disputed"].includes(checkout.fulfillmentStatus)
        ) {
          setStatus("reversed");
          return;
        }
        if (
          checkout &&
          (checkout.kind !== kind || ["failed", "expired"].includes(checkout.fulfillmentStatus))
        ) {
          setStatus("failed");
          return;
        }
        const paid =
          checkout?.paymentStatus === "paid" &&
          checkout.fulfilled &&
          checkout.fulfillmentStatus === "completed" &&
          checkout.creditsGranted > 0;
        if (
          paid &&
          (kind === "topup" ||
            (expectedTier &&
              state.tier === expectedTier &&
              state.status === "active" &&
              (!expectedInterval || state.subscription?.interval === expectedInterval)))
        ) {
          setStatus("active");
          return;
        }
      } catch (failure) {
        // A provider outage does not confirm or undo a payment. Retry briefly.
        if (!disposed) setError(failure instanceof Error ? failure.message : null);
      }
      if (disposed) return;
      if (Date.now() >= deadline) setStatus("pending");
      else timer = setTimeout(refresh, 3_000);
    }
    void refresh();
    return () => { disposed = true; clearTimeout(timer); };
  }, [kind, checkoutId, expectedTier, expectedInterval, router]);

  return (
    <div
      role="status"
      aria-live="polite"
      className="mb-6 rounded-lg border border-border bg-muted/30 p-4 text-sm"
    >
      <p>
        {status === "active" && kind === "topup"
          ? t.billing.checkout.topupComplete
          : t.billing.checkout[status]}
      </p>
      {error && status === "pending" && (
        <p role="alert" className="mt-2">
          {error}
        </p>
      )}
      {["pending", "failed", "reversed"].includes(status) && (
        <a className="mt-2 inline-block underline" href="mailto:support@openship.io">
          {t.billing.checkout.support}
        </a>
      )}
    </div>
  );
}
