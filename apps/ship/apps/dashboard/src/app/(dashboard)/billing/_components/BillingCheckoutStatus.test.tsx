// @vitest-environment happy-dom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { I18nProvider } from "@/components/i18n-provider";
import { baseDictionary } from "@/i18n";
import { BillingCheckoutStatus } from "./BillingCheckoutStatus";

const mocks = vi.hoisted(() => ({
  state: vi.fn(),
  checkout: vi.fn(),
  router: { refresh: vi.fn() },
}));
vi.mock("next/navigation", () => ({ useRouter: () => mocks.router }));
vi.mock("@/lib/api/billing", () => ({
  billingApi: { getBillingState: mocks.state, getCheckoutStatus: mocks.checkout },
}));
const copy = baseDictionary.billing.checkout;
const paid = {
  id: "cs_selected",
  kind: "subscription",
  status: "complete",
  paymentStatus: "paid",
  fulfillmentStatus: "completed",
  fulfilled: true,
  creditsGranted: 1_200_000,
};
let container: HTMLDivElement;
let root: Root;
async function render(props: Parameters<typeof BillingCheckoutStatus>[0]) {
  await act(async () =>
    root.render(
      <I18nProvider>
        <BillingCheckoutStatus {...props} />
      </I18nProvider>,
    ),
  );
}
beforeEach(() => {
  vi.useFakeTimers();
  vi.resetAllMocks();
  vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT", true);
  mocks.state.mockResolvedValue({
    tier: "starter",
    status: "active",
    subscription: { interval: "monthly" },
  });
  mocks.checkout.mockResolvedValue(paid);
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
});
afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
  vi.useRealTimers();
  vi.unstubAllGlobals();
});
const subscription = {
  kind: "subscription" as const,
  checkoutId: "cs_selected",
  expectedTier: "starter",
  expectedInterval: "monthly" as const,
};
describe("checkout return confirmation", () => {
  it("confirms the selected paid subscription only after its credits are delivered", async () => {
    await render(subscription);
    expect(container.textContent).toContain(copy.active);
    expect(mocks.checkout).toHaveBeenCalledExactlyOnceWith("cs_selected");
  });
  it.each([undefined, "cs_selected"])(
    "does not reuse an existing active subscription to confirm unpaid checkout %s",
    async (checkoutId) => {
      mocks.checkout.mockResolvedValue({
        ...paid,
        paymentStatus: "unpaid",
        fulfilled: false,
        fulfillmentStatus: "pending",
        creditsGranted: 0,
      });
      await render({ ...subscription, checkoutId });
      expect(container.textContent).toContain(copy.checking);
      await act(async () => {
        await vi.advanceTimersByTimeAsync(30_000);
      });
      expect(container.textContent).toContain(copy.pending);
      expect(container.textContent).not.toContain(copy.active);
    },
  );
  it("keeps a verified payment pending until fulfillment finishes", async () => {
    mocks.checkout.mockResolvedValueOnce({
      ...paid,
      fulfilled: false,
      fulfillmentStatus: "pending",
      creditsGranted: 0,
    });
    await render(subscription);
    expect(container.textContent).toContain(copy.checking);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(3_000);
    });
    expect(container.textContent).toContain(copy.active);
  });
  it("confirms top-ups from the specific credited payment", async () => {
    mocks.checkout.mockResolvedValue({ ...paid, kind: "topup", creditsGranted: 5_000_000 });
    await render({ kind: "topup", checkoutId: "cs_selected" });
    expect(container.textContent).toContain(copy.topupComplete);
  });
  it.each(["refunded", "partially_refunded", "disputed"])(
    "reports %s without showing successful credit delivery",
    async (fulfillmentStatus) => {
      mocks.checkout.mockResolvedValue({ ...paid, fulfillmentStatus });
      await render(subscription);
      expect(container.textContent).toContain(copy.reversed);
      expect(container.querySelector("a")?.href).toBe("mailto:support@openship.io");
    },
  );
  it("rejects a different kind of checkout", async () => {
    mocks.checkout.mockResolvedValue({ ...paid, kind: "topup" });
    await render(subscription);
    expect(container.textContent).toContain(copy.failed);
  });
  it("shows a useful provider failure after retrying instead of inventing payment success", async () => {
    mocks.checkout.mockRejectedValue(new Error("Billing is unavailable. Reference: support-123."));
    await render(subscription);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(30_000);
    });
    expect(container.textContent).toContain(copy.pending);
    expect(container.querySelector('[role="alert"]')?.textContent).toContain("support-123");
  });
});
