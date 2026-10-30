import { createServer, type Server, type ServerResponse } from "node:http";
import { Oblien } from "oblien";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { CloudRuntime } from "./cloud";

// Use the real SDK and HTTP transport. An idle stream never yields another log
// to let a cancelled-flag check run; cleanup must close the upstream connection.
let server: Server;
let runtime: CloudRuntime;
let active: Set<ServerResponse>;
let requests: string[];
let unavailableWorkload: boolean;
let sendHeaders: boolean;

beforeEach(async () => {
  active = new Set();
  requests = [];
  unavailableWorkload = false;
  sendHeaders = true;
  server = createServer((req, res) => {
    const path = new URL(req.url!, "http://localhost").pathname;
    requests.push(path);
    if (unavailableWorkload && path.includes("/workloads/")) {
      res.writeHead(404, { "Content-Type": "application/json" });
      res.end(JSON.stringify({ error: "workload not found" }));
      return;
    }
    active.add(res);
    res.on("close", () => active.delete(res));
    if (sendHeaders) {
      res.writeHead(200, { "Content-Type": "text/event-stream" });
      res.flushHeaders();
    }
  });
  await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", resolve));
  const address = server.address();
  if (!address || typeof address === "string") throw new Error("No test server address");
  runtime = new CloudRuntime(new Oblien({ token: "test", baseUrl: `http://127.0.0.1:${address.port}` }));
});

afterEach(async () => {
  server.closeAllConnections();
  await new Promise<void>((resolve) => server.close(() => resolve()));
});

it.each([
  ["workload stream", false, true, 0, "/workloads/app/logs/stream"],
  ["fallback stream", true, true, 0, "/logs/stream/cmd"],
  ["request awaiting headers", false, false, 0, "/workloads/app/logs/stream"],
  ["history request", false, false, 100, "/workloads/app/logs"],
  ["fallback history request", true, false, 100, "/logs"],
] as const)("closes an idle %s immediately on unsubscribe", async (_label, fallback, headers, tail, path) => {
  unavailableWorkload = fallback;
  sendHeaders = headers;
  const onLog = vi.fn();
  const onEnd = vi.fn();
  const stop = await runtime.streamRuntimeLogs("workspace", onLog, { tail, onEnd });
  await vi.waitFor(() => {
    expect(requests.at(-1)).toBe(`/workspace/workspace${path}`);
    expect(active.size).toBe(1);
  });
  const started = requests.length;
  stop();
  stop();
  await vi.waitFor(() => expect(active.size).toBe(0));
  expect(requests).toHaveLength(started);
  expect(onLog).not.toHaveBeenCalled();
  expect(onEnd).not.toHaveBeenCalled();
});

it("cancels only its subscription when another view follows the same workspace", async () => {
  const firstLog = vi.fn();
  const secondLog = vi.fn();
  const stopFirst = await runtime.streamRuntimeLogs("workspace", firstLog, { tail: 0 });
  await vi.waitFor(() => expect(active.size).toBe(1));
  const stopSecond = await runtime.streamRuntimeLogs("workspace", secondLog, { tail: 0 });
  await vi.waitFor(() => expect(active.size).toBe(2));
  stopFirst();
  await vi.waitFor(() => expect(active.size).toBe(1));
  for (const response of active) {
    response.write('data: {"message":"still following","stream":"stdout"}\n\n');
  }
  await vi.waitFor(() => expect(secondLog).toHaveBeenCalledWith(expect.objectContaining({ message: "still following" })));
  expect(firstLog).not.toHaveBeenCalled();
  stopSecond();
  await vi.waitFor(() => expect(active.size).toBe(0));
});

it("preserves history, live output, and normal completion through the real SDK", async () => {
  server.removeAllListeners("request");
  server.on("request", (req, res) => {
    if (req.url?.endsWith("/stream")) {
      res.writeHead(200, { "Content-Type": "text/event-stream" });
      res.end('data: {"message":"live","stream":"stderr"}\n\n');
    } else {
      res.writeHead(200, { "Content-Type": "application/json" });
      res.end(JSON.stringify({ logs: "[2026-09-24T00:00:00Z] stdout: history" }));
    }
  });
  const onLog = vi.fn();
  const onEnd = vi.fn();
  await runtime.streamRuntimeLogs("workspace", onLog, { onEnd });
  await vi.waitFor(() => expect(onEnd).toHaveBeenCalledExactlyOnceWith());
  expect(onLog.mock.calls.map(([entry]) => [entry.message, entry.level])).toEqual([
    ["history", "info"], ["live", "warn"],
  ]);
});
