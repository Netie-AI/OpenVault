#!/usr/bin/env python3
"""Scripted one-seat demo path (auto-safe; mocks / simulate only).

Parent: Netie-AI/OpenVault#18 / ticket #32.

Path: vault a fake key -> FreeRoute empty + sealed refuse (not live paid keys) ->
gated ship allow under local_demo/simulate (labeled; no fake host URL) ->
gate deny with refusal in the same evidence file.

Does NOT exercise HT1-HT5 (live CF/Coolify/Netlify, live FreeRoute spend,
unseal UX, Cortex Phase 0, live secrets-at-ship). Those are human-only.

Usage (from OpenMW/):
  uv run python scripts/one_seat_demo.py
  uv run python scripts/one_seat_demo.py --out .demo_evidence/one_seat.json

Or from repo root:
  python apps/cli/openvault_cli.py demo-path
"""

from __future__ import annotations

import argparse
import json
import os
import tempfile
import time
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from openmw.openvault.app import create_app
from openmw.openvault.ship.engine import run_ship_engine
from openmw.openvault.vault.accounts import AccountStore
from openmw.openvault.vault.crypto import Seal
from openmw.openvault.vault.store import KeyVault

_CHAT = {
    "model": "auto",
    "messages": [{"role": "user", "content": "hi"}],
}
_FAKE_SECRET = "sk-demo-one-seat-not-a-real-key-aaaaaaaa"


def _step(evidence: dict[str, Any], name: str, **payload: Any) -> None:
    entry = {"step": name, "ts": time.time(), **payload}
    evidence["steps"].append(entry)
    status = payload.get("status", "ok")
    print(f"[{status}] {name}: {payload.get('summary', '')}")


def _finish(evidence: dict[str, Any], out_path: Path | None) -> dict[str, Any]:
    if out_path is not None:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
        print(f"evidence written: {out_path}")
    return evidence


def run_demo(*, out_path: Path | None) -> dict[str, Any]:
    evidence: dict[str, Any] = {
        "ticket": "Netie-AI/OpenVault#32",
        "mode": "auto-safe",
        "label": "SIMULATE / local_demo -- not a live host URL; not HT1-HT5",
        "claims": {
            "live_host_deploy": False,
            "live_freeroute_paid_keys": False,
            "ht1_ht5": "human-only -- not claimed complete",
        },
        "steps": [],
        "ok": False,
    }

    # ignore_cleanup_errors: Windows often holds SQLite locks briefly after close.
    with tempfile.TemporaryDirectory(
        prefix="ov-one-seat-", ignore_cleanup_errors=True
    ) as tmp:
        home = Path(tmp) / "ovhome"
        home.mkdir()
        project = Path(tmp) / "web"
        project.mkdir()
        (project / "package.json").write_text(
            '{"name":"one-seat-demo"}', encoding="utf-8"
        )

        os.environ["OPENVAULT_HOME"] = str(home)
        os.environ["OPENSHIP_MODE"] = "simulate"

        # key_path so lock() + unseal() round-trip without passphrase UX (not HT3).
        seal = Seal(key_path=home / "master.key")
        vault = KeyVault(db_path=home / "keys.db", seal=seal)
        accounts = AccountStore(db_path=home / "accounts.db")
        app = create_app(
            vault=vault,
            accounts=accounts,
            mock_health=True,
            enable_precheck_loop=False,
            cortex_url="http://127.0.0.1:9",
        )
        with TestClient(app, client=("127.0.0.1", 5555)) as client:
            return _run_steps(client, vault, project, evidence, out_path)


def _run_steps(
    client: TestClient,
    vault: KeyVault,
    project: Path,
    evidence: dict[str, Any],
    out_path: Path | None,
) -> dict[str, Any]:
    # FreeRoute no longer believes a caller's headers about who it is or what
    # tier it gets. The demo holds a real issued key, exactly like the buyer's
    # app would -- and the key id is the identity the ledger attributes to.
    issued = client.post(
        "/api/apikeys", json={"label": "one-seat-demo", "tier": "free"}
    ).json()
    fr_headers = {"Authorization": f"Bearer {issued['token']}"}
    demo_key_id = issued["key"]["key_id"]

    # --- 1. FreeRoute empty refuse (auto-safe) ---
    empty = client.post("/v1/chat/completions", json=_CHAT, headers=fr_headers)
    empty_body = empty.json()
    empty_ok = (
        empty.status_code == 503
        and empty_body.get("error", {}).get("type") == "openvault_no_keys"
    )
    _step(
        evidence,
        "freeroute_empty_refuse",
        status="ok" if empty_ok else "FAIL",
        summary="empty vault pool refuses chat (no live keys)",
        http_status=empty.status_code,
        error_type=empty_body.get("error", {}).get("type"),
        auto_safe=True,
    )
    if not empty_ok:
        return _finish(evidence, out_path)

    # --- 2. Vault a fake key ---
    created = client.post(
        "/api/keys",
        json={
            "label": "one-seat-demo",
            "provider": "openai",
            "secret": _FAKE_SECRET,
            "role": "free",
            "base_url": "https://example.invalid/v1",
        },
    )
    created_body = (
        created.json()
        if created.headers.get("content-type", "").startswith("application/json")
        else {}
    )
    key_id = created_body.get("id", "")
    vault_ok = created.status_code == 200 and bool(key_id)
    leaked = _FAKE_SECRET in created.text
    _step(
        evidence,
        "vault_key",
        status="ok" if vault_ok and not leaked else "FAIL",
        summary="vaulted fake key (never a live provider secret)",
        http_status=created.status_code,
        key_id=key_id,
        secret_echoed=leaked,
    )
    if not vault_ok or leaked:
        return _finish(evidence, out_path)

    vault.set_precheck(key_id, status="ok", latency_ms=1.0, error=None)

    # --- 3. FreeRoute sealed refuse (auto-safe) ---
    vault.seal.lock()
    assert vault.seal.is_sealed
    # Fresh key = fresh budget, so a drained bucket cannot mask the sealed path.
    sealed_issued = client.post(
        "/api/apikeys", json={"label": "one-seat-sealed", "tier": "free"}
    ).json()
    sealed = client.post(
        "/v1/chat/completions",
        json=_CHAT,
        headers={"Authorization": f"Bearer {sealed_issued['token']}"},
    )
    sealed_body = sealed.json()
    sealed_ok = (
        sealed.status_code == 403
        and sealed_body.get("error", {}).get("type") == "openvault_vault_sealed"
        and _FAKE_SECRET not in sealed.text
    )
    _step(
        evidence,
        "freeroute_sealed_refuse",
        status="ok" if sealed_ok else "FAIL",
        summary="sealed vault refuses FreeRoute (no plaintext leak)",
        http_status=sealed.status_code,
        error_type=sealed_body.get("error", {}).get("type"),
        auto_safe=True,
    )
    if not sealed_ok:
        return _finish(evidence, out_path)

    # Re-open for ship allow (in-process; not HT3 passphrase UX).
    vault.seal.unseal()
    assert not vault.seal.is_sealed

    gate_open = client.post("/api/gate/check", json={"action": "deploy"})
    gate_body = gate_open.json()
    allow_ready = (
        gate_open.status_code == 200
        and gate_body.get("allowed") is True
        and gate_body.get("keys_ready") is True
    )
    _step(
        evidence,
        "gate_allow_check",
        status="ok" if allow_ready else "FAIL",
        summary="leave-machine gate open with vaulted key ready",
        http_status=gate_open.status_code,
        allowed=gate_body.get("allowed"),
        keys_ready=gate_body.get("keys_ready"),
        reasons=gate_body.get("reasons"),
    )
    if not allow_ready:
        return _finish(evidence, out_path)

    # --- 4. Ship allow: simulate execute + local_demo engine (no one-press /
    # Cortex probe -- keeps the offline demo fast and honest). ---
    allow_plan = client.post(
        "/api/deploy/from-cortex",
        json={"project_path": str(project), "subdomain": "app.example.com"},
    )
    if allow_plan.status_code != 200:
        _step(
            evidence,
            "ship_allow_local_demo_simulate",
            status="FAIL",
            summary="deploy plan failed before simulate execute",
            http_status=allow_plan.status_code,
            body=allow_plan.text[:500],
        )
        return _finish(evidence, out_path)
    allow_id = allow_plan.json()["deploy_id"]
    ship = client.post(
        f"/api/deploy/{allow_id}/execute",
        json={"simulate": True},
    )
    ship_body = ship.json()
    engine_out = run_ship_engine(
        target="local_demo",
        project_path=str(project),
        hostname="app.example.com",
    )
    deployment = engine_out.get("deployment") or {}
    public_url = deployment.get("public_url", "")
    mode = deployment.get("mode", "")
    ship_ok = (
        ship.status_code == 200
        and ship_body.get("openship", {}).get("executed") is True
        and engine_out.get("ok") is True
        and mode == "simulated"
        and public_url == ""
        and "opsh.io" not in str(public_url)
        and not str(public_url).startswith("https://")
    )
    _step(
        evidence,
        "ship_allow_local_demo_simulate",
        status="ok" if ship_ok else "FAIL",
        summary="gated ship allow under local_demo/simulate (no fake host URL)",
        http_status=ship.status_code,
        executed=ship_body.get("openship", {}).get("executed"),
        mode=mode,
        public_url=public_url,
        target="local_demo",
        simulate=True,
        label="SIMULATE -- not a live CF/Coolify/Netlify deploy",
    )
    if not ship_ok:
        return _finish(evidence, out_path)

    # --- 5. Gate deny: seal + leave-machine execute refuses ---
    vault.seal.lock()
    plan = client.post(
        "/api/deploy/from-cortex",
        json={"project_path": str(project), "subdomain": "app.example.com"},
    )
    if plan.status_code != 200:
        _step(
            evidence,
            "gate_deny",
            status="FAIL",
            summary="plan failed before deny execute",
            http_status=plan.status_code,
            body=plan.text[:500],
        )
        return _finish(evidence, out_path)

    deploy_id = plan.json()["deploy_id"]
    deny = client.post(
        f"/api/deploy/{deploy_id}/execute",
        json={"simulate": True},
    )
    deny_detail = deny.json().get("detail") or {}
    deny_ok = (
        deny.status_code == 403
        and deny_detail.get("allowed") is False
        and bool(deny_detail.get("reasons"))
    )
    _step(
        evidence,
        "gate_deny",
        status="ok" if deny_ok else "FAIL",
        summary="leave-machine execute refused while sealed (gate deny visible)",
        http_status=deny.status_code,
        allowed=deny_detail.get("allowed"),
        keys_ready=deny_detail.get("keys_ready"),
        reasons=deny_detail.get("reasons"),
    )
    if not deny_ok:
        return _finish(evidence, out_path)

    # --- 6. Usage ledger: the seat can be shown what it spent ---
    usage = client.get("/api/usage", params={"api_key_id": demo_key_id})
    usage_body = usage.json()
    summary = usage_body.get("summary", {})
    ledger_ok = (
        usage.status_code == 200
        and usage_body.get("count", 0) >= 1
        and all(row["api_key_id"] == demo_key_id for row in usage_body.get("events", []))
        and summary.get("priced") is False
    )
    _step(
        evidence,
        "usage_ledger_attributed",
        status="ok" if ledger_ok else "FAIL",
        summary="every gateway call is attributed to the issued key it was made with",
        http_status=usage.status_code,
        rows=usage_body.get("count"),
        requests=summary.get("requests"),
        billable_tokens=summary.get("billable_tokens"),
        estimated_tokens=summary.get("estimated_tokens"),
        priced=summary.get("priced"),
        label="METERED -- no price attached; pricing is a founder decision",
    )
    if not ledger_ok:
        return _finish(evidence, out_path)

    evidence["ok"] = True
    _step(
        evidence,
        "complete",
        status="ok",
        summary=(
            "one-seat auto-safe path done; HT1-HT5 remain human-only "
            "(live host URL, live FreeRoute keys, unseal UX, Cortex Phase 0, "
            "live secrets-at-ship)"
        ),
    )
    return _finish(evidence, out_path)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="OpenVault one-seat demo path (mocks/simulate only)"
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=Path(".demo_evidence") / "one_seat.json",
        help="evidence JSON path (default: .demo_evidence/one_seat.json)",
    )
    args = parser.parse_args(argv)
    evidence = run_demo(out_path=args.out)
    return 0 if evidence.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
