/**
 * Masked .env preview and per-row add. Fixtures only. No network.
 */

import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import {
  clearEnvPaste,
  onEnvPasteClear,
  previewEnvText,
  runEnvAdds,
  type EnvAddBody,
} from "./envImport.ts";

const GROQ = "gsk_fixture_not_a_real_key_0001";
const OPENROUTER = "sk-or-v1-fixture-openrouter-0001";
const GOOGLE = "AIzaFixtureGoogleKey0000000001";
const NVIDIA = "nvapi-fixture-nvidia-key-0001";
const SEA = "sealion-fixture-key-0000000001";

const PASTE = [
  "# comment",
  `export GROQ_API_KEY="${GROQ}"`,
  `OPENROUTER_API_KEY=${OPENROUTER}`,
  `GOOGLE_API_KEY='${GOOGLE}'`,
  `NVIDIA_API_KEY=${NVIDIA}`,
  `SEA_LION_API_KEY=${SEA}`,
  "UNKNOWN_API_KEY=should-not-appear-in-preview",
  "OPENVAULT_URL=http://127.0.0.1:5000",
].join("\n");

const SECRETS = [GROQ, OPENROUTER, GOOGLE, NVIDIA, SEA];

test("preview masks known providers and skips unknown names", () => {
  const rows = previewEnvText(PASTE);
  assert.deepEqual(
    rows.map((row) => row.provider),
    ["groq", "openrouter", "google", "nvidia", "sea_lion"],
  );
  assert.deepEqual(
    rows.map((row) => row.envKey),
    [
      "GROQ_API_KEY",
      "OPENROUTER_API_KEY",
      "GOOGLE_API_KEY",
      "NVIDIA_API_KEY",
      "SEA_LION_API_KEY",
    ],
  );
  const blob = JSON.stringify(rows);
  for (const secret of SECRETS) {
    assert.equal(blob.includes(secret), false);
  }
  for (const row of rows) {
    assert.equal(row.masked.includes("..."), true);
    assert.equal(row.masked.endsWith("****"), true);
  }
  assert.equal(blob.includes("should-not-appear-in-preview"), false);
  assert.equal(blob.includes("127.0.0.1"), false);
});

test("add reports sea_lion success and a failed row without secret bodies", async () => {
  const posted: EnvAddBody[] = [];
  const outcomes = await runEnvAdds(PASTE, async (body) => {
    posted.push(body);
  });
  assert.equal(posted.length, 5);
  assert.equal(posted[0]?.provider, "groq");
  assert.equal(posted[0]?.role, "free");
  assert.equal(posted[4]?.provider, "sea_lion");
  assert.equal(posted[4]?.base_url, undefined);

  const blob = JSON.stringify(outcomes);
  for (const secret of SECRETS) {
    assert.equal(blob.includes(secret), false);
  }
  assert.equal(outcomes.filter((row) => row.ok).length, 5);
  const sea = outcomes.find((row) => row.envKey === "SEA_LION_API_KEY");
  assert.equal(sea?.ok, true);
  assert.equal(sea?.provider, "sea_lion");

  const cursor = "cursor-fixture-key-0000000001";
  const failed = await runEnvAdds(`CURSOR_API_KEY=${cursor}`, async (body) => {
    throw new Error(`rejected ${body.secret}`);
  });
  const failBlob = JSON.stringify(failed);
  assert.equal(failBlob.includes(cursor), false);
  assert.equal(failed[0]?.ok, false);
  assert.equal(failed[0]?.provider, "custom");
  assert.match(failed[0]?.error ?? "", /rejected/);
});

test("SEALION_API_KEY imports as sea_lion", async () => {
  const secret = "sealion-alias-fixture-key-0002";
  const text = `SEALION_API_KEY=${secret}`;
  const rows = previewEnvText(text);
  assert.equal(rows[0]?.envKey, "SEALION_API_KEY");
  assert.equal(rows[0]?.provider, "sea_lion");
  assert.equal(JSON.stringify(rows).includes(secret), false);
  const posted: EnvAddBody[] = [];
  const outcomes = await runEnvAdds(text, async (body) => {
    posted.push(body);
  });
  assert.equal(posted[0]?.provider, "sea_lion");
  assert.equal(outcomes[0]?.ok, true);
  assert.equal(outcomes[0]?.provider, "sea_lion");
  assert.equal(JSON.stringify(outcomes).includes(secret), false);
});

test("env import UI posts /api/keys and does not log", () => {
  const src = readFileSync(
    new URL("../../components/vault/EnvTextImport.tsx", import.meta.url),
    "utf8",
  );
  assert.equal(src.includes("previewEnvText"), true);
  assert.equal(src.includes("createKey"), true);
  assert.equal(src.includes("onEnvPasteClear"), true);
  assert.equal(src.includes('data-testid="env-import-preview"'), true);
  assert.equal(src.toLowerCase().includes("console.log"), false);
});

test("Lock clears the .env paste listeners see", () => {
  let text = `GROQ_API_KEY=${GROQ}`;
  const stop = onEnvPasteClear(() => {
    text = "";
  });
  try {
    clearEnvPaste();
    assert.equal(text, "");
    assert.equal(text.includes(GROQ), false);
  } finally {
    stop();
  }
});
