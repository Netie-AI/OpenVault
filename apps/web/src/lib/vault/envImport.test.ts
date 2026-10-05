/**
 * Masked .env preview and per-row add. Fixtures only. No network.
 */

import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import { previewEnvText, runEnvAdds, type EnvAddBody } from "./envImport.ts";

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

test("add reports ok and fail without secret bodies", async () => {
  const posted: EnvAddBody[] = [];
  const outcomes = await runEnvAdds(PASTE, async (body) => {
    posted.push(body);
    if (body.provider === "custom") {
      throw new Error(`rejected ${body.secret}`);
    }
  });
  assert.equal(posted.length, 5);
  assert.equal(posted[0]?.provider, "groq");
  assert.equal(posted[0]?.role, "free");
  assert.equal(posted[4]?.provider, "custom");
  assert.equal(posted[4]?.base_url, "https://api.sea-lion.ai/v1");

  const blob = JSON.stringify(outcomes);
  for (const secret of SECRETS) {
    assert.equal(blob.includes(secret), false);
  }
  assert.equal(outcomes.filter((row) => row.ok).length, 4);
  const failed = outcomes.find((row) => row.envKey === "SEA_LION_API_KEY");
  assert.equal(failed?.ok, false);
  assert.equal(failed?.provider, "sea_lion");
  assert.match(failed?.error ?? "", /rejected/);
  assert.equal((failed?.error ?? "").includes(SEA), false);
});

test("env import UI posts /api/keys and does not log", () => {
  const src = readFileSync(
    new URL("../../components/vault/EnvTextImport.tsx", import.meta.url),
    "utf8",
  );
  assert.equal(src.includes("previewEnvText"), true);
  assert.equal(src.includes("createKey"), true);
  assert.equal(src.includes('data-testid="env-import-preview"'), true);
  assert.equal(src.toLowerCase().includes("console.log"), false);
});
