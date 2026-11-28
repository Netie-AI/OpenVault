#!/usr/bin/env python3
"""Tenant, metering, and cap sim against the real gateway and a stub upstream.

Forces a temporary OPENVAULT_HOME. Does not read or write the operator vault.
Does not print tokens, questions, or the admin credential.

Default shape is 3 tenants x 2 seats so CI stays bounded. The 2026-11-28
readiness run was 3 tenants x 50 users; set OV_SIM_SEATS to repeat that size.
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch


def _scrub(text: str) -> str:
    out: list[str] = []
    for token in text.split():
        if token.startswith("ov_") or token.startswith("ovm_") or token.startswith("sk-"):
            out.append("[redacted]")
        else:
            out.append(token)
    return " ".join(out)


def _fail(name: str, status: int, detail: str = "") -> None:
    raise SystemExit(f"seat-sim fail {name} status={status} {_scrub(detail)[:180]}")


def _need(name: str, response: Any, status: int) -> Any:
    if response.status_code != status:
        detail = ""
        try:
            body = response.json()
            err = body.get("error") or body.get("detail") or ""
            detail = str(err)
        except Exception:
            detail = ""
        _fail(name, response.status_code, detail)
    if status == 204:
        return None
    content = response.headers.get("content-type", "")
    if content.startswith("text/csv"):
        return response.text
    return response.json()


def _stub() -> MagicMock:
    resp = MagicMock()
    resp.status_code = 200
    resp.text = "{}"
    resp.headers = {}
    resp.json = MagicMock(
        return_value={
            "id": "sim",
            "choices": [],
            "usage": {"prompt_tokens": 3, "completion_tokens": 4, "total_tokens": 7},
        }
    )
    mock = MagicMock()
    mock.post = AsyncMock(return_value=resp)
    mock.__aenter__ = AsyncMock(return_value=mock)
    mock.__aexit__ = AsyncMock(return_value=None)
    return mock


def main() -> int:
    seats = int(os.environ.get("OV_SIM_SEATS") or "2")
    if seats < 1 or seats > 50:
        raise SystemExit("seat-sim fail OV_SIM_SEATS must be from 1 to 50")
    home = Path(tempfile.mkdtemp(prefix="ov-seat-sim-"))
    os.environ["OPENVAULT_HOME"] = str(home)
    os.environ["OV_SEAT_PLANE"] = "1"
    os.environ.pop("OV_SEAT_POLICY", None)
    os.environ.pop("OPENVAULT_ROUTE_POLICY", None)
    os.environ.pop("OPENVAULT_REDIS_URL", None)
    try:
        from cryptography.fernet import Fernet
        from fastapi.testclient import TestClient
        from openmw.openvault.app import create_app
        from openmw.openvault.vault.admin_token import admin_headers
        from openmw.openvault.vault.crypto import Seal
        from openmw.openvault.vault.seat_plane import MANAGER_HEADER
        from openmw.openvault.vault.store import KeyVault

        vault = KeyVault(db_path=home / "keys.db", seal=Seal(Fernet.generate_key()))
        rec = vault.create(
            label="sim-groq",
            provider="groq",
            secret="sk-test-sim-stub-only",
            role="primary",
            priority=0,
            base_url="https://example.invalid/v1",
        )
        vault.set_precheck(rec.id, status="ok", latency_ms=1.0, error=None)
        app = create_app(
            vault=vault,
            mock_health=True,
            enable_precheck_loop=False,
            cortex_url="http://127.0.0.1:9",
        )
        admin = admin_headers()
        client = TestClient(app, client=("127.0.0.1", 5555))
        mock = _stub()
        tenants: list[dict[str, Any]] = []
        with patch("openmw.openvault.vault.proxy.httpx.AsyncClient", return_value=mock):
            for index in range(3):
                account = _need(
                    "account",
                    client.post(
                        "/api/accounts",
                        json={
                            "display_name": f"Sim {index}",
                            "auth_provider": "netie_email",
                            "local_part": f"sim{index}",
                        },
                    ),
                    200,
                )
                tenant = _need(
                    "tenant",
                    client.post(
                        "/api/system/tenant/tenants",
                        json={"account_id": account["id"], "label": f"t{index}"},
                    ),
                    200,
                )["tenant"]
                manager = _need(
                    "manager",
                    client.post(
                        "/api/system/tenant/managers",
                        json={"tenant_id": tenant["tenant_id"], "label": "desk"},
                    ),
                    200,
                )
                seat_ids: list[str] = []
                key_headers: list[dict[str, str]] = []
                for seat_index in range(seats):
                    seat = _need(
                        "seat",
                        client.post(
                            "/api/system/tenant/seats",
                            json={"tenant_id": tenant["tenant_id"], "label": f"s{seat_index}"},
                        ),
                        200,
                    )["seat"]
                    issued = _need(
                        "apikey",
                        client.post(
                            "/api/apikeys",
                            json={"label": f"t{index}s{seat_index}", "tier": "free"},
                            headers=admin,
                        ),
                        200,
                    )
                    _need(
                        "bind",
                        client.post(
                            "/api/system/tenant/bind",
                            json={
                                "seat_id": seat["seat_id"],
                                "api_key_id": issued["key"]["key_id"],
                            },
                        ),
                        200,
                    )
                    seat_ids.append(seat["seat_id"])
                    key_headers.append({"Authorization": f"Bearer {issued['token']}"})
                    chat = _need(
                        "chat",
                        client.post(
                            "/v1/chat/completions",
                            json={
                                "model": "auto",
                                "messages": [{"role": "user", "content": "seat ping"}],
                            },
                            headers=key_headers[-1],
                        ),
                        200,
                    )
                    if chat.get("error"):
                        _fail("chat-body", 200, str(chat.get("error")))
                tenants.append(
                    {
                        "account_id": account["id"],
                        "tenant_id": tenant["tenant_id"],
                        "manager": {MANAGER_HEADER: manager["token"]},
                        "seats": seat_ids,
                        "keys": key_headers,
                    }
                )
            left = tenants[0]
            right = tenants[1]
            meter = _need(
                "meter",
                client.get(
                    "/api/system/metering",
                    params={"account_id": left["account_id"]},
                    headers=left["manager"],
                ),
                200,
            )
            if meter["ledger_scope"] != "tenant" or meter["ledger"]["requests"] != seats:
                _fail("meter-scope", 200, "ledger was not this tenant")
            if (
                client.get(
                    "/api/system/metering",
                    params={"account_id": right["account_id"]},
                    headers=left["manager"],
                ).status_code
                != 404
            ):
                _fail("meter-isolation", 200, "other tenant was visible")
            usage = _need(
                "usage",
                client.get("/api/usage", headers=left["manager"]),
                200,
            )
            if usage["totals"]["tenant"]["requests"] != seats:
                _fail("usage-total", 200, "tenant total did not match seats")
            if any(row.get("route_role") is None for row in usage["events"]):
                _fail("usage-role", 200, "role missing")
            if "est_cost_usd" not in usage["events"][0]:
                _fail("usage-cost", 200, "cost missing")
            capped = _need(
                "cap",
                client.post(
                    f"/api/system/tenant/seats/{left['seats'][0]}/caps",
                    json={"ceiling_usd": 0},
                    headers=left["manager"],
                ),
                200,
            )
            if "caps" not in capped:
                _fail("cap-body", 200, "caps missing")
            before = mock.post.await_count
            blocked = client.post(
                "/v1/chat/completions",
                json={"model": "auto", "messages": [{"role": "user", "content": "seat ping"}]},
                headers=left["keys"][0],
            )
            if blocked.status_code != 402:
                _fail("ceiling", blocked.status_code)
            if mock.post.await_count != before:
                _fail("ceiling-upstream", blocked.status_code, "stub was called")
            _need(
                "raise",
                client.post(
                    f"/api/system/tenant/seats/{left['seats'][0]}/caps",
                    json={"ceiling_usd": 25},
                    headers=left["manager"],
                ),
                200,
            )
            audit = _need(
                "audit",
                client.get("/api/system/tenant/audit", headers=left["manager"]),
                200,
            )
            changes = [row for row in audit["audit"] if row["action"] == "seat_caps"]
            if len(changes) < 2:
                _fail("audit", 200, "cap changes were not logged")
            if changes[0]["old"]["ceiling_usd"] != 0 or changes[0]["new"]["ceiling_usd"] != 25:
                _fail("audit-values", 200, "old or new cap was wrong")
            if "ovm_" in str(changes):
                _fail("audit-secret", 200, "token landed in the audit")
            _need(
                "reopen",
                client.post(
                    "/v1/chat/completions",
                    json={"model": "auto", "messages": [{"role": "user", "content": "seat ping"}]},
                    headers=left["keys"][0],
                ),
                200,
            )
            policy_path = home / "bad-seat-policy.json"
            policy_path.write_text("{", encoding="utf-8")
            os.environ["OV_SEAT_POLICY"] = str(policy_path)
            from openmw.openvault.vault.seat_plane import reset_seat_policy_cache

            reset_seat_policy_cache()
            _need(
                "bad-policy-chat",
                client.post(
                    "/v1/chat/completions",
                    json={"model": "auto", "messages": [{"role": "user", "content": "seat ping"}]},
                    headers=left["keys"][1] if seats > 1 else left["keys"][0],
                ),
                200,
            )
            reported = _need("policy", client.get("/api/system/tenant/policy"), 200)
            if not reported.get("policy_error"):
                _fail("policy-error", 200, "bad file was not reported")
            quiet = _need(
                "questions-off",
                client.get("/api/system/tenant/questions", headers=left["manager"]),
                200,
            )
            if quiet["enabled"] or quiet["questions"]:
                _fail("capture-default", 200, "capture was on")
            _need(
                "capture-on",
                client.post(
                    "/api/system/tenant/capture",
                    json={"tenant_id": left["tenant_id"], "enabled": True, "retention_days": 14},
                    headers=left["manager"],
                ),
                200,
            )
            _need(
                "capture-chat",
                client.post(
                    "/v1/chat/completions",
                    json={"model": "auto", "messages": [{"role": "user", "content": "seat ping"}]},
                    headers=left["keys"][0],
                ),
                200,
            )
            questions = _need(
                "questions",
                client.get("/api/system/tenant/questions", headers=left["manager"]),
                200,
            )
            if seats > 1 and not questions["questions"]:
                _fail("capture", 200, "question was not stored")
            if (
                client.get(
                    "/api/system/tenant/questions",
                    params={"tenant_id": left["tenant_id"]},
                    headers=right["manager"],
                ).status_code
                != 404
            ):
                _fail("capture-scope", 200, "other manager saw questions")
            bill = _need(
                "bill",
                client.get(
                    "/api/system/tenant/billing",
                    params={"month": "2026-11", "format": "json"},
                    headers=right["manager"],
                ),
                200,
            )
            if bill["markup"] != 1.2:
                _fail("markup", 200, "default markup was not 1.2")
        print(f"seat-sim ok tenants=3 seats={seats} upstream_calls={mock.post.await_count}")
        return 0
    finally:
        shutil.rmtree(home, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
