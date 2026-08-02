#!/usr/bin/env python3
"""Minimal FreeIDE local bridge stub for mesh bring-up / approval testing.

Binds 127.0.0.1:5100, announces to OpenVault, exposes /api/healthz.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HOST = os.environ.get("OPENIDE_HOST", "127.0.0.1")
PORT = int(os.environ.get("OPENIDE_PORT", "5100"))
OPENVAULT = os.environ.get("OPENVAULT_URL", "http://127.0.0.1:5000").rstrip("/")


def _post(url: str, payload: dict[str, object]) -> dict[str, object]:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=5) as resp:
        return json.loads(resp.read().decode("utf-8"))


def announce() -> None:
    try:
        result = _post(
            f"{OPENVAULT}/api/local/handshake",
            {
                "peer_kind": "openide",
                "name": "FreeIDE stub",
                "base_url": f"http://{HOST}:{PORT}",
                "capabilities": ["signin", "passkey", "editor", "stub"],
                "auto_approve": True,
            },
        )
        print("announced:", json.dumps(result.get("handshake", {}), indent=2))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        print("announce deferred (start OpenVault first):", exc)


class Handler(BaseHTTPRequestHandler):
    def _json(self, code: int, payload: dict[str, object]) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        if self.path in ("/api/healthz", "/health", "/"):
            self._json(
                200,
                {
                    "status": "ok",
                    "service": "openide-stub",
                    "openvault": OPENVAULT,
                },
            )
            return
        if self.path == "/api/openvault/ping":
            try:
                with urllib.request.urlopen(f"{OPENVAULT}/api/healthz", timeout=3) as resp:
                    remote = json.loads(resp.read().decode("utf-8"))
                self._json(200, {"ok": True, "openvault": remote})
            except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
                self._json(503, {"ok": False, "error": str(exc)})
            return
        self._json(404, {"error": "not found"})

    def log_message(self, fmt: str, *args: object) -> None:
        print(f"[openide-stub] {self.address_string()} {fmt % args}")


def main() -> None:
    announce()
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"FreeIDE stub on http://{HOST}:{PORT} → OpenVault {OPENVAULT}")
    server.serve_forever()


if __name__ == "__main__":
    main()
