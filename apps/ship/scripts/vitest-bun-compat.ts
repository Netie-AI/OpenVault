// Copyright (c) 2026 Netie AI. Licensed under Apache-2.0.
//
// Bun's `toStartWith` matcher for vitest, so scripts/*.test.ts written for
// `bun test` run unchanged. Same check: the received string starts with the
// expected prefix.
import { expect } from "vitest";

expect.extend({
  toStartWith(received: unknown, prefix: string) {
    const pass = typeof received === "string" && received.startsWith(prefix);
    return {
      pass,
      message: () =>
        `expected ${JSON.stringify(received)} ${pass ? "not " : ""}to start with ${JSON.stringify(prefix)}`,
    };
  },
});
