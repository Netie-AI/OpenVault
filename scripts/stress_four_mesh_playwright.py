"""Stress OpenVault + AirGPT FreeIDE buttons via Playwright.

Blocks/warns are asserted for bypass attempts.
Requires: OpenVault :5000, AirGPT :8765, playwright installed.

  python D:\\OpenVault\\scripts\\stress_four_mesh_playwright.py
"""

from __future__ import annotations

import json
import sys
import urllib.request
from typing import Any

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
except Exception:
    pass

BASE_OV = "http://127.0.0.1:5000"
BASE_IDE = "http://127.0.0.1:8765"


def http_json(method: str, url: str, body: dict | None = None) -> dict[str, Any]:
    data = None
    headers = {"Accept": "application/json"}
    if body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=12) as resp:
        return json.loads(resp.read().decode() or "{}")


def safe(s: str) -> str:
    return (s or "").encode("ascii", "replace").decode("ascii")


def main() -> int:
    failures: list[str] = []
    print("== API stress ==")
    for label, url in [
        ("openvault", f"{BASE_OV}/api/healthz"),
        ("cortex", "http://127.0.0.1:8000/health"),
        ("airgpt", f"{BASE_IDE}/"),
    ]:
        try:
            urllib.request.urlopen(url, timeout=5)
            print(f"OK {label}")
        except Exception as e:
            failures.append(f"{label} down: {e}")
            print(f"FAIL {label}: {e}")

    deny = http_json("POST", f"{BASE_OV}/api/cloud/firewall/check", {"action": "share_lan", "bypass": True})
    if deny.get("allowed") is not False:
        failures.append("bypass was allowed — SECURITY FAIL")
    else:
        print("OK bypass denied:", safe((deny.get("reasons") or [""])[0][:90]))

    gate = http_json("POST", f"{BASE_OV}/api/gate/check", {"action": "deploy", "force": True})
    if gate.get("allowed") is not False:
        failures.append("gate force bypass allowed — SECURITY FAIL")
    else:
        print("OK gate force denied")

    share = http_json(
        "POST",
        f"{BASE_OV}/api/cloud/shares",
        {"title": "Stress Share App", "source_path": "apps/hello", "owner": "stress"},
    )
    if not share.get("ok"):
        failures.append("share publish failed")
    else:
        print("OK share", share["share"]["id"])

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("SKIP playwright UI (not installed)")
        return 1 if failures else 0

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(f"http://127.0.0.1:3010/peers", wait_until="domcontentloaded", timeout=15000)
        page.wait_for_timeout(600)
        tabs = [
            "Detection",
            "Data Flow",
            "Bottleneck",
            "Middleware Gain",
            "Routing",
            "Vault",
            "Accounts",
            "Local Mesh",
            "Engine",
            "Deploy",
        ]
        for name in tabs:
            try:
                page.get_by_role("button", name=name).click(timeout=2000)
                page.wait_for_timeout(200)
                print("click OV tab", name)
            except Exception as e:
                print("warn OV tab", name, safe(str(e)[:80]))

        page.goto(f"{BASE_IDE}/#openide", wait_until="domcontentloaded", timeout=15000)
        page.wait_for_timeout(800)
        page.keyboard.press("Escape")
        page.evaluate(
            """() => {
              document.querySelectorAll('.modal.open').forEach(m=>m.classList.remove('open'));
              if (typeof showOpenIDEPage==='function') showOpenIDEPage();
            }"""
        )
        page.wait_for_timeout(700)
        for name in ("Create app", "Keys", "Vault", "New", "Run", "Agents", "IDE"):
            try:
                loc = page.get_by_role("button", name=name)
                if loc.count() == 0:
                    continue
                loc.first.click(timeout=2000)
                page.wait_for_timeout(200)
                page.keyboard.press("Escape")
                print("click IDE", name)
            except Exception as e:
                print("warn IDE", name, safe(str(e)[:80]))
        browser.close()

    if failures:
        print("FAILURES:")
        for f in failures:
            print("-", safe(f))
        return 1
    print("ALL STRESS CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
