"""READY-02: no unauthenticated GET outside an explicit allowlist.

Auth change, founder GO 2026-11-28 17:01 SGT. The route walk builds the app,
lists every GET route it serves under ``/api/`` and ``/keys/`` (from the
OpenAPI schema and from the router table, so a route hidden from the schema is
still walked), and asks each one with no credential. Anything outside
``PUBLIC_READ_ALLOWLIST`` must answer 401. A new GET route is therefore closed
until someone adds it to the allowlist and changes ``EXPECTED_PUBLIC`` here on
purpose.

Run: uv run pytest tests/test_http_guard_read_gate.py -q
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from conftest import inject_admin_credential, issue_key
from cryptography.fernet import Fernet
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.routing import Mount, Route

from openmw.openvault.app import create_app
from openmw.openvault.vault.admin_token import ADMIN_HEADER, admin_headers
from openmw.openvault.vault.crypto import Seal
from openmw.openvault.vault.http_guard import PUBLIC_READ_ALLOWLIST, read_needs_credential
from openmw.openvault.vault.store import KeyVault

#: Changing this set is an auth change. See the module docstring.
EXPECTED_PUBLIC: frozenset[str] = frozenset(
    {
        "/api/healthz",
        "/.well-known/jwks.json",
        "/keys/jwks",
        "/api/freeroute/status",
        "/api/freeroute/onboard",
        "/api/freeroute/ratelimit",
        "/api/openfree/ratelimit",
        "/api/tool/register",
    }
)
#: GET routes the console and peers must still reach without a credential.
#: Same exact paths, restated so a typo in the allowlist cannot pass silently.
_MUST_STAY_PUBLIC = ("/api/healthz", "/api/freeroute/status", "/.well-known/jwks.json")
_MUST_BE_CLOSED = (
    "/api/usage",
    "/api/accounts",
    "/api/system/bind",
    "/api/system/catalog",
    "/api/cloud/devices",
    "/api/providers/cards",
    "/api/access/uptime",
    "/api/access/registry",
    "/api/ship/github/status",
    "/api/sentinel/devices",
    "/api/route/targets",
    "/api/slots",
)
_PARAM = re.compile(r"\{[^}]+\}")


def _make_app(tmp_path: Path) -> FastAPI:
    seal = Seal(Fernet.generate_key())
    vault = KeyVault(db_path=tmp_path / "keys.db", seal=seal)
    return create_app(
        vault=vault,
        mock_health=True,
        enable_precheck_loop=False,
        cortex_url="http://127.0.0.1:9",
    )


@pytest.fixture
def app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> FastAPI:
    monkeypatch.setenv("OPENVAULT_HOME", str(tmp_path / "home"))
    return _make_app(tmp_path)


def _router_get_paths(routes: list[object], prefix: str = "") -> set[str]:
    found: set[str] = set()
    for route in routes:
        path = prefix + str(getattr(route, "path", ""))
        if isinstance(route, Mount):
            found |= _router_get_paths(list(route.routes), path)
        elif isinstance(route, Route) and "GET" in (route.methods or set()):
            found.add(path)
    return found


def _get_paths(app: FastAPI) -> list[str]:
    schema = app.openapi()
    paths = {p for p, ops in schema["paths"].items() if "get" in ops}
    paths |= _router_get_paths(list(app.routes))
    return sorted(p for p in paths if p.startswith(("/api/", "/keys/")) or p == "/keys")


def _concrete(path: str) -> str:
    return _PARAM.sub("x", path)


def test_public_allowlist_is_exactly_the_reviewed_set() -> None:
    assert PUBLIC_READ_ALLOWLIST == EXPECTED_PUBLIC
    for path in _MUST_STAY_PUBLIC:
        assert path in PUBLIC_READ_ALLOWLIST
    for path in _MUST_BE_CLOSED:
        assert path not in PUBLIC_READ_ALLOWLIST
        assert read_needs_credential("GET", path)
        assert read_needs_credential("HEAD", path)
        assert not read_needs_credential("POST", path)


def test_route_walk_is_not_empty_and_sees_the_known_routes(app: FastAPI) -> None:
    paths = _get_paths(app)
    assert len(paths) >= 60, f"route walk saw only {len(paths)} GET routes"
    for path in (*_MUST_STAY_PUBLIC, *_MUST_BE_CLOSED):
        if path != "/.well-known/jwks.json":
            assert path in paths, f"{path} missing from the route walk"


@pytest.mark.parametrize("peer", ["127.0.0.1", "::1", "10.128.0.3", "8.8.8.8"])
def test_no_unauthenticated_get_outside_the_allowlist(app: FastAPI, peer: str) -> None:
    """The contract: every non-public GET is 401 with no credential, any peer."""
    token = inject_admin_credential.set(False)
    try:
        client = TestClient(app, client=(peer, 5555))
        leaked: list[str] = []
        for path in _get_paths(app):
            if path in PUBLIC_READ_ALLOWLIST:
                continue
            res = client.get(_concrete(path))
            if res.status_code != 401:
                leaked.append(f"{res.status_code} {path}")
        assert not leaked, "unauthenticated GET not answered 401:\n" + "\n".join(leaked)
    finally:
        inject_admin_credential.reset(token)


def test_head_without_credential_is_closed_too(app: FastAPI) -> None:
    token = inject_admin_credential.set(False)
    try:
        client = TestClient(app, client=("127.0.0.1", 5555))
        for path in _MUST_BE_CLOSED:
            assert client.head(path).status_code == 401, path
    finally:
        inject_admin_credential.reset(token)


def test_public_reads_stay_reachable_without_credential(app: FastAPI) -> None:
    token = inject_admin_credential.set(False)
    try:
        client = TestClient(app, client=("127.0.0.1", 5555))
        for path in ("/api/healthz", "/api/freeroute/status", "/.well-known/jwks.json"):
            assert client.get(path).status_code == 200, path
        assert client.get("/keys/jwks").status_code == 200
    finally:
        inject_admin_credential.reset(token)


def test_near_miss_paths_do_not_ride_the_allowlist(app: FastAPI) -> None:
    token = inject_admin_credential.set(False)
    try:
        client = TestClient(app, client=("127.0.0.1", 5555))
        for path in (
            "/api/healthz/",
            "/api/healthz/x",
            "/api/HEALTHZ",
            "/api/freeroute/status/x",
            "/api/freeroute/ratelimit/x",
        ):
            assert client.get(path).status_code in (401, 404), path
        assert read_needs_credential("GET", "/api/healthz/")
        assert read_needs_credential("GET", "/api/freeroute/status/x")
    finally:
        inject_admin_credential.reset(token)


def test_admin_header_opens_the_reads_from_loopback(app: FastAPI) -> None:
    token = inject_admin_credential.set(False)
    try:
        client = TestClient(app, client=("127.0.0.1", 5555))
        for path in ("/api/usage", "/api/accounts", "/api/system/bind", "/api/cloud/devices"):
            res = client.get(path, headers=admin_headers())
            assert res.status_code == 200, f"{path} -> {res.status_code}"
        wrong = client.get("/api/usage", headers={ADMIN_HEADER: "not-the-token"})
        assert wrong.status_code == 401
    finally:
        inject_admin_credential.reset(token)


def test_issued_key_keeps_its_scoped_usage_read(app: FastAPI) -> None:
    """An ov_ holder still reads its own usage rows. A bad bearer is 403, not anonymous."""
    loop = TestClient(app, client=("127.0.0.1", 5555))
    _key_id, headers = issue_key(loop)
    token = inject_admin_credential.set(False)
    try:
        assert loop.get("/api/usage", headers=headers).status_code == 200
        bad = loop.get("/api/usage", headers={"Authorization": "Bearer ov_not_a_real_key"})
        assert bad.status_code == 403
        remote = TestClient(app, client=("8.8.8.8", 5555))
        assert remote.get("/api/usage", headers=headers).status_code == 200
        assert remote.get("/api/usage").status_code == 401
    finally:
        inject_admin_credential.reset(token)


def test_bind_response_carries_no_internal_url_or_address(app: FastAPI) -> None:
    client = TestClient(app, client=("127.0.0.1", 5555))
    for path in ("/api/system/bind", "/api/system/catalog"):
        text = client.get(path, headers=admin_headers()).text
        assert not re.search(
            r"\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}", text.replace("127.0.0.1", "")
        ), path
        assert "internal_writers_url" not in text
        assert "jwks_url" not in text


def test_write_methods_keep_their_existing_rules(app: FastAPI) -> None:
    """The read gate is GET/HEAD only. POST on a read path is not newly 401 by it."""
    assert not read_needs_credential("POST", "/api/usage")
    assert not read_needs_credential("PUT", "/api/accounts")
    assert not read_needs_credential("GET", "/")
    assert not read_needs_credential("GET", "/provider-cards")
