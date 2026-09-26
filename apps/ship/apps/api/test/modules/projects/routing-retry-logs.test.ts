import { beforeEach, describe, expect, it, vi } from "vitest";
import type { ExecutionContext } from "@repo/platform";

const h = vi.hoisted(() => ({
  retry: vi.fn(),
  work: undefined as Promise<void> | undefined,
}));
vi.mock("@repo/platform/engine/modules/projects/project-runtime.service", () => ({
  retryProjectRouting: h.retry,
}));
vi.mock("@repo/platform/engine/lib/self-app-routing", () => ({
  canRouteSelfApp: async () => false,
}));
vi.mock("@repo/platform/engine/modules/domains/domain.operations", () => ({
  verifyProjectRoutingDomains: vi.fn(),
}));
vi.mock("@repo/platform/engine/lib/audit-emitter", () => ({
  audit: { recordAsync: vi.fn() },
  operationAuditContext: (ctx: unknown) => ctx,
}));
vi.mock("@repo/platform/engine/lib/background-work", () => ({
  trackBackgroundWork: (work: Promise<void>) => {
    h.work = work;
    return work;
  },
}));

import { subscribeRoutingRetry } from "@repo/platform/engine/modules/projects/project-routing-retry.operations";

beforeEach(() => {
  vi.clearAllMocks();
  h.work = undefined;
});

describe("routing retry log outcome", () => {
  it.each([false, true])(
    "reports each failed hostname once and retains the failed outcome (logged during work: %s)",
    async (alreadyLogged) => {
      const warnings = [
        "api.example.com: server is unreachable",
        "app.example.com: target is unavailable",
      ];
      h.retry.mockImplementation(async (_id, _org, opts) => {
        opts.onLog("Applying the project's current routes…");
        if (alreadyLogged) for (const warning of warnings) opts.onLog(warning);
        opts.onLog("Synchronizing managed domains…");
        return { ok: false, warning: warnings.join("\n") };
      });
      const events: Array<{ event: string; data: { message?: string; status?: string } }> = [];
      subscribeRoutingRetry(
        { organizationId: "org-a" } as ExecutionContext,
        "project-a",
      )((event, data) => {
        events.push({ event, data: JSON.parse(data) });
        return true;
      });
      await h.work;

      const lines = events
        .filter(({ event }) => event === "log")
        .flatMap(({ data }) => data.message?.split("\n") ?? []);
      for (const warning of warnings)
        expect(lines.filter((line) => line === warning)).toHaveLength(1);
      expect(events.at(-1)).toEqual({
        event: "complete",
        data: { type: "complete", status: "failed" },
      });
    },
  );
});
