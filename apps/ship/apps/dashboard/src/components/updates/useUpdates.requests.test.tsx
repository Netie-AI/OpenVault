// @vitest-environment happy-dom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { UseUpdates } from "./useUpdates";
const deployment = vi.hoisted(() => ({ selfHosted: true, version: "0.1.0" }));
vi.mock("@/hooks/useDeploymentInfo", () => ({
  useDeploymentInfo: () => deployment,
}));
vi.mock("@/lib/api/urls", () => ({ getRestApiBaseUrl: () => "http://localhost:4000/api" }));

const snapshot = {
  latest: { tag: "v99.0.0", version: "99.0.0", notes: "Product notes." },
  manifest: { advisories: [] },
};
const check = vi.fn();
const fetcher = vi.fn();
const persist = vi.fn();
const critical = {
  id: "critical-notice",
  severity: "critical" as const,
  announce: true,
  affects: ">=0.1.0",
  title: "Maintenance notice",
  message: "Review this notice.",
};
let root: Root;
let container: HTMLDivElement;
let useUpdates: () => UseUpdates;
const state: UseUpdates[] = [];
function Harness({ id }: { id: number }) {
  state[id] = useUpdates();
  return null;
}
async function mount() {
  await act(async () =>
    root.render(
      <>
        <Harness id={0} />
        <Harness id={1} />
      </>,
    ),
  );
}
const releaseRequests = () => fetcher.mock.calls.filter(([url]) => String(url).includes("github"));

beforeEach(async () => {
  vi.resetModules();
  deployment.selfHosted = true;
  vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT", true);
  vi.stubGlobal("fetch", fetcher);
  fetcher.mockReset().mockImplementation(async (url) => {
    if (String(url).endsWith("/latest"))
      return new Response(JSON.stringify({ tag_name: "v99.0.0" }));
    if (String(url).endsWith("CHANGELOG.md")) return new Response("## 99.0.0\n\nProduct notes.");
    return new Response(JSON.stringify({ advisories: [] }));
  });
  check.mockReset().mockResolvedValue({ available: false, ...snapshot });
  persist.mockReset().mockResolvedValue(true);
  window.desktop = {
    isDesktop: true,
    app: { version: async () => "0.1.0" },
    config: {
      get: async (key: string) => (key === "lastSeenVersion" ? "0.1.0" : undefined),
      set: persist,
    },
    updates: { check },
  } as unknown as DesktopBridge;
  localStorage.clear();
  ({ useUpdates } = await import("./useUpdates"));
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
});

describe("advisory dismissal within an app session", () => {
  beforeEach(() => {
    check.mockResolvedValue({ ...snapshot, manifest: { advisories: [critical] } });
  });

  it("dismisses a critical notice in every consumer and keeps it dismissed after navigation and refresh", async () => {
    await mount();
    expect(state[0].state?.advisories).toEqual([critical]);
    expect(state[1].state?.advisories).toEqual([critical]);

    await act(async () => state[0].dismissAdvisory(critical.id));
    expect(state[0].state?.advisories).toEqual([]);
    expect(state[1].state?.advisories).toEqual([]);
    await act(async () => root.render(null));
    await mount();
    await act(async () => {
      state[0].refresh();
      state[1].reload();
    });
    expect(state[0].state?.advisories).toEqual([]);
    expect(state[1].state?.advisories).toEqual([]);
    expect(persist).not.toHaveBeenCalled();
    expect(localStorage.length).toBe(0);
  });

  it("does not resurrect a notice when an older in-flight load finishes", async () => {
    await mount();
    let finish!: (value: unknown) => void;
    check.mockReturnValueOnce(new Promise((resolve) => { finish = resolve; }));
    await act(async () => state[1].refresh());
    await act(async () => state[0].dismissAdvisory(critical.id));
    await act(async () => finish({ ...snapshot, manifest: { advisories: [critical] } }));
    expect(state[0].state?.advisories).toEqual([]);
    expect(state[1].state?.advisories).toEqual([]);
  });

  it("shows a new advisory and restores a critical notice in a new app session", async () => {
    await mount();
    await act(async () => state[0].dismissAdvisory(critical.id));
    const next = { ...critical, id: "new-critical-notice" };
    check.mockResolvedValue({ ...snapshot, manifest: { advisories: [critical, next] } });
    await act(async () => state[0].refresh());
    expect(state[0].state?.advisories).toEqual([next]);

    await act(async () => root.render(null));
    vi.resetModules();
    ({ useUpdates } = await import("./useUpdates"));
    await mount();
    expect(state[0].state?.advisories).toEqual([critical, next]);
  });

  it("also shares dismissal for operator notices in the cloud dashboard", async () => {
    delete window.desktop;
    deployment.selfHosted = false;
    // Every consumer receives its own readable response body.
    fetcher.mockImplementation(async () => new Response(JSON.stringify({ advisories: [critical] })));
    await mount();
    expect(state[0].state?.advisories).toEqual([critical]);
    await act(async () => state[0].dismissAdvisory(critical.id));
    await act(async () => state[1].reload());
    expect(state[0].state?.advisories).toEqual([]);
    expect(state[1].state?.advisories).toEqual([]);
    expect(localStorage.length).toBe(0);
  });

  it("keeps a noncritical dismissal for this session even if saving the preference fails", async () => {
    const recommended = { ...critical, severity: "recommended" as const };
    check.mockResolvedValue({ ...snapshot, manifest: { advisories: [recommended] } });
    persist.mockRejectedValue(new Error("Configuration store unavailable"));
    await mount();
    await act(async () => state[0].dismissAdvisory(recommended.id));
    await act(async () => state[1].reload());
    expect(state[0].state?.advisories).toEqual([]);
    expect(state[1].state?.advisories).toEqual([]);
    expect(persist).toHaveBeenCalledWith("dismissedAdvisoryIds", [recommended.id]);
  });
});
afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
  delete window.desktop;
  vi.unstubAllGlobals();
});

describe("release check requests (#661)", () => {
  it("uses one native snapshot for multiple desktop consumers without a renderer GitHub fetch", async () => {
    await mount();
    expect(check).toHaveBeenCalledOnce();
    expect(releaseRequests()).toHaveLength(0);
    expect(state[0].latest).toEqual(snapshot.latest);
    expect(state[1].latest).toEqual(snapshot.latest);
  });

  it("refreshes through one native check and uses that result", async () => {
    await mount();
    check.mockResolvedValueOnce({
      available: false,
      ...snapshot,
      latest: { ...snapshot.latest, version: "100.0.0" },
    });
    await act(async () => {
      state[0].refresh();
      state[1].refresh();
    });
    expect(check).toHaveBeenCalledTimes(2);
    expect(check).toHaveBeenLastCalledWith(true);
    expect(releaseRequests()).toHaveLength(0);
    expect(state[0].latest?.version).toBe("100.0.0");
  });

  it("also coalesces overlapping manual refreshes in the web dashboard", async () => {
    delete window.desktop;
    await mount();
    expect(releaseRequests()).toHaveLength(3);
    await act(async () => {
      state[0].refresh();
      state[1].refresh();
    });
    expect(releaseRequests()).toHaveLength(6);
  });
});
