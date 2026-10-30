// @vitest-environment happy-dom
// Copyright (c) 2026 Netie AI. Licensed under Apache-2.0.
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { I18nProvider } from "@/components/i18n-provider";
import { baseDictionary } from "@/i18n";
import { NewChannelForm } from "./NotificationsTab";

const api = vi.hoisted(() => ({
  getEmailSettings: vi.fn(),
  createChannel: vi.fn(),
  showToast: vi.fn(),
}));

vi.mock("@/lib/api/system", () => ({ systemApi: { getEmailSettings: api.getEmailSettings } }));
vi.mock("@/lib/api", () => ({
  notificationsApi: { createChannel: api.createChannel },
  getApiErrorMessage: (error: Error) => error.message,
}));
vi.mock("@/lib/auth-client", () => ({ authClient: {} }));
vi.mock("@/context/ToastContext", () => ({ useToast: () => ({ showToast: api.showToast }) }));

let root: Root;
let host: HTMLDivElement;

beforeEach(() => {
  vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT", true);
  api.getEmailSettings.mockResolvedValue({ deliverable: true });
  host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
});
afterEach(async () => {
  await act(async () => root.unmount());
  host.remove();
  vi.unstubAllGlobals();
  vi.clearAllMocks();
});

async function render() {
  await act(async () =>
    root.render(
      <I18nProvider>
        <NewChannelForm onSaved={async () => {}} onCancel={() => {}} />
      </I18nProvider>,
    ),
  );
}

async function pick(label: string) {
  const found = [...document.querySelectorAll("button")].find(
    (node) => node.textContent?.trim() === label,
  );
  expect(found, `button ${label}`).toBeDefined();
  await act(async () => found!.click());
}

const form = baseDictionary.settings.notifications.form;

describe("webhook channel form documents the signature headers", () => {
  it("names both headers and says they carry the same value", async () => {
    await render();
    await pick(baseDictionary.settings.notifications.kinds.webhook);
    const text = host.textContent ?? "";
    expect(text).toContain(form.webhookSignatureHint);
    expect(form.webhookSignatureHint).toContain("X-FreeBuild-Signature-256");
    expect(form.webhookSignatureHint).toContain("X-Openship-Signature-256");
    expect(form.webhookSignatureHint).toContain("same value");
    expect(host.querySelector(`input[placeholder="${form.webhookPlaceholder}"]`)).not.toBeNull();
  });

  it("shows the hint for the webhook kind only", async () => {
    await render();
    expect(host.textContent ?? "").not.toContain(form.webhookSignatureHint);
    await pick(baseDictionary.settings.notifications.kinds.slack);
    expect(host.textContent ?? "").not.toContain(form.webhookSignatureHint);
    await pick(baseDictionary.settings.notifications.kinds.webhook);
    expect(host.textContent ?? "").toContain(form.webhookSignatureHint);
  });
});
