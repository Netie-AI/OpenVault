import { describe, expect, it } from "vitest";

import type { MailDeferralKind } from "@/lib/api";

import en from "./locales/en/emailsAdmin.json";

/**
 * The Outbound delivery card is the one place in the mail panel where a wrong
 * translation is worse than a missing one.
 *
 * Two ways that happens, both invisible to the parity ratchet (which counts keys,
 * not their contents):
 *
 *   1. A dropped placeholder. "Relaying through {host}" without `{host}` renders
 *      "Relaying through " — an operator reads that as "we don't know where mail
 *      goes" on a box that is working fine.
 *   2. A deferral kind the copy doesn't cover. The server classifies every
 *      deferral into one of five kinds and the card indexes `kind`/`hint` by it,
 *      so a sixth kind added server-side without copy renders `undefined` exactly
 *      where the diagnosis belongs.
 */

// Modified by Netie AI, 2026: English-only — the other 8 locales' dirs are
// gone (see apps/dashboard/src/i18n/index.ts). The it.each blocks below that
// iterate LOCALES now run 0 cases instead of 8; the English-only assertions
// further down (unchanged) still run and still catch a real regression.
const LOCALES: Array<[string, typeof en]> = [];

/** Every leaf under a subtree, as dotted path → string. */
function leaves(node: unknown, prefix: string): Record<string, string> {
  if (typeof node === "string") return { [prefix]: node };
  if (!node || typeof node !== "object") return {};
  return Object.entries(node as Record<string, unknown>).reduce<Record<string, string>>(
    (acc, [k, v]) => Object.assign(acc, leaves(v, `${prefix}.${k}`)),
    {},
  );
}

function placeholders(text: string): string[] {
  return [...(text.match(/\{\w+\}/g) ?? [])].sort();
}

function at(dict: unknown, path: string): string | undefined {
  const value = path
    .split(".")
    .reduce<unknown>((node, key) => (node as Record<string, unknown> | undefined)?.[key], dict);
  return typeof value === "string" ? value : undefined;
}

const INTERPOLATED = Object.entries({
  ...leaves(en.health.delivery, "health.delivery"),
  "health.summary.partDelivery": en.health.summary.partDelivery,
  "health.summary.almostSubQueueOne": en.health.summary.almostSubQueueOne,
  "health.summary.almostSubQueueOther": en.health.summary.almostSubQueueOther,
}).filter(([, text]) => placeholders(text).length > 0);

describe("outbound-delivery copy takes the same values in every locale", () => {
  it("has values to check in the first place", () => {
    // Guards the guard: a rename that empties this list would make the whole
    // suite below pass by checking nothing.
    expect(INTERPOLATED.length).toBeGreaterThanOrEqual(6);
  });

  // A key a locale hasn't translated falls back to English per key (deepMerge in
  // ./index) and renders correctly, so absence is fine — only a PRESENT string
  // with the wrong placeholders is a defect.
  it.each(LOCALES)("%s", (_locale, dict) => {
    let checked = 0;
    for (const [path, english] of INTERPOLATED) {
      const translated = at(dict, path);
      if (translated === undefined) continue;
      expect(placeholders(translated), path).toEqual(placeholders(english));
      checked += 1;
    }
    // Nothing resolving would make this locale pass by skipping every assertion.
    expect(checked).toBeGreaterThan(0);
  });
});

describe("every deferral kind the server can report has copy", () => {
  // Typed as a full Record, so tsc fails here first if MailDeferralKind grows.
  const KINDS: Record<MailDeferralKind, true> = {
    auth: true,
    tls: true,
    network: true,
    rejected: true,
    other: true,
  };
  const kinds = Object.keys(KINDS).sort();

  it("labels each kind", () => {
    expect(Object.keys(en.health.delivery.kind).sort()).toEqual(kinds);
  });

  it("names a remedy for each kind, plus the relay-specific auth case", () => {
    expect(Object.keys(en.health.delivery.hint).sort()).toEqual([...kinds, "authRelay"].sort());
  });

  it.each(LOCALES)("is all-or-nothing per locale, never a partial map (%s)", (_locale, dict) => {
    for (const group of ["kind", "hint"] as const) {
      const translated = kinds.filter(
        (kind) => at(dict, `health.delivery.${group}.${kind}`) !== undefined,
      );
      if (translated.length === 0) continue;
      expect(translated, group).toEqual(kinds);
    }
  });
});
