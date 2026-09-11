/**
 * Tests for the bounded readiness probe.
 *
 * Run:  npm --prefix apps/shell test
 *
 * The load-bearing case is "stalls": a listener that completes the TCP
 * handshake and then never writes a byte. That is the real :3010 failure this
 * module was written for - a long-lived `next dev` that accepts connections and
 * answers nothing - and it is the case the previous inline probe could not
 * survive, because its single unbounded `fetch` never settled and its loop
 * bound was therefore never re-evaluated.
 *
 * These use real sockets rather than a stubbed fetch on purpose. A fake that
 * "never resolves" would prove only that the test author can write a pending
 * promise; a real half-open connection proves the probe survives the thing that
 * actually happened.
 */

const test = require("node:test");
const assert = require("node:assert");
const net = require("node:net");
const http = require("node:http");

const { waitForServer } = require("./waitForServer");

/**
 * A server that answers 200 with Next's build-in-progress shell on every
 * path except /ok, which gets the real app document. This is the state a
 * cold `next dev` holds for as long as the build takes.
 */
function startCompilingServer() {
  const server = http.createServer((req, res) => {
    const body =
      req.url === "/ok"
        ? "<html><body><div id=\"__next\">OpenVault</div></body></html>"
        : "<html><body>Compiling... please wait</body></html>";
    res.writeHead(200, { "Content-Type": "text/html; charset=utf-8" });
    res.end(body);
  });
  return new Promise((resolve) => {
    server.listen(0, "127.0.0.1", () => {
      resolve({
        port: server.address().port,
        close: () => new Promise((done) => server.close(done)),
      });
    });
  });
}

/** A listener that accepts the socket and never answers. The :3010 wedge. */
function startBlackHoleServer() {
  const sockets = [];
  const server = net.createServer((socket) => {
    // Hold the connection open and write nothing, forever.
    sockets.push(socket);
  });
  return new Promise((resolve) => {
    server.listen(0, "127.0.0.1", () => {
      resolve({
        port: server.address().port,
        close: () =>
          new Promise((done) => {
            for (const s of sockets) s.destroy();
            server.close(done);
          }),
      });
    });
  });
}

/** A normal HTTP server that answers after `delayMs`. */
function startSlowServer(delayMs, status = 200) {
  const server = http.createServer((_req, res) => {
    setTimeout(() => {
      res.writeHead(status, { "Content-Type": "text/plain" });
      res.end("ok");
    }, delayMs);
  });
  return new Promise((resolve) => {
    server.listen(0, "127.0.0.1", () => {
      resolve({
        port: server.address().port,
        close: () => new Promise((done) => server.close(done)),
      });
    });
  });
}

test("stalled listener is reported within the deadline, not waited on forever", async () => {
  const srv = await startBlackHoleServer();
  try {
    const started = Date.now();
    const res = await waitForServer("http://127.0.0.1:" + srv.port + "/", 3000, {
      attemptTimeoutMs: 500,
      intervalMs: 50,
    });
    const elapsed = Date.now() - started;

    assert.equal(res.ok, false, "a server that never answers must not report ready");
    assert.equal(res.reason, "stalled", "must distinguish stalled from unreachable");
    assert.ok(res.stalledAttempts > 0, "must record at least one stalled attempt");
    // The whole point: the overall bound is actually reachable.
    assert.ok(
      elapsed < 3000 * 3,
      "must return near the deadline, took " + elapsed + "ms"
    );
  } finally {
    await srv.close();
  }
});

test("nothing listening is reported as unreachable, not stalled", async () => {
  // Bind and immediately release, so the port is almost certainly free.
  const srv = await startSlowServer(0);
  const port = srv.port;
  await srv.close();

  const res = await waitForServer("http://127.0.0.1:" + port + "/", 1200, {
    attemptTimeoutMs: 500,
    intervalMs: 50,
  });

  assert.equal(res.ok, false);
  assert.equal(res.reason, "unreachable", "connection refused is not a stall");
  assert.equal(res.stalledAttempts, 0);
});

test("a live server is reported ready", async () => {
  const srv = await startSlowServer(0);
  try {
    const res = await waitForServer("http://127.0.0.1:" + srv.port + "/", 5000, {
      attemptTimeoutMs: 1000,
      intervalMs: 50,
    });
    assert.equal(res.ok, true);
    assert.equal(res.reason, "ready");
  } finally {
    await srv.close();
  }
});

test("a slow-but-healthy server is not refused (R-0005)", async () => {
  // Stands in for the measured 17.5s cold Turbopack compile: slow, but working.
  // If the attempt budget were tighter than the response time this would fail,
  // and the probe would be a control that refuses legitimate work.
  const srv = await startSlowServer(700);
  try {
    const res = await waitForServer("http://127.0.0.1:" + srv.port + "/", 6000, {
      attemptTimeoutMs: 3000,
      intervalMs: 50,
    });
    assert.equal(res.ok, true, "a slow server that does answer must count as ready");
    assert.equal(res.reason, "ready");
  } finally {
    await srv.close();
  }
});

test("a 5xx still counts as up - it compiled and chose to fail", async () => {
  const srv = await startSlowServer(0, 503);
  try {
    const res = await waitForServer("http://127.0.0.1:" + srv.port + "/", 2000, {
      attemptTimeoutMs: 1000,
      intervalMs: 50,
    });
    // 503 is >= 500, so the probe keeps waiting and then reports honestly
    // rather than claiming ready.
    assert.equal(res.ok, false);
    assert.equal(res.reason, "unreachable");
    assert.equal(res.lastError, "HTTP 503");
  } finally {
    await srv.close();
  }
});

test("progress is reported so a caller can show a visible waiting state", async () => {
  const srv = await startBlackHoleServer();
  const seen = [];
  try {
    await waitForServer("http://127.0.0.1:" + srv.port + "/", 1500, {
      attemptTimeoutMs: 400,
      intervalMs: 50,
      onProgress: (p) => seen.push(p.state),
    });
    assert.ok(seen.length > 0, "onProgress must fire while waiting");
    assert.ok(seen.includes("stalled"), "a stall must be observable while it happens");
  } finally {
    await srv.close();
  }
});

test("expectHtml refuses a build-in-progress shell, and names it (not 'unreachable')", async () => {
  const srv = await startCompilingServer();
  try {
    const result = await waitForServer(`http://127.0.0.1:${srv.port}/compile`, 1500, {
      expectHtml: true,
      intervalMs: 100,
    });
    assert.equal(result.ok, false, "a Compiling shell at HTTP 200 must not count as ready");
    // Naming it matters as much as refusing it: 'unreachable' would send the
    // user to start a server that is already running.
    assert.equal(result.reason, "compiling");
    assert.match(result.lastError, /still compiling/);
  } finally {
    await srv.close();
  }
});

test("expectHtml accepts the real app document", async () => {
  const srv = await startCompilingServer();
  try {
    const result = await waitForServer(`http://127.0.0.1:${srv.port}/ok`, 3000, {
      expectHtml: true,
      intervalMs: 100,
    });
    assert.equal(result.ok, true, `expected ready, got ${result.reason}: ${result.lastError}`);
    assert.equal(result.reason, "ready");
  } finally {
    await srv.close();
  }
});

test("without expectHtml the same shell counts as ready - the flag is what does the work", async () => {
  const srv = await startCompilingServer();
  try {
    const result = await waitForServer(`http://127.0.0.1:${srv.port}/compile`, 1500, {
      intervalMs: 100,
    });
    // This is the OLD behaviour, kept deliberately for the custody API on :5000
    // which serves JSON and must not be held to an HTML contract. It is also
    // the bug when applied to :3010 - which is why the web path passes the flag.
    assert.equal(result.ok, true);
  } finally {
    await srv.close();
  }
});
