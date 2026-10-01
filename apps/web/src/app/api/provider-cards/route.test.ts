/**
 * Provider-card proxy: the operator presents X-OpenVault-Admin.
 * The route must not open the admin token file.
 *
 * Run: npx tsx --test src/app/api/provider-cards/route.test.ts
 */

import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import { NextRequest } from "next/server";

import { POST } from "./route.ts";

const SECRET = "sk-unit-test-key-9f3c2a7b-do-not-log";
const TOKEN = "unit-admin-presented-not-from-a-file";

function post(url: string, headers: Record<string, string>, body: string): NextRequest {
  return new NextRequest(url, { method: "POST", headers, body });
}

async function withFetch(
  impl: typeof fetch,
  run: () => Promise<void>,
): Promise<void> {
  const prior = globalThis.fetch;
  globalThis.fetch = impl;
  try {
    await run();
  } finally {
    globalThis.fetch = prior;
  }
}

test("proxy source never reads the admin token file", () => {
  const src = readFileSync(new URL("./route.ts", import.meta.url), "utf8");
  const lowered = src.toLowerCase();
  assert.equal(lowered.includes("admin_token"), false);
  assert.equal(lowered.includes("readfilesync"), false);
  assert.equal(src.includes("node:fs"), false);
  assert.equal(src.includes('from "fs"'), false);
  assert.equal(src.includes("from 'fs'"), false);
  assert.equal(lowered.includes("x-openvault-admin"), true);
});

test("proxy without the admin header returns 401 and does not fetch", async () => {
  let called = 0;
  await withFetch(async () => {
    called += 1;
    throw new Error("upstream must not be called");
  }, async () => {
    const res = await POST(
      post(
        "http://127.0.0.1:3010/api/provider-cards",
        {
          origin: "http://127.0.0.1:3010",
          "content-type": "application/json",
        },
        JSON.stringify({ provider: "groq", secret: SECRET }),
      ),
    );
    assert.equal(res.status, 401);
    const text = await res.text();
    assert.equal(text.includes(SECRET), false);
    assert.equal(called, 0);
  });
});

test("forged Host localhost without a token returns 401 and does not fetch", async () => {
  let called = 0;
  await withFetch(async () => {
    called += 1;
    throw new Error("upstream must not be called");
  }, async () => {
    const res = await POST(
      post(
        "http://localhost/api/provider-cards",
        {
          host: "localhost",
          origin: "http://localhost",
          "content-type": "application/json",
        },
        JSON.stringify({ provider: "groq", secret: SECRET }),
      ),
    );
    assert.equal(res.status, 401);
    assert.equal((await res.text()).includes(SECRET), false);
    assert.equal(called, 0);
  });
});

test("presented admin header is forwarded and never echoed or logged", async () => {
  const logs: string[] = [];
  const methods = ["log", "info", "warn", "error", "debug"] as const;
  const prior = new Map<string, (...args: unknown[]) => void>();
  for (const name of methods) {
    prior.set(name, console[name]);
    console[name] = (...args: unknown[]) => {
      logs.push(args.map((item) => String(item)).join(" "));
    };
  }
  let forwarded: string | null = null;
  try {
    await withFetch(async (_url, init) => {
      forwarded = new Headers(init?.headers).get("x-openvault-admin");
      return new Response(
        JSON.stringify({
          label: TOKEN,
          masked_id: TOKEN,
          outcome: `added ${TOKEN}`,
        }),
        { status: 200, headers: { "content-type": "application/json" } },
      );
    }, async () => {
      const res = await POST(
        post(
          "http://127.0.0.1:3010/api/provider-cards",
          {
            origin: "http://127.0.0.1:3010",
            "content-type": "application/json",
            "x-openvault-admin": TOKEN,
          },
          JSON.stringify({ provider: "groq", secret: SECRET }),
        ),
      );
      assert.equal(res.status, 200);
      const text = await res.text();
      assert.equal(forwarded, TOKEN);
      assert.equal(text.includes(TOKEN), false);
      assert.equal(text.includes(SECRET), false);
      assert.equal(logs.join("\n").includes(TOKEN), false);
      assert.equal(logs.join("\n").includes(SECRET), false);
    });
  } finally {
    for (const name of methods) {
      console[name] = prior.get(name) as typeof console.log;
    }
  }
});
