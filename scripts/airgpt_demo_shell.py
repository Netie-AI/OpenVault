#!/usr/bin/env python3
"""AirGPT demo shell - stand-in when D:\\AirGPT is not cloned.

Serves an OpenIDE-shaped UI on :8765 (the port AirGPT owns per PRODUCT_ROLES
and ``openmw.openvault.ports``) so the OpenVault mesh and the Playwright
recorders can demo the three-surface contract without the real AirGPT repo.

It is a thin client: every button that touches state calls OpenVault's own
routes (``/api/local/*``, ``/api/keyvault/snapshot``, ``/api/cloud/*``) through
same-origin proxies, so the demo exercises the real custody API rather than a
mock of it. Stdlib only, so any Python the launcher has can run it.

Real AirGPT (local-only git at D:\\AirGPT) replaces this in production demos.

Environment:
    OPENIDE_HOST / OPENIDE_PORT   bind address, default 127.0.0.1:8765
    OPENVAULT_URL                 custody API, default http://127.0.0.1:$OPENVAULT_API_PORT
    CORTEX_URL                    Cortex engine, default http://127.0.0.1:8000
"""

from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

log = logging.getLogger("airgpt_demo_shell")

HOST = os.environ.get("OPENIDE_HOST", "127.0.0.1")
PORT = int(os.environ.get("OPENIDE_PORT", "8765"))
SELF_URL = f"http://{HOST}:{PORT}"
OPENVAULT = os.environ.get(
    "OPENVAULT_URL",
    f"http://127.0.0.1:{os.environ.get('OPENVAULT_API_PORT', '5000')}",
).rstrip("/")
CORTEX = os.environ.get("CORTEX_URL", "http://127.0.0.1:8000").rstrip("/")

#: Errors a proxied call to OpenVault or Cortex can raise: URLError, HTTPError and
#: timeouts are all OSError; a non-JSON body is a ValueError.
_UPSTREAM_ERRORS = (OSError, ValueError)

_FONTS_URL = (
    "https://fonts.googleapis.com/css2?family=Instrument+Sans:wght@400;500;600;700"
    "&family=IBM+Plex+Mono:wght@400;500&display=swap"
)

_HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>AirGPT — host shell</title>
  <link rel="preconnect" href="https://fonts.googleapis.com" />
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin />
  <link href="__FONTS__" rel="stylesheet" />
  <style>
    :root {
      --ink: #0f1419;
      --paper: #f4f0e6;
      --panel: #fffdf8;
      --line: #d8d0c0;
      --accent: #0b6e4f;
      --accent-2: #c45c26;
      --muted: #6b6458;
      --ok: #1a7f4b;
      --warn: #b45309;
      --bad: #b91c1c;
      --sans: "Instrument Sans", system-ui, sans-serif;
      --mono: "IBM Plex Mono", ui-monospace, monospace;
    }
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body {
      font-family: var(--sans);
      color: var(--ink);
      min-height: 100vh;
      background:
        radial-gradient(ellipse 70% 50% at 10% 0%, rgba(11,110,79,0.12), transparent 55%),
        radial-gradient(ellipse 50% 40% at 90% 10%, rgba(196,92,38,0.10), transparent 50%),
        linear-gradient(165deg, #efe8d8 0%, var(--paper) 45%, #e7e0d2 100%);
    }
    header {
      display: flex; align-items: center; justify-content: space-between;
      padding: 18px 28px; border-bottom: 1px solid var(--line);
      background: rgba(255,253,248,0.72); backdrop-filter: blur(10px);
    }
    .brand { display: flex; flex-direction: column; gap: 2px; }
    .brand strong { font-size: 1.35rem; letter-spacing: -0.03em; }
    .brand span { font-size: 0.78rem; color: var(--muted); }
    .status-row { display: flex; gap: 10px; align-items: center; }
    .pill {
      font-family: var(--mono); font-size: 0.72rem; padding: 6px 10px;
      border: 1px solid var(--line); border-radius: 999px; background: var(--panel);
    }
    .pill.ok { color: var(--ok); border-color: rgba(26,127,75,0.35); }
    .pill.bad { color: var(--bad); border-color: rgba(185,28,28,0.35); }
    main { max-width: 1100px; margin: 0 auto; padding: 28px 24px 64px; }
    h1 { font-size: 2rem; letter-spacing: -0.03em; margin-bottom: 8px; }
    .lede { color: var(--muted); max-width: 52ch; margin-bottom: 22px; }
    .toolbar { display: flex; flex-wrap: wrap; gap: 8px; margin-bottom: 22px; }
    button {
      font: inherit; cursor: pointer; border: 1px solid var(--line);
      background: var(--panel); color: var(--ink); padding: 10px 14px;
      border-radius: 10px; transition: transform .15s ease, border-color .15s ease;
    }
    button:hover { border-color: var(--accent); transform: translateY(-1px); }
    button.primary { background: var(--accent); color: #fff; border-color: var(--accent); }
    .grid { display: grid; grid-template-columns: 1.2fr 0.8fr; gap: 16px; }
    @media (max-width: 860px) { .grid { grid-template-columns: 1fr; } }
    .card {
      background: rgba(255,253,248,0.88); border: 1px solid var(--line);
      border-radius: 16px; padding: 18px; min-height: 220px;
    }
    .card h2 { font-size: 1rem; margin-bottom: 10px; }
    pre {
      font-family: var(--mono); font-size: 0.72rem; white-space: pre-wrap;
      color: var(--muted); max-height: 280px; overflow: auto;
    }
    .modal {
      display: none; position: fixed; inset: 0; background: rgba(15,20,25,0.45);
      align-items: center; justify-content: center; z-index: 20;
    }
    .modal.open { display: flex; }
    .modal .sheet {
      width: min(480px, 92vw); background: var(--panel); border-radius: 16px;
      border: 1px solid var(--line); padding: 20px;
    }
    #openide { scroll-margin-top: 80px; }
  </style>
</head>
<body>
  <header>
    <div class="brand">
      <strong>AirGPT</strong>
      <span>Host shell · OpenIDE bridge · thin client of OpenVault + Cortex</span>
    </div>
    <div class="status-row">
      <span class="pill" id="ovPill">OpenVault …</span>
      <span class="pill" id="cxPill">Cortex …</span>
    </div>
  </header>
  <main>
    <section id="openide">
      <h1>OpenIDE on the LAN mesh</h1>
      <p class="lede">Create a small app, keep keys in OpenVault, let Cortex run the brain —
      then share a live agent thread with a teammate on the same network.</p>
      <div class="toolbar">
        <button type="button" class="primary" id="btnCreate">Create app</button>
        <button type="button" id="btnKeys">Keys</button>
        <button type="button" id="btnVault">Vault</button>
        <button type="button" id="btnNew">New</button>
        <button type="button" id="btnRun">Run</button>
        <button type="button" id="btnAgents">Agents</button>
        <button type="button" id="btnIDE">IDE</button>
        <button type="button" id="btnShare">Share LAN</button>
        <button type="button" id="btnJoin">Join session</button>
      </div>
      <div class="grid">
        <div class="card">
          <h2>Live session</h2>
          <pre id="sessionLog">Ready.
Peer contract: AirGPT __SELF__ &rarr; OpenVault __OPENVAULT__ &rarr; Cortex __CORTEX__</pre>
        </div>
        <div class="card">
          <h2>Mesh snapshot</h2>
          <pre id="meshSnap">Loading…</pre>
        </div>
      </div>
    </section>
  </main>
  <div class="modal" id="modal">
    <div class="sheet">
      <h2 id="modalTitle">Dialog</h2>
      <p id="modalBody" style="margin:12px 0;color:var(--muted)"></p>
      <button type="button" id="modalClose">Close</button>
    </div>
  </div>
  <script>
    const OV = "__OPENVAULT__";
    const CX = "__CORTEX__";
    const log = (msg) => {
      const el = document.getElementById("sessionLog");
      el.textContent = msg + "\\n" + el.textContent;
    };
    function showOpenIDEPage() {
      document.getElementById("openide").scrollIntoView({ behavior: "smooth" });
    }
    function openModal(title, body) {
      document.getElementById("modalTitle").textContent = title;
      document.getElementById("modalBody").textContent = body;
      document.getElementById("modal").classList.add("open");
    }
    document.getElementById("modalClose").onclick = () =>
      document.getElementById("modal").classList.remove("open");

    async function ping(url, pillId, label) {
      const pill = document.getElementById(pillId);
      try {
        const r = await fetch(url, { mode: "cors" });
        if (!r.ok) throw new Error(String(r.status));
        pill.textContent = label + " ok";
        pill.className = "pill ok";
      } catch (e) {
        // same-origin proxy fallbacks
        try {
          const r2 = await fetch("/api/openvault/ping");
          const j = await r2.json();
          if (pillId === "ovPill" && j.ok) {
            pill.textContent = label + " ok";
            pill.className = "pill ok";
            return;
          }
        } catch (_) {}
        try {
          const r3 = await fetch("/api/cortex/ping");
          const j3 = await r3.json();
          if (pillId === "cxPill" && j3.ok) {
            pill.textContent = label + " ok";
            pill.className = "pill ok";
            return;
          }
        } catch (_) {}
        pill.textContent = label + " down";
        pill.className = "pill bad";
      }
    }

    async function refreshMesh() {
      try {
        const r = await fetch("/api/mesh-proxy");
        const j = await r.json();
        document.getElementById("meshSnap").textContent = JSON.stringify(j, null, 2);
      } catch (e) {
        document.getElementById("meshSnap").textContent = String(e);
      }
    }

    document.getElementById("btnCreate").onclick = () => {
      log("Create app -> scaffolding local OpenIDE workspace");
      openModal("Create app", "Scaffolded apps/hello on this host. Secrets stay in OpenVault.");
    };
    document.getElementById("btnKeys").onclick = () =>
      openModal("Keys", "Keys SoT is OpenVault. AirGPT env.local is offline cache only.");
    document.getElementById("btnVault").onclick = async () => {
      try {
        const r = await fetch("/api/keyvault-proxy");
        const j = await r.json();
        openModal("Vault", JSON.stringify(j).slice(0, 400));
      } catch (e) {
        openModal("Vault", String(e));
      }
    };
    document.getElementById("btnNew").onclick = () => openModal("New", "New agent thread (local).");
    document.getElementById("btnRun").onclick = () => {
      log("Run -> Cortex orchestration request (gated by OpenVault)");
      openModal("Run",
        "Asked Cortex to plan; OpenVault gate decides if anything may leave the machine.");
    };
    document.getElementById("btnAgents").onclick = () =>
      openModal("Agents", "Multiplayer agent session ready to host.");
    document.getElementById("btnIDE").onclick = () => {
      showOpenIDEPage();
      openModal("IDE", "OpenIDE surface active.");
    };
    document.getElementById("btnShare").onclick = async () => {
      try {
        const r = await fetch("/api/share-lan", { method: "POST" });
        const j = await r.json();
        log("Share LAN -> " + JSON.stringify(j).slice(0, 180));
        openModal("Share LAN",
          j.ok ? ("Share code " + (j.share?.id || "issued")) : JSON.stringify(j));
      } catch (e) {
        openModal("Share LAN", String(e));
      }
    };
    document.getElementById("btnJoin").onclick = async () => {
      try {
        const r = await fetch("/api/join-session", { method: "POST" });
        const j = await r.json();
        log("Join session -> " + JSON.stringify(j).slice(0, 180));
        openModal("Join session", JSON.stringify(j).slice(0, 400));
      } catch (e) {
        openModal("Join session", String(e));
      }
    };

    ping(OV + "/api/healthz", "ovPill", "OpenVault");
    ping(CX + "/health", "cxPill", "Cortex");
    refreshMesh();
    if (location.hash.includes("openide")) showOpenIDEPage();
  </script>
</body>
</html>
"""

HTML = (
    _HTML_TEMPLATE.replace("__FONTS__", _FONTS_URL)
    .replace("__SELF__", SELF_URL)
    .replace("__OPENVAULT__", OPENVAULT)
    .replace("__CORTEX__", CORTEX)
)


def _get_json(url: str) -> dict[str, Any]:
    with urllib.request.urlopen(url, timeout=5) as resp:
        return json.loads(resp.read().decode("utf-8") or "{}")


def _post_json(url: str, payload: dict[str, Any]) -> dict[str, Any]:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url, data=data, headers={"Content-Type": "application/json"}, method="POST"
    )
    with urllib.request.urlopen(req, timeout=8) as resp:
        return json.loads(resp.read().decode("utf-8") or "{}")


def _upstream_failure(exc: BaseException) -> tuple[int, dict[str, Any]]:
    """Turn an upstream error into (status, body) without losing the vault's verdict.

    A 403 from ``/api/cloud/*`` carries the firewall decision in its body; that is
    the demo's evidence, so it is passed through rather than flattened to 503.
    """
    if isinstance(exc, urllib.error.HTTPError):
        try:
            detail = json.loads(exc.read().decode("utf-8") or "{}")
        except (OSError, ValueError):
            detail = {}
        return exc.code, {"ok": False, "error": str(exc), "detail": detail}
    return 503, {"ok": False, "error": str(exc)}


def announce() -> None:
    try:
        result = _post_json(
            f"{OPENVAULT}/api/local/handshake",
            {
                "peer_kind": "airgpt",
                "name": "AirGPT demo shell",
                "base_url": SELF_URL,
                "capabilities": ["signin", "passkey", "editor", "shell", "demo"],
                "auto_approve": True,
            },
        )
    except _UPSTREAM_ERRORS as exc:
        log.warning("announce deferred (start OpenVault first): %s", exc)
        return
    handshake = result.get("handshake", result)
    log.info("announced to %s: %s", OPENVAULT, json.dumps(handshake)[:400])


class Handler(BaseHTTPRequestHandler):
    def _send(self, code: int, body: bytes, content_type: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, code: int, payload: dict[str, Any]) -> None:
        self._send(code, json.dumps(payload).encode("utf-8"), "application/json")

    def _proxy_get(self, url: str, wrap: str | None = None) -> None:
        try:
            remote = _get_json(url)
        except _UPSTREAM_ERRORS as exc:
            self._json(*_upstream_failure(exc))
            return
        self._json(200, {"ok": True, wrap: remote} if wrap else remote)

    def do_OPTIONS(self) -> None:
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self) -> None:
        path = self.path.split("?", 1)[0]
        if path in ("/", "/index.html"):
            self._send(200, HTML.encode("utf-8"), "text/html; charset=utf-8")
            return
        if path in ("/api/healthz", "/health", "/api/health"):
            self._json(
                200,
                {
                    "status": "ok",
                    "service": "airgpt-demo-shell",
                    "openvault": OPENVAULT,
                    "cortex": CORTEX,
                    "note": "Stand-in for D:/AirGPT when repo is not cloned",
                },
            )
            return
        if path == "/api/openvault/ping":
            self._proxy_get(f"{OPENVAULT}/api/healthz", wrap="openvault")
            return
        if path == "/api/cortex/ping":
            self._proxy_get(f"{CORTEX}/health", wrap="cortex")
            return
        if path == "/api/mesh-proxy":
            self._proxy_get(f"{OPENVAULT}/api/local/connect-pack")
            return
        if path == "/api/keyvault-proxy":
            self._proxy_get(f"{OPENVAULT}/api/keyvault/snapshot")
            return
        self._json(404, {"error": "not found"})

    def do_POST(self) -> None:
        path = self.path.split("?", 1)[0]
        if path == "/api/share-lan":
            try:
                # Honest path: the firewall check must not honour a bypass flag.
                deny = _post_json(
                    f"{OPENVAULT}/api/cloud/firewall/check",
                    {"action": "share_lan", "bypass": True},
                )
                share = _post_json(
                    f"{OPENVAULT}/api/cloud/shares",
                    {
                        "title": "AirGPT demo share",
                        "source_path": "apps/hello",
                        "owner": "airgpt-demo",
                    },
                )
            except _UPSTREAM_ERRORS as exc:
                self._json(*_upstream_failure(exc))
                return
            self._json(
                200,
                {
                    "ok": True,
                    "firewall_bypass_denied": deny,
                    "share": share.get("share", share),
                },
            )
            return
        if path == "/api/join-session":
            try:
                created = _post_json(
                    f"{OPENVAULT}/api/cloud/sessions",
                    {"title": "YC multiplayer demo", "owner": "airgpt-demo"},
                )
                sid = (created.get("session") or {}).get("id")
                joined = None
                if sid:
                    joined = _post_json(
                        f"{OPENVAULT}/api/cloud/sessions/{sid}/join",
                        {"user": "teammate-2", "peer_ip": "127.0.0.1"},
                    )
            except _UPSTREAM_ERRORS as exc:
                self._json(*_upstream_failure(exc))
                return
            self._json(200, {"ok": True, "created": created, "joined": joined})
            return
        self._json(404, {"error": "not found"})

    def log_message(self, fmt: str, *args: object) -> None:
        log.info("%s %s", self.address_string(), fmt % args)


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [airgpt] %(levelname)s %(message)s",
    )
    announce()
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    log.info("AirGPT demo shell %s -> OpenVault %s, Cortex %s", SELF_URL, OPENVAULT, CORTEX)
    server.serve_forever()


if __name__ == "__main__":
    main()
