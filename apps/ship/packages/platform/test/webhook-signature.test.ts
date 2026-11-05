// Copyright (c) 2026 Netie AI. Licensed under Apache-2.0.
import { createHmac } from "node:crypto";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { NotificationChannel } from "@repo/db";

const mocks = vi.hoisted(() => ({
  safeFetch: vi.fn(),
  // The stored secret is "sealed"; the fake decrypt strips a marker so the test
  // can tell that the worker signs with the plaintext, never the ciphertext.
  decrypt: vi.fn((sealed: string) => sealed.replace(/^sealed:/, "")),
}));

vi.mock("@repo/db", () => ({ repos: {} }));
vi.mock("@repo/platform/engine/lib/mail", () => ({ sendMail: vi.fn() }));
vi.mock("@repo/platform/engine/lib/encryption", () => ({ decrypt: mocks.decrypt }));
vi.mock("@repo/platform/engine/lib/safe-fetch", () => ({ safeFetch: mocks.safeFetch }));
vi.mock("@repo/platform/engine/config/env", () => ({
  env: { CLOUD_MODE: false, NOTIFY_WEBHOOK_ALLOW_INTERNAL: false },
}));

import {
  WEBHOOK_SIGNATURE_HEADER,
  WEBHOOK_SIGNATURE_LEGACY_HEADER,
  sendTestToChannel,
  webhookSignatureHeaders,
} from "../src/engine/lib/notification-workers";

const SECRET = "s3cret-signing-key";

function webhookChannel(config: Record<string, unknown>): NotificationChannel {
  return {
    id: "ch_1",
    userId: "user_1",
    kind: "webhook",
    label: "receiver",
    config,
    verified: false,
    enabled: true,
  } as unknown as NotificationChannel;
}

function sentRequest() {
  expect(mocks.safeFetch).toHaveBeenCalledTimes(1);
  const [url, init] = mocks.safeFetch.mock.calls[0] as [
    string,
    { method: string; headers: Record<string, string>; body: string },
  ];
  return { url, init };
}

beforeEach(() => {
  mocks.safeFetch.mockReset().mockResolvedValue({ ok: true, status: 200, text: async () => "" });
  mocks.decrypt.mockClear();
});

describe("outbound webhook signature headers", () => {
  it("names both headers", () => {
    expect(WEBHOOK_SIGNATURE_HEADER).toBe("X-FreeBuild-Signature-256");
    expect(WEBHOOK_SIGNATURE_LEGACY_HEADER).toBe("X-Openship-Signature-256");
  });

  it("webhookSignatureHeaders writes one HMAC under both names", () => {
    const headers = webhookSignatureHeaders(SECRET, '{"a":1}');
    const expected = `sha256=${createHmac("sha256", SECRET).update('{"a":1}').digest("hex")}`;
    expect(Object.keys(headers).sort()).toEqual(
      ["X-FreeBuild-Signature-256", "X-Openship-Signature-256"].sort(),
    );
    expect(headers["X-FreeBuild-Signature-256"]).toBe(expected);
    expect(headers["X-Openship-Signature-256"]).toBe(expected);
  });

  it("sends both headers on a delivery and both carry the same HMAC of the raw body", async () => {
    await sendTestToChannel(
      webhookChannel({ url: "https://receiver.example/hook", hmacSecret: `sealed:${SECRET}` }),
    );
    const { url, init } = sentRequest();
    expect(url).toBe("https://receiver.example/hook");
    expect(init.method).toBe("POST");

    // Recompute from the bytes that went on the wire, with the plaintext secret.
    const expected = `sha256=${createHmac("sha256", SECRET).update(init.body).digest("hex")}`;
    expect(init.headers["X-FreeBuild-Signature-256"]).toBe(expected);
    expect(init.headers["X-Openship-Signature-256"]).toBe(expected);
    expect(init.headers["X-FreeBuild-Signature-256"]).toBe(init.headers["X-Openship-Signature-256"]);
    expect(mocks.decrypt).toHaveBeenCalledWith(`sealed:${SECRET}`);

    // The sealed value is never what was signed.
    const sealedHmac = `sha256=${createHmac("sha256", `sealed:${SECRET}`).update(init.body).digest("hex")}`;
    expect(init.headers["X-FreeBuild-Signature-256"]).not.toBe(sealedHmac);
  });

  it("keeps the other request headers unchanged", async () => {
    await sendTestToChannel(
      webhookChannel({ url: "https://receiver.example/hook", hmacSecret: `sealed:${SECRET}` }),
    );
    const { init } = sentRequest();
    expect(Object.keys(init.headers).sort()).toEqual(
      [
        "Content-Type",
        "User-Agent",
        "X-FreeBuild-Signature-256",
        "X-Openship-Signature-256",
      ].sort(),
    );
    expect(init.headers["Content-Type"]).toBe("application/json");
    expect(init.headers["User-Agent"]).toBe("FreeBuild-Webhook/1.0");
  });

  it("sends neither header when the channel has no signing secret", async () => {
    await sendTestToChannel(webhookChannel({ url: "https://receiver.example/hook" }));
    const { init } = sentRequest();
    expect(init.headers["X-FreeBuild-Signature-256"]).toBeUndefined();
    expect(init.headers["X-Openship-Signature-256"]).toBeUndefined();
    expect(mocks.decrypt).not.toHaveBeenCalled();
  });
});
